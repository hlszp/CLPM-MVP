/**
 * CLPM 认证 API（占位模块）
 *
 * 对齐 IDS v3.2 接口契约，仅定义类型与函数签名，具体实现待后续补充。
 * 注意：框架核心认证 API 位于 `#/api/core/auth`，本模块为 CLPM 业务扩展。
 */
import type { UserInfo } from '@vben/types';

import { requestClient } from '#/api/request';

export namespace ClpmAuthApi {
  /** 登录参数 */
  export interface LoginParams {
    username: string;
    password: string;
  }

  /** 登录返回结果 */
  export interface LoginResult {
    accessToken: string;
    refreshToken: string;
    user: UserInfo;
  }

  /** 刷新 Token 返回结果 */
  export interface RefreshTokenResult {
    accessToken: string;
    refreshToken: string;
  }
}

/**
 * 登录
 */
export function loginApi(data: ClpmAuthApi.LoginParams) {
  return requestClient.post<ClpmAuthApi.LoginResult>('/auth/login', data);
}

/**
 * 刷新 accessToken
 */
export function refreshTokenApi(refreshToken: string) {
  return requestClient.post<ClpmAuthApi.RefreshTokenResult>('/auth/refresh', {
    refreshToken,
  });
}

/**
 * 退出登录
 */
export function logoutApi() {
  return requestClient.post('/auth/logout');
}

/**
 * 获取用户权限码
 */
export function getAccessCodesApi() {
  return requestClient.get<string[]>('/auth/codes');
}
