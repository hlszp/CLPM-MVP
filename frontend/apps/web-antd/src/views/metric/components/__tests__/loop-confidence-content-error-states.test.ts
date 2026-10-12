import type { LoopConfidenceLatestItem } from '#/api/metric';

/**
 * G3-R03（14 号文 §4）：可信度详情内容——latest 概要与评估历史两个
 * 数据域各自四态（loading/成功空/成功有值/error）+ 竞态守卫
 *
 * 覆盖验收反例（与 loop-performance-detail-error-states.test.ts 共同
 * 覆盖"两个历史入口均验"）：
 * - HTTP 成功返回 0 条 → 显示确无记录（成功空 ≠ 失败）
 * - 422/500/超时 → 显式失败原因 + 重试，不冒充空态
 * - 403 → 权限提示；切换回路后旧回路数据（概要+历史）不泄漏
 * - 重试成功恢复（latest 与历史各自）
 * - 第二页失败：旧页数据与页码保持对应（"旧数据+更新失败"标注），重试重发失败页
 * - A 慢 B 快切换只显示 B；组件关闭后迟到返回不污染
 * - latest 概要错误时可独立重试用，但不替代历史成功（独立失败域）
 */
import { flushPromises, mount } from '@vue/test-utils';

import { Table } from 'ant-design-vue';
import { beforeEach, describe, expect, it, vi } from 'vitest';

// oxlint-disable-next-line import/first -- Vitest mocks must be registered before these imports
import LoopConfidenceContent from '#/views/metric/components/loop-confidence-content.vue';

const { getLoopConfidenceLatestApi, getLoopSnapshotsApi } = vi.hoisted(() => ({
  getLoopConfidenceLatestApi: vi.fn(),
  getLoopSnapshotsApi: vi.fn(),
}));

vi.mock('#/api/metric', () => ({
  getLoopConfidenceLatestApi: (...args: any[]) =>
    getLoopConfidenceLatestApi(...args),
  getLoopSnapshotsApi: (...args: any[]) => getLoopSnapshotsApi(...args),
}));

/** 模拟 axios HTTP 错误形状（requestClient 抛出链：response.status/data.message） */
function httpError(status: number, message = ''): Error {
  const error = new Error(`Request failed with status code ${status}`);
  (error as any).response = { status, data: { message } };
  return error;
}

/** 模拟 axios 超时（无 response，code=ECONNABORTED） */
function timeoutError(): Error {
  const error = new Error('timeout of 30000ms exceeded');
  (error as any).code = 'ECONNABORTED';
  return error;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, reject, resolve };
}

function latestDetail(score = 88): LoopConfidenceLatestItem {
  return {
    loopId: 'loop-x',
    evalTime: '2026-10-12T08:00:00Z',
    dataTsStart: '2026-10-12T07:00:00Z',
    dataTsEnd: '2026-10-12T08:00:00Z',
    status: 'SUCCESS',
    score,
    confidenceLevel: 'A',
    validRate: 0.98,
    metrics: {},
    algorithmVersion: 'KPI_CALC_v3.0-rc1',
    updatedAt: null,
  } as unknown as LoopConfidenceLatestItem;
}

function snapRow(score: number) {
  return {
    tsStart: `2026-10-12T0${score % 9}:00:00Z`,
    tsEnd: `2026-10-12T0${score % 9}:59:59Z`,
    score,
    status: 'SUCCESS',
    confidenceLevel: 'B',
    algorithmVersion: 'KPI_CALC_v3.0-rc1',
  };
}

function mountConfidence(loopId = 'loop-A') {
  return mount(LoopConfidenceContent, { props: { loopId } });
}

async function clickRetry(
  wrapper: ReturnType<typeof mount>,
  keyword = '重新加载',
) {
  const btn = wrapper
    .findAll('button')
    .find((b) => b.text().includes(keyword));
  expect(btn, '应渲染重试按钮').toBeTruthy();
  await btn!.trigger('click');
}

/** 历史分页表 = 组件内第 2 个 Table（第 1 个为 12 子指标表） */
function historyTable(wrapper: ReturnType<typeof mount>) {
  const tables = wrapper.findAllComponents(Table);
  expect(tables.length).toBe(2);
  return tables[1]!;
}

beforeEach(() => {
  getLoopConfidenceLatestApi.mockReset();
  getLoopSnapshotsApi.mockReset();
});

