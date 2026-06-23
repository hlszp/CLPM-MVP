"""写操作幂等性中间件 (S2-C6)。

通过 Idempotency-Key header 实现写操作幂等性：
- 客户端在 POST/PUT 请求中携带 Idempotency-Key header
- 相同 key 的请求在 24 小时内返回首次请求的响应（不重复执行）
- 无 key 或 GET/DELETE/PATCH 请求正常处理（向后兼容）
- Redis 不可用时降级为正常请求
"""

from __future__ import annotations

import json
import logging
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.redis import redis_client

logger = logging.getLogger(__name__)

IDEMPOTENCY_KEY_HEADER = "Idempotency-Key"
IDEMPOTENCY_TTL = 86400  # 24 小时


class IdempotencyMiddleware(BaseHTTPMiddleware):
    """写操作幂等性中间件。

    通过 Idempotency-Key header 实现幂等性。
    仅对 POST/PUT 请求生效。
    """

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        # 仅对写操作生效
        if request.method not in ("POST", "PUT"):
            return await call_next(request)

        # 检查 Idempotency-Key header
        idempotency_key = request.headers.get(IDEMPOTENCY_KEY_HEADER)
        if not idempotency_key:
            return await call_next(request)

        # 构建 Redis key
        redis_key = f"idempotency:{idempotency_key}"

        # 检查是否已有缓存响应
        try:
            cached = await redis_client.get(redis_key)
            if cached:
                cached_data = json.loads(cached)
                return Response(
                    content=cached_data["body"],
                    status_code=cached_data["status_code"],
                    headers=cached_data.get("headers", {}),
                    media_type="application/json",
                )
        except Exception as exc:
            logger.warning("幂等性缓存读取失败，降级为正常请求: %s", exc)

        # 执行请求
        response = await call_next(request)

        # 仅缓存成功的响应（2xx）
        if not (200 <= response.status_code < 300):
            return response

        # 读取响应体（BaseHTTPMiddleware 返回流式响应，需消费 body_iterator）
        try:
            response_body = b""
            async for chunk in response.body_iterator:
                if isinstance(chunk, str):
                    chunk = chunk.encode("utf-8")
                response_body += chunk
        except Exception as exc:
            logger.warning("读取响应体失败: %s", exc)
            return Response(
                content=b"",
                status_code=response.status_code,
                media_type=response.media_type,
            )

        # 缓存响应
        try:
            cache_data = {
                "status_code": response.status_code,
                "body": response_body.decode("utf-8"),
                "headers": {"content-type": "application/json"},
            }
            await redis_client.setex(
                redis_key,
                IDEMPOTENCY_TTL,
                json.dumps(cache_data),
            )
        except Exception as exc:
            logger.warning("幂等性缓存写入失败: %s", exc)

        # 返回新响应（排除 content-length 避免与实际 body 长度冲突）
        headers = {
            k: v
            for k, v in response.headers.items()
            if k.lower() not in ("content-length", "content-type")
        }
        return Response(
            content=response_body,
            status_code=response.status_code,
            headers=headers,
            media_type=response.media_type or "application/json",
        )


__all__ = ["IDEMPOTENCY_KEY_HEADER", "IDEMPOTENCY_TTL", "IdempotencyMiddleware"]
