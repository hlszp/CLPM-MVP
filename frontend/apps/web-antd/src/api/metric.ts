/**
 * CLPM 性能评估 API（对齐 IDS v3.2 §2.3）
 *
 * 覆盖指标配置、引擎规则、全局看板、低效回路排行、统计报表五类子能力。
 * 所有接口前缀 `/performance`，响应格式 `{code, message, data}`。
 */
import { requestClient } from '#/api/request';

/** KPI 状态色标 */
export type KpiStatus = 'INCONCLUSIVE' | 'PARTIAL' | 'SUCCESS';

/** 可信度等级（算法说明 §3.7.2，基于 valid_rate 自动判定） */
export type ConfidenceLevel = 'A' | 'B' | 'C' | 'D' | 'E';

/** 控制类型（指标配置用，对齐 MetricConfigUpdate.pattern） */
export type ControlType = 'FAST' | 'LOGIC' | 'SLOW' | 'STABLE';

/** DataPlanner 控制类型（算法层用，对齐 app.contracts.data_types.ControlType） */
export type DataPlannerControlType = 'CC' | 'FC' | 'LC' | 'PC' | 'TC';

/** 时间窗枚举 */
export type TimeWindow = 'last_7_days' | 'last_30_days' | 'today' | 'yesterday';

/** 报表粒度 */
export type Granularity = 'day' | 'hour' | 'month' | 'week';

/** 执行状态 */
export type ExecutionStatus = 'FAILED' | 'RUNNING' | 'SUCCESS';

/** 处理状态 */
export type ActionStatus =
  | 'IGNORED'
  | 'IMPLEMENTED'
  | 'IN_PROGRESS'
  | 'PENDING';

export namespace MetricApi {
  /** 指标阈值 */
  export interface MetricThreshold {
    min: number;
    max: number;
    alert: number;
  }

  /** 指标类别（v5.3 对齐 FDS §5.3.1 3+1+8 结构） */
  export type MetricCategory =
    | 'AUXILIARY_DIAGNOSTIC'
    | 'COMMISSIONING'
    | 'CORE';

  /** 指标配置项 */
  export interface MetricItem {
    metricId: string;
    /** 指标代码（大写下划线格式，如 IDEAL_SETTLING_TIME，由 /performance/metrics 返回） */
    metricCode?: string;
    /** 指标 key（驼峰格式，如 idealSettlingTime，由 /configs/metrics 批量接口返回） */
    metricKey: string;
    metricName: string;
    formula: string;
    weight: number;
    threshold: MetricThreshold;
    controlType: ControlType;
    isEnabled: boolean;
    description?: string;
    algorithmVersion: string;
    updatedAt: string;
    updatedBy: string;
    /** v5.3 指标类别（CORE/COMMISSIONING/AUXILIARY_DIAGNOSTIC） */
    category?: MetricCategory | null;
  }

  /** 指标配置列表响应 */
  export interface MetricListResult {
    items: MetricItem[];
    totalWeight: number;
    weightValid: boolean;
  }

  /** 更新指标配置参数 */
  export interface MetricUpdateParams {
    formula: string;
    weight: number;
    threshold: MetricThreshold;
    controlType: ControlType;
    isEnabled: boolean;
    description?: string;
  }

  /** 引擎规则类型（对齐后端 EngineRuleType） */
  export type EngineRuleType =
    | 'ARCHIVE'
    | 'CALC_CYCLE'
    | 'DATA_FETCH'
    | 'RETRY'
    | 'SCHEDULE';

  /** 引擎规则项（对齐后端 EngineRuleItem） */
  export interface RuleItem {
    ruleId: string;
    ruleCode: string;
    ruleName: string;
    ruleType: EngineRuleType;
    /** 规则参数（不同 ruleType 的 params 结构不同） */
    params: null | Record<string, any>;
    isEnabled: boolean;
    updatedBy?: null | string;
    updatedAt?: null | string;
    /** P3 #51: EVAL_CALC_CYCLE 变更时返回，提示 Beat 进程需重启 */
    warning?: null | string;
  }

  /** 更新引擎规则参数（对齐后端 EngineRuleUpdate） */
  export interface RuleUpdateParams {
    ruleName?: string;
    params?: null | Record<string, any>;
    isEnabled?: boolean;
  }

  /** 看板筛选范围 */
  export interface BoardFilterScope {
    plantNodeId: null | string;
    plantNodeName: string;
    timeWindow: TimeWindow;
  }

