/**
 * CLPM 性能评估 API（对齐 IDS v3.2 §2.3）
 *
 * 覆盖指标配置、引擎规则、全局看板、低效回路排行、统计报表五类子能力。
 * 所有接口前缀 `/performance`，响应格式 `{code, message, data}`。
 */
import { requestClient } from '#/api/request';

/** KPI 状态色标 */
export type KpiStatus = 'SUCCESS' | 'INCONCLUSIVE' | 'PARTIAL';

/** 控制类型 */
export type ControlType = 'STABLE' | 'SLOW' | 'FAST' | 'LOGIC';

/** 时间窗枚举 */
export type TimeWindow = 'last_7_days' | 'last_30_days' | 'today' | 'yesterday';

/** 报表粒度 */
export type Granularity = 'day' | 'hour' | 'month' | 'week';

/** 执行状态 */
export type ExecutionStatus = 'FAILED' | 'RUNNING' | 'SUCCESS';

/** 处理状态 */
export type ActionStatus = 'PENDING' | 'IN_PROGRESS' | 'RESOLVED' | 'IGNORED';

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
