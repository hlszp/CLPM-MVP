/**
 * utils/token-freshness 单元测试（2026-09-06 WS 403 死循环根因修复）
 *
 * 覆盖：isTokenStale 判定边界（临期/过期/非 JWT）；ensureFreshToken 的
 * 免刷新快路径、refresh rotation、并发去重与失败回退。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

const storeMock = vi.hoisted(() => ({
  store: {
    accessToken: '' as null | string,
    refreshToken: null as null | string,
    setAccessToken(t: string) {
      this.accessToken = t;
    },
    setRefreshToken(t: string) {
      this.refreshToken = t;
    },
  },
}));
const apiMock = vi.hoisted(() => ({ refreshTokenApi: vi.fn() }));

vi.mock('@vben/stores', () => ({
  useAccessStore: () => storeMock.store,
}));
vi.mock('#/api/core', () => ({
  refreshTokenApi: (...args: any[]) => apiMock.refreshTokenApi(...args),
}));

import { ensureFreshToken, isTokenStale } from '#/utils/token-freshness';

/** 构造标准 JWT（仅 exp 有意义） */
function makeJwt(expSecFromNow: number): string {
  const b64 = (obj: unknown) =>
    btoa(JSON.stringify(obj)).replaceAll('+', '-').replaceAll('/', '_');
  return `${b64({ alg: 'HS256', typ: 'JWT' })}.${b64({ exp: Math.floor(Date.now() / 1000) + expSecFromNow })}.sig`;
}

beforeEach(() => {
  storeMock.store.accessToken = null;
  storeMock.store.refreshToken = null;
  apiMock.refreshTokenApi.mockReset();
});

describe('isTokenStale', () => {
  it('未到期/临期/过期判定', () => {
    expect(isTokenStale(makeJwt(600))).toBe(false); // 余量充足
    expect(isTokenStale(makeJwt(5), 15)).toBe(true); // 余量内（临期）
    expect(isTokenStale(makeJwt(-60))).toBe(true); // 已过期
  });
  it('非 JWT / 空值按不陈旧处理（保持同步建连原行为）', () => {
    expect(isTokenStale('test-token')).toBe(false);
    expect(isTokenStale('a.b')).toBe(false);
    expect(isTokenStale('')).toBe(false);
    expect(isTokenStale(null)).toBe(false);
  });
});

describe('ensureFreshToken', () => {
  it('token 新鲜：原样返回且不调刷新', async () => {
    storeMock.store.accessToken = makeJwt(600);
    const t = await ensureFreshToken();
    expect(t).toBe(storeMock.store.accessToken);
    expect(apiMock.refreshTokenApi).not.toHaveBeenCalled();
  });

  it('token 过期且持有 refreshToken：换新并保存 rotation', async () => {
    storeMock.store.accessToken = makeJwt(-60);
    storeMock.store.refreshToken = 'r-old';
    apiMock.refreshTokenApi.mockResolvedValue({
      accessToken: 'new-access',
      refreshToken: 'new-refresh',
    });
    const t = await ensureFreshToken();
    expect(t).toBe('new-access');
    expect(storeMock.store.accessToken).toBe('new-access');
    expect(storeMock.store.refreshToken).toBe('new-refresh');
    expect(apiMock.refreshTokenApi).toHaveBeenCalledWith('r-old', {
      __isRetryRequest: true,
    });
  });

  it('并发去重：同时多次建连只触发一次 refresh', async () => {
    storeMock.store.accessToken = makeJwt(-60);
    storeMock.store.refreshToken = 'r-old';
    let resolveApi: (v: unknown) => void = () => {};
    apiMock.refreshTokenApi.mockReturnValue(
      new Promise((resolve) => {
        resolveApi = resolve;
      }),
    );
    const p1 = ensureFreshToken();
    const p2 = ensureFreshToken();
    resolveApi({ accessToken: 'ok', refreshToken: 'ok2' });
    expect(await p1).toBe('ok');
    expect(await p2).toBe('ok');
    expect(apiMock.refreshTokenApi).toHaveBeenCalledTimes(1);
  });

  it('无 refreshToken：返回当前 token 不调用 API', async () => {
    storeMock.store.accessToken = makeJwt(-60);
    const t = await ensureFreshToken();
    expect(t).toBe(storeMock.store.accessToken);
    expect(apiMock.refreshTokenApi).not.toHaveBeenCalled();
  });

  it('刷新失败：返回 null 且不抛出（会话失效交由 REST 401 路径处理）', async () => {
    storeMock.store.accessToken = makeJwt(-60);
    storeMock.store.refreshToken = 'r-old';
    apiMock.refreshTokenApi.mockRejectedValue(new Error('boom'));
    await expect(ensureFreshToken()).resolves.toBeNull();
  });
});
