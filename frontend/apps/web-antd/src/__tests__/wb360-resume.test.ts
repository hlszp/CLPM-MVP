/**
 * 最近继续接线测试（Slice G → O02-CORE，2026-10-14）
 *
 * 覆盖任务要求：
 * - 对象确认成功加载后调用 PUT /me/work-resume/loop/{id}（正确 kind/id/
 *   context/expectedRevision/generation；存储 context 仅 剖面/窗口/证据引用，
 *   workRef 业务身份不入档）；
 * - 无效/越权/404 对象不得产生最近项（GET/PUT 均不发起）；
 * - 409 携 currentRevision/currentGeneration 刷新重试一次；重试仍失败静默放弃；
 * - 同会话同对象同上下文不重复 PUT；上下文变化后按回收版本再写。
 */
import { flushPromises, mount } from '@vue/test-utils';
import { computed, defineComponent, h, ref } from 'vue';

import { beforeEach, describe, expect, it, vi } from 'vitest';

const { api, pw } = vi.hoisted(() => ({
  api: {
    getLoopDetailApi: vi.fn(),
    getLoopModeMappingApi: vi.fn(),
    getLoopMonitorDetailApi: vi.fn(),
    getLoopMonitorListApi: vi.fn(),
    getLoopTagsApi: vi.fn(),
    getTagDetailApi: vi.fn(),
  },
  pw: {
    getWorkResumeApi: vi.fn(),
    putWorkResumeApi: vi.fn(),
  },
}));

vi.mock('#/api/loop', () => ({
  getLoopDetailApi: (...a: unknown[]) => api.getLoopDetailApi(...a),
  getLoopModeMappingApi: (...a: unknown[]) => api.getLoopModeMappingApi(...a),
  getLoopMonitorDetailApi: (...a: unknown[]) => api.getLoopMonitorDetailApi(...a),
  getLoopMonitorListApi: (...a: unknown[]) => api.getLoopMonitorListApi(...a),
  getLoopTagsApi: (...a: unknown[]) => api.getLoopTagsApi(...a),
}));

vi.mock('#/api/tag', () => ({
  getTagDetailApi: (...a: unknown[]) => api.getTagDetailApi(...a),
}));

vi.mock('#/api/personal-work', () => ({
  extractRevisionConflict: (error: unknown) => {
    const response = (error as any)?.response;
    if (!response || response.status !== 409) return null;
    const body = response.data ?? {};
    const code = String(body.code ?? '');
    if (code !== 'ERR_REVISION_CONFLICT' && code !== 'ERR_GENERATION_STALE') {
      return null;
    }
    const data = body.data ?? {};
    return {
      code,
      currentGeneration:
        typeof data.currentGeneration === 'number'
          ? data.currentGeneration
          : null,
      currentRevision:
        typeof data.currentRevision === 'number' ? data.currentRevision : null,
      message: String(body.message ?? '版本冲突'),
    };
  },
  getWorkResumeApi: (...a: unknown[]) => pw.getWorkResumeApi(...a),
  newRequestId: () => 'req-test',
  putWorkResumeApi: (...a: unknown[]) => pw.putWorkResumeApi(...a),
}));

// mock realtimeWs 单例（避免真实 WS 连接）
vi.mock('#/utils/realtime-ws', () => ({
  LEGACY_INTEREST_OWNER: Symbol('clpm-realtime-legacy-interest'),
  realtimeWs: {
    connect: vi.fn(),
    isConnected: false,
    onConnectionChange: vi.fn(() => () => {}),
    onMessage: vi.fn(() => () => {}),
    registerInterest: vi.fn(),
    status: 'offline',
    unregisterAllInterests: vi.fn(),
    unregisterInterest: vi.fn(),
  },
}));

vi.mock('@vben/stores', () => ({
  useAccessStore: () => ({ accessToken: 'test-token' }),
}));

import type { WorkbenchContext } from '../views/loop/workbench360/workbench-context';

import { useWb360Loop } from '../views/loop/workbench360/composables/use-wb360-loop';
import {
  useWb360Session,
  type Wb360Session,
} from '../views/loop/workbench360/composables/use-wb360-session';

function detailOf(id: string) {
  return {
    aasSyncStatus: { associatedTagCount: 7, lastSyncAt: '2026-10-01' },
    basicInfo: {
      createdAt: '',
      createdBy: '',
      description: `回路${id}`,
      isActive: true,
      loopId: id,
      scoreWeights: {},
      status: 'READY',
      tagName: `TAG-${id}`,
      unitId: 'U1',
      unitName: '单元一',
      updatedAt: '',
      updatedBy: '',
    },
    runtimeParams: {},
    tagMapping: {},
  };
}

