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
    good_value_rate: number;
    auto_mode_rate: number;
    effective_auto_rate: number;
    steady_rate: number;
    accuracy_rate: number;
    fast_response_rate: number;
    oscillation_rate: number;
    saturation_rate: number;
    composite_score: number;
    status: KpiStatus;
    algorithm_version: string;
    /** v4.0 数据血缘字段（7 个，对齐 KpiSnapshotSchema） */
    ideal_settling_time?: null | number;
    sampling_freq?: null | string;
    quality_policy?: null | string;
    valid_rate?: null | number;
    confidence_level?: null | ConfidenceLevel;
    data_lineage?: DataLineage | null;
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
    unitName: string;
    compositeScore: number;
    goodValueRate: number;
    autoModeRate: number;
    effectiveAutoRate: number;
    steadyRate: number;
    accuracyRate: number;
    fastResponseRate: number;
    oscillationRate: number;
    saturationRate: number;
    status: KpiStatus;
    algorithmVersion: string;
    preDiagnosis?: string;
    actionStatus: ActionStatus;
    /** v4.0 数据血缘字段（对齐后端 RankingItem schema） */
    confidenceLevel?: null | ConfidenceLevel;
    validRate?: null | number;
    samplingFreq?: null | string;
    qualityPolicy?: null | string;
    idealSettlingTime?: null | number;
    dataLineage?: DataLineage | null;
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
    fastResponseRate?: null | number;
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
    fastResponseRate?: null | number;
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
    fastResponseRate?: null | number;
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
 */
export function getLoopTypeWeightsApi() {
  return requestClient.get<MetricApi.LoopTypeWeightListResult>(
    '/config/loop-type-weights',
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
    `/config/loop-type-weights/${loopType}`,
    data,
  );
}

/**
 * 获取回路级别权重列表 — 配置项
 */
export function getLoopLevelWeightsApi() {
  return requestClient.get<MetricApi.LoopLevelWeightListResult>(
    '/config/loop-level-weights',
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
    `/config/loop-level-weights/${level}`,
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
