"""Request ID middleware (S3-B4).

为每个请求生成/传递 request_id，用于全链路追踪：
- 从 X-Request-ID header 读取，不存在则生成 UUID
- 设置到 contextvar，供日志记录使用
- 添加到响应 header X-Request-ID
"""

from __future__ import annotations

import uuid
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import _request_id_ctx


class RequestIdMiddleware(BaseHTTPMiddleware):
    """为每个请求注入 request_id（X-Request-ID header + contextvar）。"""

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        # 从 header 读取或生成 UUID
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        # 设置到 contextvar，供日志 Formatter 读取
        token = _request_id_ctx.set(request_id)
        try:
            response = await call_next(request)
        finally:
            # 请求结束后重置 contextvar
            _request_id_ctx.reset(token)
        # 添加到响应 header
        response.headers["X-Request-ID"] = request_id
        return response


def get_request_id() -> str | None:
    """获取当前请求的 request_id（从 contextvar 读取）。"""
    return _request_id_ctx.get()


__all__ = ["RequestIdMiddleware", "get_request_id"]
