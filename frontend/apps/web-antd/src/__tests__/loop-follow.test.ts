/**
 * P6-05 关注入口断链修复测试
 *
 * 覆盖：
 * - use-loop-follow 共享集：分页拉全量（幂等去重）、toggle 设定语义
 *   （expectedRevision/requestId 契约）、409 三分支（幂等收敛 / 新版本
 *   重试一次 / 重试仍冲突按服务端真实状态回滚+如实提示）、网络失败回滚、
 *   busy 防重入；
 * - LoopHeader ★ 星标（wb360 页头入口）：初始态/已关注态渲染、点击
 *   emit toggleFollow、在途禁点。
 */
import { mount } from '@vue/test-utils';

import { useAccessStore, useUserStore } from '@vben/stores';

import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({
  getMyLoopFollowsApi: vi.fn(),
  setLoopFollowApi: vi.fn(),
}));

/* 网络 API mock；extractRevisionConflict / newRequestId 为纯函数保留真实现 */
vi.mock('#/api/personal-work', async () => {
  const actual = await vi.importActual<any>('#/api/personal-work');
  return {
    ...actual,
    getMyLoopFollowsApi: (...a: any[]) => api.getMyLoopFollowsApi(...a),
    setLoopFollowApi: (...a: any[]) => api.setLoopFollowApi(...a),
  };
});

const msg = vi.hoisted(() => ({
  error: vi.fn(),
  success: vi.fn(),
  warning: vi.fn(),
}));

vi.mock('ant-design-vue', async () => {
  const actual = await vi.importActual<any>('ant-design-vue');
  return { ...actual, message: msg };
});

vi.mock('@vben/preferences', async () => {
  const actual = await vi.importActual<any>('@vben/preferences');
  return {
    ...actual,
    updatePreferences: vi.fn(),
    usePreferences: () => ({ isDark: { value: false } }),
  };
});

import {
  refreshFollows,
  resetLoopFollowState,
  toggleLoopFollow,
  useLoopFollow,
} from '#/composables/use-loop-follow';
import LoopHeader from '#/views/loop/workbench360/components/LoopHeader.vue';

function followPage(items: Array<[string, number]>, total: number) {
  return {
    inaccessibleCount: 0,
    items: items.map(([loopId, revision]) => ({
      description: null,
      isFollowed: true,
      loopId,
      revision,
      tagName: `TAG-${loopId}`,
      unitId: null,
      updatedAt: '2026-10-16T00:00:00Z',
    })),
    page: 1,
    pageSize: 100,
    readAt: '2026-10-16T00:00:00Z',
    readStatus: 'COMPLETE' as const,
    total,
  };
}

function conflict409(data: Record<string, any>) {
  return {
    response: {
      data: {
        code: 'ERR_REVISION_CONFLICT',
        data,
        message: '关注状态版本冲突，请刷新后重试',
      },
      status: 409,
    },
  };
}

function setAccount(userId: null | string) {
  useAccessStore().setAccessToken(userId ? `token-${userId}` : null);
  useUserStore().setUserInfo(
    userId
      ? ({
          realName: userId,
          roles: ['IC_ENGINEER'],
          userId,
          username: userId,
        } as any)
      : null,
  );
}

function deferred<T = any>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((accept, fail) => {
    resolve = accept;
    reject = fail;
  });
  return { promise, reject, resolve };
}

function changedFollow(isFollowed: boolean, revision = 1) {
  return {
    isFollowed,
    loopId: 'loop-1',
    replayed: false,
    revision,
    updatedAt: '',
  };
}

beforeEach(() => {
  setActivePinia(createPinia());
  api.getMyLoopFollowsApi.mockReset();
  api.setLoopFollowApi.mockReset();
  msg.error.mockReset();
  msg.success.mockReset();
  msg.warning.mockReset();
  resetLoopFollowState();
  setAccount('user-a');
});

