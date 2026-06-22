/**
 * CLPM 系统管理 API（占位模块）
 *
 * 对齐 IDS v3.2 接口契约，仅定义类型与函数签名，具体实现待后续补充。
 */
import type { PageQuery, PaginatedResponse } from '#/api/types';

import { requestClient } from '#/api/request';

export namespace SystemApi {
  /** 用户信息 */
  export interface SystemUser {
    id: string;
    username: string;
    realName: string;
    email: string;
    phone?: string;
    department?: string;
    roles: string[];
    status: 'active' | 'disabled';
    createdAt: string;
    updatedAt: string;
  }

  /** 角色信息 */
  export interface Role {
    id: string;
    name: string;
    code: string;
    description?: string;
    permissions: string[];
    createdAt: string;
  }

  /** 装置/单元信息 */
  export interface Unit {
    id: string;
    name: string;
    code: string;
    description?: string;
    parentUnitId?: string;
    createdAt: string;
  }

  /** 用户查询参数 */
  export interface UserQueryParams extends PageQuery {
    keyword?: string;
    status?: SystemUser['status'];
    department?: string;
  }

  /** 创建用户参数 */
  export interface CreateUserParams {
    username: string;
    realName: string;
    email: string;
    phone?: string;
    department?: string;
    roles: string[];
    password: string;
  }

  /** 系统配置项 */
  export interface SystemConfig {
    key: string;
    value: string;
    description?: string;
    updatedAt: string;
  }
}

/**
 * 获取用户列表（分页）
 */
export function getUserListApi(params: SystemApi.UserQueryParams) {
  return requestClient.get<PaginatedResponse<SystemApi.SystemUser>>(
    '/system/users',
    { params },
  );
}

/**
 * 创建用户
 */
export function createUserApi(data: SystemApi.CreateUserParams) {
  return requestClient.post<SystemApi.SystemUser>('/system/users', data);
}

/**
 * 获取角色列表
 */
export function getRoleListApi() {
  return requestClient.get<SystemApi.Role[]>('/system/roles');
}

/**
 * 获取装置/单元列表
 */
export function getUnitListApi() {
  return requestClient.get<SystemApi.Unit[]>('/system/units');
}

/**
 * 获取系统配置
 */
export function getSystemConfigApi() {
  return requestClient.get<SystemApi.SystemConfig[]>('/system/configs');
}

/**
 * 更新系统配置
 */
export function updateSystemConfigApi(key: string, value: string) {
  return requestClient.put<SystemApi.SystemConfig>(`/system/configs/${key}`, {
    value,
  });
}