  /** KPI 卡片 */
  export interface KpiCard {
    metricKey: string;
    metricName: string;
    value: number;
    unit: string;
    status: KpiStatus;
    algorithmVersion: string;
    /** 可信度等级（v4.0 血缘字段） */
    confidenceLevel?: ConfidenceLevel;
  }

  /** 数据血缘信息（8 字段，对齐 DataLineageSchema） */
  export interface DataLineage {
    samplingFreq: string;
    aggregationPolicy: string;
    qualityPolicy: string;
    tagGroup: string;
    dataBlockIds: string[];
    validRate: number;
    dataPolicyVersion: string;
    algorithmVersion: string;
  }

  /** KPI 摘要（对齐 GB/T 44693.2-2024） */
  export interface KpiSummary {
    goodValueRate: number;
    autoModeRate: number;
    effectiveAutoRate: number;
    steadyRate: number;
    accuracyRate: number;
    fastRate: number;
    oscillationRate: number;
    saturationRate: number;
    instrumentFaultRate: number;
    compositeScore: number;
    status: KpiStatus;
    algorithmVersion: string;
    /** v4.0 数据血缘字段（7 个，对齐 KpiSnapshotSchema） */
    idealSettlingTime?: null | number;
    samplingFreq?: null | string;
    qualityPolicy?: null | string;
    validRate?: null | number;
    confidenceLevel?: ConfidenceLevel | null;
    dataLineage?: DataLineage | null;
  }

  /** 趋势数据 */
  export interface TrendData {
    timestamps: string[];
    values: number[];
  }

  /** Partial 警告 */
  export interface PartialWarning {
    active: boolean;
    inconclusiveCount: number;
    partialCount: number;
    message: string;
  }

  /** 看板响应 */
  export interface BoardResult {
    filterScope: BoardFilterScope;
    kpiCards: KpiCard[];
    kpiSummary: KpiSummary;
    steadyRateTrend: TrendData;
    partialWarning: PartialWarning;
  }

  /** 排行项（对齐 GB/T 44693.2-2024） */
  export interface RankingItem {
    rank: number;
    loopId: string;
    tagName: string;
    loopName: null | string;
    unitName: string;
    score: number;
    goodValueRate: number;
    autoModeRate: number;
    effectiveAutoRate: number;
    steadyRate: number;
    accuracyRate: number;
    fastRate: number;
    oscillationRate: number;
    saturationRate: number;
    instrumentFaultRate: number;
    status: KpiStatus;
    algorithmVersion: string;
    preDiagnosis?: string;
    actionStatus: ActionStatus;
    includeInEvaluation?: boolean | null;
    /** v4.0 数据血缘字段（对齐后端 RankingItem schema） */
    confidenceLevel?: ConfidenceLevel | null;
    validRate?: null | number;
    samplingFreq?: null | string;
    qualityPolicy?: null | string;
    idealSettlingTime?: null | number;
    dataLineage?: DataLineage | null;
    /** 历史评分趋势（用于行内 sparkline 展示） */
    scoreHistory?: number[];
  }

  /** 排行查询参数 */
  export interface RankingQueryParams {
    limit?: number;
    /** 偏移量（配合 limit 实现分页拉全量） */
    offset?: number;
    plantNodeId?: string;
    sortBy?: string;
    sortOrder?: 'asc' | 'desc';
    timeWindow: TimeWindow;
  }

  /** 报表筛选范围 */
  export interface AnalyticsFilterScope {
    endTime: string;
    granularity: Granularity;
    metricKey: string;
    plantNodeId: null | string;
    startTime: string;
  }

  /** 报表趋势序列 */
  export interface AnalyticsSeries {
    metricKey: string;
    metricName: string;
    values: number[];
  }

  /** 报表 KPI 趋势 */
  export interface AnalyticsKpiTrend {
    timestamps: string[];
    series: AnalyticsSeries[];
  }

  /** 装置评分排行 */
  export interface UnitRankingItem {
    unitId: string;
    unitName: string;
    score: number;
    loopCount: number;
  }

  /** 差等生分布 */
  export interface BadActorItem {
    label: string;
    count: number;
  }

  /** 报表响应 */
  export interface AnalyticsResult {
    filterScope: AnalyticsFilterScope;
    kpiTrend: AnalyticsKpiTrend;
    unitRanking: UnitRankingItem[];
    badActorDistribution: BadActorItem[];
  }

  /** 报表导出参数 */
  export interface AnalyticsExportParams {
    endTime: string;
    format: 'csv' | 'xlsx';
    granularity?: Granularity;
    metricKey?: string;
    plantNodeId?: string;
    startTime: string;
  }

