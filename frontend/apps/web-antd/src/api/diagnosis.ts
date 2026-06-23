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
  /** 处理状态枚举（IDS v3.2 §2.4） */
  export type ActionStatus = 'IGNORED' | 'IN_PROGRESS' | 'PENDING' | 'RESOLVED';

  /** 时间窗枚举 */
  export type TimeWindow =
    | 'last_7_days'
    | 'last_24_hours'
    | 'last_30_days'
    | 'today'
    | 'yesterday';

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

  /** 诊断列表查询参数 */
  export interface DiagnosisListQueryParams {
    plantNodeId?: string;
    diagnosisLabel?: DiagnosisLabel;
    actionStatus?: ActionStatus;
    timeWindow?: TimeWindow;
    page?: number;
    pageSize?: number;
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
    featureValues: Record<string, number>;
    evidenceChain: EvidenceChain;
    algorithmVersion: string;
    diagnosedAt: string;
  }

  /** 波形数据（IDS v3.2 §2.4） */
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
  }

  /** 波形查询参数 */
  export interface WaveformQueryParams {
    startTime: string;
    endTime: string;
    downsample?: boolean;
    maxPoints?: number;
  }

  /** Tracker 状态更新参数（仅 IC_ENGINEER） */
  export interface TrackerStatusUpdateParams {
    status: ActionStatus;
    comment?: string;
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
  }

  /** Tracker 列表查询参数 */
  export interface TrackerListQueryParams {
    plantNodeId?: string;
    diagnosisLabel?: DiagnosisLabel;
    actionStatus?: ActionStatus;
    timeWindow?: TimeWindow;
    page?: number;
    pageSize?: number;
  }

  /** PDF 导出参数 */
  export interface PdfExportParams {
    timeWindow?: string;
    includeWaveform?: boolean;
    includeScatterPlot?: boolean;
  }

  /** PDF 导出响应 */
  export interface PdfExportResult {
    taskId: string;
    status: string;
    checkUrl: string;
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
    before: number;
    after: number;
    unit: string;
  }

  /** A/B 对比 - 趋势数据 */
  export interface AbCompareTrend {
    before: { pv: (null | number)[]; timestamps: number[] };
    after: { pv: (null | number)[]; timestamps: number[] };
  }

  /** A/B 对比响应 */
  export interface AbCompareResult {
    loopId: string;
    tagName: string;
    beforeRange: { endTime: string; startTime: string };
    afterRange: { endTime: string; startTime: string };
    trend: AbCompareTrend;
    kpiComparison: AbCompareKpiItem[];
    improvement: Record<string, number>;
  }

  /** A/B 对比查询参数 */
  export interface AbCompareQueryParams {
    loopId: string;
    beforeStartTime: string;
    beforeEndTime: string;
    afterStartTime: string;
    afterEndTime: string;
  }
}

/**
 * 获取诊断指标配置列表 — IDS v3.2 §2.4
 */
export function getDiagnosisMetricsApi() {
  return requestClient.get<DiagnosisApi.MetricListResult>('/diagnosis/metrics');
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
 */
export function getDiagnosisListApi(
  params: DiagnosisApi.DiagnosisListQueryParams,
) {
  return requestClient.get<PaginatedResponse<DiagnosisApi.DiagnosisListItem>>(
    '/diagnosis/list',
    { params },
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
  return requestClient.get<PaginatedResponse<DiagnosisApi.TrackerItem>>(
    '/diagnosis/list',
    { params },
  );
}

/**
 * 导出诊断 PDF — IDS v3.2 §2.4（异步任务）
 */
export function exportDiagnosisPdfApi(
  loopId: string,
  data: DiagnosisApi.PdfExportParams,
) {
  return requestClient.post<DiagnosisApi.PdfExportResult>(
    `/tracker/${loopId}/export`,
    data,
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
