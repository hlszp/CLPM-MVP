/**
 * CLPM 诊断中心 API（对齐 IDS v3.2 §2.4）
 *
 * 覆盖诊断指标配置、诊断列表/详情、波形查看、Action Tracker、统计报表五类子能力。
 * - 诊断 API 前缀：/api/v1/diagnosis/
 * - 波形 API 前缀：/api/v1/timeseries/
 * - Tracker API 前缀：/api/v1/tracker/
 *
 * 注意：ActionStatus / TimeWindow / Granularity 与 metric.ts 同名但语义不同，
 * 仅在 DiagnosisApi 命名空间内导出，避免与 index.ts 的 `export *` 冲突。
 */
import type { PaginatedResponse } from '#/api/types';

import { requestClient } from '#/api/request';

/** 8 类诊断标签（IDS v3.2 §2.4） */
export type DiagnosisLabel =
  | 'EXTERNAL_DISTURBANCE'
  | 'MANUAL_REVIEW'
  | 'OSCILLATION'
  | 'OUTPUT_SATURATION'
  | 'OVERAGGRESSIVE'
  | 'OVERCONSERVATIVE'
  | 'QUALITY_ABNORMAL'
  | 'VALVE_STICTION';

/** 质量码 */
export type Quality = 'BAD' | 'GOOD' | 'UNCERTAIN' | null;

export namespace DiagnosisApi {
  /** 处理状态枚举（P1a 扩展闭环状态机：PENDING → IN_PROGRESS → VERIFYING → CLOSED，VERIFYING可→REOPENED） */
  export type ActionStatus =
    | 'CLOSED'
    | 'IGNORED'
    | 'IMPLEMENTED'
    | 'IN_PROGRESS'
    | 'PENDING'
    | 'REOPENED'
    | 'VERIFYING';

  /** 时间窗枚举 */
  export type TimeWindow = 'last_7_days' | 'last_24_hours' | 'last_30_days';

  /** 报表粒度 */
  export type Granularity = 'day' | 'month' | 'week';

  /** 诊断指标配置项（IDS v3.2 §2.4） */
  export interface MetricItem {
    diagId: string;
    diagKey: string;
    diagName: string;
    label: DiagnosisLabel;
    algorithmType: string;
    calcMethod: string;
    params: Record<string, number>;
    threshold: Record<string, number>;
    isEnabled: boolean;
    algorithmVersion: string;
    updatedAt: string;
    updatedBy: string;
  }

  /** 诊断指标列表响应 */
  export interface MetricListResult {
    items: MetricItem[];
  }

  /** 更新诊断指标参数（仅 ADMIN） */
  export interface MetricUpdateParams {
    label: DiagnosisLabel;
    algorithmType: string;
    calcMethod: string;
    params: Record<string, number>;
    threshold: Record<string, number>;
    isEnabled: boolean;
  }

  /** 诊断列表项（IDS v3.2 §2.4） */
  export interface DiagnosisListItem {
    loopId: string;
    tagName: string;
    unitName: string;
    compositeScore: number;
    diagnosisLabel: DiagnosisLabel;
    labelName: string;
    confidence: number;
    fusedConfidence: number;
    algorithm: string;
    actionStatus: ActionStatus;
    diagnosedAt: string;
    algorithmVersion: string;
  }

  /** 列表排序字段：diagnosed_at(默认,诊断时间) / created_at(tracker建单时间) */
  export type DiagnosisSortBy = 'created_at' | 'diagnosed_at';

  /** 诊断列表查询参数 */
  export interface DiagnosisListQueryParams {
    plantNodeId?: string;
    diagnosisLabel?: DiagnosisLabel;
    actionStatus?: ActionStatus;
    timeWindow?: TimeWindow;
    sortBy?: DiagnosisSortBy;
    /**
     * 按回路 ID 列表批量筛选（D6 入口整合：loop/monitor.vue 批量查诊断标签）
     * 后端 FastAPI list[str] = Query(None) 接受重复参数 ?loopIds=a&loopIds=b
     */
    loopIds?: string[];
    page?: number;
    pageSize?: number;
  }

  /** 列表聚合统计（后端 SQL group-by 对全部筛选结果聚合，不受分页影响） */
  export interface DiagnosisAggregates {
    total: number;
    statusCounts: Record<string, number>;
    labelCounts: Record<string, number>;
    /** 近 7 天归档数（仅 records 接口返回） */
    recent7Days?: number;
    /** C1-3：VERIFYING 超 24h 未闭环的超期条目数（验证闭环提醒） */
    verifyOverdueCount?: number;
  }

  /** 诊断列表响应（含聚合统计） */
  export interface DiagnosisListResult extends PaginatedResponse<DiagnosisListItem> {
    aggregates?: DiagnosisAggregates;
  }

  /** Tracker 列表响应（复用 /diagnosis/list，含聚合统计） */
  export interface TrackerListResult extends PaginatedResponse<TrackerItem> {
    aggregates?: DiagnosisAggregates;
  }

  /** 诊断记录列表响应（含聚合统计） */
  export interface DiagnosisRecordListResult extends PaginatedResponse<TaskItem> {
    aggregates?: DiagnosisAggregates;
  }

  /** 诊断详情 - 单个标签结果 */
  export interface DiagnosisLabelItem {
    label: DiagnosisLabel;
    labelName: string;
    confidence: number;
    evidence: Record<string, unknown>;
    algorithm: string;
  }

  /** 诊断详情 - 证据链 */
  export interface EvidenceChain {
    waveformUrl?: string;
    scatterPlot?: { x: number[]; y: number[] };
    reasoning?: string;
  }

  /** 诊断详情（IDS v3.2 §2.4） */
  export interface DiagnosisDetail {
    loopId: string;
    tagName: string;
    compositeScore: number;
    diagnosisLabels: DiagnosisLabelItem[];
    fusedConfidence: number;
    /** B5：可信度等级 A/B/C/D/E（基于有效数据率，旧数据可能缺省） */
    confidenceLevel?: 'A' | 'B' | 'C' | 'D' | 'E' | null;
    /** B5：有效数据率 0~1（旧数据可能缺省） */
    validRate?: null | number;
    featureValues: Record<string, number>;
    evidenceChain: EvidenceChain;
    algorithmVersion: string;
    diagnosedAt: string;
  }

