"""Dashboard aggregation schemas (IDS v3.2 §2 — S6-PORTAL-001 BFF 层).

工作台聚合 API 响应 Schema，遵循 API 契约：
- 6 大 KPI 卡片（自控投用率/平稳率/综合评分/报警次数/操作频次/好值率）
- 低效回路 Top 10
- 回路趋势摘要（最近 7 天每日综合评分）
- 待处理异常数（未关闭诊断 + 未处理 Action Tracker）
"""

from __future__ import annotations

from pydantic import ConfigDict, Field

from app.schemas.base import CamelModel

# ---------------------------------------------------------------------------
# KPI 卡片
# ---------------------------------------------------------------------------


class KpiCardData(CamelModel):
    """单个 KPI 卡片数据。

    Attributes:
        value: 当前值
        unit: 单位（%/次/分）
        trend: 趋势枚举 up/down/stable
        delta: 与上一周期差值
    """

    value: float | int | None = None
    unit: str = ""
    trend: str = "stable"
    delta: float | int = 0.0


class KpiCards(CamelModel):
    """6 大 KPI 卡片集合。"""

    auto_mode_rate: KpiCardData
    steady_rate: KpiCardData
    composite_score: KpiCardData
    alarm_count: KpiCardData
    operation_count: KpiCardData
    good_value_rate: KpiCardData


# ---------------------------------------------------------------------------
# 低效回路 Top 10
# ---------------------------------------------------------------------------


class LoopKeyMetric(CamelModel):
    """回路关键指标摘要。"""

    auto_mode_rate: float | None = None
    steady_rate: float | None = None


class InefficientLoopItem(CamelModel):
    """低效回路列表项。"""

    loop_id: str
    loop_tag: str | None = None
    loop_name: str | None = None
    plant_name: str | None = None
    composite_score: float | None = None
    diagnosis_labels: list[str] = Field(default_factory=list)
    key_metric: LoopKeyMetric


# ---------------------------------------------------------------------------
# 回路趋势摘要
# ---------------------------------------------------------------------------


class TrendSummary(CamelModel):
    """回路趋势摘要（最近 7 天每日综合评分）。"""

    dates: list[str] = Field(default_factory=list)
    composite_scores: list[float | None] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 待处理异常
# ---------------------------------------------------------------------------


class PendingAlerts(CamelModel):
    """待处理异常数。"""

    open_diagnoses: int = 0
    open_trackers: int = 0


# ---------------------------------------------------------------------------
# 工作台聚合响应
# ---------------------------------------------------------------------------


class DashboardFilterScope(CamelModel):
    """工作台筛选范围。"""

    plant_id: str | None = None
    plant_name: str | None = None
    granularity: str = "day"
    user_role: str = ""


class DashboardOverview(CamelModel):
    """工作台聚合响应 data 块。"""

    filter_scope: DashboardFilterScope
    kpi_cards: KpiCards
    inefficient_loops: list[InefficientLoopItem] = Field(default_factory=list)
    trend_summary: TrendSummary
    pending_alerts: PendingAlerts
    cached: bool = False

    model_config = ConfigDict(
        alias_generator=CamelModel.model_config["alias_generator"],
        populate_by_name=True,
        from_attributes=True,
        extra="allow",
    )


# ---------------------------------------------------------------------------
# 装置总览管理者版：治理聚合（GET /dashboard/governance-summary）
# ---------------------------------------------------------------------------


class GovernanceHandlingSummary(CamelModel):
    """处置闭环计数（双实体：loop_action_item 建议 + handling_order 工单）。

    Attributes:
        open_items: 未闭环处置建议数（status ∈ PENDING/ACCEPTED）
        open_orders: 未闭环处置工单数（status ∈ PENDING/EXECUTING/VERIFYING/REOPENED）
        overdue_orders: 超期未闭环工单数（口径同 monitor_attention HANDLING 来源）
        closed_in_window: 时间窗内闭环的工单数（status=CLOSED 且 verified_at ∈ 窗口）
    """

    open_items: int = 0
    open_orders: int = 0
    overdue_orders: int = 0
    closed_in_window: int = 0


class GovernanceFunnel(CamelModel):
    """治理漏斗：发现 → 诊断 → 方案 → 闭环。"""

    discovered: int = 0
    diagnosed: int = 0
    planned: int = 0
    closed: int = 0


class GovernanceBadLoops(CamelModel):
    """最新等级分布中的问题回路计数（WARNING/POOR 档）。"""

    warning: int = 0
    poor: int = 0


class GovernanceSummary(CamelModel):
    """GET /dashboard/governance-summary 响应 data 块。"""

    time_window: str
    handling: GovernanceHandlingSummary
    funnel: GovernanceFunnel
    bad_loops: GovernanceBadLoops


__all__ = [
    "DashboardFilterScope",
    "DashboardOverview",
    "GovernanceBadLoops",
    "GovernanceFunnel",
    "GovernanceHandlingSummary",
    "GovernanceSummary",
    "InefficientLoopItem",
    "KpiCardData",
    "KpiCards",
    "LoopKeyMetric",
    "PendingAlerts",
    "TrendSummary",
]
