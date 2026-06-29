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
    algorithms,
    audit_logs,
    auth,
    configs,
    dashboard,
    dataplanner,
    diagnosis,
    health,
    loop_level_weight,
    loop_mode_mapping,
    loop_type_weight,
    loops,
    node_performance,
    performance,
    plant_nodes,
    realtime,
    reports,
    tags,
    tasks as eval_tasks,
    tuning,
    users,
    ws_realtime,
)
from app.core.config import settings
from app.core.db import dispose_engine
from app.core.exceptions import register_exception_handlers
from app.core.logging import get_logger, setup_logging
from app.core.metrics import MetricsMiddleware, setup_metrics
from app.core.redis import close_redis
from app.middleware.idempotency import IdempotencyMiddleware
from app.middleware.rate_limit import RateLimitMiddleware
from app.middleware.request_id import RequestIdMiddleware

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Application lifespan: initialise resources on startup, clean up on shutdown."""
    setup_logging()
    logger.info("Starting %s v%s", settings.APP_NAME, settings.APP_VERSION)
    logger.info("数据源类型: %s", settings.DATA_SOURCE_TYPE)

    # 启动实时数据订阅（如已启用）
    from app.services.data_source.realtime_subscriber import start_subscriber

    await start_subscriber()

    yield

    logger.info("Shutting down %s", settings.APP_NAME)

    # 停止实时数据订阅
    from app.services.data_source.realtime_subscriber import stop_subscriber

    await stop_subscriber()

    # 关闭数据源 Provider
    from app.services.data_source.factory import close_provider

    await close_provider()

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
        allow_headers=["Authorization", "Content-Type", "Accept", "Idempotency-Key"],
    )
    # S2-C5: 敏感端点速率限制
    app.add_middleware(RateLimitMiddleware)
    # S2-C6: 写操作幂等性（在限流之后，缓存命中时跳过限流）
    app.add_middleware(IdempotencyMiddleware)
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
    v1_router.include_router(tags.router)
    v1_router.include_router(aas.router)
    v1_router.include_router(performance.router)
    # S3-METRIC 节点级性能评估（GB/T 44693.2-2024 §6.4 综合评估）
    v1_router.include_router(node_performance.router)
    # S6 工作台门户：BFF 聚合层
    v1_router.include_router(dashboard.router)
    # S4 诊断中心：诊断、波形、Tracker、诊断标签
    # v4.0: tags_router 须在 diagnosis.router 之前注册，避免 GET /{loop_id} 拦截 /diagnosis/tags
    v1_router.include_router(diagnosis.tags_router)
    v1_router.include_router(diagnosis.router)
    v1_router.include_router(diagnosis.timeseries_router)
    v1_router.include_router(tags.timeseries_router)
    v1_router.include_router(diagnosis.tracker_router)
    # v4.0: DataPlanner 内部管理接口（仅 ADMIN）
    v1_router.include_router(dataplanner.router)
    # v4.0: 算法服务接口（IDS §2.7）
    v1_router.include_router(algorithms.router)
    # v4.0: 批量配置接口（IDS §2.8/§2.9）
    v1_router.include_router(configs.router)
    # v4.0: 评估任务管理（标准/自定义）
    v1_router.include_router(eval_tasks.router)
    # S5 系统管理：用户管理、审计日志、报表配置
    v1_router.include_router(users.router)
    v1_router.include_router(audit_logs.router)
    v1_router.include_router(reports.router)
    # S7 回路整定：模型辨识、PID 整定、闭环仿真
    v1_router.include_router(tuning.router)
    # 重构方案 v1.2：回路配置 CRUD（投用定义、类型权重、级别权重）
    v1_router.include_router(loop_mode_mapping.router)
    v1_router.include_router(loop_type_weight.router)
    v1_router.include_router(loop_level_weight.router)
    # 实时数据查询（从 Redis 缓存读取 SignalR 订阅数据）
    v1_router.include_router(realtime.router)
    v1_router.include_router(ws_realtime.router)
    app.include_router(v1_router)

    return app


app = create_app()