  /** 诊断可视化 - FFT 频谱数据 */
  export interface SpectrumData {
    frequencies: number[];
    amplitudes: number[];
    peakFrequency: number;
    peakAmplitude: number;
    oscillationIndex: number;
  }

  /** 诊断可视化 - 阶跃响应数据 */
  export interface StepResponseData {
    timestamps: number[];
    pvResponse: number[];
    spValues: number[];
    stepIndices: number[];
    overshoot: number;
    decayRatio: number;
    steadyStateError: number;
  }

  /** 诊断可视化 - CUSUM 累积和数据 */
  export interface CusumAnalysisData {
    timestamps: number[];
    cusumPos: number[];
    cusumNeg: number[];
    shiftPoints: number[];
    threshold: number;
    shiftCount: number;
    maxCusum: number;
  }

  /** 诊断可视化 - PV-OP 散点图数据 */
  export interface ScatterPlotData {
    x: number[];
    y: number[];
    fittingScore: number;
    stictionIndex: number;
  }

  /** 诊断可视化 - 质量码时序数据 */
  export interface QualityTimelineData {
    badRate: number;
    totalPoints: number;
    badPoints: number;
    qualityPattern: string;
  }

  /** 诊断可视化 - OP 饱和分析数据 */
  export interface SaturationAnalysisData {
    saturationRate: number;
    highSaturationCount: number;
    lowSaturationCount: number;
  }

  /** 诊断可视化 - 响应迟缓分析数据 */
  export interface SlowResponseData {
    timeConstant: number;
    expectedTimeConstant: number;
    ratio: number;
  }

  /** 诊断可视化 - Choudhury 非线性检测数据 */
  export interface ChoudhuryData {
    ngi: number;
    nli: number;
    stictionIndex: number;
  }

  /** 诊断可视化 - IAE 零交叉分析数据 */
  export interface IaeAnalysisData {
    similarity: number;
    zeroCrossingCount: number;
    meanPeriod: number;
    opZeroCrossCount: number;
    pvZeroCrossCount: number;
    similarityRate: number;
    oscillationIndex: number;
  }

  /** 诊断可视化 - Kano 统计法数据 */
  export interface KanoData {
    stictionRatio: number;
    correlation: number;
    stdRatio: number;
    biasIndex: number;
    countP: number;
    countN: number;
    countZ: number;
  }

  /** 诊断可视化数据（包含 8 类算法的完整可视化数组） */
  export interface DiagnosisVisualizationData {
    loopId: string;
    tagName: string;
    compositeScore: null | number;
    fusedConfidence: null | number;
    diagnosedAt: null | string;
    diagnosisLabels: DiagnosisLabelItem[];
    spectrum: SpectrumData;
    stepResponse: StepResponseData;
    cusumAnalysis: CusumAnalysisData;
    scatterPlot: ScatterPlotData;
    qualityTimeline: QualityTimelineData;
    saturationAnalysis: SaturationAnalysisData;
    slowResponse: SlowResponseData;
    choudhury: ChoudhuryData;
    iaeAnalysis: IaeAnalysisData;
    kano: KanoData;
  }

  /** 波形数据（IDS v3.2 §2.4，Phase 5 扩展血缘字段） */
  export interface WaveformResult {
    loopId: string;
    tagName: string;
    timeRange: { endTime: string; startTime: string };
    timestamps: number[];
    pv: (null | number)[];
    sp: (null | number)[];
    op: (null | number)[];
    mode: (null | number)[];
    pvQuality: Quality[];
    /** v4.0 血缘字段（Phase 5 扩展） */
    samplingFreq?: string;
    qualityPolicy?: string;
    validRate?: number;
    confidenceLevel?: string;
    downsampled?: boolean;
    pointCount?: number;
    /** 采样间隔（秒），由后端根据时间范围动态计算（如 72h → 72s） */
    sampleInterval?: number;
  }

  /** 波形查询参数（Phase 5 扩展 tagGroup/includeValidMask/maxPoints） */
  export interface WaveformQueryParams {
    startTime: string;
    endTime: string;
    downsample?: boolean;
    maxPoints?: number;
    /** Phase 5: 按标签组筛选（BASE/OP_HF/PVOP_HF/MODE_HF/QUALITY_HF） */
    tagGroup?: string;
    /** Phase 5: 是否返回 valid_mask（逐点有效性标记） */
    includeValidMask?: boolean;
  }

  /** P3 #44: 批量波形查询请求体（POST /timeseries/batch/waveform） */
  export interface BatchWaveformRequest {
    /** 回路 ID 列表（1~50 个） */
    loopIds: string[];
    /** 开始时间（ISO 8601） */
    startTime: string;
    /** 结束时间（ISO 8601） */
    endTime: string;
    /** 按标签组筛选（BASE/OP_HF/PVOP_HF/MODE_HF/QUALITY_HF） */
    tagGroup?: string;
    /** 是否返回 valid_mask（默认 true） */
    includeValidMask?: boolean;
    /** 每个回路最大数据点数（100~50000，默认 5000） */
    maxPoints?: number;
  }

  /** P3 #44: 批量波形查询中的失败回路信息 */
  export interface BatchWaveformFailure {
    loopId: string;
    error: string;
  }

  /** P3 #44: 批量波形查询响应 */
  export interface BatchWaveformResponse {
    /** 成功获取的波形数据列表 */
    items: WaveformResult[];
    /** 失败的回路列表（含错误信息） */
    failed: BatchWaveformFailure[];
    /** 成功回路数 */
    total: number;
  }