  /** 报表导出响应 */
  export interface AnalyticsExportResult {
    taskId: string;
  }

  /** 回路类型权重项（STABLE/SLOW/FAST/LOGIC） */
  export interface LoopTypeWeightItem {
    /** 回路类型 */
    loopType: ControlType;
    /** 类型名称 */
    loopTypeName?: string;
    /** A 权重（自动模式率权重） */
    weightA: number;
    /** F 权重（快速率权重） */
    weightF: number;
    /** S 权重（稳定率权重） */
    weightS: number;
    /** 描述 */
    description?: string;
    updatedAt?: string;
    updatedBy?: string;
  }

  /** 回路类型权重列表响应 */
  export interface LoopTypeWeightListResult {
    items: LoopTypeWeightItem[];
  }

  /** 回路类型权重更新参数 */
  export interface LoopTypeWeightUpdateParams {
    weightA: number;
    weightF: number;
    weightS: number;
    description?: string;
  }

  /** 回路级别（1/2/3） */
  export type LoopLevel = 1 | 2 | 3;

  /** 回路级别权重项 */
  export interface LoopLevelWeightItem {
    /** 级别 */
    level: LoopLevel;
    /** 级别名称 */
    levelName?: string;
    /** 权重 */
    weight: number;
    /** 描述 */
    description?: string;
    updatedAt?: string;
    updatedBy?: string;
  }

  /** 回路级别权重列表响应 */
  export interface LoopLevelWeightListResult {
    items: LoopLevelWeightItem[];
  }

  /** 回路级别权重更新参数 */
  export interface LoopLevelWeightUpdateParams {
    weight: number;
    description?: string;
  }

  /** 实时自控率统计 */
  export interface RealtimeAutoRateResult {
    /** 工厂节点 ID（null 表示全厂） */
    plantNodeId: null | string;
    /** 工厂节点名称 */
    plantNodeName?: string;
    /** 自动回路数 */
    autoCount: number;
    /** 手动回路数 */
    manualCount: number;
    /** 总回路数 */
    totalCount: number;
    /** 实时自控率（百分比） */
    autoRate: number;
    /** 统计时间 */
    readAt?: string;
  }

  // ========================================================================
  // v5.3 权重模板 / 定级阈值 / 版本历史（FDS v5.1 §5.2.2 / §5.2.4）
  // ========================================================================

  /** 权重模板单项（单个控制类型的 6 指标权重） */
  export interface WeightTemplateItem {
    controlType: ControlType;
    autoModeRate: number;
    steadyRate: number;
    accuracyRate: number;
    fastRate: number;
    oscillationRate: number;
    saturationRate: number;
  }

  /** 权重模板（4 类控制类型的权重集合） */
  export interface WeightTemplateSchema {
    version: number;
    templates: WeightTemplateItem[];
    updatedAt?: null | string;
    updatedBy?: null | string;
  }

  /** 权重模板保存请求 */
  export interface WeightTemplateSaveRequest {
    templates: WeightTemplateItem[];
    remark?: string;
  }

  /** 定级阈值单项 */
  export interface GradingThresholdItem {
    level: number;
    name: string;
    /** 中文显示名称（可配置，如"优秀"/"良好"/"合格"/"警告"/"不合格"） */
    label?: null | string;
    minScore: number;
    maxScore: number;
    color?: null | string;
  }

  /** 定级阈值配置（5 级） */
  export interface GradingThresholdSchema {
    thresholds: GradingThresholdItem[];
    updatedAt?: null | string;
    updatedBy?: null | string;
  }

  /** 定级阈值更新请求 */
  export interface GradingThresholdSaveRequest {
    thresholds: GradingThresholdItem[];
  }

  /** 可信度阈值单项 */
  export interface ConfidenceThresholdItem {
    level: number;
    name: string;
    minRate: number;
    description?: null | string;
    color?: null | string;
  }

  /** 可信度阈值配置（5 级 A/B/C/D/E） */
  export interface ConfidenceThresholdSchema {
    thresholds: ConfidenceThresholdItem[];
    updatedAt?: null | string;
    updatedBy?: null | string;
  }

  /** 可信度阈值更新请求 */
  export interface ConfidenceThresholdSaveRequest {
    thresholds: ConfidenceThresholdItem[];
  }

  /** 异常值检测控制类型（回路物理类型：流量/压力/温度/液位/成分） */
  export type OutlierControlType = 'CC' | 'FC' | 'LC' | 'PC' | 'TC';

