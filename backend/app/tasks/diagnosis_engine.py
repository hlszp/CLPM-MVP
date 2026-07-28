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
import warnings
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import numpy as np
from celery.schedules import crontab
from sqlalchemy import delete, or_, select
from sqlalchemy.exc import IntegrityError

from app.constants.mode import AUTO_MODES, MODE_LABELS_EN
from app.contracts.data_types import ControlType, QualityStatus, RawTimeSeries
from app.models.diagnosis import (
    DiagnosisConfig,
    DiagnosisResult,
    DiagnosisRule,
    DiagnosisTag,
    DiagnosisTask,
    DiagnosisThresholdOverride,
)
from app.models.loop import LoopLedger, LoopTagMapping
from app.models.metric import KpiSnapshotHourly
from app.models.tag import TagRegistry
from app.models.tracker import ActionTracker
from app.services.confidence_evaluator import ConfidenceEvaluator
from app.services.diagnosis_rule import apply_rules as apply_db_rules
from app.services.diagnosis_rule import get_active_rules
from app.services.diagnosis_trigger_config import get_trigger_config
from app.services.metric_calculator.oscillation import (
    _DEFAULT_MAX_RATIO,
    _DEFAULT_MIN_RATIO,
    MIN_ZERO_CROSSINGS,
    OscillationRateCalculator,
)
from app.services.preprocessing.outlier_detection import OutlierDetector
from app.services.preprocessing.quality_code import map_quality_code
from app.services.preprocessing.quality_summary import compute_quality_summary
from app.services.preprocessing.thresholds import get_threshold as get_outlier_threshold
from app.tasks.celery_app import AsyncTask, celery_app

logger = logging.getLogger(__name__)

# 算法版本号
DIAG_ALGORITHM_VERSION = "DIAG_ENGINE_v1.0"

# 整改计划 C6：触发条件从 sys_config 配置读取（diagnosis_trigger.current）
# 热路径通过 get_trigger_config() 读取进程内缓存，保存后立即生效，不查库。
# 默认值：score_threshold=60, concurrency=5, min_data_points=32

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

# B4：loop_type → 预处理控制类型映射（与 kpi_calc._loop_type_to_control_type 对齐，
# SPEED/OTHER/缺省回退 FLOW 通用阈值）
_LOOP_TYPE_TO_CONTROL_TYPE: dict[str, ControlType] = {
    "FLOW": ControlType.FLOW,
    "PRESSURE": ControlType.PRESSURE,
    "TEMPERATURE": ControlType.TEMPERATURE,
    "LEVEL": ControlType.LEVEL,
    "ANALYSIS": ControlType.COMPOSITION,
}

#: 响应迟缓期望时间常数默认表（真实秒，按回路类型工业经验值，
#: _detect_slow_response / _expected_time_constant 的代码回退默认，
#: 与 _THRESHOLD_SCHEMA["OVERCONSERVATIVE"]["slow_expected_tau_seconds"] 同源）
_DEFAULT_EXPECTED_TAU_SECONDS: dict[str, float] = {
    "FLOW": 10.0,
    "PRESSURE": 30.0,
    "LEVEL": 120.0,
    "TEMPERATURE": 600.0,
    "ANALYSIS": 900.0,
    "OTHER": 60.0,
}

