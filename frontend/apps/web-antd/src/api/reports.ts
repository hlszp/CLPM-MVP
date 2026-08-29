/**
 * 统计报告聚合 API（IA 优化 P0+P3，2026-08-22）
 *
 * 后端：backend/app/api/v1/endpoints/reports.py
 * - GET /reports/overview              管理总览（P3 S1/S2/S3 自适应）
 * - GET /reports/diagnosis-statistics  诊断统计（基于 DiagnosisRun）
 * - GET /reports/benefit               收益报告（技术指标，不含经济收益；P2-3 增整定执行区块）
 * - GET /reports/benefit/orders        逐工单前后对比明细（R1 自持，P2-4）
 * - GET /reports/handling-statistics   处置报告统计（R1 自持，P0-2；P2-1 增 SLA/漏斗/工作量）
 * - GET /reports/diagnosis-runs        诊断报告明细（R1 自持，P0-4）
 * - GET /reports/diagnosis-runs/export 诊断明细 CSV 导出（≤5000 行，D4）
 * - GET/PUT /reports/stage-lock        读取 / 设置阶段锁定（ADMIN 写入）
 * - POST /reports/export-pdf           触发 PDF 导出（异步）
 * - GET  /reports/export-tasks/{id}    查询 PDF 导出任务状态
 * - GET  /reports/export-download/{id} 下载 PDF
 */