  /** 8 类异常值检测开关键 */
  export type OutlierDetectorKey =
    | 'frozen'
    | 'hf_noise'
    | 'jump'
    | 'nan'
    | 'out_of_range'
    | 'qc_bad'
    | 'spike'
    | 'ts_anomaly';

  /** 单控制类型的异常值检测参数（合并视图中全部非空） */
  export interface OutlierThresholdParams {
    baseSamplingFreq?: null | number;
    frozenWindowPoints?: null | number;
    frozenStdPct?: null | number;
    jumpThresholdPct?: null | number;
    spikeThresholdPct?: null | number;
    noiseCutoffHz?: null | number;
    minConsecutivePoints?: null | number;
  }

  /** 单控制类型阈值合并视图（含每项是否被覆盖标记） */
  export interface OutlierThresholdViewItem {
    controlType: OutlierControlType;
    params: OutlierThresholdParams;
    overridden: Record<string, boolean>;
  }

  /** 8 类异常值检测参数配置合并视图 */
  export interface OutlierParamsSchema {
    thresholds: OutlierThresholdViewItem[];
    switches: Record<string, boolean>;
    updatedAt?: null | string;
    updatedBy?: null | string;
  }

  /** 8 类异常值检测参数配置保存请求（部分覆盖，未覆盖回落算法默认） */
  export interface OutlierParamsSaveRequest {
    thresholds: Partial<Record<OutlierControlType, OutlierThresholdParams>>;
    switches: Partial<Record<OutlierDetectorKey, boolean>>;
  }

  /** 版本历史单项 */
  export interface VersionHistoryItem {
    version: number;
    updatedAt?: null | string;
    updatedBy?: null | string;
    remark?: null | string;
    isCurrent: boolean;
  }

  /** 版本历史列表 */
  export interface VersionHistorySchema {
    items: VersionHistoryItem[];
    currentVersion?: number;
  }

  /** AAS 同步状态 */
  export interface AasSyncStatus {
    enabled: boolean;
    endpoint?: null | string;
    syncIntervalSeconds?: null | number;
    lastSyncAt?: null | string;
    lastSyncStatus?: null | string;
    tagStats: {
      byQuality: Record<string, number>;
      linked: number;
      total: number;
    };
  }

  /** AAS 同步日志项 */
  export interface AasSyncLog {
    id: string;
    operationType: string;
    operator: string;
    operatedAt: string;
    beforeValue?: null | string;
    afterValue?: null | string;
  }

  /** AAS 同步日志列表结果 */
  export interface AasSyncLogListResult {
    items: AasSyncLog[];
    total: number;
  }

  /** 非标任务结果项 */
  export interface TaskResultItem {
    loopId: string;
    loopTagName: string;
    tsStart: null | string;
    tsEnd: null | string;
    score: null | number;
    accuracyRate: null | number;
    fastRate: null | number;
    steadyRate: null | number;
    effectiveAutoRate: null | number;
    goodValueRate: null | number;
    oscillationRate: null | number;
    saturationRate: null | number;
    instrumentFaultRate: null | number;
    autoModeRate: null | number;
    stictionIndex: null | number;
    outputTripIndex: null | number;
    settlingTime: null | number;
    idealSettlingTime: null | number;
    status: string;
    confidenceLevel: null | string;
    validRate: null | number;
    algorithmVersion: null | string;
    samplingFreq: null | string;
    qualityPolicy: null | string;
    dataLineage: null | Record<string, unknown>;
    createdAt: null | string;
  }

  /** 非标任务结果列表结果 */
  export interface TaskResultListResult {
    items: TaskResultItem[];
    total: number;
    taskStatus: string;
  }

  // ========================================================================
  // 节点级 KPI 类型（对齐 IDS v3.2 §6.4 — GB/T 44693.2-2024）
  // ========================================================================

  /** 节点类型筛选 */
  export type NodeFilterType = 'EQUIPMENT' | 'FACTORY' | 'UNIT';

  /** 节点监控维度 */
  export type NodeMonitorDimension = 'day' | 'hour' | 'month';

  /** 节点级快照项（对齐 NodeSnapshotItem） */
  export interface NodeSnapshotItem {
    plantNodeId: string;
    plantNodeName?: null | string;
    tsStart?: null | string;
    tsEnd?: null | string;
    score?: null | number;
    goodValueRate?: null | number;
    autoModeRate?: null | number;
    effectiveAutoRate?: null | number;
    steadyRate?: null | number;
    accuracyRate?: null | number;
    fastRate?: null | number;
    oscillationRate?: null | number;
    saturationRate?: null | number;
    instrumentFaultRate?: null | number;
    autoLoopRatio?: null | number;
    realtimeAutoRate?: null | number;
    loopCount: number;
    status: KpiStatus;
    algorithmVersion?: null | string;
  }