  /** Tracker 状态更新参数（仅 IC_ENGINEER） */
  export interface TrackerStatusUpdateParams {
    status: ActionStatus;
    comment?: string;
    /** 变更说明（审计字段，预留） */
    changeRemark?: string;
    /** D3: MOC 变更管理关联编号（VERIFYING/IMPLEMENTED 时必填，或勾选不适用） */
    mocRef?: string;
    /** D3: MOC 是否不适用 */
    mocNotApplicable?: boolean;
    /** D3: MOC 不适用时的依据说明（mocNotApplicable=true 时必填） */
    mocReason?: string;
    /** V62-P3-008：实施责任人（与建单人 triggeredBy 区分） */
    assignee?: string;
    /** V62-P3-008：计划执行时间 ISO 8601 */
    plannedAt?: string;
    /** P1a: 实施后新比例增益 P（VERIFYING 状态时必填） */
    newPidP?: null | number;
    /** P1a: 实施后新积分时间 I（秒，VERIFYING 状态时必填） */
    newPidI?: null | number;
    /** P1a: 实施后新微分时间 D（秒，VERIFYING 状态时必填） */
    newPidD?: null | number;
    /** P1a: 实际实施时间 ISO 8601（默认当前时间） */
    implementedAt?: null | string;
    /** P1a: 重开原因（REOPENED 状态时必填） */
    reopenReason?: null | string;
    /** P3-01：关联整定任务记录 ID（VERIFYING 时可选，用于知识库生成） */
    tuningRecordId?: null | string;
  }

  /** Tracker 记录项 */
  export interface TrackerItem {
    loopId: string;
    tagName: string;
    unitName: string;
    diagnosisLabel: DiagnosisLabel;
    labelName: string;
    actionStatus: ActionStatus;
    compositeScore: number;
    confidence: number;
    createdAt: string;
    updatedAt: string;
    comment?: string;
    updatedBy?: string;
    /** D1: 建单方式 auto/manual */
    triggerType?: 'auto' | 'manual';
    /** D1: 建单人 */
    triggeredBy?: string;
    /** D1: 严重等级 */
    severity?: 'CRITICAL' | 'ERROR' | 'INFO' | 'WARN';
    /** D3: MOC 变更管理关联编号（VERIFYING/IMPLEMENTED 时存在） */
    mocRef?: string;
    /** D3: MOC 是否不适用 */
    mocNotApplicable?: boolean;
    /** D3: MOC 不适用时的依据说明 */
    mocReason?: string;
    /** D4: 整改效果验证（T+7d 自动回写，True=改善 / False=恶化或无明显变化 / null=未验证） */
    effectVerified?: boolean | null;
    /** D4: 整改效果验证时间 */
    effectVerifiedAt?: string;
    /** D4: A/B 对比结果快照（改善/恶化指标数 + 关键 KPI 变化） */
    abCompareSummary?: Record<string, unknown>;
    /** V62-P3-008：实施责任人（与建单人 triggeredBy 区分） */
    assignee?: string;
    /** V62-P3-008：计划执行时间 ISO 8601 */
    plannedAt?: string;
    /** P1a: 实施后新比例增益 P */
    newPidP?: null | number;
    /** P1a: 实施后新积分时间 I（秒） */
    newPidI?: null | number;
    /** P1a: 实施后新微分时间 D（秒） */
    newPidD?: null | number;
    /** P1a: 实际实施时间 ISO 8601 */
    implementedAt?: null | string;
    /** P1a: 实施人 */
    implementedBy?: null | string;
    /** P1a: 闭环时间 ISO 8601（CLOSED 时存在） */
    closedAt?: null | string;
    /** P1a: 重开原因（REOPENED 时存在） */
    reopenReason?: null | string;
  }

  // -----------------------------------------------------------------------
  // P1a: 异常处置时间线
  // -----------------------------------------------------------------------

  /** 时间线事件类型（对齐后端 snake_case） */
  export type TimelineEventType =
    | 'claimed' // 认领处理
    | 'comment' // 添加备注/评论
    | 'diagnosis_detected' // 系统发现异常
    | 'ignored' // 忽略
    | 'implemented' // 现场实施（VERIFYING）
    | 'moc_recorded' // 记录MOC变更
    | 'tuning_completed' // 整定完成
    | 'verification_failed' // 验证失败（REOPENED）
    | 'verification_passed'; // 验证通过（CLOSED）

  /** 时间线事件项 */
  export interface TimelineEventItem {
    eventId: string;
    eventType: TimelineEventType;
    timestamp: string;
    actor?: null | string;
    title: string;
    description?: null | string;
    meta: Record<string, unknown>;
  }

  /** 时间线响应数据 */
  export interface TimelineData {
    loopId: string;
    tagName?: null | string;
    /** 当前跟踪状态 */
    currentStatus?: ActionStatus | null;
    events: TimelineEventItem[];
    /** 预计自动验证时间 ISO 8601（VERIFYING 状态时存在） */
    pendingVerificationAt?: null | string;
  }

  /** Tracker 列表查询参数 */
  export interface TrackerListQueryParams {
    plantNodeId?: string;
    diagnosisLabel?: DiagnosisLabel;
    actionStatus?: ActionStatus;
    loopId?: string;
    timeWindow?: TimeWindow;
    /** 排序字段：聚合卡"最近建单"用 created_at；默认 diagnosed_at */
    sortBy?: DiagnosisSortBy;
    page?: number;
    pageSize?: number;
  }

  /** 统计报表 - 标签分布项 */
  export interface LabelDistributionItem {
    label: DiagnosisLabel;
    labelName: string;
    count: number;
  }

  /** 统计报表 - 处理效率趋势 */
  export interface EfficiencyTrend {
    timestamps: string[];
    resolvedCount: number[];
    avgCloseDurationHours: number[];
  }

  /** 统计报表 - 闭环时长分布项 */
  export interface CloseDurationItem {
    range: string;
    count: number;
  }

  /** 统计报表 - 筛选范围 */
  export interface AnalyticsFilterScope {
    startTime: string;
    endTime: string;
    plantNodeId: null | string;
    diagnosisLabel: null | string;
    actionStatus: null | string;
    granularity: Granularity;
  }

  /** 统计报表响应（IDS v3.2 §2.4） */
  export interface AnalyticsResult {
    filterScope: AnalyticsFilterScope;
    labelDistribution: LabelDistributionItem[];
    efficiencyTrend: EfficiencyTrend;
    closeDurationDistribution: CloseDurationItem[];
  }

  /** 统计报表查询参数 */
  export interface AnalyticsQueryParams {
    startTime: string;
    endTime: string;
    plantNodeId?: string;
    diagnosisLabel?: DiagnosisLabel;
    actionStatus?: ActionStatus;
    granularity?: Granularity;
  }

