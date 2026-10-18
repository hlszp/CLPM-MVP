/**
 * F07：使用真实 RequestClient 和响应拦截器保留 HTTP → 业务错误的边界。
 * 仅传输 adapter 提供确定性 HTTP 响应，不 mock 冲突提取器/API/关注状态机。
 */
import { useAccessStore, useUserStore } from '@vben/stores';

import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const wire = vi.hoisted(() => ({
  errorMessage: vi.fn(),
  reauthenticate: vi.fn(),
  refreshToken: vi.fn(),
  requests: [] as Array<{
    body: Record<string, any>;
    method: string;
    url: string;
  }>,
  responses: [] as Array<{ body: unknown; status: number }>,
}));
const messages = vi.hoisted(() => ({
  error: vi.fn(),
  success: vi.fn(),
  warning: vi.fn(),
}));

vi.mock('ant-design-vue', () => ({ message: messages }));
vi.mock('#/api/request', async () => {
  const {
    authenticateResponseInterceptor,
    AxiosError,
    defaultResponseInterceptor,
    errorMessageResponseInterceptor,
    RequestClient,
  } = await import('@vben/request');
  const client = new RequestClient({
    adapter: async (config) => {
      wire.requests.push({
        body:
          typeof config.data === 'string'
            ? JSON.parse(config.data)
            : config.data,
        method: config.method ?? '',
        url: config.url ?? '',
      });
      const next = wire.responses.shift();
      if (!next) throw new Error('Unexpected HTTP request');
      const response = {
        config,
        data: next.body,
        headers: {},
        status: next.status,
        statusText: String(next.status),
      };
      if (next.status >= 400) {
        throw new AxiosError(
          `Request failed with status code ${next.status}`,
          AxiosError.ERR_BAD_REQUEST,
          config,
          undefined,
          response,
        );
      }
      return response;
    },
    responseReturn: 'data',
  });
  client.addResponseInterceptor(
    defaultResponseInterceptor({
      codeField: 'code',
      dataField: 'data',
      successCode: (code: unknown) => code === 0 || code === '0',
    }),
  );
  client.addResponseInterceptor(
    authenticateResponseInterceptor({
      client,
      doReAuthenticate: wire.reauthenticate,
      doRefreshToken: wire.refreshToken,
      enableRefreshToken: true,
      formatToken: (token) => `Bearer ${token}`,
    }),
  );
  client.addResponseInterceptor(
    errorMessageResponseInterceptor((message, error) => {
      if ((error as any)?.config?.skipErrorMessage !== true) {
        wire.errorMessage(message, error);
      }
    }),
  );
  return { requestClient: client };
});

import { extractRevisionConflict, setLoopFollowApi } from '#/api/personal-work';
import {
  resetLoopFollowState,
  toggleLoopFollow,
  useLoopFollow,
} from '#/composables/use-loop-follow';

function conflictBody(
  data: Record<string, unknown>,
  code = 'ERR_REVISION_CONFLICT',
) {
  return { code, data, message: '关注状态版本冲突，请刷新后重试' };
}

beforeEach(() => {
  setActivePinia(createPinia());
  resetLoopFollowState();
  useAccessStore().setAccessToken('peer-token');
  useUserStore().setUserInfo({ userId: 'audit-peer' } as any);
  wire.requests.length = 0;
  wire.responses.length = 0;
  vi.clearAllMocks();
});