  /** 节点排名项（对齐 NodeRankingItem） */
  export interface NodeRankingItem {
    rank: number;
    plantNodeId: string;
    plantNodeName?: null | string;
    plantNodeType?: null | string;
    tsStart?: null | string;
    score?: null | number;
    goodValueRate?: null | number;
    autoModeRate?: null | number;
    effectiveAutoRate?: null | number;
    steadyRate?: null | number;
    accuracyRate?: null | number;
    fastRate?: null | number;
    oscillationRate?: null | number;
    saturationRate?: null | number;
    instrumentFaultRate?: null | number;
    autoLoopRatio?: null | number;
    realtimeAutoRate?: null | number;
    loopCount: number;
    status: KpiStatus;
    algorithmVersion?: null | string;
  }

  /** 节点趋势序列（对齐 NodeTrendSeries） */
  export interface NodeTrendSeries {
    metricKey: string;
    metricName: string;
    values: (null | number)[];
  }

  /** 节点趋势数据（对齐 NodeTrendData） */
  export interface NodeTrendData {
    plantNodeId: string;
    plantNodeName?: null | string;
    timestamps: string[];
    series: NodeTrendSeries[];
  }

  /** 全厂总览单节点项（对齐 NodeOverviewItem） */
  export interface NodeOverviewItem {
    plantNodeId: string;
    plantNodeName?: null | string;
    plantNodeType?: null | string;
    score?: null | number;
    autoLoopRatio?: null | number;
    realtimeAutoRate?: null | number;
    steadyRate?: null | number;
    effectiveAutoRate?: null | number;
    loopCount: number;
    status: KpiStatus;
    tsStart?: null | string;
  }

  /** 全厂总览（对齐 NodeOverviewData） */
  export interface NodeOverviewData {
    totalNodes: number;
    nodesWithSnapshot: number;
    nodes: NodeOverviewItem[];
    statusDistribution: Record<string, number>;
  }

  /** 节点计算请求（对齐 NodeCalculateRequest） */
  export interface NodeCalculateRequest {
    tsStart?: null | string;
    tsEnd?: null | string;
  }

  /** 节点计算结果（对齐 NodeCalculateResult） */
  export interface NodeCalculateResult {
    plantNodeId: string;
    status: string;
    snapshot?: null | Record<string, unknown>;
    reason?: null | string;
  }

  /** 节点监控快照（对齐 NodeMonitorSnapshot，兼容 hour/day/month） */
  export interface NodeMonitorSnapshot {
    plantNodeId: string;
    plantNodeName?: null | string;
    dimension: NodeMonitorDimension;
    tsStart?: null | string;
    tsEnd?: null | string;
    score?: null | number;
    goodValueRate?: null | number;
    autoModeRate?: null | number;
    effectiveAutoRate?: null | number;
    steadyRate?: null | number;
    accuracyRate?: null | number;
    fastRate?: null | number;
    oscillationRate?: null | number;
    saturationRate?: null | number;
    instrumentFaultRate?: null | number;
    autoLoopRatio?: null | number;
    realtimeAutoRate?: null | number;
    loopCount: number;
    status: KpiStatus;
    algorithmVersion?: null | string;
  }

  /** 节点监控数据（对齐 NodeMonitorData） */
  export interface NodeMonitorData {
    plantNodeId: string;
    plantNodeName?: null | string;
    dimension: NodeMonitorDimension;
    start?: null | string;
    end?: null | string;
    snapshots: NodeMonitorSnapshot[];
  }

  /** 节点排名查询参数 */
  export interface NodeRankingQueryParams {
    timeWindow: TimeWindow;
    nodeType?: NodeFilterType;
    sortBy?: 'autoLoopRatio' | 'effectiveAutoRate' | 'score' | 'steadyRate';
    sortOrder?: 'asc' | 'desc';
    limit?: number;
  }
}

const BASE = '/performance';

/**
 * 获取指标配置列表 — IDS v3.2 §2.3
 */
export function getMetricsApi() {
  return requestClient.get<MetricApi.MetricListResult>(`${BASE}/metrics`);
}

/**
 * 更新指标配置 — IDS v3.2 §2.3（仅 ADMIN）
 */
export function updateMetricApi(
  metricId: string,
  data: MetricApi.MetricUpdateParams,
) {
  return requestClient.put<MetricApi.MetricItem>(
    `${BASE}/metrics/${metricId}`,
    data,
  );
}

