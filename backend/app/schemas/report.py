"""Report configuration schemas (S5-SYS-003)."""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from app.schemas.base import CamelModel


class ReportConfigCreateRequest(CamelModel):
    """POST /api/v1/reports/configs request body."""

    name: str = Field(..., min_length=1, max_length=100, description="配置名称")
    reportPeriod: str = Field(..., description="报表周期：SHIFT/DAILY/WEEKLY/MONTHLY")
    recipients: list[str] = Field(..., min_length=1, description="接收人用户 ID 列表")
    contentTemplate: dict[str, Any] | None = Field(None, description="内容模板")
    isEnabled: bool = Field(True, description="是否启用")

    @field_validator("reportPeriod")
    @classmethod
    def validate_period(cls, v: str) -> str:
        allowed = {"SHIFT", "DAILY", "WEEKLY", "MONTHLY"}
        if v not in allowed:
            raise ValueError(f"报表周期必须是 {allowed} 之一")
        return v


class ReportConfigUpdateRequest(CamelModel):
    """PUT /api/v1/reports/configs/{id} request body (partial update)."""

    name: str | None = Field(None, min_length=1, max_length=100)
    reportPeriod: str | None = None
    recipients: list[str] | None = None
    contentTemplate: dict[str, Any] | None = None
    isEnabled: bool | None = None

    @field_validator("reportPeriod")
    @classmethod
    def validate_period(cls, v: str | None) -> str | None:
        if v is None:
            return v
        allowed = {"SHIFT", "DAILY", "WEEKLY", "MONTHLY"}
        if v not in allowed:
            raise ValueError(f"报表周期必须是 {allowed} 之一")
        return v


class ReportConfigItem(CamelModel):
    """Report config item in list / detail responses."""

    id: str
    name: str
    reportPeriod: str
    recipients: list[str]
    contentTemplate: dict[str, Any] | None = None
    isEnabled: bool = True
    createdBy: str | None = None
    updatedBy: str | None = None
    createdAt: str | None = None
    updatedAt: str | None = None


class ReportGenerateRequest(CamelModel):
    """POST /api/v1/reports/generate request body."""

    configId: str | None = Field(None, description="报表配置 ID（可选）")
    reportPeriod: str | None = Field(None, description="报表周期（可选，默认 DAILY）")

    @field_validator("reportPeriod")
    @classmethod
    def validate_period(cls, v: str | None) -> str | None:
        if v is None:
            return v
        allowed = {"SHIFT", "DAILY", "WEEKLY", "MONTHLY"}
        if v not in allowed:
            raise ValueError(f"报表周期必须是 {allowed} 之一")
        return v


class ReportGenerateData(CamelModel):
    """Report generation trigger response data."""

    taskId: str
    taskType: str = "REPORT_GENERATE"
    status: str = "PROCESSING"
    checkUrl: str | None = None
    estimatedSeconds: int = 30


# ---------------------------------------------------------------------------
# 统计报告聚合 API（IA 优化 P0，2026-08-22）
# S1 字段返回真实数据；S2/S3 字段恒为 None，待 P3 填充，保证固定骨架不跳动。
# ---------------------------------------------------------------------------


class ReportOverviewKpi(CamelModel):
    """管理总览单个 KPI 格（S1~S3 统一结构，缺失值用 null 占位）。"""

    key: str
    label: str
    value: float | int | None = None
    unit: str | None = None
    status: str | None = None
    context: str | None = None


class ReportOverviewTrendPoint(CamelModel):
    date: str
    score: float | None = None
    loopCount: int | None = None


class ReportOverviewTopLoop(CamelModel):
    loopId: str
    loopTagName: str
    unitPath: str | None = None
    latestScore: float | None = None
    primaryCategory: str | None = None
    primaryCategoryLabel: str | None = None
    severity: str | None = None
    # S2 追加列（未达标阶段时为 None）
    handlingStatus: str | None = None
    # S3 追加列（未达标阶段时为 None，取闭环处置前后评分差值 score_delta，纯技术口径）
    benefitEstimate: float | None = None


