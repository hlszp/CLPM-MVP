/**
 * CLPM 回路管理 API（对齐 IDS v3.2 §2.2）
 *
 * 覆盖回路台账 CRUD、Tag 关联管理、回路监控三类子能力。
 */
import type { PageQuery, PaginatedResponse } from '#/api/types';

import { requestClient } from '#/api/request';

export namespace LoopApi {
  /** 回路状态（IDS v3.2 §2.2.7） */
  export type LoopStatus = 'INCONCLUSIVE' | 'Partial' | 'Ready';

  /** 控制方式（IDS v3.2 §2.2.7） */
  export type ControlMode = 'Auto' | 'Cascade' | 'Manual';

  /** Tag 角色（IDS v3.2 §2.2.12） */
  export type TagRole =
    | 'MODE'
    | 'OP'
    | 'PID_D'
    | 'PID_I'
    | 'PID_P'
    | 'PV'
    | 'SP';

  /** 质量码（IDS v3.2 §2.2.5） */
  export type Quality = 'Bad' | 'Good' | 'Uncertain' | null;

  /** 趋势时间窗（IDS v3.2 §2.2.14） */
  export type TrendWindow = 'last_1_hour' | 'last_7_days' | 'last_24_hours';

  /** KPI 状态（IDS v3.2 §2.2.14） */
  export type KpiStatus = 'GOOD' | 'INCONCLUSIVE' | 'POOR' | 'WARNING';

  /** 评分权重（6 大 KPI，总和须 100） */
  export interface ScoreWeights {
    /** 优良值率权重 */
    good_value_rate: number;
    /** 自动模式率权重 */
    auto_mode_rate: number;
    /** 稳定率权重 */
    steady_rate: number;
    /** 准确度权重 */
    accuracy_rate: number;
    /** 振荡率权重 */
    oscillation_rate: number;
    /** 饱和率权重 */
    saturation_rate: number;
  }

  /** Tag 关联完整性状态（7 个槽位） */
  export interface TagMappingStatus {
    pv: boolean;
    sp: boolean;
    op: boolean;
    mode: boolean;
    pid_p: boolean;
    pid_i: boolean;
    pid_d: boolean;
  }

  /** 回路列表项（IDS v3.2 §2.2.7） */
  export interface LoopListItem {
    loopId: string;
    tagName: string;
    description: string;
    unitId: string;
    unitName: string;
    controlMode: ControlMode;
    isActive: boolean;
    status: LoopStatus;
    score: number;
    lastScoreAt: string;
    tagMappingStatus: TagMappingStatus;
  }

  /** 回路列表查询参数（IDS v3.2 §2.2.7） */
  export interface LoopQueryParams extends PageQuery {
    plantNodeId?: string;
    controlMode?: ControlMode;
    isActive?: boolean;
    status?: LoopStatus;
    keyword?: string;
  }

  /** 创建回路参数（IDS v3.2 §2.2.8） */
  export interface CreateLoopParams {
    tagName: string;
    description?: string;
    unitId: string;
    scoreWeights?: ScoreWeights;
    isActive?: boolean;
    remark?: string;
  }

  /** 更新回路参数（IDS v3.2 §2.2.10） */
  export interface UpdateLoopParams {
    description?: string;
    scoreWeights?: ScoreWeights;
    isActive?: boolean;
    remark?: string;
  }

  /** 创建回路响应（IDS v3.2 §2.2.8） */
  export interface CreateLoopResult {
    loopId: string;
    tagName: string;
    description: string;
    unitId: string;
    status: LoopStatus;
    isActive: boolean;
    scoreWeights: ScoreWeights;
    remark?: string;
    createdAt: string;
    createdBy: string;
  }

  /** 更新回路响应（IDS v3.2 §2.2.10） */
  export interface UpdateLoopResult {
    loopId: string;
    description: string;
    scoreWeights: ScoreWeights;
    isActive: boolean;
    remark?: string;
    updatedAt: string;
    updatedBy: string;
  }

  /** 删除回路响应（IDS v3.2 §2.2.11） */
  export interface DeleteLoopResult {
    loopId: string;
    deleted: boolean;
    deletedAt: string;
  }

  /** 单个 Tag 关联信息（IDS v3.2 §2.2.9） */
  export interface TagMappingItem {
    tagId: null | string;
    tagName: null | string;
    required: boolean;
    associated: boolean;
  }

  /** 回路详情 - 基础信息（IDS v3.2 §2.2.9） */
  export interface LoopBasicInfo {
    loopId: string;
    tagName: string;
    description: string;
    unitId: string;
    unitName: string;
    isActive: boolean;
    status: LoopStatus;
    scoreWeights: ScoreWeights;
    remark?: string;
    createdAt: string;
    createdBy: string;
    updatedAt: string;
    updatedBy: string;
  }

  /** 回路详情 - Tag 关联映射（IDS v3.2 §2.2.9） */
  export interface LoopTagMapping {
    pv: TagMappingItem;
    sp: TagMappingItem;
    op: TagMappingItem;
    mode: TagMappingItem;
    pid_p: TagMappingItem;
    pid_i: TagMappingItem;
    pid_d: TagMappingItem;
  }

  /** 回路详情 - 运行态参数（IDS v3.2 §2.2.9） */
  export interface LoopRuntimeParams {
    controlMode: ControlMode;
    pidP: number;
    pidI: number;
    pidD: number;
    readAt: string;
  }

  /** 回路详情 - AAS 同步状态（IDS v3.2 §2.2.9） */
  export interface LoopAasSyncStatus {
    lastSyncAt: string;
    associatedTagCount: number;
  }

