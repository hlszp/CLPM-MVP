/**
 * CLPM 回路整定 API（对齐 IDS v3.2 §2.5）
 *
 * 覆盖模型辨识、PID 整定、闭环仿真、整定任务管理、效果统计五类子能力。
 * - 整定 API 前缀：/api/v1/tuning/
 *
 * 注意：类型仅在 TuningApi 命名空间内导出，避免与其他模块 `export *` 冲突。
 */
import type { PaginatedResponse } from '#/api/types';

import { requestClient } from '#/api/request';

export namespace TuningApi {
  /** 模型类型 */
  export type ModelType = 'FOPDT' | 'IPDT' | 'SOPDT';

  /** FOPDT 辨识方法 */
  export type IdentifyMethod = 'AREA' | 'COMBINED' | 'TWO_POINT';

  /** 整定算法 */
  export type Algorithm = 'COHEN_COON' | 'IMC' | 'LAMBDA' | 'SIMC' | 'ZN';

  /** 任务状态（Phase 2 对齐实现契约 + 兼容旧枚举） */
  export type TaskStatus =
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

  /** 扰动类型 */
  export type DisturbanceType = 'none' | 'step';

  // ---- Phase 2 新增枚举 ----

  /** 辨识策略（Phase 2） */
  export type IdentifyStrategy = 'AUTO' | 'HISTORY_ONLY' | 'STEP_ONLY';

  /** 历史辨识方法（Phase 2） */
  export type HistoryIdentifyMethod =
    | 'HISTORICAL_ARMAX'
    | 'HISTORICAL_ARX'
    | 'HISTORICAL_IV'
    | 'STEP_AREA'
    | 'STEP_NLS'
    | 'STEP_TWO_POINT';

  /** 数据来源（Phase 2） */
  export type DataSource = 'HISTORY' | 'STEP_EXPERIMENT';

  /** 可信度等级（Phase 2，对齐平台口径） */
  export type ConfidenceLevel =
    | 'A'
    | 'B'
    | 'C'
    | 'D'
    | 'E'
    | 'INCONCLUSIVE';

  /** 异步任务状态 */
  export type AsyncTaskStatus =
    | 'FAILED'
    | 'PENDING'
    | 'RUNNING'
    | 'SUCCESS';

  /** 模型参数 */
  export interface ModelParams {
    /** 过程增益 */
    K?: null | number;
    /** 时间常数（秒，FOPDT） */
    tau?: null | number;
    /** 死区时间（秒） */
    theta?: null | number;
    /** SOPDT 第一时间常数（秒） */
    T1?: null | number;
    /** SOPDT 第二时间常数（秒） */
    T2?: null | number;
  }

  /** PID 参数 */
  export interface PidParams {
    /** 比例增益 */
    kp: number;
    /** 积分时间（秒） */
    ti: number;
    /** 微分时间（秒） */
    td: number;
  }

  /** 带标签的 PID 参数（Phase 2 多 PID 对比） */
  export interface PidParamsWithLabel extends PidParams {
    /** PID 标签（如 IMC λ=1.0） */
    label: string;
  }

  /** 模型辨识请求 */
  export interface IdentifyRequest {
    loopId: string;
    startTime: string;
    endTime: string;
    modelType: ModelType;
    method?: IdentifyMethod;
  }

  /** 历史数据辨识请求（Phase 2） */
  export interface IdentifyHistoryRequest {
    loopId: string;
    startTime: string;
    endTime: string;
    identifyStrategy?: IdentifyStrategy;
    candidateModelTypes?: ModelType[];
    /** 纯滞后预估值（秒），null 自动估计 */
    thetaEstimate?: null | number;
  }

  /** 候选模型（多阶次并行辨识） */
  export interface CandidateModel {
    modelType: ModelType;
    params: ModelParams;
    fittingScore: number;
    confidence: ConfidenceLevel;
    identifyMethod?: null | HistoryIdentifyMethod;
    residualTestPassed?: null | boolean;
    excitationScore?: null | number;
    reason?: null | string;
  }

  /** 模型辨识结果 */
  export interface IdentifyResult {
    modelType: ModelType;
    params: ModelParams;
    /** 拟合度 R²（%） */
    fittingScore: number;
    algorithmVersion: string;
    dataPoints: number;
    /** 拟合曲线 {timestamps: [], pv: [], fitted: []} */
    fittedCurve?: {
      fitted: number[];
      pv: number[];
      timestamps: number[];
    };
  }

  /** 历史数据辨识结果（Phase 2） */
  export interface IdentifyHistoryResult {
    success: boolean;
    modelType?: null | string;
    params?: null | ModelParams;
    fittingScore?: null | number;
    confidenceLevel?: null | ConfidenceLevel;
    dataConfidenceLevel?: null | ConfidenceLevel;
    confidenceReason?: null | string;
    excitationScore?: null | number;
    residualTestPassed?: null | boolean;
    identifyMethod?: null | HistoryIdentifyMethod;
    candidateModels?: null | CandidateModel[];
    algorithmVersion?: null | string;
    dataPoints?: null | number;
    validRate?: null | number;
    samplingFreq?: null | number;
    reason?: null | string;
    tagName?: null | string;
  }

