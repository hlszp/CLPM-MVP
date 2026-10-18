"""Structured logging configuration (JSON to stdout).

Provides `setup_logging()` to initialise the root logger. In DEBUG mode logs are
emitted as human-readable lines for convenience; otherwise JSON is used so logs
can be ingested by external pipelines.

S3-B4: 支持 request_id 请求追踪（contextvar）
S3-B5: 敏感信息脱敏（password/token/Bearer/JWT）
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

from app.core.config import settings

# request_id 上下文变量（S3-B4），供 RequestIdMiddleware 设置、Formatter 读取
_request_id_ctx: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)


# ---------------------------------------------------------------------------
# S3-B5: 敏感信息脱敏
# ---------------------------------------------------------------------------

# 脱敏正则模式列表：(pattern, replacement)
_SANITIZE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # password=xxx → password=***
    (re.compile(r"(password\s*=\s*)([^\s,;}\]]+)"), r"\1***"),
    # "password": "xxx" → "password": "***"
    (re.compile(r'("password"\s*:\s*")([^"]*)(")'), r"\1***\3"),
    # token=xxx → token=***
    (re.compile(r"(token\s*=\s*)([^\s,;}\]]+)"), r"\1***"),
    # Bearer xxx → Bearer ***
    (re.compile(r"(Bearer\s+)([^\s,;}\]]+)"), r"\1***"),
    # JWT: eyJxxx → eyJ***
    (re.compile(r"(eyJ)([A-Za-z0-9_-]+)"), r"\1***"),
]


def _sanitize_message(message: str) -> str:
    """对日志消息进行敏感信息脱敏。

    替换模式：
    - password=xxx → password=***
    - "password": "xxx" → "password": "***"
    - token=xxx → token=***
    - Bearer xxx → Bearer ***
    - JWT (eyJxxx) → eyJ***
    """
    sanitized = message
    for pattern, replacement in _SANITIZE_PATTERNS:
        sanitized = pattern.sub(replacement, sanitized)
    return sanitized


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


class JsonFormatter(logging.Formatter):
    """Emit log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        # S3-B5: 敏感信息脱敏
        message = _sanitize_message(record.getMessage())
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": message,
        }
        # S3-B4: 添加 request_id（从 contextvar 读取，不存在则不添加）
        request_id = _request_id_ctx.get()
        if request_id:
            payload["request_id"] = request_id
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


class _DebugFormatter(logging.Formatter):
    """DEBUG 模式下的人类可读 Formatter，支持 request_id 注入与脱敏。"""

    def format(self, record: logging.LogRecord) -> str:
        original = super().format(record)
        # S3-B4: 在非 DEBUG 模式下也添加 request_id（DEBUG 模式同样添加）
        request_id = _request_id_ctx.get()
        if request_id:
            original = f"[{request_id}] {original}"
        # S3-B5: DEBUG 模式也应用脱敏
        return _sanitize_message(original)


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


def setup_logging() -> None:
    """Configure root logger with a stdout handler."""
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if settings.DEBUG else logging.INFO)
    # DEBUG 下抑制高音量框架日志（2026-08-09 后端宕死排查：DEBUG 全量时
    # sqlalchemy/httpx 每请求数十行，dev 日志 26 万行/天，压测下同步日志 I/O
    # 会显著拖慢事件循环；业务日志不受影响，仍按 root 级别输出）
    if settings.DEBUG:
        for noisy in (
            "sqlalchemy.engine",
            "sqlalchemy.pool",
            "httpx",
            "httpcore",
            "asyncpg",
            "urllib3",
            "websockets",
        ):
            logging.getLogger(noisy).setLevel(logging.WARNING)
    # Remove any pre-existing handlers to avoid duplicate output on reload.
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stdout)
    if settings.DEBUG:
        handler.setFormatter(
            _DebugFormatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
        )
    else:
        handler.setFormatter(JsonFormatter())
    root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """Return a module-level logger."""
    return logging.getLogger(name)
