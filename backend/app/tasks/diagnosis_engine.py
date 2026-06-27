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
from sqlalchemy import delete, select

from app.models.diagnosis import DiagnosisConfig, DiagnosisResult
from app.models.loop import LoopLedger, LoopTagMapping
from app.models.metric import KpiSnapshotHourly
from app.models.tag import TagRegistry
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
    name="app.tasks.diagnosis_engine.run_loop_diagnosis",
    base=AsyncTask,
    autoretry_for=(Exception,),
    retry_kwargs={"max_retries": 3, "countdown": 60},
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
)
def run_loop_diagnosis(loop_id: str, ts_start: str | None = None) -> dict:
    """单回路诊断（可手动触发）。"""
    logger.info("单回路诊断, loop_id=%s", loop_id)
    return AsyncTask().run_async(_do_diagnose_single_loop(loop_id, ts_start))


# ---------------------------------------------------------------------------
# Beat 调度配置
# ---------------------------------------------------------------------------


_beat_entry = {
    "task": "app.tasks.diagnosis_engine.run_diagnosis_hourly",
    "schedule": 3600.0,  # 1 小时
}

_existing_beat = getattr(celery_app.conf, "beat_schedule", None) or {}
_existing_beat["diagnosis-engine-hourly"] = _beat_entry
celery_app.conf.beat_schedule = _existing_beat
celery_app.conf.timezone = "Asia/Shanghai"


# ---------------------------------------------------------------------------
# 异步诊断逻辑
# ---------------------------------------------------------------------------


