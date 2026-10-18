"""Rate limiting middleware (S2-C5).

基于 Redis 的滑动窗口限流，针对敏感端点：
- 登录/刷新/改密/创建用户 等接口限制请求频率
- 登录接口采用 IP + 用户名双维度限流（防密码喷洒/撞库），
  与 services/auth.py 的"5 次失败锁定 15 分钟"互补
- 超限返回 429 Too Many Requests
"""

from __future__ import annotations

import json
import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.redis import redis_client
from app.core.security import get_client_ip

logger = logging.getLogger(__name__)

LOGIN_PATH = "/api/v1/auth/login"

# 敏感端点限流配置: path -> (method, max_requests, window_seconds)
# 注意 /api/v1/auth/password 实际端点为 PUT（auth.py @router.put），
# 方法不匹配会导致该条限流永不生效。
RATE_LIMITS: dict[str, tuple[str, int, int]] = {
    LOGIN_PATH: ("POST", 10, 60),  # 登录：10 次/分钟/IP
    "/api/v1/auth/refresh": ("POST", 10, 60),  # 刷新令牌：10 次/分钟
    "/api/v1/auth/password": ("PUT", 5, 60),  # 改密：5 次/分钟
    "/api/v1/users": ("POST", 5, 60),  # 创建用户：5 次/分钟
}

# 登录接口用户名维度限流（与 IP 维度并行，任一维度超限即拒绝）
LOGIN_USER_LIMIT: tuple[int, int] = (10, 60)  # 10 次/分钟/账号


class RateLimitMiddleware(BaseHTTPMiddleware):
    """基于 Redis INCR + EXPIRE 的滑动窗口限流中间件。"""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        config = RATE_LIMITS.get(path)
        # 仅对配置的敏感端点限流（path + method 精确匹配）
        if config is None or request.method != config[0]:
            return await call_next(request)

        _, limit, window = config
        # 与 security.get_client_ip 统一口径：反向代理后认 X-Forwarded-For，
        # 避免所有用户共享代理 IP 导致限流误伤/失效
        client_ip = get_client_ip(request) or "unknown"

        # (redis_key, max_requests, window_seconds) 检查清单
        checks: list[tuple[str, int, int]] = [(f"rate_limit:{path}:{client_ip}", limit, window)]

        # 登录接口追加用户名维度限流
        if path == LOGIN_PATH:
            username = await self._extract_username(request)
            if username:
                user_limit, user_window = LOGIN_USER_LIMIT
                checks.append((f"rate_limit:{path}:user:{username}", user_limit, user_window))

        try:
            for key, max_requests, win in checks:
                count = await redis_client.incr(key)
                if count == 1:
                    await redis_client.expire(key, win)
                if count > max_requests:
                    logger.warning(
                        "限流触发: key=%s ip=%s count=%d limit=%d",
                        key,
                        client_ip,
                        count,
                        max_requests,
                    )
                    return JSONResponse(
                        status_code=429,
                        content={
                            "code": "ERR_RATE_LIMITED",
                            "message": "请求过于频繁，请稍后再试",
                            "data": None,
                        },
                        headers={"Retry-After": str(win)},
                    )
        except Exception:
            # Redis 不可用时不阻断请求（降级策略）
            logger.warning("限流检查失败（Redis 不可用），放行请求")

        return await call_next(request)

    @staticmethod
    async def _extract_username(request: Request) -> str | None:
        """从登录请求体解析用户名。

        BaseHTTPMiddleware 的 _CachedRequest 会缓存 body 并传递给下游，
        此处读取不影响端点正常解析。解析失败时返回 None（跳过用户名维度，
        由端点自身的参数校验兜底）。
        """
        try:
            body = await request.body()
            if not body:
                return None
            data = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        username = data.get("username")
        return str(username) if username else None