describe('use-loop-follow 共享关注集（P6-05）', () => {
  it('refreshFollows：分页拉全量（跨页合并）；就绪后重复调用零请求', async () => {
    api.getMyLoopFollowsApi
      .mockResolvedValueOnce(followPage([['loop-1', 3]], 150))
      .mockResolvedValueOnce(
        followPage(
          [
            ['loop-101', 1],
            ['loop-102', 5],
          ],
          150,
        ),
      );
    const ok = await refreshFollows();
    expect(ok).toBe(true);
    expect(api.getMyLoopFollowsApi).toHaveBeenCalledTimes(2);
    expect(api.getMyLoopFollowsApi).toHaveBeenNthCalledWith(1, {
      page: 1,
      pageSize: 100,
    });
    expect(api.getMyLoopFollowsApi).toHaveBeenNthCalledWith(2, {
      page: 2,
      pageSize: 100,
    });
    const follow = useLoopFollow();
    expect(follow.isFollowed('loop-1')).toBe(true);
    expect(follow.isFollowed('loop-102')).toBe(true);
    expect(follow.isFollowed('loop-x')).toBe(false);
    // 幂等：就绪后 ensureLoaded 不再发请求
    await follow.ensureLoaded();
    expect(api.getMyLoopFollowsApi).toHaveBeenCalledTimes(2);
  });

  it('首次关注：expectedRevision=0 + isFollowed=true + requestId；成功后以服务端 revision 收敛', async () => {
    api.setLoopFollowApi.mockResolvedValueOnce({
      isFollowed: true,
      loopId: 'loop-9',
      replayed: false,
      revision: 1,
      updatedAt: '2026-10-16T00:00:00Z',
    });
    const outcome = await toggleLoopFollow('loop-9', 'TIC-900');
    expect(outcome.ok).toBe(true);
    expect(api.setLoopFollowApi).toHaveBeenCalledWith(
      'loop-9',
      expect.objectContaining({ expectedRevision: 0, isFollowed: true }),
    );
    const body = api.setLoopFollowApi.mock.calls[0]![1]!;
    expect(typeof body.requestId).toBe('string');
    expect(body.requestId.length).toBeGreaterThan(0);
    expect(useLoopFollow().isFollowed('loop-9')).toBe(true);
    expect(msg.success).toHaveBeenCalledWith('已关注 TIC-900');
  });

  it('取消关注：携带已知 revision 的设定语义；成功后从关注集移除', async () => {
    api.getMyLoopFollowsApi.mockResolvedValueOnce(
      followPage([['loop-1', 3]], 1),
    );
    await refreshFollows();
    api.setLoopFollowApi.mockResolvedValueOnce({
      isFollowed: false,
      loopId: 'loop-1',
      replayed: false,
      revision: 4,
      updatedAt: '2026-10-16T00:00:00Z',
    });
    const outcome = await toggleLoopFollow('loop-1', 'TIC-100');
    expect(outcome.ok).toBe(true);
    expect(api.setLoopFollowApi).toHaveBeenCalledWith(
      'loop-1',
      expect.objectContaining({ expectedRevision: 3, isFollowed: false }),
    );
    expect(useLoopFollow().isFollowed('loop-1')).toBe(false);
    expect(msg.success).toHaveBeenCalledWith('已取消关注 TIC-100');
  });

  it('409 幂等收敛：服务端已是目标状态（他处已操作）→ 按成功口径对齐，零重试', async () => {
    // 本地未关注 → 目标关注；服务端已关注（他处已点）
    api.setLoopFollowApi.mockRejectedValueOnce(
      conflict409({ currentRevision: 2, isFollowed: true, loopId: 'loop-7' }),
    );
    const outcome = await toggleLoopFollow('loop-7', 'TIC-700');
    expect(outcome.ok).toBe(true);
    expect(api.setLoopFollowApi).toHaveBeenCalledTimes(1);
    expect(useLoopFollow().isFollowed('loop-7')).toBe(true);
    expect(msg.success).toHaveBeenCalled();
  });

  it('409 版本失配（本地 revision 过期）：以 currentRevision 重试一次成功；两次 requestId 不同', async () => {
    api.getMyLoopFollowsApi.mockResolvedValueOnce(
      followPage([['loop-1', 1]], 1),
    );
    await refreshFollows();
    // 本地 rev=1 过期；服务端 currentRevision=5 仍关注中 → 取消关注重试成功
    api.setLoopFollowApi
      .mockRejectedValueOnce(
        conflict409({ currentRevision: 5, isFollowed: true, loopId: 'loop-1' }),
      )
      .mockResolvedValueOnce({
        isFollowed: false,
        loopId: 'loop-1',
        replayed: false,
        revision: 6,
        updatedAt: '2026-10-16T00:00:00Z',
      });
    const outcome = await toggleLoopFollow('loop-1');
    expect(outcome.ok).toBe(true);
    expect(api.setLoopFollowApi).toHaveBeenCalledTimes(2);
    expect(api.setLoopFollowApi.mock.calls[1]![1]).toEqual(
      expect.objectContaining({ expectedRevision: 5, isFollowed: false }),
    );
    const [r1, r2] = api.setLoopFollowApi.mock.calls.map(
      (c) => (c[1] as any).requestId,
    );
    expect(r1).not.toBe(r2); // 重试不是幂等重放
    expect(useLoopFollow().isFollowed('loop-1')).toBe(false);
  });

  it('409 重试仍冲突：按服务端真实状态回滚（不保留乐观假态）+ 如实提示', async () => {
    // 目标取消关注；两次 409 均报服务端仍关注中 → 本地保持关注态
    api.getMyLoopFollowsApi.mockResolvedValueOnce(
      followPage([['loop-1', 1]], 1),
    );
    await refreshFollows();
    api.setLoopFollowApi
      .mockRejectedValueOnce(
        conflict409({ currentRevision: 5, isFollowed: true, loopId: 'loop-1' }),
      )
      .mockRejectedValueOnce(
        conflict409({ currentRevision: 6, isFollowed: true, loopId: 'loop-1' }),
      );
    const outcome = await toggleLoopFollow('loop-1', 'TIC-100');
    expect(outcome.ok).toBe(false);
    expect(outcome.conflict?.isFollowed).toBe(true);
    expect(useLoopFollow().isFollowed('loop-1')).toBe(true);
    expect(msg.warning).toHaveBeenCalledWith(
      expect.stringContaining('已在其他页面变更'),
    );
  });

  it('网络/5xx 失败：回滚操作前状态 + 错误提示（不冒充成功）', async () => {
    api.setLoopFollowApi.mockRejectedValueOnce(new TypeError('Network Error'));
    const outcome = await toggleLoopFollow('loop-9', 'TIC-900');
    expect(outcome.ok).toBe(false);
    expect(outcome.conflict).toBeUndefined();
    expect(useLoopFollow().isFollowed('loop-9')).toBe(false);
    expect(msg.error).toHaveBeenCalledWith(
      expect.stringContaining('关注 TIC-900 失败'),
    );
  });

  it('busy 防重入：在途期间同回路二次点击直接失败且不新增请求', async () => {
    let resolveFirst!: (v: any) => void;
    api.setLoopFollowApi.mockImplementationOnce(
      () => new Promise((res) => (resolveFirst = res)),
    );
    const first = toggleLoopFollow('loop-9', 'TIC-900');
    const second = await toggleLoopFollow('loop-9', 'TIC-900');
    expect(second.ok).toBe(false);
    expect(api.setLoopFollowApi).toHaveBeenCalledTimes(1);
    resolveFirst({
      isFollowed: true,
      loopId: 'loop-9',
      replayed: false,
      revision: 1,
      updatedAt: '',
    });
    const firstOutcome = await first;
    expect(firstOutcome.ok).toBe(true);
    expect(useLoopFollow().isFollowed('loop-9')).toBe(true);
  });
});

