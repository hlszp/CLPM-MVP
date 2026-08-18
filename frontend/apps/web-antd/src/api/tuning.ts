/**
 * 整定模块 API（09 设计方案恢复为一级模块，2026-08-19）
 *
 * 设计文档：docs/MVP设计/09-整定模块设计方案.md §5.2/§5.3
 * 后端：backend/app/api/v1/endpoints/tuning.py（/api/v1/tuning/*）
 */

import { requestClient } from '#/api/request';

export namespace TuningApi {
  /** 模型类型 */
  export type ModelType = 'FOPDT' | 'IPDT' | 'SOPDT';
  /** 整定算法（矩阵 5 算法） */
  export type TuningAlgorithm = 'COHEN_COON' | 'IMC' | 'LAMBDA' | 'SIMC' | 'ZN';
  /** 可信度等级 */
  export type ConfidenceLevel = 'A' | 'B' | 'C' | 'D' | 'E' | 'INCONCLUSIVE';
  /** 整定任务状态（MVP 实际使用 SIMULATED→APPLIED→VERIFIED 路径） */
  export type TuningTaskStatus =
    | 'APPLIED'
    | 'COMPLETED'
    | 'DRAFT'
    | 'IDENTIFIED'
    | 'INCONCLUSIVE'
    | 'PENDING'
    | 'ROLLED_BACK'
    | 'RUNNING'
    | 'SIMULATED'
    | 'VERIFIED';

  /** 模型参数（FOPDT: K/tau/theta；SOPDT 标准形: K/T1/T2/theta） */
  export interface ModelParams {
    K?: null | number;
    T1?: null | number;
    T2?: null | number;
    tau?: null | number;
    theta?: null | number;
  }

  export interface PidParams {
    kp: number;
    ti: number;
    td: number;
  }

  export interface PidParamsWithLabel extends PidParams {
    label: string;
  }

  /** 阶跃实验辨识结果 */
  export interface ModelIdentifyResult {
    modelType: ModelType;
    params: ModelParams;
    fittingScore: number;
    algorithmVersion: string;
    dataPoints: number;
    recordId?: null | string;
    fittedCurve?: { [key: string]: any[] } | null;
  }

  /** 历史数据辨识异步响应 */
  export interface IdentifyHistoryAsyncResponse {
    taskId: string;
    [key: string]: any;
  }

  /** 异步任务进度 */
  export interface TaskProgress {
    taskId: string;
    status: 'FAILED' | 'PENDING' | 'RUNNING' | 'SUCCESS';
    progress: number;
    stage?: null | string;
    message?: null | string;
    result?: {
      confidenceLevel?: ConfidenceLevel;
      fittingScore?: number;
      modelType?: ModelType;
      params?: ModelParams;
      reason?: string;
      [key: string]: any;
    } | null;
    error?: null | string;
  }

  /** 整定方法信息 */
  export interface TuningMethodInfo {
    code: string;
    name: string;
    description: string;
    applicableModel: string;
    params: Record<string, any>[];
  }

  /** 单算法整定结果 */
  export interface TuneResult {
    algorithm: string;
    recommendedPid: PidParams;
    currentPid?: null | PidParams;
    algorithmParams?: Record<string, any> | null;
    algorithmVersion: string;
    notes?: null | string;
    risk?: {
      riskLevel: string;
      factors: string[];
      description?: null | string;
    } | null;
    rollbackPid?: null | PidParams;
  }

  /** 全算法矩阵行（09 §4.2：单行失败不阻断） */
  export interface TuneMatrixRow {
    algorithm: TuningAlgorithm;
    ok: boolean;
    result?: TuneResult;
    error?: string;
  }

  /** 仿真性能指标 */
  export interface SimulationMetrics {
    riseTime?: null | number;
    overshoot?: null | number;
    settlingTime?: null | number;
    itae?: null | number;
  }

