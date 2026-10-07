"""工作台 v2.0 Celery 任务 — 预计算 / 事件归档 / 缓存清理 / MV 刷新。

M1 阶段为 skeleton：每个任务执行最小查询验证 DB 连通 + 记录日志，
不修改业务数据（``refresh_workbench_mv`` 例外：REFRESH MATERIALIZED VIEW
是只读刷新，不修改源表）。M2 填充完整业务逻辑。

Beat 调度（追加式注册，不覆盖其他模块）：
- ``workbench-precalc``（5min）：三窗口 KPI 预计算 → upsert workbench_window_summary
- ``event-archive``（daily 03:30）：event_bus 归档（保留 90d）
- ``wb-cache-cleanup``（1min）：清理 wb_cache_log 过期记录
- ``refresh-workbench-mv``（5min，错峰 2min）：刷新 3 个物化视图

2026-10-05 用户裁决②：``sla-sweep``（1min 骨架，仅 COUNT + todo 注释空转）
删除——处置域滞留提醒由 ``handling_remind``（每日 08:30 聚合单条 NOTIFY）
承接；``handling_order.sla_*`` 列与统计读取保留（历史归档口径）。

注意：Beat + Worker 由后端 lifespan 自动启动，**严禁手工再启动**
（多个 worker/beat 并存会导致任务重复消费或双触发）。
"""

from __future__ import annotations

import logging
from typing import Any

from celery.schedules import crontab

from app.tasks.celery_app import AsyncTask, celery_app

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Task 1: workbench_precalc — 三窗口 KPI 预计算（5min）
# ---------------------------------------------------------------------------
@celery_app.task(base=AsyncTask, bind=True, name="app.tasks.workbench.workbench_precalc")
def workbench_precalc(self: AsyncTask, *args: object, **kwargs: object) -> dict[str, Any]:
    """三窗口（24h/7d/30d）KPI 预计算 → upsert workbench_window_summary。

    M2：以 unit_kpi_summary 为数据源，按 GLOBAL/FACTORY/AREA/UNIT × 三窗口
    加权聚合 upsert（详见 app/services/workbench_precalc.py 口径注释）。
    """
    return self.run_async(_workbench_precalc_async())


async def _workbench_precalc_async() -> dict[str, Any]:
    from app.core.db import AsyncSessionLocal
    from app.services.workbench_precalc import build_and_upsert

    async with AsyncSessionLocal() as db:
        return await build_and_upsert(db)


# ---------------------------------------------------------------------------
# Task 2: event_archive — 事件归档（daily 03:30）
# ---------------------------------------------------------------------------
@celery_app.task(base=AsyncTask, bind=True, name="app.tasks.workbench.event_archive")
def event_archive(self: AsyncTask, *args: object, **kwargs: object) -> dict[str, Any]:
    """event_bus 归档：保留 90d，超期记录迁移到 event_bus_archive（M2）或删除。

    M1 skeleton：查询 >90d 的事件数 + 日志。M2 填充：迁移到归档表。
    """
    return self.run_async(_event_archive_async())


async def _event_archive_async() -> dict[str, Any]:
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import func, select

    from app.core.db import AsyncSessionLocal
    from app.models.event_bus import EventBus

    cutoff = datetime.now(UTC) - timedelta(days=90)

    async with AsyncSessionLocal() as db:
        stale_count = await db.scalar(
            select(func.count()).select_from(EventBus).where(EventBus.created_at < cutoff)
        )
    logger.info("event_archive skeleton: >90d 事件 %s 条待归档", stale_count)
    return {"status": "skeleton", "stale_count": stale_count, "todo": "M2 填充归档迁移"}


# ---------------------------------------------------------------------------
# Task 3: wb_cache_cleanup — 缓存日志清理（1min）
# ---------------------------------------------------------------------------
@celery_app.task(base=AsyncTask, bind=True, name="app.tasks.workbench.wb_cache_cleanup")
def wb_cache_cleanup(self: AsyncTask, *args: object, **kwargs: object) -> dict[str, Any]:
    """清理 wb_cache_log 过期记录（保留 7d）。

    M1 skeleton：查询过期记录数 + 日志。M2 填充：DELETE 过期记录。
    """
    return self.run_async(_wb_cache_cleanup_async())


async def _wb_cache_cleanup_async() -> dict[str, Any]:
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import func, select

    from app.core.db import AsyncSessionLocal
    from app.models.wb_cache_log import WbCacheLog

    cutoff = datetime.now(UTC) - timedelta(days=7)

    async with AsyncSessionLocal() as db:
        stale_count = await db.scalar(
            select(func.count()).select_from(WbCacheLog).where(WbCacheLog.created_at < cutoff)
        )
    logger.info("wb_cache_cleanup skeleton: >7d 缓存日志 %s 条待清理", stale_count)
    return {"status": "skeleton", "stale_count": stale_count, "todo": "M2 填充 DELETE"}


# ---------------------------------------------------------------------------
# Task 4: refresh_workbench_mv — 物化视图刷新（5min，与 precalc 错峰 2min）
# ---------------------------------------------------------------------------
@celery_app.task(base=AsyncTask, bind=True, name="app.tasks.workbench.refresh_workbench_mv")
def refresh_workbench_mv(self: AsyncTask, *args: object, **kwargs: object) -> dict[str, Any]:
    """刷新 3 个物化视图（CONCURRENTLY，需 UNIQUE INDEX）。

    M1 已实现：REFRESH MATERIALIZED VIEW CONCURRENTLY（只读刷新，不修改源表）。
    """
    return self.run_async(_refresh_workbench_mv_async())


_MATERIALIZED_VIEWS = ("mv_staff_workload", "mv_diagnosis_pareto", "mv_handling_funnel")


async def _refresh_workbench_mv_async() -> dict[str, Any]:
    from sqlalchemy import text

    from app.core.db import AsyncSessionLocal

    refreshed: list[str] = []
    async with AsyncSessionLocal() as db:
        for mv_name in _MATERIALIZED_VIEWS:
            await db.execute(text(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {mv_name}"))  # noqa: S608
            refreshed.append(mv_name)
        await db.commit()
    logger.info("refresh_workbench_mv: 已刷新 %s", ", ".join(refreshed))
    return {"status": "ok", "refreshed": refreshed}


# ---------------------------------------------------------------------------
# Beat 调度注册（追加式，不覆盖其他模块的 beat_schedule）
# ---------------------------------------------------------------------------
_existing_beat = getattr(celery_app.conf, "beat_schedule", None) or {}
_existing_beat.update(
    {
        "workbench-precalc": {
            "task": "app.tasks.workbench.workbench_precalc",
            "schedule": crontab(minute="*/5"),
        },
        "event-archive": {
            "task": "app.tasks.workbench.event_archive",
            "schedule": crontab(hour=3, minute=30),
        },
        "wb-cache-cleanup": {
            "task": "app.tasks.workbench.wb_cache_cleanup",
            "schedule": crontab(minute="*/1"),
        },
        # 与 precalc 错峰 2min：precalc 在 0/5/10...，MV 刷新在 2/7/12...
        "refresh-workbench-mv": {
            "task": "app.tasks.workbench.refresh_workbench_mv",
            "schedule": crontab(minute="2,7,12,17,22,27,32,37,42,47,52,57"),
        },
    }
)
celery_app.conf.beat_schedule = _existing_beat