describe('关注账户隔离与晚到响应（F07）', () => {
  it('A 已加载的星标在换 B 时同步清空；B 不复用 A 的 ready 缓存', async () => {
    api.getMyLoopFollowsApi
      .mockResolvedValueOnce(followPage([['loop-a', 2]], 1))
      .mockResolvedValueOnce(followPage([['loop-b', 4]], 1));
    const follow = useLoopFollow();
    await follow.ensureLoaded();
    expect(follow.isFollowed('loop-a')).toBe(true);

    setAccount('user-b');
    expect(follow.isFollowed('loop-a')).toBe(false);
    expect(follow.followedLoopIds.value.size).toBe(0);
    expect(follow.followLoadState.value).toBe('idle');
    await follow.ensureLoaded();
    expect(api.getMyLoopFollowsApi).toHaveBeenCalledTimes(2);
    expect(follow.isFollowed('loop-b')).toBe(true);
  });

  it('A 的晚到读取不写入 B，也不清掉 B 在途 promise 的去重所有权', async () => {
    const a = deferred();
    const b = deferred();
    api.getMyLoopFollowsApi
      .mockReturnValueOnce(a.promise)
      .mockReturnValueOnce(b.promise);
    const follow = useLoopFollow();
    const old = refreshFollows();
    setAccount('user-b');
    const current = refreshFollows();
    expect(current).not.toBe(old);

    a.resolve(followPage([['loop-a', 1]], 1));
    expect(await old).toBe(false);
    expect(follow.isFollowed('loop-a')).toBe(false);
    expect(follow.followLoadState.value).toBe('loading');
    expect(refreshFollows()).toBe(current);
    expect(api.getMyLoopFollowsApi).toHaveBeenCalledTimes(2);
    b.resolve(followPage([['loop-b', 1]], 1));
    expect(await current).toBe(true);
    expect(follow.isFollowed('loop-b')).toBe(true);
  });

  it('A 的晚到读取失败不能把 B 已加载的状态改成 error', async () => {
    const a = deferred();
    api.getMyLoopFollowsApi
      .mockReturnValueOnce(a.promise)
      .mockResolvedValueOnce(followPage([['loop-b', 1]], 1));
    const old = refreshFollows();
    setAccount('user-b');
    await refreshFollows();
    a.reject(new TypeError('old account disconnected'));
    expect(await old).toBe(false);
    expect(useLoopFollow().followLoadState.value).toBe('ready');
    expect(useLoopFollow().isFollowed('loop-b')).toBe(true);
  });

  it('A 的分页读取在换账号后停止，不用 B 的令牌继续读取下一页', async () => {
    const firstPage = deferred();
    api.getMyLoopFollowsApi.mockReturnValueOnce(firstPage.promise);
    const old = refreshFollows();
    setAccount('user-b');
    firstPage.resolve(followPage([['loop-a', 1]], 250));
    expect(await old).toBe(false);
    expect(api.getMyLoopFollowsApi).toHaveBeenCalledTimes(1);
    expect(useLoopFollow().followedLoopIds.value.size).toBe(0);
  });

  it('A 取消关注的晚到成功不能取消 B 的乐观关注或解除 B 的 busy 锁', async () => {
    const a = deferred();
    const b = deferred();
    api.getMyLoopFollowsApi.mockResolvedValueOnce(
      followPage([['loop-1', 3]], 1),
    );
    await refreshFollows();
    api.setLoopFollowApi
      .mockReturnValueOnce(a.promise)
      .mockReturnValueOnce(b.promise);
    const follow = useLoopFollow();
    const old = follow.toggle('loop-1');
    setAccount('user-b');
    const current = follow.toggle('loop-1');
    expect(follow.isFollowed('loop-1')).toBe(true);

    a.resolve(changedFollow(false, 4));
    expect(await old).toMatchObject({ ok: false });
    expect(follow.isFollowed('loop-1')).toBe(true);
    expect(follow.isBusy('loop-1')).toBe(true);
    expect(msg.success).not.toHaveBeenCalled();
    expect(await follow.toggle('loop-1')).toMatchObject({ ok: false });
    expect(api.setLoopFollowApi).toHaveBeenCalledTimes(2);
    b.resolve(changedFollow(true, 1));
    expect(await current).toMatchObject({ ok: true });
    expect(follow.isBusy('loop-1')).toBe(false);
    expect(msg.success).toHaveBeenCalledTimes(1);
  });

  it('A 关注失败不能回滚 B 已从服务端加载的关注，也不弹旧账户失败提示', async () => {
    const a = deferred();
    api.setLoopFollowApi.mockReturnValueOnce(a.promise);
    const old = toggleLoopFollow('loop-1');
    setAccount('user-b');
    api.getMyLoopFollowsApi.mockResolvedValueOnce(
      followPage([['loop-1', 7]], 1),
    );
    await refreshFollows();
    a.reject(new TypeError('old account disconnected'));
    expect(await old).toMatchObject({ ok: false });
    expect(useLoopFollow().isFollowed('loop-1')).toBe(true);
    expect(msg.error).not.toHaveBeenCalled();
  });

  it('A 的晚到 409 不得使用 B 的令牌重试写入', async () => {
    const a = deferred();
    api.setLoopFollowApi.mockReturnValueOnce(a.promise);
    const old = toggleLoopFollow('loop-1');
    setAccount('user-b');
    a.reject(
      conflict409({ currentRevision: 5, isFollowed: false, loopId: 'loop-1' }),
    );
    expect(await old).toMatchObject({ ok: false });
    expect(api.setLoopFollowApi).toHaveBeenCalledTimes(1);
    expect(msg.success).not.toHaveBeenCalled();
    expect(msg.warning).not.toHaveBeenCalled();
  });

  it.each(['resolve', 'reject'] as const)(
    'A 已发出的冲突重试晚到 %s 也不能改写 B',
    async (ending) => {
      const retry = deferred();
      api.setLoopFollowApi
        .mockRejectedValueOnce(
          conflict409({
            currentRevision: 2,
            isFollowed: false,
            loopId: 'loop-1',
          }),
        )
        .mockReturnValueOnce(retry.promise);
      const old = toggleLoopFollow('loop-1');
      await vi.waitFor(() =>
        expect(api.setLoopFollowApi).toHaveBeenCalledTimes(2),
      );
      setAccount('user-b');
      api.getMyLoopFollowsApi.mockResolvedValueOnce(followPage([], 0));
      await refreshFollows();
      if (ending === 'resolve') retry.resolve(changedFollow(true, 3));
      else
        retry.reject(
          conflict409({
            currentRevision: 4,
            isFollowed: true,
            loopId: 'loop-1',
          }),
        );
      expect(await old).toMatchObject({ ok: false });
      expect(useLoopFollow().isFollowed('loop-1')).toBe(false);
      expect(msg.success).not.toHaveBeenCalled();
      expect(msg.warning).not.toHaveBeenCalled();
    },
  );

  it('退出立即失效；同 userId 重新登录也不能接收旧 session 的结果', async () => {
    const a = deferred();
    api.setLoopFollowApi.mockReturnValueOnce(a.promise);
    const follow = useLoopFollow();
    const old = follow.toggle('loop-1');
    useAccessStore().setIsLoggingOut(true);
    expect(follow.isFollowed('loop-1')).toBe(false);
    expect(follow.isBusy('loop-1')).toBe(false);
    expect(follow.followLoadState.value).toBe('idle');
    expect(await refreshFollows()).toBe(false);
    expect(await follow.toggle('loop-2')).toMatchObject({ ok: false });
    expect(api.setLoopFollowApi).toHaveBeenCalledTimes(1);

    setAccount(null);
    useAccessStore().setIsLoggingOut(false);
    setAccount('user-a');
    api.getMyLoopFollowsApi.mockResolvedValueOnce(followPage([], 0));
    await refreshFollows();
    a.resolve(changedFollow(true, 1));
    expect(await old).toMatchObject({ ok: false });
    expect(follow.isFollowed('loop-1')).toBe(false);
    expect(msg.success).not.toHaveBeenCalled();
  });

  it('匿名状态不读取、不写入关注，也不复用上一个账号的内容', async () => {
    api.getMyLoopFollowsApi.mockResolvedValueOnce(
      followPage([['loop-a', 1]], 1),
    );
    const follow = useLoopFollow();
    await follow.ensureLoaded();
    setAccount(null);
    expect(follow.followedLoopIds.value.size).toBe(0);
    expect(await follow.refresh()).toBe(false);
    expect(await follow.toggle('loop-a')).toMatchObject({ ok: false });
    expect(api.getMyLoopFollowsApi).toHaveBeenCalledTimes(1);
    expect(api.setLoopFollowApi).not.toHaveBeenCalled();
  });
});

