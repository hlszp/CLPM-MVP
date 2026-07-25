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
# C2: 专家规则引擎
# ---------------------------------------------------------------------------

# 动作类型枚举
RuleActionType = Literal[
    "REMOVE_LABEL", "ADD_LABEL", "KEEP_HIGHEST", "FILTER_ONLY", "SORT_PRIORITY"
]


class DiagnosisRuleItem(CamelModel):
    """规则配置项（响应）。"""

    ruleId: str
    ruleCode: str
    ruleName: str
    priority: int
    conditionExpr: str
    actionType: RuleActionType
    actionParams: dict[str, Any] = Field(default_factory=dict)
    isEnabled: bool = True
    version: int = 1
    updatedBy: str | None = None
    updatedAt: str | None = None


class DiagnosisRuleUpdate(CamelModel):
    """PUT /diagnosis/rules/{ruleId} 请求体。"""

    ruleName: str | None = Field(None, max_length=100)
    conditionExpr: str | None = Field(None, max_length=2000)
    actionType: RuleActionType | None = None
    actionParams: dict[str, Any] | None = None
    priority: int | None = Field(None, ge=0, le=999)
    isEnabled: bool | None = None


# ---------------------------------------------------------------------------
# C3: 差异化阈值覆盖
# ---------------------------------------------------------------------------

ThresholdScopeType = Literal["loop_type", "plant", "loop"]


class ThresholdOverrideItem(CamelModel):
    """阈值覆盖项（响应）。"""

    overrideId: str
    diagCode: str
    scopeType: ThresholdScopeType
    scopeId: str
    threshold: dict[str, Any] = Field(default_factory=dict)
    version: int = 1
    updatedBy: str | None = None
    updatedAt: str | None = None


class ThresholdOverrideUpsert(CamelModel):
    """POST /diagnosis/threshold-overrides 请求体。"""

    diagCode: str = Field(..., max_length=50)
    scopeType: ThresholdScopeType
    scopeId: str = Field(..., max_length=100)
    threshold: dict[str, Any]


# ---------------------------------------------------------------------------
# C4: 配置版本与回滚
# ---------------------------------------------------------------------------


class DiagnosisThresholdVersionItem(CamelModel):
    """阈值版本历史项（从 sys_audit_log 读取）。"""

    auditLogId: str
    version: int = 1
    beforeValue: dict[str, Any] | None = None
    afterValue: dict[str, Any] | None = None
    operatedBy: str | None = None
    operatedAt: str | None = None


class DiagnosisThresholdRollbackRequest(CamelModel):
    """POST /diagnosis/metrics/{diagId}/rollback 请求体。"""

    auditLogId: str = Field(..., description="目标版本的审计日志 ID")


# ---------------------------------------------------------------------------
# C5: 关键配置审批流
# ---------------------------------------------------------------------------

ConfigChangeTargetType = Literal["config", "rule", "trigger"]
ConfigChangeType = Literal["update", "enable", "disable"]
ConfigChangeStatus = Literal["PENDING", "APPROVED", "REJECTED"]


class ConfigChangeRequestItem(CamelModel):
    """变更请求项（响应）。"""

    changeId: str
    targetType: ConfigChangeTargetType
    targetId: str
    changeType: ConfigChangeType
    beforeValue: dict[str, Any] | None = None
    afterValue: dict[str, Any] | None = None
    status: ConfigChangeStatus = "PENDING"
    requestedBy: str
    requestedAt: str | None = None
    reviewedBy: str | None = None
    reviewedAt: str | None = None
    reviewNote: str | None = None
    effectiveFrom: str | None = None


class ConfigChangeCreateRequest(CamelModel):
    """POST /diagnosis/config-changes 请求体。"""

    targetType: ConfigChangeTargetType
    targetId: str = Field(..., max_length=100)
    changeType: ConfigChangeType
    beforeValue: dict[str, Any] | None = None
    afterValue: dict[str, Any] | None = None


class ConfigChangeReviewRequest(CamelModel):
    """POST /diagnosis/config-changes/{id}/approve|reject 请求体。"""

    reviewNote: str | None = Field(None, max_length=500)


