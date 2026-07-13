/**
 * 该文件可自行根据业务逻辑进行调整
 */
import type { RequestClientOptions } from '@vben/request';

import { useAppConfig } from '@vben/hooks';
import { preferences } from '@vben/preferences';
import {
  authenticateResponseInterceptor,
  defaultResponseInterceptor,
  errorMessageResponseInterceptor,
  RequestClient,
} from '@vben/request';
import { useAccessStore } from '@vben/stores';

import { message } from 'ant-design-vue';

import { useAuthStore } from '#/store';

import { refreshTokenApi } from './core';

const { apiURL } = useAppConfig(import.meta.env, import.meta.env.PROD);

function createRequestClient(baseURL: string, options?: RequestClientOptions) {
  const client = new RequestClient({
    ...options,
    baseURL,
  });

  /**
   * 重新认证逻辑
   */
  async function doReAuthenticate() {
    console.warn('Access token or refresh token is invalid or expired. ');
    const accessStore = useAccessStore();
    const authStore = useAuthStore();
    accessStore.setAccessToken(null);
    accessStore.setRefreshToken(null);
    if (
      preferences.app.loginExpiredMode === 'modal' &&
      accessStore.isAccessChecked
    ) {
      accessStore.setLoginExpired(true);
    } else {
      await authStore.logout();
    }
  }

  /**
   * 刷新 token 逻辑（对齐 IDS v3.2 §5.2）
   * 使用存储的 refreshToken 调用 /auth/refresh 获取新的 accessToken
   *
   * 标记 __isRetryRequest: true 防止 /auth/refresh 自身返回 401 时
   * 进入 refreshToken 队列导致死锁（请求挂起永不返回）。
   */
  async function doRefreshToken() {
    const accessStore = useAccessStore();
    const refreshToken = accessStore.refreshToken;
    if (!refreshToken) {
      throw new Error('No refresh token available');
    }
    const resp = await refreshTokenApi(refreshToken, {
      __isRetryRequest: true,
    });
    // 后端实现 refresh token rotation：每次刷新发放新的 accessToken + refreshToken
    // 必须同时保存两者，否则下次刷新会使用已黑名单的旧 refreshToken 导致失败
    accessStore.setAccessToken(resp.accessToken);
    if (resp.refreshToken) {
      accessStore.setRefreshToken(resp.refreshToken);
    }
    return resp.accessToken;
  }

  function formatToken(token: null | string) {
    return token ? `Bearer ${token}` : null;
  }

  // 请求头处理
  client.addRequestInterceptor({
    fulfilled: async (config) => {
      const accessStore = useAccessStore();

      config.headers.Authorization = formatToken(accessStore.accessToken);
      config.headers['Accept-Language'] = preferences.app.locale;
      return config;
    },
  });

  // 处理返回的响应数据格式（对齐 IDS v3.2 统一响应规范）
  // 成功：code === "0" 或 code === 0 → 返回 data 字段
  // 业务错误：code !== "0" → 抛出包含 {code, message} 的错误
  client.addResponseInterceptor(
    defaultResponseInterceptor({
      codeField: 'code',
      dataField: 'data',
      successCode: (code: any) => code === 0 || code === '0',
    }),
  );

  // token过期的处理（HTTP 401：触发 Refresh Token 流程）
  client.addResponseInterceptor(
    authenticateResponseInterceptor({
      client,
      doReAuthenticate,
      doRefreshToken,
      enableRefreshToken: preferences.app.enableRefreshToken,
      formatToken,
    }),
  );

  // 通用的错误处理
  // - HTTP 403：无权限 → 弹出无权限提示
  // - HTTP 5xx：服务异常 → 记录日志并提示"服务异常"
  // - 业务错误：优先展示后端返回的 message
  client.addResponseInterceptor(
    errorMessageResponseInterceptor((msg: string, error) => {
      const responseData = error?.response?.data ?? {};
      const status = error?.response?.status;
      const bizMessage = responseData?.message ?? '';

      // 5xx 服务异常：记录日志并提示
      if (status && status >= 500) {
        console.error('[CLPM] 服务异常:', {
          status,
          url: error?.config?.url,
          message: bizMessage || msg,
        });
        message.error('服务异常，请稍后重试');
        return;
      }

      // 403 无权限
      if (status === 403) {
        message.error('无权限访问');
        return;
      }

      // 业务错误或其他：优先使用后端返回的 message
      message.error(bizMessage || msg);
    }),
  );

  return client;
}

export const requestClient = createRequestClient(apiURL, {
  responseReturn: 'data',
});

export const baseRequestClient = new RequestClient({ baseURL: apiURL });
