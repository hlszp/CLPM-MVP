/**
 * CLPM 回路管理 API（占位模块）
 *
 * 对齐 IDS v3.2 接口契约，仅定义类型与函数签名，具体实现待后续补充。
 */
import type { PageQuery, PaginatedResponse } from '#/api/types';

import { requestClient } from '#/api/request';

export namespace LoopApi {
  /** 回路状态 */
  export type LoopStatus = 'auto' | 'cascade' | 'error' | 'manual';

  /** 回路类型 */
  export type LoopType = 'p' | 'pd' | 'pi' | 'pid';

  /** 回路信息 */
  export interface LoopInfo {
    id: string;
    /** 回路位号（唯一标识） */
    tag: string;
    /** 回路描述 */
    description: string;
    /** 所属装置/单元 */
    unit: string;
    /** 回路类型 */
    type: LoopType;
    /** 当前状态 */
    status: LoopStatus;
    /** 关联的控制器位号 */
    controllerTag?: string;
    /** 关联的测量值位号 */
    pvTag?: string;
    /** 关联的设定值位号 */
    spTag?: string;
    /** 关联的输出值位号 */
    opTag?: string;
    /** 创建时间 */
    createdAt: string;
    /** 更新时间 */
    updatedAt: string;
  }

  /** 回路创建参数 */
  export interface CreateLoopParams {
    tag: string;
    description?: string;
    unit: string;
    type: LoopType;
    controllerTag?: string;
    pvTag?: string;
    spTag?: string;
    opTag?: string;
  }

  /** 回路更新参数 */
  export interface UpdateLoopParams extends Partial<CreateLoopParams> {
    id: string;
  }

  /** 回路查询参数 */
  export interface LoopQueryParams extends PageQuery {
    unit?: string;
    status?: LoopStatus;
    type?: LoopType;
    keyword?: string;
  }
}

/**
 * 获取回路列表（分页）
 */
export function getLoopListApi(params: LoopApi.LoopQueryParams) {
  return requestClient.get<PaginatedResponse<LoopApi.LoopInfo>>('/loops', {
    params,
  });
}

/**
 * 获取回路详情
 */
export function getLoopDetailApi(id: string) {
  return requestClient.get<LoopApi.LoopInfo>(`/loops/${id}`);
}

/**
 * 创建回路
 */
export function createLoopApi(data: LoopApi.CreateLoopParams) {
  return requestClient.post<LoopApi.LoopInfo>('/loops', data);
}

/**
 * 更新回路
 */
export function updateLoopApi(data: LoopApi.UpdateLoopParams) {
  const { id, ...rest } = data;
  return requestClient.put<LoopApi.LoopInfo>(`/loops/${id}`, rest);
}

/**
 * 删除回路
 */
export function deleteLoopApi(id: string) {
  return requestClient.delete(`/loops/${id}`);
}
