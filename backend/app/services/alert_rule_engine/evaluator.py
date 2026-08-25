"""规则求值引擎（方案 §4.3 步骤 3-5）。

求值流程：
1. 时效窗口过滤（timeWindow）
2. 可信度门禁（confidencePolicy）
3. 条件求值（THRESHOLD/DRIFT/COMPOSITE/CONFIDENCE）
4. 持续时长检查（durationSeconds）

Phase 1 实现：THRESHOLD + CONFIDENCE + 时效窗口 + 可信度门禁
Phase 2 实现：DRIFT + COMPOSITE（SEQUENCE）

数据源：
- 实时轨：Redis 缓存（realtime:history:*）
- 周期轨：本地 TDengine（通过 get_provider()）
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.alert_rule_engine.dsl import render_dedup_key

logger = logging.getLogger(__name__)

# 严重度等级排序（用于升级/降级）
_SEVERITY_ORDER = {"INFO": 0, "WARN": 1, "ERROR": 2, "CRITICAL": 3}
_SEVERITY_BY_LEVEL = {v: k for k, v in _SEVERITY_ORDER.items()}


class EvaluationResult:
    """单次规则求值结果。"""

    def __init__(
        self,
        triggered: bool,
        triggered_value: float | None = None,
        condition_snapshot: dict[str, Any] | None = None,
        data_window: dict[str, Any] | None = None,
        confidence_level: str | None = None,
        severity: str | None = None,
        dedup_key: str | None = None,
    ) -> None:
        self.triggered = triggered
        self.triggered_value = triggered_value
        self.condition_snapshot = condition_snapshot or {}
        self.data_window = data_window
        self.confidence_level = confidence_level
        self.severity = severity
        self.dedup_key = dedup_key


async def evaluate_rule(
    db: AsyncSession,
    rule: dict[str, Any],
    loop_id: str,
    current_values: dict[str, float | str] | None = None,
    confidence_level: str | None = None,
) -> EvaluationResult:
    """求值单条规则（完整流程）。

    Args:
        db: 数据库会话
        rule: 规则缓存字典（cache._rule_to_dict 格式）
        loop_id: 回路 ID
        current_values: 当前实时值（metric → value）；None 时从 Redis/TDengine 取
        confidence_level: 回路当前可信度等级（A/B/C/D/E）；None 时不做门禁

    Returns:
        EvaluationResult
    """
    dsl = rule.get("dsl", {})

    # 1. 时效窗口过滤
    time_window = dsl.get("timeWindow")
    if time_window and time_window.get("enabled"):
        if not _is_in_time_window(time_window):
            return EvaluationResult(triggered=False)

    # 2. 可信度门禁
    conf_policy = dsl.get("confidencePolicy")
    severity = dsl.get("severity", "WARN")
    if conf_policy and confidence_level:
        max_level = conf_policy.get("maxLevel")
        action = conf_policy.get("action", "SUPPRESS")
        if max_level and _confidence_worse_than(confidence_level, max_level):
            if action == "SUPPRESS":
                return EvaluationResult(triggered=False, confidence_level=confidence_level)
            elif action == "DOWNGRADE":
                severity = _downgrade_severity(severity)
                if severity is None:
                    return EvaluationResult(triggered=False, confidence_level=confidence_level)

    # 3. 取当前值（如未传入）
    if current_values is None:
        current_values = await _get_current_values(loop_id)

    # 4. 条件求值
    rule_type = dsl.get("ruleType")
    condition = dsl.get("condition", {})

    if rule_type == "METRIC_THRESHOLD":
        # 指标阈值预警：基于评估/诊断结果，按监测周期检查（周期节流在函数内）
        return await _evaluate_metric_threshold_rule(
            db, rule, condition, loop_id, severity, confidence_level
        )
    elif rule_type == "THRESHOLD":
        triggered, triggered_value, snapshot = _evaluate_threshold(condition, current_values)
    elif rule_type == "CONFIDENCE":
        triggered, triggered_value, snapshot = _evaluate_confidence(condition, confidence_level)
    elif rule_type == "DRIFT":
        # Phase 2 实现，Phase 1 跳过
        return EvaluationResult(triggered=False)
    elif rule_type == "COMPOSITE":
        # Phase 2 实现，Phase 1 跳过（AND/OR 简化版）
        triggered, triggered_value, snapshot = _evaluate_composite_simple(
            condition, current_values, confidence_level
        )
    else:
        return EvaluationResult(triggered=False)

    # 5. dedupKey 渲染
    dedup_template = dsl.get("dedupKey", "${loop_id}+${rule_id}")
    dedup_key = render_dedup_key(dedup_template, loop_id=loop_id, rule_id=rule.get("id", ""))

    return EvaluationResult(
        triggered=triggered,
        triggered_value=triggered_value,
        condition_snapshot=snapshot,
        confidence_level=confidence_level,
        severity=severity,
        dedup_key=dedup_key,
    )


# ---------------------------------------------------------------------------
# THRESHOLD 求值
# ---------------------------------------------------------------------------


def _evaluate_threshold(
    condition: dict[str, Any],
    values: dict[str, float | str],
) -> tuple[bool, float | None, dict[str, Any]]:
    """阈值规则求值。

    Returns:
        (triggered, triggered_value, snapshot)
    """
    metric = condition.get("metric")
    operator = condition.get("operator")
    threshold_value = condition.get("value")

    actual = values.get(metric) if metric else None
    if actual is None:
        return False, None, {"metric": metric, "reason": "no_data"}

    # 数值比较
    if operator in (">", ">=", "<", "<=", "==", "!="):
        try:
            actual_num = float(actual)
            threshold_num = _resolve_value(threshold_value)
            triggered = _compare(actual_num, operator, threshold_num)
            return (
                triggered,
                actual_num,
                {
                    "metric": metric,
                    "operator": operator,
                    "threshold": threshold_value,
                    "actualValue": actual_num,
                },
            )
        except (ValueError, TypeError):
            return False, None, {"metric": metric, "reason": "type_mismatch"}

    # 枚举比较（MODE）
    if operator in ("IN", "NOT_IN"):
        if not isinstance(threshold_value, list):
            return False, None, {"metric": metric, "reason": "value_not_list"}
        in_set = str(actual) in [str(v) for v in threshold_value]
        triggered = in_set if operator == "IN" else not in_set
        return (
            triggered,
            None,
            {
                "metric": metric,
                "operator": operator,
                "threshold": threshold_value,
                "actualValue": actual,
            },
        )

    # 变化率（Phase 2，需窗口数据）
    if operator == "RATE_OF_CHANGE":
        return False, None, {"metric": metric, "reason": "rate_of_change_not_supported"}

    return False, None, {"metric": metric, "reason": "unknown_operator"}


def _resolve_value(value: Any) -> float:
    """解析阈值 value（支持数值/百分比字符串/highLimit/lowLimit）。

    Phase 1 仅支持数值；百分比/量程引用在 Phase 2 实现（需 loop_ledger 量程）。
    """
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        if value.endswith("%"):
            # Phase 2: 基于回路量程换算
            raise ValueError(f"百分比阈值 '{value}' 暂未实现（Phase 2）")
        if value in ("highLimit", "lowLimit"):
            raise ValueError(f"量程引用 '{value}' 暂未实现（Phase 2）")
        return float(value)
    raise ValueError(f"无法解析阈值: {value}")


def _compare(actual: float, operator: str, threshold: float) -> bool:
    """数值比较。"""
    if operator == ">":
        return actual > threshold
    if operator == ">=":
        return actual >= threshold
    if operator == "<":
        return actual < threshold
    if operator == "<=":
        return actual <= threshold
    if operator == "==":
        return actual == threshold
    if operator == "!=":
        return actual != threshold
    return False


# ---------------------------------------------------------------------------
# CONFIDENCE 求值
# ---------------------------------------------------------------------------


def _evaluate_confidence(
    condition: dict[str, Any],
    confidence_level: str | None,
) -> tuple[bool, float | None, dict[str, Any]]:
    """可信度联动规则求值。

    当回路可信度劣于 maxLevel 时触发（生成"数据质量低"事件）。
    """
    max_level = condition.get("maxLevel")
    if not max_level or not confidence_level:
        return False, None, {"reason": "no_confidence_data"}

    triggered = _confidence_worse_than(confidence_level, max_level)
    return (
        triggered,
        None,
        {
            "maxLevel": max_level,
            "actualLevel": confidence_level,
        },
    )


def _confidence_worse_than(actual: str, threshold: str) -> bool:
    """判断可信度是否劣于阈值（A 最优 → E 最差）。"""
    order = {"A": 0, "B": 1, "C": 2, "D": 3, "E": 4}
    return order.get(actual, 0) > order.get(threshold, 0)


# ---------------------------------------------------------------------------
# COMPOSITE 简化求值（Phase 1：AND/OR/NOT，不含 SEQUENCE）
# ---------------------------------------------------------------------------


def _evaluate_composite_simple(
    condition: dict[str, Any],
    values: dict[str, float | str],
    confidence_level: str | None,
) -> tuple[bool, float | None, dict[str, Any]]:
    """组合条件求值（Phase 1 简化版，仅 AND/OR/NOT）。

    SEQUENCE 需 Redis 状态机，Phase 2 实现。
    """
    logic = condition.get("logic", "AND")
    operands = condition.get("operands", [])

    if logic == "SEQUENCE":
        # Phase 2 实现
        return False, None, {"reason": "sequence_not_supported"}

    if not operands:
        return False, None, {"reason": "no_operands"}

    results = []
    for operand in operands:
        op_type = operand.get("type")
        if op_type == "THRESHOLD":
            triggered, _val, _ = _evaluate_threshold(operand, values)
        elif op_type == "CONFIDENCE":
            triggered, _val, _ = _evaluate_confidence(operand, confidence_level)
        elif op_type == "DRIFT":
            triggered = False  # Phase 2
        elif op_type == "COMPOSITE":
            triggered, _val, _ = _evaluate_composite_simple(operand, values, confidence_level)
        else:
            triggered = False
        results.append(triggered)

    if logic == "AND":
        triggered = all(results)
    elif logic == "OR":
        triggered = any(results)
    elif logic == "NOT":
        triggered = not results[0] if results else False
    else:
        triggered = False

    return triggered, None, {"logic": logic, "operandResults": results}


# ---------------------------------------------------------------------------
# 时效窗口
# ---------------------------------------------------------------------------


def _is_in_time_window(time_window: dict[str, Any]) -> bool:
    """检查当前时间是否在时效窗口内。

    Phase 1 简化实现：仅检查 cron 的小时/分钟部分。
    完整 cron 解析在 Phase 2 引入 croniter 库。
    """
    cron = time_window.get("cron", "")

    # 简化：解析 cron 的 hour 部分（如 "0 8-20 * * *" → hour=8-20）
    # 格式：minute hour day month weekday
    parts = cron.split()
    if len(parts) < 2:
        return True  # 无法解析，默认生效

    now = datetime.now(UTC)
    # 简化时区处理（UTC+8）
    local_hour = (now.hour + 8) % 24

    hour_part = parts[1]
    if hour_part == "*":
        return True

    # 解析 "8-20" / "8,12,16" / "8" 格式
    if "-" in hour_part:
        start, end = hour_part.split("-")
        return int(start) <= local_hour < int(end)
    elif "," in hour_part:
        hours = [int(h) for h in hour_part.split(",")]
        return local_hour in hours
    else:
        return local_hour == int(hour_part)


# ---------------------------------------------------------------------------
# 严重度升级/降级
# ---------------------------------------------------------------------------


def _downgrade_severity(severity: str) -> str | None:
    """降级严重度（CRITICAL → ERROR → WARN → INFO；INFO 降级为 None=跳过）。"""
    level = _SEVERITY_ORDER.get(severity, 1)
    if level <= 0:
        return None
    return _SEVERITY_BY_LEVEL[level - 1]


def upgrade_severity(severity: str, trigger_count: int, threshold: int = 3) -> str:
    """升级严重度（重复触发 threshold 次后升级一级）。"""
    if trigger_count < threshold:
        return severity
    level = _SEVERITY_ORDER.get(severity, 1)
    if level >= 3:
        return "CRITICAL"
    return _SEVERITY_BY_LEVEL[level + 1]


# ---------------------------------------------------------------------------
# 数据源读取
# ---------------------------------------------------------------------------


async def _get_current_values(loop_id: str) -> dict[str, float | str]:
    """从 Redis 实时缓存读取回路当前值（7 tag）。

    数据源：``realtime:<tag_name>`` → JSON {tagCode, value, quality, collectTime}，
    由 RealtimeSubscriber._cache_value 写入。回路 7 tag 通过 loop_tag_mapping +
    tag_registry 关联，role ∈ {PV, SP, OP, MODE, PID_P, PID_I, PID_D}。
    """
    import json

    from sqlalchemy import select

    from app.core.db import AsyncSessionLocal
    from app.core.redis import redis_client
    from app.models.loop import LoopTagMapping
    from app.models.tag import TagRegistry

    values: dict[str, float | str] = {}
    try:
        # 查回路 7 tag 的 tag_name + role
        async with AsyncSessionLocal() as db:
            stmt = (
                select(TagRegistry.tag_name, LoopTagMapping.tag_role)
                .join(LoopTagMapping, LoopTagMapping.tag_id == TagRegistry.id)
                .where(LoopTagMapping.loop_id == loop_id)
            )
            result = await db.execute(stmt)
            tag_rows = result.all()

        if not tag_rows:
            return values

        # 批量从 Redis 读取实时值
        keys = [f"realtime:{row[0]}" for row in tag_rows]
        raw_values = await redis_client.mget(keys)
        for (_tag_name, role), raw in zip(tag_rows, raw_values, strict=False):
            if not raw:
                continue
            try:
                payload = json.loads(raw if isinstance(raw, str) else raw.decode())
            except (json.JSONDecodeError, TypeError):
                continue
            val = payload.get("value")
            if val is None or val == "":
                continue
            # 数值优先转 float，MODE 等枚举保留字符串
            try:
                values[role] = float(val)
            except (ValueError, TypeError):
                values[role] = str(val)
    except Exception:  # noqa: BLE001
        logger.debug("实时值读取异常，返回空值", exc_info=True)
    return values


# ---------------------------------------------------------------------------
# METRIC_THRESHOLD（指标阈值预警）求值
# ---------------------------------------------------------------------------

#: 诊断严重度 → 数值映射（severity 指标比较用）
_DIAG_SEVERITY_MAP = {"LOW": 1.0, "MEDIUM": 2.0, "HIGH": 3.0}


async def _evaluate_metric_threshold_rule(
    db: AsyncSession,
    rule: dict[str, Any],
    condition: dict[str, Any],
    loop_id: str,
    severity: str,
    confidence_level: str | None,
) -> EvaluationResult:
    """指标阈值预警求值（基于评估 KPI / 诊断结果，按监测周期检查）。

    流程：
    1. 周期节流：Redis ``alert:metriccheck:<rule_id>:<loop_id>`` SETNX EX
       checkIntervalMinutes×60s —— 存在则本周期已检查过，跳过；
    2. 数据新鲜度：结果时间早于 2× 监测周期则视为陈旧，跳过；
    3. 取指标值并比较；
    4. 连续超限计数：Redis ``alert:mcount:<rule_id>:<loop_id>`` INCR，
       达到 durationCount 才触发（触发后清零重新计数）；未超限即清零。
    """
    from app.core.redis import redis_client

    dsl = rule.get("dsl", {})
    rule_id = str(rule.get("id", ""))
    metric_source = condition.get("metricSource", "KPI")
    metric_code = condition.get("metricCode")
    operator = condition.get("operator")
    threshold_value = condition.get("value")
    levels = condition.get("levels") or []
    interval_minutes = condition.get("checkIntervalMinutes", 60)
    duration_count = condition.get("durationCount", 1)

    # dedupKey 渲染（与主流程一致）
    dedup_template = dsl.get("dedupKey", "${loop_id}+${rule_id}")
    dedup_key = render_dedup_key(dedup_template, loop_id=loop_id, rule_id=rule_id)

    def _result(
        triggered: bool,
        snapshot: dict[str, Any],
        value: float | None = None,
        severity_override: str | None = None,
    ):
        return EvaluationResult(
            triggered=triggered,
            triggered_value=value,
            condition_snapshot=snapshot,
            confidence_level=confidence_level,
            severity=severity_override or severity,
            dedup_key=dedup_key,
        )

    # 1. 周期节流（Redis 不可用时退化为每次都检查）
    check_key = f"alert:metriccheck:{rule_id}:{loop_id}"
    try:
        already_checked = not await redis_client.set(
            check_key, "1", ex=max(interval_minutes, 1) * 60, nx=True
        )
        if already_checked:
            return _result(False, {"reason": "interval_not_reached"})
    except Exception:  # noqa: BLE001
        logger.debug("指标预警周期节流检查失败（Redis 异常，按需继续）", exc_info=True)

    # 2. 取指标值
    try:
        if metric_source == "DIAGNOSIS":
            actual, data_time = await _get_latest_diagnosis_metric(db, loop_id, metric_code)
        else:
            actual, data_time = await _get_latest_kpi_metric(db, loop_id, metric_code)
    except Exception:  # noqa: BLE001
        logger.warning("指标预警取值失败: loop=%s metric=%s", loop_id, metric_code, exc_info=True)
        return _result(False, {"reason": "query_failed", "metric": metric_code})

    if actual is None:
        return _result(False, {"reason": "no_data", "metric": metric_code})

    # 3. 数据新鲜度：早于 2× 监测周期视为陈旧（评估/诊断任务停摆时不误报）
    if data_time is not None:
        from datetime import UTC, datetime

        age_seconds = (datetime.now(UTC) - data_time).total_seconds()
        if age_seconds > max(interval_minutes * 60 * 2, 7200):
            return _result(
                False,
                {"reason": "stale_data", "metric": metric_code, "ageSeconds": age_seconds},
            )

    # 4. 比较（三级阈值：取满足条件的最严重等级；单级向后兼容）
    matched_level: dict[str, Any] | None = None
    if levels:
        level_order = {"WARN": 1, "ERROR": 2, "CRITICAL": 3}
        matched_list = [
            lv
            for lv in levels
            if isinstance(lv, dict)
            and isinstance(lv.get("value"), int | float)
            and _compare(actual, operator, float(lv["value"]))
        ]
        triggered = bool(matched_list)
        if matched_list:
            matched_level = max(matched_list, key=lambda lv: level_order.get(lv.get("severity"), 0))
        snapshot = {
            "metricSource": metric_source,
            "metric": metric_code,
            "operator": operator,
            "levels": levels,
            "matchedLevel": matched_level,
            "actualValue": actual,
            "dataTime": data_time.isoformat() if data_time else None,
        }
    else:
        triggered = _compare(actual, operator, float(threshold_value))
        snapshot = {
            "metricSource": metric_source,
            "metric": metric_code,
            "operator": operator,
            "threshold": threshold_value,
            "actualValue": actual,
            "dataTime": data_time.isoformat() if data_time else None,
        }

    # 5. 连续超限计数（Redis 异常时按 durationCount=1 直接判定）
    if duration_count > 1:
        mcount_key = f"alert:mcount:{rule_id}:{loop_id}"
        try:
            if triggered:
                count = await redis_client.incr(mcount_key)
                await redis_client.expire(mcount_key, max(interval_minutes * 60 * 4, 86400))
                if count < duration_count:
                    return _result(False, {**snapshot, "consecutiveCount": count})
                # 达到连续次数：清零计数（冷却期后需重新累计）
                await redis_client.delete(mcount_key)
            else:
                await redis_client.delete(mcount_key)
        except Exception:  # noqa: BLE001
            logger.debug("指标预警连续计数失败（Redis 异常，直接判定）", exc_info=True)

    sev_override = matched_level.get("severity") if triggered and matched_level else None
    return _result(triggered, snapshot, actual, severity_override=sev_override)


async def _get_latest_kpi_metric(
    db: AsyncSession, loop_id: str, metric_code: str | None
) -> tuple[float | None, Any]:
    """从 loop_confidence_latest（每回路单行）读取 KPI 指标值与评估时间。

    Returns:
        (指标值, 评估时刻 timezone-aware UTC)；无数据返回 (None, None)。
    """
    from datetime import UTC

    from sqlalchemy import select

    from app.models.metric import LoopConfidenceLatest

    row = (
        await db.execute(
            select(LoopConfidenceLatest).where(LoopConfidenceLatest.loop_id == loop_id)
        )
    ).scalar_one_or_none()
    if row is None:
        return None, None

    value: float | None = None
    if metric_code == "score":
        value = float(row.score) if row.score is not None else None
    elif metric_code == "valid_rate":
        value = float(row.valid_rate) if row.valid_rate is not None else None
    elif metric_code and row.metrics and metric_code in row.metrics:
        entry = row.metrics[metric_code]
        # metrics JSONB 结构：{code: {value: x, confidence: ...}} 或直接数值
        if isinstance(entry, dict):
            raw = entry.get("value")
            value = float(raw) if raw is not None else None
        elif isinstance(entry, int | float):
            value = float(entry)

    eval_time = row.eval_time if row.eval_time is not None else row.updated_at
    aware_time = (
        eval_time.replace(tzinfo=UTC) if eval_time and eval_time.tzinfo is None else eval_time
    )
    return value, aware_time


async def _get_latest_diagnosis_metric(
    db: AsyncSession, loop_id: str, metric_code: str | None
) -> tuple[float | None, Any]:
    """从 diagnosis_run 最新一条读取诊断指标值与诊断时间。

    可监测指标：severity（LOW=1/MEDIUM=2/HIGH=3）、primary_confidence（0-1）。
    """
    from datetime import UTC

    from sqlalchemy import select

    from app.models.diagnosis_run import DiagnosisRun

    row = (
        await db.execute(
            select(DiagnosisRun)
            .where(DiagnosisRun.loop_id == loop_id)
            .order_by(DiagnosisRun.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        return None, None

    value: float | None = None
    if metric_code == "severity":
        value = _DIAG_SEVERITY_MAP.get(row.severity or "")
    elif metric_code == "primary_confidence":
        value = float(row.primary_confidence) if row.primary_confidence is not None else None

    created = row.created_at
    aware_time = created.replace(tzinfo=UTC) if created and created.tzinfo is None else created
    return value, aware_time


# ---------------------------------------------------------------------------
# 批量求值入口（周期巡检用）
# ---------------------------------------------------------------------------


async def evaluate_loop_rules(
    db: AsyncSession,
    loop_id: str,
    confidence_level: str | None = None,
) -> list[EvaluationResult]:
    """批量求值回路的所有订阅规则（周期巡检用）。

    Returns:
        所有触发的 EvaluationResult 列表（未触发的不返回）
    """
    from app.services.alert_rule_engine.cache import get_rules_for_loop

    rules = await get_rules_for_loop(db, loop_id)
    if not rules:
        return []

    # 取当前值（所有规则共享一次读取）
    current_values = await _get_current_values(loop_id)

    triggered_results: list[EvaluationResult] = []
    for rule in rules:
        try:
            result = await evaluate_rule(
                db=db,
                rule=rule,
                loop_id=loop_id,
                current_values=current_values,
                confidence_level=confidence_level,
            )
            if result.triggered:
                triggered_results.append(result)
        except Exception:  # noqa: BLE001
            logger.warning(
                "规则求值异常 rule=%s loop=%s", rule.get("ruleCode"), loop_id, exc_info=True
            )
    return triggered_results
