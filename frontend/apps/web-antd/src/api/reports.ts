/**
 * 统计报告聚合 API（IA 优化 P0，2026-08-22）
 *
 * 后端：backend/app/api/v1/endpoints/reports.py
 * - GET /reports/overview              管理总览（S1 基础指标，S2/S3 字段 null）
 * - GET /reports/diagnosis-statistics  诊断统计（基于 DiagnosisRun）
 * - GET /reports/benefit               收益报告（技术指标，不含经济收益）
 */
import { requestClient } from '#/api/request';

export namespace ReportsApi {
  export type Stage = 'S1' | 'S2' | 'S3';

  export interface OverviewKpi {
    key: string;
    label: string;
    value: null | number | string;
    unit?: null | string;
    status?: null | string;
    context?: null | string;
  }

  export interface OverviewTrendPoint {
    date: string;
    score: null | number;
    loopCount: null | number;
  }

  export interface OverviewTopLoop {
    loopId: string;
    loopTagName: string;
    unitPath?: null | string;
    latestScore: null | number;
    primaryCategory?: null | string;
    primaryCategoryLabel?: null | string;
    severity?: null | string;
  }

  export interface OverviewData {
    stage: Stage;
    kpis: OverviewKpi[];
    healthTrend: OverviewTrendPoint[];
    topProblemLoops: OverviewTopLoop[];
    closedLoopTrend: null | Record<string, unknown>[];
    benefitTrend: null | Record<string, unknown>[];
  }

  export interface CategoryItem {
    category: string;
    label: string;
    count: number;
    ratio: number;
  }

  export interface ConfidenceItem {
    range: string;
    label: string;
    count: number;
    ratio: number;
  }

  export interface DiagnosisTopLoop {
    loopId: string;
    loopTagName: string;
    unitPath?: null | string;
    runCount: number;
    highCount: number;
    latestCategory?: null | string;
    latestCategoryLabel?: null | string;
    latestSeverity?: null | string;
    latestConfidence?: null | number;
  }

  export interface DiagnosisTrendPoint {
    date: string;
    total: number;
    high: number;
  }

  export interface DiagnosisStatisticsData {
    total: number;
    successCount: number;
    reviewPendingCount: number;
    categoryDistribution: CategoryItem[];
    confidenceDistribution: ConfidenceItem[];
    topAbnormalLoops: DiagnosisTopLoop[];
    trend: DiagnosisTrendPoint[];
  }

  export interface BenefitKpiComparison {
    metric: string;
    label: string;
    before: null | number;
    after: null | number;
    delta: null | number;
    unit: null | string;
  }

  export interface BenefitCurvePoint {
    date: string;
    autoRate: null | number;
    score: null | number;
  }

  export interface BenefitBenchmarkItem {
    unitId: null | string;
    unitName: string;
    loopCount: number;
    avgScore: null | number;
    avgAutoRate: null | number;
    avgDelta: null | number;
  }

  export interface BenefitData {
    tuningCount: number;
    closedOrderCount: number;
    kpiComparison: BenefitKpiComparison[];
    autoRateCurve: BenefitCurvePoint[];
    benchmark: BenefitBenchmarkItem[];
  }

  export interface ReportQuery {
    startDate?: string;
    endDate?: string;
    plantNodeId?: string;
  }
}

export function getReportOverviewApi(
  params: ReportsApi.ReportQuery & { stage?: ReportsApi.Stage },
) {
  return requestClient.get<ReportsApi.OverviewData>('/reports/overview', {
    params,
  });
}

export function getReportDiagnosisStatisticsApi(
  params: ReportsApi.ReportQuery,
) {
  return requestClient.get<ReportsApi.DiagnosisStatisticsData>(
    '/reports/diagnosis-statistics',
    { params },
  );
}

export function getReportBenefitApi(params: ReportsApi.ReportQuery) {
  return requestClient.get<ReportsApi.BenefitData>('/reports/benefit', {
    params,
  });
}
