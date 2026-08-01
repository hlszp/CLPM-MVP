"""Health check endpoints (S2-B7).

- ``GET /health`` — Liveness probe（进程存活即返回 ok）
- ``GET /health/ready`` — Readiness probe（检查 DB/Redis/TDengine 依赖连通性）
- ``GET /health/db-connections`` — PG 连接池监控（P2-018，查询 pg_stat_activity）
"""

from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.config import settings
from app.core.db import engine
from app.core.metrics import pg_active_connections
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


@router.get("/health/db-connections")
async def health_db_connections() -> JSONResponse:
    """PG 连接池监控（P2-018）— 查询 pg_stat_activity 按 application_name 分组。

    返回当前数据库的活跃连接数、PG max_connections 配置、按 app 分组的连接明细，
    以及连接利用率百分比。同步更新 Prometheus pg_active_connections Gauge。

    用于排查 E2E 连续运行 / Celery 并发任务导致的连接池耗尽问题。
    """
    try:
        async with engine.connect() as conn:
            # 按 application_name 分组统计活跃连接
            rows = (
                await conn.execute(
                    text("""
                        SELECT COALESCE(application_name, 'unknown') AS app,
                               count(*) AS cnt
                        FROM pg_stat_activity
                        WHERE datname = current_database()
                        GROUP BY application_name
                        ORDER BY count(*) DESC
                    """)
                )
            ).fetchall()

            # max_connections 配置（PG 返回字符串，需转 int）
            max_val = int((await conn.execute(text("SHOW max_connections"))).scalar())

            # 当前数据库总活跃连接
            total_val = (
                await conn.execute(
                    text("""
                        SELECT count(*) FROM pg_stat_activity
                        WHERE datname = current_database()
                    """)
                )
            ).scalar()

        by_app = {row.app: row.cnt for row in rows}

        # 同步 Prometheus Gauge（按 application_name 分标签）
        for app_name, cnt in by_app.items():
            pg_active_connections.labels(application_name=app_name).set(cnt)

        utilization = round(total_val / max_val * 100, 1) if max_val else 0.0

        return JSONResponse(
            content={
                "total": total_val,
                "max": max_val,
                "byApp": by_app,
                "utilization": utilization,
            }
        )
    except Exception as exc:
        logger.warning("连接池监控查询失败: %s", exc)
        return JSONResponse(
            status_code=503,
            content={"error": f"{exc.__class__.__name__}: {exc}"},
        )
