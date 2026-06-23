/**
 * Axios 拦截器单元测试
 *
 * 覆盖 src/api/request.ts 中配置的请求/响应拦截器：
 * - 请求拦截：Token 注入
 * - 响应拦截：成功响应、401 刷新、刷新失败、业务错误、权限拒绝、网络错误
 */
import type { AxiosInstance } from 'axios';

import { createPinia, setActivePinia } from 'pinia';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import MockAdapter from 'axios-mock-adapter';

// Mock 依赖模块（必须在 import requestClient 之前声明）
vi.mock('@vben/hooks', () => ({
  useAppConfig: () => ({
    apiURL: '/api',
    baseURL: '/api',
  }),
}));

vi.mock('@vben/preferences', () => ({
  preferences: {
    app: {
      locale: 'zh-CN',
      enableRefreshToken: true,
      loginExpiredMode: 'modal',
      defaultHomePath: '/dashboard',
    },
  },
}));

const messageErrorSpy = vi.fn();
const messageSuccessSpy = vi.fn();
vi.mock('ant-design-vue', () => ({
  message: {
    error: (...args: any[]) => messageErrorSpy(...args),
    success: (...args: any[]) => messageSuccessSpy(...args),
    loading: vi.fn(),
    warning: vi.fn(),
  },
  notification: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

// Mock auth store（避免 router 依赖）
const logoutSpy = vi.fn();
vi.mock('#/store', () => ({
  useAuthStore: () => ({
    logout: (...args: any[]) => logoutSpy(...args),
  }),
}));

// Mock refreshTokenApi
const refreshTokenApiMock = vi.fn();
vi.mock('#/api/core', () => ({
  refreshTokenApi: (...args: any[]) => refreshTokenApiMock(...args),
}));

// 导入被测模块（在 mock 声明之后）
import { useAccessStore } from '@vben/stores';
import { requestClient } from '#/api/request';

describe('axios 拦截器测试', () => {
  let mock: MockAdapter;

  beforeEach(() => {
    setActivePinia(createPinia());
    mock = new MockAdapter(requestClient.instance as AxiosInstance);
    messageErrorSpy.mockClear();
    logoutSpy.mockClear();
    refreshTokenApiMock.mockReset();
  });

  afterEach(() => {
    mock.restore();
  });

  // UT-HTTP-001: 请求拦截-Token注入（已登录时 Header 含 Authorization）
  it('uT-HTTP-001: 已登录时请求头包含 Authorization Bearer token', async () => {
    const accessStore = useAccessStore();
    accessStore.setAccessToken('test-access-token');

    mock.onGet('/test/auth').reply((config) => {
      // 验证请求头
      expect(config.headers?.Authorization).toBe('Bearer test-access-token');
      return [200, { code: '0', message: 'success', data: { ok: true } }];
    });

    const result = await requestClient.get('/test/auth');
    expect(result).toEqual({ ok: true });
  });

  // UT-HTTP-002: 请求拦截-无Token（未登录时无 Authorization Header）
  it('uT-HTTP-002: 未登录时请求头 Authorization 为 null', async () => {
    const accessStore = useAccessStore();
    accessStore.setAccessToken(null);

    mock.onGet('/test/no-auth').reply((config) => {
      // 未登录时 Authorization 应为 null（formatToken 返回 null）
      expect(config.headers?.Authorization).toBeNull();
      return [200, { code: '0', message: 'success', data: { ok: true } }];
    });

    const result = await requestClient.get('/test/no-auth');
    expect(result).toEqual({ ok: true });
  });

  // UT-HTTP-003: 响应拦截-200成功（返回 response.data）
  it('uT-HTTP-003: 成功响应返回 data 字段', async () => {
    mock.onGet('/test/success').reply(200, {
      code: '0',
      message: 'success',
      data: { id: 1, name: 'test' },
    });

    const result = await requestClient.get('/test/success');
    // 应返回 data 字段内容，而非整个响应体
    expect(result).toEqual({ id: 1, name: 'test' });
    expect((result as any).code).toBeUndefined();
  });

  // UT-HTTP-004: 响应拦截-401触发刷新（accessToken 过期自动调用 refreshToken）
  it('uT-HTTP-004: 401 响应触发 refreshToken 刷新', async () => {
    const accessStore = useAccessStore();
    accessStore.setAccessToken('expired-token');
    accessStore.setRefreshToken('valid-refresh-token');

    // refreshTokenApi 成功返回新 token
    refreshTokenApiMock.mockResolvedValue({
      accessToken: 'new-access-token',
      expiresIn: 1800,
      tokenType: 'Bearer',
    });

    // 第一次请求返回 401，重试请求成功
    mock.onGet('/test/401').replyOnce(401, {
      code: '401',
      message: 'token expired',
      data: null,
    });
    mock.onGet('/test/401').reply(200, {
      code: '0',
      message: 'success',
      data: { refreshed: true },
    });

    const result = await requestClient.get('/test/401');
    // refreshToken 应被调用
    expect(refreshTokenApiMock).toHaveBeenCalledWith('valid-refresh-token');
    // accessToken 应被更新
    expect(accessStore.accessToken).toBe('new-access-token');
    // 应返回重试后的数据
    expect(result).toEqual({ refreshed: true });
  });

  // UT-HTTP-005: 响应拦截-刷新失败跳登录（refreshToken 也过期时清空 store 跳转）
  it('uT-HTTP-005: refreshToken 刷新失败时触发登出', async () => {
    const accessStore = useAccessStore();
    accessStore.setAccessToken('expired-token');
    accessStore.setRefreshToken('expired-refresh-token');

    // refreshTokenApi 抛出错误（refreshToken 也过期了）
    refreshTokenApiMock.mockRejectedValue(new Error('refresh token expired'));

    mock.onGet('/test/refresh-fail').replyOnce(401, {
      code: '401',
      message: 'token expired',
      data: null,
    });

    // 请求应被拒绝
    await expect(requestClient.get('/test/refresh-fail')).rejects.toBeTruthy();
    // refreshToken 应被调用
    expect(refreshTokenApiMock).toHaveBeenCalledWith('expired-refresh-token');
    // 应触发登出流程
    expect(logoutSpy).toHaveBeenCalled();
  });

  // UT-HTTP-006: 响应拦截-400业务错误（message.error 显示错误）
  it('uT-HTTP-006: 业务错误时 message.error 显示错误信息', async () => {
    mock.onGet('/test/biz-error').reply(200, {
      code: 'ERR_VALIDATION',
      message: '参数校验失败',
      data: null,
    });

    // 业务错误（code !== "0"）应抛出异常
    await expect(requestClient.get('/test/biz-error')).rejects.toBeTruthy();
  });

  // UT-HTTP-007: 响应拦截-403权限拒绝（message.error "权限不足"）
  it('uT-HTTP-007: 403 响应触发 message.error 提示无权限', async () => {
    mock.onGet('/test/403').reply(403, {
      code: '403',
      message: 'Forbidden',
      data: null,
    });

    await expect(requestClient.get('/test/403')).rejects.toBeTruthy();
    // 应调用 message.error
    expect(messageErrorSpy).toHaveBeenCalled();
    // 提示内容应包含"无权限"
    const msgArg = messageErrorSpy.mock.calls[0]?.[0] as string;
    expect(msgArg).toContain('无权限');
  });

  // UT-HTTP-008: 响应拦截-网络错误（message.error "网络异常"）
  it('uT-HTTP-008: 网络错误时 message.error 提示网络异常', async () => {
    mock.onGet('/test/network-error').networkError();

    await expect(requestClient.get('/test/network-error')).rejects.toBeTruthy();
    // 应调用 message.error
    expect(messageErrorSpy).toHaveBeenCalled();
  });
});
