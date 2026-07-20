"""Celery diagnosis engine (IDS v3.2 §2.4 — S4-DIAG-002).

设计要点：
- 监听回路评分跌破阈值事件（从 kpi_snapshot_hourly 检测）
- 从 TDengine 拉取波形数据（PV/SP/OP/MODE）
- 执行 FFT 频域分析（振荡检测）
- 执行 PV-OP 散点拟合（阀门粘滞检测）
- 按 diagnosis_config 输出预诊标签
- 诊断结果写入 diagnosis_result 表
- 5 并发 worker
- 失败重试 3 次
- 使用 Dempster-Shafer 证据理论融合多算法置信度
- TDengine 不可用时优雅降级
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import numpy as np
from celery.schedules import crontab
from sqlalchemy import delete, or_, select

from app.contracts.data_types import QualityStatus, RawTimeSeries
from app.models.diagnosis import DiagnosisConfig, DiagnosisResult, DiagnosisTag, DiagnosisTask
from app.models.loop import LoopLedger, LoopTagMapping
from app.models.metric import KpiSnapshotHourly
from app.models.tag import TagRegistry
from app.services.preprocessing.quality_code import map_quality_code
from app.tasks.celery_app import AsyncTask, celery_app

logger = logging.getLogger(__name__)

# 算法版本号
DIAG_ALGORITHM_VERSION = "DIAG_ENGINE_v1.0"

# 评分阈值：跌破此值触发诊断
SCORE_THRESHOLD = Decimal("60")

# 并发 worker 数
CONCURRENCY = 5

# 数据最少点数
MIN_DATA_POINTS = 32

# 诊断标签严重等级映射（A11：写入 diagnosis_tag.severity）
_TAG_SEVERITY_MAP: dict[str, str] = {
    "VALVE_STICTION": "ERROR",
    "QUALITY_ABNORMAL": "ERROR",
    "OSCILLATION": "WARN",
    "OVERAGGRESSIVE": "WARN",
    "OVERCONSERVATIVE": "WARN",
    "OUTPUT_SATURATION": "WARN",
    "EXTERNAL_DISTURBANCE": "INFO",
    "MANUAL_REVIEW": "INFO",
}

# 诊断标签中文名（与 app.services.diagnosis.DIAG_LABEL_NAMES 保持一致）
_TAG_LABEL_NAMES: dict[str, str] = {
    "OSCILLATION": "振荡",
    "VALVE_STICTION": "阀门粘滞",
    "OVERAGGRESSIVE": "参数过激",
    "OVERCONSERVATIVE": "参数过保守",
    "EXTERNAL_DISTURBANCE": "外扰频繁",
    "QUALITY_ABNORMAL": "PV 质量异常",
    "OUTPUT_SATURATION": "输出饱和",
    "MANUAL_REVIEW": "人工复核",
}

# evidence 未携带 algorithm 字段时的来源指标兜底（A11：写入 diagnosis_tag.source_metric）
_TAG_SOURCE_METRIC_FALLBACK: dict[str, str] = {
    "QUALITY_ABNORMAL": "PV_QUALITY_STATS",
    "OUTPUT_SATURATION": "OP_SATURATION_STATS",
    "MANUAL_REVIEW": "MANUAL_REVIEW",
}


# ---------------------------------------------------------------------------
# Celery 任务定义
# ---------------------------------------------------------------------------


@celery_app.task(
    name="app.tasks.diagnosis_engine.run_diagnosis_hourly",
    bind=True,
    base=AsyncTask,
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 3, "countdown": 60},
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
)
def run_diagnosis_hourly(self: AsyncTask) -> dict:
    """每小时全量诊断：扫描评分跌破阈值的回路并执行诊断。

    失败自动重试 3 次，指数退避。
    """
    logger.info("诊断引擎任务开始, task_id=%s", self.request.id)
    try:
        result = self.run_async(_do_run_diagnosis())
        logger.info("诊断引擎任务完成: %s", result)
        return result
    except Exception:
        logger.exception("诊断引擎任务失败")
        raise


@celery_app.task(
    name="app.tasks.diagnosis_engine.run_diagnosis_checkup",
    bind=True,
    base=AsyncTask,
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 3, "countdown": 60},
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
)
def run_diagnosis_checkup(self: AsyncTask) -> dict:
    """每 8 小时体检轨：对全部启用回路执行周期性健康检查（不受评分阈值限制）。

    失败自动重试 3 次，指数退避。
    """
    logger.info("体检轨任务开始, task_id=%s", self.request.id)
    try:
        result = self.run_async(_do_run_checkup())
        logger.info("体检轨任务完成: %s", result)
        return result
    except Exception:
        logger.exception("体检轨任务失败")
        raise


@celery_app.task(
    name="app.tasks.diagnosis_engine.run_loop_diagnosis",
    base=AsyncTask,
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 3, "countdown": 60},
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
)
def run_loop_diagnosis(
    loop_id: str,
    ts_start: str | None = None,
    task_id: str | None = None,
    time_range_start: str | None = None,
    time_range_end: str | None = None,
    labels: list[str] | None = None,
) -> dict:
    """单回路诊断（可手动触发）。

    支持两种时间范围参数（向后兼容）：
    - 旧参数：ts_start（向后兼容，自动推算 ts_end = ts_start + 1h）
    - 新参数：time_range_start / time_range_end（诊断任务专用，支持自定义时间窗）

    labels（B6 按需诊断）为可选的诊断标签子集：None 表示全量执行；
    指定子集时仅执行子集内标签对应的算法（MANUAL_REVIEW 兜底不受子集限制）。

    当 task_id 不为 None 时，会更新 diagnosis_task 状态：
    - 开始时：PENDING → RUNNING
    - 成功时：RUNNING → SUCCESS
    - 失败时：RUNNING → FAILED（记录 error_message）
    """
    logger.info(
        "单回路诊断, loop_id=%s, task_id=%s, time_range=%s~%s, labels=%s",
        loop_id,
        task_id,
        time_range_start,
        time_range_end,
        labels,
    )
    return AsyncTask().run_async(
        _do_diagnose_single_loop(
            loop_id,
            ts_start=ts_start,
            task_id=task_id,
            time_range_start=time_range_start,
            time_range_end=time_range_end,
            labels=labels,
        )
    )


# ---------------------------------------------------------------------------
# Beat 调度配置
# ---------------------------------------------------------------------------


_beat_entry = {
    "task": "app.tasks.diagnosis_engine.run_diagnosis_hourly",
    # 对齐 KPI 整点计算（crontab minute=0），在整点后第 10 分钟执行诊断，
    # 避免裸 3600s 间隔与 KPI 计算相位错位导致漏诊
    "schedule": crontab(minute=10),
}

_existing_beat = getattr(celery_app.conf, "beat_schedule", None) or {}
_existing_beat["diagnosis-engine-hourly"] = _beat_entry
# 体检轨（B1）：每 8 小时对全部启用回路做健康检查，与事件轨 minute=10 错开
_existing_beat["diagnosis-engine-checkup-8h"] = {
    "task": "app.tasks.diagnosis_engine.run_diagnosis_checkup",
    "schedule": crontab(minute=20, hour="*/8"),
}
celery_app.conf.beat_schedule = _existing_beat
celery_app.conf.timezone = "Asia/Shanghai"


# ---------------------------------------------------------------------------
# 异步诊断逻辑
# ---------------------------------------------------------------------------


async def _do_run_diagnosis() -> dict:
    """执行全量诊断的实际 async 逻辑。

    自动触发时也为每个回路创建 DiagnosisTask 记录（trigger_type='auto'），
    与手动触发的任务记录统一管理。
    """
    from app.core.db import AsyncSessionLocal

    now = datetime.now(UTC)
    ts_end = now.replace(minute=0, second=0, microsecond=0)
    ts_start = ts_end - timedelta(hours=1)
    # naive datetime 用于入库（diagnosis_task.time_range_* 为 TIMESTAMP WITHOUT TIME ZONE）
    ts_start_naive = ts_start.replace(tzinfo=None)
    ts_end_naive = ts_end.replace(tzinfo=None)

    # 主 session 仅用于查询待诊断回路列表和诊断配置（只读，无并发）
    async with AsyncSessionLocal() as db:
        # 1. 查询最近一小时评分跌破阈值的回路
        # 评分为 NULL（数据质量差 INCONCLUSIVE）的回路同样需要诊断，一并纳入
        snapshot_stmt = (
            select(KpiSnapshotHourly)
            .where(KpiSnapshotHourly.ts_start >= ts_start_naive)
            .where(KpiSnapshotHourly.ts_start <= ts_end_naive)
            .where(
                or_(
                    KpiSnapshotHourly.score < SCORE_THRESHOLD,
                    KpiSnapshotHourly.score.is_(None),
                )
            )
            .where(KpiSnapshotHourly.status == "SUCCESS")
        )
        snap_result = await db.execute(snapshot_stmt)
        snapshots = list(snap_result.scalars().all())

        logger.info("待诊断回路数: %d", len(snapshots))

        if not snapshots:
            return {"total": 0, "diagnosed": 0, "failed": 0}

        # 去重 loop_id
        loop_ids = list({str(s.loop_id) for s in snapshots if s.loop_id})

        # 2. 加载诊断配置
        config_result = await db.execute(
            select(DiagnosisConfig).where(DiagnosisConfig.is_enabled.is_(True))
        )
        diag_configs = {c.diag_code: c for c in config_result.scalars().all()}

        # 3. 为每个回路创建 DiagnosisTask 记录（自动触发）
        # 去重：同回路同时间窗已存在 PENDING/RUNNING 任务时跳过创建与诊断，
        # 避免 Beat 重复运行（或手动补跑）重复建任务
        existing_result = await db.execute(
            select(DiagnosisTask.loop_id)
            .where(DiagnosisTask.loop_id.in_(loop_ids))
            .where(DiagnosisTask.time_range_start == ts_start_naive)
            .where(DiagnosisTask.time_range_end == ts_end_naive)
            .where(DiagnosisTask.status.in_(["PENDING", "RUNNING"]))
        )
        existing_loop_ids = {str(r) for r in existing_result.scalars().all()}

        # 显式生成 id 避免 flush 依赖（兼容 mock 测试环境）
        loop_task_ids: dict[str, str] = {}
        for lid in loop_ids:
            if lid in existing_loop_ids:
                logger.info("回路 %s 同时间窗已有未完成任务，跳过创建", lid)
                continue
            task_id = str(uuid4())
            task = DiagnosisTask(
                id=task_id,
                loop_id=lid,
                trigger_type="auto",
                triggered_by="system",
                status="PENDING",
                time_range_start=ts_start_naive,
                time_range_end=ts_end_naive,
            )
            db.add(task)
            loop_task_ids[lid] = task_id
        await db.commit()

    # 4. 并发诊断（信号量限制并发数，每协程独立 session 避免并发共享）
    diagnosed_count, failed_count = await _run_diag_tasks_concurrent(
        loop_task_ids, diag_configs, ts_start, ts_end
    )

    return {
        "total": len(loop_ids),
        "diagnosed": diagnosed_count,
        "failed": failed_count,
        "skipped": len(loop_ids) - len(loop_task_ids),
        "ts_start": ts_start.isoformat(),
        "ts_end": ts_end.isoformat(),
    }


async def _run_diag_tasks_concurrent(
    loop_task_ids: dict[str, str],
    diag_configs: dict[str, DiagnosisConfig],
    ts_start: datetime,
    ts_end: datetime,
) -> tuple[int, int]:
    """并发执行诊断任务（信号量限流，每协程独立 session 避免并发共享）。

    事件轨与体检轨共用：对每个 loop 进入 RUNNING → 诊断 → SUCCESS/FAILED 状态机。

    Args:
        loop_task_ids: loop_id → task_id 映射（仅含需执行的回路）
        diag_configs: 诊断配置字典
        ts_start: 时间窗起始
        ts_end: 时间窗结束

    Returns:
        (diagnosed_count, failed_count)
    """
    from app.core.db import AsyncSessionLocal
    from app.services.data_source.factory import get_provider

    sem = asyncio.Semaphore(CONCURRENCY)

    async def _diag_with_sem(loop_id: str, task_id: str) -> dict | None:
        async with sem:
            # 每协程独立 session，避免 AsyncSession 并发共享导致的不可预期错误
            async with AsyncSessionLocal() as worker_db:
                query_wide_fn = get_provider().make_query_fn(worker_db)
                # 进入 RUNNING 状态
                await _update_task_status(worker_db, task_id, "RUNNING")
                await worker_db.commit()
                try:
                    result = await _diagnose_loop(
                        db=worker_db,
                        loop_id=loop_id,
                        diag_configs=diag_configs,
                        ts_start=ts_start,
                        ts_end=ts_end,
                        query_wide_fn=query_wide_fn,
                        task_id=task_id,
                    )
                    await worker_db.commit()
                    # 根据诊断结果更新任务状态
                    now_naive = datetime.now(UTC).replace(tzinfo=None)
                    if result is None:
                        # 诊断未产出结果（如缺少 PV Tag），标记为 FAILED
                        await _update_task_status(
                            worker_db,
                            task_id,
                            "FAILED",
                            error_message="诊断未产出结果",
                            completed_at=now_naive,
                        )
                    else:
                        # 成功：更新任务状态为 SUCCESS
                        await _update_task_status(
                            worker_db,
                            task_id,
                            "SUCCESS",
                            completed_at=now_naive,
                        )
                    await worker_db.commit()
                    return result
                except Exception as exc:
                    await worker_db.rollback()
                    # 失败：更新任务状态为 FAILED
                    try:
                        await _update_task_status(
                            worker_db,
                            task_id,
                            "FAILED",
                            error_message=str(exc),
                            completed_at=datetime.now(UTC).replace(tzinfo=None),
                        )
                        await worker_db.commit()
                    except Exception:
                        logger.exception("更新任务 %s 状态为 FAILED 失败", task_id)
                    raise

    tasks = [asyncio.create_task(_diag_with_sem(lid, tid)) for lid, tid in loop_task_ids.items()]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    diagnosed_count = 0
    failed_count = 0
    for r in results:
        if isinstance(r, Exception):
            failed_count += 1
            logger.warning("回路诊断失败: %s", r)
        elif r is None:
            failed_count += 1
        else:
            diagnosed_count += 1

    return diagnosed_count, failed_count


async def _do_run_checkup() -> dict:
    """体检轨（B1）：对全部启用回路执行周期性健康检查。

    与事件轨 _do_run_diagnosis 的区别：
    - 覆盖对象：全部 status='READY' 的启用回路（不受评分阈值限制，健康回路也纳入）
    - 任务标识：trigger_type='auto' + triggered_by='checkup-scheduler'
    - 开关：EngineRule（rule_type='SCHEDULE', rule_code='DIAG_CHECKUP'）的
      params.enabled 控制，默认启用；关闭时记日志并跳过
    """
    from app.core.db import AsyncSessionLocal
    from app.services.engine_rule_loader import get_engine_rule_loader

    now = datetime.now(UTC)
    ts_end = now.replace(minute=0, second=0, microsecond=0)
    ts_start = ts_end - timedelta(hours=1)
    # naive datetime 用于入库（diagnosis_task.time_range_* 为 TIMESTAMP WITHOUT TIME ZONE）
    ts_start_naive = ts_start.replace(tzinfo=None)
    ts_end_naive = ts_end.replace(tzinfo=None)

    # 主 session 仅用于开关检查、查询回路列表和诊断配置（只读，无并发）
    async with AsyncSessionLocal() as db:
        # 0. 开关检查（EngineRuleLoader 带 60s 缓存，规则缺失时回退默认启用）
        checkup_params = await get_engine_rule_loader().get_params(db, "DIAG_CHECKUP")
        if not checkup_params.get("enabled", True):
            logger.info("体检轨调度已禁用（DIAG_CHECKUP.enabled=false），跳过本次运行")
            return {"total": 0, "diagnosed": 0, "failed": 0, "disabled": True}

        # 1. 查询全部启用回路（不受评分限制）
        loop_result = await db.execute(select(LoopLedger).where(LoopLedger.status == "READY"))
        loop_ids = [str(lo.id) for lo in loop_result.scalars().all() if lo.id]

        logger.info("体检轨待检查回路数: %d", len(loop_ids))

        if not loop_ids:
            return {"total": 0, "diagnosed": 0, "failed": 0}

        # 2. 加载诊断配置
        config_result = await db.execute(
            select(DiagnosisConfig).where(DiagnosisConfig.is_enabled.is_(True))
        )
        diag_configs = {c.diag_code: c for c in config_result.scalars().all()}

        # 3. 为每个回路创建 DiagnosisTask 记录（triggered_by='checkup-scheduler'）
        # 去重：同回路同时间窗已存在 PENDING/RUNNING 任务时跳过创建与诊断，
        # 避免与事件轨/手动触发重复建任务
        existing_result = await db.execute(
            select(DiagnosisTask.loop_id)
            .where(DiagnosisTask.loop_id.in_(loop_ids))
            .where(DiagnosisTask.time_range_start == ts_start_naive)
            .where(DiagnosisTask.time_range_end == ts_end_naive)
            .where(DiagnosisTask.status.in_(["PENDING", "RUNNING"]))
        )
        existing_loop_ids = {str(r) for r in existing_result.scalars().all()}

        # 显式生成 id 避免 flush 依赖（兼容 mock 测试环境）
        loop_task_ids: dict[str, str] = {}
        for lid in loop_ids:
            if lid in existing_loop_ids:
                logger.info("回路 %s 同时间窗已有未完成任务，跳过创建", lid)
                continue
            task_id = str(uuid4())
            task = DiagnosisTask(
                id=task_id,
                loop_id=lid,
                trigger_type="auto",
                triggered_by="checkup-scheduler",
                status="PENDING",
                time_range_start=ts_start_naive,
                time_range_end=ts_end_naive,
            )
            db.add(task)
            loop_task_ids[lid] = task_id
        await db.commit()

    # 4. 并发诊断（与事件轨共用并发执行逻辑）
    diagnosed_count, failed_count = await _run_diag_tasks_concurrent(
        loop_task_ids, diag_configs, ts_start, ts_end
    )

    return {
        "total": len(loop_ids),
        "diagnosed": diagnosed_count,
        "failed": failed_count,
        "skipped": len(loop_ids) - len(loop_task_ids),
        "ts_start": ts_start.isoformat(),
        "ts_end": ts_end.isoformat(),
    }


async def _do_diagnose_single_loop(
    loop_id: str,
    ts_start: str | None = None,
    task_id: str | None = None,
    time_range_start: str | None = None,
    time_range_end: str | None = None,
    labels: list[str] | None = None,
) -> dict:
    """单回路诊断。

    支持两种时间范围参数（向后兼容）：
    - 旧参数：ts_start（向后兼容，自动推算 ts_end = ts_start + 1h）
    - 新参数：time_range_start / time_range_end（诊断任务专用）

    labels（B6 按需诊断）为可选的诊断标签子集，透传至 _diagnose_loop 做算法门控。

    当 task_id 不为 None 时，更新 diagnosis_task 状态机：
    - PENDING → RUNNING（开始时）
    - RUNNING → SUCCESS / FAILED（完成时）
    """
    from app.core.db import AsyncSessionLocal
    from app.services.data_source.factory import get_provider

    async with AsyncSessionLocal() as db:
        # 获取宽表查询函数（适配 tdengine/remote_api）
        query_wide_fn = get_provider().make_query_fn(db)

        # 加载诊断配置
        config_result = await db.execute(
            select(DiagnosisConfig).where(DiagnosisConfig.is_enabled.is_(True))
        )
        diag_configs = {c.diag_code: c for c in config_result.scalars().all()}

        now = datetime.now(UTC)
        # 解析时间范围：优先使用 time_range_start/time_range_end，其次 ts_start
        if time_range_end:
            ts_end_dt = _parse_iso_to_naive(time_range_end)
        else:
            ts_end_dt = now.replace(tzinfo=None)

        if time_range_start:
            ts_start_dt = _parse_iso_to_naive(time_range_start)
        elif ts_start:
            ts_start_dt = _parse_iso_to_naive(ts_start)
            # 旧模式：ts_start + 1h = ts_end
            ts_end_dt = ts_start_dt + timedelta(hours=1)
        else:
            ts_start_dt = ts_end_dt - timedelta(hours=1)

        # 如果有 task_id，更新任务状态为 RUNNING（立即 commit，避免卡在数据查询时状态仍为 PENDING）
        if task_id:
            await _update_task_status(db, task_id, "RUNNING")
            await db.commit()

        try:
            result = await _diagnose_loop(
                db=db,
                loop_id=loop_id,
                diag_configs=diag_configs,
                ts_start=ts_start_dt,
                ts_end=ts_end_dt,
                query_wide_fn=query_wide_fn,
                task_id=task_id,
                labels=labels,
            )
            await db.commit()
            if result is None:
                # _diagnose_loop 返回 None 表示诊断未执行成功
                # （回路不存在/缺少 PV Tag/TDengine 查询失败/数据点不足等）
                # 此时没有写入 DiagnosisResult，任务应标记为 FAILED
                if task_id:
                    await _update_task_status(
                        db,
                        task_id,
                        "FAILED",
                        error_message="诊断未产出结果（回路不存在/缺少PV位号/数据查询失败/数据点不足）",
                        completed_at=datetime.now(UTC).replace(tzinfo=None),
                    )
                    await db.commit()
                return {"loopId": loop_id, "status": "FAILED"}
            # 成功：更新任务状态为 SUCCESS
            if task_id:
                await _update_task_status(
                    db, task_id, "SUCCESS", completed_at=datetime.now(UTC).replace(tzinfo=None)
                )
                await db.commit()
            return result
        except Exception as exc:
            # 失败：更新任务状态为 FAILED
            if task_id:
                await _update_task_status(
                    db,
                    task_id,
                    "FAILED",
                    error_message=str(exc),
                    completed_at=datetime.now(UTC).replace(tzinfo=None),
                )
                await db.commit()
            raise


def _parse_iso_to_naive(time_str: str) -> datetime:
    """解析 ISO 8601 时间字符串为 naive datetime。

    兼容带 Z 后缀、带时区偏移、不带时区三种格式，统一剥离 tzinfo。
    """
    try:
        dt = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
    except ValueError:
        dt = datetime.fromisoformat(time_str)
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    return dt


async def _update_task_status(
    db,
    task_id: str,
    status: str,
    error_message: str | None = None,
    completed_at: datetime | None = None,
) -> None:
    """更新诊断任务状态（内部辅助函数）。

    Args:
        db: 异步数据库会话
        task_id: 诊断任务 ID
        status: 目标状态（RUNNING/SUCCESS/FAILED/CANCELLED）
        error_message: 错误信息（FAILED 时填写）
        completed_at: 完成时间（SUCCESS/FAILED 时填写）
    """
    result = await db.execute(select(DiagnosisTask).where(DiagnosisTask.id == task_id))
    task = result.scalar_one_or_none()
    if task is None:
        logger.warning("诊断任务 %s 不存在，无法更新状态为 %s", task_id, status)
        return
    task.status = status
    if error_message is not None:
        task.error_message = error_message
    if completed_at is not None:
        task.completed_at = completed_at


def _upsert_diagnosis_tag(
    db,
    active_tag_map: dict[str, Any],
    loop_id: str,
    result: dict[str, Any],
    diagnosed_at: datetime,
) -> None:
    """同步诊断标签到 diagnosis_tag 表（A11，IDS §2.4.10）。

    同 loop_id + tag_code 已有 ACTIVE 行：更新最近触发时间与触发快照
    （trigger_condition/trigger_value）；否则插入新 ACTIVE 行。

    Args:
        db: 异步数据库会话
        active_tag_map: 该回路现有 ACTIVE 标签映射（tag_code → DiagnosisTag），
            插入新行后会同步更新该映射，避免同批次重复建行
        loop_id: 回路 ID
        result: D-S 融合后的单条诊断标签结果（label/confidence/evidence）
        diagnosed_at: 本次诊断时间
    """
    label = result["label"]
    evidence = result.get("evidence") or {}
    confidence = float(result.get("confidence") or 0.0)
    algorithm = evidence.get("algorithm")
    source_metric = algorithm or _TAG_SOURCE_METRIC_FALLBACK.get(label, "DIAG_ENGINE")
    trigger_condition = {
        "algorithm": algorithm,
        "confidence": round(confidence, 4),
        "reasoning": evidence.get("reasoning"),
    }
    trigger_value = Decimal(str(round(confidence, 4)))
    severity = _TAG_SEVERITY_MAP.get(label, "INFO")

    existing = active_tag_map.get(label)
    if existing is not None:
        existing.triggered_at = diagnosed_at
        existing.trigger_condition = trigger_condition
        existing.trigger_value = trigger_value
        existing.source_metric = source_metric
        existing.severity = severity
        return

    tag = DiagnosisTag(
        id=str(uuid4()),
        loop_id=loop_id,
        tag_code=label,
        tag_name=_TAG_LABEL_NAMES.get(label),
        severity=severity,
        source_metric=source_metric,
        trigger_condition=trigger_condition,
        trigger_value=trigger_value,
        triggered_at=diagnosed_at,
        status="ACTIVE",
    )
    db.add(tag)
    active_tag_map[label] = tag


async def _diagnose_loop(
    db,
    loop_id: str,
    diag_configs: dict[str, DiagnosisConfig],
    ts_start: datetime,
    ts_end: datetime,
    query_wide_fn,
    task_id: str | None = None,
    labels: list[str] | None = None,
) -> dict | None:
    """对单回路执行诊断。

    Args:
        db: 异步数据库会话
        loop_id: 回路 ID
        diag_configs: 诊断配置字典
        ts_start: 时间窗起始
        ts_end: 时间窗结束
        query_wide_fn: 宽表查询函数
        task_id: 关联诊断任务 ID（可选，用于关联 DiagnosisResult）
        labels: 诊断标签子集（B6 按需诊断，可选）。None 表示全量执行；
            指定子集时仅执行子集内标签对应的算法（在 is_enabled 门控之上叠加），
            MANUAL_REVIEW 兜底标签不受子集限制

    Returns:
        诊断结果字典
    """
    # 统一转换为 naive datetime：
    # - TDengine REST API 不支持 ISO 8601 时区后缀（+00:00 / Z）
    # - PostgreSQL diagnosis_result.diagnosed_at 列为 TIMESTAMP WITHOUT TIME ZONE
    #   asyncpg 不允许 tz-aware datetime 传入 naive 列
    if ts_start.tzinfo is not None:
        ts_start = ts_start.replace(tzinfo=None)
    if ts_end.tzinfo is not None:
        ts_end = ts_end.replace(tzinfo=None)

    # 查询回路
    loop_result = await db.execute(select(LoopLedger).where(LoopLedger.id == loop_id))
    loop = loop_result.scalar_one_or_none()
    if loop is None:
        logger.warning("回路 %s 不存在", loop_id)
        return None

    # 查询 Tag 关联
    m_result = await db.execute(select(LoopTagMapping).where(LoopTagMapping.loop_id == loop_id))
    mappings = {m.tag_role: m for m in m_result.scalars().all()}

    tag_ids = [str(m.tag_id) for m in mappings.values()]
    tags_map: dict[str, TagRegistry] = {}
    if tag_ids:
        t_result = await db.execute(select(TagRegistry).where(TagRegistry.id.in_(tag_ids)))
        for t in t_result.scalars().all():
            tags_map[str(t.id)] = t

    pv_tag_name = _get_tag_name(mappings, tags_map, "PV")

    if not pv_tag_name:
        logger.warning("回路 %s 缺少 PV Tag", loop.tag_name)
        return None

    # 从数据源拉取数据（宽表查询，一次返回多列）
    try:
        raw_series = await query_wide_fn(
            loop_id=loop_id,
            tag_roles=["pv", "sp", "op", "mode"],
            start=ts_start,
            end=ts_end,
            interval_s=1,
        )
        if not isinstance(raw_series, RawTimeSeries):
            logger.warning("宽表查询返回的不是 RawTimeSeries 对象，跳过回路 %s", loop.tag_name)
            return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("宽表查询失败（回路 %s 跳过）: %s", loop.tag_name, exc)
        return None

    # 数据不足判定
    if len(raw_series.timestamps) < MIN_DATA_POINTS:
        logger.info(
            "回路 %s 数据点不足 (%d < %d)",
            loop.tag_name,
            len(raw_series.timestamps),
            MIN_DATA_POINTS,
        )
        return None

    # 构建对齐的数据并剔除 PV 质量码为 Bad 的点
    pv_quality_codes = raw_series.quality_codes.get("pv_quality", [])
    pv_quality_data: list[dict[str, str]] = []
    aligned: list[dict[str, Any]] = []
    for i, ts in enumerate(raw_series.timestamps):
        status = (
            map_quality_code(pv_quality_codes[i])
            if i < len(pv_quality_codes)
            else QualityStatus.GOOD
        )
        quality_label = "UNCERTAIN" if status == QualityStatus.UNKNOWN else status.value.upper()
        pv_quality_data.append({"quality": quality_label})
        if status == QualityStatus.BAD:
            continue

        pv_list = raw_series.signals.get("pv")
        pv_val = pv_list[i] if pv_list and i < len(pv_list) else None
        if pv_val is None:
            continue

        sp_list = raw_series.signals.get("sp")
        op_list = raw_series.signals.get("op")
        mode_list = raw_series.signals.get("mode")

        aligned.append(
            {
                "ts": ts,
                "pv": pv_val,
                "sp": sp_list[i] if sp_list and i < len(sp_list) else None,
                "op": op_list[i] if op_list and i < len(op_list) else None,
                "mode": mode_list[i] if mode_list and i < len(mode_list) else None,
            }
        )

    if len(aligned) < MIN_DATA_POINTS:
        logger.info("回路 %s 对齐后数据点不足", loop.tag_name)
        return None

    # 执行各算法
    pv_values = np.array([d["pv"] for d in aligned if d.get("pv") is not None], dtype=float)
    sp_values = np.array([d["sp"] for d in aligned if d.get("sp") is not None], dtype=float)
    op_values = np.array([d["op"] for d in aligned if d.get("op") is not None], dtype=float)

    # 提取 MODE 值数组（P0-3：饱和率分析仅统计自控模式）
    mode_values = np.array(
        [d.get("mode") for d in aligned if d.get("mode") is not None],
        dtype=object,
    )

    # 计算采样间隔（秒），用于 FFT 频率换算
    sample_interval = _compute_sample_interval(aligned)

    # 算法启停门控（FDS §5.4.1 指标启停）：diag_configs 仅含 is_enabled=True 的配置，
    # 禁用或配置不存在的算法不执行，以空结果占位（不产出标签、不参与证据融合）
    # B6 标签子集门控：labels 为 None 表示全量；否则仅执行子集内的标签
    def _in_labels(label: str) -> bool:
        return labels is None or label in labels

    osc_enabled = "OSCILLATION" in diag_configs and _in_labels("OSCILLATION")
    stiction_enabled = "VALVE_STICTION" in diag_configs and _in_labels("VALVE_STICTION")
    quality_enabled = "QUALITY_ABNORMAL" in diag_configs and _in_labels("QUALITY_ABNORMAL")
    saturation_enabled = "OUTPUT_SATURATION" in diag_configs and _in_labels("OUTPUT_SATURATION")
    overaggressive_enabled = "OVERAGGRESSIVE" in diag_configs and _in_labels("OVERAGGRESSIVE")
    overconservative_enabled = "OVERCONSERVATIVE" in diag_configs and _in_labels("OVERCONSERVATIVE")
    disturbance_enabled = "EXTERNAL_DISTURBANCE" in diag_configs and _in_labels(
        "EXTERNAL_DISTURBANCE"
    )

    # 1. FFT 频域分析（振荡检测）
    if osc_enabled:
        osc_result = _detect_oscillation_fft(pv_values, sample_interval)

        # 1b. IAE 零交叉相似率法振荡检测（FDS §5.4.6 在线主算法）
        osc_iae_result = _detect_oscillation_iae(
            pv_values,
            sp_values,
            sample_interval,
            threshold=_get_threshold(diag_configs, "OSCILLATION", None, None),
        )
    else:
        osc_result = _empty_osc_result()
        osc_iae_result = _empty_oscillation_iae_result()

    # 2. PV-OP 散点拟合（阀门粘滞检测）
    if stiction_enabled:
        stiction_result = _detect_valve_stiction(pv_values, op_values)
    else:
        stiction_result = _empty_stiction_result()

    # 3. PV 质量码统计（P2-1：Q001-Q005 规则矩阵）
    if quality_enabled:
        quality_result = _analyze_quality(
            pv_quality_data,
            threshold=_get_threshold(diag_configs, "QUALITY_ABNORMAL", None, None),
        )
    else:
        quality_result = _empty_quality_result()

    # 4. OP 饱和率分析（P0-3：仅自控模式 + 绝对工程限位）
    if saturation_enabled:
        saturation_result = _analyze_saturation(
            op_values,
            mode_values if len(mode_values) > 0 else None,
            threshold=_get_threshold(diag_configs, "OUTPUT_SATURATION", None, None),
        )
    else:
        saturation_result = _empty_saturation_result()

    # 5. Choudhury NGI/NLI 非线性检测（阀门粘滞高级检测，设计依据：FDS §5.4.6）
    if stiction_enabled:
        choudhury_result = _detect_choudhury_nonlinearity(pv_values, op_values)
    else:
        choudhury_result = _empty_choudhury_result()

    # 6. Kano 统计法粘滞检测（与 Choudhury 互为交叉验证）
    if stiction_enabled:
        kano_result = _detect_kano_stiction(pv_values, op_values)
    else:
        kano_result = _empty_kano_result()

    # 提取时间戳数组（供阶跃响应/响应迟缓/偏差突变算法使用）
    ts_values = np.array(
        [_ts_to_float(d.get("ts")) for d in aligned if d.get("ts") is not None],
        dtype=float,
    )
    # 若时间戳数量与 PV 不一致，回退为 None（使用等间隔假设）
    ts_param = ts_values if len(ts_values) == len(pv_values) else None

    # 7. 完整阶跃响应分析（过冲/衰减比/稳态误差）
    if overaggressive_enabled:
        step_response_result = _analyze_step_response(pv_values, sp_values, op_values, ts_param)
    else:
        step_response_result = _empty_step_response_result()

    # 8. 响应迟缓检测（一阶滞后拟合）
    # 控制类型从回路扩展属性获取（默认 PI）
    if overconservative_enabled:
        control_type = getattr(loop, "control_type", None) or "PI"
        slow_response_result = _detect_slow_response(pv_values, sp_values, control_type, ts_param)
    else:
        slow_response_result = _empty_slow_response_result()

    # 9. 偏差突变检测（CUSUM）
    if disturbance_enabled:
        bias_shift_result = _detect_bias_shift(pv_values, sp_values, ts_param)
    else:
        bias_shift_result = _empty_bias_shift_result()

    # 收集所有算法结果（带置信度）
    algorithm_results: list[dict[str, Any]] = []

    # 收集所有算法的可视化数据（无条件保存，供前端图表展示）
    all_visualization_data: dict[str, Any] = {}

    if osc_result["detected"]:
        algorithm_results.append(
            {
                "label": "OSCILLATION",
                "confidence": osc_result["confidence"],
                "feature_values": {
                    "oscillation_amplitude": osc_result["amplitude"],
                    "oscillation_frequency": osc_result["frequency"],
                    "oscillation_index": osc_result["index"],
                    "fft_frequencies": osc_result.get("frequencies", []),
                    "fft_amplitudes": osc_result.get("amplitudes", []),
                },
                "evidence": {
                    "reasoning": (
                        f"FFT 频域分析检测到主频 {osc_result['frequency']:.3f} Hz，"
                        f"振幅 {osc_result['amplitude']:.3f}，振荡指数 {osc_result['index']:.3f}"
                    ),
                    "algorithm": "FFT",
                },
            }
        )
    # 无条件保存 FFT 可视化数据
    all_visualization_data.update(
        {
            "fft_frequencies": osc_result.get("frequencies", []),
            "fft_amplitudes": osc_result.get("amplitudes", []),
            "oscillation_frequency": osc_result["frequency"],
            "oscillation_amplitude": osc_result["amplitude"],
            "oscillation_index": osc_result["index"],
        }
    )

    # IAE 零交叉相似率法振荡检测（与 FFT 互为交叉验证，标签同为 OSCILLATION）
    if osc_iae_result["detected"]:
        algorithm_results.append(
            {
                "label": "OSCILLATION",
                "confidence": osc_iae_result["confidence"],
                "feature_values": {
                    "iae_similarity": osc_iae_result["similarity"],
                    "iae_zero_crossing_count": osc_iae_result["zero_crossing_count"],
                    "iae_mean_period": osc_iae_result["mean_period"],
                },
                "evidence": {
                    "reasoning": (
                        f"IAE 零交叉相似率法检测到振荡：相似率 "
                        f"{osc_iae_result['similarity']:.3f}，"
                        f"零交叉数 {osc_iae_result['zero_crossing_count']}，"
                        f"平均周期 {osc_iae_result['mean_period']:.3f}s"
                    ),
                    "algorithm": "IAE_ZERO_CROSSING",
                },
            }
        )
    # 无条件保存 IAE 可视化数据
    all_visualization_data.update(
        {
            "iae_similarity": osc_iae_result["similarity"],
            "iae_zero_crossing_count": osc_iae_result["zero_crossing_count"],
            "iae_mean_period": osc_iae_result["mean_period"],
        }
    )

    if stiction_result["detected"]:
        algorithm_results.append(
            {
                "label": "VALVE_STICTION",
                "confidence": stiction_result["confidence"],
                "feature_values": {
                    "stiction_index": stiction_result["stiction_index"],
                    "fitting_score": stiction_result["fitting_score"],
                },
                "evidence": {
                    "reasoning": (
                        f"PV-OP 散点图呈现椭圆轨迹，拟合度 {stiction_result['fitting_score']:.3f}，"
                        f"粘滞指数 {stiction_result['stiction_index']:.3f}"
                    ),
                    "scatter_plot": _build_scatter_plot_data(aligned),
                    "algorithm": "PV_OP_SCATTER",
                },
            }
        )
    # 无条件保存散点图可视化数据
    scatter_data = _build_scatter_plot_data(aligned)
    all_visualization_data.update(
        {
            "stiction_index": stiction_result["stiction_index"],
            "fitting_score": stiction_result["fitting_score"],
            "scatter_plot_x": scatter_data.get("x", []),
            "scatter_plot_y": scatter_data.get("y", []),
        }
    )

    if quality_result["abnormal"]:
        algorithm_results.append(
            {
                "label": "QUALITY_ABNORMAL",
                "confidence": quality_result["confidence"],
                "feature_values": {
                    "bad_quality_rate": quality_result["bad_rate"],
                    "total_points": quality_result["total"],
                    "bad_points": quality_result["bad_count"],
                    "quality_pattern": quality_result.get("quality_pattern", "NORMAL"),
                },
                "evidence": {
                    "reasoning": (
                        f"PV 质量码统计：Bad 占比 {quality_result['bad_rate']:.3f}，"
                        f"总点数 {quality_result['total']}，"
                        f"Bad 点数 {quality_result['bad_count']}，"
                        f"质量模式 {quality_result.get('quality_pattern', 'NORMAL')}"
                    ),
                },
            }
        )
    # 无条件保存质量码可视化数据
    all_visualization_data.update(
        {
            "bad_quality_rate": quality_result["bad_rate"],
            "total_points": quality_result["total"],
            "bad_points": quality_result["bad_count"],
            "quality_pattern": quality_result.get("quality_pattern", "NORMAL"),
        }
    )

    if saturation_result["detected"]:
        algorithm_results.append(
            {
                "label": "OUTPUT_SATURATION",
                "confidence": saturation_result["confidence"],
                "feature_values": {
                    "saturation_rate": saturation_result["saturation_rate"],
                    "high_saturation_count": saturation_result["high_count"],
                    "low_saturation_count": saturation_result["low_count"],
                },
                "evidence": {
                    "reasoning": (
                        f"OP 饱和率分析：饱和率 {saturation_result['saturation_rate']:.3f}，"
                        f"高饱和 {saturation_result['high_count']} 点，"
                        f"低饱和 {saturation_result['low_count']} 点"
                    ),
                },
            }
        )
    # 无条件保存饱和率可视化数据
    all_visualization_data.update(
        {
            "saturation_rate": saturation_result["saturation_rate"],
            "high_saturation_count": saturation_result["high_count"],
            "low_saturation_count": saturation_result["low_count"],
        }
    )

    # Choudhury NGI/NLI 非线性检测 → VALVE_STICTION（交叉验证）
    if choudhury_result["detected"]:
        algorithm_results.append(
            {
                "label": "VALVE_STICTION",
                "confidence": choudhury_result["confidence"],
                "feature_values": {
                    "ngi": choudhury_result["ngi"],
                    "nli": choudhury_result["nli"],
                    "choudhury_stiction_index": choudhury_result["stiction_index"],
                    "fitting_score": choudhury_result["fitting_score"],
                },
                "evidence": {
                    "reasoning": (
                        f"Choudhury 非线性检测：NGI={choudhury_result['ngi']:.4f}，"
                        f"NLI={choudhury_result['nli']:.4f}，"
                        f"椭圆拟合度={choudhury_result['fitting_score']:.3f}"
                    ),
                    "algorithm": "CHOUDHURY_NGI_NLI",
                },
            }
        )
    # 无条件保存 Choudhury 可视化数据
    all_visualization_data.update(
        {
            "ngi": choudhury_result["ngi"],
            "nli": choudhury_result["nli"],
            "choudhury_stiction_index": choudhury_result["stiction_index"],
            "fitting_score": choudhury_result["fitting_score"],
        }
    )

    # Kano 统计法粘滞检测 → VALVE_STICTION（交叉验证）
    if kano_result["detected"]:
        algorithm_results.append(
            {
                "label": "VALVE_STICTION",
                "confidence": kano_result["confidence"],
                "feature_values": {
                    "kano_stiction_ratio": kano_result["stiction_ratio"],
                    "pv_op_correlation": kano_result["correlation"],
                    "std_ratio": kano_result["std_ratio"],
                },
                "evidence": {
                    "reasoning": (
                        f"Kano 统计法：粘滞区间占比={kano_result['stiction_ratio']:.3f}，"
                        f"PV-OP 相关系数={kano_result['correlation']:.3f}，"
                        f"标准差比值={kano_result['std_ratio']:.3f}"
                    ),
                    "algorithm": "KANO_STATISTICAL",
                },
            }
        )
    # 无条件保存 Kano 可视化数据
    all_visualization_data.update(
        {
            "kano_stiction_ratio": kano_result["stiction_ratio"],
            "pv_op_correlation": kano_result["correlation"],
            "std_ratio": kano_result["std_ratio"],
        }
    )

    # 完整阶跃响应分析 → OVERAGGRESSIVE
    if step_response_result["detected"]:
        algorithm_results.append(
            {
                "label": "OVERAGGRESSIVE",
                "confidence": step_response_result["confidence"],
                "feature_values": {
                    "overshoot": step_response_result["overshoot"],
                    "decay_ratio": step_response_result["decay_ratio"],
                    "steady_state_error": step_response_result["steady_state_error"],
                    "step_count": step_response_result["step_count"],
                    "step_timestamps": step_response_result.get("timestamps", []),
                    "step_pv_response": step_response_result.get("pv_response", []),
                    "step_sp_values": step_response_result.get("sp_values", []),
                    "step_indices": step_response_result.get("step_indices", []),
                },
                "evidence": {
                    "reasoning": (
                        f"阶跃响应分析：过冲={step_response_result['overshoot']:.3f}，"
                        f"衰减比={step_response_result['decay_ratio']:.3f}，"
                        f"稳态误差={step_response_result['steady_state_error']:.3f}，"
                        f"阶跃次数={step_response_result['step_count']}"
                    ),
                    "algorithm": "STEP_RESPONSE",
                },
            }
        )
    # 无条件保存阶跃响应可视化数据
    all_visualization_data.update(
        {
            "overshoot": step_response_result["overshoot"],
            "decay_ratio": step_response_result["decay_ratio"],
            "steady_state_error": step_response_result["steady_state_error"],
            "step_count": step_response_result["step_count"],
            "step_timestamps": step_response_result.get("timestamps", []),
            "step_pv_response": step_response_result.get("pv_response", []),
            "step_sp_values": step_response_result.get("sp_values", []),
            "step_indices": step_response_result.get("step_indices", []),
        }
    )

    # 响应迟缓检测 → OVERCONSERVATIVE
    if slow_response_result["detected"]:
        algorithm_results.append(
            {
                "label": "OVERCONSERVATIVE",
                "confidence": slow_response_result["confidence"],
                "feature_values": {
                    "time_constant": slow_response_result["time_constant"],
                    "expected_time_constant": slow_response_result["expected_time_constant"],
                    "ratio": slow_response_result["ratio"],
                },
                "evidence": {
                    "reasoning": (
                        f"响应迟缓检测：时间常数={slow_response_result['time_constant']:.3f}，"
                        f"期望值={slow_response_result['expected_time_constant']:.3f}，"
                        f"比值={slow_response_result['ratio']:.2f}"
                    ),
                    "algorithm": "SLOW_RESPONSE",
                },
            }
        )
    # 无条件保存响应迟缓可视化数据
    all_visualization_data.update(
        {
            "time_constant": slow_response_result["time_constant"],
            "expected_time_constant": slow_response_result["expected_time_constant"],
            "ratio": slow_response_result["ratio"],
        }
    )

    # 偏差突变检测 → EXTERNAL_DISTURBANCE
    if bias_shift_result["detected"]:
        algorithm_results.append(
            {
                "label": "EXTERNAL_DISTURBANCE",
                "confidence": bias_shift_result["confidence"],
                "feature_values": {
                    "shift_count": bias_shift_result["shift_count"],
                    "max_cusum": bias_shift_result["max_cusum"],
                    "shift_magnitude": bias_shift_result["shift_magnitude"],
                    "cusum_timestamps": bias_shift_result.get("timestamps", []),
                    "cusum_pos": bias_shift_result.get("cusum_pos", []),
                    "cusum_neg": bias_shift_result.get("cusum_neg", []),
                    "cusum_shift_points": bias_shift_result.get("shift_points", []),
                    "cusum_threshold": bias_shift_result.get("threshold", 0.0),
                },
                "evidence": {
                    "reasoning": (
                        f"偏差突变检测：突变次数={bias_shift_result['shift_count']}，"
                        f"最大 CUSUM={bias_shift_result['max_cusum']:.3f}，"
                        f"突变幅度={bias_shift_result['shift_magnitude']:.3f}"
                    ),
                    "algorithm": "BIAS_SHIFT_CUSUM",
                },
            }
        )
    # 无条件保存 CUSUM 可视化数据
    all_visualization_data.update(
        {
            "shift_count": bias_shift_result["shift_count"],
            "max_cusum": bias_shift_result["max_cusum"],
            "shift_magnitude": bias_shift_result["shift_magnitude"],
            "cusum_timestamps": bias_shift_result.get("timestamps", []),
            "cusum_pos": bias_shift_result.get("cusum_pos", []),
            "cusum_neg": bias_shift_result.get("cusum_neg", []),
            "cusum_shift_points": bias_shift_result.get("shift_points", []),
            "cusum_threshold": bias_shift_result.get("threshold", 0.0),
        }
    )

    # 兜底标签：无任何算法命中
    if not algorithm_results:
        algorithm_results.append(
            {
                "label": "MANUAL_REVIEW",
                "confidence": 0.5,
                "feature_values": {},
                "evidence": {
                    "reasoning": "所有算法均未检测到明显异常，建议人工复核",
                },
            }
        )

    # 应用专家规则矩阵 R01-R06（FDS §5.4.6）
    algorithm_results = _apply_expert_rules(algorithm_results)

    # 标签去重（P1-4：同一标签保留置信度最高的记录）
    algorithm_results = _deduplicate_labels(algorithm_results)

    # 使用 Dempster-Shafer 证据理论融合置信度
    fused_confidence = _dempster_shafer_fusion(
        [(r["label"], r["confidence"]) for r in algorithm_results]
    )

    # 幂等性（S1-C3）：删除同一任务的旧诊断记录，避免重复写入
    # 注意：按 task_id 隔离，避免不同任务互相删除诊断结果
    if task_id:
        await db.execute(
            delete(DiagnosisResult).where(
                DiagnosisResult.task_id == task_id,
            )
        )
    else:
        # 无 task_id 时（旧模式兼容）：按回路 + 时间窗删除
        await db.execute(
            delete(DiagnosisResult).where(
                DiagnosisResult.loop_id == loop_id,
                DiagnosisResult.diagnosed_at >= ts_start,
                DiagnosisResult.diagnosed_at <= ts_end + timedelta(hours=1),
            )
        )

    # A11：查询该回路现有 ACTIVE 诊断标签，供落库时 upsert（更新而非重复建行）
    active_tag_rows = (
        (
            await db.execute(
                select(DiagnosisTag).where(
                    DiagnosisTag.loop_id == loop_id,
                    DiagnosisTag.status == "ACTIVE",
                )
            )
        )
        .scalars()
        .all()
    )
    active_tag_map: dict[str, Any] = {t.tag_code: t for t in active_tag_rows}

    # 写入诊断结果（每个标签一条记录）
    diagnosed_at = datetime.now(UTC).replace(tzinfo=None)
    for result in algorithm_results:
        confidence_decimal = Decimal(str(round(result["confidence"] * 100, 2)))
        evidence_chain = {
            **result["evidence"],
            "fused_confidence": fused_confidence,
        }
        # 合并所有算法的可视化数据（无条件保存所有可视化数组）
        feature_values = {
            **all_visualization_data,
            **result.get("feature_values", {}),
        }
        diag_record = DiagnosisResult(
            id=str(uuid4()),
            loop_id=loop_id,
            diag_label=result["label"],
            confidence=confidence_decimal,
            feature_values=feature_values,
            evidence_chain=evidence_chain,
            algorithm_version=DIAG_ALGORITHM_VERSION,
            diagnosed_at=diagnosed_at,
            task_id=task_id,
        )
        db.add(diag_record)
        # A11：同步 upsert diagnosis_tag（D-S 融合后标签逐条处理）
        _upsert_diagnosis_tag(db, active_tag_map, loop_id, result, diagnosed_at)

    return {
        "loopId": loop_id,
        "tagName": loop.tag_name,
        "diagnosedAt": diagnosed_at.isoformat(),
        "labels": [r["label"] for r in algorithm_results],
        "fusedConfidence": fused_confidence,
        "algorithmVersion": DIAG_ALGORITHM_VERSION,
        "status": "SUCCESS",
    }


# ---------------------------------------------------------------------------
# 算法实现
# ---------------------------------------------------------------------------


def _empty_osc_result() -> dict[str, Any]:
    """空振荡检测结果。"""
    return {
        "detected": False,
        "confidence": 0.0,
        "amplitude": 0.0,
        "frequency": 0.0,
        "index": 0.0,
        "frequencies": [],
        "amplitudes": [],
    }


def _empty_stiction_result() -> dict[str, Any]:
    """空粘滞检测结果。"""
    return {
        "detected": False,
        "confidence": 0.0,
        "stiction_index": 0.0,
        "fitting_score": 0.0,
    }


def _empty_oscillation_iae_result() -> dict[str, Any]:
    """空 IAE 振荡检测结果（OSCILLATION 算法禁用时占位）。"""
    return {
        "detected": False,
        "confidence": 0.0,
        "similarity": 0.0,
        "zero_crossing_count": 0,
        "mean_period": 0.0,
    }


def _empty_quality_result() -> dict[str, Any]:
    """空质量码分析结果（QUALITY_ABNORMAL 算法禁用时占位）。"""
    return {
        "abnormal": False,
        "confidence": 0.0,
        "bad_rate": 0.0,
        "total": 0,
        "bad_count": 0,
        "quality_pattern": "NORMAL",
    }


def _empty_saturation_result() -> dict[str, Any]:
    """空饱和率分析结果（OUTPUT_SATURATION 算法禁用时占位）。"""
    return {
        "detected": False,
        "confidence": 0.0,
        "saturation_rate": 0.0,
        "high_count": 0,
        "low_count": 0,
    }


def _detect_oscillation_fft(pv_values: np.ndarray, sample_interval: float = 1.0) -> dict[str, Any]:
    """FFT 频域分析检测振荡。

    Args:
        pv_values: PV 数据数组
        sample_interval: 采样间隔（秒），用于频率换算

    Returns:
        {detected, confidence, amplitude, frequency, index}
    """
    if len(pv_values) < 8:
        return _empty_osc_result()

    try:
        N = len(pv_values)
        fs = 1.0 / sample_interval if sample_interval > 0 else 1.0  # 采样频率 (Hz)
        # 去均值
        pv_centered = pv_values - np.mean(pv_values)
        # FFT
        fft_vals = np.fft.rfft(pv_centered)
        fft_magnitude = np.abs(fft_vals)
        # 主频
        if len(fft_magnitude) <= 1:
            return _empty_osc_result()
        peak_idx = int(np.argmax(fft_magnitude[1:])) + 1
        amplitude = float(fft_magnitude[peak_idx] / N)
        # 频率 = peak_idx * fs / N（标准 FFT 频率换算公式）
        frequency = float(peak_idx * fs / N)

        # 振荡指数：主频能量占比
        total_energy = float(np.sum(fft_magnitude[1:] ** 2))
        if total_energy <= 0:
            return _empty_osc_result()
        peak_energy = float(fft_magnitude[peak_idx] ** 2)
        osc_index = peak_energy / total_energy

        # IAE 零交叉检测
        zero_crossings = int(np.sum(np.diff(np.sign(pv_centered)) != 0))

        # 振荡判定：振荡指数 > 0.3 且零交叉次数 > 5
        detected = osc_index > 0.3 and zero_crossings > 5
        # 置信度：基于振荡指数
        confidence = min(1.0, osc_index * 1.5) if detected else 0.0

        frequencies = np.arange(len(fft_magnitude)) * fs / N
        amplitudes = fft_magnitude / N

        max_points = 500
        if len(frequencies) > max_points:
            step = len(frequencies) // max_points
            frequencies = frequencies[::step]
            amplitudes = amplitudes[::step]

        return {
            "detected": detected,
            "confidence": confidence,
            "amplitude": amplitude,
            "frequency": frequency,
            "index": osc_index,
            "frequencies": frequencies.tolist(),
            "amplitudes": amplitudes.tolist(),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("FFT 振荡检测失败: %s", exc)
        return _empty_osc_result()


def _detect_valve_stiction(pv_values: np.ndarray, op_values: np.ndarray) -> dict[str, Any]:
    """PV-OP 散点拟合检测阀门粘滞。

    使用椭圆拟合：若 PV-OP 散点呈现椭圆轨迹，则存在粘滞。

    Returns:
        {detected, confidence, stiction_index, fitting_score}
    """
    min_len = min(len(pv_values), len(op_values))
    if min_len < 8:
        return _empty_stiction_result()

    try:
        pv = pv_values[:min_len]
        op = op_values[:min_len]

        # 标准化
        pv_norm = (pv - np.mean(pv)) / (np.std(pv) + 1e-9)
        op_norm = (op - np.mean(op)) / (np.std(op) + 1e-9)

        # 椭圆拟合：使用 SVD 分解
        points = np.column_stack([pv_norm, op_norm])
        # 计算协方差矩阵
        cov = np.cov(points.T)
        eigenvalues, eigenvectors = np.linalg.eigh(cov)

        # 椭圆性指标：长短轴比
        if eigenvalues.min() <= 0:
            return _empty_stiction_result()
        # 拟合度：基于点到主轴的距离
        principal_axis = eigenvectors[:, -1]
        projected = points @ principal_axis
        residuals = points - np.outer(projected, principal_axis)
        residual_variance = float(np.mean(np.sum(residuals**2, axis=1)))
        total_variance = float(np.mean(np.sum(points**2, axis=1)))
        fitting_score = 1.0 - (residual_variance / (total_variance + 1e-9))
        fitting_score = max(0.0, min(1.0, fitting_score))

        # 粘滞指数：基于 OP 不动时 PV 仍在变化的比例
        op_diff = np.abs(np.diff(op))
        pv_diff = np.abs(np.diff(pv))
        op_static = op_diff < (np.std(op_diff) + 1e-9) * 0.1
        pv_moving = pv_diff > (np.std(pv_diff) + 1e-9) * 0.5
        stiction_index = float(np.sum(op_static & pv_moving) / max(len(op_diff), 1))

        # 粘滞判定：拟合度 > 0.7 且粘滞指数 > 0.3
        detected = fitting_score > 0.7 and stiction_index > 0.3
        confidence = min(1.0, (fitting_score + stiction_index) / 2) if detected else 0.0

        return {
            "detected": detected,
            "confidence": confidence,
            "stiction_index": stiction_index,
            "fitting_score": fitting_score,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("阀门粘滞检测失败: %s", exc)
        return _empty_stiction_result()


def _analyze_quality(pv_data: list[dict], threshold: dict | None = None) -> dict[str, Any]:
    """PV 质量码统计与质量模式识别（FDS §5.4.6 Q001-Q005 规则矩阵）。

    规则矩阵：
    - Q001（连续 Bad）：连续 Bad 点数 > 10 → 传感器故障（置信度 0.9）
    - Q002（间歇 Bad）：Bad 点占比 > 10% 且不满足 Q001 → 通信问题（置信度 0.6）
    - Q003（Uncertain 质量码）：Uncertain 点占比 > 20% → 校准漂移（置信度 0.6）
    - Q004（质量突变）：从 Good 突变为 Bad 且持续时间 > 5 点 → 突发故障（置信度 0.8）
    - Q005（质量恢复）：Bad 段后恢复 Good，且 Bad 段持续 3-10 点 → 瞬态干扰（置信度 0.4）
    - NORMAL：无异常

    满足任一规则即判定为异常。

    Args:
        pv_data: PV 数据列表，每个元素含 "quality" 字段
        threshold: 阈值配置（可选），支持键：
            - q001_consecutive_bad: Q001 连续 Bad 阈值（默认 10）
            - q002_bad_rate: Q002 Bad 占比阈值（默认 0.1）
            - q003_uncertain_rate: Q003 Uncertain 占比阈值（默认 0.2）
            - q004_bad_duration: Q004 Bad 持续点数阈值（默认 5）
            - q005_min_bad: Q005 Bad 段最小点数（默认 3）
            - q005_max_bad: Q005 Bad 段最大点数（默认 10）

    Returns:
        {abnormal, confidence, bad_rate, total, bad_count, quality_pattern}
    """
    if threshold is None:
        threshold = {}
    q001_consecutive_bad = int(threshold.get("q001_consecutive_bad", 10))
    q002_bad_rate = float(threshold.get("q002_bad_rate", 0.1))
    q003_uncertain_rate = float(threshold.get("q003_uncertain_rate", 0.2))
    q004_bad_duration = int(threshold.get("q004_bad_duration", 5))
    q005_min_bad = int(threshold.get("q005_min_bad", 3))
    q005_max_bad = int(threshold.get("q005_max_bad", 10))

    total = len(pv_data)
    if total == 0:
        return {
            "abnormal": False,
            "confidence": 0.0,
            "bad_rate": 0.0,
            "total": 0,
            "bad_count": 0,
            "quality_pattern": "NORMAL",
        }

    try:
        # 统计 Bad / Uncertain 数量
        bad_count = 0
        uncertain_count = 0
        quality_seq: list[str] = []
        for d in pv_data:
            q = str(d.get("quality", "GOOD")).upper()
            quality_seq.append(q)
            if q == "BAD":
                bad_count += 1
            elif q == "UNCERTAIN":
                uncertain_count += 1

        bad_rate = bad_count / total
        uncertain_rate = uncertain_count / total

        # 计算 Bad 连续段（用于 Q001/Q004/Q005）
        bad_segments: list[int] = []  # 每段长度
        current_bad_run = 0
        max_consecutive_bad = 0
        for q in quality_seq:
            if q == "BAD":
                current_bad_run += 1
            else:
                if current_bad_run > 0:
                    bad_segments.append(current_bad_run)
                    max_consecutive_bad = max(max_consecutive_bad, current_bad_run)
                current_bad_run = 0
        if current_bad_run > 0:
            bad_segments.append(current_bad_run)
            max_consecutive_bad = max(max_consecutive_bad, current_bad_run)

        # Q001: 连续 Bad 点数 > 阈值 → 传感器故障
        q001_hit = max_consecutive_bad > q001_consecutive_bad

        # Q004: 从 Good 突变为 Bad 且持续 > 阈值 → 突发故障
        # （即存在 Bad 段长度 > q004_bad_duration，且该段之前是 Good）
        q004_hit = any(seg > q004_bad_duration for seg in bad_segments) and not q001_hit

        # Q005: Bad 段后恢复 Good，且 Bad 段持续在 [min, max] 范围 → 瞬态干扰
        q005_hit = any(q005_min_bad <= seg <= q005_max_bad for seg in bad_segments)

        # Q002: Bad 占比 > 阈值 且不满足 Q001 → 通信问题
        q002_hit = bad_rate > q002_bad_rate and not q001_hit

        # Q003: Uncertain 占比 > 阈值 → 校准漂移
        q003_hit = uncertain_rate > q003_uncertain_rate

        # 按优先级选择质量模式（Q001 > Q004 > Q002 > Q003 > Q005 > NORMAL）
        if q001_hit:
            quality_pattern = "Q001"
            confidence = 0.9
            abnormal = True
        elif q004_hit:
            quality_pattern = "Q004"
            confidence = 0.8
            abnormal = True
        elif q002_hit:
            quality_pattern = "Q002"
            confidence = 0.6
            abnormal = True
        elif q003_hit:
            quality_pattern = "Q003"
            confidence = 0.6
            abnormal = True
        elif q005_hit:
            quality_pattern = "Q005"
            confidence = 0.4
            abnormal = True
        else:
            quality_pattern = "NORMAL"
            confidence = 0.0
            abnormal = False

        return {
            "abnormal": abnormal,
            "confidence": confidence,
            "bad_rate": bad_rate,
            "total": total,
            "bad_count": bad_count,
            "quality_pattern": quality_pattern,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("质量码分析失败: %s", exc)
        return {
            "abnormal": False,
            "confidence": 0.0,
            "bad_rate": 0.0,
            "total": total,
            "bad_count": 0,
            "quality_pattern": "NORMAL",
        }


def _analyze_saturation(
    op_values: np.ndarray,
    mode_values: np.ndarray | None = None,
    threshold: dict | None = None,
) -> dict[str, Any]:
    """OP 饱和率分析（FDS §5.4.6 — 仅自控模式 + 绝对工程限位）。

    修复要点：
    1. 若提供 mode_values，仅保留 MODE 为 Auto/CAS/RCAS 的数据点
       （大小写不敏感，包含 "AUTO" 或 "CAS"）
    2. 使用绝对工程限位（默认 0-100%），不再做 min-max 相对归一化
    3. saturation_epsilon 默认 2%（≥98% 或 ≤2% 为饱和），与 FDS §5.4.6 一致
    4. 饱和率 > 20% 判定为 detected

    Args:
        op_values: OP 数据数组
        mode_values: MODE 数据数组（可选），若提供则仅统计自控模式点
        threshold: 阈值配置，支持键：
            - op_high_limit: 工程高限位（默认 100.0）
            - op_low_limit: 工程低限位（默认 0.0）
            - saturation_epsilon: 饱和容差（默认 2.0，即 ≥98 或 ≤2 为饱和）

    Returns:
        {detected, confidence, saturation_rate, high_count, low_count}
    """
    # 解析阈值配置
    if threshold is None:
        threshold = {}
    op_high_limit = float(threshold.get("op_high_limit", 100.0))
    op_low_limit = float(threshold.get("op_low_limit", 0.0))
    saturation_epsilon = float(threshold.get("saturation_epsilon", 2.0))

    # 若提供 mode_values，仅保留自控模式数据点
    if mode_values is not None and len(mode_values) > 0:
        min_len = min(len(op_values), len(mode_values))
        op_filtered = []
        for i in range(min_len):
            mode_val = mode_values[i]
            # mode_val 可能是数值或字符串
            try:
                mode_str = str(mode_val).upper()
            except Exception:
                continue
            # 仅保留 Auto/CAS/RCAS（包含 "AUTO" 或 "CAS"）
            if "AUTO" in mode_str or "CAS" in mode_str:
                op_filtered.append(float(op_values[i]))
        op_arr = np.array(op_filtered, dtype=float) if op_filtered else np.array([], dtype=float)
    else:
        op_arr = np.asarray(op_values, dtype=float)

    total = len(op_arr)
    if total == 0:
        return {
            "detected": False,
            "confidence": 0.0,
            "saturation_rate": 0.0,
            "high_count": 0,
            "low_count": 0,
        }

    try:
        # 计算饱和阈值（基于绝对工程限位）
        high_threshold = op_high_limit - saturation_epsilon
        low_threshold = op_low_limit + saturation_epsilon

        high_count = int(np.sum(op_arr >= high_threshold))
        low_count = int(np.sum(op_arr <= low_threshold))
        saturation_rate = (high_count + low_count) / total

        # 饱和判定：饱和率 > 20%
        detected = saturation_rate > 0.2
        confidence = min(1.0, saturation_rate * 3) if detected else 0.0

        return {
            "detected": detected,
            "confidence": confidence,
            "saturation_rate": saturation_rate,
            "high_count": high_count,
            "low_count": low_count,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("OP 饱和率分析失败: %s", exc)
        return {
            "detected": False,
            "confidence": 0.0,
            "saturation_rate": 0.0,
            "high_count": 0,
            "low_count": 0,
        }


# ---------------------------------------------------------------------------
# 扩展诊断算法（设计依据：FDS §5.4.6 / ADS §5.2-5.5）
# ---------------------------------------------------------------------------


def _empty_choudhury_result() -> dict[str, Any]:
    """空 Choudhury 非线性检测结果。"""
    return {
        "detected": False,
        "confidence": 0.0,
        "ngi": 0.0,
        "nli": 0.0,
        "stiction_index": 0.0,
        "fitting_score": 0.0,
    }


def _empty_kano_result() -> dict[str, Any]:
    """空 Kano 粘滞检测结果。"""
    return {
        "detected": False,
        "confidence": 0.0,
        "stiction_ratio": 0.0,
        "correlation": 0.0,
        "std_ratio": 0.0,
    }


def _empty_step_response_result() -> dict[str, Any]:
    """空阶跃响应分析结果。"""
    return {
        "detected": False,
        "confidence": 0.0,
        "overshoot": 0.0,
        "decay_ratio": 0.0,
        "steady_state_error": 0.0,
        "step_count": 0,
        "timestamps": [],
        "pv_response": [],
        "sp_values": [],
        "step_indices": [],
    }


def _empty_slow_response_result() -> dict[str, Any]:
    """空响应迟缓检测结果。"""
    return {
        "detected": False,
        "confidence": 0.0,
        "time_constant": 0.0,
        "expected_time_constant": 0.0,
        "ratio": 0.0,
    }


def _empty_bias_shift_result() -> dict[str, Any]:
    """空偏差突变检测结果。"""
    return {
        "detected": False,
        "confidence": 0.0,
        "shift_count": 0,
        "max_cusum": 0.0,
        "shift_magnitude": 0.0,
        "timestamps": [],
        "cusum_pos": [],
        "cusum_neg": [],
        "shift_points": [],
        "threshold": 0.0,
    }


def _compute_max_bicoherence(signal: np.ndarray, n_seg: int = 4, n_freq: int = 16) -> float:
    """计算信号的最大双相干性（NLI 近似）。

    双相干性衡量信号的二次相位耦合（QPC），是非线性检测的标准指标。
    通过分段 FFT 平均近似计算双谱方差比。

    Args:
        signal: 去均值后的信号
        n_seg: 分段数（影响统计稳定性）
        n_freq: 计算的频率对数量

    Returns:
        最大双相干性值（0~1）
    """
    N = len(signal)
    seg_len = N // n_seg
    if seg_len < 8:
        return 0.0

    try:
        # 构建分段矩阵 (n_seg, seg_len) 并计算 FFT
        segments = np.empty((n_seg, seg_len), dtype=float)
        for i in range(n_seg):
            seg = signal[i * seg_len : (i + 1) * seg_len]
            segments[i] = seg - np.mean(seg)

        X = np.fft.rfft(segments, axis=1)  # shape: (n_seg, n_freq_total)
        n = X.shape[1]
        if n < 4:
            return 0.0

        max_f = min(n_freq, n // 2)

        # 构建频率对网格（向量化）
        f1_arr = np.arange(1, max_f)
        f2_arr = np.arange(1, max_f)
        f1_grid, f2_grid = np.meshgrid(f1_arr, f2_arr, indexing="ij")
        mask = (f2_grid >= f1_grid) & ((f1_grid + f2_grid) < n)
        f1_valid = f1_grid[mask]
        f2_valid = f2_grid[mask]

        if len(f1_valid) == 0:
            return 0.0

        # 提取各频率分量（向量化）
        X_f1 = X[:, f1_valid]  # (n_seg, n_pairs)
        X_f2 = X[:, f2_valid]
        X_f12 = X[:, f1_valid + f2_valid]

        # 双谱（分段平均）
        bis = np.mean(X_f1 * X_f2 * np.conj(X_f12), axis=0)

        # 归一化分母
        psd_f1 = np.mean(np.abs(X_f1) ** 2, axis=0)
        psd_f2 = np.mean(np.abs(X_f2) ** 2, axis=0)
        psd_f12 = np.mean(np.abs(X_f12) ** 2, axis=0)
        denom = np.sqrt(psd_f1 * psd_f2 * psd_f12) + 1e-12

        bic = (np.abs(bis) / denom) ** 2
        return float(min(1.0, np.max(bic)))
    except Exception as exc:  # noqa: BLE001
        logger.debug("双相干性计算失败: %s", exc)
        return 0.0


def _detect_choudhury_nonlinearity(pv: np.ndarray, op: np.ndarray) -> dict[str, Any]:
    """Choudhury NGI/NLI 非线性检测（阀门粘滞高级检测）。

    设计依据：FDS §5.4.6 / ADS §5.2.2

    基于 OP 信号的非高斯性（NGI）和非线性（NLI）指标检测阀门粘滞：
    - NGI = |Kurtosis(x) - 3| / 6 + Skewness(x)² / 24
    - NLI 通过最大双相干性近似（二次相位耦合指标）
    - 当 NGI > 0.001 且 NLI > 0.01 时判定存在非线性（粘滞）

    Args:
        pv: PV 数据数组
        op: OP 数据数组

    Returns:
        {detected, confidence, ngi, nli, stiction_index, fitting_score}
    """
    min_len = min(len(pv), len(op))
    if min_len < 32:
        return _empty_choudhury_result()

    try:
        from scipy import stats as sp_stats

        op_arr = op[:min_len].astype(float)
        pv_arr = pv[:min_len].astype(float)

        # 去均值
        op_centered = op_arr - np.mean(op_arr)
        op_std = float(np.std(op_centered))
        if op_std < 1e-9:
            return _empty_choudhury_result()

        # 4 阶矩统计量（Fisher 定义，正态分布 excess kurtosis=0）
        skewness = float(sp_stats.skew(op_centered))
        kurtosis_excess = float(sp_stats.kurtosis(op_centered, fisher=True))

        # NGI: 非高斯指数（ADS §5.2.2 公式）
        ngi = abs(kurtosis_excess) / 6.0 + (skewness**2) / 24.0

        # NLI: 非线性指数（最大双相干性近似）
        nli = _compute_max_bicoherence(op_centered)

        # PV-OP 椭圆拟合（复用现有 _detect_valve_stiction 拟合度）
        stiction_fit = _detect_valve_stiction(pv_arr, op_arr)
        fitting_score = float(stiction_fit.get("fitting_score", 0.0))
        stiction_index = float(stiction_fit.get("stiction_index", 0.0))

        # 判定规则（ADS §5.2.2: NGI > 0.001 且 NLI > 0.01）
        detected = bool(ngi > 0.001 and nli > 0.01)

        # 置信度：融合 NGI、NLI 和椭圆拟合度（权重和为 1）
        if detected:
            confidence = min(1.0, ngi * 0.5 + nli * 0.3 + fitting_score * 0.2)
        else:
            confidence = 0.0

        return {
            "detected": detected,
            "confidence": confidence,
            "ngi": ngi,
            "nli": nli,
            "stiction_index": stiction_index,
            "fitting_score": fitting_score,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Choudhury 非线性检测失败: %s", exc)
        return _empty_choudhury_result()


def _detect_kano_stiction(
    pv: np.ndarray, op: np.ndarray, mv: np.ndarray | None = None
) -> dict[str, Any]:
    """Kano 统计法阀门粘滞检测。

    设计依据：FDS §5.4.6 / ADS §5.2.3

    基于 OP 与 PV 的统计特性，计算粘滞区间特征：
    - 将 OP 序列分段（单调变化区间）
    - 计算每段 OP 变化范围 ΔOP_i 和 PV 变化范围 ΔPV_i
    - 粘滞区间 = OP 几乎不变但 PV 大幅变化的段
    - ρ = 粘滞区间长度 / 总区间长度
    - ρ > 0.6 → 高概率粘滞

    与 Choudhury 方法互为交叉验证。

    Args:
        pv: PV 数据数组
        op: OP 数据数组
        mv: 操纵变量（可选，默认与 OP 一致）

    Returns:
        {detected, confidence, stiction_ratio, correlation, std_ratio}
    """
    min_len = min(len(pv), len(op))
    if min_len < 16:
        return _empty_kano_result()

    try:
        pv_arr = pv[:min_len].astype(float)
        op_arr = op[:min_len].astype(float)
        mv[:min_len].astype(float) if mv is not None else op_arr

        # PV 和 OP 的标准差比值
        pv_std = float(np.std(pv_arr))
        op_std = float(np.std(op_arr))
        std_ratio = pv_std / (op_std + 1e-9)

        # PV-OP 相关系数
        if pv_std > 1e-9 and op_std > 1e-9:
            correlation = float(np.corrcoef(pv_arr, op_arr)[0, 1])
        else:
            correlation = 0.0

        # OP 单调分段：检测方向变化点
        op_diff = np.diff(op_arr)
        # 方向符号（+1/-1/0）
        signs = np.sign(op_diff)
        # 方向变化点（忽略 0）
        nonzero_signs = signs[signs != 0]
        if len(nonzero_signs) < 2:
            return _empty_kano_result()

        # 找到方向变化的索引
        sign_changes = np.where(np.diff(nonzero_signs) != 0)[0]
        # 分段边界
        boundaries = np.concatenate([[-1], sign_changes, [len(nonzero_signs) - 1]])

        total_segments = len(boundaries) - 1
        if total_segments == 0:
            return _empty_kano_result()

        # 统计粘滞区间：OP 变化小但 PV 变化大
        stiction_segments = 0
        op_range = float(np.max(op_arr) - np.min(op_arr)) + 1e-9
        pv_range = float(np.max(pv_arr) - np.min(pv_arr)) + 1e-9

        for i in range(total_segments):
            start_idx = int(boundaries[i]) + 1
            end_idx = int(boundaries[i + 1]) + 1
            if end_idx <= start_idx:
                continue
            seg_op = op_arr[start_idx:end_idx]
            seg_pv = pv_arr[start_idx:end_idx]
            delta_op = float(np.max(seg_op) - np.min(seg_op)) / op_range
            delta_pv = float(np.max(seg_pv) - np.min(seg_pv)) / pv_range
            # 粘滞区间：OP 变化 < 5% 但 PV 变化 > 20%
            if delta_op < 0.05 and delta_pv > 0.20:
                stiction_segments += 1

        stiction_ratio = stiction_segments / total_segments

        # 判定规则（ADS §5.2.3: ρ > 0.6）
        detected = bool(stiction_ratio > 0.6)
        confidence = min(1.0, stiction_ratio) if detected else 0.0

        return {
            "detected": detected,
            "confidence": confidence,
            "stiction_ratio": stiction_ratio,
            "correlation": correlation,
            "std_ratio": std_ratio,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Kano 粘滞检测失败: %s", exc)
        return _empty_kano_result()


def _analyze_step_response(
    pv: np.ndarray,
    sp: np.ndarray,
    op: np.ndarray | None = None,
    ts: np.ndarray | list[float] | None = None,
) -> dict[str, Any]:
    """完整阶跃响应分析（过冲/衰减比/稳态误差）。

    设计依据：FDS §5.4.6 / ADS §5.3.2

    检测 SP 阶跃变化，提取响应曲线并计算：
    - 过冲 Overshoot = (PV_peak - SP_new) / (SP_new - SP_old) × 100%
    - 衰减比 DecayRatio = A2 / A1（第二峰/第一峰）
    - 稳态误差 = |mean(PV_tail) - SP_new|

    满足 2 项及以上指标超阈值 → 输出过激判定。

    Args:
        pv: PV 数据数组
        sp: SP 数据数组
        op: OP 数据数组（可选，用于辅助分析）
        ts: 时间戳数组（可选，用于时间归一化）

    Returns:
        {detected, confidence, overshoot, decay_ratio, steady_state_error, step_count}
    """
    min_len = min(len(pv), len(sp))
    if min_len < 16:
        return _empty_step_response_result()

    try:
        pv_arr = pv[:min_len].astype(float)
        sp_arr = sp[:min_len].astype(float)

        # SP 量程
        sp_range = float(np.max(sp_arr) - np.min(sp_arr))
        if sp_range < 1e-9:
            return _empty_step_response_result()

        # 检测 SP 阶跃点（变化超过 SP 量程的 5%）
        sp_diff = np.diff(sp_arr)
        step_threshold = sp_range * 0.05
        step_indices = np.where(np.abs(sp_diff) > step_threshold)[0]

        if len(step_indices) == 0:
            return _empty_step_response_result()

        # 分析第一个阶跃（最显著的）
        step_idx = int(step_indices[0])
        step_size = float(sp_arr[step_idx + 1] - sp_arr[step_idx])
        if abs(step_size) < 1e-9:
            return _empty_step_response_result()

        new_sp = float(sp_arr[step_idx + 1])
        float(sp_arr[step_idx])

        # 响应窗口：阶跃后的数据
        response_end = min(step_idx + 1 + min_len // 2, min_len)
        pv_response = pv_arr[step_idx + 1 : response_end]
        if len(pv_response) < 4:
            return _empty_step_response_result()

        # 指标1：过冲
        if step_size > 0:
            pv_peak = float(np.max(pv_response))
            overshoot = max(0.0, (pv_peak - new_sp) / step_size)
        else:
            pv_trough = float(np.min(pv_response))
            overshoot = max(0.0, (new_sp - pv_trough) / abs(step_size))

        # 指标2：衰减比（A2/A1，同方向连续峰）
        decay_ratio = _compute_decay_ratio(pv_response, new_sp, step_size)

        # 指标3：稳态误差（最后 20% 数据的均值与 SP 的偏差）
        tail_len = max(1, len(pv_response) // 5)
        pv_tail = pv_response[-tail_len:]
        steady_state_error = abs(float(np.mean(pv_tail)) - new_sp) / sp_range

        # 判定规则（ADS §5.3.2: 满足 2 项及以上）
        overshoot_threshold = 0.25  # 25%
        decay_ratio_threshold = 0.4
        sse_threshold = 0.05  # 5% SP 量程

        flags = [
            overshoot > overshoot_threshold,
            decay_ratio > decay_ratio_threshold,
            steady_state_error > sse_threshold,
        ]
        satisfied = sum(flags)

        # 过激判定：满足 2 项及以上
        detected = bool(satisfied >= 2)
        # 限制最大置信度为 95%，避免全部满足时直接到 100%
        confidence = min(0.95, satisfied / 3.0) if detected else 0.0

        response_ts = (
            ts[step_idx + 1 : response_end] if ts is not None else np.arange(len(pv_response))
        )
        sp_response = sp_arr[step_idx + 1 : response_end]

        return {
            "detected": detected,
            "confidence": confidence,
            "overshoot": overshoot,
            "decay_ratio": decay_ratio,
            "steady_state_error": steady_state_error,
            "step_count": len(step_indices),
            "timestamps": response_ts.tolist(),
            "pv_response": pv_response.tolist(),
            "sp_values": sp_response.tolist(),
            "step_indices": step_indices.tolist(),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("阶跃响应分析失败: %s", exc)
        return _empty_step_response_result()


def _compute_decay_ratio(pv_response: np.ndarray, new_sp: float, step_size: float) -> float:
    """计算衰减比 A2/A1（同方向连续振荡峰）。

    Args:
        pv_response: 阶跃后的 PV 响应数据
        new_sp: 新设定值
        step_size: 阶跃幅度

    Returns:
        衰减比（0~1），无振荡返回 0
    """
    if len(pv_response) < 8:
        return 0.0

    try:
        # 去除稳态值
        deviation = pv_response - new_sp
        if step_size < 0:
            deviation = -deviation

        # 寻找局部极大值（振荡峰）
        # 使用信号处理方法寻找峰值
        from scipy.signal import find_peaks

        peaks, _ = find_peaks(deviation, prominence=np.std(deviation) * 0.1)
        if len(peaks) < 2:
            return 0.0

        # A1 = 第一个峰幅值，A2 = 第二个峰幅值
        a1 = float(deviation[peaks[0]])
        a2 = float(deviation[peaks[1]])
        if a1 < 1e-9:
            return 0.0
        return min(1.0, a2 / a1)
    except Exception:
        return 0.0


def _detect_slow_response(
    pv: np.ndarray,
    sp: np.ndarray,
    control_type: str = "PI",
    ts: np.ndarray | list[float] | None = None,
) -> dict[str, Any]:
    """响应迟缓检测（Slow Response Detection）。

    设计依据：FDS §5.4.6 / ADS §5.4.2

    基于 PV 对 SP 变化的响应延迟：
    - 检测 SP 阶跃变化
    - 对 PV 响应拟合一阶滞后模型 PV(t) = K(1 - exp(-t/τ))
    - 计算响应时间常数 τ
    - 与期望响应时间（基于控制类型阈值）比较

    Args:
        pv: PV 数据数组
        sp: SP 数据数组
        control_type: 控制类型（P/PI/PID），影响期望响应时间
        ts: 时间戳数组（秒）

    Returns:
        {detected, confidence, time_constant, expected_time_constant, ratio}
    """
    min_len = min(len(pv), len(sp))
    if min_len < 16:
        return _empty_slow_response_result()

    try:
        pv_arr = pv[:min_len].astype(float)
        sp_arr = sp[:min_len].astype(float)

        # 时间轴（归一化到 0~1）
        if ts is not None and len(ts) >= min_len:
            ts_arr = np.asarray(ts[:min_len], dtype=float)
            ts_arr = ts_arr - ts_arr[0]
            total_time = float(ts_arr[-1] - ts_arr[0])
            if total_time < 1e-9:
                t_norm = np.linspace(0, 1, min_len)
            else:
                t_norm = (ts_arr - ts_arr[0]) / total_time
        else:
            t_norm = np.linspace(0, 1, min_len)

        # SP 量程
        sp_range = float(np.max(sp_arr) - np.min(sp_arr))
        if sp_range < 1e-9:
            return _empty_slow_response_result()

        # 检测 SP 阶跃点
        sp_diff = np.diff(sp_arr)
        step_threshold = sp_range * 0.05
        step_indices = np.where(np.abs(sp_diff) > step_threshold)[0]

        if len(step_indices) == 0:
            # 无阶跃：基于稳态偏差和 OP 活跃度判断
            bias = pv_arr - sp_arr
            bias_std = float(np.std(bias))
            # 稳态偏差大且变化缓慢 → 过保守
            # 提高阈值，避免误报：偏差标准差超过 SP 量程的 20% 才判定
            ratio = bias_std / sp_range
            detected = bool(ratio > 0.2)
            expected_tau = _expected_time_constant(control_type)
            # 降低置信度计算系数，避免轻易达到 100%
            confidence = min(0.8, ratio * 3) if detected else 0.0
            return {
                "detected": detected,
                "confidence": confidence,
                "time_constant": 0.0,
                "expected_time_constant": expected_tau,
                "ratio": ratio,
            }

        # 分析第一个阶跃后的响应
        step_idx = int(step_indices[0])
        step_size = float(sp_arr[step_idx + 1] - sp_arr[step_idx])
        if abs(step_size) < 1e-9:
            return _empty_slow_response_result()

        float(sp_arr[step_idx + 1])
        old_sp = float(sp_arr[step_idx])

        # 响应窗口
        response_end = min(step_idx + 1 + min_len // 2, min_len)
        pv_response = pv_arr[step_idx + 1 : response_end]
        t_response = t_norm[step_idx + 1 : response_end]
        if len(pv_response) < 8:
            return _empty_slow_response_result()

        # 一阶滞后拟合：PV(t) = old_sp + step_size * (1 - exp(-t/τ))
        # 归一化时间到 0~1 范围
        if t_response[-1] > t_response[0]:
            t_fit = (t_response - t_response[0]) / (t_response[-1] - t_response[0])
        else:
            t_fit = np.linspace(0, 1, len(t_response))

        # 使用 scipy 曲线拟合
        from scipy.optimize import curve_fit

        def _first_order_lag(t: np.ndarray, tau: float) -> np.ndarray:
            return old_sp + step_size * (1.0 - np.exp(-t / max(tau, 1e-6)))

        try:
            popt, _ = curve_fit(
                _first_order_lag,
                t_fit,
                pv_response,
                p0=[0.3],
                bounds=([0.001], [10.0]),
                maxfev=1000,
            )
            time_constant = float(popt[0])
        except Exception:
            # 拟合失败：使用 63.2% 响应时间近似
            target = old_sp + step_size * 0.632
            if step_size > 0:
                reach_idx = np.where(pv_response >= target)[0]
            else:
                reach_idx = np.where(pv_response <= target)[0]
            if len(reach_idx) > 0:
                time_constant = float(t_fit[reach_idx[0]])
            else:
                time_constant = 1.0

        # 期望时间常数（基于控制类型）
        expected_tau = _expected_time_constant(control_type)

        # 响应迟缓判定：实际时间常数 > 期望值
        ratio = time_constant / expected_tau if expected_tau > 0 else 0.0
        detected = bool(ratio > 2.0)  # 实际响应比期望慢 2 倍以上

        # 置信度计算：使用更保守的公式，避免轻易达到 100%
        # 拟合时间常数达到上限时，置信度不应直接到 100%
        if detected:
            # 限制最大置信度为 90%，避免时间常数拟合上限导致的误判
            confidence = min(0.9, ratio / 10.0)
        else:
            confidence = 0.0

        return {
            "detected": detected,
            "confidence": confidence,
            "time_constant": time_constant,
            "expected_time_constant": expected_tau,
            "ratio": ratio,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("响应迟缓检测失败: %s", exc)
        return _empty_slow_response_result()


def _expected_time_constant(control_type: str) -> float:
    """根据控制类型返回期望响应时间常数（归一化值）。

    基于工业实践经验：
    - P 控制：响应较慢，期望 τ ≈ 0.5
    - PI 控制：中等响应，期望 τ ≈ 0.3
    - PID 控制：快速响应，期望 τ ≈ 0.2
    """
    defaults = {
        "P": 0.5,
        "PI": 0.3,
        "PID": 0.2,
    }
    return defaults.get(control_type.upper(), 0.3)


def _detect_bias_shift(
    pv: np.ndarray,
    sp: np.ndarray,
    ts: np.ndarray | list[float] | None = None,
) -> dict[str, Any]:
    """偏差突变检测（Bias Shift Detection）。

    设计依据：FDS §5.4.6 / ADS §5.5.2

    检测 PV-SP 偏差的突变点：
    - 使用 CUSUM（累积和）算法检测均值变化
    - 统计偏差突变频率
    - 频率 > 5 次/小时 → 外扰频繁

    Args:
        pv: PV 数据数组
        sp: SP 数据数组
        ts: 时间戳数组（秒）

    Returns:
        {detected, confidence, shift_count, max_cusum, shift_magnitude}
    """
    min_len = min(len(pv), len(sp))
    if min_len < 16:
        return _empty_bias_shift_result()

    try:
        pv_arr = pv[:min_len].astype(float)
        sp_arr = sp[:min_len].astype(float)

        # 计算偏差
        bias = pv_arr - sp_arr

        # 偏差统计量
        bias_mean = float(np.mean(bias))
        bias_std = float(np.std(bias))
        if bias_std < 1e-9:
            return _empty_bias_shift_result()

        # CUSUM 参数
        # k = 允许的偏移量（典型为 0.5*σ）
        k = 0.5 * bias_std
        # h = 检测阈值（典型为 5*σ）
        h = 5.0 * bias_std

        # 双边 CUSUM
        bias_centered = bias - bias_mean
        cusum_pos = np.zeros(min_len)
        cusum_neg = np.zeros(min_len)
        shift_points: list[int] = []

        for i in range(1, min_len):
            cusum_pos[i] = max(0.0, cusum_pos[i - 1] + bias_centered[i] - k)
            cusum_neg[i] = min(0.0, cusum_neg[i - 1] + bias_centered[i] + k)
            if cusum_pos[i] > h or abs(cusum_neg[i]) > h:
                shift_points.append(i)
                # 重置 CUSUM
                cusum_pos[i] = 0.0
                cusum_neg[i] = 0.0

        # 计算时间窗口（秒）
        if ts is not None and len(ts) >= min_len:
            ts_arr = np.asarray(ts[:min_len], dtype=float)
            total_time = float(ts_arr[-1] - ts_arr[0])
        else:
            # 假设 1 秒采样间隔
            total_time = float(min_len)

        total_hours = total_time / 3600.0 if total_time > 0 else 1.0
        shift_count = len(shift_points)
        shift_frequency = shift_count / total_hours if total_hours > 0 else 0.0

        # 最大 CUSUM 值
        max_cusum = float(max(np.max(cusum_pos), abs(np.min(cusum_neg))))

        # 突变幅度
        shift_magnitude = 0.0
        if shift_points:
            shift_magnitude = float(np.mean(np.abs(bias_centered[shift_points])))

        # 判定规则（ADS §5.5.2: 频率 > 5 次/小时）
        detected = bool(shift_frequency > 5.0)
        # 限制最大置信度为 95%，避免频率很高时置信度直接到 100%
        confidence = min(0.95, shift_frequency / 20.0) if detected else 0.0

        cusum_ts = ts[:min_len].tolist() if ts is not None else np.arange(min_len).tolist()

        return {
            "detected": detected,
            "confidence": confidence,
            "shift_count": shift_count,
            "max_cusum": max_cusum,
            "shift_magnitude": shift_magnitude,
            "timestamps": cusum_ts,
            "cusum_pos": cusum_pos.tolist(),
            "cusum_neg": cusum_neg.tolist(),
            "shift_points": shift_points,
            "threshold": h,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("偏差突变检测失败: %s", exc)
        return _empty_bias_shift_result()


def _get_threshold(
    diag_configs: dict[str, Any],
    diag_code: str,
    key: str | None,
    default: Any,
) -> Any:
    """从诊断配置表中读取阈值参数（P0-1 配置表与算法对齐）。

    Args:
        diag_configs: 诊断配置字典 {diag_code: DiagnosisConfig}
        diag_code: 诊断标签代码（如 "OSCILLATION"）
        key: 阈值键名（如 "saturation_epsilon"），若为 None 则返回整个 threshold dict
        default: 默认值（配置不存在或键缺失时返回）

    Returns:
        阈值值，或默认值
    """
    config = diag_configs.get(diag_code)
    if config is None:
        return default
    threshold = getattr(config, "threshold", None)
    if threshold is None:
        return default
    if key is None:
        return threshold
    return threshold.get(key, default)


def _apply_expert_rules(algorithm_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """应用专家规则矩阵 R01-R06（FDS §5.4.6）。

    多标签优先级与互斥处理：
    - R01: OSCILLATION + VALVE_STICTION（stiction 置信度 > 0.5）→ 移除 OSCILLATION（根因是粘滞）
    - R02: OSCILLATION + OVERAGGRESSIVE（无 VALVE_STICTION）→ 移除 OSCILLATION（根因是参数过激）
    - R03: OVERAGGRESSIVE + OVERCONSERVATIVE → 保留置信度更高的
    - R04: PV 质量异常严重（bad_rate > 0.5）→ 移除所有其他标签（数据不可信）
    - R05: 所有算法置信度 < 0.5 → 添加 MANUAL_REVIEW 标签
    - R06: 标签优先级（用于排序）：QUALITY_ABNORMAL > VALVE_STICTION > OVERAGGRESSIVE >
           OVERCONSERVATIVE > OUTPUT_SATURATION > OSCILLATION > EXTERNAL_DISTURBANCE

    Args:
        algorithm_results: 算法结果列表，每个元素含 label/confidence/feature_values/evidence

    Returns:
        处理后的算法结果列表
    """
    if not algorithm_results:
        return algorithm_results

    try:
        # 标签优先级映射（数值越小优先级越高）
        priority_map = {
            "QUALITY_ABNORMAL": 1,
            "VALVE_STICTION": 2,
            "OVERAGGRESSIVE": 3,
            "OVERCONSERVATIVE": 4,
            "OUTPUT_SATURATION": 5,
            "OSCILLATION": 6,
            "EXTERNAL_DISTURBANCE": 7,
            "MANUAL_REVIEW": 99,
        }

        labels = {r["label"] for r in algorithm_results}

        # R04: 质量异常严重时移除其他标签
        quality_result = next(
            (r for r in algorithm_results if r["label"] == "QUALITY_ABNORMAL"), None
        )
        if quality_result is not None:
            bad_rate = quality_result.get("feature_values", {}).get("bad_quality_rate", 0.0)
            if bad_rate > 0.5:
                # 仅保留 QUALITY_ABNORMAL
                return [quality_result]

        # R01: OSCILLATION + VALVE_STICTION（stiction 置信度 > 0.5）→ 移除 OSCILLATION
        stiction_result = next(
            (r for r in algorithm_results if r["label"] == "VALVE_STICTION"), None
        )
        if (
            "OSCILLATION" in labels
            and "VALVE_STICTION" in labels
            and stiction_result is not None
            and stiction_result["confidence"] > 0.5
        ):
            algorithm_results = [r for r in algorithm_results if r["label"] != "OSCILLATION"]
            labels = {r["label"] for r in algorithm_results}

        # R02: OSCILLATION + OVERAGGRESSIVE（无 VALVE_STICTION）→ 移除 OSCILLATION
        if (
            "OSCILLATION" in labels
            and "OVERAGGRESSIVE" in labels
            and "VALVE_STICTION" not in labels
        ):
            algorithm_results = [r for r in algorithm_results if r["label"] != "OSCILLATION"]
            labels = {r["label"] for r in algorithm_results}

        # R03: OVERAGGRESSIVE + OVERCONSERVATIVE → 保留置信度更高的
        if "OVERAGGRESSIVE" in labels and "OVERCONSERVATIVE" in labels:
            agg_result = next(
                (r for r in algorithm_results if r["label"] == "OVERAGGRESSIVE"), None
            )
            cons_result = next(
                (r for r in algorithm_results if r["label"] == "OVERCONSERVATIVE"), None
            )
            if agg_result is not None and cons_result is not None:
                if agg_result["confidence"] >= cons_result["confidence"]:
                    algorithm_results = [
                        r for r in algorithm_results if r["label"] != "OVERCONSERVATIVE"
                    ]
                else:
                    algorithm_results = [
                        r for r in algorithm_results if r["label"] != "OVERAGGRESSIVE"
                    ]

        # R05: 所有算法置信度 < 0.5 → 添加 MANUAL_REVIEW
        if algorithm_results and all(r["confidence"] < 0.5 for r in algorithm_results):
            algorithm_results.append(
                {
                    "label": "MANUAL_REVIEW",
                    "confidence": 0.5,
                    "feature_values": {},
                    "evidence": {
                        "reasoning": "所有算法置信度均低于 0.5，建议人工复核",
                    },
                }
            )

        # R06: 按优先级排序
        algorithm_results.sort(key=lambda r: priority_map.get(r["label"], 100))

        return algorithm_results
    except Exception as exc:  # noqa: BLE001
        logger.warning("专家规则应用失败: %s", exc)
        return algorithm_results


def _deduplicate_labels(algorithm_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """标签去重（P1-4 修复标签重复写入）。

    同一标签保留置信度最高的记录，合并多条记录的 evidence 和 feature_values：
    - 主记录保留最高置信度记录的 evidence 和 feature_values
    - 其他记录的 evidence 追加到主记录的 "cross_validated_algorithms" 列表

    Args:
        algorithm_results: 算法结果列表

    Returns:
        去重后的算法结果列表
    """
    if not algorithm_results:
        return algorithm_results

    try:
        # 按 label 分组
        groups: dict[str, list[dict[str, Any]]] = {}
        for r in algorithm_results:
            groups.setdefault(r["label"], []).append(r)

        deduplicated: list[dict[str, Any]] = []
        for _label, records in groups.items():
            if len(records) == 1:
                deduplicated.append(records[0])
                continue

            # 按置信度降序排序，取最高
            records.sort(key=lambda r: r["confidence"], reverse=True)
            primary = records[0]

            # 收集其他记录的 evidence 作为交叉验证
            cross_validated = []
            for rec in records[1:]:
                cross_validated.append(
                    {
                        "label": rec["label"],
                        "confidence": rec["confidence"],
                        "evidence": rec.get("evidence", {}),
                        "feature_values": rec.get("feature_values", {}),
                    }
                )

            # 合并 feature_values（主记录优先，补充其他记录的键）
            merged_features = dict(primary.get("feature_values", {}))
            for rec in records[1:]:
                for k, v in rec.get("feature_values", {}).items():
                    if k not in merged_features:
                        merged_features[k] = v

            # 构建合并后的记录
            merged_record = {
                "label": primary["label"],
                "confidence": primary["confidence"],
                "feature_values": merged_features,
                "evidence": dict(primary.get("evidence", {})),
            }
            if cross_validated:
                merged_record["evidence"]["cross_validated_algorithms"] = cross_validated

            deduplicated.append(merged_record)

        return deduplicated
    except Exception as exc:  # noqa: BLE001
        logger.warning("标签去重失败: %s", exc)
        return algorithm_results


def _detect_oscillation_iae(
    pv: np.ndarray,
    sp: np.ndarray,
    sample_interval: float = 1.0,
    threshold: dict | None = None,
) -> dict[str, Any]:
    """IAE 零交叉相似率法振荡检测（FDS §5.4.6 在线主算法，Thornhill & Hägglund 1999）。

    算法原理：
    1. 计算控制偏差 e(t) = PV - SP
    2. 对偏差取绝对值后做积分（累积和），得到 IAE 累积曲线
    3. 对 IAE 累积曲线做一阶差分，找零交叉点（从正变负或从负变正）
    4. 计算相邻零交叉之间的间隔（采样点数）
    5. 相似率 = 1 - (std(间隔) / mean(间隔))，值越接近 1 越规律
    6. 若相似率 > similarity_threshold（默认 0.4）且零交叉数 >= 3，判定为振荡
    7. 置信度 = min(1.0, similarity * 1.5)

    Args:
        pv: PV 数据数组
        sp: SP 数据数组
        sample_interval: 采样间隔（秒）
        threshold: 阈值配置，支持键：
            - similarity_threshold: 相似率阈值（默认 0.4）
            - min_zero_crossings: 最小零交叉数（默认 3）

    Returns:
        {detected, confidence, similarity, zero_crossing_count, mean_period}
    """
    if threshold is None:
        threshold = {}
    similarity_threshold = float(threshold.get("similarity_threshold", 0.4))
    min_zero_crossings = int(threshold.get("min_zero_crossings", 3))

    min_len = min(len(pv), len(sp))
    if min_len < 8:
        return {
            "detected": False,
            "confidence": 0.0,
            "similarity": 0.0,
            "zero_crossing_count": 0,
            "mean_period": 0.0,
        }

    try:
        pv_arr = pv[:min_len].astype(float)
        sp_arr = sp[:min_len].astype(float)

        # 1. 计算控制偏差
        error = pv_arr - sp_arr

        # 2. IAE 累积曲线（绝对值积分 = 累积和，用于振幅参考）
        iae_cumsum = np.cumsum(np.abs(error))

        # 3. 对 IAE 累积曲线做一阶差分，找零交叉点
        # 由于 IAE 累积和单调递增，对累积曲线去线性趋势后找零交叉
        # 线性趋势 = 最小二乘拟合，去除后得到振荡分量
        n = len(iae_cumsum)
        x = np.arange(n, dtype=float)
        # 最小二乘线性拟合
        A = np.vstack([x, np.ones_like(x)]).T
        try:
            slope, intercept = np.linalg.lstsq(A, iae_cumsum, rcond=None)[0]
            iae_trend = slope * x + intercept
        except Exception:
            iae_trend = np.mean(iae_cumsum) * np.ones(n)
        iae_detrended = iae_cumsum - iae_trend

        # 零交叉：符号变化点
        signs = np.sign(iae_detrended)
        # 去除 0 符号（替换为前一非零符号避免误判）
        for i in range(1, len(signs)):
            if signs[i] == 0:
                signs[i] = signs[i - 1]

        zero_crossings = np.where(np.diff(signs) != 0)[0]

        if len(zero_crossings) < max(min_zero_crossings, 2):
            return {
                "detected": False,
                "confidence": 0.0,
                "similarity": 0.0,
                "zero_crossing_count": int(len(zero_crossings)),
                "mean_period": 0.0,
            }

        # 4. 计算相邻零交叉间隔
        intervals = np.diff(zero_crossings).astype(float)
        if len(intervals) < 2:
            return {
                "detected": False,
                "confidence": 0.0,
                "similarity": 0.0,
                "zero_crossing_count": int(len(zero_crossings)),
                "mean_period": 0.0,
            }

        mean_interval = float(np.mean(intervals))
        std_interval = float(np.std(intervals))

        # 5. 相似率 = 1 - (std / mean)，值越接近 1 越规律
        if mean_interval <= 0:
            similarity = 0.0
        else:
            similarity = max(0.0, 1.0 - std_interval / mean_interval)

        # 6. 振荡判定：相似率 > 阈值 且 零交叉数 >= 最小值
        detected = bool(
            similarity > similarity_threshold and len(zero_crossings) >= min_zero_crossings
        )

        # 7. 置信度
        confidence = min(1.0, similarity * 1.5) if detected else 0.0

        # 平均周期（秒）
        mean_period = mean_interval * sample_interval if sample_interval > 0 else mean_interval

        return {
            "detected": detected,
            "confidence": confidence,
            "similarity": similarity,
            "zero_crossing_count": int(len(zero_crossings)),
            "mean_period": float(mean_period),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("IAE 零交叉振荡检测失败: %s", exc)
        return {
            "detected": False,
            "confidence": 0.0,
            "similarity": 0.0,
            "zero_crossing_count": 0,
            "mean_period": 0.0,
        }


def _dempster_shafer_fusion(evidence: list[tuple[str, float]]) -> float:
    """多算法置信度融合（D-S 证据理论公式，FDS §5.4.7）。

    使用 FDS §5.4.7 指定的对数赔率融合公式：
        C_fused = (Π cᵢ) / (Π cᵢ + Π (1-cᵢ))
    其中 Π cᵢ 是所有置信度的乘积，Π (1-cᵢ) 是所有 (1-置信度) 的乘积。

    边界处理：
    - 空证据列表返回 0.0
    - 单条证据返回该置信度
    - 置信度为 0 或 1 时通过微小量避免除零

    Args:
        evidence: [(label, confidence), ...] 每个算法的标签和置信度

    Returns:
        融合后的置信度（0-1）
    """
    if not evidence:
        return 0.0
    if len(evidence) == 1:
        return evidence[0][1]

    # 全零置信度特判：所有证据置信度为 0 时融合结果为 0
    if all(conf <= 0.0 for _, conf in evidence):
        return 0.0
    # 全满置信度特判：所有证据置信度为 1 时融合结果为 1
    if all(conf >= 1.0 for _, conf in evidence):
        return 1.0

    # D-S 公式: C_fused = (Π cᵢ) / (Π cᵢ + Π (1-cᵢ))
    # 引入微小量 epsilon 避免置信度为 0 或 1 时乘积为 0 导致丢失信息
    eps = 1e-9
    prod_c = 1.0
    prod_not_c = 1.0
    for _, conf in evidence:
        c = max(eps, min(1.0 - eps, conf))
        prod_c *= c
        prod_not_c *= 1.0 - c

    denom = prod_c + prod_not_c
    if denom <= 0:
        return 0.0
    fused = prod_c / denom

    return max(0.0, min(1.0, fused))


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _get_tag_name(
    mappings: dict[str, LoopTagMapping],
    tags_map: dict[str, TagRegistry],
    role: str,
) -> str | None:
    """获取指定角色的 tag_name。"""
    mapping = mappings.get(role)
    if not mapping:
        return None
    tag = tags_map.get(str(mapping.tag_id))
    if not tag:
        return None
    return tag.tag_name


def _ts_to_float(ts: Any) -> float | None:
    """将时间戳转换为浮点数（秒）。

    支持 int/float、datetime 对象、ISO 8601 字符串。

    Args:
        ts: 时间戳（int/float/datetime/str）

    Returns:
        浮点秒数，转换失败返回 None
    """
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        return float(ts)
    if hasattr(ts, "timestamp"):
        return float(ts.timestamp())
    try:
        from datetime import datetime

        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return float(dt.timestamp())
    except (ValueError, TypeError):
        return None


def _compute_sample_interval(aligned: list[dict[str, Any]]) -> float:
    """从对齐后的时序数据计算平均采样间隔（秒）。

    Args:
        aligned: 对齐后的数据列表，每个元素含 "ts" 字段

    Returns:
        平均采样间隔（秒），默认 1.0
    """
    ts_values: list[float] = []
    for d in aligned:
        ts = d.get("ts")
        if ts is None:
            continue
        if isinstance(ts, (int, float)):
            ts_values.append(float(ts))
        elif hasattr(ts, "timestamp"):
            # datetime 对象
            ts_values.append(float(ts.timestamp()))
        else:
            # 尝试解析 ISO 格式字符串
            try:
                from datetime import datetime

                dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                ts_values.append(float(dt.timestamp()))
            except (ValueError, TypeError):
                continue

    if len(ts_values) < 2:
        return 1.0

    diffs = [ts_values[i + 1] - ts_values[i] for i in range(len(ts_values) - 1)]
    diffs = [d for d in diffs if d > 0]
    if not diffs:
        return 1.0
    return sum(diffs) / len(diffs)


def _build_scatter_plot_data(aligned: list[dict[str, Any]]) -> dict[str, list[float]]:
    """构建 PV-OP 散点图坐标数据。

    从对齐的时序数据中提取 PV(x) 和 OP(y) 坐标，降采样到最多 500 点。
    """
    max_points = 500
    points: list[tuple[float, float]] = []
    for d in aligned:
        pv = d.get("pv")
        op = d.get("op")
        if pv is None or op is None:
            continue
        try:
            points.append((float(pv), float(op)))
        except (TypeError, ValueError):
            continue

    if not points:
        return {"x": [], "y": []}

    # 均匀降采样
    if len(points) > max_points:
        step = len(points) / max_points
        indices = [int(i * step) for i in range(max_points)]
        points = [points[i] for i in indices]

    return {"x": [p[0] for p in points], "y": [p[1] for p in points]}


__all__ = [
    "DIAG_ALGORITHM_VERSION",
    "AsyncTask",
    "_analyze_step_response",
    "_apply_expert_rules",
    "_deduplicate_labels",
    "_detect_bias_shift",
    "_detect_choudhury_nonlinearity",
    "_detect_kano_stiction",
    "_detect_oscillation_iae",
    "_detect_slow_response",
    "_get_threshold",
    "run_diagnosis_hourly",
    "run_loop_diagnosis",
]
