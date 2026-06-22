/**
 * CLPM AAS（Asset Administration Shell）API（对齐 IDS v3.2 §2.2.5 ~ §2.2.6 + 配置接口）
 *
 * 覆盖 AAS Tag 同步、连接配置管理。
 */
import type { PageQuery } from '#/api/types';

import { requestClient } from '#/api/request';

export namespace AasApi {
  /** 质量码 */
  export type Quality = 'Bad' | 'Good' | 'Uncertain' | null;

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
    quality?: 'Bad' | 'Good' | 'Uncertain';
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
    syncInterval: number;
    enabled: boolean;
    lastSyncAt: null | string;
    lastSyncStatus: null | SyncStatus;
  }

  /** 更新 AAS 配置参数 */
  export interface UpdateAasConfigParams {
    endpoint: string;
    syncInterval: number;
    enabled: boolean;
  }

  /** 测试连接结果 */
  export interface AasConfigTestResult {
    success: boolean;
    latency: number;
    message: string;
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