function monitorOf(id: string) {
  return {
    currentValues: {
      mode: 1,
      modeLabel: 'Auto',
      op: 50,
      pv: 100,
      pvQuality: 'GOOD',
      readAt: '2026-10-14T00:00:00Z',
      sp: 101,
    },
    kpiStatus: 'SUCCESS',
    kpiSummary: { composite_score: 88 },
    loopId: id,
    loopStatus: 'READY',
    runtimeParams: { controlMode: 'Auto', pidD: 0, pidI: 30, pidP: 1.2 },
    tagName: `TAG-${id}`,
    trend: { mode: [], op: [], pv: [], pvQuality: [], sp: [], timestamps: [] },
  };
}

function conflict409(currentRevision: number, currentGeneration: number) {
  return {
    response: {
      data: {
        code: 'ERR_REVISION_CONFLICT',
        data: { currentGeneration, currentRevision },
        message: '版本冲突',
      },
      status: 409,
    },
  };
}

function mountLoop(context: WorkbenchContext) {
  const ctxRef = ref(context);
  let session!: Wb360Session;
  let loop!: ReturnType<typeof useWb360Loop>;
  const wrapper = mount(
    defineComponent({
      setup() {
        session = useWb360Session(ctxRef);
        loop = useWb360Loop(
          computed(() => ctxRef.value.loopId || null),
          session,
        );
        return () => h('div');
      },
    }),
  );
  return { ctxRef, loop, session, wrapper };
}

