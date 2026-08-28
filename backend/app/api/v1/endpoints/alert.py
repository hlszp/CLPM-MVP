"""智能预警规则引擎 API 端点（方案 §6 + IDS v2.7）。

路由清单：
- 规则 CRUD
    GET    /alert/rules                         规则列表
    POST   /alert/rules                         创建规则（ADMIN）
    GET    /alert/rules/{ruleId}                规则详情
    PUT    /alert/rules/{ruleId}                更新规则（ADMIN）
    DELETE /alert/rules/{ruleId}                删除规则（ADMIN）
    PATCH  /alert/rules/{ruleId}/toggle         启停规则（ADMIN）
- 订阅 CRUD
    GET    /alert/rules/{ruleId}/subscriptions  规则的订阅列表
    POST   /alert/rules/{ruleId}/subscriptions  创建订阅（ADMIN/IC_ENGINEER）
    GET    /alert/subscriptions                 订阅列表（可按 loopId 筛选）
    DELETE /alert/subscriptions/{subId}         删除订阅（ADMIN/IC_ENGINEER）
- 事件查询与处置
    GET    /alert/events                        事件列表（分页+筛选）
    GET    /alert/events/{eventId}              事件详情
    POST   /alert/events/{eventId}/acknowledge  确认事件
    POST   /alert/events/{eventId}/resolve      处置事件
    POST   /alert/events/{eventId}/false-positive 标记误报
    POST   /alert/events/{eventId}/archive      归档事件
- 手动抑制
    GET    /alert/suppressions                  抑制记录列表
    POST   /alert/suppressions                  创建抑制（ADMIN/IC_ENGINEER）
    DELETE /alert/suppressions/{supId}          删除抑制（ADMIN/IC_ENGINEER）
- 审计日志
    GET    /alert/audit-logs                    规则变更审计
- 全局开关
    GET    /alert/global-switch                 读取全局开关
    PUT    /alert/global-switch                 更新全局开关（ADMIN）
- 徽章
    GET    /alert/badge                         当前用户未读计数
    POST   /alert/badge/reset                   重置未读计数
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_perms, require_roles
from app.core.db import get_db
from app.models.sys_user import SysUser
from app.schemas.alert import (
    AlertAuditLogListData,
    AlertBadgeCount,
    AlertDryRunRequest,
    AlertEventAcknowledge,
    AlertEventFalsePositive,
    AlertEventItem,
    AlertEventListData,
    AlertEventResolve,
    AlertGlobalSwitch,
    AlertRuleCreate,
    AlertRuleItem,
    AlertRuleListData,
    AlertRuleUpdate,
    AlertSubscriptionCreate,
    AlertSubscriptionItem,
    AlertSuppressionCreate,
    AlertSuppressionItem,
    AlertSuppressionListData,
)
from app.schemas.common import ApiResponse, success
from app.services.alert_rule_engine import service as alert_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/alert", tags=["alert"])


# ===========================================================================
# 规则 CRUD
# ===========================================================================


@router.get("/rules", response_model=ApiResponse[AlertRuleListData])
async def list_rules_endpoint(
    ruleType: str | None = Query(None, description="规则类型筛选"),
    isEnabled: bool | None = Query(None, description="启用状态筛选"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_perms("alert:view")),
) -> dict:
    """获取预警规则列表（所有角色可查看）。"""
    data = await alert_service.list_rules(
        db, rule_type=ruleType, is_enabled=isEnabled, limit=limit, offset=offset
    )
    return success(data=data)


@router.post("/rules", response_model=ApiResponse[AlertRuleItem])
async def create_rule_endpoint(
    body: AlertRuleCreate,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """创建预警规则（仅 ADMIN）。"""
    data = await alert_service.create_rule(db, user.username, body.model_dump())
    await db.commit()
    return success(data=data, message="规则已创建")


@router.get("/rules/{rule_id}", response_model=ApiResponse[AlertRuleItem])
async def get_rule_endpoint(
    rule_id: str,
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_perms("alert:view")),
) -> dict:
    """获取规则详情。"""
    data = await alert_service.get_rule(db, rule_id)
    return success(data=data)


@router.put("/rules/{rule_id}", response_model=ApiResponse[AlertRuleItem])
async def update_rule_endpoint(
    rule_id: str,
    body: AlertRuleUpdate,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """更新预警规则（仅 ADMIN）。"""
    data = await alert_service.update_rule(
        db, rule_id, user.username, body.model_dump(exclude_unset=True)
    )
    await db.commit()
    return success(data=data, message="规则已更新")


@router.delete("/rules/{rule_id}")
async def delete_rule_endpoint(
    rule_id: str,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """删除预警规则（仅 ADMIN）。"""
    await alert_service.delete_rule(db, rule_id, user.username)
    await db.commit()
    return success(data=None, message="规则已删除")


@router.put("/rules/{rule_id}/toggle", response_model=ApiResponse[AlertRuleItem])
async def toggle_rule_endpoint(
    rule_id: str,
    enabled: bool = Query(..., description="true=启用，false=停用"),
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """启停规则（仅 ADMIN）。"""
    data = await alert_service.toggle_rule(db, rule_id, enabled, user.username)
    await db.commit()
    return success(data=data, message="规则状态已更新")


@router.post("/rules/dry-run", response_model=ApiResponse[dict])
async def dry_run_rule_endpoint(
    body: AlertDryRunRequest,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER")),
) -> dict:
    """规则试运行：对指定回路求值，不创建事件、不设冷却期、不触发动作。

    支持两种模式：
    1. 传入 ruleId：使用已存在的规则 DSL 试运行
    2. 传入 dsl：使用自定义 DSL 试运行（先校验合法性）
    """
    data = await alert_service.dry_run(
        db,
        loop_id=body.loop_id,
        rule_id=body.rule_id,
        dsl=body.dsl,
        confidence_level=body.confidence_level,
    )
    return success(data=data, message="试运行完成")


# ===========================================================================
# 订阅 CRUD
# ===========================================================================


@router.get(
    "/rules/{rule_id}/subscriptions",
    response_model=ApiResponse[list[AlertSubscriptionItem]],
)
async def list_rule_subscriptions_endpoint(
    rule_id: str,
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_perms("alert:view")),
) -> dict:
    """获取规则的订阅列表。"""
    data = await alert_service.list_subscriptions(db, rule_id=rule_id)
    return success(data=data)


@router.post(
    "/rules/{rule_id}/subscriptions",
    response_model=ApiResponse[AlertSubscriptionItem],
)
async def create_subscription_endpoint(
    rule_id: str,
    body: AlertSubscriptionCreate,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER")),
) -> dict:
    """创建订阅关系（ADMIN/IC_ENGINEER）。"""
    data = await alert_service.create_subscription(db, rule_id, user.username, body.model_dump())
    await db.commit()
    return success(data=data, message="订阅已创建")


@router.get(
    "/subscriptions",
    response_model=ApiResponse[list[AlertSubscriptionItem]],
)
async def list_subscriptions_endpoint(
    loopId: str | None = Query(None, description="按回路筛选"),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_perms("alert:view")),
) -> dict:
    """获取订阅列表（可按回路筛选）。"""
    data = await alert_service.list_subscriptions(db, loop_id=loopId)
    return success(data=data)


@router.delete("/subscriptions/{subscription_id}")
async def delete_subscription_endpoint(
    subscription_id: str,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER")),
) -> dict:
    """删除订阅关系（ADMIN/IC_ENGINEER）。"""
    await alert_service.delete_subscription(db, subscription_id, user.username)
    await db.commit()
    return success(data=None, message="订阅已删除")


# ===========================================================================
# 事件查询与处置
# ===========================================================================


@router.get("/events", response_model=ApiResponse[AlertEventListData])
async def list_events_endpoint(
    loopId: str | None = Query(None),
    ruleId: str | None = Query(None),
    severity: str | None = Query(None),
    status: str | None = Query(None, alias="status", description="事件状态"),
    startTime: datetime | None = Query(None),
    endTime: datetime | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_perms("alert:view")),
) -> dict:
    """获取预警事件列表（分页+筛选）。

    startTime/endTime 归一为 naive UTC（alert_event 时间戳列为 timestamp
    without time zone；asyncpg 不接受 aware 与 naive 混比）。
    """
    if startTime is not None and startTime.tzinfo is not None:
        startTime = startTime.astimezone(UTC).replace(tzinfo=None)
    if endTime is not None and endTime.tzinfo is not None:
        endTime = endTime.astimezone(UTC).replace(tzinfo=None)
    data = await alert_service.list_events(
        db,
        loop_id=loopId,
        rule_id=ruleId,
        severity=severity,
        status_filter=status,
        start_time=startTime,
        end_time=endTime,
        limit=limit,
        offset=offset,
    )
    return success(data=data)


@router.get("/events/{event_id}", response_model=ApiResponse[AlertEventItem])
async def get_event_endpoint(
    event_id: str,
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_perms("alert:view")),
) -> dict:
    """获取事件详情。"""
    data = await alert_service.get_event(db, event_id)
    return success(data=data)


@router.post("/events/{event_id}/acknowledge", response_model=ApiResponse[AlertEventItem])
async def acknowledge_event_endpoint(
    event_id: str,
    body: AlertEventAcknowledge,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER")),
) -> dict:
    """确认预警事件（ADMIN/IC_ENGINEER）。"""
    data = await alert_service.acknowledge_event(db, event_id, user.username, body.note)
    await db.commit()
    return success(data=data, message="事件已确认")


@router.post("/events/{event_id}/resolve", response_model=ApiResponse[AlertEventItem])
async def resolve_event_endpoint(
    event_id: str,
    body: AlertEventResolve,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER")),
) -> dict:
    """处置预警事件（ADMIN/IC_ENGINEER）。"""
    data = await alert_service.resolve_event(db, event_id, user.username, body.resolution_note)
    await db.commit()
    return success(data=data, message="事件已处置")


@router.post("/events/{event_id}/false-positive", response_model=ApiResponse[AlertEventItem])
async def mark_false_positive_endpoint(
    event_id: str,
    body: AlertEventFalsePositive,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER")),
) -> dict:
    """标记事件为误报（ADMIN/IC_ENGINEER）。"""
    data = await alert_service.mark_false_positive(db, event_id, body.is_false_positive)
    await db.commit()
    return success(data=data, message="误报标记已更新")


@router.post("/events/{event_id}/archive", response_model=ApiResponse[AlertEventItem])
async def archive_event_endpoint(
    event_id: str,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """归档事件（仅 ADMIN）。"""
    data = await alert_service.archive_event(db, event_id)
    await db.commit()
    return success(data=data, message="事件已归档")


# ===========================================================================
# 手动抑制
# ===========================================================================


@router.get("/suppressions", response_model=ApiResponse[AlertSuppressionListData])
async def list_suppressions_endpoint(
    ruleId: str | None = Query(None),
    loopId: str | None = Query(None),
    isActive: bool | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_perms("alert:view")),
) -> dict:
    """获取手动抑制记录列表。"""
    data = await alert_service.list_suppressions(
        db,
        rule_id=ruleId,
        loop_id=loopId,
        is_active=isActive,
        limit=limit,
        offset=offset,
    )
    return success(data=data)


@router.post("/suppressions", response_model=ApiResponse[AlertSuppressionItem])
async def create_suppression_endpoint(
    body: AlertSuppressionCreate,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER")),
) -> dict:
    """创建手动抑制（ADMIN/IC_ENGINEER）。"""
    data = await alert_service.create_suppression(db, user.username, body.model_dump())
    await db.commit()
    return success(data=data, message="抑制已创建")


@router.delete("/suppressions/{suppression_id}")
async def delete_suppression_endpoint(
    suppression_id: str,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN", "IC_ENGINEER")),
) -> dict:
    """删除手动抑制（ADMIN/IC_ENGINEER）。"""
    await alert_service.delete_suppression(db, suppression_id, user.username)
    await db.commit()
    return success(data=None, message="抑制已删除")


# ===========================================================================
# 审计日志
# ===========================================================================


@router.get("/audit-logs", response_model=ApiResponse[AlertAuditLogListData])
async def list_audit_logs_endpoint(
    ruleId: str | None = Query(None),
    operator: str | None = Query(None),
    operationType: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_perms("alert:view")),
) -> dict:
    """获取规则变更审计日志。"""
    data = await alert_service.list_audit_logs(
        db,
        rule_id=ruleId,
        operator=operator,
        operation_type=operationType,
        limit=limit,
        offset=offset,
    )
    return success(data=data)


# ===========================================================================
# 全局开关
# ===========================================================================


@router.get("/global-switch", response_model=ApiResponse[AlertGlobalSwitch])
async def get_global_switch_endpoint(
    db: AsyncSession = Depends(get_db),
    _: SysUser = Depends(require_perms("alert:view")),
) -> dict:
    """读取预警引擎全局开关。"""
    enabled = await alert_service.get_global_switch(db)
    return success(data={"enabled": enabled})


@router.put("/global-switch", response_model=ApiResponse[AlertGlobalSwitch])
async def set_global_switch_endpoint(
    body: AlertGlobalSwitch,
    db: AsyncSession = Depends(get_db),
    user: SysUser = Depends(require_roles("ADMIN")),
) -> dict:
    """更新预警引擎全局开关（仅 ADMIN）。"""
    await alert_service.set_global_switch(db, body.enabled, user.username)
    await db.commit()
    return success(data={"enabled": body.enabled}, message="全局开关已更新")


# ===========================================================================
# 徽章
# ===========================================================================


@router.get("/badge", response_model=ApiResponse[AlertBadgeCount])
async def get_badge_endpoint(
    user: SysUser = Depends(get_current_user),
) -> dict:
    """获取当前用户未读预警事件计数。"""
    count = await alert_service.get_badge_count(user.id)
    return success(data={"count": count})


@router.post("/badge/reset", response_model=ApiResponse[AlertBadgeCount])
async def reset_badge_endpoint(
    user: SysUser = Depends(get_current_user),
) -> dict:
    """重置当前用户未读预警事件计数（查看事件列表后调用）。"""
    await alert_service.reset_badge(user.id)
    return success(data={"count": 0})


__all__ = ["router"]