import type { DiagnosisApi } from '#/api/diagnosis';
import type { HandlingApi } from '#/api/handling';
import type { PaginatedResponse } from '#/api/types';

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
    /** P2-3 整定执行区块（方案 §5.2，向后兼容可选字段） */
    tuningExecution?: BenefitTuningExecution | null;
    fittingDistribution?: BenefitFittingBucket[];
    latestBatchScatter?: BenefitBatchScatter | null;
  }

  // ---------- P2-3 整定执行区块（报告模块优化 P2，方案 §5.2） ----------

  export interface BenefitTuningAlgoItem {
    algorithm: string;
    count: number;
  }

  export interface BenefitTuningStatusItem {
    status: string;
    count: number;
  }

  export interface BenefitTuningExecution {
    totalRecords: number;
    byAlgorithm: BenefitTuningAlgoItem[];
    byStatus: BenefitTuningStatusItem[];
    /** 回滚率（ROLLED_BACK / 窗口内全部整定记录，无记录为 null） */
    rollbackRate: null | number;
    /** 平均拟合度（0~100，无拟合度记录为 null） */
    avgFittingScore: null | number;
  }

  export interface BenefitFittingBucket {
    bucket: string;
    label: string;
    count: number;
  }

  export interface BenefitScatterPoint {
    loopId: string;
    score: null | number;
    loopTagName?: null | string;
  }

  export interface BenefitBatchScatter {
    batchNo: string;
    title: string;
    completedAt: null | string;
    before: BenefitScatterPoint[];
    after: BenefitScatterPoint[];
  }

  // ---------- P2-4 逐工单前后对比明细（方案 §5.3） ----------

  export interface BenefitOrderItem {
    orderNo: string;
    loopId: string;
    loopTagName: string;
    actionType: string;
    actionTypeLabel: string;
    handler: null | string;
    /** 四指标快照：score/effectiveAutoRate/goodValueRate/oscillationRate */
    kpiBefore: Record<string, any>;
    kpiAfter: Record<string, any>;
    verifyResult: null | string;
    verifiedAt: null | string;
  }

  export interface ReportQuery {
    startDate?: string;
    endDate?: string;
    plantNodeId?: string;
  }

  // ---------- 基座补域（报告模块优化 P1，2026-08-28） ----------

  export interface DataQualityTrendPoint {
    date: string;
    healthRate: null | number;
    inconclusiveRate: null | number;
  }

  export interface ConfidenceDistItem {
    level: string;
    count: number;
  }

  export interface DataQualityItem {
    loopId: string;
    loopTagName: string;
    loopDescription: null | string;
    unitPath: string;
    includeInEvaluation: boolean;
    pvCompleteness: null | number;
    overallCompleteness: null | number;
    integrityStatus: null | string;
    checkedAt: null | string;
    goodValueRate: null | number;
    confidenceLevel: null | string;
    evalStatus: null | string;
    evalTime: null | string;
    fitnessLevel: null | string;
    nonEvalReason: null | string;
  }

  export interface DataQualityData {
    summary: {
      confidenceDistribution: ConfidenceDistItem[];
      dataHealthRate: null | number;
      evaluableLoops: number;
      evaluateRate: null | number;
      inconclusiveRate: null | number;
      totalLoops: number;
    };
    trend: DataQualityTrendPoint[];
    items: DataQualityItem[];
  }

  export interface AlertTrendPoint {
    date: string;
    CRITICAL: number;
    ERROR: number;
    INFO: number;
    WARN: number;
  }

  export interface AlertDistItem {
    key: string;
    count: number;
  }

  export interface AlertTopRule {
    ruleCode: string;
    ruleName: null | string;
    count: number;
    falsePositives: number;
  }

  export interface AlertTopLoop {
    loopId: string;
    loopTagName: string;
    count: number;
    falsePositives: number;
  }

  export interface AlertStatisticsData {
    summary: {
      active: number;
      activeSuppressions: number;
      falsePositiveRate: null | number;
      mttaHours: null | number;
      mttrHours: null | number;
      total: number;
    };
    trend: AlertTrendPoint[];
    statusDistribution: AlertDistItem[];
    severityDistribution: AlertDistItem[];
    topRules: AlertTopRule[];
    topLoops: AlertTopLoop[];
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

// ---------- R1 自持端点（报告模块优化 P0-3/P0-4，2026-08-28） ----------

/** 处置报告统计（直读 handling_order/loop_action_item，模块禁用不受影响） */
export function getReportHandlingStatisticsApi(
  params: ReportsApi.ReportQuery & { months?: number },
) {
  return requestClient.get<HandlingApi.StatisticsData>(
    '/reports/handling-statistics',
    { params },
  );
}

/** 诊断报告明细（直读 diagnosis_run，支持 plantNodeId 透传，修复 P-07） */
export function getReportDiagnosisRunsApi(
  params: ReportsApi.ReportQuery & {
    category?: string;
    page?: number;
    pageSize?: number;
    severity?: string;
  },
) {
  return requestClient.get<PaginatedResponse<DiagnosisApi.RunListItem>>(
    '/reports/diagnosis-runs',
    { params },
  );
}

/** 诊断报告明细 CSV 导出（≤5000 行，D4 上限） */
export function exportReportDiagnosisRunsApi(
  params: ReportsApi.ReportQuery & { category?: string; severity?: string },
) {
  return requestClient.get<string>('/reports/diagnosis-runs/export', {
    params,
    responseType: 'blob',
  });
}

// ---------- 基座补域（报告模块优化 P1，2026-08-28） ----------

/** 数据质量报告聚合（基础模块数据，模块禁用不受影响） */
export function getReportDataQualityApi(params: ReportsApi.ReportQuery) {
  return requestClient.get<ReportsApi.DataQualityData>('/reports/data-quality', {
    params,
  });
}

/** 预警统计报告聚合（基础模块数据，模块禁用不受影响） */
export function getReportAlertStatisticsApi(
  params: ReportsApi.ReportQuery & { severity?: string; status?: string },
) {
  return requestClient.get<ReportsApi.AlertStatisticsData>(
    '/reports/alert-statistics',
    { params },
  );
}

// ---------- 闭环增强（报告模块优化 P2-4，方案 §5.3） ----------

/** 逐工单前后对比明细（直读 handling_order，模块禁用不受影响） */
export function getReportBenefitOrdersApi(
  params: ReportsApi.ReportQuery & { page?: number; pageSize?: number },
) {
  return requestClient.get<PaginatedResponse<ReportsApi.BenefitOrderItem>>(
    '/reports/benefit/orders',
    { params },
  );
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
