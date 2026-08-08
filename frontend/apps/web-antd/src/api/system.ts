/**
 * CLPM 系统管理 API（对齐 IDS v3.2 §2.6）
 *
 * 覆盖用户管理、审计日志、自动报表三类子能力。
 * - 用户 API 前缀：/api/v1/users
 * - 审计日志 API 前缀：/api/v1/audit-logs
 * - 报表 API 前缀：/api/v1/reports
 *
 * 注意：类型仅在 SystemApi 命名空间内导出，避免与 index.ts 的 `export *` 冲突。
 */
import type { ClpmRole } from '#/api/auth';

import { requestClient } from '#/api/request';

export namespace SystemApi {
  /** 用户信息（对齐后端 UserItem，camelCase） */
  export interface User {
    id: string;
    username: string;
    displayName: string;
    role: ClpmRole;
    email?: null | string;
    isActive: boolean;
    createdAt?: null | string;
    lastLoginAt?: null | string;
    updatedAt?: null | string;
  }

  /** 用户分页查询参数（对齐后端 keyword/role/isActive/page/pageSize） */
  export interface UserListQueryParams {
    page?: number;
    pageSize?: number;
    keyword?: string;
    role?: ClpmRole;
    isActive?: boolean;
  }

  /** 用户分页响应 */
  export interface UserListResult {
    items: User[];
    total: number;
    page: number;
    pageSize: number;
  }

  /** 创建用户参数（对齐 UserCreateRequest；后端无 phone 字段） */
  export interface CreateUserParams {
    username: string;
    password: string;
    displayName: string;
    role: ClpmRole;
    email?: string;
  }

  /** 更新用户参数 */
  export interface UpdateUserParams {
    displayName?: string;
    role?: ClpmRole;
    email?: string;
    isActive?: boolean;
  }

  /** 重置密码参数 */
  export interface ResetPasswordParams {
    newPassword: string;
  }

  /** 操作类型枚举 */
  export type OperationType =
    | 'CREATE'
    | 'DELETE'
    | 'LOGIN'
    | 'LOGOUT'
    | 'UPDATE';

  /** 资源类型枚举 */
  export type ResourceType =
    | 'DIAGNOSIS'
    | 'LOOP'
    | 'METRIC'
    | 'REPORT'
    | 'USER';

  /** 审计日志（对齐后端 AuditLogItem，camelCase；targetType 为后端原始字符串如 "User"） */
  export interface AuditLog {
    logId: string;
    operator: string;
    operationType: OperationType;
    targetType?: null | string;
    targetId?: null | string;
    beforeValue?: unknown;
    afterValue?: unknown;
    clientIp?: null | string;
    operatedAt?: null | string;
  }

  /** 审计日志分页查询参数（对齐后端 operator/operationType/startTime/endTime） */
  export interface AuditLogListQueryParams {
    page?: number;
    pageSize?: number;
    operator?: string;
    operationType?: OperationType;
    startTime?: string;
    endTime?: string;
  }

  /** 审计日志分页响应 */
  export interface AuditLogListResult {
    items: AuditLog[];
    total: number;
  }

  /** 报表配置（对齐 S5-SYS-003 后端 ReportConfigItem） */
  export interface ReportConfig {
    id: string;
    name: string;
    reportPeriod: string;
    recipients: string[];
    contentTemplate?: null | Record<string, unknown>;
    isEnabled: boolean;
    createdBy?: string;
    updatedBy?: string;
    createdAt?: string;
    updatedAt?: string;
  }

  /** 创建报表配置参数 */
  export interface CreateReportConfigParams {
    name: string;
    reportPeriod: string;
    recipients: string[];
    contentTemplate?: null | Record<string, unknown>;
    isEnabled: boolean;
  }

  /** 更新报表配置参数 */
  export interface UpdateReportConfigParams {
    name?: string;
    reportPeriod?: string;
    recipients?: string[];
    contentTemplate?: null | Record<string, unknown>;
    isEnabled?: boolean;
  }

  /** 报表生成触发响应 */
  export interface ReportGenerateResult {
    taskId: string;
    taskType?: string;
    status?: string;
    checkUrl?: string;
    estimatedSeconds?: number;
  }

  /** 报表任务状态 */
  export type ReportTaskStatus = 'COMPLETED' | 'FAILED' | 'PROCESSING';

  /** 报表任务状态查询响应 */
  export interface ReportTaskResult {
    taskId: string;
    status: ReportTaskStatus;
    progress: number;
    message?: null | string;
    downloadUrl?: null | string;
  }
}

/**
 * 分页查询用户列表 — IDS v3.2 §2.6
 */
export function getUserListApi(params: SystemApi.UserListQueryParams) {
  return requestClient.get<SystemApi.UserListResult>('/users', { params });
}

/**
 * 创建用户 — IDS v3.2 §2.6
 */
export function createUserApi(data: SystemApi.CreateUserParams) {
  return requestClient.post<SystemApi.User>('/users', data);
}

/**
 * 更新用户 — IDS v3.2 §2.6
 */
export function updateUserApi(id: string, data: SystemApi.UpdateUserParams) {
  return requestClient.put<SystemApi.User>(`/users/${id}`, data);
}

/**
 * 禁用用户（软删除） — IDS v3.2 §2.6
 */
export function deleteUserApi(id: string) {
  return requestClient.delete(`/users/${id}`);
}

/**
 * 重置用户密码 — IDS v3.2 §2.6
 */
export function resetUserPasswordApi(
  id: string,
  data: SystemApi.ResetPasswordParams,
) {
  return requestClient.put(`/users/${id}/reset-password`, data);
}

/**
 * 分页查询审计日志 — IDS v3.2 §2.6
 */
export function getAuditLogListApi(params: SystemApi.AuditLogListQueryParams) {
  return requestClient.get<SystemApi.AuditLogListResult>('/audit-logs', {
    params,
  });
}

/**
 * 查询报表配置列表 — IDS v3.2 §2.6
 */
export function getReportConfigListApi() {
  return requestClient.get<SystemApi.ReportConfig[]>('/reports/configs');
}

/**
 * 创建报表配置 — IDS v3.2 §2.6
 */
export function createReportConfigApi(
  data: SystemApi.CreateReportConfigParams,
) {
  return requestClient.post<SystemApi.ReportConfig>('/reports/configs', data);
}

/**
 * 更新报表配置 — IDS v3.2 §2.6
 */
export function updateReportConfigApi(
  id: string,
  data: SystemApi.UpdateReportConfigParams,
) {
  return requestClient.put<SystemApi.ReportConfig>(
    `/reports/configs/${id}`,
    data,
  );
}

/**
 * 手动触发报表生成 — IDS v3.2 §2.6（异步任务）
 */
export function generateReportApi(configId: string) {
  return requestClient.post<SystemApi.ReportGenerateResult>(
    '/reports/generate',
    { configId },
  );
}

/**
 * 查询报表任务状态 — IDS v3.2 §2.6（轮询用）
 */
export function getReportTaskStatusApi(taskId: string) {
  return requestClient.get<SystemApi.ReportTaskResult>(
    `/reports/tasks/${taskId}`,
  );
}