describe('G3-R03 loop-confidence-content 历史分页四态', () => {
  it('HTTP 成功返回 0 条：latest 空态 + 历史空态，不显示任何失败提示', async () => {
    getLoopConfidenceLatestApi.mockResolvedValue(null); // HTTP 200 data=null
    getLoopSnapshotsApi.mockResolvedValue({ items: [], total: 0 });
    const wrapper = mountConfidence();
    await flushPromises();

    const text = wrapper.text();
    expect(text).toContain('暂无评估记录');
    expect(text).toContain('该回路暂无历史评估记录');
    expect(text).not.toContain('加载失败');
  });

  it.each([
    ['422', httpError(422, 'loopId 无效'), '请求被拒绝（422）：loopId 无效'],
    ['500', httpError(500), '服务异常（500）'],
    ['超时', timeoutError(), '请求超时'],
  ])(
    '历史 %s 失败：显式原因 + 重试，不冒充"暂无历史评估记录"',
    async (_name, error, reason) => {
      getLoopConfidenceLatestApi.mockResolvedValue(latestDetail());
      getLoopSnapshotsApi.mockRejectedValue(error);
      const wrapper = mountConfidence();
      await flushPromises();

      const text = wrapper.text();
      expect(text).toContain(`评估历史加载失败：${reason}`);
      expect(text).toContain('重新加载');
      // 失败不是"没有历史"：空态文案不出现，emptyText 走错误分支
      expect(text).not.toContain('该回路暂无历史评估记录');
      expect(text).toContain('历史加载失败，无法确认该回路是否有评估记录');
    },
  );

  it('latest 500 失败：概要显式错误 + 重试；历史成功仍可用（独立失败域）', async () => {
    getLoopConfidenceLatestApi.mockRejectedValue(httpError(500));
    getLoopSnapshotsApi.mockResolvedValue({
      items: [snapRow(95)],
      total: 1,
    });
    const wrapper = mountConfidence();
    await flushPromises();

    const text = wrapper.text();
    expect(text).toContain('评估概要加载失败：服务异常（500）');
    expect(text).toContain('重新加载');
    // latest 失败不冒充"暂无评估记录"空态
    expect(text).not.toContain('暂无评估记录。');
    // 历史成功不被 latest 失败替代（14 号文：latest 概要错误可独立用但不替代历史成功）
    expect(text).toContain('95.00');
    expect(text).not.toContain('评估历史加载失败');
  });

  it('latest 成功 + 历史失败：概要正常展示，历史显式报错（历史失败不阻塞概要）', async () => {
    getLoopConfidenceLatestApi.mockResolvedValue(latestDetail(88));
    getLoopSnapshotsApi.mockRejectedValue(httpError(503));
    const wrapper = mountConfidence();
    await flushPromises();

    const text = wrapper.text();
    expect(text).toContain('评估概要（最新一条 latest）');
    expect(text).toContain('88.00');
    expect(text).toContain('评估历史加载失败：服务异常（503）');
  });

  it('403：latest 与历史均为权限提示，不显示空态冒充', async () => {
    getLoopConfidenceLatestApi.mockRejectedValue(httpError(403));
    getLoopSnapshotsApi.mockRejectedValue(httpError(403));
    const wrapper = mountConfidence();
    await flushPromises();

    const text = wrapper.text();
    expect(text).toContain('评估概要加载失败：无权限访问（403）');
    expect(text).toContain('评估历史加载失败：无权限访问（403）');
    expect(text).not.toContain('暂无评估记录。');
    expect(text).not.toContain('该回路暂无历史评估记录');
  });

  it('403 切换回路：旧回路概要与历史数据不泄漏（越权旧详情不保留）', async () => {
    getLoopConfidenceLatestApi.mockResolvedValueOnce(latestDetail(63));
    getLoopSnapshotsApi.mockResolvedValueOnce({
      items: [snapRow(63)],
      total: 1,
    });
    const wrapper = mountConfidence('loop-A');
    await flushPromises();
    expect(wrapper.text()).toContain('63.00');

    getLoopConfidenceLatestApi.mockRejectedValueOnce(httpError(403));
    getLoopSnapshotsApi.mockRejectedValueOnce(httpError(403));
    await wrapper.setProps({ loopId: 'loop-B' });
    await flushPromises();

    const text = wrapper.text();
    expect(text).toContain('评估概要加载失败：无权限访问（403）');
    expect(text).toContain('评估历史加载失败：无权限访问（403）');
    expect(text).not.toContain('63.00');
  });

  it('latest 重试成功恢复：错误消失、概要渲染', async () => {
    getLoopConfidenceLatestApi.mockRejectedValueOnce(httpError(500));
    getLoopSnapshotsApi.mockResolvedValue({ items: [snapRow(95)], total: 1 });
    const wrapper = mountConfidence();
    await flushPromises();
    expect(wrapper.text()).toContain('评估概要加载失败');

    getLoopConfidenceLatestApi.mockResolvedValueOnce(latestDetail(88));
    await clickRetry(wrapper);
    await flushPromises();

    const text = wrapper.text();
    expect(text).toContain('评估概要（最新一条 latest）');
    expect(text).toContain('88.00');
    expect(text).not.toContain('评估概要加载失败');
  });

  it('第二页失败：保留第 1 页数据与页码对应 + "旧数据"标注；重试重发失败页并恢复', async () => {
    getLoopConfidenceLatestApi.mockResolvedValue(latestDetail());
    getLoopSnapshotsApi.mockResolvedValueOnce({
      items: [snapRow(95)],
      total: 15,
    });
    const wrapper = mountConfidence();
    await flushPromises();
    expect(wrapper.text()).toContain('95.00');

    // 翻到第 2 页失败
    getLoopSnapshotsApi.mockRejectedValueOnce(httpError(500));
    historyTable(wrapper).vm.$emit('change', { current: 2 });
    await flushPromises();

    let text = wrapper.text();
    // 旧页数据保留 + 页码未推进的显式标注（分页失败不把旧页标成新页）
    expect(text).toContain('95.00');
    expect(text).toContain('评估历史加载失败：服务异常（500）');
    expect(text).toContain('当前仍展示旧数据（第 1 页），页码未推进');
    expect(getLoopSnapshotsApi).toHaveBeenLastCalledWith(
      expect.objectContaining({ page: 2 }),
    );

    // 重试：重发失败的页（page=2）并成功
    getLoopSnapshotsApi.mockResolvedValueOnce({
      items: [snapRow(42)],
      total: 15,
    });
    await clickRetry(wrapper);
    await flushPromises();

    text = wrapper.text();
    expect(getLoopSnapshotsApi).toHaveBeenLastCalledWith(
      expect.objectContaining({ page: 2 }),
    );
    expect(text).toContain('42.00');
    expect(text).not.toContain('95.00');
    expect(text).not.toContain('加载失败');
    expect(text).not.toContain('当前仍展示旧数据');
  });

  it('A 慢 B 快切换：只显示 B 的概要与历史，A 迟到响应被丢弃', async () => {
    const aLatest = deferred<LoopConfidenceLatestItem | null>();
    const aSnap = deferred<{ items: any[]; total: number }>();
    getLoopConfidenceLatestApi.mockReturnValueOnce(aLatest.promise);
    getLoopSnapshotsApi.mockReturnValueOnce(aSnap.promise);
    const wrapper = mountConfidence('loop-A');
    await flushPromises();

    const bLatest = deferred<LoopConfidenceLatestItem | null>();
    const bSnap = deferred<{ items: any[]; total: number }>();
    getLoopConfidenceLatestApi.mockReturnValueOnce(bLatest.promise);
    getLoopSnapshotsApi.mockReturnValueOnce(bSnap.promise);
    await wrapper.setProps({ loopId: 'loop-B' });
    bLatest.resolve(latestDetail(77));
    bSnap.resolve({ items: [snapRow(77)], total: 1 });
    await flushPromises();
    expect(wrapper.text()).toContain('77.00');

    // A 的慢响应迟到返回：概要与历史都不得被覆盖
    aLatest.resolve(latestDetail(66));
    aSnap.resolve({ items: [snapRow(66)], total: 1 });
    await flushPromises();

    const text = wrapper.text();
    expect(text).toContain('77.00');
    expect(text).not.toContain('66.00');
  });

  it('组件关闭后迟到返回不污染：unmount 后旧响应不写入，新实例不受影响', async () => {
    const aLatest = deferred<LoopConfidenceLatestItem | null>();
    const aSnap = deferred<{ items: any[]; total: number }>();
    getLoopConfidenceLatestApi.mockReturnValueOnce(aLatest.promise);
    getLoopSnapshotsApi.mockReturnValueOnce(aSnap.promise);
    const wrapper = mountConfidence('loop-A');
    await flushPromises();

    wrapper.unmount();
    // 迟到返回（unmount 已 cancelAll 递增代次，guard 全拒）
    aLatest.resolve(latestDetail(66));
    aSnap.resolve({ items: [snapRow(66)], total: 1 });
    await flushPromises();

    // 新实例（重新打开详情）正常加载，不受旧实例迟到响应污染
    getLoopConfidenceLatestApi.mockResolvedValueOnce(latestDetail(55));
    getLoopSnapshotsApi.mockResolvedValueOnce({
      items: [snapRow(55)],
      total: 1,
    });
    const second = mountConfidence('loop-B');
    await flushPromises();

    const text = second.text();
    expect(text).toContain('55.00');
    expect(text).not.toContain('66.00');
  });
});
