import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Dict


class JsonFormatter(logging.Formatter):
    """Logging formatter that outputs structured JSON."""

    def format(self, record: logging.LogRecord) -> str:
        log_record: Dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Include extra fields if present (e.g. request_id, method, path, status, latency_ms, message_id, dup, result)
        for key, value in record.__dict__.items():
            if key.startswith("_"):
                continue
            if key in (
                "args",
                "msg",
                "levelname",
                "levelno",
                "name",
                "created",
                "msecs",
                "relativeCreated",
                "lineno",
                "pathname",
                "filename",
                "funcName",
                "module",
                "exc_info",
                "exc_text",
                "stack_info",
            ):
                continue
            log_record[key] = value

        if record.exc_info:
            log_record["exc_info"] = self.formatException(record.exc_info)

        return json.dumps(log_record, ensure_ascii=False)


def configure_logging(level_name: str = "INFO") -> None:
    """Configure application-wide structured JSON logging."""

    level = getattr(logging, level_name.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)

    # Clear existing handlers (e.g., from uvicorn) so we can reconfigure
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)

    # Reduce noise from third-party libraries if needed
    logging.getLogger("uvicorn").setLevel(level)
    logging.getLogger("uvicorn.error").setLevel(level)
    logging.getLogger("uvicorn.access").setLevel(level)