# ---------------------------------------------------------------------------
# 算法元数据（GET /diagnosis/algorithms/meta — Batch 4 算法价值传递）
# ---------------------------------------------------------------------------


class DiagnosisAlgorithmMetaItem(CamelModel):
    """单条诊断算法展示元数据。

    用于前端"算法价值传递卡片"渲染：算法名、原理说明、关键特征值字段名、
    阈值字段名、对应可视化数据块键名、当前生效阈值快照。
    """

    label: str = Field(..., description="诊断标签码（8 类之一）")
    labelName: str = Field(..., description="标签中文名")
    algorithmName: str = Field(..., description="算法中文名")
    algorithmVersion: str = Field("DIAG_ENGINE_v1.0", description="算法版本号")
    principle: str = Field(..., description="算法原理说明")
    featureKeys: list[str] = Field(
        default_factory=list, description="关键特征值字段名（对应 featureValues）"
    )
    thresholdKeys: list[str] = Field(
        default_factory=list, description="阈值字段名（对应 DiagnosisConfig.threshold）"
    )
    visualizationKey: str | None = Field(
        None, description="对应可视化数据块键名（spectrum/scatterPlot 等）"
    )
    confidenceLevelExplanation: str | None = Field(None, description="置信度等级释义（A-E 五级）")
    isEnabled: bool = True
    threshold: dict[str, Any] | None = Field(
        None, description="当前生效阈值快照（从 DiagnosisConfig 读取）"
    )


class DiagnosisAlgorithmMetaList(CamelModel):
    """算法元数据列表响应 data 块。"""

    items: list[DiagnosisAlgorithmMetaItem] = Field(default_factory=list)
    total: int = 0


# ---------------------------------------------------------------------------
# S4-DIAG-003: 诊断列表与详情
# ---------------------------------------------------------------------------


class DiagnosisListItem(CamelModel):
    """诊断列表项。

    D1 扩展：triggerType/triggeredBy/severity/createdAt/updatedAt/comment/updatedBy
    用于区分自动建单与手工建单，并支持工作台聚合卡按建单时间排序与展示来源徽标。
    """

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
    # D1：建单来源与严重等级（tracker 可能为 None，此时建单信息缺省）
    triggerType: str | None = Field(None, description="建单方式：auto(系统自动) / manual(用户手工)")
    triggeredBy: str | None = Field(None, description="建单人：auto 时为 system")
    severity: str | None = Field(None, description="严重等级 INFO/WARN/ERROR/CRITICAL")
    # D2：建单时间与处理信息（工作台聚合卡"最近建单"按 createdAt 排序）
    createdAt: str | None = Field(None, description="建单时间 ISO 8601")
    updatedAt: str | None = Field(None, description="最近处理时间 ISO 8601")
    comment: str | None = Field(None, description="处理意见/审查备注")
    updatedBy: str | None = Field(None, description="最近处理人")


class DiagnosisAggregates(CamelModel):
    """列表聚合统计（SQL group-by 对全部筛选结果聚合，不受分页影响）。"""

    total: int = 0
    statusCounts: dict[str, int] = Field(default_factory=dict)
    labelCounts: dict[str, int] = Field(default_factory=dict)


class DiagnosisListData(CamelModel):
    """诊断列表响应 data 块。"""

    items: list[DiagnosisListItem] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    pageSize: int = 20
    aggregates: DiagnosisAggregates | None = None


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
    # D3: MOC 关联（标记 IMPLEMENTED 时必填 moc_ref 或 moc_not_applicable+moc_reason）
    comment: str | None = Field(None, max_length=500, description="处理意见/审查备注")
    mocRef: str | None = Field(None, max_length=255, description="MOC 变更管理关联编号")
    mocNotApplicable: bool | None = Field(None, description="MOC 是否不适用")
    mocReason: str | None = Field(None, max_length=500, description="MOC 不适用时的依据说明")