describe('LoopHeader ★ 关注星标（wb360 页头入口，P6-05）', () => {
  const baseLoop = {
    currentValues: { op: 50, pv: 100, sp: 101, unit: '℃' },
    loopId: 'loop-1',
    tagName: 'TIC-101',
  };

  function mountHeader(props: Record<string, unknown> = {}) {
    return mount(LoopHeader, {
      props: {
        connectionStatus: 'online',
        fitnessLevel: null,
        followBusy: false,
        isFollowed: false,
        lastMessageAt: null,
        // 渲染仅消费 tagName/currentValues；其余字段与断言无关
        loop: baseLoop as any,
        ...props,
      },
    });
  }

  it('未关注渲染 ☆（aria-pressed=false）；已关注渲染实心金色 ★', async () => {
    const w = mountHeader();
    const star = w.find('[data-testid="wb360-follow-star-loop-1"]');
    expect(star.exists()).toBe(true);
    expect(star.text()).toBe('☆');
    expect(star.attributes('aria-pressed')).toBe('false');
    expect(star.classes()).not.toContain('icon-btn--starred');

    await w.setProps({ isFollowed: true });
    expect(star.text()).toBe('★');
    expect(star.attributes('aria-pressed')).toBe('true');
    expect(star.classes()).toContain('icon-btn--starred');
  });

  it('点击星标 emit toggleFollow；在途（followBusy）禁点', async () => {
    const w = mountHeader();
    const star = w.find('[data-testid="wb360-follow-star-loop-1"]');
    await star.trigger('click');
    expect(w.emitted('toggleFollow')).toHaveLength(1);

    await w.setProps({ followBusy: true });
    expect(star.attributes('disabled')).toBeDefined();
  });
});
