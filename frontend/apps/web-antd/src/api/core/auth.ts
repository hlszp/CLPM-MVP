import type { UserInfo } from '@vben/types';

import { requestClient } from '#/api/request';

export namespace AuthApi {
  /** 登录接口参数（对齐 IDS v3.2 §5.1） */
  export interface LoginParams {
    password: string;
    /** 是否记住登录（true 时 Refresh Token 有效期延长至 30 天） */
    rememberMe?: boolean;
    username: string;
  }

  /** 登录用户信息（对齐 IDS v3.2 §5.1 data.user） */
  export interface LoginUserInfo {
    /** 邮箱 */
    email: string;
    /** 用户唯一标识（UUID） */
    id: string;
    /** 显示名称 */
    displayName: string;
    /** 权限列表（模块:操作格式，* 表示通配） */
    permissions: string[];
    /** 角色枚举：ADMIN / IC_ENGINEER / PE_ENGINEER / SPONSOR / EXPERT */
    role: string;
    /** 用户名 */
    username: string;
  }

  /** 登录接口返回值（对齐 IDS v3.2 §5.1） */
  export interface LoginResult {
    /** JWT Access Token，有效期 30 分钟 */
    accessToken: string;
    /** Access Token 过期时间（秒），固定 1800 */
    expiresIn: number;
    /** JWT Refresh Token，有效期 7 天（rememberMe=true 时 30 天） */
    refreshToken: string;
    /** Token 类型，固定为 "Bearer" */
    tokenType: string;
    /** 用户信息 */
    user: LoginUserInfo;
  }

  /** 刷新 Token 返回结果（对齐 IDS v3.2 §5.2） */
  export interface RefreshTokenResult {
    accessToken: string;
    expiresIn: number;
    tokenType: string;
  }

  /** 当前用户信息（对齐 IDS v3.2 §5.4） */
  export interface CurrentUser {
    /** 角色默认首页路径 */
    defaultHome: string;
    /** 邮箱 */
    email: string;
    /** 用户唯一标识（UUID） */
    id: string;
    /** 最后登录时间（ISO8601） */
    lastLoginAt?: string;
    /** 权限列表 */
    permissions: string[];
    /** 角色枚举 */
    role: string;
    /** 显示名称 */
    displayName: string;
    /** 用户名 */
    username: string;
  }

  /** 修改密码参数（对齐 IDS v3.2 §5.5） */
  export interface ChangePasswordParams {
    /** 新密码（6-64 字符，需包含字母+数字） */
    newPassword: string;
    /** 当前密码（明文，HTTPS 保护） */
    oldPassword: string;
  }
}

/**
 * 登录（对齐 IDS v3.2 §5.1）
 */
export async function loginApi(data: AuthApi.LoginParams) {
  return requestClient.post<AuthApi.LoginResult>('/auth/login', data);
}

/**
 * 刷新 accessToken（对齐 IDS v3.2 §5.2）
 *
 * 支持传递额外 config（如 __isRetryRequest 标记），避免 /auth/refresh
 * 自身返回 401 时进入 refreshToken 队列导致死锁。
 */
export async function refreshTokenApi(
  refreshToken: string,
  config?: Record<string, any>,
) {
  return requestClient.post<AuthApi.RefreshTokenResult>(
    '/auth/refresh',
    { refreshToken },
    config,
  );
}

/**
 * 退出登录（对齐 IDS v3.2 §5.3）
 */
export async function logoutApi() {
  return requestClient.post('/auth/logout');
}

/**
 * 获取当前用户信息（对齐 IDS v3.2 §5.4）
 * 前端路由守卫和菜单渲染依赖此接口。
 */
export async function getUserInfoApi() {
  return requestClient.get<AuthApi.CurrentUser>('/auth/me');
}

/**
 * 获取用户权限码
 * 权限码来源于 /auth/me 返回的 permissions 字段，
 * 后端无 /auth/codes 路由，此处直接返回空数组，由 store 层从用户信息中提取。
 */
export async function getAccessCodesApi(): Promise<string[]> {
  return [];
}

/**
 * 修改密码（对齐 IDS v3.2 §5.5）
 * 修改成功后，当前 Access Token 和 Refresh Token 立即失效，前端需跳转登录页。
 */
export async function changePasswordApi(data: AuthApi.ChangePasswordParams) {
  return requestClient.put('/auth/password', data);
}

/**
 * 将 CLPM 当前用户信息转换为框架 UserInfo
 * 用于兼容 vue-vben-admin 的 useUserStore。
 */
export function mapCurrentUserToUserInfo(
  current: AuthApi.CurrentUser,
): UserInfo {
  return {
    avatar: '',
    desc: current.email,
    homePath: current.defaultHome || '/dashboard',
    realName: current.displayName,
    roles: [current.role],
    token: '',
    userId: current.id,
    username: current.username,
  };
}
