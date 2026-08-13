/**
 * CLPM 工作台聚合 API（对齐 IDS v3.2 §2.1 BFF 聚合层）
 *
 * 工作台首页聚合数据：6 大 KPI 卡片 + 低效回路列表 + 趋势摘要 + 待处理异常。
 * - API 前缀：/api/v1/dashboard/
 *
 * 注意：类型仅在 DashboardApi 命名空间内导出，避免与 index.ts 的 `export *` 冲突。
 * DiagnosisLabel 复用 diagnosis.ts 的定义，保证标签语义一致。
 */
import type { DiagnosisLabel } from '#/api/diagnosis';

import { requestClient } from '#/api/request';

export namespace DashboardApi {
  /** 工作台统计粒度（UI/UX v4.1 §6.1.1） */
  export type Granularity = 'day' | 'month' | 'week';

  /** 趋势方向 */
  export type Trend = 'down' | 'stable' | 'up';

  /** KPI 卡片单项 */
  export interface KpiCard {
    /** 当前值 */
    value: number;
    /** 单位 */
    unit: string;
    /** 趋势方向 */
    trend: Trend;
    /** 同比变化量（正负） */
    delta: number;
  }

  /** 8 大 KPI 卡片数据（IDS v3.2 §2.1，对齐 GB/T 44693.2-2024） */
  export interface KpiCards {
    /** 自控投用率 */
    auto_mode_rate: KpiCard;
    /** 有效自控率 */
    effective_auto_rate: KpiCard;
    /** 平稳率 */
    steady_rate: KpiCard;
    /** 综合评分 */
    composite_score: KpiCard;
    /** 报警次数 */
    alarm_count: KpiCard;
    /** 操作频次 */
    operation_count: KpiCard;
    /** 好值率 */
    good_value_rate: KpiCard;
    /** 快速率 */
    fast_response_rate: KpiCard;
  }

  /** 低效回路关键指标 */
  export interface KeyMetric {
    /** 自控投用率 */
    auto_mode_rate: number;
    /** 平稳率 */
    steady_rate: number;
  }

  /** 低效回路列表项 */
  export interface InefficientLoop {
    /** 回路 ID */
    loop_id: string;
    /** 回路位号 */
    loop_tag: string;
    /** 回路名称 */
    loop_name: string;
    /** 装置名称 */
    plant_name: string;
    /** 综合评分 */
    composite_score: number;
    /** 预诊标签列表（8 类诊断标签） */
    diagnosis_labels: DiagnosisLabel[];
    /** 关键指标 */
    key_metric: KeyMetric;
  }

  /** 趋势摘要 */
  export interface TrendSummary {
    /** 日期列表 */
    dates: string[];
    /** 综合评分序列 */
    composite_scores: number[];
  }

  /** 待处理异常 */
  export interface PendingAlerts {
    /** 待处理诊断数 */
    open_diagnoses: number;
    /** 待处理 Tracker 数 */
    open_trackers: number;
  }

  /** 工作台概览响应（IDS v3.2 §2.1） */
  export interface OverviewResult {
    /** 6 大 KPI 卡片 */
    kpi_cards: KpiCards;
    /** 低效回路列表（按评分升序） */
    inefficient_loops: InefficientLoop[];
    /** 趋势摘要 */
    trend_summary: TrendSummary;
    /** 待处理异常 */
    pending_alerts: PendingAlerts;
  }

  /** 工作台概览查询参数 */
  export interface OverviewQueryParams {
    /** 工厂节点 ID（全厂/装置/单元） */
    plantId?: string;
    /** 统计粒度 */
    granularity?: Granularity;
  }

  /** 装置级 KPI 看板单项（来自 unit_kpi_summary） */
  export interface BoardItem {
    nodeId: string;
    nodeName: null | string;
    snapshotTime: null | string;
    avgScore: null | number;
    autoModeRate: null | number;
    stabilityRate: null | number;
    effectiveAutoRate: null | number;
    accuracyRate: null | number;
    fastRate: null | number;
    goodValueRate: null | number;
    oscillationRate: null | number;
    saturationRate: null | number;
    instrumentFaultRate: null | number;
    totalLoops: number;
    evaluatedLoops: number;
    inconclusiveLoops: number;
    excludedLoops: number;
    status: string;
    algorithmVersion: null | string;
  }

  /** 装置级 KPI 看板结果 */
  export interface BoardResult {
    items: BoardItem[];
    total: number;
  }

