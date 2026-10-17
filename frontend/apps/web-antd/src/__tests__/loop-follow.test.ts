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

beforeEach(() => {
  api.getMyLoopFollowsApi.mockReset();
  api.setLoopFollowApi.mockReset();
  msg.error.mockReset();
  msg.success.mockReset();
  msg.warning.mockReset();
  resetLoopFollowState();
});

describe('use-loop-follow 共享关注集（P6-05）', () => {
  it('refreshFollows：分页拉全量（跨页合并）；就绪后重复调用零请求', async () => {
    api.getMyLoopFollowsApi
      .mockResolvedValueOnce(followPage([['loop-1', 3]], 150))
      .mockResolvedValueOnce(
        followPage([['loop-101', 1], ['loop-102', 5]], 150),
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
    api.getMyLoopFollowsApi.mockResolvedValueOnce(followPage([['loop-1', 3]], 1));
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
    api.getMyLoopFollowsApi.mockResolvedValueOnce(followPage([['loop-1', 1]], 1));
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
    api.getMyLoopFollowsApi.mockResolvedValueOnce(followPage([['loop-1', 1]], 1));
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