  /** A/B 对比 - KPI 对比项 */
  export interface AbCompareKpiItem {
    metricKey: string;
    metricName: string;
    unit: string;
    before: null | number;
    after: null | number;
    change: null | number;
    changePct: null | number;
    /** true=改善 / false=恶化 / null=持平或无数据 */
    improved: boolean | null;
  }

  /** A/B 对比 - 数据窗口 */
  export interface AbCompareWindow {
    startTime: string;
    endTime: string;
    waveformUrl?: string;
  }

  /** A/B 对比 - 窗口内诊断标签快照（includeDiagnosis=true 时返回） */
  export interface DiagnosisWindowLabel {
    label: DiagnosisLabel;
    labelName: string;
    /** 置信度 0~1（后端由 0-100 归一化） */
    confidence: number;
    diagnosedAt: string;
  }

  /** A/B 对比 - 标签变更项 */
  export interface LabelChangeItem {
    label: DiagnosisLabel;
    /** added=新增 / removed=消失 / confidence_changed=置信度变化 */
    change: 'added' | 'confidence_changed' | 'removed';
    beforeConfidence?: number;
    afterConfidence?: number;
  }

  /** A/B 对比响应 */
  export interface AbCompareResult {
    loopId: string;
    tagName: string;
    implementedAt: null | string;
    /** 实施后窗口数据不足 24h 时为 true（评估数据采集中） */
    dataInsufficient: boolean;
    beforeWindow: AbCompareWindow;
    afterWindow: AbCompareWindow;
    kpiComparison: AbCompareKpiItem[];
    /** includeDiagnosis=true 时返回：处置前窗口最新标签快照 */
    beforeDiagnosisLabels?: DiagnosisWindowLabel[];
    /** includeDiagnosis=true 时返回：处置后窗口最新标签快照 */
    afterDiagnosisLabels?: DiagnosisWindowLabel[];
    /** includeDiagnosis=true 时返回：标签新增/消失/置信度变化 */
    labelChanges?: LabelChangeItem[];
  }

  /** A/B 对比查询参数（implementedAt 与显式窗口二选一） */
  export interface AbCompareQueryParams {
    loopId: string;
    implementedAt?: string;
    beforeStartTime?: string;
    beforeEndTime?: string;
    afterStartTime?: string;
    afterEndTime?: string;
    /** true 时额外返回诊断标签对比（beforeDiagnosisLabels/afterDiagnosisLabels/labelChanges） */
    includeDiagnosis?: boolean;
  }

  /** D4-2 整改效果验证周期配置 */
  export interface TrackerVerificationConfig {
    /** 验证周期（小时），IMPLEMENTED 后等待 N 小时触发验证，默认 24 */
    intervalHours: number;
    updatedBy?: string;
    updatedAt?: string;
  }

  /** D4-3 整改有效率趋势单日数据 */
  export interface EffectivenessTrendItem {
    date: string;
    verifiedCount: number;
    improvedCount: number;
    effectiveRate: null | number;
  }

  /** D4-3 整改有效率统计响应 */
  export interface TrackerEffectivenessData {
    totalImplemented: number;
    verifiedCount: number;
    improvedCount: number;
    deterioratedCount: number;
    /** 整改有效率 = improvedCount / verifiedCount，无验证数据时为 null */
    effectiveRate: null | number;
    pendingVerificationCount: number;
    trend: EffectivenessTrendItem[];
  }

  /** 算法元数据项（对齐 app.schemas.diagnosis.DiagnosisAlgorithmMetaItem） */
  export interface AlgorithmMetaItem {
    label: DiagnosisLabel;
    labelName: string;
    algorithmName: string;
    algorithmVersion: string;
    principle: string;
    featureKeys: string[];
    thresholdKeys: string[];
    visualizationKey: null | string;
    confidenceLevelExplanation: null | string;
    isEnabled: boolean;
    threshold: null | Record<string, number>;
  }

  /** 算法元数据列表响应 */
  export interface AlgorithmMetaList {
    items: AlgorithmMetaItem[];
    total: number;
  }

  /** 解决方案推荐项（SVC-11） */
  export interface RecommendationItem {
    label: string;
    labelName: string;
    priority: number;
    action: string;
    description: string;
    targetModule: string;
  }

  /** 解决方案推荐响应（SVC-11） */
  export interface RecommendationResult {
    loopId: string;
    recommendations: RecommendationItem[];
    totalCount: number;
  }

  /** 诊断建议书 PDF 生成请求（SVC-12） */
  export interface DiagnosisReportParams {
    tagCodes?: string[];
  }

  /** 诊断统计 CSV 导出参数（SVC-13） */
  export interface StatisticsExportParams {
    startDate: string;
    endDate: string;
    plantNodeId?: string;
  }

  // -----------------------------------------------------------------------
  // 诊断标签管理（Phase 5 — IDS §2.4.10-2.4.12）
  // -----------------------------------------------------------------------

  /** 诊断标签类型（8 类，对齐 DiagnosisTagType） */
  export type TagType = DiagnosisLabel;

  /** 诊断标签严重等级 */
  export type TagSeverity = 'CRITICAL' | 'ERROR' | 'INFO' | 'WARN';

  /** 诊断标签处理状态（区别于旧版 ActionStatus） */
  export type TagStatus = 'ACTIVE' | 'RESOLVED' | 'SUPPRESSED';

  /** 诊断标签项（对齐 DiagnosisTagSchema，驼峰序列化） */
  export interface DiagnosisTagItem {
    id: string;
    loopId: string;
    tagType: TagType;
    severity: TagSeverity;
    status: TagStatus;
    sourceMetric?: null | string;
    triggerCondition?: null | Record<string, unknown>;
    triggerValue?: null | number;
    threshold?: null | number;
    confidenceLevel?: null | string;
    description?: null | string;
    detectedAt: string;
    resolvedAt?: null | string;
    resolvedBy?: null | string;
    resolutionNote?: null | string;
  }

  /** 诊断标签列表响应 */
  export interface DiagnosisTagListResult {
    items: DiagnosisTagItem[];
    total: number;
    page: number;
    pageSize: number;
  }