  /** 异步任务提交响应 */
  export interface AsyncTaskResponse {
    taskId: string;
    status: AsyncTaskStatus;
  }

  /** 异步任务进度 */
  export interface TaskProgress {
    taskId: string;
    status: AsyncTaskStatus;
    /** 进度 0~100 */
    progress: number;
    /** 当前阶段 */
    stage?: null | string;
    message?: null | string;
    result?: null | Record<string, any>;
    error?: null | string;
  }

  /** 可辨识片段 */
  export interface IdentifySegment {
    startIdx: number;
    endIdx: number;
    mode?: null | string;
    excitationScore?: null | number;
    conditionNumber?: null | number;
    isSufficient: boolean;
  }

  /** 可辨识片段预览请求 */
  export interface IdentifySegmentsRequest {
    loopId: string;
    startTime: string;
    endTime: string;
  }

  /** 可辨识片段预览结果 */
  export interface IdentifySegmentsResult {
    loopId: string;
    totalSegments: number;
    segments: IdentifySegment[];
    sufficientCount: number;
  }

  /** PID 整定请求 */
  export interface TuneRequest {
    modelType: ModelType;
    modelParams: ModelParams;
    algorithm: Algorithm;
    algorithmParams?: Record<string, number>;
    currentPid?: PidParams;
    loopId?: string;
  }

  /** PID 整定结果 */
  export interface TuneResult {
    algorithm: Algorithm;
    recommendedPid: PidParams;
    currentPid?: PidParams;
    algorithmParams?: Record<string, number>;
    algorithmVersion: string;
    notes?: string;
  }

  /** 闭环仿真请求（Phase 2 扩展 pidCandidates） */
  export interface SimulateRequest {
    modelType: ModelType;
    modelParams: ModelParams;
    currentPid: PidParams;
    recommendedPid: PidParams;
    /** 多组候选 PID（Phase 2，向后兼容） */
    pidCandidates?: null | PidParamsWithLabel[];
    /** 仿真时长（秒） */
    simDuration?: number;
    /** 仿真步长（秒） */
    simStep?: number;
    /** 设定值阶跃幅值 */
    setpointStep?: number;
    disturbanceType?: DisturbanceType;
  }

  /** 仿真性能指标 */
  export interface SimulationMetrics {
    /** 上升时间（秒） */
    riseTime?: null | number;
    /** 超调量（%） */
    overshoot?: null | number;
    /** 稳定时间（秒） */
    settlingTime?: null | number;
    /** ITAE 积分 */
    itae?: null | number;
  }

  /** 单组 PID 响应序列 */
  export interface PidResponse {
    pv: number[];
    op: number[];
    sp: number[];
  }

  /** 候选 PID 响应（多 PID 对比） */
  export interface CandidateResponse {
    label: string;
    response: PidResponse;
    metrics: SimulationMetrics;
  }

  /** 闭环仿真结果（Phase 2 扩展 candidateResponses） */
  export interface SimulationResult {
    timestamps: number[];
    currentResponse: PidResponse;
    recommendedResponse: PidResponse;
    currentMetrics: SimulationMetrics;
    recommendedMetrics: SimulationMetrics;
    /** 改善幅度 */
    improvement: Record<string, null | number>;
    /** 多 PID 候选响应（Phase 2） */
    candidateResponses?: null | CandidateResponse[];
  }

  /** 整定任务列表项（Phase 2 扩展元数据） */
  export interface TuningTaskItem {
    id: string;
    loopId: string;
    tagName?: null | string;
    modelType: ModelType;
    modelParams?: ModelParams | null;
    algorithm: Algorithm;
    recommendedPid?: null | PidParams;
    fittingScore?: null | number;
    status: TaskStatus;
    createdBy?: null | string;
    createdAt: string;
    // Phase 2 元数据
    identifyMethod?: null | HistoryIdentifyMethod;
    dataSource?: null | DataSource;
    confidenceLevel?: null | ConfidenceLevel;
    confidenceReason?: null | string;
    excitationScore?: null | number;
    residualTestPassed?: null | boolean;
    taskId?: null | string;
    completedAt?: null | string;
  }

  /** 整定任务详情 */
  export interface TuningTaskDetail extends TuningTaskItem {
    simulationResult?: null | SimulationResult;
    currentPid?: null | PidParams;
    pidCandidates?: null | Record<string, any>;
    candidateResults?: null | Record<string, any>;
  }

  /** 创建整定任务请求（Phase 2 扩展元数据） */
  export interface CreateTaskRequest {
    loopId: string;
    modelType: ModelType;
    modelParams: ModelParams;
    algorithm: Algorithm;
    recommendedPid: PidParams;
    currentPid?: PidParams;
    fittingScore?: null | number;
    simulationResult?: null | SimulationResult;
    status?: TaskStatus;
    // Phase 2 元数据
    identifyMethod?: null | HistoryIdentifyMethod;
    dataSource?: null | DataSource;
    confidenceLevel?: null | ConfidenceLevel;
    confidenceReason?: null | string;
    excitationScore?: null | number;
    residualTestPassed?: null | boolean;
    pidCandidates?: null | Record<string, any>;
    candidateResults?: null | Record<string, any>;
  }

