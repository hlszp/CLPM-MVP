"""Structured logging configuration (JSON to stdout).

Provides `setup_logging()` to initialise the root logger. In DEBUG mode logs are
emitted as human-readable lines for convenience; otherwise JSON is used so logs
can be ingested by external pipelines.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from app.core.config import settings


class JsonFormatter(logging.Formatter):
    """Emit log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        # Attach extra fields (those not in the standard LogRecord attrs).
        standard = set(
            logging.LogRecord(
                name="", level=0, pathname="", lineno=0, msg="", args=None, exc_info=None
            ).__dict__.keys()
        )
        for key, value in record.__dict__.items():
            if key not in standard and key not in {"message", "asctime"}:
                payload[key] = value
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging() -> None:
    """Configure root logger with a stdout handler."""
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if settings.DEBUG else logging.INFO)
    # Remove any pre-existing handlers to avoid duplicate output on reload.
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stdout)
    if settings.DEBUG:
        handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
        )
    else:
        handler.setFormatter(JsonFormatter())
    root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """Return a module-level logger."""
    return logging.getLogger(name)