  /** 回路详情（IDS v3.2 §2.2.9） */
  export interface LoopDetail {
    basicInfo: LoopBasicInfo;
    tagMapping: LoopTagMapping;
    runtimeParams: LoopRuntimeParams;
    aasSyncStatus: LoopAasSyncStatus;
  }

  /** Tag 关联详情项（IDS v3.2 §2.2.12） */
  export interface LoopTagDetail {
    role: TagRole;
    tagId: null | string;
    tagName: null | string;
    description: null | string;
    required: boolean;
    associated: boolean;
    currentValue: null | number;
    quality: Quality;
    lastSyncAt: null | string;
  }

  /** Tag 关联详情（IDS v3.2 §2.2.12） */
  export interface LoopTagsResult {
    loopId: string;
    tagName: string;
    status: LoopStatus;
    tags: LoopTagDetail[];
  }

  /** Tag 关联更新参数（IDS v3.2 §2.2.13） */
  export interface UpdateTagMappingParams {
    pv: null | string;
    sp: null | string;
    op: null | string;
    mode: null | string;
    pid_p: null | string;
    pid_i: null | string;
    pid_d: null | string;
  }

  /** 监控列表项 - 当前值（IDS v3.2 §2.2.15） */
  export interface MonitorCurrentValues {
    pv: number;
    sp: number;
    op: number;
    mode: number;
    modeLabel: string;
    pvQuality: Quality;
    readAt?: string;
  }

  /** 回路监控列表项（IDS v3.2 §2.2.15） */
  export interface MonitorListItem {
    loopId: string;
    tagName: string;
    description: string;
    unitName: string;
    currentValues: MonitorCurrentValues;
    controlMode: ControlMode;
    score: number;
    status: LoopStatus;
    isActive: boolean;
    readAt: string;
  }

  /** 回路监控列表查询参数（IDS v3.2 §2.2.15） */
  export interface MonitorQueryParams extends PageQuery {
    plantNodeId?: string;
    view?: 'card' | 'list';
    keyword?: string;
  }

  /** 回路监控列表响应（IDS v3.2 §2.2.15） */
  export interface MonitorListResult {
    view: 'card' | 'list';
    items: MonitorListItem[];
    total: number;
    page: number;
    pageSize: number;
  }

  /** 回路监控详情 - 趋势数据（IDS v3.2 §2.2.14） */
  export interface MonitorTrend {
    timestamps: number[];
    pv: (null | number)[];
    sp: (null | number)[];
    op: (null | number)[];
    mode: (null | number)[];
    pvQuality: Quality[];
  }

  /** 回路监控详情 - KPI 摘要（IDS v3.2 §2.2.14） */
  export interface KpiSummary {
    good_value_rate: number;
    auto_mode_rate: number;
    steady_rate: number;
    accuracy_rate: number;
    oscillation_rate: number;
    saturation_rate: number;
    composite_score: number;
    status: KpiStatus;
    algorithm_version: string;
    calculatedAt: string;
  }

  /** 回路监控详情（IDS v3.2 §2.2.14） */
  export interface MonitorDetail {
    loopId: string;
    tagName: string;
    currentValues: MonitorCurrentValues & { readAt: string };
    runtimeParams: {
      controlMode: ControlMode;
      pidD: number;
      pidI: number;
      pidP: number;
    };
    trend: MonitorTrend;
    kpiSummary: KpiSummary;
  }
}

/**
 * 获取回路列表（分页）— IDS v3.2 §2.2.7
 */
export function getLoopListApi(params: LoopApi.LoopQueryParams) {
  return requestClient.get<PaginatedResponse<LoopApi.LoopListItem>>('/loops', {
    params,
  });
}

/**
 * 创建回路 — IDS v3.2 §2.2.8
 */
export function createLoopApi(data: LoopApi.CreateLoopParams) {
  return requestClient.post<LoopApi.CreateLoopResult>('/loops', data);
}

/**
 * 获取回路详情 — IDS v3.2 §2.2.9
 */
export function getLoopDetailApi(loopId: string) {
  return requestClient.get<LoopApi.LoopDetail>(`/loops/${loopId}`);
}

/**
 * 更新回路 — IDS v3.2 §2.2.10
 */
export function updateLoopApi(loopId: string, data: LoopApi.UpdateLoopParams) {
  return requestClient.put<LoopApi.UpdateLoopResult>(`/loops/${loopId}`, data);
}

/**
 * 删除回路 — IDS v3.2 §2.2.11
 */
export function deleteLoopApi(loopId: string) {
  return requestClient.delete<LoopApi.DeleteLoopResult>(`/loops/${loopId}`);
}

/**
 * 获取回路关联的 Tag 列表 — IDS v3.2 §2.2.12
 */
export function getLoopTagsApi(loopId: string) {
  return requestClient.get<LoopApi.LoopTagsResult>(`/loops/${loopId}/tags`);
}

/**
 * 更新回路 Tag 关联 — IDS v3.2 §2.2.13
 */
export function updateLoopTagMappingApi(
  loopId: string,
  data: LoopApi.UpdateTagMappingParams,
) {
  return requestClient.put<LoopApi.LoopTagsResult>(
    `/loops/${loopId}/tags`,
    data,
  );
}

/**
 * 获取回路运行态数据（详情） — IDS v3.2 §2.2.14
 */
export function getLoopMonitorDetailApi(
  loopId: string,
  trendWindow: LoopApi.TrendWindow = 'last_24_hours',
) {
  return requestClient.get<LoopApi.MonitorDetail>(`/loops/${loopId}/monitor`, {
    params: { trendWindow },
  });
}

/**
 * 获取回路监控列表 — IDS v3.2 §2.2.15
 */
export function getLoopMonitorListApi(params: LoopApi.MonitorQueryParams) {
  return requestClient.get<LoopApi.MonitorListResult>('/loops/monitor', {
    params,
  });
}
