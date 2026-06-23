"""FastAPI application entry point.

Wires up logging, CORS, global exception handlers and route prefixes:
- ``/health`` (root) for liveness probes
- ``/api/v1/*`` for business endpoints (auth, ...)
- ``/docs`` and ``/redoc`` for OpenAPI documentation
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.endpoints import (
    aas,
    audit_logs,
    auth,
    dashboard,
    diagnosis,
    health,
    loops,
    performance,
    plant_nodes,
    reports,
    tuning,
    users,
)
from app.core.config import settings
from app.core.db import dispose_engine
from app.core.exceptions import register_exception_handlers
from app.core.logging import get_logger, setup_logging
from app.core.metrics import MetricsMiddleware, setup_metrics
from app.core.redis import close_redis
from app.middleware.rate_limit import RateLimitMiddleware
from app.middleware.request_id import RequestIdMiddleware

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Application lifespan: initialise resources on startup, clean up on shutdown."""
    setup_logging()
    logger.info("Starting %s v%s", settings.APP_NAME, settings.APP_VERSION)
    yield
    logger.info("Shutting down %s", settings.APP_NAME)
    await dispose_engine()
    await close_redis()


def create_app() -> FastAPI:
    """Application factory."""
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description="Control Loop Performance Monitoring backend API",
        debug=settings.DEBUG,
        docs_url="/docs" if settings.DEBUG else None,
        redoc_url="/redoc" if settings.DEBUG else None,
        openapi_url="/openapi.json" if settings.DEBUG else None,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Accept"],
    )
    # S2-C5: 敏感端点速率限制
    app.add_middleware(RateLimitMiddleware)
    # S3-B3: Prometheus 指标采集中间件
    app.add_middleware(MetricsMiddleware)
    # S3-B4: request_id 请求追踪（最外层，最先执行）
    app.add_middleware(RequestIdMiddleware)

    # S3-B3: 挂载 /metrics 端点
    setup_metrics(app)

    register_exception_handlers(app)

    # Health probe at root (no business prefix) for k8s/container probes.
    app.include_router(health.router)

    # Business endpoints under /api/v1.
    from fastapi import APIRouter

    v1_router = APIRouter(prefix="/api/v1")
    v1_router.include_router(auth.router)
    v1_router.include_router(plant_nodes.router)
    v1_router.include_router(loops.router)
    v1_router.include_router(aas.router)
    v1_router.include_router(performance.router)
    # S6 工作台门户：BFF 聚合层
    v1_router.include_router(dashboard.router)
    # S4 诊断中心：诊断、波形、Tracker（三个 router 共享 v1 前缀，各自有子前缀）
    v1_router.include_router(diagnosis.router)
    v1_router.include_router(diagnosis.timeseries_router)
    v1_router.include_router(diagnosis.tracker_router)
    # S5 系统管理：用户管理、审计日志、报表配置
    v1_router.include_router(users.router)
    v1_router.include_router(audit_logs.router)
    v1_router.include_router(reports.router)
    # S7 回路整定：模型辨识、PID 整定、闭环仿真
    v1_router.include_router(tuning.router)
    app.include_router(v1_router)

    return app


app = create_app()