describe('最近继续接线（PUT /me/work-resume/loop/{id}）', () => {
  beforeEach(() => {
    for (const fn of Object.values(api)) fn.mockReset();
    for (const fn of Object.values(pw)) fn.mockReset();
    api.getLoopModeMappingApi.mockResolvedValue([]);
    api.getLoopTagsApi.mockResolvedValue({ tags: [] });
    pw.getWorkResumeApi.mockResolvedValue({
      currentGeneration: 7,
      inaccessibleCount: 0,
      items: [],
      readAt: '2026-10-14T00:00:00Z',
      readStatus: 'COMPLETE',
    });
    pw.putWorkResumeApi.mockResolvedValue({
      generation: 7,
      lastVisitedAt: '2026-10-14T00:00:00Z',
      replayed: false,
      resourceId: 'loop-A',
      resourceKind: 'loop',
      revision: 1,
    });
  });

  it('成功加载后调用 PUT：正确 kind/id/context/expectedRevision/generation', async () => {
    api.getLoopDetailApi.mockResolvedValue(detailOf('loop-A'));
    api.getLoopMonitorDetailApi.mockResolvedValue(monitorOf('loop-A'));
    const { wrapper } = mountLoop({
      evidenceRefs: [{ id: 'R1', kind: 'diagnosis_run', version: 'v3' }],
      loopId: 'loop-A',
      section: 'diagnosis',
      window: {
        tsEnd: '2026-10-02T00:00:00.000Z',
        timezone: 'Asia/Shanghai',
        tsStart: '2026-10-01T00:00:00.000Z',
      },
      workRef: { id: 'W1', kind: 'handling_order' },
    });
    await flushPromises();

    // 首次记录先拉取代际/版本镜像
    expect(pw.getWorkResumeApi).toHaveBeenCalledTimes(1);
    expect(pw.getWorkResumeApi).toHaveBeenCalledWith({
      kind: 'loop',
      limit: 100,
    });
    expect(pw.putWorkResumeApi).toHaveBeenCalledTimes(1);
    const [kind, id, body] = pw.putWorkResumeApi.mock.calls[0]!;
    expect(kind).toBe('loop');
    expect(id).toBe('loop-A');
    expect(body.expectedRevision).toBe(0); // 镜像无该资源 → 首次创建期望 0
    expect(body.generation).toBe(7);
    expect(typeof body.requestId).toBe('string');
    // 存储 context 仅 剖面/窗口/证据引用；workRef 业务身份不入档
    expect(body.context).toEqual({
      evidenceRefs: [{ id: 'R1', kind: 'diagnosis_run', version: 'v3' }],
      section: 'diagnosis',
      window: {
        tsEnd: '2026-10-02T00:00:00.000Z',
        timezone: 'Asia/Shanghai',
        tsStart: '2026-10-01T00:00:00.000Z',
      },
    });
    expect(body.context.workRef).toBeUndefined();
    wrapper.unmount();
  });

  it('无效/404 对象：不产生最近项（GET/PUT 均不发起）', async () => {
    api.getLoopDetailApi.mockRejectedValue(new Error('404'));
    api.getLoopMonitorDetailApi.mockRejectedValue(new Error('404'));
    const { loop, wrapper } = mountLoop({ loopId: 'loop-GONE' });
    await flushPromises();

    expect(loop.loadError.value).toContain('回路对象不可用');
    expect(pw.getWorkResumeApi).not.toHaveBeenCalled();
    expect(pw.putWorkResumeApi).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it('409：按响应携 currentRevision/currentGeneration 刷新重试一次成功', async () => {
    api.getLoopDetailApi.mockResolvedValue(detailOf('loop-A'));
    api.getLoopMonitorDetailApi.mockResolvedValue(monitorOf('loop-A'));
    pw.putWorkResumeApi
      .mockRejectedValueOnce(conflict409(3, 8))
      .mockResolvedValueOnce({
        generation: 8,
        lastVisitedAt: '2026-10-14T00:00:00Z',
        replayed: false,
        resourceId: 'loop-A',
        resourceKind: 'loop',
        revision: 4,
      });
    pw.getWorkResumeApi
      .mockResolvedValueOnce({
        currentGeneration: 7,
        inaccessibleCount: 0,
        items: [],
        readAt: '2026-10-14T00:00:00Z',
        readStatus: 'COMPLETE',
      })
      .mockResolvedValueOnce({
        currentGeneration: 8,
        inaccessibleCount: 0,
        items: [
          {
            context: {},
            generation: 8,
            lastVisitedAt: '2026-10-13T00:00:00Z',
            resourceId: 'loop-A',
            resourceKind: 'loop',
            revision: 3,
          },
        ],
        readAt: '2026-10-14T00:01:00Z',
        readStatus: 'COMPLETE',
      });

    const { wrapper } = mountLoop({ loopId: 'loop-A' });
    await flushPromises();

    expect(pw.putWorkResumeApi).toHaveBeenCalledTimes(2);
    expect(pw.getWorkResumeApi).toHaveBeenCalledTimes(2); // 首次镜像 + 409 后刷新
    const retryBody = pw.putWorkResumeApi.mock.calls[1]![2];
    expect(retryBody.expectedRevision).toBe(3); // 409 携 currentRevision
    expect(retryBody.generation).toBe(8); // 409 携 currentGeneration
    wrapper.unmount();
  });

  it('409 重试仍失败：静默放弃（不阻塞、不显示已保存；恰好 2 次 PUT）', async () => {
    api.getLoopDetailApi.mockResolvedValue(detailOf('loop-A'));
    api.getLoopMonitorDetailApi.mockResolvedValue(monitorOf('loop-A'));
    pw.putWorkResumeApi
      .mockRejectedValueOnce(conflict409(3, 8))
      .mockRejectedValueOnce(conflict409(5, 9));

    const { loop, wrapper } = mountLoop({ loopId: 'loop-A' });
    await flushPromises();

    // 作业面不受影响（对象正常呈现），PUT 恰好 2 次后放弃
    expect(loop.current.value?.loopId).toBe('loop-A');
    expect(pw.putWorkResumeApi).toHaveBeenCalledTimes(2);
    wrapper.unmount();
  });

  it('同会话同对象同上下文不重复 PUT；上下文变化后按回收版本再写', async () => {
    api.getLoopDetailApi.mockResolvedValue(detailOf('loop-A'));
    api.getLoopMonitorDetailApi.mockResolvedValue(monitorOf('loop-A'));
    const { ctxRef, session, wrapper } = mountLoop({ loopId: 'loop-A' });
    await flushPromises();
    expect(pw.putWorkResumeApi).toHaveBeenCalledTimes(1);

    // 同对象同上下文再次记录：幂等跳过
    await session.recordResume();
    expect(pw.putWorkResumeApi).toHaveBeenCalledTimes(1);

    // 上下文变化（同回路换窗口）：再次写入，expectedRevision 回收自上次写响应
    ctxRef.value = {
      loopId: 'loop-A',
      window: {
        tsEnd: '2026-10-03T00:00:00.000Z',
        timezone: 'Asia/Shanghai',
        tsStart: '2026-10-02T00:00:00.000Z',
      },
    };
    await session.recordResume();
    expect(pw.putWorkResumeApi).toHaveBeenCalledTimes(2);
    const second = pw.putWorkResumeApi.mock.calls[1]![2];
    expect(second.expectedRevision).toBe(1); // 首次写响应 revision=1 回收进镜像
    expect(second.context.window?.tsStart).toBe('2026-10-02T00:00:00.000Z');
    wrapper.unmount();
  });
});
