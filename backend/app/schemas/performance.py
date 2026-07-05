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
    # v5.3 P3-T9：formula 参数废弃（接收但忽略写入，算法已固化在代码中）
    formula: str | None = Field(
        None,
        max_length=2000,
        deprecated=True,
        description="（已废弃）计算公式——算法已固化在 metric_calculator 代码中，接收但忽略写入",
    )
    weight: Decimal | None = Field(None, ge=0, le=100)
    threshold: dict[str, Any] | None = None
    # v5.3 P3-T9：controlType 参数废弃（控制类型由回路配置决定，不在指标配置中修改）
    controlType: str | None = Field(
        None,
        pattern="^(STABLE|SLOW|FAST|LOGIC)$",
        deprecated=True,
        description="（已废弃）控制类型——由回路配置决定，不在指标配置中修改",
    )
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
    # P3 #51: EVAL_CALC_CYCLE 变更时填充，提示前端 Beat 进程需重启
    warning: str | None = None


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
    fast_rate: float | None = None
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
    # v5.3 对齐 FDS v5.1 / DDS v4.1：compositeScore → score
    score: float | None = None
    goodValueRate: float | None = None
    autoModeRate: float | None = None
    effectiveAutoRate: float | None = None
    steadyRate: float | None = None
    accuracyRate: float | None = None
    # v5.3 对齐 DDS v4.1：fastResponseRate → fastRate
    fastRate: float | None = None
    oscillationRate: float | None = None
    saturationRate: float | None = None
    status: str = "INCONCLUSIVE"
    algorithmVersion: str = "KPI_CALC_v1.0"
    preDiagnosis: str | None = None
    actionStatus: str | None = None
    # v5.3 新增：是否参与评估（FDS §5.2.3）
    includeInEvaluation: bool | None = None
    # v4.0 数据血缘字段（Phase 5 Track A — IDS §2.7.1）
    confidenceLevel: str | None = None
    validRate: float | None = None
    samplingFreq: str | None = None
    qualityPolicy: str | None = None


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


# ---------------------------------------------------------------------------
# v4.0 数据血缘与 KPI 快照 schema（Phase 5 Track A — IDS §2.7.1）
# 设计依据：算法说明 §3.7.1（数据血缘）/§3.7.2（可信度），DDS §2.8
# ---------------------------------------------------------------------------


class DataLineageSchema(CamelModel):
    """数据血缘信息（8 字段，算法说明 §3.7.1）.

    随指标结果一起存储于 ``kpi_snapshot_hourly.data_lineage`` (JSONB)，
    支持审计追溯。由 ``DataLineage.to_dict()`` 序列化生成。

    Attributes:
        samplingFreq: 实际采样频率（如 ``1s`` / ``5s``）
        aggregationPolicy: 聚合策略（LAST / MEAN / MAX）
        qualityPolicy: 质量策略（KEEP_ALL_WITH_VALIDITY / KEEP_ALL）
        tagGroup: 数据来源 tagGroup（BASE/OP_HF/PVOP_HF/MODE_HF/QUALITY_HF）
        dataBlockIds: 使用的 DataBlock ID 列表
        validRate: 有效数据率（0~1）
        dataPolicyVersion: 预处理版本（如 ``pre_v1``）
        algorithmVersion: 算法版本（如 ``KPI_CALC_v2.0``）
    """

    samplingFreq: str = ""
    aggregationPolicy: str = ""
    qualityPolicy: str = ""
    tagGroup: str = ""
    dataBlockIds: list[str] = Field(default_factory=list)
    validRate: float = 0.0
    dataPolicyVersion: str = "pre_v1"
    algorithmVersion: str = "KPI_CALC_v2.0"