class TrackerStatusData(CamelModel):
    """Tracker 状态更新响应 data 块。"""

    loopId: str
    diagnosisLabel: str | None = None
    actionStatus: ActionStatus
    evidenceUrl: str | None = None
    updatedBy: str | None = None
    updatedAt: str | None = None
    createdAt: str | None = None
    comment: str | None = None
    mocRef: str | None = None
    mocNotApplicable: bool | None = None
    mocReason: str | None = None
    # D1：建单来源与严重等级
    triggerType: str | None = Field(None, description="建单方式：auto/manual")
    triggeredBy: str | None = Field(None, description="建单人")
    severity: str | None = Field(None, description="严重等级 INFO/WARN/ERROR/CRITICAL")
    abComparison: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# A/B 对比（GET /diagnosis/ab-compare）
# ---------------------------------------------------------------------------


class AbCompareKpiItem(CamelModel):
    """A/B 对比单 KPI 项。"""

    metricKey: str
    metricName: str
    unit: str = ""
    before: float | None = None
    after: float | None = None
    change: float | None = None
    changePct: float | None = None
    # true=改善 / false=恶化 / None=持平或无数据
    improved: bool | None = None


class AbCompareWindow(CamelModel):
    """A/B 对比窗口。"""

    startTime: str
    endTime: str
    waveformUrl: str | None = None


class AbCompareData(CamelModel):
    """A/B 对比响应 data 块。"""

    loopId: str
    tagName: str | None = None
    implementedAt: str | None = None
    dataInsufficient: bool = False
    beforeWindow: AbCompareWindow
    afterWindow: AbCompareWindow
    kpiComparison: list[AbCompareKpiItem] = Field(default_factory=list)


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


# ---------------------------------------------------------------------------
# SVC-11: 诊断解决方案推荐
# ---------------------------------------------------------------------------


class RecommendationItem(CamelModel):
    """单条解决方案推荐。"""

    label: str = Field(..., description="标签码（归一化后的 8 类标签之一）")
    labelName: str = Field(..., description="标签中文名")
    priority: int = Field(..., ge=1, le=3, description="优先级：1=高、2=中、3=低")
    action: str = Field(..., description="行动项")
    description: str = Field(..., description="详细描述")
    targetModule: str = Field(..., description="目标模块：整定/跟踪/none")


class RecommendationData(CamelModel):
    """解决方案推荐响应 data 块。"""

    loopId: str
    recommendations: list[RecommendationItem] = Field(default_factory=list)
    totalCount: int = 0


# ---------------------------------------------------------------------------
# SVC-12: 诊断建议书 PDF 生成
# ---------------------------------------------------------------------------


class DiagnosisReportRequest(CamelModel):
    """POST /diagnosis/{loopId}/report 请求体（可选，默认使用最新诊断结果）。"""

    tag_codes: list[str] | None = Field(None, description="诊断标签列表（可选，默认从数据库读取）")


# ---------------------------------------------------------------------------
# SVC-13: 诊断统计 CSV 导出
# ---------------------------------------------------------------------------


class DiagnosisStatisticsExportParams(CamelModel):
    """GET /diagnosis/statistics/export 查询参数。"""

    startDate: str = Field(..., description="开始日期（ISO 8601）")
    endDate: str = Field(..., description="结束日期（ISO 8601）")
    plantNodeId: str | None = Field(None, description="按装置/单元筛选")


# ---------------------------------------------------------------------------
# 诊断标签管理 (PRD §5.6, IDS §2.4.10-2.4.12)
# ---------------------------------------------------------------------------

# 诊断标签类型（8 类，对齐 PRD §5.2 诊断标签体系）
DiagnosisTagType = Literal[
    "OSCILLATION",
    "VALVE_STICTION",
    "OVERAGGRESSIVE",
    "OVERCONSERVATIVE",
    "EXTERNAL_DISTURBANCE",
    "QUALITY_ABNORMAL",
    "OUTPUT_SATURATION",
    "MANUAL_REVIEW",
]

# 诊断标签严重等级
DiagnosisTagSeverity = Literal["INFO", "WARN", "ERROR", "CRITICAL"]

# 诊断标签处理状态
DiagnosisTagStatus = Literal["ACTIVE", "RESOLVED", "SUPPRESSED"]


