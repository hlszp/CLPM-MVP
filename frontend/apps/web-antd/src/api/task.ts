/**
 * CLPM 任务管理 API（对齐 IDS v3.2 §2.7.6 — Phase 5）
 *
 * 覆盖标准评估任务和自定义评估任务的全生命周期管理。
 * 任务状态存储在 Redis 中（key: task:{task_id}）。
 *
 * 任务状态机（PRD §4.3.7.C）:
 *   PENDING → RUNNING → SUCCESS
 *                      → FAILED
 *                      → CANCELLED
 *
 * 接口前缀：/api/v1/tasks
 *
 * 注意：TaskType/TaskStatus 等类型定义在 TaskApi 命名空间内，
 * 避免与 tuning.ts 的顶层 TaskStatus 冲突。
 */
import { requestClient } from '#/api/request';

export namespace TaskApi {
  /** 任务类型（对齐 app.schemas.task.TaskType） */
  export type TaskType = 'CUSTOM' | 'STANDARD';

  /** 任务状态（对齐 app.schemas.task.TaskStatus） */
  export type TaskStatus =
    | 'CANCELLED'
    | 'FAILED'
    | 'PENDING'
    | 'RUNNING'
    | 'SUCCESS';

  /** 终态状态集合 */
  export const TERMINAL_STATUSES: TaskStatus[] = [
    'CANCELLED',
    'FAILED',
    'SUCCESS',
  ];

  /** 活跃状态集合 */
  export const ACTIVE_STATUSES: TaskStatus[] = ['PENDING', 'RUNNING'];

  /** 标准评估任务创建参数 */
  export interface StandardTaskCreateParams {
    /** 评估时间窗起始（ISO 8601），不传则使用当前小时 */
    tsStart?: string;
  }

  /** 自定义评估任务创建参数 */
  export interface CustomTaskCreateParams {
    /** 目标回路 ID 列表（至少 1 个） */
    loopIds: string[];
    /** 目标指标子集（如 accuracy_rate / fast_response_rate 等） */
    metrics: string[];
    /** 评估时间窗起始（ISO 8601） */
    tsStart: string;
    /** 评估时间窗结束（ISO 8601） */
    tsEnd: string;
  }

  /** 任务响应（对齐 app.schemas.task.TaskResponse） */
  export interface TaskItem {
    taskId: string;
    taskType: TaskType;
    status: TaskStatus;
    /** 进度 0~1 */
    progress?: null | number;
    /** 当前阶段：取数/预处理/指标计算/可信度判定 */
    currentStage?: null | string;
    loopsTotal?: null | number;
    loopsDone?: null | number;
    createdAt: string;
    startedAt?: null | string;
    finishedAt?: null | string;
    errorMessage?: null | string;
    createdBy: string;
  }

  /** 任务列表响应 */
  export interface TaskListResult {
    items: TaskItem[];
    total: number;
  }

  /** 任务列表查询参数 */
  export interface TaskListQueryParams {
    taskType?: TaskType;
    status?: TaskStatus;
    page?: number;
    pageSize?: number;
  }

  /** 任务通知项 */
  export interface TaskNotification {
    taskId: string;
    taskType: TaskType;
    status: TaskStatus;
    progress?: null | number;
    currentStage?: null | string;
    message: string;
    createdAt: string;
  }

  /** 通知列表响应 */
  export interface NotificationListResult {
    items: TaskNotification[];
    total: number;
  }

  /** 标记通知已读响应 */
  export interface MarkReadResult {
    taskId: string;
    read: boolean;
  }

  /** 取消任务响应 */
  export interface CancelTaskResult {
    taskId: string;
    cancelled: boolean;
  }

  /** 非标任务结果项 */
  export interface TaskResultItem {
    loopId: string;
    loopTagName: string;
    tsStart: string | null;
    tsEnd: string | null;
    score: number | null;
    accuracyRate: number | null;
    fastRate: number | null;
    steadyRate: number | null;
    effectiveAutoRate: number | null;
    goodValueRate: number | null;
    oscillationRate: number | null;
    saturationRate: number | null;
    autoModeRate: number | null;
    stictionIndex: number | null;
    outputTripIndex: number | null;
    settlingTime: number | null;
    idealSettlingTime: number | null;
    status: string;
    confidenceLevel: string | null;
    validRate: number | null;
    algorithmVersion: string | null;
    samplingFreq: string | null;
    qualityPolicy: string | null;
    dataLineage: Record<string, unknown> | null;
    createdAt: string | null;
  }

  /** 非标任务结果列表结果 */
  export interface TaskResultListResult {
    items: TaskResultItem[];
    total: number;
    taskStatus: string;
  }
}

const BASE = '/tasks';

/**
 * 触发标准评估任务 — IDS §2.7.6（IC_ENGINEER/PE_ENGINEER/ADMIN）
 *
 * 标准任务每小时由系统调度器自动触发，也可由用户手动触发。
 * 评估全量回路，结果写入 kpi_snapshot_hourly 参与装置级聚合。
 */
export function triggerStandardEvaluateApi(
  data: TaskApi.StandardTaskCreateParams = {},
) {
  return requestClient.post<TaskApi.TaskItem>(
    `${BASE}/standard/evaluate`,
    data,
  );
}

/**
 * 触发自定义评估任务 — IDS §2.7.6（IC_ENGINEER/PE_ENGINEER/ADMIN）
 *
 * 用户按需选定回路/指标/时间范围触发评估。
 * 结果写入 kpi_snapshot_custom，不参与装置级聚合。
 */
export function triggerCustomEvaluateApi(data: TaskApi.CustomTaskCreateParams) {
  return requestClient.post<TaskApi.TaskItem>(`${BASE}/custom/evaluate`, data);
}

/**
 * 查询任务详情 — IDS §2.7.6
 */
export function getTaskDetailApi(taskId: string) {
  return requestClient.get<TaskApi.TaskItem>(`${BASE}/${taskId}`);
}

/**
 * 查询任务列表 — IDS §2.7.6
 */
export function getTaskListApi(params: TaskApi.TaskListQueryParams) {
  return requestClient.get<TaskApi.TaskListResult>(BASE, { params });
}

/**
 * 取消任务 — IDS §2.7.6（IC_ENGINEER/PE_ENGINEER/ADMIN）
 *
 * 仅 PENDING/RUNNING 状态的任务可取消。
 */
export function cancelTaskApi(taskId: string) {
  return requestClient.post<TaskApi.CancelTaskResult>(
    `${BASE}/${taskId}/cancel`,
  );
}

/**
 * 查询当前用户任务通知 — Phase 5 补齐
 *
 * 返回终态任务完成通知列表（成功/失败/取消）。
 * 系统定时任务（created_by_id 为空）不发通知。
 */
export function getTaskNotificationsApi(limit = 20) {
  return requestClient.get<TaskApi.NotificationListResult>(
    `${BASE}/notifications`,
    { params: { limit } },
  );
}

/**
 * 标记通知为已读 — Phase 5 补齐
 */
export function markNotificationReadApi(taskId: string) {
  return requestClient.post<TaskApi.MarkReadResult>(
    `${BASE}/notifications/${taskId}/read`,
  );
}

/**
 * 获取非标任务结果 — FDS v5.1 §5.3.8
 */
export function getTaskResultsApi(
  taskId: string,
  params?: { page?: number; pageSize?: number },
) {
  return requestClient.get<TaskApi.TaskResultListResult>(
    `${BASE}/${taskId}/results`,
    { params },
  );
}
