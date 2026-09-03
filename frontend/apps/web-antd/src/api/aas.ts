/**
 * AAS API — 位号元数据同步与连接配置.
 *
 * 对接后端 /api/v1/aas/*（schemas/aas.py）；同步为异步 Celery 任务，
 * 经 GET /aas/config 的 lastSyncStatus 轮询进度（PROCESSING → SUCCESS/FAILED）。
 */
import { requestClient } from '#/api/request';

export namespace AasApi {
  /** AAS 连接配置与最近同步状态（GET /aas/config，仅 ADMIN） */
  export interface AasConfig {
    /** OPC UA 端点 URL */
    endpoint: string;
    /** 同步周期（秒，定时同步已停用，仅展示） */
    syncIntervalSeconds: number;
    /** 是否启用定时同步 */
    enabled: boolean;
    /** 是否为 Mock 模式（无真实 AAS） */
    mockMode: boolean;
    /** 安全模式：None/Sign/SignAndEncrypt */
    securityMode: string;
    /** 最近一次同步完成时间（ISO 8601），未同步过为 null */
    lastSyncAt: null | string;
    /** 最近一次同步状态：PROCESSING/SUCCESS/FAILED，未同步过为 null */
    lastSyncStatus: 'FAILED' | 'PROCESSING' | 'SUCCESS' | null;
  }

  /** 同步触发结果（POST /aas/sync） */
  export interface SyncTriggerResult {
    /** Celery 任务 ID */
    taskId: string;
    /** 触发后立即返回的状态（恒 PROCESSING） */
    status: string;
    /** 任务进度查询地址 */
    checkUrl: null | string;
  }
}

/** 获取 AAS 连接配置与最近同步状态（仅 ADMIN） */
export function getAasConfigApi() {
  return requestClient.get<AasApi.AasConfig>('/aas/config');
}

/** 手动触发 AAS 位号同步（异步任务，经 getAasConfigApi 轮询 lastSyncStatus） */
export function triggerAasSyncApi() {
  return requestClient.post<AasApi.SyncTriggerResult>('/aas/sync');
}