class DiagnosisTagSchema(CamelModel):
    """诊断标签 schema (IDS §2.4.10).

    对应 ``DiagnosisTag`` 模型，承载回路级故障标签的完整管理元数据。
    """

    id: str
    loop_id: str
    tag_type: str = Field(..., description="标签类型：OSCILLATION/VALVE_STICTION/...")
    severity: str = Field(..., description="严重等级：INFO/WARN/ERROR/CRITICAL")
    status: str = Field(..., description="处理状态：ACTIVE/RESOLVED/SUPPRESSED")
    source_metric: str | None = Field(None, description="来源指标代码")
    trigger_condition: dict[str, Any] | None = Field(
        None, description="触发条件快照（算法名/阈值/实际值等）"
    )
    trigger_value: float | None = Field(None, description="触发值")
    threshold: float | None = Field(None, description="触发阈值")
    confidence_level: str | None = Field(None, description="可信度等级")
    description: str | None = Field(None, description="标签描述（中文名）")
    detected_at: str = Field(..., description="检测时间（ISO 8601）")
    resolved_at: str | None = Field(None, description="处理时间（ISO 8601）")
    resolved_by: str | None = Field(None, description="处理人 ID")
    resolution_note: str | None = Field(None, description="处理说明")


class DiagnosisTagListResponse(CamelModel):
    """诊断标签列表响应 data 块 (IDS §2.4.10/2.4.11)."""

    items: list[DiagnosisTagSchema] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 20


class TagResolveRequest(CamelModel):
    """PUT /diagnosis/tags/{tagId}/resolve 请求体 (IDS §2.4.12).

    处理人 (resolved_by) 从认证上下文获取，不由客户端传入，确保审计可追溯。
    """

    status: str = Field(..., description="目标处理状态：RESOLVED（已处理）/ SUPPRESSED（已抑制）")
    resolution_note: str | None = Field(None, description="处理说明（抑制时必填）")


# ---------------------------------------------------------------------------
# 诊断任务管理 (PRD §5.6 诊断中心 — 诊断任务子模块)
# ---------------------------------------------------------------------------

# 诊断任务状态：PENDING/RUNNING/SUCCESS/FAILED/CANCELLED
DiagnosisTaskStatus = Literal["PENDING", "RUNNING", "SUCCESS", "FAILED", "CANCELLED"]

# 诊断任务触发方式：manual（手动）/ auto（自动）
DiagnosisTriggerType = Literal["manual", "auto"]


class DiagnosisTriggerRequest(CamelModel):
    """POST /diagnosis/trigger 请求体 — 触发诊断（支持批量）。

    支持单/批量回路诊断触发，可选时间范围（默认最近 1 小时）。
    labels 为可选的诊断标签子集（B6 按需诊断）：为空/未传时执行全量算法；
    仅接受 8 类标准标签（不含 MANUAL_REVIEW，其为兜底标签不受子集限制）。
    """

    loopIds: list[str] = Field(..., min_length=1, description="回路 ID 列表（至少 1 个）")
    startTime: str | None = Field(None, description="时间窗起始（ISO 8601，默认最近 1 小时）")
    endTime: str | None = Field(None, description="时间窗结束（ISO 8601，默认当前时间）")
    labels: list[str] | None = Field(
        None, description="诊断标签子集（可选，默认全量；不含 MANUAL_REVIEW）"
    )


class DiagnosisTaskTriggerItem(CamelModel):
    """触发诊断响应中的单个任务项。"""

    taskId: str
    loopId: str
    status: DiagnosisTaskStatus = "PENDING"


class DiagnosisTriggerData(CamelModel):
    """触发诊断响应 data 块。"""

    tasks: list[DiagnosisTaskTriggerItem] = Field(default_factory=list)


class DiagnosisLabelItem(CamelModel):
    """诊断任务列表项中的标签聚合。"""

    label: str
    confidence: float = 0.0


