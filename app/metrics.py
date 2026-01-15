from fastapi import APIRouter
from fastapi.responses import PlainTextResponse
from prometheus_client import CollectorRegistry, Counter, generate_latest

from .config import get_settings

router = APIRouter()

registry = CollectorRegistry()

webhook_requests_total = Counter(
    "webhook_requests_total",
    "Total number of webhook requests received",
    ["result"],
    registry=registry,
)

messages_stored_total = Counter(
    "messages_stored_total",
    "Total number of messages stored (idempotent; counts new records only)",
    registry=registry,
)


@router.get("/metrics", include_in_schema=False)
def metrics() -> PlainTextResponse:
    """Expose Prometheus metrics if enabled."""

    settings = get_settings()
    if not settings.enable_metrics:
        return PlainTextResponse("", status_code=404)

    content = generate_latest(registry)
    return PlainTextResponse(
        content.decode("utf-8"),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