class ReportMaturityCounts(CamelModel):
    diagnosisRuns: int = 0
    handlingOrders: int = 0
    tuningRecords: int = 0
    closedVerifiedOrders: int = 0


class ReportAvailability(CamelModel):
    s1Available: bool = True
    s2Available: bool = False
    s3Available: bool = False


class ReportOverviewData(CamelModel):
    """GET /reports/overview 响应（P3：S1/S2/S3 自适应）。"""

    stage: str = "S1"
    stageOrigin: str = "AUTO"  # 'AUTO' 自动判定 / 'LOCK' 管理员锁定
    isLocked: bool = False
    availability: ReportAvailability = ReportAvailability()
    maturityCounts: ReportMaturityCounts = ReportMaturityCounts()
    kpis: list[ReportOverviewKpi] = []
    healthTrend: list[ReportOverviewTrendPoint] = []
    # S2
    closedLoopTrend: list[dict] | None = None
    anomalyDistributionChange: list[dict] | None = None
    # S3
    benefitTrend: list[dict] | None = None
    # 共用
    topProblemLoops: list[ReportOverviewTopLoop] = []


class ReportDiagnosisCategoryItem(CamelModel):
    category: str
    label: str
    count: int
    ratio: float


class ReportDiagnosisConfidenceItem(CamelModel):
    range: str
    label: str
    count: int
    ratio: float


class ReportDiagnosisTopLoop(CamelModel):
    loopId: str
    loopTagName: str
    unitPath: str | None = None
    runCount: int
    highCount: int
    latestCategory: str | None = None
    latestCategoryLabel: str | None = None
    latestSeverity: str | None = None
    latestConfidence: float | None = None


class ReportDiagnosisTrendPoint(CamelModel):
    date: str
    total: int
    high: int


class ReportDiagnosisStatisticsData(CamelModel):
    """GET /reports/diagnosis-statistics 响应（基于 DiagnosisRun 表）。"""

    total: int
    successCount: int
    reviewPendingCount: int
    categoryDistribution: list[ReportDiagnosisCategoryItem] = []
    confidenceDistribution: list[ReportDiagnosisConfidenceItem] = []
    topAbnormalLoops: list[ReportDiagnosisTopLoop] = []
    trend: list[ReportDiagnosisTrendPoint] = []


class ReportBenefitKpiComparison(CamelModel):
    """整定/处置前后 KPI 对比（技术指标聚合，均值口径）。"""

    metric: str
    label: str
    before: float | None = None
    after: float | None = None
    delta: float | None = None
    unit: str | None = None


class ReportBenefitCurvePoint(CamelModel):
    date: str
    autoRate: float | None = None
    score: float | None = None


class ReportBenefitBenchmarkItem(CamelModel):
    unitId: str | None = None
    unitName: str
    loopCount: int
    avgScore: float | None = None
    avgAutoRate: float | None = None
    avgDelta: float | None = None


class ReportBenefitData(CamelModel):
    """GET /reports/benefit 响应（技术指标口径，不含任何经济换算）。"""

    tuningCount: int
    closedOrderCount: int
    kpiComparison: list[ReportBenefitKpiComparison] = []
    autoRateCurve: list[ReportBenefitCurvePoint] = []
    benchmark: list[ReportBenefitBenchmarkItem] = []


__all__ = [
    "ReportConfigCreateRequest",
    "ReportConfigItem",
    "ReportConfigUpdateRequest",
    "ReportGenerateData",
    "ReportGenerateRequest",
    "ReportOverviewData",
    "ReportOverviewKpi",
    "ReportOverviewTopLoop",
    "ReportOverviewTrendPoint",
    "ReportDiagnosisStatisticsData",
    "ReportDiagnosisCategoryItem",
    "ReportDiagnosisConfidenceItem",
    "ReportDiagnosisTopLoop",
    "ReportDiagnosisTrendPoint",
    "ReportBenefitData",
    "ReportBenefitKpiComparison",
    "ReportBenefitCurvePoint",
    "ReportBenefitBenchmarkItem",
]