  /** 诊断标签查询参数（对齐后端 /diagnosis/tags 参数名） */
  export interface DiagnosisTagQueryParams {
    tagType?: TagType;
    status?: TagStatus;
    severity?: TagSeverity;
    plantNodeId?: string;
    /** 时间范围开始（ISO 8601） */
    tsStart?: string;
    /** 时间范围结束（ISO 8601） */
    tsEnd?: string;
    page?: number;
    pageSize?: number;
  }

  /** 标签处理参数（PUT /diagnosis/tags/{tagId}/resolve） */
  export interface TagResolveParams {
    /** 目标状态：RESOLVED（已处理）/ SUPPRESSED（已抑制） */
    status: 'RESOLVED' | 'SUPPRESSED';
    /** 处理说明（抑制时必填） */
    resolutionNote?: string;
  }

  // -----------------------------------------------------------------------
  // 诊断任务（Phase 5 扩展 — 诊断任务管理 + 归档）
  // -----------------------------------------------------------------------

  /** 诊断任务状态机 */
  export type TaskStatus =
    | 'CANCELLED'
    | 'FAILED'
    | 'PENDING'
    | 'RUNNING'
    | 'SUCCESS';

  /** 诊断任务触发方式 */
  export type TriggerType = 'auto' | 'manual';

  /** 诊断任务列表项（每回路一行，未归档） */
  export interface TaskItem {
    taskId: string;
    loopId: string;
    tagName: string;
    loopName: string;
    unitName: string;
    compositeScore: null | number;
    accuracyScore: null | number;
    fastScore: null | number;
    steadyScore: null | number;
    effectiveAutoRate: null | number;
    diagLabels: string[];
    status: TaskStatus;
    triggerType: TriggerType;
    triggeredBy: null | string;
    triggeredAt: string;
    completedAt: null | string;
    timeRangeStart: null | string;
    timeRangeEnd: null | string;
    labels: { confidence: number; label: string }[];
    isArchived: boolean;
    archivedAt: null | string;
    archivedBy: null | string;
    errorMessage: null | string;
  }

  /** 触发诊断请求 */
  export interface TriggerRequest {
    loopIds: string[];
    startTime?: string;
    endTime?: string;
    /** 诊断标签子集（可选，默认全量；不含 MANUAL_REVIEW） */
    labels?: string[];
  }

  /** 触发诊断响应中的单个任务项（对齐后端 DiagnosisTaskTriggerItem） */
  export interface TriggerTaskItem {
    taskId: string;
    loopId: string;
    status: TaskStatus;
  }

  /** 触发诊断响应 data 块（对齐后端 DiagnosisTriggerData，非裸数组） */
  export interface TriggerResult {
    tasks: TriggerTaskItem[];
  }

  /** 诊断任务查询参数 */
  export interface TaskListQueryParams {
    status?: string;
    triggerType?: string;
    loopId?: string;
    plantNodeId?: string;
    timeWindow?: TimeWindow;
    /** 是否包含已归档任务（SUCCESS 完成即自动归档） */
    includeArchived?: boolean;
    /** 仅返回已归档任务（P2-16-B2 Tab 化）；与 includeArchived 同时为 true 时 archivedOnly 优先 */
    archivedOnly?: boolean;
    page?: number;
    pageSize?: number;
  }

  /** 诊断任务 Tab 计数（P2-16-B2） */
  export interface TaskStats {
    active: number;
    completed: number;
    archived: number;
  }

  /** 诊断结果项（任务详情内嵌） */
  export interface DiagnosisResultItem {
    diagLabel: string;
    confidence: number;
    evidenceChain: Record<string, unknown>;
    featureValues: Record<string, unknown>;
    algorithmVersion: string;
  }

  /** 诊断任务详情（含诊断结果） */
  export interface TaskDetail extends TaskItem {
    results: DiagnosisResultItem[];
    errorMessage: null | string;
  }

  // -----------------------------------------------------------------------
  // P3-02: 诊断阈值模板化与自适应
  // -----------------------------------------------------------------------

  /** 阈值覆盖 scope 类型（loop_type 模板 / plant 装置级 / loop 回路级） */
  export type ThresholdScopeType = 'loop' | 'loop_type' | 'plant';

  /** 阈值覆盖项（响应） */
  export interface ThresholdOverrideItem {
    overrideId: string;
    diagCode: string;
    scopeType: ThresholdScopeType;
    scopeId: string;
    threshold: Record<string, number>;
    version: number;
    updatedAt?: null | string;
    updatedBy?: null | string;
  }

  /** 创建/更新阈值覆盖请求体 */
  export interface ThresholdOverrideUpsertParams {
    diagCode: string;
    scopeType: ThresholdScopeType;
    scopeId: string;
    threshold: Record<string, number>;
  }

  /** 一键套用模板请求体（P3-02） */
  export interface ThresholdApplyParams {
    loopId: string;
    diagCode: string;
    /** loop=回路级（ic_engineer 可用）/ plant=装置级（仅 ADMIN） */
    targetScope: 'loop' | 'plant';
  }

  /** 单级阈值来源（推荐响应 scopeChain，展示"为什么是这个阈值"） */
  export interface ThresholdScopeSource {
    /** None 表示全局默认 */
    scopeType?: null | ThresholdScopeType;
    scopeId?: null | string;
    threshold: Record<string, number>;
    /** 是否实际生效（最高优先级那一层） */
    isApplied: boolean;
    /** global_default/loop_type_template/plant_override/loop_override */
    source: string;
  }

  /** 单个 diag_code 的阈值推荐（P3-02） */
  export interface ThresholdRecommendationItem {
    diagCode: string;
    diagName?: null | string;
    globalDefault: Record<string, number>;
    loopTypeTemplate?: null | Record<string, number>;
    plantOverride?: null | Record<string, number>;
    loopOverride?: null | Record<string, number>;
    effectiveThreshold: Record<string, number>;
    scopeChain: ThresholdScopeSource[];
  }

  /** 按回路推荐阈值模板响应（P3-02） */
  export interface ThresholdRecommendationResult {
    loopId: string;
    tagName?: null | string;
    loopType?: null | string;
    plantId?: null | string;
    plantName?: null | string;
    recommendations: ThresholdRecommendationItem[];
  }

  // -----------------------------------------------------------------------
  // P3-04: 自然语言诊断解读
  // -----------------------------------------------------------------------

