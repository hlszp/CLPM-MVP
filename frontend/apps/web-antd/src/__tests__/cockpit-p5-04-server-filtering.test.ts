/**
 * P5-04 服务端筛选/竞态/实时节流前端单元测试（03 号文 §8 P5-04 卡）
 *
 * - use-loop-wall-list：筛选/排序/分页参数全量下发服务端（grades/modes csv、
 *   scoreDelta 劣化排序、pageSize=30、focusLoopId）——"无需多页预抓"的
 *   参数面证据（网络面板证据属浏览器验收，此处锁请求形状）；
 * - A→B 竞态反例：装置 A 慢响应迟到，B 已返回后 A 不得覆盖（use-latest-request
 *   守卫，复用不分叉）；
 * - 静默刷新失败保留旧数据（诚实化：不静默清空冒充空结果）；
 * - create-realtime-merge-buffer：短时合并（窗口内同位号仅最新值）、
 *   后台页停更（隐藏不应用）、恢复可见冲刷、dispose 清理；
 * - block-state-overlay：三态渲染（OK/缺省不渲染，UNAVAILABLE 显示
 *   "该来源暂不可用"+重试按钮）。
 */
import type { Mock } from 'vitest';

import type { LoopApi } from '#/api/loop';

import { flushPromises, mount } from '@vue/test-utils';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import BlockStateOverlay from '../views/cockpit/components/block-state-overlay.vue';
import { createRealtimeMergeBuffer } from '../views/cockpit/composables/create-realtime-merge-buffer';
import {
  LOOP_WALL_PAGE_SIZE,
  loopWallRequestKey,
  toServerSort,
  useLoopWallList,
} from '../views/cockpit/composables/use-loop-wall-list';

// mock 列表 API（避免真实请求；断言请求参数形状）
vi.mock('#/api/loop', () => ({
  getLoopMonitorListApi: vi.fn(),
}));

// mock use-latest-request 的 onBeforeUnmount（composable 在无组件宿主时使用）
vi.mock('vue', async (importOriginal) => {
  const vue = await importOriginal<typeof import('vue')>();
  return { ...vue, onBeforeUnmount: vi.fn() };
});

import { getLoopMonitorListApi } from '#/api/loop';

const apiMock = getLoopMonitorListApi as unknown as Mock;

function makeItem(loopId: string, tagName: string): LoopApi.MonitorListItem {
  return {
    loopId,
    tagName,
    description: '',
    unitName: '',
    currentValues: {
      mode: null,
      modeLabel: null,
      op: null,
      pv: null,
      pvQuality: null,
      readAt: null,
      sp: null,
    },
    controlMode: null,
    score: null,
    loopStatus: 'READY',
    isActive: true,
    readAt: null,
  } as unknown as LoopApi.MonitorListItem;
}

function makeResult(
  ids: string[],
  extra: Partial<LoopApi.MonitorListResult> = {},
): LoopApi.MonitorListResult {
  return {
    view: 'card',
    items: ids.map((id, i) => makeItem(id, `TAG-${i}`)),
    total: ids.length,
    page: 1,
    pageSize: LOOP_WALL_PAGE_SIZE,
    aggregate: null,
    focusIndex: null,
    focusPage: null,
    ...extra,
  };
}

const BASE_QUERY = {
  grades: [],
  modes: [],
  page: 1,
  plantNodeId: null,
  sortBy: 'scoreDesc' as const,
};

beforeEach(() => {
  apiMock.mockReset();
});

afterEach(() => {
  vi.useRealTimers();
});

// ===========================================================================
// 参数映射（服务端排序契约）
// ===========================================================================

describe('P5-04 toServerSort（劣化排序 = scoreDelta 升序）', () => {
  it('scoreDesc → score/desc；degradeDesc → scoreDelta/asc', () => {
    expect(toServerSort('scoreDesc')).toEqual({
      sortBy: 'score',
      sortOrder: 'desc',
    });
    expect(toServerSort('degradeDesc')).toEqual({
      sortBy: 'scoreDelta',
      sortOrder: 'asc',
    });
  });
});

