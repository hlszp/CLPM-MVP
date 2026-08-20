/**
 * 通用字典项 API（可配置枚举：测点类型等）
 *
 * 字典类型编码常量与后端 app/services/dict_item.py 保持一致。
 */
import type { PaginatedResponse } from '#/api/types';

import { requestClient } from '#/api/request';

export namespace DictApi {
  /** 已注册字典类型 */
  export interface DictType {
    dictType: string;
    title: string;
  }

  /** 字典项（下拉用，轻量） */
  export interface DictItemOption {
    itemCode: string;
    itemLabel: string;
  }

  /** 字典项（管理列表行，含引用标记） */
  export interface DictItem {
    id: string;
    dictType: string;
    itemCode: string;
    itemLabel: string;
    sortOrder: number;
    isEnabled: boolean;
    /** 是否被业务数据引用（引用中不可删除/禁用） */
    isReferenced: boolean;
    updatedBy?: null | string;
    updatedAt?: null | string;
  }

  export interface CreateDictItemParams {
    dictType: string;
    itemCode: string;
    itemLabel: string;
    sortOrder?: number;
    isEnabled?: boolean;
  }

  export interface UpdateDictItemParams {
    itemLabel?: string;
    sortOrder?: number;
    isEnabled?: boolean;
  }
}

/** 测点类型字典编码（与后端 DICT_MEASURE_TYPE 一致） */
export const DICT_TYPE_MEASURE_TYPE = 'MEASURE_TYPE';

/** 参数类型字典编码（与后端 DICT_TAG_TYPE 一致） */
export const DICT_TYPE_TAG_TYPE = 'TAG_TYPE';

/** 回路类型字典编码（与后端 DICT_LOOP_TYPE 一致） */
export const DICT_TYPE_LOOP_TYPE = 'LOOP_TYPE';

/** 已注册字典类型列表 */
export function getDictTypesApi() {
  return requestClient.get<DictApi.DictType[]>('/dicts/types');
}

/** 字典项列表（下拉用，默认仅启用项） */
export function getDictItemsApi(dictType: string, enabledOnly = true) {
  return requestClient.get<DictApi.DictItemOption[]>(
    `/dicts/${dictType}/items`,
    { params: { enabledOnly } },
  );
}

/** 字典项分页管理列表（ADMIN，含引用标记） */
export function getDictItemsPagedApi(
  dictType: string,
  params: { page?: number; pageSize?: number },
) {
  return requestClient.get<PaginatedResponse<DictApi.DictItem>>('/dicts/items', {
    params: { dictType, ...params },
  });
}

/** 新建字典项（ADMIN） */
export function createDictItemApi(data: DictApi.CreateDictItemParams) {
  return requestClient.post<DictApi.DictItem>('/dicts/items', data);
}

/** 更新字典项（ADMIN；code 与 dictType 不可改） */
export function updateDictItemApi(id: string, data: DictApi.UpdateDictItemParams) {
  return requestClient.put<DictApi.DictItem>(`/dicts/items/${id}`, data);
}

/** 删除字典项（ADMIN；被业务数据引用时后端拒绝） */
export function deleteDictItemApi(id: string) {
  return requestClient.delete<null>(`/dicts/items/${id}`);
}
