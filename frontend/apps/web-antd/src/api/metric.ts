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

  /** 引擎规则项 */
  export interface RuleItem {
    ruleId: string;
    ruleName: string;
    calcPeriod: string;
    dataFetchWindow: string;
    scheduleConcurrency: number;
    isEnabled: boolean;
    lastExecutedAt?: string;
    lastExecutionStatus?: ExecutionStatus;
    lastExecutionDuration?: number;
    processedLoopCount?: number;
    updatedAt: string;
    updatedBy: string;
    /** P3 #51: EVAL_CALC_CYCLE 变更时返回，提示 Beat 进程需重启 */
    warning?: string;
  }

  /** 引擎规则列表响应 */
  export interface RuleListResult {
    items: RuleItem[];
  }

  /** 更新引擎规则参数 */
  export interface RuleUpdateParams {
    calcPeriod: string;
    dataFetchWindow: string;
    scheduleConcurrency: number;
    isEnabled: boolean;
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
    compositeScore: number;
    status: KpiStatus;
    algorithmVersion: string;
    /** v4.0 数据血缘字段（7 个，对齐 KpiSnapshotSchema） */
    idealSettlingTime?: null | number;
    samplingFreq?: null | string;
    qualityPolicy?: null | string;
    validRate?: null | number;
    confidenceLevel?: null | ConfidenceLevel;
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
    loopName: string | null;
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
    status: KpiStatus;
    algorithmVersion: string;
    preDiagnosis?: string;
    actionStatus: ActionStatus;
    includeInEvaluation?: boolean | null;
    /** v4.0 数据血缘字段（对齐后端 RankingItem schema） */
    confidenceLevel?: null | ConfidenceLevel;
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
    updatedAt?: string | null;
    updatedBy?: string | null;
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
    minScore: number;
    maxScore: number;
    color?: string | null;
  }

  /** 定级阈值配置（5 级） */
  export interface GradingThresholdSchema {
    thresholds: GradingThresholdItem[];
    updatedAt?: string | null;
    updatedBy?: string | null;
  }

  /** 定级阈值更新请求 */
  export interface GradingThresholdSaveRequest {
    thresholds: GradingThresholdItem[];
  }

  /** 版本历史单项 */
  export interface VersionHistoryItem {
    version: number;
    updatedAt?: string | null;
    updatedBy?: string | null;
    remark?: string | null;
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
    endpoint?: string | null;
    syncIntervalSeconds?: number | null;
    lastSyncAt?: string | null;
    lastSyncStatus?: string | null;
    tagStats: {
      total: number;
      linked: number;
      byQuality: Record<string, number>;
    };
  }

  /** AAS 同步日志项 */
  export interface AasSyncLog {
    id: string;
    operationType: string;
    operator: string;
    operatedAt: string;
    beforeValue?: string | null;
    afterValue?: string | null;
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
    tsStart: string | null;
    tsEnd: string | null;
    score: number | null;
    accuracyRate: number | null;
    fastRate: number | null;
    steadyRate: number | null;
    effectiveAutoRate: number | null;
    goodValueRate: number | null;
    oscillationRate: number | null;
    saturationRate: number | null;
    autoModeRate: number | null;
    stictionIndex: number | null;
    outputTripIndex: number | null;
    settlingTime: number | null;
    idealSettlingTime: number | null;
    status: string;
    confidenceLevel: string | null;
    validRate: number | null;
    algorithmVersion: string | null;
    samplingFreq: string | null;
    qualityPolicy: string | null;
    dataLineage: Record<string, unknown> | null;
    createdAt: string | null;
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
    values: (number | null)[];
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
 */
export function getRulesApi() {
  return requestClient.get<MetricApi.RuleListResult>(`${BASE}/rules`);
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
 * 获取全局看板 — IDS v3.2 §2.3
 */
export function getBoardApi(params: {
  plantNodeId?: string;
  timeWindow: TimeWindow;
}) {
  return requestClient.get<MetricApi.BoardResult>(`${BASE}/board`, {
    params,
  });
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
  params: { startTime: string; endTime: string },
) {
  return requestClient.get<MetricApi.NodeTrendData>(`${NODE_BASE}/${nodeId}/trend`, {
    params,
  });
}

/**
 * 获取节点间性能排名 — GET /performance/nodes/ranking
 */
export function getNodeRankingApi(params: MetricApi.NodeRankingQueryParams) {
  return requestClient.get<MetricApi.NodeRankingItem[]>(`${NODE_BASE}/ranking`, {
    params,
  });
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
  return requestClient.get<MetricApi.NodeOverviewData>(`${NODE_BASE}/overview`, {
    params,
  });
}

/**
 * 获取节点多维度监控数据（hour/day/month）
 * GET /performance/nodes/{nodeId}/monitor
 */
export function getNodeMonitorApi(
  nodeId: string,
  params: {
    dimension: MetricApi.NodeMonitorDimension;
    start: string;
    end: string;
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
export function saveWeightTemplatesApi(data: MetricApi.WeightTemplateSaveRequest) {
  return requestClient.post<MetricApi.WeightTemplateSchema>(WEIGHT_BASE, data);
}

/**
 * 获取权重模板版本历史 — FDS v5.1 §5.2.2
 */
export function getWeightTemplateHistoryApi() {
  return requestClient.get<MetricApi.VersionHistorySchema>(`${WEIGHT_BASE}/history`);
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
export function saveGradingThresholdsApi(data: MetricApi.GradingThresholdSaveRequest) {
  return requestClient.post<MetricApi.GradingThresholdSchema>(GRADING_BASE, data);
}

// ===========================================================================
// 回路小时指标快照列表 — GET /performance/loops/snapshots
// ===========================================================================

const SNAPSHOTS_BASE = '/performance/loops/snapshots';

/** 回路小时指标快照列表项（24 字段 + loopTagName） */
export interface KpiSnapshotItem {
  loopId: string | null;
  loopTagName: string | null;
  tsStart: string | null;
  tsEnd: string | null;
  score: number | null;
  goodValueRate: number | null;
  autoModeRate: number | null;
  effectiveAutoRate: number | null;
  steadyRate: number | null;
  accuracyRate: number | null;
  oscillationRate: number | null;
  saturationRate: number | null;
  fastRate: number | null;
  stictionIndex: number | null;
  settlingTime: number | null;
  outputTravelIndex: number | null;
  status: KpiStatus;
  idealSettlingTime: number | null;
  algorithmVersion: string | null;
  samplingFreq: string | null;
  qualityPolicy: string | null;
  validRate: number | null;
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
  /** 页码 */
  page?: number;
  /** 每页条数 */
  pageSize?: number;
}

/**
 * 查询回路小时指标快照列表
 *
 * 按回路/装置/时间范围/状态/可信度筛选，分页返回。
 * 默认时间范围为近 7 天，排序按 tsStart DESC。
 */
export function getLoopSnapshotsApi(params: KpiSnapshotQueryParams) {
  return requestClient.get<KpiSnapshotListResult>(SNAPSHOTS_BASE, { params });
}