/**
 * 获取引擎规则列表 — IDS v3.2 §2.3
 * 后端返回裸数组 ApiResponse<list[EngineRuleItem]>
 */
export function getRulesApi() {
  return requestClient.get<MetricApi.RuleItem[]>(`${BASE}/rules`);
}

/**
 * 更新引擎规则 — IDS v3.2 §2.3（仅 ADMIN）
 */
export function updateRuleApi(
  ruleId: string,
  data: MetricApi.RuleUpdateParams,
) {
  return requestClient.put<MetricApi.RuleItem>(`${BASE}/rules/${ruleId}`, data);
}

/**
 * 获取低效回路排行 — IDS v3.2 §2.3
 */
export function getRankingApi(params: MetricApi.RankingQueryParams) {
  return requestClient.get<MetricApi.RankingItem[]>(`${BASE}/ranking`, {
    params,
  });
}

/**
 * 获取统计报表 — IDS v3.2 §2.3
 */
export function getAnalyticsApi(params: {
  endTime: string;
  granularity?: Granularity;
  metricKey?: string;
  plantNodeId?: string;
  startTime: string;
}) {
  return requestClient.get<MetricApi.AnalyticsResult>(`${BASE}/analytics`, {
    params,
  });
}

/**
 * 导出统计报表 — IDS v3.2 §2.3
 */
export function exportAnalyticsApi(data: MetricApi.AnalyticsExportParams) {
  return requestClient.post<MetricApi.AnalyticsExportResult>(
    `${BASE}/analytics/export`,
    data,
  );
}

/**
 * 获取回路类型权重列表 — 配置项
 *
 * P2 #30 B7: API 前缀从 /config/ 统一为 /configs/（与 /configs/metrics 对齐）
 */
export function getLoopTypeWeightsApi() {
  return requestClient.get<MetricApi.LoopTypeWeightListResult>(
    '/configs/loop-type-weights',
  );
}

/**
 * 更新回路类型权重 — 仅 ADMIN
 */
export function updateLoopTypeWeightApi(
  loopType: ControlType,
  data: MetricApi.LoopTypeWeightUpdateParams,
) {
  return requestClient.put<MetricApi.LoopTypeWeightItem>(
    `/configs/loop-type-weights/${loopType}`,
    data,
  );
}

/**
 * 获取回路级别权重列表 — 配置项
 *
 * P2 #30 B7: API 前缀从 /config/ 统一为 /configs/
 */
export function getLoopLevelWeightsApi() {
  return requestClient.get<MetricApi.LoopLevelWeightListResult>(
    '/configs/loop-level-weights',
  );
}

/**
 * 更新回路级别权重 — 仅 ADMIN
 */
export function updateLoopLevelWeightApi(
  level: MetricApi.LoopLevel,
  data: MetricApi.LoopLevelWeightUpdateParams,
) {
  return requestClient.put<MetricApi.LoopLevelWeightItem>(
    `/configs/loop-level-weights/${level}`,
    data,
  );
}

/**
 * 获取实时自控率统计 — 用于仪表盘组件
 */
export function getRealtimeAutoRateApi(params?: { plantNodeId?: string }) {
  return requestClient.get<MetricApi.RealtimeAutoRateResult>(
    `${BASE}/realtime-auto-rate`,
    { params },
  );
}

// ===========================================================================
// 节点级 KPI API（对齐 IDS v3.2 §6.4 — GB/T 44693.2-2024）
// 后端端点定义见 backend/app/api/v1/endpoints/node_performance.py
// ===========================================================================

const NODE_BASE = '/performance/nodes';

/**
 * 获取节点最新性能快照 — GET /performance/nodes/{nodeId}/snapshot
 */
export function getNodeSnapshotApi(nodeId: string) {
  return requestClient.get<MetricApi.NodeSnapshotItem | null>(
    `${NODE_BASE}/${nodeId}/snapshot`,
  );
}

/**
 * 获取节点历史趋势 — GET /performance/nodes/{nodeId}/trend
 */
export function getNodeTrendApi(
  nodeId: string,
  params: { endTime: string; startTime: string },
) {
  return requestClient.get<MetricApi.NodeTrendData>(
    `${NODE_BASE}/${nodeId}/trend`,
    {
      params,
    },
  );
}

/**
 * 获取节点间性能排名 — GET /performance/nodes/ranking
 */
export function getNodeRankingApi(params: MetricApi.NodeRankingQueryParams) {
  return requestClient.get<MetricApi.NodeRankingItem[]>(
    `${NODE_BASE}/ranking`,
    {
      params,
    },
  );
}