  /** 解读生成模式
   * - auto：优先 LLM，不可用/失败时 fallback 到规则模板（默认）
   * - template：仅规则模板（离线可用）
   * - llm：仅 LLM，不可用抛 503（供"重新生成"强制走 LLM）
   */
  export type InterpretMode = 'auto' | 'llm' | 'template';

  /** 自然语言诊断解读请求体（P3-04） */
  export interface InterpretParams {
    mode?: InterpretMode;
  }

  /** 自然语言诊断解读响应（P3-04） */
  export interface InterpretResult {
    /** 结构化纯文本解读（含概述/主因分析/风险提示三段） */
    interpretation: string;
    /** 实际来源：template（规则模板）/ llm（LLM 生成） */
    source: 'llm' | 'template';
    /** LLM 模型名（source=llm 时有值） */
    model?: null | string;
    /** 生成时间 ISO 8601 */
    generatedAt: string;
  }
}

/**
 * 获取诊断指标配置列表 — IDS v3.2 §2.4
 */
export function getDiagnosisMetricsApi() {
  return requestClient.get<DiagnosisApi.MetricItem[]>('/diagnosis/metrics');
}

/**
 * 更新诊断指标配置 — IDS v3.2 §2.4（仅 ADMIN）
 */
export function updateDiagnosisMetricApi(
  diagId: string,
  data: DiagnosisApi.MetricUpdateParams,
) {
  return requestClient.put<DiagnosisApi.MetricItem>(
    `/diagnosis/metrics/${diagId}`,
    data,
  );
}

/**
 * 获取诊断列表 — IDS v3.2 §2.4
 *
 * D6 入口整合：loopIds 数组参数以 repeat 格式序列化（?loopIds=a&loopIds=b），
 * 对齐后端 FastAPI `list[str] = Query(None)` 默认解析方式。
 */
export function getDiagnosisListApi(
  params: DiagnosisApi.DiagnosisListQueryParams,
) {
  return requestClient.get<DiagnosisApi.DiagnosisListResult>(
    '/diagnosis/list',
    {
      params,
      paramsSerializer: 'repeat',
    },
  );
}

/**
 * 获取诊断详情 — IDS v3.2 §2.4
 */
export function getDiagnosisDetailApi(
  loopId: string,
  timeWindow?: DiagnosisApi.TimeWindow,
) {
  return requestClient.get<DiagnosisApi.DiagnosisDetail>(
    `/diagnosis/${loopId}`,
    { params: { timeWindow } },
  );
}

/**
 * 获取诊断可视化数据（包含 8 类算法的完整可视化数组）
 */
export function getDiagnosisVisualizationApi(loopId: string) {
  return requestClient.get<DiagnosisApi.DiagnosisVisualizationData>(
    `/diagnosis/${loopId}/visualization`,
  );
}

/**
 * 获取波形数据 — IDS v3.2 §2.4
 */
export function getWaveformApi(
  loopId: string,
  params: DiagnosisApi.WaveformQueryParams,
) {
  return requestClient.get<DiagnosisApi.WaveformResult>(
    `/timeseries/${loopId}/waveform`,
    { params },
  );
}

/**
 * P3 #44: 批量获取波形数据 — IDS v3.2 §2.4
 *
 * 一次请求并行获取多个回路的波形数据，减少 HTTP 请求次数。
 * 适用于多回路对比、批量导出等场景。
 *
 * - 最多 50 个回路
 * - 单个回路失败不影响其他回路（失败信息放入 failed 列表）
 * - 每个回路独立应用 LTTB 降采样
 */
export function getBatchWaveformApi(data: DiagnosisApi.BatchWaveformRequest) {
  return requestClient.post<DiagnosisApi.BatchWaveformResponse>(
    '/timeseries/batch/waveform',
    data,
  );
}

/**
 * 更新 Tracker 状态 — IDS v3.2 §2.4（仅 IC_ENGINEER）
 * 使用 PATCH 方法（通过 request 透传）
 */
export function updateTrackerStatusApi(
  loopId: string,
  data: DiagnosisApi.TrackerStatusUpdateParams,
) {
  return requestClient.request(`/tracker/${loopId}/status`, {
    data,
    method: 'PATCH',
  });
}

/**
 * 获取 Tracker 列表 — IDS v3.2 §2.4
 * 后端无 /tracker 列表端点，复用 /diagnosis/list 接口获取数据。
 */
export function getTrackerListApi(params: DiagnosisApi.TrackerListQueryParams) {
  return requestClient.get<DiagnosisApi.TrackerListResult>('/diagnosis/list', {
    params,
  });
}

/**
 * 导出诊断建议书 PDF — IDS v3.2 §2.4（同步生成，直接下载）
 *
 * 返回 Blob，前端通过 URL.createObjectURL 触发下载。
 * 文件名格式：CLPM-诊断建议书-[位号]-[日期].pdf
 */
export function exportDiagnosisPdfApi(loopId: string) {
  return requestClient.download<Blob>(`/tracker/${loopId}/export`, {
    method: 'POST',
  });
}

/**
 * V62-P3-33：异步提交「整改建议书 PDF」导出（避免大回路网关超时）。
 *
 * 返回 { taskId }，前端轮询 GET /tasks/{taskId}，进度 100% 后通过
 * buildTaskDownloadUrl(taskId) 调用 window.open 触发下载。
 */
export function exportDiagnosisPdfAsyncApi(loopId: string) {
  return requestClient.post<{ message?: string; taskId: string }>(
    `/tracker/${loopId}/export`,
    undefined,
    { params: { async: true } },
  );
}

/**
 * 获取诊断统计报表 — IDS v3.2 §2.4
 */
export function getDiagnosisAnalyticsApi(
  params: DiagnosisApi.AnalyticsQueryParams,
) {
  return requestClient.get<DiagnosisApi.AnalyticsResult>(
    '/diagnosis/analytics',
    { params },
  );
}

/**
 * 导出诊断统计报表 — IDS v3.2 §2.4
 */
export function exportDiagnosisAnalyticsApi(
  data: DiagnosisApi.AnalyticsQueryParams,
) {
  return requestClient.post('/diagnosis/analytics/export', data);
}

/**
 * 获取 A/B 对比数据 — IDS v3.2 §2.4
 */