class DiagnosisTaskItem(CamelModel):
    """诊断任务列表项（未归档）。"""

    taskId: str
    loopId: str
    tagName: str | None = None
    loopName: str | None = None
    unitName: str | None = None
    compositeScore: float | None = None
    accuracyScore: float | None = None
    fastScore: float | None = None
    steadyScore: float | None = None
    effectiveAutoRate: float | None = None
    diagLabels: list[str] = Field(default_factory=list)
    status: DiagnosisTaskStatus
    triggerType: DiagnosisTriggerType
    triggeredBy: str | None = None
    triggeredAt: str | None = None
    completedAt: str | None = None
    timeRangeStart: str | None = None
    timeRangeEnd: str | None = None
    labels: list[DiagnosisLabelItem] = Field(default_factory=list)
    isArchived: bool = False
    errorMessage: str | None = None


class DiagnosisTaskListData(CamelModel):
    """诊断任务列表响应 data 块。"""

    items: list[DiagnosisTaskItem] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    pageSize: int = 20


class DiagnosisTaskResultItem(CamelModel):
    """诊断任务详情中的单条诊断结果。"""

    id: str
    label: str
    labelName: str | None = None
    confidence: float = 0.0
    featureValues: dict[str, Any] = Field(default_factory=dict)
    evidenceChain: dict[str, Any] = Field(default_factory=dict)
    algorithmVersion: str | None = None
    diagnosedAt: str | None = None


class DiagnosisTaskDetail(CamelModel):
    """诊断任务详情响应 data 块。"""

    taskId: str
    loopId: str
    tagName: str | None = None
    loopName: str | None = None
    unitName: str | None = None
    status: DiagnosisTaskStatus
    triggerType: DiagnosisTriggerType
    triggeredBy: str | None = None
    triggeredAt: str | None = None
    completedAt: str | None = None
    timeRangeStart: str | None = None
    timeRangeEnd: str | None = None
    errorMessage: str | None = None
    isArchived: bool = False
    results: list[DiagnosisTaskResultItem] = Field(default_factory=list)


class DiagnosisRecordItem(DiagnosisTaskItem):
    """诊断记录列表项（已归档），结构与任务列表项一致。"""


class DiagnosisRecordAggregates(DiagnosisAggregates):
    """诊断记录聚合统计（含近 7 天归档数）。"""

    recent7Days: int = 0


class DiagnosisRecordListData(CamelModel):
    """诊断记录列表响应 data 块。"""

    items: list[DiagnosisRecordItem] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    pageSize: int = 20
    aggregates: DiagnosisRecordAggregates | None = None


__all__ = [
    "AbCompareData",
    "AbCompareKpiItem",
    "AbCompareWindow",
    "AnalyticsExportData",
    "AnalyticsExportRequest",
    "AnalyticsFilterScope",
    "CloseDurationItem",
    "DiagnosisAggregates",
    "DiagnosisAlgorithmMetaItem",
    "DiagnosisAlgorithmMetaList",
    "DiagnosisAnalyticsData",
    "DiagnosisConfigItem",
    "DiagnosisConfigUpdate",
    "DiagnosisDetail",
    "DiagnosisEvidence",
    "DiagnosisLabelDetail",
    "DiagnosisListData",
    "DiagnosisListItem",
    "DiagnosisRecordAggregates",
    "DiagnosisRecordItem",
    "DiagnosisRecordListData",
    "DiagnosisReportRequest",
    "DiagnosisStatisticsExportParams",
    "DiagnosisTagListResponse",
    "DiagnosisTagSchema",
    "DiagnosisTagSeverity",
    "DiagnosisTagStatus",
    "DiagnosisTagType",
    "DiagnosisTaskDetail",
    "DiagnosisTaskItem",
    "DiagnosisTaskListData",
    "DiagnosisTaskResultItem",
    "DiagnosisTaskStatus",
    "DiagnosisTaskTriggerItem",
    "DiagnosisTriggerData",
    "DiagnosisTriggerRequest",
    "DiagnosisTriggerType",
    "DiagnosisLabelItem",
    "EfficiencyTrend",
    "EvidenceChain",
    "LabelDistributionItem",
    "RecommendationData",
    "RecommendationItem",
    "TagResolveRequest",
    "TrackerStatusData",
    "TrackerStatusUpdate",
    "WaveformData",
    "WaveformTimeRange",
]