  /** 候选 PID 响应（多 PID 对比） */
  export interface CandidateResponse {
    label: string;
    response: { op: number[]; pv: number[]; sp: number[] };
    metrics: SimulationMetrics;
  }

  /** 闭环仿真结果 */
  export interface SimulationResult {
    timestamps: number[];
    currentResponse: { op: number[]; pv: number[]; sp: number[] };
    recommendedResponse: { op: number[]; pv: number[]; sp: number[] };
    currentMetrics: SimulationMetrics;
    recommendedMetrics: SimulationMetrics;
    improvement: Record<string, null | number>;
    candidateResponses?: CandidateResponse[] | null;
  }

  /** 整定任务列表项 */
  export interface TuningTaskItem {
    id: string;
    loopId: string;
    tagName?: null | string;
    modelType: ModelType;
    modelParams?: Record<string, any> | null;
    algorithm: string;
    recommendedPid?: PidParams | null;
    fittingScore?: null | number;
    status: TuningTaskStatus;
    createdBy?: null | string;
    createdAt: string;
    confidenceLevel?: ConfidenceLevel | null;
    confidenceReason?: null | string;
    identifyMethod?: null | string;
    dataSource?: null | string;
    excitationScore?: null | number;
    residualTestPassed?: boolean | null;
    taskId?: null | string;
    completedAt?: null | string;
  }

  export interface TuningTaskDetail extends TuningTaskItem {
    simulationResult?: Record<string, any> | null;
    currentPid?: PidParams | null;
  }

  export interface TuningTaskListData {
    items: TuningTaskItem[];
    total: number;
    page: number;
    pageSize: number;
  }

  export interface TuningHistoryStats {
    totalTasks: number;
    byAlgorithm: Record<string, number>;
    byStatus: Record<string, number>;
    avgFittingScore?: null | number;
    recentTasks: TuningTaskItem[];
  }

  /** KPI 快照摘要（与处置模块同口径） */
  export interface KpiSummary {
    score: null | number;
    goodValueRate: null | number;
    effectiveAutoRate: null | number;
    steadyRate: null | number;
    accuracyRate: null | number;
    fastRate: null | number;
    oscillationRate: null | number;
    saturationRate: null | number;
    confidenceLevel: null | string;
    tsStart: null | string;
    tsEnd: null | string;
  }

  /** 波形序列（get_waveform 契约） */
  export interface WaveformData {
    loopId: string;
    tagName?: string;
    timestamps: string[];
    pv: (null | number)[];
    sp: (null | number)[];
    op: (null | number)[];
    mode?: (null | number)[];
    pvQuality?: string[];
    downsampled?: boolean;
    pointCount?: number;
    sampleInterval?: number;
  }

  /** 效果验证前后窗数据（09 §4.5） */
  export interface VerificationData {
    loopId: string;
    pointTime: string;
    windowHours: number;
    before: WaveformData;
    after: WaveformData;
    kpiBefore: KpiSummary | null;
    kpiAfter: KpiSummary | null;
    /** 后窗超出当前时刻（数据截至当前时刻） */
    afterTruncated: boolean;
  }
}

/** 整定方法信息 */
export function getTuningMethodsApi() {
  return requestClient.get<TuningApi.TuningMethodInfo[]>('/tuning/methods');
}

/** 阶跃实验辨识（同步） */
export function identifyStepApi(data: {
  loopId: string;
  startTime: string;
  endTime: string;
  modelType?: TuningApi.ModelType;
  method?: string;
}) {
  return requestClient.post<TuningApi.ModelIdentifyResult>(
    '/tuning/identify',
    data,
  );
}

/** 历史数据辨识（异步，返回 taskId 轮询） */
export function identifyHistoryApi(data: {
  loopId: string;
  startTime: string;
  endTime: string;
  candidateModelTypes?: TuningApi.ModelType[];
  thetaEstimate?: null | number;
}) {
  return requestClient.post<TuningApi.IdentifyHistoryAsyncResponse>(
    '/tuning/identify/history',
    data,
  );
}

