"""预警规则引擎 CRUD 服务（方案 §6 API 业务逻辑）。

封装规则/订阅/事件/抑制/审计的数据库操作，endpoint 层只做参数校验和调用。
所有写操作在同一事务中完成（含审计日志），由调用方在 endpoint 提交。
"""

from __future__ import annotations

import copy
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.models.alert import (
    AlertEvent,
    AlertRule,
    AlertRuleAuditLog,
    AlertRuleSubscription,
    AlertSuppression,
)
from app.models.loop import LoopLedger
from app.services.alert_rule_engine.audit import write_audit
from app.services.alert_rule_engine.cache import (
    invalidate_all_cache,
    invalidate_loop_cache,
    invalidate_rule_cache,
)
from app.services.alert_rule_engine.dsl import validate_dsl
from app.services.alert_rule_engine.suppressor import Suppressor

logger = logging.getLogger(__name__)

_suppressor = Suppressor()

# sys_config 全局开关键
_KEY_GLOBAL_SWITCH = "alert.global_enabled"
_KEY_GLOBAL_SWITCH_DESC = "预警引擎全局开关（true/false）"

# ---------------------------------------------------------------------------
# 预制规则（评估/诊断指标，2026-08-24 预警规则产品化）
# 用户仅可调阈值（含一般/重要/紧急三级）与启停，不允许新增/删除/改结构
# ---------------------------------------------------------------------------

PRESET_RULE_CODE_PREFIX = "PRESET_"

_LEVEL_SEVERITY_ORDER = ("WARN", "ERROR", "CRITICAL")


def _preset_levels(*values: float) -> list[dict[str, Any]]:
    """按 一般(WARN)→重要(ERROR)→紧急(CRITICAL) 顺序生成三级阈值（可只给前 N 级）。"""
    return [
        {"severity": sev, "value": val}
        for sev, val in zip(_LEVEL_SEVERITY_ORDER, values, strict=False)
    ]


def _build_preset_dsl(
    metric_source: str,
    metric_code: str,
    operator: str,
    levels: list[dict[str, Any]],
    check_interval_minutes: int = 60,
    duration_count: int = 1,
) -> dict[str, Any]:
    """生成预制规则 DSL（condition.value 取 WARN 级阈值，向后兼容单级链路）。"""
    warn_value = next(
        (lv["value"] for lv in levels if lv["severity"] == "WARN"), levels[0]["value"]
    )
    return {
        "ruleType": "METRIC_THRESHOLD",
        "scope": {"loopSelector": {"type": "ALL"}},
        "condition": {
            "metricSource": metric_source,
            "metricCode": metric_code,
            "operator": operator,
            "value": warn_value,
            "levels": levels,
            "checkIntervalMinutes": check_interval_minutes,
            "durationCount": duration_count,
        },
        "severity": "WARN",
        "actions": [{"type": "CREATE_EVENT"}, {"type": "NOTIFY"}],
    }