  /** 实时自控率结果 */
  export interface AutoRateRt {
    rate: null | number;
    autoCount: number;
    manualCount: number;
    totalCount: number;
    /** 5 种标准 MODE 值各自的回路数（key 为 "0"/"1"/"2"/"3"/"4"） */
    modeCounts?: Record<string, number>;
    readAt: null | string;
    message?: string;
  }

  /** 节点级聚合 KPI 结果（v6.1 新增） */
  export interface BoardAggregateResult {
    items: BoardItem[];
    total: number;
    aggregate: {
      accuracyRate: null | number;
      autoModeRate: null | number;
      avgScore: null | number;
      effectiveAutoRate: null | number;
      evaluatedLoops: number;
      excludedLoops: number;
      fastRate: null | number;
      goodValueRate: null | number;
      inconclusiveLoops: number;
      instrumentFaultRate: null | number;
      nodeId: null | string;
      nodeName: null | string;
      stabilityRate: null | number;
      totalLoops: number;
    };
    /** 统计窗口回显（仅指定 timeWindow 时返回，v6.1.4） */
    timeWindow?: string;
    windowStart?: string;
    windowEnd?: string;
  }

  /** 节点级聚合趋势结果（v6.1 新增） */
  export interface BoardTrendResult {
    timestamps: string[];
    avgScore: (null | number)[];
    autoModeRate: (null | number)[];
    stabilityRate: (null | number)[];
    evaluatedLoops: number[];
    totalLoops: number;
  }

  // -------------------------------------------------------------------------
  // P3-05：异常预测与提前预警
  // -------------------------------------------------------------------------

  /** 风险等级 */
  export type RiskLevel = 'HIGH' | 'LOW' | 'MEDIUM';

  /** 趋势分析覆盖的指标键（对齐后端 _prediction_to_dict） */
  export type PredictionMetricKey =
    | 'oscillation_rate'
    | 'saturation_rate'
    | 'score'
    | 'steady_rate';

  /** 单个指标的趋势分析结果 */
  export interface MetricTrend {
    /** 当前值 */
    currentValue: null | number;
    /** 每小时变化量（斜率） */
    slope: null | number;
    /** 未来 24h 预测值 */
    projectedValue: null | number;
    /** 是否为风险方向（score/steady 下降，oscillation/saturation 上升） */
    isRisky: boolean;
  }

  /** 单回路预测结果 */
  export interface LoopPrediction {
    /** 回路 ID */
    loopId: string;
    /** 回路位号 */
    tagName: string;
    /** 回路描述 */
    description: null | string;
    /** 装置名称 */
    plantName: null | string;
    /** 综合风险分（0-100） */
    riskScore: number;
    /** 风险等级 */
    riskLevel: RiskLevel;
    /** 主要风险因素描述列表 */
    riskFactors: string[];
    /** 各指标趋势分析 */
    trends: Partial<Record<PredictionMetricKey, MetricTrend>>;
    /** 最近诊断标签 */
    recentDiagnosisLabels: string[];
    /** 参与分析的数据点数 */
    dataPoints: number;
  }

  /** 异常预测查询参数 */
  export interface PredictionQueryParams {
    /** 按装置筛选；为空分析全厂 */
    plantId?: string;
    /** 返回的高风险回路数（1-50） */
    topN?: number;
  }

  /** 异常预测响应（P3-05） */
  export interface PredictionResult {
    /** 高风险回路列表（按风险分降序，仅含 MEDIUM+HIGH） */
    predictions: LoopPrediction[];
    /** 实际参与趋势分析的回路数 */
    totalLoopsAnalyzed: number;
    /** 符合预测条件的活跃回路总数 */
    totalLoopsEligible: number;
    /** HIGH 风险回路数 */
    highRiskCount: number;
    /** MEDIUM 风险回路数 */
    mediumRiskCount: number;
    /** 预测生成时间（ISO 字符串） */
    generatedAt: string;
    /** 预测时间跨度（小时） */
    forecastHorizonHours: number;
    /** 是否命中 Redis 缓存 */
    cached?: boolean;
  }

  // -------------------------------------------------------------------------
  // 04-系统概览 标杆页聚合接口
  // -------------------------------------------------------------------------

  /** 系统概览 KPI 统计带 */
  export interface SystemOverviewSummary {
    totalLoops: number;
    evaluatedLoops: number;
    inconclusiveLoops: number;
    excludedLoops: number;
    avgScore: number | null;
    autoModeRate: number | null;
    stabilityRate: number | null;
    attentionCount: number;
    pendingTrackerCount: number;
  }