/** 异步任务进度查询（taskId 为 Celery 任务 ID） */
export function getTuningTaskStatusApi(taskId: string) {
  return requestClient.get<TuningApi.TaskProgress>(
    `/tuning/tasks/${taskId}/status`,
  );
}

/** 取消异步任务 */
export function cancelTuningTaskApi(taskId: string) {
  return requestClient.post<{ cancelled: boolean }>(
    `/tuning/tasks/${taskId}/cancel`,
  );
}

/** 单算法整定（矩阵行内参数微调重算） */
export function tuneSingleApi(data: {
  modelType: TuningApi.ModelType;
  modelParams: TuningApi.ModelParams;
  algorithm: TuningApi.TuningAlgorithm;
  algorithmParams?: Record<string, any> | null;
  currentPid?: null | TuningApi.PidParams;
  loopId?: null | string;
  sourceRecordId?: null | string;
  modelSource?: string;
  riskConfirmed?: boolean;
}) {
  return requestClient.post<TuningApi.TuneResult>('/tuning/tune', data);
}

/** 全算法矩阵（5 算法一次全算，09 §4.2） */
export function tuneMatrixApi(data: {
  modelType: TuningApi.ModelType;
  modelParams: TuningApi.ModelParams;
  algorithmParams?: Record<string, any> | null;
  currentPid?: null | TuningApi.PidParams;
  loopId?: null | string;
  sourceRecordId?: null | string;
  modelSource?: string;
  riskConfirmed?: boolean;
}) {
  return requestClient.post<{ rows: TuningApi.TuneMatrixRow[] }>(
    '/tuning/tune/matrix',
    data,
  );
}

/** 多 PID 对比仿真（pidCandidates ≥2 组） */
export function comparePidsApi(data: {
  modelType: TuningApi.ModelType;
  modelParams: TuningApi.ModelParams;
  pidCandidates: TuningApi.PidParamsWithLabel[];
  currentPid?: null | TuningApi.PidParams;
  simDuration?: number;
  simStep?: number;
  setpointStep?: number;
  loopId?: null | string;
  sourceRecordId?: null | string;
  modelSource?: string;
  riskConfirmed?: boolean;
}) {
  return requestClient.post<TuningApi.SimulationResult>(
    '/tuning/compare',
    data,
  );
}

/** 保存整定方案（09 §4.4：显式保存才落记录） */
export function saveTuningTaskApi(data: {
  loopId: string;
  modelType: TuningApi.ModelType;
  modelParams: TuningApi.ModelParams;
  algorithm: string;
  recommendedPid: TuningApi.PidParams;
  currentPid?: null | TuningApi.PidParams;
  fittingScore?: null | number;
  simulationResult?: Record<string, any> | null;
  status?: TuningApi.TuningTaskStatus;
  [key: string]: any;
}) {
  return requestClient.post<{ id: string }>('/tuning/tasks', data);
}

/** 整定任务列表（分页 + 筛选） */
export function getTuningTasksApi(params: {
  loopId?: string;
  algorithm?: string;
  status?: string;
  page?: number;
  pageSize?: number;
}) {
  return requestClient.get<TuningApi.TuningTaskListData>('/tuning/tasks', {
    params,
  });
}

/** 整定任务详情 */
export function getTuningTaskDetailApi(taskId: string) {
  return requestClient.get<TuningApi.TuningTaskDetail>(
    `/tuning/tasks/${taskId}`,
  );
}

/** 整定历史统计 */
export function getTuningHistoryApi() {
  return requestClient.get<TuningApi.TuningHistoryStats>('/tuning/history');
}

/** 效果验证前后窗曲线数据（09 §4.5，实时拉取不落库） */
export function getVerificationDataApi(params: {
  loopId: string;
  pointTime: string;
  windowHours: number;
}) {
  return requestClient.get<TuningApi.VerificationData>(
    '/tuning/verification/data',
    { params },
  );
}
