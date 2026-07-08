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
    nodeName: string | null;
    snapshotTime: string | null;
    avgScore: number | null;
    autoModeRate: number | null;
    stabilityRate: number | null;
    effectiveAutoRate: number | null;
    accuracyRate: number | null;
    fastRate: number | null;
    goodValueRate: number | null;
    oscillationRate: number | null;
    saturationRate: number | null;
    totalLoops: number;
    evaluatedLoops: number;
    inconclusiveLoops: number;
    excludedLoops: number;
    status: string;
    algorithmVersion: string | null;
  }

  /** 装置级 KPI 看板结果 */
  export interface BoardResult {
    items: BoardItem[];
    total: number;
  }

  /** 实时自控率结果 */
  export interface AutoRateRt {
    rate: number | null;
    autoCount: number;
    manualCount: number;
    totalCount: number;
    readAt: string | null;
    message?: string;
  }

  /** 节点级聚合 KPI 结果（v6.1 新增） */
  export interface BoardAggregateResult {
    items: BoardItem[];
    total: number;
    aggregate: {
      nodeId: string | null;
      nodeName: string | null;
      avgScore: number | null;
      autoModeRate: number | null;
      stabilityRate: number | null;
      effectiveAutoRate: number | null;
      accuracyRate: number | null;
      fastRate: number | null;
      goodValueRate: number | null;
      totalLoops: number;
      evaluatedLoops: number;
      inconclusiveLoops: number;
      excludedLoops: number;
    };
  }

  /** 节点级聚合趋势结果（v6.1 新增） */
  export interface BoardTrendResult {
    timestamps: string[];
    avgScore: (number | null)[];
    autoModeRate: (number | null)[];
    stabilityRate: (number | null)[];
    evaluatedLoops: number[];
    totalLoops: number;
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
 * 递归聚合当前节点及所有下属节点的 KPI
 */
export function getBoardAggregateApi(params?: { plantId?: string }) {
  return requestClient.get<DashboardApi.BoardAggregateResult>('/dashboard/board/aggregate', {
    params,
  });
}

/**
 * 获取节点级聚合趋势数据（v6.1 新增）
 * 递归聚合当前节点及所有下属节点的趋势数据
 */
export function getBoardTrendApi(params?: { plantId?: string; timeWindow?: string }) {
  return requestClient.get<DashboardApi.BoardTrendResult>('/dashboard/board/trend', {
    params,
  });
}