export function getAbCompareApi(params: DiagnosisApi.AbCompareQueryParams) {
  return requestClient.get<DiagnosisApi.AbCompareResult>(
    '/diagnosis/ab-compare',
    { params },
  );
}

/**
 * 获取算法展示元数据 — Batch 4 算法价值传递
 *
 * 返回 8 类诊断算法的中文名、原理、关键特征值字段名、阈值快照、置信度等级释义。
 * 前端算法卡片直接渲染，避免硬编码。
 */
export function getAlgorithmMetaApi() {
  return requestClient.get<DiagnosisApi.AlgorithmMetaList>(
    '/diagnosis/algorithms/meta',
  );
}

/**
 * 获取解决方案推荐 — SVC-11
 *
 * 根据诊断标签返回标准化解决方案推荐。不传 tagCodes 时从数据库读取该回路最新诊断标签。
 */
export function getRecommendationsApi(loopId: string, tagCodes?: string[]) {
  const params = tagCodes?.length ? { tagCodes: tagCodes.join(',') } : {};
  return requestClient.get<DiagnosisApi.RecommendationResult>(
    `/diagnosis/${loopId}/recommendations`,
    { params },
  );
}

/**
 * 生成并下载诊断建议书 PDF — SVC-12（同步，直接返回 Blob）
 */
export function generateDiagnosisReportApi(
  loopId: string,
  data?: DiagnosisApi.DiagnosisReportParams,
) {
  return requestClient.download<Blob>(`/diagnosis/${loopId}/report`, {
    data,
    method: 'POST',
  });
}

/**
 * V62-P3-33：异步生成「诊断建议书 PDF」，返回 { taskId }，与 tracker 异步导出
 * 共用同一套 TaskTracker 轮询+下载链路。
 */
export function generateDiagnosisReportAsyncApi(
  loopId: string,
  data?: DiagnosisApi.DiagnosisReportParams,
) {
  return requestClient.post<{ message?: string; taskId: string }>(
    `/diagnosis/${loopId}/report`,
    data ?? {},
    { params: { async: true } },
  );
}

/**
 * 导出诊断统计 CSV — SVC-13
 *
 * 返回 Blob（UTF-8 with BOM），前端通过 URL.createObjectURL 触发下载。
 */
export function exportDiagnosisStatisticsApi(
  params: DiagnosisApi.StatisticsExportParams,
) {
  return requestClient.download<Blob>('/diagnosis/statistics/export', {
    params,
  });
}

// ---------------------------------------------------------------------------
// 诊断标签管理 API（Phase 5 — IDS §2.4.10-2.4.12）
// ---------------------------------------------------------------------------

/**
 * 查询诊断标签列表 — IDS §2.4.10
 *
 * 支持按标签类型、处理状态、严重等级、时间范围等多条件筛选。
 */
export function getDiagnosisTagsApi(
  params: DiagnosisApi.DiagnosisTagQueryParams,
) {
  return requestClient.get<DiagnosisApi.DiagnosisTagListResult>(
    '/diagnosis/tags',
    { params },
  );
}

/**
 * 查询指定回路的诊断标签 — IDS §2.4.11
 */
export function getLoopDiagnosisTagsApi(
  loopId: string,
  params: DiagnosisApi.DiagnosisTagQueryParams,
) {
  return requestClient.get<DiagnosisApi.DiagnosisTagListResult>(
    `/diagnosis/tags/${loopId}`,
    { params },
  );
}

/**
 * 获取诊断标签详情 — IDS §2.4.10
 */
export function getDiagnosisTagDetailApi(tagId: string) {
  return requestClient.get<DiagnosisApi.DiagnosisTagItem>(
    `/diagnosis/tags/${tagId}`,
  );
}

/**
 * 更新诊断标签处理状态 — IDS §2.4.12（IC_ENGINEER/PE_ENGINEER/ADMIN）
 *
 * 状态流转：ACTIVE → RESOLVED（已处理）/ ACTIVE → SUPPRESSED（已抑制）
 */
export function updateDiagnosisTagStatusApi(
  tagId: string,
  data: DiagnosisApi.TagResolveParams,
) {
  return requestClient.put<DiagnosisApi.DiagnosisTagItem>(
    `/diagnosis/tags/${tagId}/resolve`,
    data,
  );
}

// ---------------------------------------------------------------------------
// 诊断任务 API（Phase 5 扩展 — 诊断任务管理 + 归档）
// ---------------------------------------------------------------------------

/**
 * 触发诊断 — 批量触发一个或多个回路的诊断任务
 *
 * 返回创建的任务列表（每回路一个任务）。
 */
export function triggerDiagnosisApi(data: DiagnosisApi.TriggerRequest) {
  return requestClient.post<DiagnosisApi.TriggerResult>(
    '/diagnosis/trigger',
    data,
  );
}

/**
 * 获取诊断任务列表（未归档/含已归档/仅已归档）
 */
export function getDiagnosisTasksApi(params: DiagnosisApi.TaskListQueryParams) {
  return requestClient.get<PaginatedResponse<DiagnosisApi.TaskItem>>(
    '/diagnosis/tasks',
    { params },
  );
}

/**
 * 获取诊断任务 Tab 计数（P2-16-B2）：active/completed/archived 三类
 */
export function getDiagnosisTaskStatsApi(params?: {
  loopId?: string;
  plantNodeId?: string;
}) {
  return requestClient.get<DiagnosisApi.TaskStats>('/diagnosis/tasks/stats', {
    params,
  });
}

/**
 * 获取诊断任务详情（含诊断结果、证据链）
 */
export function getDiagnosisTaskDetailApi(taskId: string) {
  return requestClient.get<DiagnosisApi.TaskDetail>(
    `/diagnosis/tasks/${taskId}`,
  );
}

/**
 * 执行诊断任务（对已有任务重新执行诊断，不创建新任务）
 */
export function runDiagnosisTaskApi(taskId: string) {
  return requestClient.post<DiagnosisApi.TaskItem>(
    `/diagnosis/tasks/${taskId}/run`,
  );
}

/**
 * 归档诊断任务
 */
export function archiveDiagnosisTaskApi(taskId: string) {
  return requestClient.post<DiagnosisApi.TaskItem>(
    `/diagnosis/tasks/${taskId}/archive`,
  );
}

