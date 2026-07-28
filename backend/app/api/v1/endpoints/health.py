"""Health check endpoints (S2-B7).

- ``GET /health`` — Liveness probe（进程存活即返回 ok）
- ``GET /health/ready`` — Readiness probe（检查 DB/Redis/TDengine 依赖连通性）
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.config import settings
from app.core.db import engine
from app.core.redis import redis_client

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe — 进程存活即返回 ok。"""
    return {"status": "ok", "version": settings.APP_VERSION}


@router.get("/health/ready")
async def health_ready() -> JSONResponse:
    """Readiness probe — 检查 DB/Redis/TDengine 依赖连通性。

    返回 200 + ``status=ok`` 表示所有依赖就绪；
    返回 503 + ``status=degraded`` 表示部分依赖不可用。
    """
    checks: dict[str, str] = {}

    # 1. PostgreSQL
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception as exc:
        logger.warning("健康检查 PostgreSQL 失败: %s", exc)
        checks["postgres"] = f"fail: {exc.__class__.__name__}"

    # 2. Redis
    try:
        await redis_client.ping()
        checks["redis"] = "ok"
    except Exception as exc:
        logger.warning("健康检查 Redis 失败: %s", exc)
        checks["redis"] = f"fail: {exc.__class__.__name__}"

    # 3. TDengine（计算类历史数据查询一律走本地 TDengine，必查）
    try:
        from app.core.tdengine import execute_sql

        rows = await execute_sql("SHOW DATABASES")
        checks["tdengine"] = "ok" if rows is not None else "fail: empty"
    except Exception as exc:
        logger.warning("健康检查 TDengine 失败: %s", exc)
        checks["tdengine"] = f"fail: {exc.__class__.__name__}"

    all_ok = all(v == "ok" for v in checks.values())
    return JSONResponse(
        status_code=200 if all_ok else 503,
        content={
            "status": "ok" if all_ok else "degraded",
            "version": settings.APP_VERSION,
            "checks": checks,
        },
    )