  /** 评分等级分布 */
  export interface ScoreDistribution {
    /** 差（<70分） */
    poor: number;
    /** 一般（70-84分） */
    fair: number;
    /** 良好（85-94分） */
    good: number;
    /** 优秀（≥95分） */
    excellent: number;
  }

  /** 关注队列汇总 */
  export interface AttentionSummary {
    alertCount: number;
    degradationCount: number;
    dataQualityCount: number;
    trackerCount: number;
    total: number;
    pendingCount: number;
  }

  /** 实时自控率 */
  export interface AutoRate {
    rate: number | null;
    autoCount: number;
    manualCount: number;
    totalCount: number;
    modeCounts: Record<string, number>;
    readAt: string | null;
  }

  /** Top 问题回路 */
  export interface TopLoopItem {
    loopId: string;
    tagName: string;
    description: string | null;
    score: number | null;
    autoModeRate: number | null;
    unitName: string | null;
  }

  /** 趋势数据 */
  export interface OverviewTrend {
    timestamps: string[];
    avgScore: (number | null)[];
    autoModeRate: (number | null)[];
    stabilityRate: (number | null)[];
  }

  /** 对比指标 */
  export interface OverviewCompare {
    scoreDelta: number | null;
    autoDelta: number | null;
    stabilityDelta: number | null;
  }

  /** 系统概览聚合响应 */
  export interface SystemOverviewResult {
    summary: SystemOverviewSummary;
    scoreDistribution: ScoreDistribution;
    attentionSummary: AttentionSummary;
    autoRate: AutoRate;
    diagnosisDistribution: Record<string, number>;
    topLoops: TopLoopItem[];
    trend: OverviewTrend;
    compare: OverviewCompare;
    timeWindow: string;
    windowStart: string;
    windowEnd: string;
  }
}

/**
 * 获取工作台概览数据 — IDS v3.2 §2.1
 * 聚合 6 大 KPI + 低效回路 + 趋势摘要 + 待处理异常
 */
export function getDashboardOverviewApi(
  params?: DashboardApi.OverviewQueryParams,
) {
  return requestClient.get<DashboardApi.OverviewResult>('/dashboard/overview', {
    params,
  });
}

/**
 * 获取实时自控率 — FDS v5.1 §5.3.6, UIUX v5.3 ①
 */
export function getAutoRateRtApi(params?: { plantId?: string }) {
  return requestClient.get<DashboardApi.AutoRateRt>('/dashboard/auto-rate-rt', {
    params,
  });
}

/**
 * 获取节点级聚合 KPI（v6.1 新增）
 * 递归聚合当前节点及所有下属节点的 KPI；
 * v6.1.4：可选 timeWindow——缺省为每节点最新快照，指定后 rate 字段按窗口加权
 */
export function getBoardAggregateApi(params?: {
  plantId?: string;
  timeWindow?: string;
}) {
  return requestClient.get<DashboardApi.BoardAggregateResult>(
    '/dashboard/board/aggregate',
    {
      params,
    },
  );
}

/**
 * 获取节点级聚合趋势数据（v6.1 新增）
 * 递归聚合当前节点及所有下属节点的趋势数据
 */
export function getBoardTrendApi(params?: {
  plantId?: string;
  timeWindow?: string;
}) {
  return requestClient.get<DashboardApi.BoardTrendResult>(
    '/dashboard/board/trend',
    {
      params,
    },
  );
}

/**
 * 获取异常预测与提前预警 — P3-05
 *
 * 基于最近 7 天 KPI 快照趋势（线性回归），预测未来 24 小时可能出问题的回路。
 * 返回高风险回路列表（按风险分降序），仅含 MEDIUM+HIGH 等级。
 * 后端 Redis 缓存 10 分钟。
 */
export function getPredictionsApi(params?: DashboardApi.PredictionQueryParams) {
  return requestClient.get<DashboardApi.PredictionResult>(
    '/dashboard/predictions',
    { params },
  );
}

/**
 * 获取系统概览聚合数据 — 04-系统概览标杆页
 * 一次返回概览页所需的全部统计数据
 */
export function getSystemOverviewApi(params?: {
  plantId?: string;
  timeWindow?: string;
}) {
  return requestClient.get<DashboardApi.SystemOverviewResult>(
    '/dashboard/system-overview',
    { params },
  );
}
