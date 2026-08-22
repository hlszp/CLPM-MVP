/**
 * 统计报告聚合 API（IA 优化 P0+P3，2026-08-22）
 *
 * 后端：backend/app/api/v1/endpoints/reports.py
 * - GET /reports/overview              管理总览（P3 S1/S2/S3 自适应）
 * - GET /reports/diagnosis-statistics  诊断统计（基于 DiagnosisRun）
 * - GET /reports/benefit               收益报告（技术指标，不含经济收益）
 * - GET/PUT /reports/stage-lock        读取 / 设置阶段锁定（ADMIN 写入）
 * - POST /reports/export-pdf           触发 PDF 导出（异步）
 * - GET  /reports/export-tasks/{id}    查询 PDF 导出任务状态
 * - GET  /reports/export-download/{id} 下载 PDF
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

  export interface ClosedLoopTrendPoint {
    month: string;
    total: number;
    closed: number;
    closedRate: null | number;
  }

  export interface AnomalyDistributionChangeItem {
    category: string;
    label: string;
    currentCount: number;
    previousCount: number;
    currentRatio: number;
    previousRatio: number;
    deltaCount: number;
  }

  export interface BenefitTrendPoint {
    date: string;
    autoRate: null | number;
    score: null | number;
  }

  export interface OverviewTopLoop {
    loopId: string;
    loopTagName: string;
    unitPath?: null | string;
    latestScore: null | number;
    primaryCategory?: null | string;
    primaryCategoryLabel?: null | string;
    severity?: null | string;
    handlingStatus?: null | string; // S2 追加列
    benefitEstimate?: null | number; // S3 追加列（评分改善，预留经济收益位）
  }

  export interface Availability {
    s1Available: boolean;
    s2Available: boolean;
    s3Available: boolean;
  }

  export interface MaturityCounts {
    diagnosisRuns: number;
    handlingOrders: number;
    tuningRecords: number;
    closedVerifiedOrders: number;
  }

  export interface OverviewData {
    stage: Stage;
    stageOrigin: 'AUTO' | 'LOCK';
    isLocked: boolean;
    availability: Availability;
    maturityCounts: MaturityCounts;
    kpis: OverviewKpi[];
    healthTrend: OverviewTrendPoint[];
    closedLoopTrend: ClosedLoopTrendPoint[] | null;
    anomalyDistributionChange: AnomalyDistributionChangeItem[] | null;
    benefitTrend: BenefitTrendPoint[] | null;
    topProblemLoops: OverviewTopLoop[];
  }

  export interface StageLockState {
    locked: boolean;
    lockedStage: null | Stage;
    detectedStage: Stage;
    availability: Availability;
    counts: MaturityCounts;
  }

  export interface PdfExportTask {
    taskId: string;
    taskType: string;
    status: 'COMPLETED' | 'FAILED' | 'PROCESSING';
    fileUrl: null | string;
    fileName: null | string;
    fileSize: null | number;
    error: null | string;
    estimatedSeconds: number;
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

// ---------- P3：阶段锁定 ----------

export function getReportStageLockApi(params?: { plantNodeId?: string }) {
  return requestClient.get<ReportsApi.StageLockState>('/reports/stage-lock', {
    params,
  });
}

export function setReportStageLockApi(
  body: { stage: null | ReportsApi.Stage },
  params?: { plantNodeId?: string },
) {
  return requestClient.put<ReportsApi.StageLockState>(
    '/reports/stage-lock',
    body,
    { params },
  );
}

// ---------- P3：PDF 异步导出 ----------

export function triggerReportPdfExportApi(body: {
  endDate?: string;
  plantNodeId?: string;
  stage?: ReportsApi.Stage;
  startDate?: string;
}) {
  return requestClient.post<ReportsApi.PdfExportTask>(
    '/reports/export-pdf',
    body,
  );
}

export function getReportPdfExportTaskApi(taskId: string) {
  return requestClient.get<ReportsApi.PdfExportTask>(
    `/reports/export-tasks/${taskId}`,
  );
}

export function downloadReportPdfUrl(taskId: string) {
  const base = requestClient.getBaseUrl?.() ?? '/api/v1';
  return `${base}/reports/export-download/${taskId}`;
}
