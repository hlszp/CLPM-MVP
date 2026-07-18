/**
 * CLPM 回路数据管理 API — 历史数据导入（Phase 3）
 *
 * 对齐 data-architecture-optimization-spec §5.3.1
 * 路由前缀: /api/v1/loops/data-import
 */
import { requestClient } from '#/api/request';

export namespace LoopDataApi {
  /** 导入任务状态 */
  export type ImportStatus =
    | 'CANCELLED'
    | 'FAILED'
    | 'PENDING'
    | 'RUNNING'
    | 'SUCCESS';

  /** 冲突处理策略 */
  export type ConflictStrategy = 'overwrite' | 'skip';

  /** 历史数据导入请求 */
  export interface ImportRequest {
    /** 目标回路 ID 列表 */
    loopIds: string[];
    /** 导入时间范围起始（ISO 8601） */
    tsStart: string;
    /** 导入时间范围结束（ISO 8601） */
    tsEnd: string;
    /** 采样间隔（秒），默认 1 */
    interval?: number;
    /** 冲突策略，overwrite 或 skip */
    conflictStrategy?: ConflictStrategy;
    /** 导入完成后是否触发 KPI 回算 */
    triggerBackfill?: boolean;
  }

  /** 导入任务响应 */
  export interface ImportTask {
    taskId: string;
    status: ImportStatus;
    progress: number;
    loopCount: number;
    importedCount: number;
    errorCount: number;
    tsStart: string;
    tsEnd: string;
    createdAt: string;
    startedAt?: string;
    finishedAt?: string;
    errorMessage?: string;
    createdBy?: string;
    conflictStrategy: string;
    triggerBackfill: boolean;
  }

  /** 导入任务列表响应 */
  export interface ImportTaskList {
    items: ImportTask[];
    total: number;
  }
}

/** 开始历史数据导入 */
export function startImportApi(data: LoopDataApi.ImportRequest) {
  return requestClient.post<{ celeryTaskId: string; taskId: string }>(
    '/loops/data-import/start',
    data,
  );
}

/** 查询导入任务状态 */
export function getImportStatusApi(taskId: string) {
  return requestClient.get<LoopDataApi.ImportTask>(
    `/loops/data-import/${taskId}/status`,
  );
}

/** 取消导入任务 */
export function cancelImportApi(taskId: string) {
  return requestClient.post<Record<string, unknown>>(
    `/loops/data-import/${taskId}/cancel`,
  );
}

/** 删除导入任务 */
export function deleteImportApi(taskId: string) {
  return requestClient.delete<{ taskId: string }>(
    `/loops/data-import/${taskId}`,
  );
}

/** 查询导入任务列表 */
export function getImportTasksApi(params: { page: number; pageSize: number }) {
  return requestClient.get<LoopDataApi.ImportTaskList>(
    '/loops/data-import/tasks',
    { params },
  );
}

/** 触发 KPI 回算 */
export function triggerBackfillApi(taskId: string) {
  return requestClient.post<{ celeryTaskId: string; loopCount: number }>(
    `/loops/data-import/${taskId}/backfill-kpi`,
  );
}
