/**
 * CLPM 诊断中心 API（占位模块）
 *
 * 对齐 IDS v3.2 接口契约，仅定义类型与函数签名，具体实现待后续补充。
 */
import type { PageQuery, PaginatedResponse } from '#/api/types';

import { requestClient } from '#/api/request';

export namespace DiagnosisApi {
  /** 诊断类型 */
  export type DiagnosisType =
    | 'noise'
    | 'oscillation'
    | 'other'
    | 'saturation'
    | 'stiction'
    | 'tuning';

  /** 诊断严重等级 */
  export type Severity = 'critical' | 'info' | 'warning';

  /** 诊断结果项 */
  export interface DiagnosisItem {
    id: string;
    /** 关联回路 ID */
    loopId: string;
    /** 诊断类型 */
    type: DiagnosisType;
    /** 严重等级 */
    severity: Severity;
    /** 诊断标题 */
    title: string;
    /** 诊断描述 */
    description: string;
    /** 建议措施 */
    recommendation?: string;
    /** 诊断时间 */
    diagnosedAt: string;
    /** 是否已确认 */
    acknowledged: boolean;
  }

  /** 诊断查询参数 */
  export interface DiagnosisQueryParams extends PageQuery {
    loopId?: string;
    type?: DiagnosisType;
    severity?: Severity;
    acknowledged?: boolean;
    startDate?: string;
    endDate?: string;
  }

  /** 诊断任务创建参数 */
  export interface CreateDiagnosisTaskParams {
    loopId: string;
    type?: DiagnosisType;
    startDate: string;
    endDate: string;
  }

  /** 诊断任务状态 */
  export type DiagnosisTaskStatus =
    | 'completed'
    | 'failed'
    | 'pending'
    | 'running';

  /** 诊断任务 */
  export interface DiagnosisTask {
    id: string;
    loopId: string;
    status: DiagnosisTaskStatus;
    createdAt: string;
    completedAt?: string;
    result?: DiagnosisItem[];
  }
}

/**
 * 获取诊断结果列表（分页）
 */
export function getDiagnosisListApi(params: DiagnosisApi.DiagnosisQueryParams) {
  return requestClient.get<PaginatedResponse<DiagnosisApi.DiagnosisItem>>(
    '/diagnoses',
    { params },
  );
}

/**
 * 获取诊断结果详情
 */
export function getDiagnosisDetailApi(id: string) {
  return requestClient.get<DiagnosisApi.DiagnosisItem>(`/diagnoses/${id}`);
}

/**
 * 创建诊断任务
 */
export function createDiagnosisTaskApi(
  data: DiagnosisApi.CreateDiagnosisTaskParams,
) {
  return requestClient.post<DiagnosisApi.DiagnosisTask>(
    '/diagnoses/tasks',
    data,
  );
}

/**
 * 获取诊断任务状态
 */
export function getDiagnosisTaskApi(taskId: string) {
  return requestClient.get<DiagnosisApi.DiagnosisTask>(
    `/diagnoses/tasks/${taskId}`,
  );
}

/**
 * 确认诊断结果
 */
export function acknowledgeDiagnosisApi(id: string) {
  return requestClient.post(`/diagnoses/${id}/acknowledge`);
}
