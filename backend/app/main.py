"""FastAPI application entry point.

Wires up logging, CORS, global exception handlers and route prefixes:
- ``/health`` (root) for liveness probes
- ``/api/v1/*`` for business endpoints (auth, ...)
- ``/docs`` and ``/redoc`` for OpenAPI documentation

v6.1：lifespan 中自动启动 Celery Beat 调度进程，确保每小时 KPI 计算
等定时任务在项目启动后自动执行，无需手动启动 Beat。
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.endpoints import (
    aas,
    algorithms,
    audit_logs,
    auth,
    # v6.1: 数据可信度阈值配置
    confidence_config,
    configs,
    dashboard,
    dataplanner,
    datasource,
    # v6.1: DCS 配置管理（品牌/型号/MODE 定义/映射矩阵）
    dcs,
    diagnosis,
    grading_config,
    health,
    # Phase 3: 回路数据管理（历史数据导入）
    loop_data,
    loop_level_weight,
    loop_mode_mapping,
    loop_type_weight,
    loops,
    node_performance,
    outlier_config,
    performance,
    plant_nodes,
    realtime,
    reports,
    tags,
    tuning,
    users,
    weight_config,
    ws_realtime,
)
from app.api.v1.endpoints import (
    tasks as eval_tasks,
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

# Celery Beat 子进程引用（lifespan 管理）
_celery_beat_process: subprocess.Popen | None = None


def _is_production() -> bool:
    """判断当前是否为生产环境。

    生产环境由 docker-compose 独立 celery-beat 服务接管定时任务调度，
    backend lifespan 不再启动 Beat 子进程，避免重复执行。
    """
    return os.environ.get("ENV", "").lower() == "production"


def _any_beat_process_running() -> bool:
    """pgrep 扫描是否已有 celery beat 进程在运行（Beat 单例兜底检查）.

    pidfile 检查无法覆盖"pidfile 被另一个 beat 进程覆盖/删除"的场景
    （如手工启动的 beat 与 lifespan 自动启动的 beat 共用同一路径）。
    """
    try:
        result = subprocess.run(  # noqa: S603
            ["pgrep", "-f", "celery.*beat"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:  # noqa: BLE001
        # pgrep 不可用（极简容器等）时视为无其他 beat，交由 pidfile 检查兜底
        return False
    return result.returncode == 0 and bool(result.stdout.strip())


def _start_celery_beat() -> None:
    """启动 Celery Beat 调度子进程。

    在 FastAPI lifespan 中调用，确保定时任务（如每小时 KPI 计算）
    随后端启动自动运行。Beat 进程独立于 Celery worker，仅负责
    按 schedule 发送任务到队列。

    PersistentScheduler 使用文件锁（celerybeat-schedule），
    即使多个 Beat 进程启动也只有一个能运行。
    """
    global _celery_beat_process

    # 检查是否已有 Beat 进程在运行（通过 celerybeat.pid 文件）
    pid_file = os.path.join(os.getcwd(), "celerybeat.pid")
    if os.path.exists(pid_file):
        try:
            with open(pid_file) as f:
                old_pid = int(f.read().strip())
            os.kill(old_pid, 0)  # 检查进程是否存在
            logger.info("Celery Beat 已在运行 (PID=%s)，跳过启动", old_pid)
            return
        except (ProcessLookupError, ValueError, OSError):
            # 进程不存在，清理遗留的 PID 文件
            try:
                os.remove(pid_file)
            except OSError:
                pass

    # 兜底：pidfile 可能被手工启动的 beat 覆盖/失效（同一 pidfile 路径被
    # 两个 beat 共用时互相覆盖），用 pgrep 扫描确认无其他 celery beat
    # 进程在运行。两个 beat 并存会导致每个定时任务双触发（2026-07-20 实测）。
    if _any_beat_process_running():
        logger.info("检测到已有 Celery Beat 进程在运行（pgrep 兜底），跳过启动")
        return

    try:
        _celery_beat_process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "celery",
                "-A",
                "app.tasks.celery_app",
                "beat",
                "-l",
                "info",
                "--pidfile",
                pid_file,
            ],
            cwd=os.getcwd(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info(
            "Celery Beat 调度进程已启动 (PID=%s)，定时任务将自动执行",
            _celery_beat_process.pid,
        )
    except Exception as exc:
        logger.warning("启动 Celery Beat 失败（定时任务将不会自动执行）: %s", exc)


def _stop_celery_beat() -> None:
    """停止 Celery Beat 调度子进程。"""
    global _celery_beat_process
    process = _celery_beat_process
    if process is not None:
        logger.info("停止 Celery Beat 调度进程...")
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
        _celery_beat_process = None
        logger.info("Celery Beat 调度进程已停止")

        # 只清理由当前 FastAPI 实例创建的 Beat PID 文件。
        # reload 子进程若只是检测到外部 Beat 并跳过启动，绝不能删除对方
        # 的 PID 文件，否则下一次 reload 会再启动一个重复 Beat。
        pid_file = os.path.join(os.getcwd(), "celerybeat.pid")
        try:
            with open(pid_file) as file:
                pid_from_file = int(file.read().strip())
            if pid_from_file == process.pid:
                os.remove(pid_file)
        except (FileNotFoundError, ProcessLookupError, ValueError, OSError):
            pass


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Application lifespan: initialise resources on startup, clean up on shutdown."""
    setup_logging()
    logger.info("Starting %s v%s", settings.APP_NAME, settings.APP_VERSION)
    logger.info("数据源: 计算=本地 TDengine（性能评估/诊断/整定），远端 API 仅历史数据导入任务调用")

    # v6.1：自动启动 Celery Beat 调度进程
    # 生产环境由 docker-compose 独立 celery-beat 服务接管，避免重复启动
    if not _is_production():
        _start_celery_beat()
    else:
        logger.info("生产环境：Celery Beat 由独立容器接管，跳过 lifespan 启动")

    # 从 sys_config 预载数据源配置到 settings（运行时真相源优先于 .env）
    # 方案 B：.env 仅保留基础设施配置 + 合理默认值，业务 URL/Token/SignalR Hub
    # 等由 sys_config 管理。预载确保 SignalR 订阅器等启动时组件读取到 sys_config
    # 中的配置，而不是 .env 中的空值。预载失败不应阻塞启动，兜底使用 .env 默认值。
    from app.core.db import AsyncSessionLocal
    from app.services.datasource_config import preload_datasource_config

    try:
        async with AsyncSessionLocal() as db:
            await preload_datasource_config(db)
        logger.info(
            "数据源配置已从 sys_config 预载（network_mode=%s, signalr_enabled=%s, "
            "history_api_url=%s, signalr_hub_url=%s）",
            settings.NETWORK_MODE,
            settings.SIGNALR_ENABLED,
            "已配置" if settings.HISTORY_DATA_API_URL else "未配置",
            "已配置" if settings.SIGNALR_HUB_URL else "未配置",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("从 sys_config 预载数据源配置失败（将使用 .env 默认值）: %s", exc)

    # 从 sys_config 预载异常值检测参数/开关到进程内缓存（热路径不查库，
    # 预载失败回落 thresholds.py 算法默认值，不阻塞启动）
    from app.services.preprocessing.outlier_params import preload_outlier_params

    try:
        async with AsyncSessionLocal() as db:
            await preload_outlier_params(db)
    except Exception as exc:  # noqa: BLE001
        logger.warning("从 sys_config 预载异常值检测参数失败（将使用算法默认值）: %s", exc)

    # 启动实时数据订阅（如已启用）
    from app.services.data_source.realtime_subscriber import start_subscriber

    await start_subscriber()

    yield

    logger.info("Shutting down %s", settings.APP_NAME)

    # 停止 Celery Beat
    if not _is_production():
        _stop_celery_beat()

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
    # Phase 3: 回路数据管理（历史数据导入）
    v1_router.include_router(loop_data.router)
    v1_router.include_router(tags.router)
    v1_router.include_router(aas.router)
    v1_router.include_router(datasource.router)
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
    # v5.3: 权重模板管理（FDS §5.2.2）+ 定级阈值管理（FDS §5.2.4）
    v1_router.include_router(weight_config.router)
    v1_router.include_router(grading_config.router)
    # v6.1: 数据可信度阈值管理
    v1_router.include_router(confidence_config.router)

    # v6.2: 8 类异常值检测参数与启停开关配置
    v1_router.include_router(outlier_config.router)
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
    # v6.1：DCS 配置管理（品牌/型号/MODE 定义/映射矩阵）
    v1_router.include_router(dcs.router)
    # 实时数据查询（从 Redis 缓存读取 SignalR 订阅数据）
    v1_router.include_router(realtime.router)
    v1_router.include_router(ws_realtime.router)
    app.include_router(v1_router)

    return app


app = create_app()