describe('个人工作冲突的真实请求错误边界（F07）', () => {
  it.each(['ERR_REVISION_CONFLICT', 'ERR_GENERATION_STALE'])(
    'RequestClient 解包 HTTP 409 后仍识别 %s 的当前版本载荷',
    async (code) => {
      const body = conflictBody(
        { currentGeneration: 4, currentRevision: 2, isFollowed: false },
        code,
      );
      wire.responses.push({ body, status: 409 });
      const error = await setLoopFollowApi('loop-1', {
        expectedRevision: 0,
        isFollowed: true,
        requestId: 'attempt-1',
      }).catch((error_: unknown) => error_);

      // 此处验证实际公共请求层，不手写模拟调用方的错误形状。
      expect(error).toEqual(body);
      expect(error).not.toHaveProperty('response');
      expect(extractRevisionConflict(error)).toMatchObject({
        code,
        currentGeneration: 4,
        currentRevision: 2,
        isFollowed: false,
      });
      expect(wire.errorMessage).not.toHaveBeenCalled();
      expect(wire.reauthenticate).not.toHaveBeenCalled();
      expect(wire.refreshToken).not.toHaveBeenCalled();
    },
  );

  it('仍兼容保留 Axios 包装的调用方；包装存在时要求 HTTP 409', () => {
    const data = conflictBody({ currentRevision: 7, isFollowed: true });
    expect(
      extractRevisionConflict({ response: { data, status: 409 } }),
    ).toMatchObject({ currentRevision: 7, isFollowed: true });
    expect(
      extractRevisionConflict({ response: { data, status: 500 } }),
    ).toBeNull();
  });

  it.each([
    null,
    undefined,
    'ERR_REVISION_CONFLICT',
    new Error('Network Error'),
    { code: 'ERR_NETWORK', data: { currentRevision: 2 } },
    { code: 'ERR_INTERNAL_SERVER_ERROR', data: { currentRevision: 2 } },
    { data: { currentRevision: 2 }, message: '409' },
  ])('不会把非冲突错误识别成可重试冲突：%j', (error) => {
    expect(extractRevisionConflict(error)).toBeNull();
  });

  it('真实请求链：本地 rev0/服务端 rev2，409 后用 rev2 仅重试一次并关注成功', async () => {
    wire.responses.push(
      {
        body: conflictBody({
          currentRevision: 2,
          isFollowed: false,
          loopId: 'loop-1',
        }),
        status: 409,
      },
      {
        body: {
          code: '0',
          data: {
            isFollowed: true,
            loopId: 'loop-1',
            replayed: false,
            revision: 3,
          },
        },
        status: 200,
      },
    );
    const outcome = await toggleLoopFollow('loop-1', 'AUDIT-F07-0001');
    expect(outcome.ok).toBe(true);
    expect(wire.requests).toHaveLength(2);
    expect(wire.requests.map(({ body }) => body.expectedRevision)).toEqual([
      0, 2,
    ]);
    expect(wire.requests.every(({ body }) => body.isFollowed === true)).toBe(
      true,
    );
    expect(wire.requests[0]!.body.requestId).not.toBe(
      wire.requests[1]!.body.requestId,
    );
    expect(useLoopFollow().isFollowed('loop-1')).toBe(true);
    expect(messages.success).toHaveBeenCalledWith('已关注 AUDIT-F07-0001');
    expect(messages.error).not.toHaveBeenCalled();
    expect(wire.errorMessage).not.toHaveBeenCalled();
  });

  it('真实请求链：409 已是目标状态，按幂等成功收敛且不发重试', async () => {
    wire.responses.push({
      body: conflictBody({
        currentRevision: 2,
        isFollowed: true,
        loopId: 'loop-1',
      }),
      status: 409,
    });
    const outcome = await toggleLoopFollow('loop-1');
    expect(outcome.ok).toBe(true);
    expect(wire.requests).toHaveLength(1);
    expect(useLoopFollow().isFollowed('loop-1')).toBe(true);
  });

  it('真实请求链：重试仍冲突时止于两次，回滚为服务端状态并如实提示', async () => {
    wire.responses.push(
      {
        body: conflictBody({ currentRevision: 2, isFollowed: false }),
        status: 409,
      },
      {
        body: conflictBody({ currentRevision: 4, isFollowed: false }),
        status: 409,
      },
    );
    const outcome = await toggleLoopFollow('loop-1');
    expect(outcome.ok).toBe(false);
    expect(wire.requests).toHaveLength(2);
    expect(useLoopFollow().isFollowed('loop-1')).toBe(false);
    expect(messages.warning).toHaveBeenCalledWith(
      expect.stringContaining('已在其他页面变更'),
    );
    expect(messages.success).not.toHaveBeenCalled();
  });

  it('真实请求链：普通服务错误不重试、不显示成功并回滚乐观状态', async () => {
    wire.responses.push({
      body: { code: 'ERR_INTERNAL_SERVER_ERROR', message: '服务异常' },
      status: 500,
    });
    const outcome = await toggleLoopFollow('loop-1');
    expect(outcome.ok).toBe(false);
    expect(wire.requests).toHaveLength(1);
    expect(useLoopFollow().isFollowed('loop-1')).toBe(false);
    expect(messages.error).toHaveBeenCalled();
    expect(messages.success).not.toHaveBeenCalled();
  });
});
