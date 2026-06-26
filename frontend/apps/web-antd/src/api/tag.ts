/**
 * CLPM 测点清单 API
 *
 * 覆盖测点列表查询、详情、更新、删除及批量导入导出能力。
 */
import type { PageQuery, PaginatedResponse } from '#/api/types';

import { requestClient } from '#/api/request';

export namespace TagApi {
  /** 测点类型（温度/压力/液位/流量/分析/阀位/其他） */
  export type MeasureType =
    | 'ANALYSIS'
    | 'FLOW'
    | 'LEVEL'
    | 'OTHER'
    | 'PRESSURE'
    | 'POSITION'
    | 'TEMPERATURE';

  /** 参数类型（PV/SP/OP/KP/TI/TD/MODE） */
  export type TagType =
    | 'MODE'
    | 'OP'
    | 'PV'
    | 'SP'
    | 'KP'
    | 'TI'
    | 'TD';

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
  }

  /** 测点列表查询参数 */
  export interface TagQueryParams extends PageQuery {
    keyword?: string;
    measureType?: MeasureType;
    tagType?: TagType;
    plantNodeId?: string;
    isLinked?: boolean;
  }

  /** 更新测点参数 */
  export interface UpdateTagParams {
    tagDescription?: string;
    rangeMin?: null | number;
    rangeMax?: null | number;
    unit?: string;
    measureType?: MeasureType;
    tagType?: TagType;
    tdengineTagId?: string;
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

/**
 * 根据回路位号自动匹配测点
 */
export function matchTagsForLoopApi(loopTagName: string) {
  return requestClient.get<
    Array<{
      role: string;
      tagId: string;
      tagName: string;
      tagDescription?: string;
      tagType: string;
      measureType?: string;
      unit?: string;
    }>
  >('/tags/match-loop', {
    params: { loopTagName },
  });
}
