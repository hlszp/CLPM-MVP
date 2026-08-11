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

  /** 历史辨识支持的模型类型（P2-008：IPDT 差分辨识链已接入历史路径） */
  export type HistoryModelType = ModelType;

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
  export type DataSource = 'fallback_step' | 'HISTORY' | 'STEP_EXPERIMENT';

  /**
   * 算法可信度等级（辨识专用，Phase 2，对齐后端 AlgorithmConfidenceLevel）。
   *
   * 可信度统一 Phase 3（P3-1）：原 ``ConfidenceLevel`` 改名为
   * ``AlgorithmConfidenceLevel``，与平台级数据可信度（A/B/C/D/E，无 INCONCLUSIVE）
   * 区分。判定维度：算法拟合度（R²）+ 残差 + 激励。
   *
   * 对外 API 字段名不变：仍为 ``confidenceLevel`` / ``dataConfidenceLevel``。
   */
  export type AlgorithmConfidenceLevel =
    | 'A'
    | 'B'
    | 'C'
    | 'D'
    | 'E'
    | 'INCONCLUSIVE';

  /** 整定模型来源（服务端按来源执行可信度与审计门禁） */
  export type ModelSource =
    | 'IDENTIFICATION_RECORD'
    | 'MANUAL'
    | 'STEP_EXPERIMENT';

  /** 纯滞后参数来源 */
  export type ThetaSource = 'EXPLICIT' | 'HEURISTIC_2TS' | 'SEARCHED';

  /** 异步任务状态 */
  export type AsyncTaskStatus = 'FAILED' | 'PENDING' | 'RUNNING' | 'SUCCESS';

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
    candidateModelTypes?: HistoryModelType[];
    /** 纯滞后预估值（秒）；null 时使用 2Ts 启发值，可信度最高 C */
    thetaEstimate?: null | number;
  }

  /** 候选模型（多阶次并行辨识） */
  export interface CandidateModel {
    modelType: ModelType;
    params: ModelParams;
    fittingScore: number;
    confidence: AlgorithmConfidenceLevel;
    identifyMethod?: HistoryIdentifyMethod | null;
    residualTestPassed?: boolean | null;
    excitationScore?: null | number;
    reason?: null | string;
  }

  /** 模型辨识结果 */
  export interface IdentifyResult {
    /** 受控阶跃辨识落库后的审计记录 ID */
    recordId?: null | string;
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
    /** 是否通过稳定基线→单阶跃→保持段→显著响应验证 */
    stepValidationPassed?: boolean;
  }

  /** 历史数据辨识结果（Phase 2） */
  export interface IdentifyHistoryResult {
    success: boolean;
    recordId?: null | string;
    modelType?: null | string;
    params?: ModelParams | null;
    fittingScore?: null | number;
    confidenceLevel?: AlgorithmConfidenceLevel | null;
    dataConfidenceLevel?: AlgorithmConfidenceLevel | null;
    confidenceReason?: null | string;
    thetaSource?: null | ThetaSource;
    excitationScore?: null | number;
    residualTestPassed?: boolean | null;
    identifyMethod?: HistoryIdentifyMethod | null;
    dataSource?: DataSource | null;
    candidateModels?: CandidateModel[] | null;
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
    /**
     * 模型来源。兼容窗口内为可选字段，但新调用方必须显式传入：
     * 辨识记录 / 受控阶跃 / 手工模型。
     */
    modelSource?: ModelSource;
    /** IDENTIFICATION_RECORD 来源必填 */
    sourceRecordId?: string;
    /** C 级或 MANUAL 来源仅在人工确认后传 true */
    riskConfirmed?: boolean;
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
    /** 回路 ID（模型记录一致性校验） */
    loopId?: string;
    /** 模型来源，与 TuneRequest 使用同一门禁口径 */
    modelSource?: ModelSource;
    /** IDENTIFICATION_RECORD / STEP_EXPERIMENT 来源必填 */
    sourceRecordId?: string;
    /** C 级或 MANUAL 来源仅在人工确认后传 true */
    riskConfirmed?: boolean;
  }

  /** 多 PID 对比仿真请求（V62-P0-030 独立 schema，不要求 recommendedPid） */
  export interface CompareRequest {
    modelType: ModelType;
    modelParams: ModelParams;
    /** 当前 PID（对比基线，可选） */
    currentPid?: PidParams;
    /** 多组候选 PID（至少 2 组） */
    pidCandidates: PidParamsWithLabel[];
    /** 仿真时长（秒） */
    simDuration?: number;
    /** 仿真步长（秒） */
    simStep?: number;
    /** 设定值阶跃幅值 */
    setpointStep?: number;
    disturbanceType?: DisturbanceType;
    /** 回路 ID（模型记录一致性校验） */
    loopId?: string;
    /** 模型来源，与 TuneRequest 使用同一门禁口径 */
    modelSource?: ModelSource;
    /** IDENTIFICATION_RECORD / STEP_EXPERIMENT 来源必填 */
    sourceRecordId?: string;
    /** C 级或 MANUAL 来源仅在人工确认后传 true */
    riskConfirmed?: boolean;
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
    candidateResponses?: CandidateResponse[] | null;
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
    identifyMethod?: HistoryIdentifyMethod | null;
    dataSource?: DataSource | null;
    confidenceLevel?: AlgorithmConfidenceLevel | null;
    confidenceReason?: null | string;
    excitationScore?: null | number;
    residualTestPassed?: boolean | null;
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
    identifyMethod?: HistoryIdentifyMethod | null;
    dataSource?: DataSource | null;
    confidenceLevel?: AlgorithmConfidenceLevel | null;
    confidenceReason?: null | string;
    excitationScore?: null | number;
    residualTestPassed?: boolean | null;
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
    /** V62-P2-22：风险等级统计（risk_assessment->>'riskLevel' 聚合）。 */
    riskSummary?: {
      /** 是否有任何一条记录已生成风险评估（true 才能把 0 当成真实值）。 */
      calculated: boolean;
      high: number;
      low: number;
      medium: number;
      total: number;
    } | null;
    /** V62-P2-22：待整定数（DRAFT+RUNNING+PENDING+IDENTIFIED，后端统一口径）。 */
    pendingCount?: number;
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
export function comparePidsApi(data: TuningApi.CompareRequest) {
  return requestClient.post<TuningApi.SimulationResult>(
    '/tuning/compare',
    data,
  );
}

// ---------------------------------------------------------------------------
// P3-01: 整定知识库 API
// ---------------------------------------------------------------------------

export namespace KnowledgeBaseApi {
  /** 知识库条目 */
  export interface KnowledgeEntry {
    id: string;
    trackerId: string;
    tuningRecordId: null | string;
    loopId: string;
    loopType: null | string;
    controlType: null | string;
    tagName: string;
    diagnosisLabel: null | string;
    severity: null | string;
    modelType: null | string;
    algorithm: null | string;
    identifyMethod: null | string;
    confidenceLevel: null | string;
    pidBefore: null | Record<string, number>;
    pidAfter: null | Record<string, number>;
    kpiSummary: null | Record<string, unknown>;
    effectVerified: boolean | null;
    improvedCount: null | number;
    deterioratedCount: null | number;
    matchSource: string;
    implementedAt: null | string;
    verifiedAt: null | string;
    createdAt: null | string;
  }

  /** 列表查询参数 */
  export interface ListParams {
    loopType?: string;
    diagnosisLabel?: string;
    algorithm?: string;
    effectVerified?: boolean;
    page?: number;
    pageSize?: number;
  }

  /** 列表全局统计（当前筛选条件下，非当前页）。IA 整改 C-2/T-3 新增，旧后端可能为 null */
  export interface ListStats {
    total: number;
    improvedCount: number;
    deterioratedCount: number;
    unverifiedCount: number;
    avgImprovedMetrics: null | number;
  }

  /** 列表响应 */
  export interface ListData {
    items: KnowledgeEntry[];
    total: number;
    page: number;
    pageSize: number;
    stats?: ListStats | null;
  }

  /** 相似案例查询参数 */
  export interface SimilarParams {
    loopId?: string;
    loopType?: string;
    diagnosisLabel?: string;
    limit?: number;
  }

  /** 相似案例响应 */
  export interface SimilarData {
    items: KnowledgeEntry[];
    total: number;
  }
}

/**
 * 知识库列表 — P3-01
 */
export function getKnowledgeBaseApi(params: KnowledgeBaseApi.ListParams) {
  return requestClient.get<KnowledgeBaseApi.ListData>(
    '/tuning/knowledge-base',
    {
      params,
    },
  );
}

/**
 * 知识库条目详情 — P3-01
 */
export function getKnowledgeEntryApi(entryId: string) {
  return requestClient.get<KnowledgeBaseApi.KnowledgeEntry>(
    `/tuning/knowledge-base/${entryId}`,
  );
}

/**
 * 相似案例推荐 — P3-01
 */
export function getSimilarCasesApi(params: KnowledgeBaseApi.SimilarParams) {
  return requestClient.get<KnowledgeBaseApi.SimilarData>(
    '/tuning/knowledge-base/similar',
    { params },
  );
}
