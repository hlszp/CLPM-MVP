/**
 * CLPM 测点清单 API
 *
 * 覆盖测点列表查询、详情、更新、删除及批量导入导出能力。
 */
import type { PageQuery, PaginatedResponse } from '#/api/types';

import { requestClient } from '#/api/request';

export namespace TagApi {
  /** 测点类型（温度/压力/液位/流量/分析/速度/其他） */
  export type MeasureType =
    | 'ANALYSIS'
    | 'FLOW'
    | 'LEVEL'
    | 'OTHER'
    | 'PRESSURE'
    | 'SPEED'
    | 'TEMPERATURE';

  /** 参数类型（PV/SP/OP/MODE/PID_P/PID_I/PID_D/OTHER） */
  export type TagType =
    | 'MODE'
    | 'OP'
    | 'OTHER'
    | 'PID_D'
    | 'PID_I'
    | 'PID_P'
    | 'PV'
    | 'SP';

  /** 质量戳（GOOD/BAD/UNCERTAIN） */
  export type Quality = 'BAD' | 'GOOD' | 'UNCERTAIN';

  /** 测点列表项 */
  export interface TagItem {
    id: string;
    tagName: string;
    tagDescription?: string;
    tagType: TagType;
    currentValue?: null | number;
    quality?: null | Quality;
    lastSyncAt?: null | string;
    isLinked: boolean;
    rangeMin?: null | number;
    rangeMax?: null | number;
    unit?: string;
    measureType?: MeasureType | null;
    tdengineTagId?: string;
    loopId?: string;
    loopTagName?: string;
    loopDescription?: string;
    unitName?: string;
    /**
     * R06（数据链路整改）：实时新值无效（"-1.#QNAN0"/"nan"/"Infinity"/空值）时
     * 后端返回 currentValue=null 且 stale=true（不以 DB 旧值伪装最新有效读数）；
     * 前端 WS 失联期间也本地置位标旧
     */
    stale?: boolean;
    /**
     * 数据健康度（方案 C 轻量版）：实时质量码 + 同步新鲜度 + 所属回路 PV 完整度
     * 来自每日巡检快照，不在列表页逐 tag 实时查 TDengine
     */
    dataHealth?: TagDataHealth;
  }

  /** 测点数据健康度 */
  export interface TagDataHealth {
    /** 实时质量码（GOOD/BAD/UNCERTAIN） */
    quality?: null | Quality;
    /** 同步新鲜度（最近落库时间 ISO 串） */
    lastSyncAt?: null | string;
    /** 所属回路 PV 完整度（0~1，来自每日巡检快照） */
    loopPvCompleteness?: null | number;
    /** 所属回路完整性状态：OK/WARNING/CRITICAL/DATA_UNAVAILABLE */
    loopIntegrityStatus?: null | string;
    /** 最近巡检日期 */
    lastIntegrityCheck?: null | string;
  }

  /** 测点列表查询参数 */
  export interface TagQueryParams extends PageQuery {
    keyword?: string;
    /** 测点类型字典 code（可配置：系统管理 → 字典管理） */
    measureType?: string;
    /** 参数类型字典 code（可配置：系统管理 → 字典管理） */
    tagType?: string;
    plantNodeId?: string;
    isLinked?: boolean;
  }

  /** 更新测点参数 */
  export interface UpdateTagParams {
    tagDescription?: string;
    rangeMin?: null | number;
    rangeMax?: null | number;
    unit?: string;
    /** 测点类型字典 code（可配置：系统管理 → 字典管理） */
    measureType?: string;
    /** 参数类型字典 code（可配置：系统管理 → 字典管理） */
    tagType?: string;
    tdengineTagId?: string;
  }

  /** 新建测点参数 */
  export interface CreateTagParams {
    tagName: string;
    tagDescription?: string;
    rangeMin?: null | number;
    rangeMax?: null | number;
    unit?: string;
    /** 测点类型字典 code（可配置：系统管理 → 字典管理） */
    measureType?: string;
    /** 参数类型字典 code（可配置：系统管理 → 字典管理） */
    tagType?: string;
    tdengineTagId?: string;
  }

  /** Excel 导入单行错误 */
  export interface TagImportError {
    row: number;
    tagName?: null | string;
    message: string;
  }

  /** Excel 导入结果（upsert：位号存在则更新，否则新建） */
  export interface TagImportResult {
    /** 总行数 */
    total: number;
    /** 新建数 */
    inserted: number;
    /** 更新数 */
    updated: number;
    /** 失败数 */
    failed: number;
    /** 失败行明细 */
    errors: TagImportError[];
  }
}

/**
 * 获取测点列表（分页）
 */
export function getTagListApi(params: TagApi.TagQueryParams) {
  return requestClient.get<PaginatedResponse<TagApi.TagItem>>('/tags', {
    params,
  });
}

/**
 * 获取测点详情
 */
export function getTagDetailApi(tagId: string) {
  return requestClient.get<TagApi.TagItem>(`/tags/${tagId}`);
}

/**
 * 新建测点（ADMIN/IC_ENGINEER）
 *
 * 位号唯一；isLinked 恒为 false（仅由回路映射派生）
 */
export function createTagApi(data: TagApi.CreateTagParams) {
  return requestClient.post<TagApi.TagItem>('/tags', data);
}

/**
 * 更新测点
 */
export function updateTagApi(tagId: string, data: TagApi.UpdateTagParams) {
  return requestClient.put<TagApi.TagItem>(`/tags/${tagId}`, data);
}

/**
 * 删除测点
 */
export function deleteTagApi(tagId: string) {
  return requestClient.delete<null>(`/tags/${tagId}`);
}

/** 批量删除失败项 */
export interface TagBatchDeleteFailure {
  tagId: string;
  tagName?: string;
  reason: string;
}

/** 批量删除结果 */
export interface TagBatchDeleteResult {
  deleted: number;
  failed: number;
  failures: TagBatchDeleteFailure[];
}

/**
 * 批量删除测点（仅 ADMIN）
 *
 * 已关联回路的测点跳过并记入 failures。
 */
export function batchDeleteTagsApi(tagIds: string[]) {
  return requestClient.post<TagBatchDeleteResult>('/tags/batch-delete', {
    tagIds,
  });
}

/**
 * 根据回路位号自动匹配测点
 */
export function matchTagsForLoopApi(loopTagName: string) {
  return requestClient.get<
    Array<{
      measureType?: string;
      role: string;
      tagDescription?: string;
      tagId: string;
      tagName: string;
      tagType: string;
      unit?: string;
    }>
  >('/tags/match-loop', {
    params: { loopTagName },
  });
}
