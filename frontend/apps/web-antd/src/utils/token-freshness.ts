/**
 * WebSocket 建连前的 token 新鲜度保障（2026-09-06 控制台报错根因修复）
 *
 * 事故链：accessToken 30min 过期 + 页面闲置无 REST 请求 → vben 被动刷新
 * （仅 401 触发）从未执行 → WS 重连循环持续拿 store 里的过期 token 被
 * 403 拒绝，直到用户手动刷新页面才恢复。
 *
 * 本模块在 WS 客户端每次建连前主动校验 exp：临期/过期即调 refresh 换新。
 * REST 401 拦截器的 doRefreshToken（api/request.ts）仍是权威刷新路径；
 * 本模块复用同一 refreshTokenApi + rotation 存储语义，不重复另一套规则。
 */

import { useAccessStore } from '@vben/stores';

import { refreshTokenApi } from '#/api/core';

/** 解析 JWT payload（仅读 exp 等元数据，不校验签名——签名由服务端校验） */
function decodeJwtPayload(token: string): null | Record<string, any> {
  try {
    const part = token.split('.')[1] ?? '';
    if (!part) return null;
    const json = atob(part.replaceAll('-', '+').replaceAll('_', '/'));
    return JSON.parse(json);
  } catch {
    return null;
  }
}

/**
 * token 是否"陈旧"（临期/过期，建连前应先刷新）。
 *
 * 仅对可解析出 exp 的标准 JWT 判定；无法解析（非 JWT 格式等）按不陈旧处理，
 * 让调用方保持既有的同步建连行为（token 有效性最终由服务端裁决）。
 */
export function isTokenStale(token: null | string, marginSec = 15): boolean {
  if (!token) return false;
  const exp = decodeJwtPayload(token)?.exp;
  return typeof exp === 'number' && exp - Date.now() / 1000 <= marginSec;
}

/** 进行中的刷新去重（多个 WS 客户端同时建连只触发一次 refresh） */
let refreshInFlight: null | Promise<null | string> = null;

/**
 * 返回一个可用的 accessToken：非陈旧则原样返回；临期/过期且持有 refreshToken
 * 则换新（rotation：同时保存新 refreshToken）。无 refreshToken 或刷新失败时
 * 返回当前 token（保持调用方原行为）。
 */
export async function ensureFreshToken(marginSec = 15): Promise<null | string> {
  const store = useAccessStore();
  const current = store.accessToken;
  if (!isTokenStale(current, marginSec)) {
    return current ?? null;
  }
  const refreshToken = store.refreshToken;
  if (!refreshToken) {
    return current ?? null;
  }
  if (!refreshInFlight) {
    refreshInFlight = (async () => {
      try {
        const resp = await refreshTokenApi(refreshToken, {
          __isRetryRequest: true,
        });
        store.setAccessToken(resp.accessToken);
        if (resp.refreshToken) {
          store.setRefreshToken(resp.refreshToken);
        }
        return resp.accessToken;
      } catch {
        // 刷新失败不抛出：WS 侧按"有 refreshToken 但暂未换新"退避重试；
        // 会话真正失效由 REST 401 拦截器走登出路径
        return null;
      } finally {
        refreshInFlight = null;
      }
    })();
  }
  return refreshInFlight;
}
