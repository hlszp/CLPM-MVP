"""Diagnosis center schemas (IDS v3.2 §2.4 — S4-DIAG-001~006)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from app.schemas.base import CamelModel

# ---------------------------------------------------------------------------
# 枚举类型定义（S4-C3）
# 与业务代码保持一致（app/services/tracker.py VALID_STATUSES）
# ---------------------------------------------------------------------------

# 处理状态：PENDING/IN_PROGRESS/IMPLEMENTED/IGNORED（FDS §5.4.4 "已实施"）
ActionStatus = Literal["PENDING", "IN_PROGRESS", "IMPLEMENTED", "IGNORED"]

# ---------------------------------------------------------------------------
# S4-DIAG-001: 诊断指标配置
# ---------------------------------------------------------------------------


class DiagnosisConfigItem(CamelModel):
    """诊断指标配置项（响应）。"""

    diagId: str
    diagCode: str
    diagName: str
    algorithmType: str
    calcMethod: str | None = None
    params: dict[str, Any] | None = None
    threshold: dict[str, Any] | None = None
    isEnabled: bool = True
    updatedBy: str | None = None
    updatedAt: str | None = None
    version: int = 1


class DiagnosisConfigUpdate(CamelModel):
    """PUT /diagnosis/metrics/{diagId} 请求体。"""

    diagName: str | None = Field(None, max_length=100)
    algorithmType: str | None = Field(None, max_length=50)
    calcMethod: str | None = Field(None, max_length=50)
    params: dict[str, Any] | None = None
    threshold: dict[str, Any] | None = None
    isEnabled: bool | None = None


# ---------------------------------------------------------------------------
# S4-DIAG-003: 诊断列表与详情
# ---------------------------------------------------------------------------


class DiagnosisListItem(CamelModel):
    """诊断列表项。"""

    loopId: str
    tagName: str
    unitName: str | None = None
    compositeScore: float | None = None
    diagnosisLabel: str
    labelName: str
    confidence: float
    fusedConfidence: float | None = None
    algorithm: str | None = None
    actionStatus: ActionStatus = "PENDING"
    diagnosedAt: str
    algorithmVersion: str | None = None


class DiagnosisListData(CamelModel):
    """诊断列表响应 data 块。"""

    items: list[DiagnosisListItem] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    pageSize: int = 20


class DiagnosisEvidence(CamelModel):
    """诊断证据。"""

    scatterPlot: str | None = None
    reasoning: str | None = None
    # 其他动态特征字段


class DiagnosisLabelDetail(CamelModel):
    """诊断详情中的单个标签。"""

    label: str
    labelName: str
    confidence: float
    evidence: dict[str, Any] = Field(default_factory=dict)
    algorithm: str | None = None


class EvidenceChain(CamelModel):
    """证据链。"""

    waveformUrl: str | None = None
    scatterPlot: dict[str, Any] | None = None
    reasoning: str | None = None


class DiagnosisDetail(CamelModel):
    """诊断详情响应 data 块。"""

    loopId: str
    tagName: str
    compositeScore: float | None = None
    diagnosisLabels: list[DiagnosisLabelDetail] = Field(default_factory=list)
    fusedConfidence: float | None = None
    featureValues: dict[str, Any] = Field(default_factory=dict)
    evidenceChain: EvidenceChain = Field(default_factory=EvidenceChain)
    algorithmVersion: str | None = None
    diagnosedAt: str | None = None


# ---------------------------------------------------------------------------
# S4-DIAG-004: 波形查询
# ---------------------------------------------------------------------------


class WaveformTimeRange(CamelModel):
    """波形时间范围。"""

    startTime: str
    endTime: str


class WaveformData(CamelModel):
    """波形响应 data 块。"""

    loopId: str
    tagName: str
    timeRange: WaveformTimeRange
    timestamps: list[int] = Field(default_factory=list)
    pv: list[float | None] = Field(default_factory=list)
    sp: list[float | None] = Field(default_factory=list)
    op: list[float | None] = Field(default_factory=list)
    mode: list[float | None] = Field(default_factory=list)
    pvQuality: list[str] = Field(default_factory=list)
    downsampled: bool = False
    pointCount: int = 0


# ---------------------------------------------------------------------------
# S4-DIAG-005: Action Tracker
# ---------------------------------------------------------------------------


class TrackerStatusUpdate(CamelModel):
    """PATCH /tracker/{loopId}/status 请求体。"""

    status: ActionStatus = Field(..., description="处理状态")
    evidenceUrl: str | None = Field(None, max_length=255)
    remark: str | None = None


class TrackerStatusData(CamelModel):
    """Tracker 状态更新响应 data 块。"""

    loopId: str
    diagnosisLabel: str | None = None
    actionStatus: ActionStatus
    evidenceUrl: str | None = None
    updatedBy: str | None = None
    updatedAt: str | None = None
    abComparison: dict[str, Any] | None = None


class TrackerExportData(CamelModel):
    """Tracker PDF 导出响应 data 块。"""

    taskId: str
    status: str = "PENDING"


# ---------------------------------------------------------------------------
# S4-DIAG-006: 诊断统计报表
# ---------------------------------------------------------------------------


class AnalyticsFilterScope(CamelModel):
    """报表筛选范围。"""

    startTime: str
    endTime: str
    plantNodeId: str | None = None
    diagnosisLabel: str | None = None
    actionStatus: ActionStatus | None = None
    granularity: str = "day"


class LabelDistributionItem(CamelModel):
    """标签分布项。"""

    label: str
    labelName: str
    count: int = 0


class EfficiencyTrend(CamelModel):
    """效率趋势。"""

    timestamps: list[str] = Field(default_factory=list)
    resolvedCount: list[int] = Field(default_factory=list)
    avgCloseDurationHours: list[float | None] = Field(default_factory=list)


class CloseDurationItem(CamelModel):
    """闭环时长分布项。"""

    range: str
    count: int = 0


class DiagnosisAnalyticsData(CamelModel):
    """诊断统计报表响应 data 块。"""

    filterScope: AnalyticsFilterScope
    labelDistribution: list[LabelDistributionItem] = Field(default_factory=list)
    efficiencyTrend: EfficiencyTrend = Field(default_factory=EfficiencyTrend)
    closeDurationDistribution: list[CloseDurationItem] = Field(default_factory=list)


class AnalyticsExportRequest(CamelModel):
    """POST /diagnosis/analytics/export 请求体。"""

    startTime: str
    endTime: str
    plantNodeId: str | None = None
    diagnosisLabel: str | None = None
    actionStatus: ActionStatus | None = None
    granularity: str = "day"
    format: str = Field("pdf", pattern="^(pdf|csv)$")


class AnalyticsExportData(CamelModel):
    """统计报表导出响应 data 块。"""

    taskId: str
    status: str = "PENDING"


__all__ = [
    "AnalyticsExportData",
    "AnalyticsExportRequest",
    "AnalyticsFilterScope",
    "CloseDurationItem",
    "DiagnosisAnalyticsData",
    "DiagnosisConfigItem",
    "DiagnosisConfigUpdate",
    "DiagnosisDetail",
    "DiagnosisEvidence",
    "DiagnosisLabelDetail",
    "DiagnosisListData",
    "DiagnosisListItem",
    "EfficiencyTrend",
    "EvidenceChain",
    "LabelDistributionItem",
    "TrackerExportData",
    "TrackerStatusData",
    "TrackerStatusUpdate",
    "WaveformData",
    "WaveformTimeRange",
]
