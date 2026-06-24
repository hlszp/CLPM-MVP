"""Performance evaluation schemas (IDS v3.2 §2.3 — S3-METRIC-001~006)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import Field

from app.schemas.base import CamelModel

# ---------------------------------------------------------------------------
# S3-METRIC-001: 指标配置
# ---------------------------------------------------------------------------


class MetricThreshold(CamelModel):
    """指标阈值配置。"""

    min: float | None = None
    max: float | None = None
    alert: float | None = None


class MetricConfigItem(CamelModel):
    """指标配置项（响应）。"""

    metricId: str
    metricCode: str
    metricName: str
    formula: str | None = None
    weight: Decimal | None = None
    threshold: dict[str, Any] | None = None
    controlType: str | None = None
    isEnabled: bool = True
    updatedBy: str | None = None
    updatedAt: str | None = None
    version: int = 1


class MetricConfigUpdate(CamelModel):
    """PUT /performance/metrics/{metricId} 请求体。"""

    metricName: str | None = Field(None, max_length=100)
    formula: str | None = Field(None, max_length=2000)
    weight: Decimal | None = Field(None, ge=0, le=100)
    threshold: dict[str, Any] | None = None
    controlType: str | None = Field(None, pattern="^(STABLE|SLOW|FAST|LOGIC)$")
    isEnabled: bool | None = None


# ---------------------------------------------------------------------------
# S3-METRIC-002: 引擎规则配置
# ---------------------------------------------------------------------------


class EngineRuleItem(CamelModel):
    """引擎规则项（响应）。"""

    ruleId: str
    ruleCode: str
    ruleName: str
    ruleType: str
    params: dict[str, Any] | None = None
    isEnabled: bool = True
    updatedBy: str | None = None
    updatedAt: str | None = None


class EngineRuleUpdate(CamelModel):
    """PUT /performance/rules/{ruleId} 请求体。"""

    ruleName: str | None = Field(None, max_length=100)
    params: dict[str, Any] | None = None
    isEnabled: bool | None = None


# ---------------------------------------------------------------------------
# S3-METRIC-004: 全局看板
# ---------------------------------------------------------------------------


class BoardFilterScope(CamelModel):
    """看板筛选范围。"""

    plantNodeId: str | None = None
    plantNodeName: str | None = None
    timeWindow: str = "today"


class KpiCard(CamelModel):
    """看板 KPI 卡片。"""

    metricKey: str
    metricName: str
    value: float | None = None
    unit: str = "%"
    status: str = "INCONCLUSIVE"
    algorithmVersion: str = "KPI_CALC_v1.0"


class KpiSummary(CamelModel):
    """看板 KPI 汇总。"""

    good_value_rate: float | None = None
    auto_mode_rate: float | None = None
    effective_auto_rate: float | None = None
    steady_rate: float | None = None
    accuracy_rate: float | None = None
    fast_response_rate: float | None = None
    oscillation_rate: float | None = None
    saturation_rate: float | None = None
    composite_score: float | None = None
    status: str = "INCONCLUSIVE"
    algorithm_version: str = "KPI_CALC_v1.0"


class TrendSeries(CamelModel):
    """趋势数据。"""

    timestamps: list[str] = Field(default_factory=list)
    values: list[float | None] = Field(default_factory=list)


class PartialWarning(CamelModel):
    """部分数据警告。"""

    active: bool = False
    inconclusiveCount: int = 0
    partialCount: int = 0
    message: str | None = None


class BoardData(CamelModel):
    """看板响应 data 块。"""

    filterScope: BoardFilterScope
    kpiCards: list[KpiCard]
    kpiSummary: KpiSummary
    steadyRateTrend: TrendSeries
    partialWarning: PartialWarning


# ---------------------------------------------------------------------------
# S3-METRIC-005: 低效回路排行
# ---------------------------------------------------------------------------


class RankingItem(CamelModel):
    """低效回路排行项。"""

    rank: int
    loopId: str
    tagName: str
    unitName: str | None = None
    compositeScore: float | None = None
    goodValueRate: float | None = None
    autoModeRate: float | None = None
    effectiveAutoRate: float | None = None
    steadyRate: float | None = None
    accuracyRate: float | None = None
    fastResponseRate: float | None = None
    oscillationRate: float | None = None
    saturationRate: float | None = None
    status: str = "INCONCLUSIVE"
    algorithmVersion: str = "KPI_CALC_v1.0"
    preDiagnosis: str | None = None
    actionStatus: str | None = None


# ---------------------------------------------------------------------------
# S3-METRIC-006: 统计报表
# ---------------------------------------------------------------------------


class AnalyticsFilterScope(CamelModel):
    """报表筛选范围。"""

    startTime: str
    endTime: str
    plantNodeId: str | None = None
    metricKey: str = "score"
    granularity: str = "day"


class KpiTrendSeries(CamelModel):
    """报表 KPI 趋势单条序列。"""

    metricKey: str
    metricName: str
    values: list[float | None] = Field(default_factory=list)


class KpiTrend(CamelModel):
    """报表 KPI 趋势。"""

    timestamps: list[str] = Field(default_factory=list)
    series: list[KpiTrendSeries] = Field(default_factory=list)


class UnitRankingItem(CamelModel):
    """单元排名项。"""

    unitId: str
    unitName: str | None = None
    score: float | None = None
    loopCount: int = 0


class BadActorItem(CamelModel):
    """坏演员分布项。"""

    label: str
    count: int = 0


class AnalyticsData(CamelModel):
    """报表响应 data 块。"""

    filterScope: AnalyticsFilterScope
    kpiTrend: KpiTrend
    unitRanking: list[UnitRankingItem] = Field(default_factory=list)
    badActorDistribution: list[BadActorItem] = Field(default_factory=list)


class ExportRequest(CamelModel):
    """POST /performance/analytics/export 请求体。"""

    startTime: str
    endTime: str
    plantNodeId: str | None = None
    metricKey: str = "score"
    granularity: str = "day"
    format: str = Field("csv", pattern="^(csv)$")


# ---------------------------------------------------------------------------
# 校验工具
# ---------------------------------------------------------------------------


class WeightSumValidator:
    """权重总和校验工具。

    6 大 KPI 权重总和必须为 100，否则抛出 ERR_METRIC_WEIGHT_SUM。
    """

    @staticmethod
    def validate(weights: list[Decimal]) -> None:
        """校验权重总和是否为 100。

        Raises:
            BizError: ERR_METRIC_WEIGHT_SUM
        """
        from app.core.exceptions import BizError

        total = sum(weights)
        if total != 100:
            raise BizError(
                code="ERR_METRIC_WEIGHT_SUM",
                message=f"指标权重总和必须为 100，当前为 {total}",
                status_code=400,
            )


__all__ = [
    "AnalyticsData",
    "AnalyticsFilterScope",
    "BadActorItem",
    "BoardData",
    "BoardFilterScope",
    "EngineRuleItem",
    "EngineRuleUpdate",
    "ExportRequest",
    "KpiCard",
    "KpiSummary",
    "KpiTrend",
    "KpiTrendSeries",
    "MetricConfigItem",
    "MetricConfigUpdate",
    "MetricThreshold",
    "PartialWarning",
    "RankingItem",
    "TrendSeries",
    "UnitRankingItem",
    "WeightSumValidator",
]
