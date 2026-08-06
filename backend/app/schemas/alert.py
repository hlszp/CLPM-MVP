"""智能预警规则引擎 API schemas（方案 §6 + IDS v2.7）。

覆盖：规则 CRUD / 订阅 CRUD / 事件查询与处置 / 手动抑制 / 审计日志。
所有 schema 继承 CamelModel（snake_case 字段 → camelCase JSON）。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.schemas.base import CamelModel

# ---------------------------------------------------------------------------
# 枚举
# ---------------------------------------------------------------------------

RuleType = Literal["THRESHOLD", "DRIFT", "COMPOSITE", "CONFIDENCE"]
ScopeType = Literal["ALL", "LOOP", "PLANT", "CONTROL_TYPE"]
Severity = Literal["INFO", "WARN", "ERROR", "CRITICAL"]
EventStatus = Literal["ACTIVE", "ACKNOWLEDGED", "RESOLVED", "SUPPRESSED", "ARCHIVED"]
AuditOperationType = Literal["CREATE", "UPDATE", "ENABLE", "DISABLE", "DELETE"]


# ---------------------------------------------------------------------------
# 规则定义
# ---------------------------------------------------------------------------


class AlertRuleCreate(CamelModel):
    """POST /alert/rules 请求体。"""

    rule_code: str = Field(..., max_length=50, description="规则代码（唯一）")
    rule_name: str = Field(..., max_length=100, description="规则名称")
    rule_type: RuleType
    dsl: dict[str, Any] = Field(..., description="规则 DSL（结构见方案 §3）")
    description: str | None = Field(None, max_length=500)
    priority: int = Field(100, ge=1, description="优先级（越小越高）")
    is_enabled: bool = True


class AlertRuleUpdate(CamelModel):
    """PUT /alert/rules/{ruleId} 请求体。"""

    rule_name: str | None = Field(None, max_length=100)
    dsl: dict[str, Any] | None = None
    description: str | None = Field(None, max_length=500)
    priority: int | None = Field(None, ge=1)
    is_enabled: bool | None = None


class AlertRuleItem(CamelModel):
    """规则定义响应项。"""

    rule_id: str
    rule_code: str
    rule_name: str
    rule_type: RuleType
    dsl: dict[str, Any]
    description: str | None = None
    priority: int
    is_enabled: bool
    version: int
    created_by: str
    created_at: str | None = None
    updated_by: str | None = None
    updated_at: str | None = None


class AlertRuleListData(CamelModel):
    """规则分页列表响应。"""

    total: int
    items: list[AlertRuleItem]


# ---------------------------------------------------------------------------
# 订阅关系
# ---------------------------------------------------------------------------


class AlertSubscriptionCreate(CamelModel):
    """POST /alert/rules/{ruleId}/subscriptions 请求体。"""

    loop_id: str = Field(..., description="回路 ID")
    scope_type: ScopeType = Field(..., description="订阅范围类型")
    scope_value: str | None = Field(None, max_length=100)


class AlertSubscriptionItem(CamelModel):
    """订阅关系响应项。"""

    subscription_id: str
    rule_id: str
    loop_id: str
    scope_type: ScopeType
    scope_value: str | None = None
    is_active: bool
    created_by: str
    created_at: str | None = None


# ---------------------------------------------------------------------------
# 预警事件
# ---------------------------------------------------------------------------


class AlertEventItem(CamelModel):
    """预警事件响应项。"""

    event_id: str
    rule_id: str | None = None
    rule_code: str
    rule_version: int
    loop_id: str
    severity: Severity
    status: EventStatus
    trigger_condition_snapshot: dict[str, Any]
    data_window: dict[str, Any] | None = None
    triggered_value: float | None = None
    confidence_level: str | None = None
    rule_dsl_snapshot: dict[str, Any]
    tracker_id: str | None = None
    is_false_positive: bool | None = None
    trigger_count: int
    triggered_at: str
    acknowledged_by: str | None = None
    acknowledged_at: str | None = None
    resolved_by: str | None = None
    resolved_at: str | None = None
    resolution_note: str | None = None
    loop_name: str | None = None


class AlertEventListData(CamelModel):
    """事件分页列表响应。"""

    total: int
    items: list[AlertEventItem]


class AlertEventAcknowledge(CamelModel):
    """POST /alert/events/{eventId}/acknowledge 请求体。"""

    note: str | None = Field(None, max_length=500)


class AlertEventResolve(CamelModel):
    """POST /alert/events/{eventId}/resolve 请求体。"""

    resolution_note: str = Field(..., max_length=500, description="处置说明")


class AlertEventFalsePositive(CamelModel):
    """POST /alert/events/{eventId}/false-positive 请求体。"""

    is_false_positive: bool = Field(..., description="true=标记误报，false=取消")


# ---------------------------------------------------------------------------
# 手动抑制
# ---------------------------------------------------------------------------


class AlertSuppressionCreate(CamelModel):
    """POST /alert/suppressions 请求体。"""

    rule_id: str | None = Field(None, description="规则 ID（None=全规则）")
    loop_id: str | None = Field(None, description="回路 ID（None=全回路）")
    reason: str = Field(..., max_length=500)
    duration_minutes: int = Field(..., ge=1, le=43200, description="抑制时长（分钟）")


class AlertSuppressionItem(CamelModel):
    """手动抑制记录响应项。"""

    suppression_id: str
    rule_id: str | None = None
    loop_id: str | None = None
    reason: str
    suppressed_by: str
    start_at: str
    end_at: str
    is_active: bool
    created_at: str | None = None


class AlertSuppressionListData(CamelModel):
    """抑制记录分页列表响应。"""

    total: int
    items: list[AlertSuppressionItem]


# ---------------------------------------------------------------------------
# 审计日志
# ---------------------------------------------------------------------------


class AlertAuditLogItem(CamelModel):
    """规则变更审计日志响应项。"""

    log_id: str
    rule_id: str | None = None
    rule_code: str
    operation_type: AuditOperationType
    before_value: str | None = None
    after_value: str | None = None
    operator: str
    operated_at: str


class AlertAuditLogListData(CamelModel):
    """审计日志分页列表响应。"""

    total: int
    items: list[AlertAuditLogItem]


# ---------------------------------------------------------------------------
# 全局开关
# ---------------------------------------------------------------------------


class AlertGlobalSwitch(CamelModel):
    """预警引擎全局开关（sys_config）。"""

    enabled: bool = Field(True, description="全局开关：false=暂停所有预警")


# ---------------------------------------------------------------------------
# 徽章计数
# ---------------------------------------------------------------------------


class AlertBadgeCount(CamelModel):
    """用户未读预警事件计数。"""

    count: int