describe('P5-04 loopWallRequestKey（请求键规范化）', () => {
  it('筛选数组顺序无关；页码/节点/排序参与键', () => {
    expect(
      loopWallRequestKey({ ...BASE_QUERY, grades: ['GOOD', 'POOR'] }),
    ).toBe(loopWallRequestKey({ ...BASE_QUERY, grades: ['POOR', 'GOOD'] }));
    expect(loopWallRequestKey({ ...BASE_QUERY, page: 2 })).not.toBe(
      loopWallRequestKey({ ...BASE_QUERY, page: 1 }),
    );
  });
});

// ===========================================================================
// useLoopWallList（服务端筛选 + 竞态 + 刷新保数据）
// ===========================================================================

describe('P5-04 useLoopWallList（服务端分页列表）', () => {
  it('筛选/排序/分页参数全量下发服务端（无需客户端多页预抓）', async () => {
    apiMock.mockResolvedValue(makeResult(['a']));
    const wall = useLoopWallList();
    await wall.load(
      {
        grades: ['GOOD', 'POOR'],
        modes: ['AUTO', 'CAS'],
        page: 3,
        plantNodeId: 'node-1',
        sortBy: 'degradeDesc',
      },
      { focusLoopId: 'sel-1' },
    );
    expect(apiMock).toHaveBeenCalledTimes(1);
    expect(apiMock).toHaveBeenCalledWith(
      expect.objectContaining({
        page: 3,
        pageSize: LOOP_WALL_PAGE_SIZE,
        plantNodeId: 'node-1',
        sortBy: 'scoreDelta',
        sortOrder: 'asc',
        grades: 'GOOD,POOR',
        modes: 'AUTO,CAS',
        focusLoopId: 'sel-1',
      }),
    );
  });

  it('空筛选不下发 grades/modes（默认浏览态零额外参数）', async () => {
    apiMock.mockResolvedValue(makeResult(['a']));
    const wall = useLoopWallList();
    await wall.load(BASE_QUERY);
    const params = apiMock.mock.calls[0]?.[0];
    expect(params.grades).toBeUndefined();
    expect(params.modes).toBeUndefined();
    expect(params.focusLoopId).toBeUndefined();
  });

  it('A→B 竞态反例：装置 A 的慢响应迟到，不得覆盖 B 的数据', async () => {
    let resolveA: (v: LoopApi.MonitorListResult) => void = () => {};
    let resolveB: (v: LoopApi.MonitorListResult) => void = () => {};
    apiMock.mockImplementationOnce(
      () =>
        new Promise((r) => {
          resolveA = r as typeof resolveA;
        }),
    );
    apiMock.mockImplementationOnce(
      () =>
        new Promise((r) => {
          resolveB = r as typeof resolveB;
        }),
    );

    const wall = useLoopWallList();
    const pA = wall.load({ ...BASE_QUERY, plantNodeId: 'A' });
    const pB = wall.load({ ...BASE_QUERY, plantNodeId: 'B' });
    await flushPromises();

    // B 先返回（装置 B 的数据落位）
    resolveB(makeResult(['b-1', 'b-2'], { total: 2 }));
    await pB;
    expect(wall.items.value.map((i) => i.loopId)).toEqual(['b-1', 'b-2']);
    expect(wall.total.value).toBe(2);

    // A 的慢响应此刻才迟到——必须被守卫丢弃（不得覆盖 B）
    resolveA(makeResult(['a-1'], { total: 1 }));
    await pA;
    expect(wall.items.value.map((i) => i.loopId)).toEqual(['b-1', 'b-2']);
    expect(wall.total.value).toBe(2);
  });

  it('静默刷新失败保留旧页数据（不静默清空冒充空结果）', async () => {
    apiMock.mockResolvedValueOnce(makeResult(['x-1'], { total: 1 }));
    const wall = useLoopWallList();
    await wall.load(BASE_QUERY);
    expect(wall.total.value).toBe(1);

    apiMock.mockRejectedValueOnce(new Error('network down'));
    await wall.load(BASE_QUERY, { silent: true });
    expect(wall.items.value.map((i) => i.loopId)).toEqual(['x-1']);
    expect(wall.total.value).toBe(1);
  });

  it('非静默失败显式空态（总数 0）+ focusPage 透传', async () => {
    apiMock.mockResolvedValueOnce(
      makeResult(['f-1'], { total: 61, focusIndex: 60, focusPage: 3 }),
    );
    const wall = useLoopWallList();
    await wall.load(BASE_QUERY, { focusLoopId: 'f-1' });
    expect(wall.total.value).toBe(61);
    expect(wall.focusPage.value).toBe(3);

    apiMock.mockRejectedValueOnce(new Error('boom'));
    await wall.load(BASE_QUERY);
    expect(wall.items.value).toEqual([]);
    expect(wall.total.value).toBe(0);
    expect(wall.focusPage.value).toBeNull();
  });
});