# 整改计划 C1：阈值键名 schema 登记（diag_code → {key: default}）
# 所有算法通过 _get_threshold 读取的阈值键名及其代码默认值集中登记于此，
# 便于迁移种子数据对齐与运行时缺省告警。
_THRESHOLD_SCHEMA: dict[str, dict[str, Any]] = {
    "OSCILLATION": {
        "similarity_threshold": 0.4,
        "min_zero_crossings": 4,
        # FFT 频域路径（_detect_oscillation_fft）
        "fft_osc_index_threshold": 0.3,
        "fft_min_zero_crossings": 5,
    },
    "VALVE_STICTION": {
        # Choudhury NGI/NLI 非线性判定（ADS §5.2.2）
        "choudhury_ngi_threshold": 0.001,
        "choudhury_nli_threshold": 0.01,
    },
    "QUALITY_ABNORMAL": {
        # 质量码规则矩阵 Q001-Q005（_analyze_quality）
        "q001_consecutive_bad": 10,
        "q002_bad_rate": 0.1,
        "q003_uncertain_rate": 0.2,
        "q004_bad_duration": 5,
        "q005_min_bad": 3,
        "q005_max_bad": 10,
        # 传感器故障检测（_detect_sensor_faults，共享 QUALITY_ABNORMAL diag_code）
        "frozen_window": 300,
        "frozen_eps": 1e-4,
        "frozen_ratio": 0.2,
        "noise_ratio": 3.0,
        "noise_segment": 0.5,
        "drift_k": 2.0,
        "drift_segments": 5,
    },
    "OUTPUT_SATURATION": {
        "op_high_limit": 100.0,
        "op_low_limit": 0.0,
        "saturation_epsilon": 2.0,
    },
    "OVERAGGRESSIVE": {
        # Harris 指数模型失配评估（_assess_model_mismatch）
        "harris_ar_order": 10,
        "harris_warn": 2.0,
        # 阶跃响应过激判定（ADS §5.3.2，满足 2 项及以上）
        "step_overshoot_threshold": 0.25,
        "step_decay_ratio_threshold": 0.4,
        "step_sse_threshold": 0.05,
    },
    "OVERCONSERVATIVE": {
        # 响应迟缓判定（_detect_slow_response）：实际 τ / 期望 τ 超过该比值判迟缓
        "slow_response_ratio_threshold": 2.0,
        # 无阶跃场景的稳态偏差占比判定（bias_std / sp_range）
        "slow_no_step_bias_ratio": 0.2,
        # 期望时间常数（真实秒，按回路类型工业经验值）
        "slow_expected_tau_seconds": _DEFAULT_EXPECTED_TAU_SECONDS,
    },
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
    # 整改计划 C6：体检轨可配开关（checkup_enabled）
    if not get_trigger_config().checkup_enabled:
        logger.info("体检轨已禁用（checkup_enabled=False），跳过本次执行")
        return {"total": 0, "diagnosed": 0, "failed": 0, "skipped": "checkup_disabled"}
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
                    KpiSnapshotHourly.score < get_trigger_config().score_threshold,
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
        _validate_threshold_config(diag_configs)

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

        # C2: 加载专家规则（在 with 块内加载，传给并发执行）
        rules = await get_active_rules(db)
        # C3: 预加载阈值差异化覆盖
        threshold_overrides = await _load_threshold_overrides(db)

    # 4. 并发诊断（信号量限制并发数，每协程独立 session 避免并发共享）
    diagnosed_count, failed_count = await _run_diag_tasks_concurrent(
        loop_task_ids,
        diag_configs,
        ts_start,
        ts_end,
        rules=rules,
        threshold_overrides=threshold_overrides,
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
    rules: list[DiagnosisRule] | None = None,
    threshold_overrides: list[DiagnosisThresholdOverride] | None = None,
) -> tuple[int, int]:
    """并发执行诊断任务（信号量限流，每协程独立 session 避免并发共享）。

    事件轨与体检轨共用：对每个 loop 进入 RUNNING → 诊断 → SUCCESS/FAILED 状态机。

    Args:
        loop_task_ids: loop_id → task_id 映射（仅含需执行的回路）
        diag_configs: 诊断配置字典
        ts_start: 时间窗起始
        ts_end: 时间窗结束
        rules: 专家规则列表（C2 规则引擎，可选）
        threshold_overrides: 阈值差异化覆盖列表（C3，可选）

    Returns:
        (diagnosed_count, failed_count)
    """
    from app.core.db import AsyncSessionLocal
    from app.services.data_source.factory import get_provider

    sem = asyncio.Semaphore(get_trigger_config().concurrency)

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
                        rules=rules,
                        threshold_overrides=threshold_overrides,
                    )
                    # D1：诊断产出标签时自动创建 ActionTracker（PENDING）
                    if result is not None and result.get("labels"):
                        await _auto_create_trackers(
                            worker_db,
                            loop_id,
                            result["labels"],
                            result.get("labelToDiagId", {}),
                            datetime.fromisoformat(result["diagnosedAt"]),
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
        _validate_threshold_config(diag_configs)

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

        # C2: 加载专家规则（在 with 块内加载，传给并发执行）
        rules = await get_active_rules(db)
        # C3: 预加载阈值差异化覆盖
        threshold_overrides = await _load_threshold_overrides(db)

    # 4. 并发诊断（与事件轨共用并发执行逻辑）
    diagnosed_count, failed_count = await _run_diag_tasks_concurrent(
        loop_task_ids,
        diag_configs,
        ts_start,
        ts_end,
        rules=rules,
        threshold_overrides=threshold_overrides,
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
        _validate_threshold_config(diag_configs)

        # C2: 加载专家规则
        rules = await get_active_rules(db)
        # C3: 加载阈值差异化覆盖（caller 级预载，避免 _diagnose_loop 内查库）
        threshold_overrides = await _load_threshold_overrides(db)

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
                rules=rules,
                threshold_overrides=threshold_overrides,
            )
            # D1：诊断产出标签时自动创建 ActionTracker（PENDING）
            if result is not None and result.get("labels"):
                await _auto_create_trackers(
                    db,
                    loop_id,
                    result["labels"],
                    result.get("labelToDiagId", {}),
                    datetime.fromisoformat(result["diagnosedAt"]),
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

    # 自动归档：SUCCESS 状态任务自动归档，免手动操作。
    # 归档后任务从"诊断任务"列表移入"诊断记录"页面。
    # FAILED 任务不自动归档，保留在任务列表供用户排查后手动归档。
    if status == "SUCCESS":
        task.is_archived = True
        task.archived_at = datetime.now(UTC).replace(tzinfo=None)
        task.archived_by = "system-auto"
        logger.info("诊断任务 %s 成功完成，已自动归档", task_id)


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


async def _auto_create_trackers(
    db,
    loop_id: str,
    labels: list[str],
    label_to_diag_id: dict[str, str],
    diagnosed_at: datetime,
) -> None:
    """D1：诊断产出标签时自动创建 ActionTracker（PENDING）。

    同一回路同一标签在 PENDING/IN_PROGRESS 状态下不重复建单
    （uk_action_tracker_open 部分唯一索引约束）。闭环后新诊断可再建新单，
    历史记录保留。

    并发防护：SELECT 预过滤 + INSERT 之间存在竞态窗口，唯一索引
    uk_action_tracker_open 是最终防线；INSERT 触发 IntegrityError 时
    回滚该单条并跳过（视为已被并发建单）。

    Args:
        db: 异步数据库会话
        loop_id: 回路 ID
        labels: 诊断标签列表
        label_to_diag_id: 标签到诊断结果 ID 的映射
        diagnosed_at: 诊断时间
    """
    labels = [lbl for lbl in labels if lbl]
    if not labels:
        return

    # 查询该回路已有的开放态 tracker（PENDING/IN_PROGRESS），避免重复建单
    existing_result = await db.execute(
        select(ActionTracker.diagnosis_label)
        .where(ActionTracker.loop_id == loop_id)
        .where(ActionTracker.action_status.in_(["PENDING", "IN_PROGRESS"]))
        .where(ActionTracker.diagnosis_label.in_(labels))
    )
    existing_labels = {str(r) for r in existing_result.scalars().all()}

    for label in labels:
        if label in existing_labels:
            continue
        severity = _TAG_SEVERITY_MAP.get(label, "INFO")
        tracker = ActionTracker(
            id=str(uuid4()),
            loop_id=loop_id,
            diagnosis_label=label,
            action_status="PENDING",
            trigger_type="auto",
            triggered_by="system",
            severity=severity,
            diagnosis_result_id=label_to_diag_id.get(label),
            # created_at 不显式设置：沿用 server_default=now()，与 manual tracker
            # 口径一致（本地时间）。之前用 diagnosed_at(UTC naive) 会导致 auto/manual
            # tracker 的 created_at 时区不一致，sortBy=created_at 排序错乱。
            # updated_at 初始为 None（建单时尚无处理记录）。
        )
        # 用 SAVEPOINT 包裹单条插入：并发被抢先建单时仅回滚该条，
        # 不影响外层事务中已写入的 diagnosis_result / diagnosis_tag。
        try:
            async with db.begin_nested():
                db.add(tracker)
        except IntegrityError:
            logger.info(
                "D1 自动建单跳过（并发已建单）: loop_id=%s label=%s",
                loop_id,
                label,
            )
            continue
        logger.info(
            "D1 自动建单: loop_id=%s label=%s severity=%s diag_result_id=%s",
            loop_id,
            label,
            severity,
            label_to_diag_id.get(label),
        )


async def _diagnose_loop(
    db,
    loop_id: str,
    diag_configs: dict[str, DiagnosisConfig],
    ts_start: datetime,
    ts_end: datetime,
    query_wide_fn,
    task_id: str | None = None,
    labels: list[str] | None = None,
    rules: list[DiagnosisRule] | None = None,
    threshold_overrides: list[DiagnosisThresholdOverride] | None = None,
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
        rules: 专家规则列表（C2 规则引擎，可选）。None 时回退到硬编码规则
        threshold_overrides: 阈值差异化覆盖列表（C3，可选）。
            由调用者预加载，_diagnose_loop 内纯内存合并，不查 DB。

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

    # C3 差异化阈值：按回路类型/装置/回路级覆盖合并阈值（纯内存合并，不查 DB）
    if threshold_overrides:
        diag_configs = _merge_threshold_overrides(diag_configs, threshold_overrides, loop)

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
    min_points = get_trigger_config().min_data_points
    if len(raw_series.timestamps) < min_points:
        logger.info(
            "回路 %s 数据点不足 (%d < %d)",
            loop.tag_name,
            len(raw_series.timestamps),
            min_points,
        )
        return None

    # 构建对齐的数据并剔除 PV 质量码为 Bad 的点
    pv_quality_codes = raw_series.quality_codes.get("pv_quality", [])
    pv_quality_data: list[dict[str, str]] = []
    aligned: list[dict[str, Any]] = []
    # aligned 各行对应的原始时序索引（B4：raw 级有效性标记用）
    aligned_src_indices: list[int] = []
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
        aligned_src_indices.append(i)

    if len(aligned) < min_points:
        logger.info("回路 %s 对齐后数据点不足", loop.tag_name)
        return None

    # B4 轻量数据质量预处理：复用 OutlierDetector 剔除 SPIKE/JUMP/OUT_OF_RANGE/NAN
    # 异常点（pv/sp/op/ts 同步剔除保持对齐；TS_ANOMALY/HF_NOISE 仅标记不剔除），
    # 并由 compute_quality_summary 得出 valid_rate（供 B5 可信度分级）。不改量纲/归一化。
    aligned, valid_rate = _apply_outlier_preprocessing(
        aligned,
        aligned_src_indices,
        raw_series,
        loop,
        mappings,
        tags_map,
    )
    logger.info("回路 %s 数据质量摘要: valid_rate=%.4f", loop.tag_name, valid_rate)

    # B5 可信度分级（算法说明 §3.7.2，与 KPI 链路 ConfidenceEvaluator 统一）：
    # 基于 B4 有效数据率 valid_rate 判定 A/B/C/D/E，随每条 DiagnosisResult 落库
    confidence_level = ConfidenceEvaluator.evaluate(valid_rate).value
    logger.info(
        "回路 %s 可信度分级: valid_rate=%.4f → %s",
        loop.tag_name,
        valid_rate,
        confidence_level,
    )

    # 执行各算法
    pv_values = np.array([d["pv"] for d in aligned if d.get("pv") is not None], dtype=float)
    sp_values = np.array([d["sp"] for d in aligned if d.get("sp") is not None], dtype=float)
    op_values = np.array([d["op"] for d in aligned if d.get("op") is not None], dtype=float)

    # OP/MODE 成对提取（P0：饱和率分析仅统计自控模式）
    # 原实现 op/mode 分别按各自 is not None 过滤，某行只有一个字段缺失时
    # 两数组索引错位；改同一循环按行成对过滤，同行皆有效才保留
    sat_op_list: list[Any] = []
    sat_mode_list: list[Any] = []
    for d in aligned:
        if d.get("op") is not None and d.get("mode") is not None:
            sat_op_list.append(d["op"])
            sat_mode_list.append(d["mode"])
    sat_op_values = np.array(sat_op_list, dtype=float)
    mode_values = np.array(sat_mode_list, dtype=object)

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
        osc_result = _detect_oscillation_fft(
            pv_values,
            sample_interval,
            threshold=_get_threshold(diag_configs, "OSCILLATION", None, None),
        )

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

        # 3b. 传感器故障检测（B2：卡死/噪声突增/漂移，与质量码统计并列，
        # 命中时产出 QUALITY_ABNORMAL 标签，sensor_subtype 区分子类型）
        sensor_fault_result = _detect_sensor_faults(
            pv_values,
            sp_values if len(sp_values) == len(pv_values) else None,
            threshold=_get_threshold(diag_configs, "QUALITY_ABNORMAL", None, None),
        )
    else:
        quality_result = _empty_quality_result()
        sensor_fault_result = _empty_sensor_fault_result()

    # 4. OP 饱和率分析（P0-3：仅自控模式 + 绝对工程限位）
    if saturation_enabled:
        has_mode = len(mode_values) > 0
        saturation_result = _analyze_saturation(
            sat_op_values if has_mode else op_values,
            mode_values if has_mode else None,
            threshold=_get_threshold(diag_configs, "OUTPUT_SATURATION", None, None),
        )
    else:
        saturation_result = _empty_saturation_result()

    # 5. Choudhury NGI/NLI 非线性检测（阀门粘滞高级检测，设计依据：FDS §5.4.6）
    if stiction_enabled:
        choudhury_result = _detect_choudhury_nonlinearity(
            pv_values,
            op_values,
            threshold=_get_threshold(diag_configs, "VALVE_STICTION", None, None),
        )
    else:
        choudhury_result = _empty_choudhury_result()

    # 6. Kano 统计法粘滞检测（与 Choudhury 互为交叉验证）
    if stiction_enabled:
        kano_result = _detect_kano_stiction(pv_values, op_values)
    else:
        kano_result = _empty_kano_result()

    # 提取时间戳数组（供阶跃响应/响应迟缓/偏差突变算法使用）
    # 热路径向量化：批量换算时间戳，禁止逐点 naive datetime .timestamp()
    # （macOS fork 时区慢路径，项目红线）
    raw_ts = [d.get("ts") for d in aligned if d.get("ts") is not None]
    ts_values = _ts_list_to_seconds(raw_ts)
    # 若时间戳数量与 PV 不一致，回退为 None（使用等间隔假设）
    ts_param = ts_values if len(ts_values) == len(pv_values) else None

    # 7. 完整阶跃响应分析（过冲/衰减比/稳态误差）
    if overaggressive_enabled:
        step_response_result = _analyze_step_response(
            pv_values,
            sp_values,
            op_values,
            ts_param,
            threshold=_get_threshold(diag_configs, "OVERAGGRESSIVE", None, None),
        )
    else:
        step_response_result = _empty_step_response_result()

    # 8. 响应迟缓检测（一阶滞后拟合，真实秒单位 τ 与回路类型经验秒数比较）
    if overconservative_enabled:
        slow_response_result = _detect_slow_response(
            pv_values,
            sp_values,
            getattr(loop, "loop_type", None),
            ts_param,
            sample_interval=sample_interval,
            threshold=_get_threshold(diag_configs, "OVERCONSERVATIVE", None, None),
        )
    else:
        slow_response_result = _empty_slow_response_result()

    # 9. 偏差突变检测（CUSUM）
    if disturbance_enabled:
        bias_shift_result = _detect_bias_shift(pv_values, sp_values, ts_param)
    else:
        bias_shift_result = _empty_bias_shift_result()

    # 10. Harris 指数模型失配评估（B3：不单独产出标签，
    # 受 OVERAGGRESSIVE/OVERCONSERVATIVE 任一门控，供可视化与证据增强）
    if overaggressive_enabled or overconservative_enabled:
        harris_result = _assess_model_mismatch(
            pv_values,
            sp_values if len(sp_values) == len(pv_values) else None,
            threshold=_get_threshold(diag_configs, "OVERAGGRESSIVE", None, None),
        )
    else:
        harris_result = _empty_harris_result()

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

    # 传感器故障检测 → QUALITY_ABNORMAL（B2，sensor_subtype 区分子类型）
    if sensor_fault_result["detected"]:
        algorithm_results.append(
            {
                "label": "QUALITY_ABNORMAL",
                "confidence": sensor_fault_result["confidence"],
                "feature_values": {
                    "sensor_subtype": sensor_fault_result["sensor_subtype"],
                    "frozen_max_segment": sensor_fault_result["frozen_max_segment"],
                    "frozen_segment_ratio": sensor_fault_result["frozen_segment_ratio"],
                    "noise_std_ratio": sensor_fault_result["noise_std_ratio"],
                    "drift_magnitude": sensor_fault_result["drift_magnitude"],
                },
                "evidence": {
                    "reasoning": sensor_fault_result["reasoning"],
                    "algorithm": "SENSOR_FAULT_v1.0",
                },
            }
        )
    # 无条件保存传感器故障可视化数据
    all_visualization_data.update(
        {
            "sensor_fault_detected": sensor_fault_result["detected"],
            "sensor_subtype": sensor_fault_result["sensor_subtype"],
            "frozen_max_segment": sensor_fault_result["frozen_max_segment"],
            "noise_std_ratio": sensor_fault_result["noise_std_ratio"],
            "drift_magnitude": sensor_fault_result["drift_magnitude"],
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

    # Harris 指数模型失配评估（B3）：不产出标签，仅写入可视化数据；
    # OVERAGGRESSIVE / OVERCONSERVATIVE 命中时并入其 evidence 作证据增强
    if harris_result["harris_index"] is not None:
        all_visualization_data.update(
            {
                "harris_index": harris_result["harris_index"],
                "harris_warn": harris_result["harris_warn"],
            }
        )
        for r in algorithm_results:
            if r["label"] in ("OVERAGGRESSIVE", "OVERCONSERVATIVE"):
                r["evidence"]["harris_index"] = harris_result["harris_index"]
                r["evidence"]["harris_warn"] = harris_result["harris_warn"]

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
    # C2：优先使用 DB 规则引擎，无规则时回退到硬编码规则
    if rules:
        algorithm_results = apply_db_rules(algorithm_results, rules)
    else:
        algorithm_results = _apply_expert_rules(algorithm_results)

    # D-S 证据融合（P1 修复融合口径）：仅对同一标签的多算法结果在去重前融合。
    # 不同标签代表互斥的故障假设，跨标签置信度不可做赔率乘积，不再跨标签融合
    algorithm_results = _fuse_same_label_confidence(algorithm_results)

    # 标签去重（P1-4：同一标签保留置信度最高的记录）
    algorithm_results = _deduplicate_labels(algorithm_results)

    # 回路级综合置信度：融合去重后的最高标签置信度（无跨标签融合语义）
    fused_confidence = max((r["confidence"] for r in algorithm_results), default=0.0)

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
    # B7 可视化存储瘦身：全量可视化数组仅并入置信度最高的主标签记录，
    # 其余记录只存自身标量 feature_values（可视化数组键已由主记录承载；
    # 详情/可视化端点按回路聚合并集读取，对外输出结构不变）
    primary_idx = max(
        range(len(algorithm_results)),
        key=lambda i: algorithm_results[i]["confidence"],
    )
    diagnosed_at = datetime.now(UTC).replace(tzinfo=None)
    # C4: 记录诊断时使用的阈值版本快照（取所有配置的最大版本号）
    # 防御式计算：空 diag_configs 或 version 非整数时回落到 1
    try:
        threshold_version = max(
            (int(c.version or 1) for c in diag_configs.values()),
            default=1,
        )
    except (TypeError, ValueError):
        threshold_version = 1
    # D1：收集 label → diag_record_id 映射，供调用方自动建单使用
    label_to_diag_id: dict[str, str] = {}
    for idx, result in enumerate(algorithm_results):
        confidence_decimal = Decimal(str(round(result["confidence"] * 100, 2)))
        # P1 修复：evidence_chain 仅承载本标签自身证据与同标签融合标注
        # （same_label_fusion），不再写入跨标签融合值
        evidence_chain = dict(result["evidence"])
        # B5：每条记录写入统一可信度等级与有效数据率（回路级，各标签一致）
        own_features = {
            **result.get("feature_values", {}),
            "confidence_level": confidence_level,
            "valid_rate": valid_rate,
        }
        if idx == primary_idx:
            # 主标签记录：并入所有算法的可视化数据
            feature_values = {**all_visualization_data, **own_features}
        else:
            # 非主标签记录：仅保留标量，不冗余存储可视化数组
            feature_values = {k: v for k, v in own_features.items() if not isinstance(v, list)}
        diag_record = DiagnosisResult(
            id=str(uuid4()),
            loop_id=loop_id,
            diag_label=result["label"],
            confidence=confidence_decimal,
            feature_values=feature_values,
            evidence_chain=evidence_chain,
            algorithm_version=DIAG_ALGORITHM_VERSION,
            # C4: 记录诊断时使用的阈值版本快照（取所有配置的最大版本号）
            threshold_version=threshold_version,
            diagnosed_at=diagnosed_at,
            task_id=task_id,
        )
        db.add(diag_record)
        label_to_diag_id[result["label"]] = diag_record.id
        # A11：同步 upsert diagnosis_tag（D-S 融合后标签逐条处理）
        _upsert_diagnosis_tag(db, active_tag_map, loop_id, result, diagnosed_at)

    return {
        "loopId": loop_id,
        "tagName": loop.tag_name,
        "diagnosedAt": diagnosed_at.isoformat(),
        "labels": [r["label"] for r in algorithm_results],
        "fusedConfidence": fused_confidence,
        "confidenceLevel": confidence_level,
        "validRate": valid_rate,
        "algorithmVersion": DIAG_ALGORITHM_VERSION,
        "status": "SUCCESS",
        # D1：供调用方自动建单使用（label → diag_result_id 映射）
        "labelToDiagId": label_to_diag_id,
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


def _detect_oscillation_fft(
    pv_values: np.ndarray,
    sample_interval: float = 1.0,
    threshold: dict | None = None,
) -> dict[str, Any]:
    """FFT 频域分析检测振荡。

    P2 修复：
    - 单边谱幅值按 2·|X(k)|/Σw 换算（此前漏乘 2，恢复幅值仅为真实值一半）
    - 加 Hann 窗抑制频谱泄漏，幅值按窗函数相干增益（Σw）补偿
    - 振荡指数/零交叉判定阈值走 threshold 配置（fft_osc_index_threshold /
      fft_min_zero_crossings），不再硬编码 0.3 / 5

    Args:
        pv_values: PV 数据数组
        sample_interval: 采样间隔（秒），用于频率换算
        threshold: 阈值配置，支持键：
            - fft_osc_index_threshold: 振荡指数阈值（默认 0.3）
            - fft_min_zero_crossings: 最小零交叉数（默认 5）

    Returns:
        {detected, confidence, amplitude, frequency, index}
    """
    if not isinstance(threshold, dict):
        threshold = {}
    osc_index_threshold = float(threshold.get("fft_osc_index_threshold", 0.3))
    min_zero_crossings = int(threshold.get("fft_min_zero_crossings", 5))

    if len(pv_values) < 8:
        return _empty_osc_result()

    try:
        N = len(pv_values)
        fs = 1.0 / sample_interval if sample_interval > 0 else 1.0  # 采样频率 (Hz)
        # 去均值
        pv_centered = pv_values - np.mean(pv_values)
        # Hann 窗抑制频谱泄漏；幅值归一化分母 Σw 同时补偿窗的相干增益
        window = np.hanning(N)
        window_sum = float(np.sum(window))
        if window_sum <= 0:
            return _empty_osc_result()
        # FFT
        fft_vals = np.fft.rfft(pv_centered * window)
        fft_magnitude = np.abs(fft_vals)
        # 主频
        if len(fft_magnitude) <= 1:
            return _empty_osc_result()
        peak_idx = int(np.argmax(fft_magnitude[1:])) + 1
        # 单边谱幅值：2·|X(k)|/Σw（修复此前漏乘 2 导致的幅值减半）
        amplitude = float(2.0 * fft_magnitude[peak_idx] / window_sum)
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

        # 振荡判定：振荡指数与零交叉次数阈值均来自配置
        detected = osc_index > osc_index_threshold and zero_crossings > min_zero_crossings
        # 置信度：基于振荡指数
        confidence = min(1.0, osc_index * 1.5) if detected else 0.0

        frequencies = np.arange(len(fft_magnitude)) * fs / N
        # 单边幅值谱：k>0 乘 2，DC 分量（k=0）不乘
        amplitudes = 2.0 * fft_magnitude / window_sum
        amplitudes[0] = fft_magnitude[0] / window_sum

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


def _empty_sensor_fault_result() -> dict[str, Any]:
    """空传感器故障检测结果（QUALITY_ABNORMAL 算法禁用时占位）。"""
    return {
        "detected": False,
        "sensor_subtype": None,
        "confidence": 0.0,
        "frozen_max_segment": 0,
        "frozen_segment_ratio": 0.0,
        "noise_std_ratio": 1.0,
        "drift_magnitude": 0.0,
        "reasoning": "",
    }


def _rolling_std(x: np.ndarray, window: int) -> np.ndarray:
    """滚动标准差（滑动窗口，O(n) 累积和实现）。

    Args:
        x: 输入数组
        window: 窗口长度（点）

    Returns:
        长度为 len(x) - window + 1 的滚动标准差数组；x 短于 window 时返回空数组
    """
    n = len(x)
    if n < window or window <= 0:
        return np.array([], dtype=float)
    c = np.cumsum(np.insert(x, 0, 0.0))
    c2 = np.cumsum(np.insert(x * x, 0, 0.0))
    sums = c[window:] - c[:-window]
    sums2 = c2[window:] - c2[:-window]
    mean = sums / window
    var = sums2 / window - mean * mean
    # 累积和浮点误差可能使理论零方差段出现微小负值
    return np.sqrt(np.maximum(var, 0.0))


def _detect_sensor_faults(
    pv_values: np.ndarray,
    sp_values: np.ndarray | None = None,
    threshold: dict | None = None,
) -> dict[str, Any]:
    """传感器故障检测（B2：卡死/噪声突增/漂移三个子检测）。

    子检测（命中即产出 QUALITY_ABNORMAL 标签，sensor_subtype 区分）：
    - frozen（卡死/冻结）：滚动窗口（frozen_window 点）std < frozen_eps 的最长持续段，
      占信号比例 > frozen_ratio → 传感器卡死（置信度 0.85）
    - noisy（噪声突增）：前 noise_segment 比例段 vs 剩余段的滚动 std 中位数比值
      > noise_ratio（任一侧突增）→ 噪声突增（置信度 0.7）
    - drift（漂移）：等长 drift_segments 分段均值单调递进且首尾均值差
      > drift_k × 全局 std → 漂移（置信度 0.65）；若 SP 提供且同向同步变化
      （首尾段均值差同号且幅度 ≥ PV 漂移量的一半）则判为工艺真实变化，不判漂移

    Args:
        pv_values: PV 数据数组（工程单位）
        sp_values: SP 数据数组（可选，需与 pv_values 等长；用于漂移/工艺变化区分）
        threshold: 阈值配置（可选），支持键：
            - frozen_window: 冻结判定滚动窗口点数（默认 300，1 秒采样下约 5 分钟）
            - frozen_eps: 冻结判定滚动 std 阈值（默认 1e-4）
            - frozen_ratio: 冻结段占信号比例阈值（默认 0.2）
            - noise_ratio: 噪声突增 std 比值阈值（默认 3.0）
            - noise_segment: 噪声对比前段比例（默认 0.5，即前后各半）
            - drift_k: 漂移幅度系数（默认 2.0）
            - drift_segments: 漂移分段数（默认 5）

    Returns:
        {detected, sensor_subtype, confidence, frozen_max_segment,
         frozen_segment_ratio, noise_std_ratio, drift_magnitude, reasoning}
    """
    if threshold is None:
        threshold = {}
    frozen_window = int(threshold.get("frozen_window", 300))
    frozen_eps = float(threshold.get("frozen_eps", 1e-4))
    frozen_ratio = float(threshold.get("frozen_ratio", 0.2))
    noise_ratio = float(threshold.get("noise_ratio", 3.0))
    noise_segment = float(threshold.get("noise_segment", 0.5))
    drift_k = float(threshold.get("drift_k", 2.0))
    drift_segments = int(threshold.get("drift_segments", 5))

    result = _empty_sensor_fault_result()

    n = len(pv_values)
    if n < get_trigger_config().min_data_points:
        return result

    try:
        hits: list[tuple[str, float, str]] = []  # (subtype, confidence, reasoning)

        # --- 1. 卡死/冻结：滚动 std < eps 的最长持续段 ---
        frozen_max_segment = 0
        frozen_segment_ratio = 0.0
        if n >= frozen_window:
            rstd = _rolling_std(pv_values, frozen_window)
            below = rstd < frozen_eps
            # 最长连续 True 段；段内每个窗口均为冻结 → 冻结段长度 = run + window - 1
            max_run = 0
            run = 0
            for b in below:
                if b:
                    run += 1
                    max_run = max(max_run, run)
                else:
                    run = 0
            if max_run > 0:
                frozen_max_segment = max_run + frozen_window - 1
                frozen_segment_ratio = frozen_max_segment / n
            result["frozen_max_segment"] = frozen_max_segment
            result["frozen_segment_ratio"] = frozen_segment_ratio
            if frozen_segment_ratio > frozen_ratio:
                hits.append(
                    (
                        "frozen",
                        0.85,
                        (
                            f"传感器卡死：最长冻结段 {frozen_max_segment} 点"
                            f"（占比 {frozen_segment_ratio:.2f} > {frozen_ratio:.2f}），"
                            f"滚动 std < {frozen_eps}"
                        ),
                    )
                )

        # --- 2. 噪声突增：前段 vs 后段滚动 std 中位数比值 ---
        noise_std_ratio = 1.0
        split = int(n * noise_segment)
        if split >= 16 and n - split >= 16:
            win = max(10, min(30, split // 4, (n - split) // 4))
            std_first = _rolling_std(pv_values[:split], win)
            std_second = _rolling_std(pv_values[split:], win)
            if len(std_first) > 0 and len(std_second) > 0:
                med_first = float(np.median(std_first))
                med_second = float(np.median(std_second))
                denom = min(med_first, med_second)
                numer = max(med_first, med_second)
                # 分母近零（一侧完全平坦）时，另一侧有任何波动即视为极大比值
                if denom > 1e-12:
                    noise_std_ratio = numer / denom
                else:
                    noise_std_ratio = np.inf if numer > 1e-12 else 1.0
                result["noise_std_ratio"] = (
                    float(noise_std_ratio) if np.isfinite(noise_std_ratio) else 999.0
                )
                if noise_std_ratio > noise_ratio:
                    hits.append(
                        (
                            "noisy",
                            0.7,
                            (
                                f"传感器噪声突增：前后段滚动 std 中位数比值 "
                                f"{result['noise_std_ratio']:.2f} > {noise_ratio:.2f}"
                                f"（前段 {med_first:.4g} / 后段 {med_second:.4g}）"
                            ),
                        )
                    )

        # --- 3. 漂移：等长分段均值单调递进 + 幅度超阈值 ---
        drift_magnitude = 0.0
        seg_len = n // drift_segments
        if seg_len >= 4:
            means = np.array(
                [
                    float(np.mean(pv_values[i * seg_len : (i + 1) * seg_len]))
                    for i in range(drift_segments)
                ]
            )
            diffs = np.diff(means)
            drift_magnitude = float(means[-1] - means[0])
            result["drift_magnitude"] = drift_magnitude
            monotonic = bool(np.all(diffs > 0)) or bool(np.all(diffs < 0))
            global_std = float(np.std(pv_values))
            if monotonic and abs(drift_magnitude) > drift_k * global_std:
                # SP 同向同步变化 → 工艺真实变化而非传感器漂移
                sp_synced = False
                if sp_values is not None and len(sp_values) == n:
                    sp_means = np.array(
                        [
                            float(np.mean(sp_values[i * seg_len : (i + 1) * seg_len]))
                            for i in range(drift_segments)
                        ]
                    )
                    sp_magnitude = float(sp_means[-1] - sp_means[0])
                    if sp_magnitude * drift_magnitude > 0 and abs(sp_magnitude) >= 0.5 * abs(
                        drift_magnitude
                    ):
                        sp_synced = True
                if not sp_synced:
                    hits.append(
                        (
                            "drift",
                            0.65,
                            (
                                f"传感器漂移：分段均值单调递进，首尾均值差 "
                                f"{drift_magnitude:.4g}（{abs(drift_magnitude) / global_std:.2f}σ"
                                f" > {drift_k:.1f}σ）且 SP 未同向变化"
                            ),
                        )
                    )

        if hits:
            # 多子类型同时命中时按严重度取最高（frozen > noisy > drift）
            subtype, confidence, reasoning = max(hits, key=lambda h: h[1])
            result["detected"] = True
            result["sensor_subtype"] = subtype
            result["confidence"] = confidence
            result["reasoning"] = reasoning

        return result
    except Exception as exc:  # noqa: BLE001
        logger.warning("传感器故障检测失败: %s", exc)
        return _empty_sensor_fault_result()


def _empty_harris_result() -> dict[str, Any]:
    """空 Harris 指数评估结果（OVERAGGRESSIVE/OVERCONSERVATIVE 均未启用时占位）。"""
    return {"harris_index": None, "harris_warn": False}


def _assess_model_mismatch(
    pv_values: np.ndarray,
    sp_values: np.ndarray | None = None,
    threshold: dict | None = None,
) -> dict[str, Any]:
    """Harris 指数模型失配评估（B3）。

    以跟踪偏差 e = PV − SP（去均值）为对象，用 Yule-Walker 方程估计 AR(p)
    模型，取其一步预测残差方差作为最小方差基准 σ²_mv 的 lag-1 近似：
    harris_index = var(e) / σ²_mv（≥ 1，越大表示回路性能离最小方差基准越远）。
    注意：无过程延迟信息时该 lag-1 近似偏保守，指数系统性偏高。
    本评估不单独产出标签，结果仅供前端可视化与
    OVERAGGRESSIVE/OVERCONSERVATIVE 命中时的证据增强。

    Args:
        pv_values: PV 数据数组（工程单位）
        sp_values: SP 数据数组（需与 pv_values 等长；缺失或不等长时返回空结果）
        threshold: 阈值配置（可选），支持键：
            - harris_ar_order: AR 模型阶数 p（默认 10）
            - harris_warn: 告警阈值（默认 2.0，harris_index > 该值时 harris_warn=True）

    Returns:
        {harris_index: float | None, harris_warn: bool}
    """
    if threshold is None:
        threshold = {}
    ar_order = int(threshold.get("harris_ar_order", 10))
    warn_threshold = float(threshold.get("harris_warn", 2.0))

    n = len(pv_values)
    min_points = get_trigger_config().min_data_points
    if sp_values is None or len(sp_values) != n or n < max(min_points, 3 * ar_order):
        return _empty_harris_result()

    try:
        e = np.asarray(pv_values, dtype=float) - np.asarray(sp_values, dtype=float)
        e = e - np.mean(e)
        var_e = float(np.mean(e * e))
        if var_e <= 0.0:
            return _empty_harris_result()

        # 有偏自协方差序列（Yule-Walker 保证 Toeplitz 矩阵正定）
        gamma = np.array([np.dot(e[k:], e[: n - k]) / n for k in range(ar_order + 1)])
        idx = np.arange(ar_order)
        r_matrix = gamma[np.abs(idx[:, None] - idx[None, :])]
        ar_coeffs = np.linalg.solve(r_matrix, gamma[1:])
        # 一步预测残差方差（最小方差基准的 lag-1 近似）
        sigma2_mv = float(gamma[0] - ar_coeffs @ gamma[1:])
        if sigma2_mv <= 0.0:
            return _empty_harris_result()

        harris_index = var_e / sigma2_mv
        return {
            "harris_index": float(harris_index),
            "harris_warn": bool(harris_index > warn_threshold),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Harris 指数评估失败: %s", exc)
        return _empty_harris_result()


#: 自控模式英文标签集合（与 constants.mode.AUTO_MODES 数值集合对应，
#: 兼容历史数据/外部输入中的字符串形式 MODE）
_AUTO_MODE_LABELS: frozenset[str] = frozenset(MODE_LABELS_EN[m] for m in AUTO_MODES)


def _is_auto_mode(mode_val: Any) -> bool:
    """判定 MODE 值是否为自控模式（AUTO/CAS/REMOTE/APC）。

    P0 修复：TDengine 中 MODE 为数值编码（StandardMode：AUTO=1/CAS=2/REMOTE=3/APC=4），
    原实现 `"AUTO" in str(mode_val)` 对数值 mode 恒为 False，饱和诊断在数值
    mode 下永久失效。数值按 constants.mode.AUTO_MODES 集合判定（含 APC=4）；
    字符串按英文标签判定，数值字符串（如 "1"）按数值解析。
    """
    if isinstance(mode_val, bool):
        return False
    if isinstance(mode_val, (int, np.integer)):
        return int(mode_val) in AUTO_MODES
    if isinstance(mode_val, (float, np.floating)):
        return float(mode_val).is_integer() and int(mode_val) in AUTO_MODES
    mode_str = str(mode_val).strip().upper()
    if mode_str in _AUTO_MODE_LABELS:
        return True
    try:
        num = float(mode_str)
    except (TypeError, ValueError):
        return False
    return num.is_integer() and int(num) in AUTO_MODES


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

    # 若提供 mode_values，仅保留自控模式数据点（AUTO/CAS/REMOTE/APC）
    if mode_values is not None and len(mode_values) > 0:
        min_len = min(len(op_values), len(mode_values))
        auto_mask = np.array(
            [_is_auto_mode(mode_values[i]) for i in range(min_len)],
            dtype=bool,
        )
        op_arr = np.asarray(op_values[:min_len], dtype=float)[auto_mask]
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


def _detect_choudhury_nonlinearity(
    pv: np.ndarray,
    op: np.ndarray,
    threshold: dict | None = None,
) -> dict[str, Any]:
    """Choudhury NGI/NLI 非线性检测（阀门粘滞高级检测）。

    设计依据：FDS §5.4.6 / ADS §5.2.2

    基于 OP 信号的非高斯性（NGI）和非线性（NLI）指标检测阀门粘滞：
    - NGI = |Kurtosis(x) - 3| / 6 + Skewness(x)² / 24
    - NLI 通过最大双相干性近似（二次相位耦合指标）
    - 当 NGI > ngi_threshold 且 NLI > nli_threshold 时判定存在非线性（粘滞）

    Args:
        pv: PV 数据数组
        op: OP 数据数组
        threshold: 阈值配置，支持键：
            - choudhury_ngi_threshold: NGI 判定阈值（默认 0.001，ADS §5.2.2）
            - choudhury_nli_threshold: NLI 判定阈值（默认 0.01，ADS §5.2.2）

    Returns:
        {detected, confidence, ngi, nli, stiction_index, fitting_score}
    """
    if not isinstance(threshold, dict):
        threshold = {}
    ngi_threshold = float(threshold.get("choudhury_ngi_threshold", 0.001))
    nli_threshold = float(threshold.get("choudhury_nli_threshold", 0.01))

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

        # 判定规则（ADS §5.2.2: NGI > 0.001 且 NLI > 0.01，阈值可配置）
        detected = bool(ngi > ngi_threshold and nli > nli_threshold)

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
        # 非零方向符号在 op_diff 中的原始索引（P1 修复：原先 signs[signs != 0]
        # 压缩序列后丢失索引映射，分段边界被错误用于切原 op_arr）
        nz_idx = np.flatnonzero(signs != 0)
        if len(nz_idx) < 2:
            return _empty_kano_result()

        # 方向变化点（压缩序列索引）
        sign_changes = np.flatnonzero(np.diff(signs[nz_idx]) != 0)
        # 分段边界（压缩序列索引）
        boundaries = np.concatenate([[-1], sign_changes, [len(nz_idx) - 1]])

        total_segments = len(boundaries) - 1
        if total_segments == 0:
            return _empty_kano_result()

        # 统计粘滞区间：OP 变化小但 PV 变化大
        stiction_segments = 0
        op_range = float(np.max(op_arr) - np.min(op_arr)) + 1e-9
        pv_range = float(np.max(pv_arr) - np.min(pv_arr)) + 1e-9

        for i in range(total_segments):
            # 压缩边界经 nz_idx 映射回原数组：分段覆盖的差分索引为
            # nz_idx[boundaries[i]+1 .. boundaries[i+1]]，对应原数组切片
            # [首差分索引 : 末差分索引 + 2)（差分 j 覆盖 op_arr[j] 与 op_arr[j+1]，
            # 切片同时包含相邻非零差分之间的零差分平台点）
            start_idx = int(nz_idx[boundaries[i] + 1])
            end_idx = int(nz_idx[boundaries[i + 1]]) + 2
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
    threshold: dict | None = None,
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
        threshold: 阈值配置，支持键：
            - step_overshoot_threshold: 过冲阈值（默认 0.25，即 25%）
            - step_decay_ratio_threshold: 衰减比阈值（默认 0.4）
            - step_sse_threshold: 稳态误差阈值（默认 0.05，即 5% SP 量程）

    Returns:
        {detected, confidence, overshoot, decay_ratio, steady_state_error, step_count}
    """
    if not isinstance(threshold, dict):
        threshold = {}
    overshoot_threshold = float(threshold.get("step_overshoot_threshold", 0.25))
    decay_ratio_threshold = float(threshold.get("step_decay_ratio_threshold", 0.4))
    sse_threshold = float(threshold.get("step_sse_threshold", 0.05))

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

        # 判定规则（ADS §5.3.2: 满足 2 项及以上，阈值均可配置）
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
    loop_type: str | None = None,
    ts: np.ndarray | list[float] | None = None,
    *,
    sample_interval: float = 1.0,
    threshold: dict | None = None,
) -> dict[str, Any]:
    """响应迟缓检测（Slow Response Detection）。

    设计依据：FDS §5.4.6 / ADS §5.4.2

    基于 PV 对 SP 变化的响应延迟：
    - 检测 SP 阶跃变化
    - 对 PV 响应拟合一阶滞后模型 PV(t) = K(1 - exp(-t/τ))
    - 计算响应时间常数 τ（真实秒单位）
    - 与期望响应时间（按回路类型的工业经验秒数）比较

    P2 修复（无量纲 τ 漂移）：旧实现先把响应窗口时间归一化到 t∈[0,1]
    再拟合，τ 是"窗口占比"而非物理量——同一物理响应在不同窗口长度下
    τ 与判定结论都会漂移。现改为在真实秒时间轴上拟合，
    期望 τ 取回路类型经验秒数（FLOW/PRESSURE/LEVEL/TEMPERATURE/ANALYSIS），
    结论与窗口长度无关。

    Args:
        pv: PV 数据数组
        sp: SP 数据数组
        loop_type: 回路类型（FLOW/PRESSURE/LEVEL/TEMPERATURE/ANALYSIS），
            决定期望时间常数；缺省按 OTHER 处理
        ts: 时间戳数组（秒）；缺省时按 sample_interval 等间隔假设
        sample_interval: 采样间隔（秒），仅在 ts 缺省时使用
        threshold: 阈值配置，支持键：
            - slow_response_ratio_threshold: 迟缓判定比值（默认 2.0）
            - slow_no_step_bias_ratio: 无阶跃场景稳态偏差占比阈值（默认 0.2）
            - slow_expected_tau_seconds: 期望时间常数表（秒，按回路类型）

    Returns:
        {detected, confidence, time_constant, expected_time_constant, ratio}
    """
    if not isinstance(threshold, dict):
        threshold = {}
    ratio_threshold = float(threshold.get("slow_response_ratio_threshold", 2.0))
    no_step_bias_ratio = float(threshold.get("slow_no_step_bias_ratio", 0.2))
    tau_map = threshold.get("slow_expected_tau_seconds")

    min_len = min(len(pv), len(sp))
    if min_len < 16:
        return _empty_slow_response_result()

    try:
        pv_arr = pv[:min_len].astype(float)
        sp_arr = sp[:min_len].astype(float)

        # 时间轴（真实秒，禁止归一化——否则 τ 随窗口长度漂移）
        if ts is not None and len(ts) >= min_len:
            t_seconds = np.asarray(ts[:min_len], dtype=float)
            t_seconds = t_seconds - t_seconds[0]
            if t_seconds[-1] < 1e-9:
                interval = sample_interval if sample_interval > 0 else 1.0
                t_seconds = np.arange(min_len, dtype=float) * interval
        else:
            interval = sample_interval if sample_interval > 0 else 1.0
            t_seconds = np.arange(min_len, dtype=float) * interval

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
            # 偏差标准差超过 SP 量程的 no_step_bias_ratio（默认 20%）才判定
            ratio = bias_std / sp_range
            detected = bool(ratio > no_step_bias_ratio)
            expected_tau = _expected_time_constant(loop_type, tau_map)
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
        t_response = t_seconds[step_idx + 1 : response_end]
        if len(pv_response) < 8:
            return _empty_slow_response_result()

        # 一阶滞后拟合：PV(t) = old_sp + step_size * (1 - exp(-t/τ))
        # t 为阶跃时刻起算的真实秒数，τ 直接是物理秒
        t_fit = t_response - t_response[0]
        window_seconds = float(t_fit[-1]) if len(t_fit) > 0 else 0.0
        if window_seconds < 1e-9:
            return _empty_slow_response_result()

        # 使用 scipy 曲线拟合（初值/边界随窗口秒数自适应）
        from scipy.optimize import curve_fit

        def _first_order_lag(t: np.ndarray, tau: float) -> np.ndarray:
            return old_sp + step_size * (1.0 - np.exp(-t / max(tau, 1e-6)))

        try:
            popt, _ = curve_fit(
                _first_order_lag,
                t_fit,
                pv_response,
                p0=[max(window_seconds / 3.0, 1e-3)],
                bounds=([1e-3], [max(window_seconds * 10.0, 1.0)]),
                maxfev=1000,
            )
            time_constant = float(popt[0])
        except Exception:
            # 拟合失败：使用 63.2% 响应时间近似（真实秒）
            target = old_sp + step_size * 0.632
            if step_size > 0:
                reach_idx = np.where(pv_response >= target)[0]
            else:
                reach_idx = np.where(pv_response <= target)[0]
            if len(reach_idx) > 0:
                time_constant = float(t_fit[reach_idx[0]])
            else:
                time_constant = window_seconds

        # 期望时间常数（真实秒，按回路类型工业经验值）
        expected_tau = _expected_time_constant(loop_type, tau_map)

        # 响应迟缓判定：实际时间常数 > 期望值 × 比值阈值（默认慢 2 倍以上）
        ratio = time_constant / expected_tau if expected_tau > 0 else 0.0
        detected = bool(ratio > ratio_threshold)

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


def _expected_time_constant(
    loop_type: str | None,
    tau_map: dict | None = None,
) -> float:
    """按回路类型返回期望响应时间常数（真实秒）。

    工业经验值（与 _THRESHOLD_SCHEMA["OVERCONSERVATIVE"]["slow_expected_tau_seconds"]
    默认值一致，可用 threshold 配置覆盖）：
    - FLOW 流量：秒级响应，期望 τ ≈ 10s
    - PRESSURE 压力：数十秒级，期望 τ ≈ 30s
    - LEVEL 液位：分钟级，期望 τ ≈ 120s
    - TEMPERATURE 温度：十分钟级，期望 τ ≈ 600s
    - ANALYSIS 分析：刻钟级，期望 τ ≈ 900s
    - OTHER/缺省：期望 τ ≈ 60s

    Args:
        loop_type: 回路类型；None 或未知类型按 OTHER 处理
        tau_map: 配置覆盖的期望 τ 表；缺省用 schema 默认表
    """
    mapping = tau_map if isinstance(tau_map, dict) and tau_map else _DEFAULT_EXPECTED_TAU_SECONDS
    key = (loop_type or "OTHER").upper()
    try:
        return float(mapping.get(key, mapping.get("OTHER", 60.0)))
    except (TypeError, ValueError):
        return 60.0


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


async def _load_threshold_overrides(
    db,
) -> list[DiagnosisThresholdOverride]:
    """从 DB 预加载所有阈值覆盖（调用者层一次性加载，C3 差异化阈值）。

    查询失败时返回空列表（不阻塞诊断，回退到全局默认阈值）。
    """
    try:
        result = await db.execute(select(DiagnosisThresholdOverride))
        return list(result.scalars().all())
    except Exception as exc:  # noqa: BLE001
        logger.warning("预加载阈值覆盖失败，回退到全局默认: %s", exc)
        return []


def _merge_threshold_overrides(
    diag_configs: dict[str, DiagnosisConfig],
    overrides: list[DiagnosisThresholdOverride],
    loop: LoopLedger,
) -> dict[str, DiagnosisConfig]:
    """纯内存合并阈值覆盖（C3 差异化阈值，不查 DB）。

    按优先级（高→低）合并覆盖：回路级 → 装置级 → 回路类型模板 → 全局默认。
    返回合并后的 diag_configs 副本（不修改原字典）。
    """
    loop_id = str(loop.id)
    unit_id = str(loop.unit_id) if loop.unit_id else None
    loop_type = loop.loop_type or "OTHER"

    # 过滤出匹配此回路的覆盖
    scope_ids: dict[str, str] = {"loop": loop_id, "loop_type": loop_type}
    if unit_id:
        scope_ids["plant"] = unit_id

    matched = [
        ov
        for ov in overrides
        if ov.scope_type in scope_ids and scope_ids[ov.scope_type] == ov.scope_id
    ]
    if not matched:
        return diag_configs

    # 按优先级排序：loop > plant > loop_type
    priority_map = {"loop": 0, "plant": 1, "loop_type": 2}
    matched.sort(key=lambda o: priority_map.get(o.scope_type, 99))

    import copy

    merged: dict[str, DiagnosisConfig] = {k: copy.copy(v) for k, v in diag_configs.items()}

    for ov in matched:
        cfg = merged.get(ov.diag_code)
        if cfg is None:
            continue
        base_threshold = dict(cfg.threshold) if cfg.threshold else {}
        if ov.threshold:
            base_threshold.update(ov.threshold)
            new_cfg = copy.copy(cfg)
            new_cfg.threshold = base_threshold
            merged[ov.diag_code] = new_cfg

    return merged


def _get_threshold(
    diag_configs: dict[str, Any],
    diag_code: str,
    key: str | None,
    default: Any,
) -> Any:
    """从诊断配置表中读取阈值参数（P0-1 配置表与算法对齐）。

    整改计划 C1：增加键名 schema 校验与缺省告警日志。
    - diag_code 在 diag_configs 中不存在时告警（配置缺失，使用代码默认值）
    - key 在 threshold dict 中缺失时告警（使用默认值）
    - key 不在 _THRESHOLD_SCHEMA 登记表中时告警（未知键名，可能拼写错误）

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
        if diag_code in _THRESHOLD_SCHEMA:
            logger.warning(
                "诊断配置 %s 在数据库中不存在（is_enabled=True 未返回），使用代码默认值",
                diag_code,
            )
        return default
    threshold = getattr(config, "threshold", None)
    if threshold is None:
        if diag_code in _THRESHOLD_SCHEMA:
            logger.warning(
                "诊断配置 %s 的 threshold 为 NULL，使用代码默认值",
                diag_code,
            )
        return default
    if key is None:
        return threshold
    if key not in threshold:
        known = _THRESHOLD_SCHEMA.get(diag_code, {})
        if key in known:
            logger.warning(
                "阈值键 %s 在配置 %s 中缺失，使用默认值 %s",
                key,
                diag_code,
                default,
            )
        else:
            logger.warning(
                "阈值键 %s 未在 _THRESHOLD_SCHEMA 中登记（diag_code=%s），"
                "可能键名拼写错误，使用默认值 %s",
                key,
                diag_code,
                default,
            )
    return threshold.get(key, default)


def _validate_threshold_config(diag_configs: dict[str, Any]) -> None:
    """校验已加载的诊断配置与 _THRESHOLD_SCHEMA 的一致性（整改计划 C1）.

    在加载 diag_configs 后调用，一次性告警所有缺失的配置项与阈值键，
    便于管理员在配置页补齐。运行时 _get_threshold 仍会逐键告警兜底。
    """
    for diag_code, schema_keys in _THRESHOLD_SCHEMA.items():
        config = diag_configs.get(diag_code)
        if config is None:
            logger.warning(
                "诊断配置 %s 未启用或不存在（阈值将全部使用代码默认值）",
                diag_code,
            )
            continue
        threshold = getattr(config, "threshold", None)
        if threshold is None:
            logger.warning(
                "诊断配置 %s 的 threshold 为 NULL（阈值将全部使用代码默认值）",
                diag_code,
            )
            continue
        missing = [k for k in schema_keys if k not in threshold]
        if missing:
            logger.warning(
                "诊断配置 %s 缺失阈值键 %s（将使用代码默认值）",
                diag_code,
                missing,
            )


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


def _fuse_same_label_confidence(algorithm_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同标签多算法置信度融合（D-S 证据理论，FDS §5.4.7）。

    P1 修复融合口径：仅对同一标签的多算法结果在去重之前做 D-S 融合；
    不同标签代表互斥的故障假设，跨标签置信度不做融合。

    融合的标签记录：
    - confidence 更新为同标签多算法融合置信度
    - evidence 增加 same_label_fusion 标注，说明融合语义与来源置信度

    Args:
        algorithm_results: 算法结果列表（可含同标签多条记录）

    Returns:
        融合后的算法结果列表（单记录标签原样保留）
    """
    if not algorithm_results:
        return algorithm_results

    try:
        # 按 label 分组
        groups: dict[str, list[dict[str, Any]]] = {}
        for r in algorithm_results:
            groups.setdefault(r["label"], []).append(r)

        for records in groups.values():
            if len(records) < 2:
                continue
            source_confidences = [r["confidence"] for r in records]
            fused = _dempster_shafer_fusion([(r["label"], r["confidence"]) for r in records])
            for r in records:
                r["confidence"] = fused
                r.setdefault("evidence", {})["same_label_fusion"] = {
                    "semantic": "同标签多算法融合置信度",
                    "algorithm_count": len(records),
                    "source_confidences": source_confidences,
                }
        return algorithm_results
    except Exception as exc:  # noqa: BLE001
        logger.warning("同标签置信度融合失败: %s", exc)
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
    """IAE 零交叉相似率法振荡检测（FDS §5.4.6 在线主算法，与 KPI 侧同一算法）。

    P2 统一：旧实现是已被 KPI 侧移除的 CV 法（IAE 累积去趋势 +
    零交叉间隔 1-std/mean），与 KPI 振荡率（metric_calculator/oscillation.py，
    IAE 段相似率最小距离法，算法说明 §4.6）口径不一致，同一回路可能出现
    KPI 振荡率与诊断 OSCILLATION 标签互相矛盾。现直接复用
    OscillationRateCalculator 的零交叉识别、IAE 段计算与相似率静态方法，
    保证同一信号下两侧结论一致（FFT 路径保留作多算法证据，
    经 _fuse_same_label_confidence 同标签融合）。

    算法步骤（对齐算法说明 §4.6 / GB/T 44693.2-2024 附录 F.1）：
    1. 计算控制偏差 e(t) = PV - SP
    2. 识别偏差零交叉点（至少 2 个完整周期）
    3. 计算相邻零交叉间完整半周期的 IAE 与持续时间
    4. 分别对正值段/负值段 IAE 计算相似率 S_A/S_B（最小距离法）
    5. similarity = min(S_A, S_B)；S_A>=阈值 且 S_B>=阈值 判定振荡
    6. 置信度 = min(1.0, similarity * 1.5)

    Args:
        pv: PV 数据数组
        sp: SP 数据数组
        sample_interval: 采样间隔（秒），用于平均周期换算
        threshold: 阈值配置，支持键：
            - similarity_threshold: 相似率阈值（默认 0.4）
            - min_zero_crossings: 最小零交叉数（默认 4，与 KPI 侧一致）

    Returns:
        {detected, confidence, similarity, zero_crossing_count, mean_period}
    """
    if not isinstance(threshold, dict):
        threshold = {}
    similarity_threshold = float(threshold.get("similarity_threshold", 0.4))
    min_zero_crossings = int(threshold.get("min_zero_crossings", MIN_ZERO_CROSSINGS))

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
        # 1. 计算控制偏差
        error = pv[:min_len].astype(float) - sp[:min_len].astype(float)

        # 2. 识别零交叉点（复用 KPI 侧向量化实现，含零值平台前向填充）
        zero_crossings = OscillationRateCalculator._find_zero_crossings(error)
        n_crossings = len(zero_crossings)
        if n_crossings < max(min_zero_crossings, 2):
            return {
                "detected": False,
                "confidence": 0.0,
                "similarity": 0.0,
                "zero_crossing_count": n_crossings,
                "mean_period": 0.0,
            }

        # 3. 计算相邻零交叉间完整半周期的 IAE（首尾残缺半周期已剔除）
        segments = OscillationRateCalculator._compute_iae_segments(error, zero_crossings)
        pos_iae = [s[0] for s in segments if s[2] > 0]
        neg_iae = [s[0] for s in segments if s[2] < 0]
        if not pos_iae or not neg_iae:
            return {
                "detected": False,
                "confidence": 0.0,
                "similarity": 0.0,
                "zero_crossing_count": n_crossings,
                "mean_period": 0.0,
            }

        # 4. IAE 相似率 S_A/S_B（最小距离法，min/max_ratio 与 KPI 侧默认值一致）
        s_a = OscillationRateCalculator._similarity_rate(
            pos_iae, _DEFAULT_MIN_RATIO, _DEFAULT_MAX_RATIO
        )
        s_b = OscillationRateCalculator._similarity_rate(
            neg_iae, _DEFAULT_MIN_RATIO, _DEFAULT_MAX_RATIO
        )
        similarity = min(s_a, s_b)

        # 5. 振荡判定：双侧相似率均达阈值（与 KPI 侧 is_oscillating 同口径）
        detected = bool(s_a >= similarity_threshold and s_b >= similarity_threshold)

        # 6. 置信度
        confidence = min(1.0, similarity * 1.5) if detected else 0.0

        # 平均周期 = 2 × 平均半周期（秒）
        durations = [s[1] for s in segments]
        mean_period_samples = float(np.mean(durations)) * 2.0 if durations else 0.0
        mean_period = (
            mean_period_samples * sample_interval if sample_interval > 0 else mean_period_samples
        )

        return {
            "detected": detected,
            "confidence": confidence,
            "similarity": similarity,
            "zero_crossing_count": n_crossings,
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

    P1 修复口径：本函数仅用于同一标签的多算法结果融合（由
    _fuse_same_label_confidence 调用），不得跨标签调用——不同标签代表
    互斥的故障假设，置信度做赔率乘积没有证据理论意义。

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


def _resolve_pv_range(
    mappings: dict[str, LoopTagMapping],
    tags_map: dict[str, TagRegistry],
) -> tuple[float, float]:
    """解析 PV Tag 量程（B4 异常检测用），缺省或非法时回退 0.0~100.0。"""
    mapping = mappings.get("PV")
    tag = tags_map.get(str(mapping.tag_id)) if mapping else None
    range_min = getattr(tag, "range_min", None)
    range_max = getattr(tag, "range_max", None)
    min_v = float(range_min) if isinstance(range_min, (int, float)) else 0.0
    max_v = float(range_max) if isinstance(range_max, (int, float)) else 100.0
    if max_v <= min_v:
        return 0.0, 100.0
    return min_v, max_v


def _apply_outlier_preprocessing(
    aligned: list[dict[str, Any]],
    src_indices: list[int],
    raw_series: RawTimeSeries,
    loop: LoopLedger,
    mappings: dict[str, LoopTagMapping],
    tags_map: dict[str, TagRegistry],
) -> tuple[list[dict[str, Any]], float]:
    """B4 轻量数据质量预处理：异常点剔除 + 质量摘要。

    复用预处理 Pipeline 的 OutlierDetector 对 PV（及 OP）执行异常值检测，
    按 should_invalidate 规则剔除 SPIKE/JUMP/OUT_OF_RANGE/NAN 点
    （pv/sp/op/ts 同步剔除保持对齐；TS_ANOMALY/HF_NOISE 仅标记不剔除；
    冻结检测跳过——传感器卡死由 _detect_sensor_faults 作为诊断标签输出，
    稳态恒值是正常工况，不作为数据质量异常剔除）。
    全程在原始工程值上进行，不改量纲/归一化。剔除比例 >50% 时记日志并继续诊断。

    Args:
        aligned: 质量码过滤后的对齐数据（ts/pv/sp/op/mode）
        src_indices: aligned 各行对应的原始时序索引（raw 级有效性标记用）
        raw_series: 宽表查询原始时序
        loop: 回路台账（loop_type 决定检测阈值表）
        mappings: 回路 Tag 角色映射
        tags_map: Tag 注册表（取 PV 量程）

    Returns:
        (剔除异常点后的 aligned, valid_rate 有效数据率 0~1)
    """
    n_raw = len(raw_series.timestamps)
    try:
        loop_type = loop.loop_type if isinstance(loop.loop_type, str) else ""
        control_type = _LOOP_TYPE_TO_CONTROL_TYPE.get(loop_type.upper(), ControlType.FLOW)
        detector = OutlierDetector(get_outlier_threshold(control_type))
        range_min, range_max = _resolve_pv_range(mappings, tags_map)

        ts_list = [d["ts"] for d in aligned]
        pv_list = [d["pv"] for d in aligned]
        pv_reasons = detector.detect_all(
            tag_name="pv",
            values=pv_list,
            timestamps=ts_list,
            range_min=range_min,
            range_max=range_max,
            quality_codes=None,
            is_normalized=False,
            skip_frozen=True,
        )
        invalid_idx = {
            i for i, reasons in pv_reasons.items() if OutlierDetector.should_invalidate(reasons)
        }

        # OP 同步检测（接口支持多信号）：OP 缺失（None）点会被 detect_nan 标记，
        # 缺失不等于数据质量异常，剔除时跳过
        op_list = [d.get("op") for d in aligned]
        if any(v is not None for v in op_list):
            op_reasons = detector.detect_all(
                tag_name="op",
                values=op_list,
                timestamps=ts_list,
                range_min=range_min,
                range_max=range_max,
                quality_codes=None,
                is_normalized=False,
                skip_frozen=True,
            )
            for i, reasons in op_reasons.items():
                if op_list[i] is None:
                    continue
                if OutlierDetector.should_invalidate(reasons):
                    invalid_idx.add(i)

        removed = len(invalid_idx)
        if removed:
            ratio = removed / len(aligned)
            if ratio > 0.5:
                logger.warning(
                    "回路 %s 异常点剔除比例过高（%d/%d，%.1f%%），记日志并继续诊断",
                    loop.tag_name,
                    removed,
                    len(aligned),
                    ratio * 100,
                )
            else:
                logger.info(
                    "回路 %s 异常点剔除 %d/%d（%.1f%%）",
                    loop.tag_name,
                    removed,
                    len(aligned),
                    ratio * 100,
                )

        # raw 级有效性：质量码 Bad/PV 缺失（对齐段已剔除）+ 本次异常点剔除
        kept_src = set(src_indices)
        pv_valid = [i in kept_src for i in range(n_raw)]
        for i in invalid_idx:
            pv_valid[src_indices[i]] = False

        summary = compute_quality_summary(
            validity={"pv_valid": pv_valid},
            timestamps=list(raw_series.timestamps),
            point_count=n_raw,
            quality_codes=None,
            expected_interval_s=_compute_sample_interval(aligned),
        )
        filtered = [d for i, d in enumerate(aligned) if i not in invalid_idx]
        return filtered, summary.valid_rate
    except Exception as exc:  # noqa: BLE001
        # 预处理失败不中断诊断：按未剔除数据继续，valid_rate 按对齐存活率兜底
        logger.warning("回路 %s B4 异常点预处理失败，按未剔除继续: %s", loop.tag_name, exc)
        fallback_rate = len(aligned) / n_raw if n_raw else 0.0
        return aligned, round(fallback_rate, 4)


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


def _ts_list_to_seconds(ts_list: list[Any]) -> np.ndarray:
    """批量将时间戳序列转换为浮点秒数组（热路径向量化）。

    项目红线：禁止对 naive datetime 逐点调 `.timestamp()`（macOS fork
    时区慢路径）。本函数一次性将 datetime / ISO 字符串序列转为
    numpy datetime64 再转浮点秒；naive datetime 按 datetime64 默认的
    UTC 基准解释，仅用于计算相对时间间隔，语义与原实现一致。

    支持 int/float、datetime 对象、ISO 8601 字符串及其混合；
    无法解析的点返回 NaN，由调用方过滤。

    Args:
        ts_list: 时间戳列表（int/float/datetime/str）

    Returns:
        浮点秒数组（dtype=float），长度与输入一致
    """
    if not ts_list:
        return np.array([], dtype=float)

    # 全数值：直接转换（视为绝对秒）
    if all(isinstance(t, (int, float)) and not isinstance(t, bool) for t in ts_list):
        return np.asarray(ts_list, dtype=float)

    # datetime / ISO 字符串：一次性向量化转换
    try:
        with warnings.catch_warnings():
            # numpy datetime64 无显式时区表示，aware 输入按 UTC 换算（告警可忽略）
            warnings.simplefilter("ignore", UserWarning)
            dt_arr = np.asarray(ts_list, dtype="datetime64[us]")
        return dt_arr.astype("int64").astype(float) / 1e6
    except (TypeError, ValueError):
        pass

    # 混合类型或含无法解析元素：逐点解析（非热路径，仅兜底）
    values = np.full(len(ts_list), np.nan, dtype=float)
    base: datetime | None = None
    for i, ts in enumerate(ts_list):
        if isinstance(ts, bool):
            continue
        if isinstance(ts, (int, float)):
            values[i] = float(ts)
            continue
        dt: datetime | None = None
        if isinstance(ts, datetime):
            dt = ts
        else:
            try:
                dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            except (ValueError, TypeError):
                continue
        if base is None:
            base = dt
            values[i] = 0.0
            continue
        try:
            values[i] = (dt - base).total_seconds()
        except TypeError:
            # naive 与 aware 混排无法相减，该点记为无效
            continue
    return values


def _compute_sample_interval(aligned: list[dict[str, Any]]) -> float:
    """从对齐后的时序数据计算平均采样间隔（秒）。

    热路径向量化：时间戳经 _ts_list_to_seconds 一次性批量换算，
    差分用 numpy 向量化计算；禁止逐点 naive datetime .timestamp()。

    Args:
        aligned: 对齐后的数据列表，每个元素含 "ts" 字段

    Returns:
        平均采样间隔（秒），默认 1.0
    """
    raw_ts = [d.get("ts") for d in aligned if d.get("ts") is not None]
    if len(raw_ts) < 2:
        return 1.0

    ts_values = _ts_list_to_seconds(raw_ts)
    ts_values = ts_values[~np.isnan(ts_values)]
    if len(ts_values) < 2:
        return 1.0

    diffs = np.diff(ts_values)
    positive = diffs[diffs > 0]
    if len(positive) == 0:
        return 1.0
    return float(np.mean(positive))


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
    "_fuse_same_label_confidence",
    "_get_threshold",
    "_is_auto_mode",
    "_THRESHOLD_SCHEMA",
    "_ts_list_to_seconds",
    "_validate_threshold_config",
    "run_diagnosis_hourly",
    "run_loop_diagnosis",
]