#: 12 条预制规则：10 性能评估指标（KPI，0-100 百分制）+ 2 故障诊断指标
PRESET_RULES: list[dict[str, Any]] = [
    {
        "rule_code": "PRESET_KPI_SCORE",
        "rule_name": "综合评分过低",
        "description": "回路综合评分低于阈值（百分制）",
        "dsl": _build_preset_dsl("KPI", "score", "<", _preset_levels(60, 40, 20)),
    },
    {
        "rule_code": "PRESET_KPI_EFFECTIVE_AUTO_RATE",
        "rule_name": "有效自控率偏低",
        "description": "有效自控率低于阈值（百分制）",
        "dsl": _build_preset_dsl("KPI", "effective_auto_rate", "<", _preset_levels(85, 70, 50)),
    },
    {
        "rule_code": "PRESET_KPI_STEADY_RATE",
        "rule_name": "平稳率偏低",
        "description": "平稳率低于阈值（百分制）",
        "dsl": _build_preset_dsl("KPI", "steady_rate", "<", _preset_levels(85, 70, 50)),
    },
    {
        "rule_code": "PRESET_KPI_FAST_RATE",
        "rule_name": "快速率偏低",
        "description": "快速率低于阈值（百分制）",
        "dsl": _build_preset_dsl("KPI", "fast_rate", "<", _preset_levels(85, 70, 50)),
    },
    {
        "rule_code": "PRESET_KPI_ACCURACY_RATE",
        "rule_name": "准确率偏低",
        "description": "准确率低于阈值（百分制）",
        "dsl": _build_preset_dsl("KPI", "accuracy_rate", "<", _preset_levels(85, 70, 50)),
    },
    {
        "rule_code": "PRESET_KPI_AUTO_MODE_RATE",
        "rule_name": "平均自控率偏低",
        "description": "平均自控率（自动模式占比）低于阈值（百分制）",
        "dsl": _build_preset_dsl("KPI", "auto_mode_rate", "<", _preset_levels(85, 70, 50)),
    },
    {
        "rule_code": "PRESET_KPI_GOOD_VALUE_RATE",
        "rule_name": "好值率偏低",
        "description": "PV 好值率低于阈值（百分制）",
        "dsl": _build_preset_dsl("KPI", "good_value_rate", "<", _preset_levels(95, 90, 80)),
    },
    {
        "rule_code": "PRESET_KPI_VALID_RATE",
        "rule_name": "有效率偏低",
        "description": "评估有效率低于阈值（百分制）",
        "dsl": _build_preset_dsl("KPI", "valid_rate", "<", _preset_levels(90, 80, 60)),
    },
    {
        "rule_code": "PRESET_KPI_OSCILLATION_RATE",
        "rule_name": "振荡率偏高",
        "description": "振荡回路占比高于阈值（百分制）",
        "dsl": _build_preset_dsl("KPI", "oscillation_rate", ">", _preset_levels(10, 20, 40)),
    },
    {
        "rule_code": "PRESET_KPI_SATURATION_RATE",
        "rule_name": "饱和率偏高",
        "description": "输出饱和回路占比高于阈值（百分制）",
        "dsl": _build_preset_dsl("KPI", "saturation_rate", ">", _preset_levels(10, 20, 40)),
    },
    {
        "rule_code": "PRESET_DIAG_SEVERITY",
        "rule_name": "诊断故障等级过高",
        "description": "最新诊断故障等级达阈（LOW=1/MEDIUM=2/HIGH=3）",
        "dsl": _build_preset_dsl("DIAGNOSIS", "severity", ">=", _preset_levels(2, 3)),
    },
    {
        "rule_code": "PRESET_DIAG_CONFIDENCE",
        "rule_name": "诊断置信度过低",
        "description": "最新诊断主因置信度低于阈值（0-1）",
        "dsl": _build_preset_dsl(
            "DIAGNOSIS", "primary_confidence", "<=", _preset_levels(0.6, 0.4, 0.2)
        ),
    },
]


def _is_preset_rule(rule: AlertRule) -> bool:
    return rule.rule_code.startswith(PRESET_RULE_CODE_PREFIX)