async def _do_run_diagnosis() -> dict:
    """执行全量诊断的实际 async 逻辑。"""
    from app.core.db import AsyncSessionLocal
    from app.core.tdengine import query_trend_data

    now = datetime.now(UTC)
    ts_end = now.replace(minute=0, second=0, microsecond=0)
    ts_start = ts_end - timedelta(hours=1)

    # 主 session 仅用于查询待诊断回路列表和诊断配置（只读，无并发）
    async with AsyncSessionLocal() as db:
        # 1. 查询最近一小时评分跌破阈值的回路
        snapshot_stmt = (
            select(KpiSnapshotHourly)
            .where(KpiSnapshotHourly.ts_start >= ts_start)
            .where(KpiSnapshotHourly.ts_start <= ts_end)
            .where(KpiSnapshotHourly.score < SCORE_THRESHOLD)
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

    # 3. 并发诊断（信号量限制并发数，每协程独立 session 避免并发共享）
    sem = asyncio.Semaphore(CONCURRENCY)

    async def _diag_with_sem(loop_id: str) -> dict | None:
        async with sem:
            # 每协程独立 session，避免 AsyncSession 并发共享导致的不可预期错误
            async with AsyncSessionLocal() as worker_db:
                try:
                    result = await _diagnose_loop(
                        db=worker_db,
                        loop_id=loop_id,
                        diag_configs=diag_configs,
                        ts_start=ts_start,
                        ts_end=ts_end,
                        query_trend_fn=query_trend_data,
                    )
                    await worker_db.commit()
                    return result
                except Exception:
                    await worker_db.rollback()
                    raise

    tasks = [asyncio.create_task(_diag_with_sem(lid)) for lid in loop_ids]
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

    return {
        "total": len(loop_ids),
        "diagnosed": diagnosed_count,
        "failed": failed_count,
        "ts_start": ts_start.isoformat(),
        "ts_end": ts_end.isoformat(),
    }


async def _do_diagnose_single_loop(loop_id: str, ts_start: str | None = None) -> dict:
    """单回路诊断。"""
    from app.core.db import AsyncSessionLocal
    from app.core.tdengine import query_trend_data

    async with AsyncSessionLocal() as db:
        # 加载诊断配置
        config_result = await db.execute(
            select(DiagnosisConfig).where(DiagnosisConfig.is_enabled.is_(True))
        )
        diag_configs = {c.diag_code: c for c in config_result.scalars().all()}

        now = datetime.now(UTC)
        if ts_start:
            try:
                ts_start_dt = datetime.fromisoformat(ts_start.replace("Z", "+00:00"))
            except ValueError:
                ts_start_dt = datetime.fromisoformat(ts_start)
        else:
            ts_start_dt = (now - timedelta(hours=1)).replace(
                minute=0, second=0, microsecond=0
            )
        ts_end_dt = ts_start_dt + timedelta(hours=1)

        result = await _diagnose_loop(
            db=db,
            loop_id=loop_id,
            diag_configs=diag_configs,
            ts_start=ts_start_dt,
            ts_end=ts_end_dt,
            query_trend_fn=query_trend_data,
        )
        await db.commit()
        return result or {"loopId": loop_id, "status": "FAILED"}


async def _diagnose_loop(
    db,
    loop_id: str,
    diag_configs: dict[str, DiagnosisConfig],
    ts_start: datetime,
    ts_end: datetime,
    query_trend_fn,
) -> dict | None:
    """对单回路执行诊断。

    Args:
        db: 异步数据库会话
        loop_id: 回路 ID
        diag_configs: 诊断配置字典
        ts_start: 时间窗起始
        ts_end: 时间窗结束
        query_trend_fn: TDengine 查询函数

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
    m_result = await db.execute(
        select(LoopTagMapping).where(LoopTagMapping.loop_id == loop_id)
    )
    mappings = {m.tag_role: m for m in m_result.scalars().all()}

    tag_ids = [str(m.tag_id) for m in mappings.values()]
    tags_map: dict[str, TagRegistry] = {}
    if tag_ids:
        t_result = await db.execute(select(TagRegistry).where(TagRegistry.id.in_(tag_ids)))
        for t in t_result.scalars().all():
            tags_map[str(t.id)] = t

    pv_tag_name = _get_tag_name(mappings, tags_map, "PV")
    sp_tag_name = _get_tag_name(mappings, tags_map, "SP")
    op_tag_name = _get_tag_name(mappings, tags_map, "OP")
    mode_tag_name = _get_tag_name(mappings, tags_map, "MODE")

    if not pv_tag_name:
        logger.warning("回路 %s 缺少 PV Tag", loop.tag_name)
        return None

    # 从 TDengine 拉取数据
    # ts_start/ts_end 已在函数入口转换为 naive datetime，
    # isoformat() 不会带时区后缀，符合 TDengine REST API 要求
    start_iso = ts_start.isoformat()
    end_iso = ts_end.isoformat()

    try:
        pv_data = await query_trend_fn(pv_tag_name, start_iso, end_iso)
        sp_data = await query_trend_fn(sp_tag_name, start_iso, end_iso) if sp_tag_name else []
        op_data = await query_trend_fn(op_tag_name, start_iso, end_iso) if op_tag_name else []
        mode_data = await query_trend_fn(mode_tag_name, start_iso, end_iso) if mode_tag_name else []
    except Exception as exc:  # noqa: BLE001
        logger.warning("TDengine 查询失败（回路 %s 跳过）: %s", loop.tag_name, exc)
        return None

    # 数据不足判定
    if len(pv_data) < MIN_DATA_POINTS:
        logger.info("回路 %s 数据点不足 (%d < %d)", loop.tag_name, len(pv_data), MIN_DATA_POINTS)
        return None

    # 剔除 PV 质量码为 Bad 的数据点
    pv_data_filtered = [d for d in pv_data if str(d.get("quality", "GOOD")).upper() != "BAD"]

    # 按 ts 对齐
    aligned = _align_timeseries(pv_data_filtered, sp_data, op_data, mode_data)
    if len(aligned) < MIN_DATA_POINTS:
        logger.info("回路 %s 对齐后数据点不足", loop.tag_name)
        return None

    # 执行各算法
    pv_values = np.array([d["pv"] for d in aligned if d.get("pv") is not None], dtype=float)
    sp_values = np.array([d["sp"] for d in aligned if d.get("sp") is not None], dtype=float)
    op_values = np.array([d["op"] for d in aligned if d.get("op") is not None], dtype=float)

    # 计算采样间隔（秒），用于 FFT 频率换算
    sample_interval = _compute_sample_interval(aligned)

    # 1. FFT 频域分析（振荡检测）
    osc_result = _detect_oscillation_fft(pv_values, sample_interval)

    # 2. PV-OP 散点拟合（阀门粘滞检测）
    stiction_result = _detect_valve_stiction(pv_values, op_values)

    # 3. PID 增益分析（参数过激/过保守）
    pid_result = _analyze_pid_params(pv_values, sp_values)

    # 4. 外扰频繁检测
    disturbance_result = _detect_external_disturbance(pv_values, sample_interval)

    # 5. PV 质量码统计
    quality_result = _analyze_quality(pv_data)

    # 6. OP 饱和率分析
    saturation_result = _analyze_saturation(op_values)

    # 7. Choudhury NGI/NLI 非线性检测（阀门粘滞高级检测，设计依据：FDS §5.4.6）
    choudhury_result = _detect_choudhury_nonlinearity(pv_values, op_values)

    # 8. Kano 统计法粘滞检测（与 Choudhury 互为交叉验证）
    kano_result = _detect_kano_stiction(pv_values, op_values)

    # 提取时间戳数组（供阶跃响应/响应迟缓/偏差突变算法使用）
    ts_values = np.array(
        [_ts_to_float(d.get("ts")) for d in aligned if d.get("ts") is not None],
        dtype=float,
    )
    # 若时间戳数量与 PV 不一致，回退为 None（使用等间隔假设）
    ts_param = ts_values if len(ts_values) == len(pv_values) else None

    # 9. 完整阶跃响应分析（过冲/衰减比/稳态误差）
    step_response_result = _analyze_step_response(pv_values, sp_values, op_values, ts_param)

    # 10. 响应迟缓检测（一阶滞后拟合）
    # 控制类型从回路扩展属性获取（默认 PI）
    control_type = getattr(loop, "control_type", None) or "PI"
    slow_response_result = _detect_slow_response(
        pv_values, sp_values, control_type, ts_param
    )

    # 11. 偏差突变检测（CUSUM）
    bias_shift_result = _detect_bias_shift(pv_values, sp_values, ts_param)

    # 收集所有算法结果（带置信度）
    algorithm_results: list[dict[str, Any]] = []

    if osc_result["detected"]:
        algorithm_results.append(
            {
                "label": "OSCILLATION",
                "confidence": osc_result["confidence"],
                "feature_values": {
                    "oscillation_amplitude": osc_result["amplitude"],
                    "oscillation_frequency": osc_result["frequency"],
                    "oscillation_index": osc_result["index"],
                },
                "evidence": {
                    "reasoning": (
                        f"FFT 频域分析检测到主频 {osc_result['frequency']:.3f} Hz，"
                        f"振幅 {osc_result['amplitude']:.3f}，振荡指数 {osc_result['index']:.3f}"
                    ),
                },
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
                },
            }
        )

    if pid_result["overaggressive"]:
        algorithm_results.append(
            {
                "label": "OVERAGGRESSIVE",
                "confidence": pid_result["confidence"],
                "feature_values": {
                    "overshoot": pid_result["overshoot"],
                    "settling_time": pid_result["settling_time"],
                },
                "evidence": {
                    "reasoning": (
                        f"PID 增益分析显示过冲 {pid_result['overshoot']:.3f}，"
                        f"稳定时间 {pid_result['settling_time']:.3f}s，参数过激"
                    ),
                },
            }
        )

    if pid_result["overconservative"]:
        algorithm_results.append(
            {
                "label": "OVERCONSERVATIVE",
                "confidence": pid_result["confidence"],
                "feature_values": {
                    "response_time": pid_result["response_time"],
                    "steady_state_error": pid_result["steady_state_error"],
                },
                "evidence": {
                    "reasoning": (
                        f"PID 增益分析显示响应时间 {pid_result['response_time']:.3f}s，"
                        f"稳态误差 {pid_result['steady_state_error']:.3f}，参数过保守"
                    ),
                },
            }
        )

    if disturbance_result["detected"]:
        algorithm_results.append(
            {
                "label": "EXTERNAL_DISTURBANCE",
                "confidence": disturbance_result["confidence"],
                "feature_values": {
                    "disturbance_frequency": disturbance_result["frequency"],
                    "disturbance_amplitude": disturbance_result["amplitude"],
                },
                "evidence": {
                    "reasoning": (
                        f"频谱分析检测到外扰频率 {disturbance_result['frequency']:.3f} Hz，"
                        f"幅值 {disturbance_result['amplitude']:.3f}"
                    ),
                },
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
                },
                "evidence": {
                    "reasoning": (
                        f"PV 质量码统计：Bad 占比 {quality_result['bad_rate']:.3f}，"
                        f"总点数 {quality_result['total']}，Bad 点数 {quality_result['bad_count']}"
                    ),
                },
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

    # 使用 Dempster-Shafer 证据理论融合置信度
    fused_confidence = _dempster_shafer_fusion(
        [(r["label"], r["confidence"]) for r in algorithm_results]
    )

    # 幂等性（S1-C3）：删除该回路在当前时间窗内的旧诊断记录，避免重复写入
    await db.execute(
        delete(DiagnosisResult).where(
            DiagnosisResult.loop_id == loop_id,
            DiagnosisResult.diagnosed_at >= ts_start,
            DiagnosisResult.diagnosed_at <= ts_end + timedelta(hours=1),
        )
    )

    # 写入诊断结果（每个标签一条记录）
    diagnosed_at = datetime.now(UTC).replace(tzinfo=None)
    for result in algorithm_results:
        confidence_decimal = Decimal(str(round(result["confidence"] * 100, 2)))
        evidence_chain = {
            **result["evidence"],
            "fused_confidence": fused_confidence,
        }
        diag_record = DiagnosisResult(
            id=str(uuid4()),
            loop_id=loop_id,
            diag_label=result["label"],
            confidence=confidence_decimal,
            feature_values=result.get("feature_values"),
            evidence_chain=evidence_chain,
            algorithm_version=DIAG_ALGORITHM_VERSION,
            diagnosed_at=diagnosed_at,
        )
        db.add(diag_record)

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
    }


def _empty_stiction_result() -> dict[str, Any]:
    """空粘滞检测结果。"""
    return {
        "detected": False,
        "confidence": 0.0,
        "stiction_index": 0.0,
        "fitting_score": 0.0,
    }


def _detect_oscillation_fft(
    pv_values: np.ndarray, sample_interval: float = 1.0
) -> dict[str, Any]:
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

        return {
            "detected": detected,
            "confidence": confidence,
            "amplitude": amplitude,
            "frequency": frequency,
            "index": osc_index,
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


def _analyze_pid_params(pv_values: np.ndarray, sp_values: np.ndarray) -> dict[str, Any]:
    """PID 增益分析（参数过激/过保守）。

    过冲检测：仅在 SP 阶跃后计算真正过冲（PV 超过新 SP 的幅度），
    稳态数据不误报过冲。

    Returns:
        {overaggressive, overconservative, confidence, overshoot, settling_time,
         response_time, steady_state_error}
    """
    min_len = min(len(pv_values), len(sp_values))
    if min_len < 8:
        return {
            "overaggressive": False,
            "overconservative": False,
            "confidence": 0.0,
            "overshoot": 0.0,
            "settling_time": 0.0,
            "response_time": 0.0,
            "steady_state_error": 0.0,
        }

    try:
        pv = pv_values[:min_len]
        sp = sp_values[:min_len]
        error = pv - sp

        # SP 量程
        sp_range = float(np.max(sp) - np.min(sp)) or 1.0

        # 检测 SP 阶跃点（SP 变化超过 SP 量程的 5%）
        sp_diff = np.abs(np.diff(sp))
        step_threshold = sp_range * 0.05
        step_indices = np.where(sp_diff > step_threshold)[0]

        # 计算过冲：仅在 SP 阶跃后计算
        overshoot = 0.0
        if len(step_indices) > 0:
            for step_idx in step_indices:
                step_size = sp[step_idx + 1] - sp[step_idx]
                if abs(step_size) < 1e-9:
                    continue
                new_sp = sp[step_idx + 1]
                # 在阶跃后的窗口内寻找 PV 峰值
                window_end = min(step_idx + 1 + min_len // 4, min_len)
                pv_window = pv[step_idx + 1 : window_end]
                if len(pv_window) == 0:
                    continue
                if step_size > 0:
                    # 上升阶跃：过冲 = (PV_peak - new_SP) / step_size
                    pv_peak = float(np.max(pv_window))
                    if pv_peak > new_sp:
                        overshoot = max(overshoot, (pv_peak - new_sp) / step_size)
                else:
                    # 下降阶跃：过冲 = (new_SP - PV_trough) / |step_size|
                    pv_trough = float(np.min(pv_window))
                    if pv_trough < new_sp:
                        overshoot = max(overshoot, (new_sp - pv_trough) / abs(step_size))
        else:
            # 无 SP 阶跃：稳态数据，无过冲
            overshoot = 0.0

        # 稳定时间：误差收敛到 5% SP 范围内的时间
        threshold = sp_range * 0.05
        settling_idx = min_len
        for i in range(min_len - 1, -1, -1):
            if abs(error[i]) > threshold:
                settling_idx = i + 1
                break
        settling_time = float(settling_idx) / max(min_len, 1)

        # 响应时间：PV 首次到达 90% SP 的比例时间（仅在 SP 有变化时有意义）
        if len(step_indices) > 0:
            step_idx = step_indices[0]
            target = sp[step_idx] + 0.9 * (sp[step_idx + 1] - sp[step_idx])
            response_idx = step_idx
            for i in range(step_idx + 1, min_len):
                if abs(pv[i] - target) < threshold:
                    response_idx = i
                    break
            response_time = float(response_idx - step_idx) / max(min_len, 1)
        else:
            response_time = 0.0

        # 稳态误差：最后 10% 数据的平均误差
        tail_len = max(1, min_len // 10)
        steady_state_error = float(np.mean(np.abs(error[-tail_len:])))

        # 过激判定：过冲 > 20%
        overaggressive = bool(overshoot > 0.2)
        # 过保守判定：响应时间 > 0.5 且稳态误差 > 5% SP 范围
        overconservative = bool(response_time > 0.5 and steady_state_error > sp_range * 0.05)

        confidence = 0.0
        if overaggressive:
            confidence = min(1.0, overshoot)
        elif overconservative:
            confidence = min(1.0, response_time)

        return {
            "overaggressive": overaggressive,
            "overconservative": overconservative,
            "confidence": confidence,
            "overshoot": overshoot,
            "settling_time": settling_time,
            "response_time": response_time,
            "steady_state_error": steady_state_error,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("PID 增益分析失败: %s", exc)
        return {
            "overaggressive": False,
            "overconservative": False,
            "confidence": 0.0,
            "overshoot": 0.0,
            "settling_time": 0.0,
            "response_time": 0.0,
            "steady_state_error": 0.0,
        }


def _detect_external_disturbance(
    pv_values: np.ndarray, sample_interval: float = 1.0
) -> dict[str, Any]:
    """频谱分析检测外扰频繁。

    Args:
        pv_values: PV 数据数组
        sample_interval: 采样间隔（秒），用于频率换算

    Returns:
        {detected, confidence, frequency, amplitude}
    """
    if len(pv_values) < 8:
        return {"detected": False, "confidence": 0.0, "frequency": 0.0, "amplitude": 0.0}

    try:
        N = len(pv_values)
        fs = 1.0 / sample_interval if sample_interval > 0 else 1.0  # 采样频率 (Hz)
        pv_centered = pv_values - np.mean(pv_values)
        fft_vals = np.fft.rfft(pv_centered)
        fft_magnitude = np.abs(fft_vals)

        if len(fft_magnitude) <= 2:
            return {"detected": False, "confidence": 0.0, "frequency": 0.0, "amplitude": 0.0}

        # 检测高频分量（排除主频）
        low_freq_energy = float(np.sum(fft_magnitude[1:3] ** 2))
        high_freq_energy = float(np.sum(fft_magnitude[3:] ** 2))
        total_energy = low_freq_energy + high_freq_energy

        if total_energy <= 0:
            return {"detected": False, "confidence": 0.0, "frequency": 0.0, "amplitude": 0.0}

        high_freq_ratio = high_freq_energy / total_energy
        peak_idx = int(np.argmax(fft_magnitude[3:])) + 3
        amplitude = float(fft_magnitude[peak_idx] / N)
        # 频率 = peak_idx * fs / N（标准 FFT 频率换算公式）
        frequency = float(peak_idx * fs / N)

        # 外扰判定：高频能量占比 > 0.5
        detected = high_freq_ratio > 0.5
        confidence = min(1.0, high_freq_ratio) if detected else 0.0

        return {
            "detected": detected,
            "confidence": confidence,
            "frequency": frequency,
            "amplitude": amplitude,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("外扰检测失败: %s", exc)
        return {"detected": False, "confidence": 0.0, "frequency": 0.0, "amplitude": 0.0}


def _analyze_quality(pv_data: list[dict]) -> dict[str, Any]:
    """PV 质量码统计。

    Returns:
        {abnormal, confidence, bad_rate, total, bad_count}
    """
    total = len(pv_data)
    if total == 0:
        return {"abnormal": False, "confidence": 0.0, "bad_rate": 0.0, "total": 0, "bad_count": 0}

    bad_count = sum(1 for d in pv_data if str(d.get("quality", "GOOD")).upper() == "BAD")
    bad_rate = bad_count / total

    # 异常判定：Bad 占比 > 10%
    abnormal = bad_rate > 0.1
    confidence = min(1.0, bad_rate * 5) if abnormal else 0.0

    return {
        "abnormal": abnormal,
        "confidence": confidence,
        "bad_rate": bad_rate,
        "total": total,
        "bad_count": bad_count,
    }


def _analyze_saturation(op_values: np.ndarray) -> dict[str, Any]:
    """OP 饱和率分析。

    Returns:
        {detected, confidence, saturation_rate, high_count, low_count}
    """
    total = len(op_values)
    if total == 0:
        return {
            "detected": False,
            "confidence": 0.0,
            "saturation_rate": 0.0,
            "high_count": 0,
            "low_count": 0,
        }

    try:
        op_min = float(np.min(op_values))
        op_max = float(np.max(op_values))
        op_range = op_max - op_min
        if op_range <= 0:
            return {
                "detected": False,
                "confidence": 0.0,
                "saturation_rate": 0.0,
                "high_count": 0,
                "low_count": 0,
            }

        # 归一化到 0-100
        op_norm = (op_values - op_min) / op_range * 100
        high_count = int(np.sum(op_norm >= 95))
        low_count = int(np.sum(op_norm <= 5))
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
    }


def _compute_max_bicoherence(
    signal: np.ndarray, n_seg: int = 4, n_freq: int = 16
) -> float:
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
    pv: np.ndarray, op: np.ndarray
) -> dict[str, Any]:
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
        ngi = abs(kurtosis_excess) / 6.0 + (skewness ** 2) / 24.0

        # NLI: 非线性指数（最大双相干性近似）
        nli = _compute_max_bicoherence(op_centered)

        # PV-OP 椭圆拟合（复用现有 _detect_valve_stiction 拟合度）
        stiction_fit = _detect_valve_stiction(pv_arr, op_arr)
        fitting_score = float(stiction_fit.get("fitting_score", 0.0))
        stiction_index = float(stiction_fit.get("stiction_index", 0.0))

        # 判定规则（ADS §5.2.2: NGI > 0.001 且 NLI > 0.01）
        detected = bool(ngi > 0.001 and nli > 0.01)

        # 置信度：融合 NGI、NLI 和椭圆拟合度
        if detected:
            confidence = min(1.0, (ngi * 50.0 + nli * 30.0 + fitting_score * 20.0) / 3.0)
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
        mv_arr = (mv[:min_len].astype(float) if mv is not None else op_arr)

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
        old_sp = float(sp_arr[step_idx])

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
        confidence = (satisfied / 3.0) if detected else 0.0

        return {
            "detected": detected,
            "confidence": confidence,
            "overshoot": overshoot,
            "decay_ratio": decay_ratio,
            "steady_state_error": steady_state_error,
            "step_count": len(step_indices),
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("阶跃响应分析失败: %s", exc)
        return _empty_step_response_result()


def _compute_decay_ratio(
    pv_response: np.ndarray, new_sp: float, step_size: float
) -> float:
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
            ratio = bias_std / sp_range
            detected = bool(ratio > 0.1)
            expected_tau = _expected_time_constant(control_type)
            return {
                "detected": detected,
                "confidence": min(1.0, ratio * 5) if detected else 0.0,
                "time_constant": 0.0,
                "expected_time_constant": expected_tau,
                "ratio": ratio,
            }

        # 分析第一个阶跃后的响应
        step_idx = int(step_indices[0])
        step_size = float(sp_arr[step_idx + 1] - sp_arr[step_idx])
        if abs(step_size) < 1e-9:
            return _empty_slow_response_result()

        new_sp = float(sp_arr[step_idx + 1])
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

        confidence = min(1.0, ratio / 5.0) if detected else 0.0

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
        confidence = min(1.0, shift_frequency / 10.0) if detected else 0.0

        return {
            "detected": detected,
            "confidence": confidence,
            "shift_count": shift_count,
            "max_cusum": max_cusum,
            "shift_magnitude": shift_magnitude,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("偏差突变检测失败: %s", exc)
        return _empty_bias_shift_result()


def _dempster_shafer_fusion(evidence: list[tuple[str, float]]) -> float:
    """多算法置信度融合（noisy-OR 加权模型）。

    替代原 D-S 证据理论的简化实现。原实现固定 target_label 导致多标签场景
    融合结果不合理，改为 noisy-OR 模型：假设各算法独立，融合置信度为
    P(A|e1,e2,...) = 1 - ∏(1 - P(A|ei))

    Args:
        evidence: [(label, confidence), ...] 每个算法的标签和置信度

    Returns:
        融合后的置信度（0-1）
    """
    if not evidence:
        return 0.0
    if len(evidence) == 1:
        return evidence[0][1]

    # noisy-OR: 融合独立证据
    # P(异常|所有证据) = 1 - ∏(1 - conf_i)
    prob_not = 1.0
    for _, conf in evidence:
        prob_not *= max(0.0, 1.0 - conf)
    fused = 1.0 - prob_not

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


def _align_timeseries(
    pv_data: list[dict],
    sp_data: list[dict],
    op_data: list[dict],
    mode_data: list[dict],
) -> list[dict[str, Any]]:
    """按 ts 对齐 PV/SP/OP/MODE 时序数据。

    对齐策略：
    1. 优先精确时间戳匹配（兼容字符串 ts 如 "t1"）
    2. 若 ts 可转为数值，使用 bisect 最近邻匹配，容差 ±500ms

    辅助函数复用 kpi_calc 模块实现，保持两处对齐逻辑一致。
    """
    from app.tasks.kpi_calc import (
        _build_ts_index,
        _find_nearest_value,
    )

    # 精确映射（兼容字符串 ts）
    sp_map = {d.get("ts"): d.get("value") for d in sp_data}
    op_map = {d.get("ts"): d.get("value") for d in op_data}
    mode_map = {d.get("ts"): d.get("value") for d in mode_data}

    # 数值索引（用于容差匹配）
    sp_ts_floats, sp_ts_orig = _build_ts_index(sp_data)
    op_ts_floats, op_ts_orig = _build_ts_index(op_data)
    mode_ts_floats, mode_ts_orig = _build_ts_index(mode_data)
    sp_values = [sp_map[t] for t in sp_ts_orig] if sp_ts_floats else None
    op_values = [op_map[t] for t in op_ts_orig] if op_ts_floats else None
    mode_values = [mode_map[t] for t in mode_ts_orig] if mode_ts_floats else None

    aligned: list[dict[str, Any]] = []
    for d in pv_data:
        ts = d.get("ts")
        pv = d.get("value")
        aligned.append(
            {
                "ts": ts,
                "pv": pv,
                "sp": _find_nearest_value(ts, sp_ts_floats, sp_map, sp_values),
                "op": _find_nearest_value(ts, op_ts_floats, op_map, op_values),
                "mode": _find_nearest_value(
                    ts, mode_ts_floats, mode_map, mode_values
                ),
            }
        )
    return aligned


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
    "_detect_bias_shift",
    "_detect_choudhury_nonlinearity",
    "_detect_kano_stiction",
    "_detect_slow_response",
    "run_diagnosis_hourly",
    "run_loop_diagnosis",
]
