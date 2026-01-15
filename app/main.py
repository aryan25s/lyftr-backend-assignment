import hmac
import json
import logging
import time

from hashlib import sha256
from typing import Optional
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse

from .config import get_settings
from .logging_utils import configure_logging
from .metrics import messages_stored_total, router as metrics_router, webhook_requests_total
from .models import MessageIn, MessageOut, MessagesPage, StatsOut, SenderStats
from .storage import get_stats, init_db, insert_message_idempotent, list_messages


logger = logging.getLogger("app")


def verify_signature(secret: str, body: bytes, signature_header: str) -> bool:
    """Validate HMAC-SHA256 signature."""

    try:
        provided_sig = signature_header.strip()
    except Exception:
        return False

    computed = hmac.new(secret.encode("utf-8"), body, sha256).hexdigest()
    return hmac.compare_digest(computed, provided_sig)


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(title=settings.app_name)

    # Initialize DB
    init_db()

    # Request/response logging middleware
    @app.middleware("http")
    async def logging_middleware(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid4())
        request.state.request_id = request_id

        start = time.time()
        response = await call_next(request)
        latency_ms = (time.time() - start) * 1000.0
        status_code = response.status_code
        logger.info(
            "request_complete",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status": status_code,
                "latency_ms": round(latency_ms, 2),
            },
        )
        response.headers["X-Request-ID"] = request_id
        return response

    # Include metrics router
    app.include_router(metrics_router)

    @app.post("/webhook")
    async def webhook_endpoint(
        request: Request,
        x_signature: str = Header(..., alias="X-Signature"),
    ):
        """Receive webhook events.

        Expects:
        - Raw body signed with HMAC-SHA256 using WEBHOOK_SECRET,
          hex-encoded in `X-Signature` header.
        - JSON body matching the required message schema.
        """

        raw_body = await request.body()
        settings = get_settings()
        request_id = getattr(request.state, "request_id", str(uuid4()))

        if not settings.webhook_secret:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="WEBHOOK_SECRET is not configured",
            )

        if not verify_signature(settings.webhook_secret, raw_body, x_signature):
            webhook_requests_total.labels(result="invalid_signature").inc()
            logger.warning(
                "Invalid webhook signature",
                extra={
                    "request_id": request_id,
                    "result": "invalid_signature",
                },
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid signature",
            )

        try:
            body_json = json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError:
            webhook_requests_total.labels(result="invalid_json").inc()
            logger.warning(
                "Invalid JSON payload",
                extra={"request_id": request_id, "result": "invalid_json"},
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid JSON payload",
            )

        try:
            payload = MessageIn.parse_obj(body_json)
        except Exception as exc:
            webhook_requests_total.labels(result="invalid_payload").inc()
            logger.warning(
                "Invalid payload schema",
                extra={
                    "request_id": request_id,
                    "result": "invalid_payload",
                    "error": str(exc),
                },
            )
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Invalid payload schema",
            )

        created = insert_message_idempotent(payload)

        webhook_requests_total.labels(result="ok").inc()
        if created:
            messages_stored_total.inc()

        logger.info(
            "Webhook processed",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status": 200,
                "message_id": payload.message_id,
                "dup": not created,
                "result": "ok",
            },
        )

        return JSONResponse(status_code=status.HTTP_200_OK, content={"status": "ok"})

    @app.get("/messages", response_model=MessagesPage)
    def get_messages(
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0),
        from_filter: Optional[str] = Query(None, alias="from"),
        since: Optional[str] = Query(None),
        q: Optional[str] = Query(None),
        request: Request = None,
    ):
        """List stored messages with pagination, filters, and ordering."""

        items_raw, total = list_messages(
            limit=limit, offset=offset, from_filter=from_filter, since=since, q=q
        )

        items = [
            MessageOut(
                message_id=item["message_id"],
                **{
                    "from": item["from"],
                    "to": item["to"],
                    "ts": item["ts"],
                    "text": item["text"],
                },
            )
            for item in items_raw
        ]

        request_id = getattr(request.state, "request_id", None) if request else None
        logger.info(
            "Messages listed",
            extra={
                "request_id": request_id,
                "method": request.method if request else "GET",
                "path": request.url.path if request else "/messages",
                "status": 200,
                "limit": limit,
                "offset": offset,
                "total": total,
            },
        )

        return MessagesPage(items=items, total=total, limit=limit, offset=offset)

    @app.get("/stats", response_model=StatsOut)
    def stats(request: Request):
        """Return basic analytics statistics."""

        data = get_stats()

        request_id = getattr(request.state, "request_id", None)
        logger.info(
            "Stats retrieved",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status": 200,
                "total_messages": data["total_messages"],
            },
        )

        stats_obj = StatsOut(
            total_messages=data["total_messages"],
            senders_count=data["senders_count"],
            messages_per_sender=[
                SenderStats(sender=s["sender"], count=s["count"])
                for s in data["messages_per_sender"]
            ],
            first_message_ts=data["first_message_ts"],
            last_message_ts=data["last_message_ts"],
        )

        return stats_obj

    @app.get("/health/live")
    def health_live():
        """Liveness check."""

        return {"status": "ok"}

    @app.get("/health/ready")
    def health_ready(request: Request):
        """Readiness check, including database connectivity and WEBHOOK_SECRET."""

        settings_local = get_settings()
        if not settings_local.webhook_secret:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="WEBHOOK_SECRET is not configured",
            )

        try:
            # Ensure DB is reachable and schema exists
            _ = get_stats()
        except Exception as exc:
            request_id = getattr(request.state, "request_id", None)
            logger.error(
                "Readiness check failed",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status": 503,
                    "result": "unready",
                    "error": str(exc),
                },
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Database not ready",
            )

        request_id = getattr(request.state, "request_id", None)
        logger.info(
            "Readiness check passed",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status": 200,
                "result": "ready",
            },
        )
        return {"status": "ok"}

    return app


app = create_app()