/**
 * 取消诊断任务（仅 PENDING/RUNNING 状态可取消）
 */
export function cancelDiagnosisTaskApi(taskId: string) {
  return requestClient.post<DiagnosisApi.TaskItem>(
    `/diagnosis/tasks/${taskId}/cancel`,
  );
}

/**
 * 物理删除诊断任务（RUNNING 不可删除，须先取消）
 */
export function deleteDiagnosisTaskApi(taskId: string) {
  return requestClient.delete<Record<string, unknown>>(
    `/diagnosis/tasks/${taskId}`,
  );
}

/**
 * 获取诊断记录列表（已归档）
 */
export function getDiagnosisRecordsApi(
  params: DiagnosisApi.DiagnosisListQueryParams,
) {
  return requestClient.get<DiagnosisApi.DiagnosisRecordListResult>(
    '/diagnosis/records',
    { params },
  );
}

// ---------------------------------------------------------------------------
// D4-2 整改效果验证周期配置 API
// ---------------------------------------------------------------------------

export function getVerificationConfigApi() {
  return requestClient.get<DiagnosisApi.TrackerVerificationConfig>(
    '/tracker/verification-config',
  );
}

export function updateVerificationConfigApi(intervalHours: number) {
  return requestClient.request<DiagnosisApi.TrackerVerificationConfig>(
    '/tracker/verification-config',
    {
      data: { intervalHours },
      method: 'PATCH',
    },
  );
}

// ---------------------------------------------------------------------------
// D4-3 整改有效率统计 API
// ---------------------------------------------------------------------------

export function getTrackerEffectivenessApi(params?: {
  plantNodeId?: string;
  timeWindow?: 'last_7_days' | 'last_30_days' | 'last_90_days';
}) {
  return requestClient.get<DiagnosisApi.TrackerEffectivenessData>(
    '/tracker/effectiveness',
    { params },
  );
}

// ---------------------------------------------------------------------------
// P1a: 异常处置时间线 API
// ---------------------------------------------------------------------------

/**
 * P1a: 获取单回路异常处置时间线
 */
export function getLoopTimelineApi(loopId: string) {
  return requestClient.get<DiagnosisApi.TimelineData>(
    `/tracker/${loopId}/timeline`,
  );
}

// ---------------------------------------------------------------------------
// P3-02: 诊断阈值模板化与自适应 API
// ---------------------------------------------------------------------------

/**
 * P3-02: 获取阈值覆盖列表（可按 scope 筛选）
 *
 * 权限：diagnosis:view（所有登录用户可查看）
 */
export function getThresholdOverridesApi(params?: {
  scopeId?: string;
  scopeType?: DiagnosisApi.ThresholdScopeType;
}) {
  return requestClient.get<DiagnosisApi.ThresholdOverrideItem[]>(
    '/diagnosis/threshold-overrides',
    { params },
  );
}

/**
 * P3-02: 获取控制类型模板列表（loop_type scope 的预置阈值模板）
 */
export function getThresholdTemplatesApi() {
  return requestClient.get<DiagnosisApi.ThresholdOverrideItem[]>(
    '/diagnosis/threshold-templates',
  );
}

/**
 * P3-02: 创建/更新阈值覆盖
 *
 * - ADMIN：全 scope 可操作
 * - ic_engineer：仅 loop scope（回路级微调），服务层校验
 */
export function upsertThresholdOverrideApi(
  data: DiagnosisApi.ThresholdOverrideUpsertParams,
) {
  return requestClient.post<DiagnosisApi.ThresholdOverrideItem>(
    '/diagnosis/threshold-overrides',
    data,
  );
}

/**
 * P3-02: 删除阈值覆盖
 *
 * - ADMIN：全 scope 可删除
 * - ic_engineer：仅 loop scope 可删除
 */
export function deleteThresholdOverrideApi(overrideId: string) {
  return requestClient.delete(`/diagnosis/threshold-overrides/${overrideId}`);
}

/**
 * P3-02: 按回路推荐阈值模板（自适应推荐核心）
 *
 * 返回该回路所有 diag_code 的合并阈值视图：
 * 全局默认 → loop_type 模板 → 装置级覆盖 → 回路级覆盖 → 生效阈值
 * 含 scopeChain 展示各级覆盖来源（"为什么是这个阈值"）。
 */
export function getThresholdRecommendationsApi(loopId: string) {
  return requestClient.get<DiagnosisApi.ThresholdRecommendationResult>(
    '/diagnosis/threshold-recommendations',
    { params: { loopId } },
  );
}

/**
 * P3-02: 一键套用模板到回路/装置
 *
 * 将该回路 loop_type 匹配的模板阈值复制为目标 scope 的覆盖：
 * - targetScope="loop"：ADMIN/IC_ENGINEER 可用（回路级微调起点）
 * - targetScope="plant"：仅 ADMIN 可用（装置级覆盖）
 */
export function applyThresholdTemplateApi(
  data: DiagnosisApi.ThresholdApplyParams,
) {
  return requestClient.post<DiagnosisApi.ThresholdOverrideItem>(
    '/diagnosis/threshold-templates/apply',
    data,
  );
}

// ---------------------------------------------------------------------------
// P3-04: 自然语言诊断解读 API
// ---------------------------------------------------------------------------

/**
 * P3-04: 生成自然语言诊断解读
 *
 * 将结构化诊断结果翻译为工程师可读的大白话解读，辅助非算法背景用户理解
 * "这个振荡是什么意思、严不严重、该怎么处理"。
 *
 * 生成模式（mode）：
 * - auto（默认）：优先 LLM，不可用/失败时自动 fallback 到规则模板
 * - template：仅规则模板（离线可用）
 * - llm：仅 LLM，不可用抛 503（供"重新生成"强制走 LLM）
 *
 * 权限：ADMIN/IC_ENGINEER/PE_ENGINEER/EXPERT（与诊断详情一致）。
 */
export function interpretDiagnosisApi(
  loopId: string,
  data?: DiagnosisApi.InterpretParams,
) {
  return requestClient.post<DiagnosisApi.InterpretResult>(
    `/diagnosis/${loopId}/interpret`,
    data ?? {},
  );
}
