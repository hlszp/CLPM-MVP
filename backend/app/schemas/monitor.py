"""监控模块 API schemas——关注队列与工作台摘要（整改方案 §8）。

关注队列统一聚合 ALERT / DEGRADATION / DATA_QUALITY / TRACKER / VERIFICATION
五类来源，不新增数据库主键，``attentionId`` 使用 ``${source}:${sourceId}``。

所有 schema 继承 CamelModel（snake_case 字段 → camelCase JSON）。
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.base import CamelModel

# ---------------------------------------------------------------------------
# 枚举
# ---------------------------------------------------------------------------

AttentionSource = Literal["ALERT", "DEGRADATION", "DATA_QUALITY", "TRACKER", "VERIFICATION"]
AttentionPriority = Literal["URGENT", "HIGH", "MEDIUM", "LOW"]
AttentionStatus = Literal["OPEN", "ACKNOWLEDGED", "SUPPRESSED", "IN_PROGRESS", "VERIFYING"]
ConfidenceLevel = Literal["A", "B", "C", "D", "E"]

AttentionActionType = Literal[
    "VIEW_DETAIL",
    "OPEN_WORKBENCH",
    "ACKNOWLEDGE",
    "RESOLVE",
    "MARK_FALSE_POSITIVE",
    "CREATE_TRACKER",
    "VIEW_ALERT_HISTORY",
    "BACK_TO_OVERVIEW",
]


# ---------------------------------------------------------------------------
# 动作
# ---------------------------------------------------------------------------


class AttentionActionTarget(CamelModel):
    """动作跳转目标。"""

    route: Literal["/monitor/loop-workbench", "/monitor/alerts", "/dashboard/workbench"]
    query: dict[str, str] = Field(default_factory=dict, description="URL query 参数")


class AttentionAction(CamelModel):
    """关注项动作（服务端按角色和来源状态生成）。"""

    type: AttentionActionType
    label: str = Field(..., description="动作按钮文案")
    enabled: bool = Field(True, description="是否可执行")
    disabled_reason: str | None = Field(None, description="禁用原因（enabled=false 时必填）")
    target: AttentionActionTarget | None = Field(
        None, description="跳转目标（VIEW/OPEN 类动作必填）"
    )


# ---------------------------------------------------------------------------
# 关注项
# ---------------------------------------------------------------------------


class AttentionItem(CamelModel):
    """关注队列单项。"""

    attention_id: str = Field(..., description="关注项 ID：${source}:${sourceId}")
    source: AttentionSource
    source_id: str = Field(..., description="来源原始主键")
    loop_id: str
    tag_name: str
    unit_name: str | None = None
    title: str = Field(..., description="标题（一句话摘要）")
    summary: str = Field(..., description="详细摘要")
    priority: AttentionPriority
    source_severity: str | None = Field(
        None, description="来源原始严重等级（INFO/WARN/ERROR/CRITICAL）"
    )
    status: AttentionStatus
    source_status: str = Field(..., description="来源原始状态（ACTIVE/PENDING 等，便于审计解释）")
    rank_reasons: list[str] = Field(
        default_factory=list, description="排序原因（至少一条可读原因）"
    )
    occurred_at: str = Field(..., description="发生时间 ISO8601")
    updated_at: str | None = None
    confidence_level: ConfidenceLevel | None = None
    score: float | None = None
    score_delta: float | None = None
    event_id: str | None = None
    tracker_id: str | None = None
    task_id: str | None = None
    primary_action: AttentionAction
    actions: list[AttentionAction] = Field(default_factory=list)


class AttentionListData(CamelModel):
    """关注队列分页响应。"""

    items: list[AttentionItem]
    total: int
    page: int
    page_size: int
    aggregates: dict = Field(
        default_factory=dict,
        description="聚合统计：按来源/优先级/状态计数",
    )