/**
 * 手动触发节点级 KPI 聚合（仅 ADMIN/ENGINEER）
 * POST /performance/nodes/{nodeId}/calculate
 *
 * 不传 tsStart/tsEnd 时默认计算上一个完整小时。
 */
export function calculateNodeApi(
  nodeId: string,
  data?: MetricApi.NodeCalculateRequest,
) {
  return requestClient.post<MetricApi.NodeCalculateResult>(
    `${NODE_BASE}/${nodeId}/calculate`,
    data ?? {},
  );
}

/**
 * 获取全厂总览 — GET /performance/nodes/overview
 *
 * 汇总所有启用 KPI 评估的节点最新快照。
 */
export function getNodesOverviewApi(params: { timeWindow: TimeWindow }) {
  return requestClient.get<MetricApi.NodeOverviewData>(
    `${NODE_BASE}/overview`,
    {
      params,
    },
  );
}

/**
 * 获取节点多维度监控数据（hour/day/month）
 * GET /performance/nodes/{nodeId}/monitor
 */
export function getNodeMonitorApi(
  nodeId: string,
  params: {
    dimension: MetricApi.NodeMonitorDimension;
    end: string;
    start: string;
  },
) {
  return requestClient.get<MetricApi.NodeMonitorData>(
    `${NODE_BASE}/${nodeId}/monitor`,
    { params },
  );
}

// ===========================================================================
// v5.3 权重模板管理 API（FDS v5.1 §5.2.2）
// ===========================================================================

const WEIGHT_BASE = '/configs/weight-templates';

/**
 * 获取当前权重模板 — FDS v5.1 §5.2.2
 */
export function getWeightTemplatesApi() {
  return requestClient.get<MetricApi.WeightTemplateSchema>(WEIGHT_BASE);
}

/**
 * 保存权重模板为新版本 — 仅 ADMIN
 */
export function saveWeightTemplatesApi(
  data: MetricApi.WeightTemplateSaveRequest,
) {
  return requestClient.post<MetricApi.WeightTemplateSchema>(WEIGHT_BASE, data);
}

/**
 * 获取权重模板版本历史 — FDS v5.1 §5.2.2
 */
export function getWeightTemplateHistoryApi() {
  return requestClient.get<MetricApi.VersionHistorySchema>(
    `${WEIGHT_BASE}/history`,
  );
}

/**
 * 回滚到指定版本 — 仅 ADMIN
 */
export function rollbackWeightTemplateApi(version: number) {
  return requestClient.post<MetricApi.WeightTemplateSchema>(
    `${WEIGHT_BASE}/${version}/rollback`,
  );
}

/**
 * 恢复国标默认权重模板 — 仅 ADMIN
 */
export function restoreWeightDefaultsApi() {
  return requestClient.post<MetricApi.WeightTemplateSchema>(
    `${WEIGHT_BASE}/restore-defaults`,
  );
}

// ===========================================================================
// v5.3 定级阈值管理 API（FDS v5.1 §5.2.4）
// ===========================================================================

const GRADING_BASE = '/configs/grading-thresholds';

/**
 * 获取当前定级阈值 — FDS v5.1 §5.2.4
 */
export function getGradingThresholdsApi() {
  return requestClient.get<MetricApi.GradingThresholdSchema>(GRADING_BASE);
}

/**
 * 更新定级阈值 — 仅 ADMIN
 */
export function saveGradingThresholdsApi(
  data: MetricApi.GradingThresholdSaveRequest,
) {
  return requestClient.post<MetricApi.GradingThresholdSchema>(
    GRADING_BASE,
    data,
  );
}

// ===========================================================================
// 数据可信度阈值管理 API
// ===========================================================================

const CONFIDENCE_BASE = '/configs/confidence-thresholds';

/**
 * 获取当前数据可信度阈值
 */
export function getConfidenceThresholdsApi() {
  return requestClient.get<MetricApi.ConfidenceThresholdSchema>(
    CONFIDENCE_BASE,
  );
}

/**
 * 更新数据可信度阈值 — 仅 ADMIN
 */
export function saveConfidenceThresholdsApi(
  data: MetricApi.ConfidenceThresholdSaveRequest,
) {
  return requestClient.post<MetricApi.ConfidenceThresholdSchema>(
    CONFIDENCE_BASE,
    data,
  );
}

// ===========================================================================
// 8 类异常值检测参数配置 API（阈值覆盖 + 启停开关）
// ===========================================================================

const OUTLIER_PARAMS_BASE = '/configs/outlier-params';

