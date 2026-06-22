"""Dashboard aggregation schemas (IDS v3.2 §2 — S6-PORTAL-001 BFF 层).

工作台聚合 API 响应 Schema，遵循 API 契约：
- 6 大 KPI 卡片（自控投用率/平稳率/综合评分/报警次数/操作频次/好值率）
- 低效回路 Top 10
- 回路趋势摘要（最近 7 天每日综合评分）
- 待处理异常数（未关闭诊断 + 未处理 Action Tracker）
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# KPI 卡片
# ---------------------------------------------------------------------------


class KpiCardData(BaseModel):
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


class KpiCards(BaseModel):
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


class LoopKeyMetric(BaseModel):
    """回路关键指标摘要。"""

    auto_mode_rate: float | None = None
    steady_rate: float | None = None


class InefficientLoopItem(BaseModel):
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


class TrendSummary(BaseModel):
    """回路趋势摘要（最近 7 天每日综合评分）。"""

    dates: list[str] = Field(default_factory=list)
    composite_scores: list[float | None] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 待处理异常
# ---------------------------------------------------------------------------


class PendingAlerts(BaseModel):
    """待处理异常数。"""

    open_diagnoses: int = 0
    open_trackers: int = 0


# ---------------------------------------------------------------------------
# 工作台聚合响应
# ---------------------------------------------------------------------------


class DashboardFilterScope(BaseModel):
    """工作台筛选范围。"""

    plant_id: str | None = None
    plant_name: str | None = None
    granularity: str = "day"
    user_role: str = ""


class DashboardOverview(BaseModel):
    """工作台聚合响应 data 块。"""

    filter_scope: DashboardFilterScope
    kpi_cards: KpiCards
    inefficient_loops: list[InefficientLoopItem] = Field(default_factory=list)
    trend_summary: TrendSummary
    pending_alerts: PendingAlerts
    cached: bool = False

    model_config: dict[str, Any] = {"extra": "allow"}


__all__ = [
    "DashboardFilterScope",
    "DashboardOverview",
    "InefficientLoopItem",
    "KpiCardData",
    "KpiCards",
    "LoopKeyMetric",
    "PendingAlerts",
    "TrendSummary",
]
