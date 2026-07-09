/**
 * CLPM AAS（Asset Administration Shell）API（对齐 IDS v3.2 §2.2.5 ~ §2.2.6 + 配置接口）
 *
 * 覆盖 AAS Tag 同步、连接配置管理。
 */
import type { PageQuery } from '#/api/types';

import { requestClient } from '#/api/request';

export namespace AasApi {
  /** 质量码 */
  export type Quality = 'BAD' | 'GOOD' | 'UNCERTAIN' | null;

  /** 同步状态 */
  export type SyncStatus = 'FAILED' | 'PROCESSING' | 'SUCCESS';

  /** AAS Tag 项（IDS v3.2 §2.2.5） */
  export interface AasTag {
    tagId: string;
    tagName: string;
    description: string;
    currentValue: number;
    quality: Quality;
    lastSyncAt: string;
    associatedLoopId: null | string;
    associatedLoopTagName: null | string;
  }

  /** AAS Tag 列表查询参数（IDS v3.2 §2.2.5） */
  export interface AasTagQueryParams extends PageQuery {
    keyword?: string;
    quality?: 'BAD' | 'GOOD' | 'UNCERTAIN';
    associated?: boolean;
  }

  /** AAS Tag 列表响应（IDS v3.2 §2.2.5） */
  export interface AasTagListResult {
    items: AasTag[];
    total: number;
    page: number;
    pageSize: number;
    lastSyncAt: string;
    syncStatus: SyncStatus;
  }

  /** AAS 同步任务（IDS v3.2 §2.2.6） */
  export interface AasSyncTask {
    taskId: string;
    status: SyncStatus;
    checkUrl: string;
  }

  /** AAS 连接配置 */
  export interface AasConfig {
    endpoint: string;
    /** 同步周期（秒），对齐后端 syncIntervalSeconds */
    syncIntervalSeconds: number;
    enabled: boolean;
    lastSyncAt: null | string;
    lastSyncStatus: null | SyncStatus;
  }

  /** 更新 AAS 配置参数 */
  export interface UpdateAasConfigParams {
    endpoint: string;
    /** 同步周期（秒） */
    syncIntervalSeconds: number;
    enabled: boolean;
  }

  /** 测试连接结果 */
  export interface AasConfigTestResult {
    success: boolean;
    /** 延迟（毫秒），对齐后端 latencyMs */
    latencyMs: null | number;
    message: string;
  }

  /** 同步状态结果 */
  export interface SyncStatusResult {
    enabled: boolean;
    endpoint?: null | string;
    syncIntervalSeconds?: null | number;
    lastSyncAt?: null | string;
    lastSyncStatus?: null | string;
    tagStats: {
      byQuality: Record<string, number>;
      linked: number;
      total: number;
    };
  }

  /** 同步日志项 */
  export interface SyncLog {
    id: string;
    operationType: string;
    operator: string;
    operatedAt: string;
    beforeValue?: null | string;
    afterValue?: null | string;
  }

  /** 同步日志列表结果 */
  export interface SyncLogListResult {
    items: SyncLog[];
    total: number;
  }
}

/**
 * 获取 AAS 同步的 Tag 列表 — IDS v3.2 §2.2.5
 */
export function getAasTagsApi(params: AasApi.AasTagQueryParams) {
  return requestClient.get<AasApi.AasTagListResult>('/aas/tags', { params });
}

/**
 * 触发 AAS Tag 手动同步 — IDS v3.2 §2.2.6
 */
export function triggerAasSyncApi() {
  return requestClient.post<AasApi.AasSyncTask>('/aas/sync');
}

/**
 * 获取 AAS 连接配置
 */
export function getAasConfigApi() {
  return requestClient.get<AasApi.AasConfig>('/aas/config');
}

/**
 * 更新 AAS 连接配置
 */
export function updateAasConfigApi(data: AasApi.UpdateAasConfigParams) {
  return requestClient.put<AasApi.AasConfig>('/aas/config', data);
}

/**
 * 测试 AAS 连接
 */
export function testAasConfigApi() {
  return requestClient.post<AasApi.AasConfigTestResult>('/aas/config/test');
}

/**
 * 获取 AAS 同步状态 — FDS v5.1 §5.3.5
 */
export function getSyncStatusApi() {
  return requestClient.get<AasApi.SyncStatusResult>('/aas/sync-status');
}

/**
 * 获取 AAS 同步日志 — FDS v5.1 §5.3.5
 */
export function getSyncLogsApi(params?: { page?: number; pageSize?: number }) {
  return requestClient.get<AasApi.SyncLogListResult>('/aas/sync-logs', {
    params,
  });
}
