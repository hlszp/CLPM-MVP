"""Async Redis client wrapper.

Provides a singleton ``redis_client`` and a FastAPI dependency ``get_redis``.
Used for:
- Login failure counting (``login_fail:{username}``)
- Token blacklist (``token_blacklist:{jti}``)
- User token tracking (``user_tokens:{user_id}``)
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import redis.asyncio as aioredis

from app.core.config import settings

# Singleton async Redis client.
redis_client: aioredis.Redis = aioredis.Redis(
    host=settings.REDIS_HOST,
    port=settings.REDIS_PORT,
    db=settings.REDIS_DB,
    decode_responses=True,
)


async def get_redis() -> AsyncGenerator[aioredis.Redis, None]:
    """FastAPI dependency that yields the shared Redis client."""
    yield redis_client


async def close_redis() -> None:
    """Close the Redis connection pool (call on application shutdown)."""
    await redis_client.aclose()