  /** 整定任务查询参数 */
  export interface TaskQueryParams {
    loopId?: string;
    algorithm?: Algorithm;
    status?: TaskStatus;
    page?: number;
    pageSize?: number;
  }

  /** 整定历史统计 */
  export interface HistoryStats {
    totalTasks: number;
    byAlgorithm: Record<string, number>;
    byStatus: Record<string, number>;
    avgFittingScore?: null | number;
    recentTasks: TuningTaskItem[];
  }

  /** 整定方法参数定义 */
  export interface MethodParam {
    name: string;
    label: string;
    default: number | string;
    min?: number;
    max?: number;
    options?: string[];
  }

  /** 整定方法信息 */
  export interface MethodInfo {
    code: Algorithm;
    name: string;
    description: string;
    applicableModel: ModelType;
    params: MethodParam[];
  }
}

/**
 * 获取整定方法信息 — IDS v3.2 §2.5
 */
export function getTuningMethodsApi() {
  return requestClient.get<TuningApi.MethodInfo[]>('/tuning/methods');
}

/**
 * 模型辨识 — IDS v3.2 §2.5（ADMIN/IC_ENGINEER/EXPERT）
 */
export function identifyModelApi(data: TuningApi.IdentifyRequest) {
  return requestClient.post<TuningApi.IdentifyResult>('/tuning/identify', data);
}

/**
 * PID 整定 — IDS v3.2 §2.5（ADMIN/IC_ENGINEER/EXPERT）
 */
export function tunePidApi(data: TuningApi.TuneRequest) {
  return requestClient.post<TuningApi.TuneResult>('/tuning/tune', data);
}

/**
 * 闭环仿真 — IDS v3.2 §2.5（ADMIN/IC_ENGINEER/EXPERT）
 */
export function simulateTuningApi(data: TuningApi.SimulateRequest) {
  return requestClient.post<TuningApi.SimulationResult>(
    '/tuning/simulate',
    data,
  );
}

/**
 * 获取整定任务列表 — IDS v3.2 §2.5
 */
export function getTuningTasksApi(params: TuningApi.TaskQueryParams) {
  return requestClient.get<PaginatedResponse<TuningApi.TuningTaskItem>>(
    '/tuning/tasks',
    { params },
  );
}

/**
 * 获取整定任务详情 — IDS v3.2 §2.5
 */
export function getTuningTaskDetailApi(taskId: string) {
  return requestClient.get<TuningApi.TuningTaskDetail>(
    `/tuning/tasks/${taskId}`,
  );
}

/**
 * 保存整定任务 — IDS v3.2 §2.5（ADMIN/IC_ENGINEER/EXPERT）
 */
export function createTuningTaskApi(data: TuningApi.CreateTaskRequest) {
  return requestClient.post<TuningApi.TuningTaskItem>('/tuning/tasks', data);
}

/**
 * 获取整定历史统计 — IDS v3.2 §2.5
 */
export function getTuningHistoryApi() {
  return requestClient.get<TuningApi.HistoryStats>('/tuning/history');
}

// ---------------------------------------------------------------------------
// Phase 2 新增 API
// ---------------------------------------------------------------------------

/**
 * 历史数据辨识（异步）— Phase 2
 *
 * 提交 Celery 异步任务，返回 taskId 供前端轮询进度。
 */
export function identifyHistoryApi(data: TuningApi.IdentifyHistoryRequest) {
  return requestClient.post<TuningApi.AsyncTaskResponse>(
    '/tuning/identify/history',
    data,
  );
}

/**
 * 可辨识片段预览 — Phase 2
 *
 * 对数据窗口执行激励检测，返回可辨识片段列表（不执行辨识）。
 */
export function previewSegmentsApi(data: TuningApi.IdentifySegmentsRequest) {
  return requestClient.post<TuningApi.IdentifySegmentsResult>(
    '/tuning/identify/segments',
    data,
  );
}

/**
 * 异步任务进度查询 — Phase 2
 *
 * task_id 为 Celery 任务 ID（字符串）。
 */
export function getTaskStatusApi(taskId: string) {
  return requestClient.get<TuningApi.TaskProgress>(
    `/tuning/tasks/${taskId}/status`,
  );
}

/**
 * 取消异步整定任务 — Phase 2
 *
 * 注意：命名为 cancelTuningTaskApi 以避免与 #/api/task 的 cancelTaskApi 冲突。
 */
export function cancelTuningTaskApi(taskId: string) {
  return requestClient.post<Record<string, string>>(
    `/tuning/tasks/${taskId}/cancel`,
  );
}

/**
 * 多 PID 对比仿真 — Phase 2
 *
 * 至少 2 组候选 PID，返回每组响应曲线与性能指标。
 */
export function comparePidsApi(data: TuningApi.SimulateRequest) {
  return requestClient.post<TuningApi.SimulationResult>(
    '/tuning/compare',
    data,
  );
}