class KpiSnapshotSchema(CamelModel):
    """KPI 快照返回 schema（含 7 个数据血缘字段）.

    设计依据：IDS §2.7.1, DDS §2.8, 算法说明 §3.7

    7 个数据血缘字段（Phase 0 ORM 已实现，Phase 4 _save_snapshot 已写入）：
        1. idealSettlingTime — 理想稳态时间（秒）
        2. algorithmVersion — 算法版本（如 ``KPI_CALC_v2.0``）
        3. samplingFreq — 采样频率（如 ``1s`` / ``5s``）
        4. qualityPolicy — 质量策略（``KEEP_ALL_WITH_VALIDITY``）
        5. validRate — 有效数据率（0~1）
        6. confidenceLevel — 可信度等级（A/B/C/D/E）
        7. dataLineage — 完整血缘 JSONB

    所有新增字段均有默认值 None，保持向后兼容。

    Attributes:
        loopId: 回路 ID
        tsStart: 快照开始时间
        tsEnd: 快照结束时间
        score: 综合评分
        goodValueRate: 好值率
        autoModeRate: 自控率
        effectiveAutoRate: 有效自控率
        steadyRate: 稳定率
        accuracyRate: 准确率
        oscillationRate: 振荡率
        saturationRate: 饱和率
        fastRate: 快速率
        stictionIndex: 粘滞指数
        settlingTime: 稳态时间（秒）
        outputTravelIndex: 输出值行程指数
        status: 快照状态（SUCCESS/INCONCLUSIVE/PARTIAL）
        idealSettlingTime: 理想稳态时间（秒）
        algorithmVersion: 算法版本
        samplingFreq: 采样频率
        qualityPolicy: 质量策略
        validRate: 有效数据率
        confidenceLevel: 可信度等级（A/B/C/D/E）
        dataLineage: 完整数据血缘信息
    """

    loopId: str | None = None
    tsStart: str | None = None
    tsEnd: str | None = None
    score: float | None = None
    goodValueRate: float | None = None
    autoModeRate: float | None = None
    effectiveAutoRate: float | None = None
    steadyRate: float | None = None
    accuracyRate: float | None = None
    oscillationRate: float | None = None
    saturationRate: float | None = None
    # v5.3 对齐 DDS v4.1：fastResponseRate → fastRate
    fastRate: float | None = None
    # v5.3 对齐 DDS v4.1：stictionCoeff → stictionIndex
    stictionIndex: float | None = None
    # v5.3 对齐 DDS v4.1：steadyStateTime → settlingTime
    settlingTime: float | None = None
    outputTravelIndex: float | None = None
    status: str = "INCONCLUSIVE"
    # v4.0 数据血缘字段（7 个）
    idealSettlingTime: float | None = None
    algorithmVersion: str | None = None
    samplingFreq: str | None = None
    qualityPolicy: str | None = None
    validRate: float | None = None
    confidenceLevel: str | None = None  # A/B/C/D/E
    dataLineage: DataLineageSchema | None = None


class KpiSnapshotListItem(CamelModel):
    """KPI 快照列表项（KpiSnapshotSchema + 回路名）.

    在 KpiSnapshotSchema 基础上附加 loopTagName，便于前端展示。
    """

    loopId: str | None = None
    loopTagName: str | None = None
    tsStart: str | None = None
    tsEnd: str | None = None
    score: float | None = None
    goodValueRate: float | None = None
    autoModeRate: float | None = None
    effectiveAutoRate: float | None = None
    steadyRate: float | None = None
    accuracyRate: float | None = None
    oscillationRate: float | None = None
    saturationRate: float | None = None
    fastRate: float | None = None
    stictionIndex: float | None = None
    settlingTime: float | None = None
    outputTravelIndex: float | None = None
    status: str = "INCONCLUSIVE"
    idealSettlingTime: float | None = None
    algorithmVersion: str | None = None
    samplingFreq: str | None = None
    qualityPolicy: str | None = None
    validRate: float | None = None
    confidenceLevel: str | None = None
    dataLineage: DataLineageSchema | None = None


class KpiSnapshotListData(CamelModel):
    """回路小时指标快照列表响应 data 块."""

    items: list[KpiSnapshotListItem]
    total: int
    page: int
    pageSize: int


__all__ = [
    "AnalyticsData",
    "AnalyticsFilterScope",
    "BadActorItem",
    "BoardData",
    "BoardFilterScope",
    "DataLineageSchema",
    "EngineRuleItem",
    "EngineRuleUpdate",
    "ExportRequest",
    "KpiCard",
    "KpiSnapshotListData",
    "KpiSnapshotListItem",
    "KpiSnapshotSchema",
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
