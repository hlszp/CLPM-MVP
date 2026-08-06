"""预警规则引擎 CRUD 服务（方案 §6 API 业务逻辑）。

封装规则/订阅/事件/抑制/审计的数据库操作，endpoint 层只做参数校验和调用。
所有写操作在同一事务中完成（含审计日志），由调用方在 endpoint 提交。
"""

from __future__ import annotations

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
    """创建预警规则。"""
    # DSL 校验（可能抛 ValidationError）
    validate_dsl(rule_data["dsl"])

    # rule_code 唯一性检查
    existing = await db.execute(
        select(AlertRule).where(AlertRule.rule_code == rule_data["rule_code"])
    )
    if existing.scalar_one_or_none():
        raise BizError(
            code="ERR_ALERT_RULE_CODE_EXISTS",
            message=f"规则代码 {rule_data['rule_code']} 已存在",
            status_code=status.HTTP_409_CONFLICT,
        )

    rule = AlertRule(
        rule_code=rule_data["rule_code"],
        rule_name=rule_data["rule_name"],
        rule_type=rule_data["rule_type"],
        dsl=rule_data["dsl"],
        description=rule_data.get("description"),
        priority=rule_data.get("priority", 100),
        is_enabled=rule_data.get("is_enabled", True),
        version=1,
        created_by=operator,
    )
    db.add(rule)
    await db.flush()

    await write_audit(
        db,
        rule_id=rule.id,
        rule_code=rule.rule_code,
        operation_type="CREATE",
        operator=operator,
        after_value=_rule_to_dict(rule),
    )
    return _rule_to_dict(rule)


async def update_rule(
    db: AsyncSession, rule_id: str, operator: str, rule_data: dict[str, Any]
) -> dict[str, Any]:
    """更新预警规则。"""
    rule = await _get_rule_or_404(db, rule_id)
    before_snapshot = _rule_to_dict(rule)

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
    """删除规则（级联删除订阅，事件 rule_id SET NULL）。"""
    rule = await _get_rule_or_404(db, rule_id)
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