/**
 * 获取异常值检测参数配置合并视图（默认值 + 覆盖标记 + 检测开关）
 */
export function getOutlierParamsApi() {
  return requestClient.get<MetricApi.OutlierParamsSchema>(OUTLIER_PARAMS_BASE);
}

/**
 * 更新异常值检测参数覆盖与检测开关 — 仅 ADMIN
 */
export function saveOutlierParamsApi(data: MetricApi.OutlierParamsSaveRequest) {
  return requestClient.put<MetricApi.OutlierParamsSchema>(
    OUTLIER_PARAMS_BASE,
    data,
  );
}

// ===========================================================================
// 回路小时指标快照列表 — GET /performance/loops/snapshots
// ===========================================================================

const SNAPSHOTS_BASE = '/performance/loops/snapshots';

/** 回路小时指标快照列表项（24 字段 + loopTagName） */
export interface KpiSnapshotItem {
  loopId: null | string;
  loopTagName: null | string;
  tsStart: null | string;
  tsEnd: null | string;
  score: null | number;
  goodValueRate: null | number;
  autoModeRate: null | number;
  effectiveAutoRate: null | number;
  steadyRate: null | number;
  accuracyRate: null | number;
  oscillationRate: null | number;
  saturationRate: null | number;
  instrumentFaultRate: null | number;
  fastRate: null | number;
  stictionIndex: null | number;
  settlingTime: null | number;
  outputTravelIndex: null | number;
  status: KpiStatus;
  idealSettlingTime: null | number;
  algorithmVersion: null | string;
  samplingFreq: null | string;
  qualityPolicy: null | string;
  validRate: null | number;
  confidenceLevel: ConfidenceLevel | null;
  dataLineage: MetricApi.DataLineage | null;
}

/** 快照列表响应 */
export interface KpiSnapshotListResult {
  items: KpiSnapshotItem[];
  total: number;
  page: number;
  pageSize: number;
}

/** 快照列表查询参数 */
export interface KpiSnapshotQueryParams {
  /** 回路 ID（逗号分隔多个） */
  loopId?: string;
  /** 装置 ID（逗号分隔多个） */
  plantNodeId?: string;
  /** 起始时间（ISO 8601） */
  startTime?: string;
  /** 结束时间（ISO 8601） */
  endTime?: string;
  /** 快照状态 */
  status?: KpiStatus;
  /** 可信度等级 */
  confidenceLevel?: ConfidenceLevel;
  /** 回路编号模糊搜索 */
  loopTagName?: string;
  /** True=每个回路只返回最新一条评估记录（默认）；False=返回所有快照 */
  latestOnly?: boolean;
  /** 页码 */
  page?: number;
  /** 每页条数 */
  pageSize?: number;
}

/**
 * 查询回路小时指标快照列表
 *
 * 按回路/装置/时间范围/状态/可信度筛选，分页返回。
 * 默认时间范围为近 30 天，排序按 tsStart DESC。
 */
export function getLoopSnapshotsApi(params: KpiSnapshotQueryParams) {
  return requestClient.get<KpiSnapshotListResult>(SNAPSHOTS_BASE, { params });
}

// ===========================================================================
// 回路最新可信度评估记录 — GET /loops/{loopId}/confidence-latest
// ===========================================================================

/** 单个子指标的计算值与可信度（metrics JSONB 元素） */
export interface LoopConfidenceMetricDetail {
  value: null | number;
  confidence: ConfidenceLevel | null;
}

/** 回路最新一次可信度评估记录（loop_confidence_latest，每回路一条） */
export interface LoopConfidenceLatestItem {
  loopId: string;
  /** 评估时间（快照写入时刻） */
  evalTime: null | string;
  /** 数据源时间区间 */
  dataTsStart: null | string;
  dataTsEnd: null | string;
  status: KpiStatus;
  score: null | number;
  confidenceLevel: ConfidenceLevel | null;
  validRate: null | number;
  /**
   * 12 子指标（3+1+8 体系）计算值与各自可信度，
   * 键为 DB 列名（snake_case），如 accuracy_rate / steady_rate / settling_time
   */
  metrics: Record<string, LoopConfidenceMetricDetail>;
  algorithmVersion: null | string;
  updatedAt: null | string;
}

/**
 * 获取回路最新一次可信度评估记录
 *
 * 无评估记录时后端返回 data=null（HTTP 200），前端据此展示"暂无评估记录"。
 */
export function getLoopConfidenceLatestApi(loopId: string) {
  return requestClient.get<LoopConfidenceLatestItem | null>(
    `/loops/${loopId}/confidence-latest`,
  );
}