def _merge_preset_dsl(base_dsl: dict[str, Any], incoming_dsl: dict[str, Any]) -> dict[str, Any]:
    """预制规则 DSL 合并：仅允许覆盖 condition.value / condition.levels。

    指标维度（metricSource/metricCode/operator）与其余结构一律锁定，
    传入不一致时拒绝（ERR_ALERT_RULE_PRESET_LOCKED）。
    """
    if not isinstance(incoming_dsl, dict):
        raise BizError(
            code="ERR_ALERT_RULE_PRESET_LOCKED",
            message="预制规则更新必须携带完整 DSL 对象",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    base_cond = base_dsl.get("condition") or {}
    in_cond = incoming_dsl.get("condition") or {}
    for key in ("metricSource", "metricCode", "operator"):
        if in_cond.get(key) is not None and in_cond[key] != base_cond.get(key):
            raise BizError(
                code="ERR_ALERT_RULE_PRESET_LOCKED",
                message=f"预制规则不允许修改 {key}，仅可调整阈值与启停",
                status_code=status.HTTP_403_FORBIDDEN,
            )
    merged = copy.deepcopy(base_dsl)
    cond = merged.setdefault("condition", {})
    if "value" in in_cond:
        cond["value"] = in_cond["value"]
    if "levels" in in_cond:
        cond["levels"] = in_cond["levels"]
    return merged


def _now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _rule_to_dict(rule: AlertRule) -> dict[str, Any]:
    """ORM → 响应字典。"""
    return {
        "rule_id": rule.id,
        "rule_code": rule.rule_code,
        "rule_name": rule.rule_name,
        "rule_type": rule.rule_type,
        "dsl": rule.dsl,
        "description": rule.description,
        "priority": rule.priority,
        "is_enabled": rule.is_enabled,
        "version": rule.version,
        "created_by": rule.created_by,
        "created_at": rule.created_at.isoformat() if rule.created_at else None,
        "updated_by": rule.updated_by,
        "updated_at": rule.updated_at.isoformat() if rule.updated_at else None,
    }


def _event_to_dict(event: AlertEvent, loop_name: str | None = None) -> dict[str, Any]:
    """ORM → 响应字典。"""
    return {
        "event_id": event.id,
        "rule_id": event.rule_id,
        "rule_code": event.rule_code,
        "rule_version": event.rule_version,
        "loop_id": event.loop_id,
        "severity": event.severity,
        "status": event.status,
        "trigger_condition_snapshot": event.trigger_condition_snapshot,
        "data_window": event.data_window,
        "triggered_value": (
            float(event.triggered_value) if event.triggered_value is not None else None
        ),
        "confidence_level": event.confidence_level,
        "rule_dsl_snapshot": event.rule_dsl_snapshot,
        "tracker_id": event.tracker_id,
        "is_false_positive": event.is_false_positive,
        "trigger_count": event.trigger_count,
        "triggered_at": event.triggered_at.isoformat() if event.triggered_at else None,
        "acknowledged_by": event.acknowledged_by,
        "acknowledged_at": event.acknowledged_at.isoformat() if event.acknowledged_at else None,
        "resolved_by": event.resolved_by,
        "resolved_at": event.resolved_at.isoformat() if event.resolved_at else None,
        "resolution_note": event.resolution_note,
        "loop_name": loop_name,
    }


def _subscription_to_dict(sub: AlertRuleSubscription) -> dict[str, Any]:
    return {
        "subscription_id": sub.id,
        "rule_id": sub.rule_id,
        "loop_id": sub.loop_id,
        "scope_type": sub.scope_type,
        "scope_value": sub.scope_value,
        "is_active": sub.is_active,
        "created_by": sub.created_by,
        "created_at": sub.created_at.isoformat() if sub.created_at else None,
    }


def _suppression_to_dict(sup: AlertSuppression) -> dict[str, Any]:
    return {
        "suppression_id": sup.id,
        "rule_id": sup.rule_id,
        "loop_id": sup.loop_id,
        "reason": sup.reason,
        "suppressed_by": sup.suppressed_by,
        "start_at": sup.start_at.isoformat() if sup.start_at else None,
        "end_at": sup.end_at.isoformat() if sup.end_at else None,
        "is_active": sup.is_active,
        "created_at": sup.created_at.isoformat() if sup.created_at else None,
    }


def _audit_to_dict(log: AlertRuleAuditLog) -> dict[str, Any]:
    return {
        "log_id": log.id,
        "rule_id": log.rule_id,
        "rule_code": log.rule_code,
        "operation_type": log.operation_type,
        "before_value": log.before_value,
        "after_value": log.after_value,
        "operator": log.operator,
        "operated_at": log.operated_at.isoformat() if log.operated_at else None,
    }


# ---------------------------------------------------------------------------
# 规则 CRUD
# ---------------------------------------------------------------------------


async def create_rule(db: AsyncSession, operator: str, rule_data: dict[str, Any]) -> dict[str, Any]:
    """创建预警规则。

    预制规则模式（2026-08-24）：评估/诊断指标预警规则全部预制下发，
    用户仅可修改阈值与启停，不允许新增规则；预制种子由
    ``ensure_preset_rules`` 直接写库，不走本函数。
    """
    raise BizError(
        code="ERR_ALERT_RULE_CREATE_DISABLED",
        message="预制规则模式：不允许新增预警规则，仅可调整预制规则的阈值与启停",
        status_code=status.HTTP_403_FORBIDDEN,
    )


async def update_rule(
    db: AsyncSession, rule_id: str, operator: str, rule_data: dict[str, Any]
) -> dict[str, Any]:
    """更新预警规则（预制规则仅允许改阈值/启停）。"""
    rule = await _get_rule_or_404(db, rule_id)
    before_snapshot = _rule_to_dict(rule)

    if _is_preset_rule(rule):
        locked = [
            f for f in ("rule_name", "description", "priority") if rule_data.get(f) is not None
        ]
        if locked:
            raise BizError(
                code="ERR_ALERT_RULE_PRESET_LOCKED",
                message=f"预制规则不允许修改 {'/'.join(locked)}，仅可调整阈值与启停",
                status_code=status.HTTP_403_FORBIDDEN,
            )
        if rule_data.get("dsl") is not None:
            merged = _merge_preset_dsl(rule.dsl, rule_data["dsl"])
            validate_dsl(merged)
            rule.dsl = merged
    else:
        if rule_data.get("dsl") is not None:
            validate_dsl(rule_data["dsl"])
            rule.dsl = rule_data["dsl"]
        if rule_data.get("rule_name") is not None:
            rule.rule_name = rule_data["rule_name"]
        if rule_data.get("description") is not None:
            rule.description = rule_data["description"]
        if rule_data.get("priority") is not None:
            rule.priority = rule_data["priority"]
    if rule_data.get("is_enabled") is not None:
        rule.is_enabled = rule_data["is_enabled"]
    rule.version += 1
    rule.updated_by = operator
    rule.updated_at = _now_naive()
    await db.flush()

    await write_audit(
        db,
        rule_id=rule.id,
        rule_code=rule.rule_code,
        operation_type="UPDATE",
        operator=operator,
        before_value=before_snapshot,
        after_value=_rule_to_dict(rule),
    )
    await invalidate_rule_cache(rule_id)
    return _rule_to_dict(rule)


async def toggle_rule(
    db: AsyncSession, rule_id: str, enabled: bool, operator: str
) -> dict[str, Any]:
    """启停规则。"""
    rule = await _get_rule_or_404(db, rule_id)
    before_snapshot = _rule_to_dict(rule)
    rule.is_enabled = enabled
    rule.updated_by = operator
    rule.updated_at = _now_naive()
    await db.flush()

    await write_audit(
        db,
        rule_id=rule.id,
        rule_code=rule.rule_code,
        operation_type="ENABLE" if enabled else "DISABLE",
        operator=operator,
        before_value=before_snapshot,
        after_value=_rule_to_dict(rule),
    )
    await invalidate_rule_cache(rule_id)
    return _rule_to_dict(rule)


async def delete_rule(db: AsyncSession, rule_id: str, operator: str) -> None:
    """删除规则（级联删除订阅，事件 rule_id SET NULL；预制规则禁删）。"""
    rule = await _get_rule_or_404(db, rule_id)
    if _is_preset_rule(rule):
        raise BizError(
            code="ERR_ALERT_RULE_PRESET_LOCKED",
            message="预制规则不允许删除，如不需要请停用",
            status_code=status.HTTP_403_FORBIDDEN,
        )
    before_snapshot = _rule_to_dict(rule)
    rule_code = rule.rule_code

    await db.delete(rule)
    await db.flush()

    await write_audit(
        db,
        rule_id=None,
        rule_code=rule_code,
        operation_type="DELETE",
        operator=operator,
        before_value=before_snapshot,
    )
    await invalidate_rule_cache(rule_id)
    await invalidate_all_cache()


async def get_rule(db: AsyncSession, rule_id: str) -> dict[str, Any]:
    rule = await _get_rule_or_404(db, rule_id)
    return _rule_to_dict(rule)


async def list_rules(
    db: AsyncSession,
    rule_type: str | None = None,
    is_enabled: bool | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    stmt = select(AlertRule).order_by(AlertRule.priority, AlertRule.created_at.desc())
    if rule_type:
        stmt = stmt.where(AlertRule.rule_type == rule_type)
    if is_enabled is not None:
        stmt = stmt.where(AlertRule.is_enabled.is_(is_enabled))

    count_stmt = select(func.count()).select_from(AlertRule)
    if rule_type:
        count_stmt = count_stmt.where(AlertRule.rule_type == rule_type)
    if is_enabled is not None:
        count_stmt = count_stmt.where(AlertRule.is_enabled.is_(is_enabled))

    total = (await db.execute(count_stmt)).scalar() or 0
    result = await db.execute(stmt.limit(limit).offset(offset))
    items = [_rule_to_dict(r) for r in result.scalars()]
    return {"total": total, "items": items}


async def _get_rule_or_404(db: AsyncSession, rule_id: str) -> AlertRule:
    result = await db.execute(select(AlertRule).where(AlertRule.id == rule_id))
    rule = result.scalar_one_or_none()
    if rule is None:
        raise BizError(
            code="ERR_ALERT_RULE_NOT_FOUND",
            message=f"规则 {rule_id} 不存在",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    return rule


# ---------------------------------------------------------------------------
# 预制规则种子（启动时幂等保证，lifespan 调用）
# ---------------------------------------------------------------------------


async def ensure_preset_rules(db: AsyncSession, operator: str = "system") -> int:
    """幂等确保 12 条评估/诊断指标预制规则存在（按 rule_code 去重）。

    同时为每条预制规则幂等补建 scope=ALL 全局订阅：周期巡检
    （alert_patrol）以订阅记录为遍历依据，无订阅则规则永不求值；
    预制规则模式下用户无手工订阅入口，故随种子自动下发。

    Returns:
        本次新建的规则数（0 = 已全部存在）。调用方负责 commit。
    """
    existing = set(
        (
            await db.execute(
                select(AlertRule.rule_code).where(
                    AlertRule.rule_code.like(f"{PRESET_RULE_CODE_PREFIX}%")
                )
            )
        ).scalars()
    )
    created = 0
    for preset in PRESET_RULES:
        if preset["rule_code"] in existing:
            continue
        db.add(
            AlertRule(
                rule_code=preset["rule_code"],
                rule_name=preset["rule_name"],
                rule_type="METRIC_THRESHOLD",
                dsl=preset["dsl"],
                description=preset.get("description"),
                priority=100,
                is_enabled=True,
                version=1,
                created_by=operator,
            )
        )
        created += 1
    if created:
        await db.flush()

    # 幂等补建 ALL 订阅（占位回路取第一个活跃回路，口径同 create_subscription）
    placeholder_loop_id = (
        await db.execute(select(LoopLedger.id).where(LoopLedger.is_active.is_(True)).limit(1))
    ).scalar_one_or_none()
    if placeholder_loop_id is not None:
        preset_rule_ids = (
            (
                await db.execute(
                    select(AlertRule.id).where(
                        AlertRule.rule_code.like(f"{PRESET_RULE_CODE_PREFIX}%")
                    )
                )
            )
            .scalars()
            .all()
        )
        subscribed_rule_ids = set(
            (
                await db.execute(
                    select(AlertRuleSubscription.rule_id).where(
                        AlertRuleSubscription.scope_type == "ALL",
                        AlertRuleSubscription.is_active.is_(True),
                    )
                )
            ).scalars()
        )
        subs_created = 0
        for rule_id in preset_rule_ids:
            if rule_id in subscribed_rule_ids:
                continue
            db.add(
                AlertRuleSubscription(
                    rule_id=rule_id,
                    loop_id=placeholder_loop_id,
                    scope_type="ALL",
                    is_active=True,
                    created_by=operator,
                )
            )
            subs_created += 1
        if subs_created:
            await db.flush()
            logger.info("预制预警规则已补建 ALL 订阅 %s 条", subs_created)

    if created:
        await invalidate_all_cache()
        logger.info("预制预警规则已创建 %s 条", created)
    return created


# ---------------------------------------------------------------------------
# 订阅 CRUD
# ---------------------------------------------------------------------------


async def create_subscription(
    db: AsyncSession, rule_id: str, operator: str, sub_data: dict[str, Any]
) -> dict[str, Any]:
    """创建订阅关系。

    scope_type=ALL 时 loop_id 可为任意活跃回路占位（求值时按 ALL 展开），
    但为保持外键完整性，ALL 订阅也需关联一个具体回路（取第一个活跃回路）。
    """
    await _get_rule_or_404(db, rule_id)

    scope_type = sub_data["scope_type"]
    loop_id = sub_data["loop_id"]

    # ALL 范围校验：loop_id 必须是活跃回路（占位）
    if scope_type == "ALL":
        loop = await db.execute(
            select(LoopLedger).where(LoopLedger.id == loop_id, LoopLedger.is_active.is_(True))
        )
        if loop.scalar_one_or_none() is None:
            raise BizError(
                code="ERR_ALERT_LOOP_NOT_FOUND",
                message=f"回路 {loop_id} 不存在或未激活",
                status_code=status.HTTP_404_NOT_FOUND,
            )

    # 唯一性检查（同一规则同一回路仅一条活跃订阅）
    existing = await db.execute(
        select(AlertRuleSubscription).where(
            AlertRuleSubscription.rule_id == rule_id,
            AlertRuleSubscription.loop_id == loop_id,
            AlertRuleSubscription.is_active.is_(True),
        )
    )
    if existing.scalar_one_or_none():
        raise BizError(
            code="ERR_ALERT_SUBSCRIPTION_EXISTS",
            message="该规则已订阅此回路",
            status_code=status.HTTP_409_CONFLICT,
        )

    sub = AlertRuleSubscription(
        rule_id=rule_id,
        loop_id=loop_id,
        scope_type=scope_type,
        scope_value=sub_data.get("scope_value"),
        is_active=True,
        created_by=operator,
    )
    db.add(sub)
    await db.flush()
    await invalidate_loop_cache(loop_id)
    return _subscription_to_dict(sub)


async def list_subscriptions(
    db: AsyncSession, rule_id: str | None = None, loop_id: str | None = None
) -> list[dict[str, Any]]:
    stmt = select(AlertRuleSubscription).where(AlertRuleSubscription.is_active.is_(True))
    if rule_id:
        stmt = stmt.where(AlertRuleSubscription.rule_id == rule_id)
    if loop_id:
        stmt = stmt.where(AlertRuleSubscription.loop_id == loop_id)
    stmt = stmt.order_by(AlertRuleSubscription.created_at.desc())
    result = await db.execute(stmt)
    return [_subscription_to_dict(s) for s in result.scalars()]


async def delete_subscription(db: AsyncSession, subscription_id: str, operator: str) -> None:
    result = await db.execute(
        select(AlertRuleSubscription).where(AlertRuleSubscription.id == subscription_id)
    )
    sub = result.scalar_one_or_none()
    if sub is None:
        raise BizError(
            code="ERR_ALERT_SUBSCRIPTION_NOT_FOUND",
            message=f"订阅 {subscription_id} 不存在",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    loop_id = sub.loop_id
    sub.is_active = False
    await db.flush()
    await invalidate_loop_cache(loop_id)


# ---------------------------------------------------------------------------
# 事件查询与处置
# ---------------------------------------------------------------------------


async def list_events(
    db: AsyncSession,
    loop_id: str | None = None,
    rule_id: str | None = None,
    severity: str | None = None,
    status_filter: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    stmt = select(AlertEvent, LoopLedger.tag_name).outerjoin(
        LoopLedger, LoopLedger.id == AlertEvent.loop_id
    )
    count_stmt = select(func.count()).select_from(AlertEvent)

    if loop_id:
        stmt = stmt.where(AlertEvent.loop_id == loop_id)
        count_stmt = count_stmt.where(AlertEvent.loop_id == loop_id)
    if rule_id:
        stmt = stmt.where(AlertEvent.rule_id == rule_id)
        count_stmt = count_stmt.where(AlertEvent.rule_id == rule_id)
    if severity:
        stmt = stmt.where(AlertEvent.severity == severity)
        count_stmt = count_stmt.where(AlertEvent.severity == severity)
    if status_filter:
        stmt = stmt.where(AlertEvent.status == status_filter)
        count_stmt = count_stmt.where(AlertEvent.status == status_filter)
    if start_time:
        stmt = stmt.where(AlertEvent.triggered_at >= start_time)
        count_stmt = count_stmt.where(AlertEvent.triggered_at >= start_time)
    if end_time:
        stmt = stmt.where(AlertEvent.triggered_at <= end_time)
        count_stmt = count_stmt.where(AlertEvent.triggered_at <= end_time)

    stmt = stmt.order_by(AlertEvent.triggered_at.desc()).limit(limit).offset(offset)
    total = (await db.execute(count_stmt)).scalar() or 0
    result = await db.execute(stmt)
    items = [_event_to_dict(e, name) for e, name in result.all()]
    return {"total": total, "items": items}


async def get_event(db: AsyncSession, event_id: str) -> dict[str, Any]:
    result = await db.execute(
        select(AlertEvent, LoopLedger.tag_name)
        .outerjoin(LoopLedger, LoopLedger.id == AlertEvent.loop_id)
        .where(AlertEvent.id == event_id)
    )
    row = result.first()
    if row is None:
        raise BizError(
            code="ERR_ALERT_EVENT_NOT_FOUND",
            message=f"事件 {event_id} 不存在",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    return _event_to_dict(row[0], row[1])


async def acknowledge_event(
    db: AsyncSession, event_id: str, operator: str, note: str | None = None
) -> dict[str, Any]:
    event = await _get_event_or_404(db, event_id)
    if event.status not in ("ACTIVE", "SUPPRESSED"):
        raise BizError(
            code="ERR_ALERT_EVENT_BAD_STATE",
            message=f"事件状态 {event.status} 不可确认",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    event.status = "ACKNOWLEDGED"
    event.acknowledged_by = operator
    event.acknowledged_at = _now_naive()
    if note:
        event.resolution_note = note
    await db.flush()
    return await get_event(db, event_id)


async def resolve_event(
    db: AsyncSession, event_id: str, operator: str, resolution_note: str
) -> dict[str, Any]:
    event = await _get_event_or_404(db, event_id)
    if event.status in ("RESOLVED", "ARCHIVED"):
        raise BizError(
            code="ERR_ALERT_EVENT_BAD_STATE",
            message=f"事件状态 {event.status} 不可处置",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    event.status = "RESOLVED"
    event.resolved_by = operator
    event.resolved_at = _now_naive()
    event.resolution_note = resolution_note
    await db.flush()
    return await get_event(db, event_id)


async def mark_false_positive(db: AsyncSession, event_id: str, is_fp: bool) -> dict[str, Any]:
    event = await _get_event_or_404(db, event_id)
    event.is_false_positive = is_fp
    await db.flush()
    return await get_event(db, event_id)


async def archive_event(db: AsyncSession, event_id: str) -> dict[str, Any]:
    event = await _get_event_or_404(db, event_id)
    if event.status != "RESOLVED":
        raise BizError(
            code="ERR_ALERT_EVENT_BAD_STATE",
            message="仅 RESOLVED 状态可归档",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    event.status = "ARCHIVED"
    await db.flush()
    return await get_event(db, event_id)


async def _get_event_or_404(db: AsyncSession, event_id: str) -> AlertEvent:
    result = await db.execute(select(AlertEvent).where(AlertEvent.id == event_id))
    event = result.scalar_one_or_none()
    if event is None:
        raise BizError(
            code="ERR_ALERT_EVENT_NOT_FOUND",
            message=f"事件 {event_id} 不存在",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    return event


# ---------------------------------------------------------------------------
# 手动抑制
# ---------------------------------------------------------------------------


async def create_suppression(
    db: AsyncSession, operator: str, sup_data: dict[str, Any]
) -> dict[str, Any]:
    now = _now_naive()
    sup = AlertSuppression(
        rule_id=sup_data.get("rule_id"),
        loop_id=sup_data.get("loop_id"),
        reason=sup_data["reason"],
        suppressed_by=operator,
        start_at=now,
        end_at=now + timedelta(minutes=sup_data["duration_minutes"]),
        is_active=True,
    )
    db.add(sup)
    await db.flush()
    return _suppression_to_dict(sup)


async def list_suppressions(
    db: AsyncSession,
    rule_id: str | None = None,
    loop_id: str | None = None,
    is_active: bool | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    stmt = select(AlertSuppression).order_by(AlertSuppression.created_at.desc())
    count_stmt = select(func.count()).select_from(AlertSuppression)
    if rule_id:
        stmt = stmt.where(AlertSuppression.rule_id == rule_id)
        count_stmt = count_stmt.where(AlertSuppression.rule_id == rule_id)
    if loop_id:
        stmt = stmt.where(AlertSuppression.loop_id == loop_id)
        count_stmt = count_stmt.where(AlertSuppression.loop_id == loop_id)
    if is_active is not None:
        stmt = stmt.where(AlertSuppression.is_active.is_(is_active))
        count_stmt = count_stmt.where(AlertSuppression.is_active.is_(is_active))

    total = (await db.execute(count_stmt)).scalar() or 0
    result = await db.execute(stmt.limit(limit).offset(offset))
    items = [_suppression_to_dict(s) for s in result.scalars()]
    return {"total": total, "items": items}


async def delete_suppression(db: AsyncSession, suppression_id: str, operator: str) -> None:
    result = await db.execute(select(AlertSuppression).where(AlertSuppression.id == suppression_id))
    sup = result.scalar_one_or_none()
    if sup is None:
        raise BizError(
            code="ERR_ALERT_SUPPRESSION_NOT_FOUND",
            message=f"抑制记录 {suppression_id} 不存在",
            status_code=status.HTTP_404_NOT_FOUND,
        )
    sup.is_active = False
    await db.flush()


# ---------------------------------------------------------------------------
# 审计日志
# ---------------------------------------------------------------------------


async def list_audit_logs(
    db: AsyncSession,
    rule_id: str | None = None,
    operator: str | None = None,
    operation_type: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    from app.services.alert_rule_engine.audit import list_audit_logs as _list

    logs = await _list(
        db,
        rule_id=rule_id,
        operator=operator,
        operation_type=operation_type,
        limit=limit,
        offset=offset,
    )
    count_stmt = select(func.count()).select_from(AlertRuleAuditLog)
    if rule_id:
        count_stmt = count_stmt.where(AlertRuleAuditLog.rule_id == rule_id)
    if operator:
        count_stmt = count_stmt.where(AlertRuleAuditLog.operator == operator)
    if operation_type:
        count_stmt = count_stmt.where(AlertRuleAuditLog.operation_type == operation_type)
    total = (await db.execute(count_stmt)).scalar() or 0
    return {"total": total, "items": [_audit_to_dict(log) for log in logs]}


# ---------------------------------------------------------------------------
# 全局开关
# ---------------------------------------------------------------------------


async def get_global_switch(db: AsyncSession) -> bool:
    """读取全局开关（sys_config）。默认 True。"""
    from app.models.sys_config import SysConfig

    result = await db.execute(select(SysConfig).where(SysConfig.key == _KEY_GLOBAL_SWITCH))
    cfg = result.scalar_one_or_none()
    if cfg is None:
        return True
    return cfg.value.lower() in ("true", "1", "yes")


async def set_global_switch(db: AsyncSession, enabled: bool, operator: str) -> None:
    """更新全局开关（sys_config upsert）。"""
    from app.models.sys_config import SysConfig

    result = await db.execute(select(SysConfig).where(SysConfig.key == _KEY_GLOBAL_SWITCH))
    cfg = result.scalar_one_or_none()
    now = _now_naive()
    value = "true" if enabled else "false"
    if cfg is None:
        cfg = SysConfig(
            key=_KEY_GLOBAL_SWITCH,
            value=value,
            description=_KEY_GLOBAL_SWITCH_DESC,
            updated_by=operator,
            updated_at=now,
        )
        db.add(cfg)
    else:
        cfg.value = value
        cfg.updated_by = operator
        cfg.updated_at = now
    await db.flush()
    await invalidate_all_cache()


# ---------------------------------------------------------------------------
# 徽章
# ---------------------------------------------------------------------------


async def get_badge_count(user_id: str) -> int:
    return await _suppressor.get_badge_count(user_id)


async def reset_badge(user_id: str) -> None:
    await _suppressor.reset_badge(user_id)


# ---------------------------------------------------------------------------
# Dry-Run 试运行
# ---------------------------------------------------------------------------


async def dry_run(
    db: AsyncSession,
    loop_id: str,
    rule_id: str | None = None,
    dsl: dict[str, Any] | None = None,
    confidence_level: str | None = None,
) -> dict[str, Any]:
    """规则试运行：对指定回路求值，不创建事件、不设冷却期、不触发动作。

    Args:
        db: 数据库会话
        loop_id: 目标回路 ID
        rule_id: 已有规则 ID（提供时使用该规则 DSL，忽略 dsl）
        dsl: 自定义 DSL（rule_id 未提供时必填）
        confidence_level: 模拟可信度等级（A/B/C/D/E）

    Returns:
        求值结果字典（triggered/triggered_value/condition_snapshot/severity/
        confidence_level/dedup_key/current_values）
    """
    from app.services.alert_rule_engine.dsl import validate_dsl
    from app.services.alert_rule_engine.evaluator import evaluate_rule

    # 构建 rule dict（evaluator 期望的格式）
    if rule_id:
        rule = await _get_rule_or_404(db, rule_id)
        rule_dict = _rule_to_dict(rule)
    else:
        if not dsl:
            raise BizError(
                status_code=status.HTTP_400_BAD_REQUEST,
                error_code="ALERT_DRY_RUN_NO_DSL",
                message="ruleId 和 dsl 至少提供一个",
            )
        # 校验 DSL
        validate_dsl(dsl)
        rule_dict = {
            "id": "dry-run",
            "rule_code": "DRY_RUN",
            "rule_name": "试运行",
            "rule_type": dsl.get("ruleType", "THRESHOLD"),
            "dsl": dsl,
            "is_enabled": True,
            "version": 1,
        }

    # 求值（不传 current_values，让 evaluator 从 Redis 取实时值）
    result = await evaluate_rule(
        db=db,
        rule=rule_dict,
        loop_id=loop_id,
        confidence_level=confidence_level,
    )

    # 附带当前值快照（方便前端展示）
    from app.services.alert_rule_engine.evaluator import _get_current_values

    current_values = await _get_current_values(loop_id)

    return {
        "triggered": result.triggered,
        "triggered_value": result.triggered_value,
        "condition_snapshot": result.condition_snapshot,
        "severity": result.severity,
        "confidence_level": result.confidence_level or confidence_level,
        "dedup_key": result.dedup_key,
        "current_values": current_values,
    }
