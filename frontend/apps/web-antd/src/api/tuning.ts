/**
 * CLPM 回路整定 API（占位模块）
 *
 * 对齐 IDS v3.2 接口契约，仅定义类型与函数签名，具体实现待后续补充。
 */
import type { PageQuery, PaginatedResponse } from '#/api/types';

import { requestClient } from '#/api/request';

export namespace TuningApi {
  /** 整定方法 */
  export type TuningMethod =
    | 'cohen-coon'
    | 'imc'
    | 'lambda'
    | 'manual'
    | 'tyreus-luyben'
    | 'ziegler-nichols';

  /** 整定任务状态 */
  export type TuningTaskStatus = 'completed' | 'failed' | 'pending' | 'running';

  /** PID 参数 */
  export interface PidParams {
    /** 比例增益 */
    kp: number;
    /** 积分时间（秒） */
    ti: number;
    /** 微分时间（秒） */
    td: number;
  }

  /** 整定任务 */
  export interface TuningTask {
    id: string;
    /** 关联回路 ID */
    loopId: string;
    /** 整定方法 */
    method: TuningMethod;
    /** 当前 PID 参数 */
    currentParams: PidParams;
    /** 建议参数（整定完成后填充） */
    suggestedParams?: PidParams;
    /** 状态 */
    status: TuningTaskStatus;
    /** 整定开始时间 */
    startedAt?: string;
    /** 整定完成时间 */
    completedAt?: string;
    /** 备注 */
    remark?: string;
    /** 创建时间 */
    createdAt: string;
  }

  /** 创建整定任务参数 */
  export interface CreateTuningTaskParams {
    loopId: string;
    method: TuningMethod;
    /** 模型参数（如过程模型） */
    modelParams?: {
      deadTime?: number;
      gain?: number;
      timeConstant?: number;
    };
    /** 目标 lambda 值（仅 lambda 方法） */
    lambda?: number;
  }

  /** 整定任务查询参数 */
  export interface TuningQueryParams extends PageQuery {
    loopId?: string;
    method?: TuningMethod;
    status?: TuningTaskStatus;
  }

  /** 整定仿真结果 */
  export interface TuningSimulation {
    taskId: string;
    /** 仿真时间点 */
    timestamps: string[];
    /** 设定值序列 */
    sp: number[];
    /** 过程变量序列 */
    pv: number[];
    /** 操作变量序列 */
    op: number[];
  }
}

/**
 * 获取整定任务列表（分页）
 */
export function getTuningListApi(params: TuningApi.TuningQueryParams) {
  return requestClient.get<PaginatedResponse<TuningApi.TuningTask>>(
    '/tuning/tasks',
    { params },
  );
}

/**
 * 获取整定任务详情
 */
export function getTuningDetailApi(id: string) {
  return requestClient.get<TuningApi.TuningTask>(`/tuning/tasks/${id}`);
}

/**
 * 创建整定任务
 */
export function createTuningTaskApi(data: TuningApi.CreateTuningTaskParams) {
  return requestClient.post<TuningApi.TuningTask>('/tuning/tasks', data);
}

/**
 * 获取整定仿真结果
 */
export function getTuningSimulationApi(taskId: string) {
  return requestClient.get<TuningApi.TuningSimulation>(
    `/tuning/tasks/${taskId}/simulation`,
  );
}

/**
 * 应用整定建议参数
 */
export function applyTuningParamsApi(taskId: string) {
  return requestClient.post<TuningApi.TuningTask>(
    `/tuning/tasks/${taskId}/apply`,
  );
}