// ===========================================================================
// createRealtimeMergeBuffer（短时合并 + 后台停更）
// ===========================================================================

describe('P5-04 createRealtimeMergeBuffer（实时短时合并）', () => {
  it('窗口内同位号只保留最新值，窗口到期批量应用一次', async () => {
    vi.useFakeTimers();
    const applied: { tagCode: string; value: string }[][] = [];
    const buf = createRealtimeMergeBuffer<{ tagCode: string; value: string }>(
      (msgs) => applied.push(msgs),
      { windowMs: 500 },
    );
    buf.push({ tagCode: 'T1.PV', value: '1' });
    // eslint-disable-next-line unicorn/prefer-single-call -- push 类型为单参数（窗口去重逐次语义），不可合并
    buf.push({ tagCode: 'T1.PV', value: '2' }); // 同位号覆盖
    // eslint-disable-next-line unicorn/prefer-single-call -- 同上，逐次入窗
    buf.push({ tagCode: 'T2.PV', value: '9' });
    expect(applied).toEqual([]); // 窗口内未应用
    vi.advanceTimersByTime(500);
    expect(applied).toEqual([
      [
        { tagCode: 'T1.PV', value: '2' },
        { tagCode: 'T2.PV', value: '9' },
      ],
    ]);
    buf.dispose();
  });

  it('后台页（hidden）窗口到期不应用；恢复可见 flush 立即冲刷最新值', async () => {
    vi.useFakeTimers();
    let hidden = true;
    const applied: string[] = [];
    const buf = createRealtimeMergeBuffer<{ tagCode: string; value: string }>(
      (msgs) => applied.push(...msgs.map((m) => m.value)),
      { windowMs: 500, isHidden: () => hidden },
    );
    buf.push({ tagCode: 'T1.PV', value: '1' });
    vi.advanceTimersByTime(1000);
    expect(applied).toEqual([]); // 隐藏中停止高频更新

    buf.push({ tagCode: 'T1.PV', value: '3' }); // 隐藏期间仍缓冲最新值
    hidden = false;
    buf.flush(); // 恢复可见立即冲刷
    expect(applied).toEqual(['3']);
    buf.dispose();
  });

  it('dispose 清定时器：此后不再应用', async () => {
    vi.useFakeTimers();
    const applied: string[] = [];
    const buf = createRealtimeMergeBuffer<{ tagCode: string; value: string }>(
      (msgs) => applied.push(...msgs.map((m) => m.value)),
      { windowMs: 500 },
    );
    buf.push({ tagCode: 'T1.PV', value: '1' });
    buf.dispose();
    vi.advanceTimersByTime(1000);
    expect(applied).toEqual([]);
  });
});

// ===========================================================================
// BlockStateOverlay（block_states 三态渲染）
// ===========================================================================

describe('P5-04 BlockStateOverlay（OK/UNAVAILABLE 三态）', () => {
  it('OK 与缺省（旧响应兼容）不渲染任何内容', () => {
    for (const state of [undefined, null, 'OK'] as const) {
      const wrapper = mount(BlockStateOverlay, { props: { state } });
      expect(wrapper.find('.block-state').exists()).toBe(false);
    }
  });

  it('UNAVAILABLE 显示"该来源暂不可用"+ 重试按钮，点击发出 retry', async () => {
    const wrapper = mount(BlockStateOverlay, {
      props: { state: 'UNAVAILABLE' },
    });
    expect(wrapper.text()).toContain('该来源暂不可用');
    const btn = wrapper.find('.block-state__retry');
    expect(btn.exists()).toBe(true);
    await btn.trigger('click');
    expect(wrapper.emitted('retry')).toHaveLength(1);
  });
});
