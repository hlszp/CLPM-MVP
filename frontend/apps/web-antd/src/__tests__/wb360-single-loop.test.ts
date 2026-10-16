/**
 * 单回路对象上下文测试（P6-02a O02-CORE）
 *
 * 覆盖 01a §4.2/§4.3 与完成判据：
 * - single 模式无全目录请求：仅 getLoopDetailApi / getLoopMonitorDetailApi /
 *   getLoopModeMappingApi / getLoopTagsApi（+getTagDetailApi 量程），
 *   getLoopMonitorListApi 零调用；
 * - 对象不存在/删除/越权：loadError 明确原因，current=null，不回退任何回路；
 * - monitor 硬角色拒绝（SPONSOR 403）：monitorError 如实显示，基本对象仍呈现；
 * - 迟到响应（A→B 切换）：量程/对象读取落地前核对会话身份+代次，A 数据不串写 B。
 */

import { flushPromises, mount } from '@vue/test-utils';
import { computed, defineComponent, h, ref } from 'vue';

import { beforeEach, describe, expect, it, vi } from 'vitest';

const { api } = vi.hoisted(() => ({
  api: {
    getLoopDetailApi: vi.fn(),
    getLoopModeMappingApi: vi.fn(),
    getLoopMonitorDetailApi: vi.fn(),
    getLoopMonitorListApi: vi.fn(),
    getLoopTagsApi: vi.fn(),
    getTagDetailApi: vi.fn(),
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

// mock 最近继续 API（本文件不覆盖 resume 行为；recordResume 静默成功即可）
vi.mock('#/api/personal-work', () => ({
  extractRevisionConflict: () => null,
  getWorkResumeApi: vi.fn(async () => ({
    currentGeneration: 1,
    inaccessibleCount: 0,
    items: [],
    readAt: '',
    readStatus: 'COMPLETE',
  })),
  newRequestId: () => 'req-test',
  putWorkResumeApi: vi.fn(async () => ({
    generation: 1,
    lastVisitedAt: '',
    replayed: false,
    resourceId: 'x',
    resourceKind: 'loop',
    revision: 1,
  })),
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
    kpiSummary: {
      accuracy_rate: 90,
      algorithm_version: 'v1',
      auto_mode_rate: 95,
      calculatedAt: '2026-10-14T00:00:00Z',
      composite_score: 88,
      effective_auto_rate: 90,
      fast_rate: 85,
      good_value_rate: 99,
      oscillation_rate: 5,
      saturation_rate: 1,
      status: 'SUCCESS',
      steady_rate: 92,
    },
    loopId: id,
    loopStatus: 'READY',
    runtimeParams: { controlMode: 'Auto', pidD: 0, pidI: 30, pidP: 1.2 },
    tagName: `TAG-${id}`,
    trend: {
      mode: [],
      op: [],
      pv: [],
      pvQuality: [],
      sp: [],
      timestamps: [],
    },
  };
}

function tagsOf() {
  return {
    loopId: 'x',
    status: 'READY',
    tagName: 'x',
    tags: [
      { associated: true, currentValue: null, description: null, quality: null, lastSyncAt: null, required: true, role: 'PV', tagId: 'pv-tag', tagName: 'T_PV' },
      { associated: true, currentValue: null, description: null, quality: null, lastSyncAt: null, required: true, role: 'OP', tagId: 'op-tag', tagName: 'T_OP' },
    ],
  };
}

function mountLoop(initial: string) {
  const ctxRef = ref<WorkbenchContext>({ loopId: initial });
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

/** 手工控制的迟到 Promise（TS 可控赋值，避免闭包赋值窄化问题） */
function deferred<T>() {
  let resolve!: (v: PromiseLike<T> | T) => void;
  const promise = new Promise<T>((res) => (resolve = res));
  return { promise, resolve };
}

describe('useWb360Loop 单对象读取', () => {
  beforeEach(() => {
    for (const fn of Object.values(api)) fn.mockReset();
    api.getLoopModeMappingApi.mockResolvedValue([
      { isAuto: true, isEffective: true, modeLabel: 'AUTO', modeValue: 1 },
    ]);
    api.getLoopTagsApi.mockResolvedValue(tagsOf());
    api.getTagDetailApi.mockImplementation(async (tagId: string) =>
      tagId === 'pv-tag'
        ? { rangeMax: 200, rangeMin: 0, unit: '℃' }
        : { rangeMax: 100, rangeMin: 0, unit: '%' },
    );
  });

  it('仅单对象四接口（+量程 tag），全厂 monitor 列表零调用', async () => {
    api.getLoopDetailApi.mockResolvedValue(detailOf('loop-A'));
    api.getLoopMonitorDetailApi.mockResolvedValue(monitorOf('loop-A'));
    const { loop, wrapper } = mountLoop('loop-A');
    await flushPromises();

    expect(loop.current.value?.loopId).toBe('loop-A');
    expect(loop.current.value?.tagName).toBe('TAG-loop-A');
    expect(loop.current.value?.score).toBe(88);
    expect(loop.current.value?.modeMapping).toEqual({ '1': 'AUTO' });
    expect(loop.loadError.value).toBeNull();
    expect(loop.monitorError.value).toBeNull();
    // 量程真实消费（PV 满量程/单位、OP 右轴量程）
    expect(loop.ranges.value.pvRange).toEqual({ hi: 200, lo: 0 });
    expect(loop.ranges.value.pvUnit).toBe('℃');
    expect(loop.ranges.value.opRange).toEqual({ hi: 100, lo: 0 });

    expect(api.getLoopDetailApi).toHaveBeenCalledTimes(1);
    expect(api.getLoopMonitorDetailApi).toHaveBeenCalledTimes(1);
    expect(api.getLoopModeMappingApi).toHaveBeenCalledTimes(1);
    expect(api.getLoopTagsApi).toHaveBeenCalledTimes(1);
    // 关键：single 模式禁止拉全厂 monitor 列表
    expect(api.getLoopMonitorListApi).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it('对象不存在/越权：loadError 明确原因，不回退任何回路', async () => {
    api.getLoopDetailApi.mockRejectedValue(
      new Error('Request failed with status code 404'),
    );
    api.getLoopMonitorDetailApi.mockRejectedValue(
      new Error('Request failed with status code 404'),
    );
    const { loop, wrapper } = mountLoop('loop-GONE');
    await flushPromises();

    expect(loop.current.value).toBeNull();
    expect(loop.loadError.value).toContain('回路对象不可用');
    expect(loop.loadError.value).toContain('404');
    // 不回退：没有发起任何"第一条/清单"请求
    expect(api.getLoopMonitorListApi).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it('SPONSOR 等 monitor 硬角色拒绝：monitorError 如实显示，基本对象仍呈现', async () => {
    api.getLoopDetailApi.mockResolvedValue(detailOf('loop-A'));
    api.getLoopMonitorDetailApi.mockRejectedValue(
      new Error('Request failed with status code 403'),
    );
    const { loop, wrapper } = mountLoop('loop-A');
    await flushPromises();

    expect(loop.current.value?.loopId).toBe('loop-A');
    expect(loop.current.value?.tagName).toBe('TAG-loop-A');
    expect(loop.monitorError.value).toContain('监控数据不可用');
    expect(loop.monitorError.value).toContain('403');
    expect(loop.loadError.value).toBeNull();
    // 实时值诚实占位（全 null，不编造读数）
    expect(loop.current.value?.currentValues.pv).toBeNull();
    expect(loop.current.value?.currentValues.modeLabel).toBeNull();
    wrapper.unmount();
  });

  it('A→B 切换：A 的迟到对象响应核对身份+代次后丢弃，不串写 B', async () => {
    const dA = deferred<unknown>();
    api.getLoopDetailApi.mockImplementation((id: string) =>
      id === 'loop-A' ? dA.promise : Promise.resolve(detailOf(id)),
    );
    api.getLoopMonitorDetailApi.mockImplementation(async (id: string) =>
      monitorOf(id),
    );

    const { ctxRef, loop, wrapper } = mountLoop('loop-A');
    await flushPromises(); // B 尚未切换；A 的 detail 仍在途
    expect(loop.current.value).toBeNull();

    ctxRef.value = { loopId: 'loop-B' };
    await flushPromises();
    expect(loop.current.value?.loopId).toBe('loop-B');
    expect(loop.current.value?.tagName).toBe('TAG-loop-B');

    // A 的迟到响应此时才落地：必须被丢弃（身份+代次双重不符）
    dA.resolve(detailOf('loop-A'));
    await flushPromises();
    expect(loop.current.value?.loopId).toBe('loop-B');
    expect(loop.current.value?.tagName).toBe('TAG-loop-B');
    expect(api.getLoopDetailApi).toHaveBeenCalledTimes(2);
    expect(api.getLoopMonitorListApi).not.toHaveBeenCalled();
    wrapper.unmount();
  });

  it('A→B 切换：A 的迟到量程响应同样丢弃（ranges 不串写）', async () => {
    const dTags = deferred<unknown>();
    api.getLoopDetailApi.mockImplementation(async (id: string) => detailOf(id));
    api.getLoopMonitorDetailApi.mockImplementation(async (id: string) =>
      monitorOf(id),
    );
    api.getLoopTagsApi.mockImplementation((id: string) =>
      id === 'loop-A' ? dTags.promise : Promise.resolve(tagsOf()),
    );

    const { ctxRef, loop, wrapper } = mountLoop('loop-A');
    await flushPromises();
    expect(loop.current.value?.loopId).toBe('loop-A');
    expect(loop.ranges.value.pvRange).toBeNull(); // A 量程在途

    ctxRef.value = { loopId: 'loop-B' };
    await flushPromises();
    expect(loop.current.value?.loopId).toBe('loop-B');
    // B 量程已落地
    expect(loop.ranges.value.pvRange).toEqual({ hi: 200, lo: 0 });

    // A 的迟到量程落地：不得覆盖 B 的量程
    dTags.resolve(tagsOf());
    await flushPromises();
    expect(loop.current.value?.loopId).toBe('loop-B');
    expect(loop.ranges.value.pvRange).toEqual({ hi: 200, lo: 0 });
    wrapper.unmount();
  });
});
