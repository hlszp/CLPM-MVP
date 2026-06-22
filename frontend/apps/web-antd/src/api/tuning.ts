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
  export type Algorithm =
    | 'COHEN_COON'
    | 'IMC'
    | 'LAMBDA'
    | 'SIMC'
    | 'ZN';

  /** 任务状态 */
  export type TaskStatus = 'APPLIED' | 'FAILED' | 'SIMULATED';

  /** 扰动类型 */
  export type DisturbanceType = 'none' | 'step';

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

  /** 模型辨识请求 */
  export interface IdentifyRequest {
    loopId: string;
    startTime: string;
    endTime: string;
    modelType: ModelType;
    method?: IdentifyMethod;
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
      timestamps: number[];
      pv: number[];
      fitted: number[];
    };
  }

  /** PID 整定请求 */
  export interface TuneRequest {
    modelType: ModelType;
    modelParams: ModelParams;
    algorithm: Algorithm;
    algorithmParams?: Record<string, any>;
    currentPid?: PidParams;
    loopId?: string;
  }

  /** PID 整定结果 */
  export interface TuneResult {
    algorithm: Algorithm;
    recommendedPid: PidParams;
    currentPid?: PidParams;
    algorithmParams?: Record<string, any>;
    algorithmVersion: string;
    notes?: string;
  }

  /** 闭环仿真请求 */
  export interface SimulateRequest {
    modelType: ModelType;
    modelParams: ModelParams;
    currentPid: PidParams;
    recommendedPid: PidParams;
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

  /** 闭环仿真结果 */
  export interface SimulationResult {
    timestamps: number[];
    currentResponse: PidResponse;
    recommendedResponse: PidResponse;
    currentMetrics: SimulationMetrics;
    recommendedMetrics: SimulationMetrics;
    /** 改善幅度 */
    improvement: Record<string, null | number>;
  }

  /** 整定任务列表项 */
  export interface TuningTaskItem {
    id: string;
    loopId: string;
    tagName?: null | string;
    modelType: ModelType;
    modelParams?: Record<string, any> | null;
    algorithm: Algorithm;
    recommendedPid?: Record<string, any> | null;
    fittingScore?: null | number;
    status: TaskStatus;
    createdBy?: null | string;
    createdAt: string;
  }

  /** 整定任务详情 */
  export interface TuningTaskDetail extends TuningTaskItem {
    simulationResult?: Record<string, any> | null;
    currentPid?: Record<string, any> | null;
  }

  /** 创建整定任务请求 */
  export interface CreateTaskRequest {
    loopId: string;
    modelType: ModelType;
    modelParams: ModelParams;
    algorithm: Algorithm;
    recommendedPid: PidParams;
    currentPid?: PidParams;
    fittingScore?: null | number;
    simulationResult?: Record<string, any> | null;
    status?: TaskStatus;
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
    default: any;
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
