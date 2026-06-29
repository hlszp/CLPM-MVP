"""Rate limiting middleware (S2-C5).

基于 Redis 的滑动窗口限流，针对敏感端点：
- 登录/刷新/改密/创建用户 等接口限制请求频率
- 超限返回 429 Too Many Requests
"""

from __future__ import annotations

import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.redis import redis_client

logger = logging.getLogger(__name__)

# 敏感端点限流配置: path -> (max_requests, window_seconds)
RATE_LIMITS: dict[str, tuple[int, int]] = {
    "/api/v1/auth/login": (10, 60),  # 登录：10 次/分钟
    "/api/v1/auth/refresh": (10, 60),  # 刷新令牌：10 次/分钟
    "/api/v1/auth/password": (5, 60),  # 改密：5 次/分钟
    "/api/v1/users": (5, 60),  # 创建用户：5 次/分钟
}


class RateLimitMiddleware(BaseHTTPMiddleware):
    """基于 Redis INCR + EXPIRE 的滑动窗口限流中间件。"""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        # 仅对配置的敏感端点限流（精确匹配 POST 方法）
        if path not in RATE_LIMITS or request.method != "POST":
            return await call_next(request)

        limit, window = RATE_LIMITS[path]
        client_ip = request.client.host if request.client else "unknown"
        key = f"rate_limit:{path}:{client_ip}"

        try:
            count = await redis_client.incr(key)
            if count == 1:
                await redis_client.expire(key, window)
            if count > limit:
                logger.warning(
                    "限流触发: path=%s ip=%s count=%d limit=%d",
                    path,
                    client_ip,
                    count,
                    limit,
                )
                return JSONResponse(
                    status_code=429,
                    content={
                        "code": "ERR_RATE_LIMITED",
                        "message": "请求过于频繁，请稍后再试",
                        "data": None,
                    },
                    headers={"Retry-After": str(window)},
                )
        except Exception:
            # Redis 不可用时不阻断请求（降级策略）
            logger.warning("限流检查失败（Redis 不可用），放行请求")

        return await call_next(request)
