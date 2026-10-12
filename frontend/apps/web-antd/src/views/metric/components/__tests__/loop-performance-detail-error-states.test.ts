import type { LoopPerformanceRow } from '#/views/metric/components/loop-performance-shared';

/**
 * G3-R03（14 号文 §4）：回路性能详情内容——历史快照子表错误态与竞态守卫
 *
 * 覆盖验收反例（与 loop-confidence-content-error-states.test.ts 共同覆盖
 * "两个历史入口均验"）：
 * - HTTP 成功返回 0 条 → 显示"暂无历史评估记录"（成功空 ≠ 失败）
 * - 422/500/超时 → 显式失败原因 + 重试，不冒充空态
 * - 403 → 权限提示，切换回路后旧回路数据不泄漏
 * - 重试成功恢复
 * - A 慢 B 快切换只显示 B；组件关闭后迟到返回不污染
 * - describeRequestError 错误分类（两组件共用 helper）
 */
import { flushPromises, mount } from '@vue/test-utils';

import { beforeEach, describe, expect, it, vi } from 'vitest';

// oxlint-disable-next-line import/first -- Vitest mocks must be registered before these imports
import LoopPerformanceDetailContent from '#/views/metric/components/loop-performance-detail-content.vue';
import { describeRequestError } from '#/views/metric/components/loop-performance-shared';

const { getLoopSnapshotsApi } = vi.hoisted(() => ({
  getLoopSnapshotsApi: vi.fn(),
}));

vi.mock('#/api/metric', () => ({
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

function baseRow(loopId = 'loop-A'): LoopPerformanceRow {
  return {
    loopId,
    loopTagName: 'FIC-101',
    tsStart: '2026-10-12T08:00:00Z',
    tsEnd: '2026-10-12T09:00:00Z',
    score: 80,
    status: 'SUCCESS',
    fastRate: 90,
    accuracyRate: 90,
    steadyRate: 95,
    effectiveAutoRate: 90,
    autoModeRate: 92,
    goodValueRate: 99,
    oscillationRate: 5,
    confidenceLevel: 'A',
    algorithmVersion: 'KPI_CALC_v3.0-rc1',
  } as unknown as LoopPerformanceRow;
}

function snapRow(loopId: string, score: number) {
  return {
    loopId,
    tsStart: '2026-10-12T0' + (score % 9) + ':00:00Z',
    tsEnd: '2026-10-12T0' + (score % 9) + ':59:59Z',
    score,
    status: 'SUCCESS',
    confidenceLevel: 'B',
    algorithmVersion: 'KPI_CALC_v3.0-rc1',
  };
}

function mountDetail(record: LoopPerformanceRow) {
  return mount(LoopPerformanceDetailContent, {
    props: { record, withHistory: true },
  });
}

async function clickRetry(wrapper: ReturnType<typeof mount>) {
  const btn = wrapper
    .findAll('button')
    .find((b) => b.text().includes('重新加载'));
  expect(btn, '应渲染重试按钮').toBeTruthy();
  await btn!.trigger('click');
}

beforeEach(() => {
  getLoopSnapshotsApi.mockReset();
});

describe('G3-R03 loop-performance-detail-content 历史子表四态', () => {
  it('HTTP 成功返回 0 条：显示"暂无历史评估记录"，不显示失败', async () => {
    getLoopSnapshotsApi.mockResolvedValue({ items: [], total: 0 });
    const wrapper = mountDetail(baseRow());
    await flushPromises();

    const text = wrapper.text();
    expect(text).toContain('历史快照（最近 10 条）');
    expect(text).toContain('该回路暂无历史评估记录');
    expect(text).not.toContain('加载失败');
    expect(getLoopSnapshotsApi).toHaveBeenCalledTimes(1);
  });

  it('500 失败：显式服务异常原因 + 重试，不冒充空态', async () => {
    getLoopSnapshotsApi.mockRejectedValue(httpError(500));
    const wrapper = mountDetail(baseRow());
    await flushPromises();

    const text = wrapper.text();
    expect(text).toContain('历史快照加载失败：服务异常（500）');
    expect(text).toContain('重新加载');
    // 失败不是"没有历史"：空态文案不出现，emptyText 走错误分支
    expect(text).not.toContain('该回路暂无历史评估记录');
    expect(text).toContain('历史快照加载失败，无法确认');
  });

  it('422 失败：显式请求被拒绝原因 + 重试', async () => {
    getLoopSnapshotsApi.mockRejectedValue(httpError(422, 'loopId 无效'));
    const wrapper = mountDetail(baseRow());
    await flushPromises();

    expect(wrapper.text()).toContain(
      '历史快照加载失败：请求被拒绝（422）：loopId 无效',
    );
    expect(wrapper.text()).toContain('重新加载');
  });

  it('超时失败：显式请求超时原因 + 重试', async () => {
    getLoopSnapshotsApi.mockRejectedValue(timeoutError());
    const wrapper = mountDetail(baseRow());
    await flushPromises();

    expect(wrapper.text()).toContain('历史快照加载失败：请求超时');
    expect(wrapper.text()).toContain('重新加载');
  });

  it('403 失败：权限提示，不显示"暂无历史评估记录"冒充空态', async () => {
    getLoopSnapshotsApi.mockRejectedValue(httpError(403));
    const wrapper = mountDetail(baseRow());
    await flushPromises();

    const text = wrapper.text();
    expect(text).toContain('历史快照加载失败：无权限访问（403）');
    expect(text).not.toContain('该回路暂无历史评估记录');
  });

  it('重试成功恢复：失败后点击重试，数据渲染且错误消失', async () => {
    getLoopSnapshotsApi.mockRejectedValueOnce(httpError(500));
    const wrapper = mountDetail(baseRow());
    await flushPromises();
    expect(wrapper.text()).toContain('历史快照加载失败');

    getLoopSnapshotsApi.mockResolvedValueOnce({
      items: [snapRow('loop-A', 95)],
      total: 1,
    });
    await clickRetry(wrapper);
    await flushPromises();

    const text = wrapper.text();
    expect(text).toContain('95.00');
    expect(text).not.toContain('加载失败');
    expect(text).not.toContain('该回路暂无历史评估记录');
  });

  it('403 切换回路：旧回路历史数据不泄漏（越权旧详情不保留）', async () => {
    // A 历史行用独特 score（63），避免与 B 详情主体指标数值撞值
    getLoopSnapshotsApi.mockResolvedValueOnce({
      items: [snapRow('loop-A', 63)],
      total: 1,
    });
    const wrapper = mountDetail(baseRow('loop-A'));
    await flushPromises();
    expect(wrapper.text()).toContain('63.00');

    getLoopSnapshotsApi.mockRejectedValueOnce(httpError(403));
    await wrapper.setProps({ record: baseRow('loop-B') });
    await flushPromises();

    const text = wrapper.text();
    expect(text).toContain('历史快照加载失败：无权限访问（403）');
    // 旧回路 A 的历史行不残留；历史子表走错误分支（emptyText），非空态冒充
    expect(text).not.toContain('63.00');
    expect(text).not.toContain('该回路暂无历史评估记录');
  });

  it('A 慢 B 快切换：只显示 B 的历史，A 迟到响应被丢弃', async () => {
    const aSnap = deferred<{ items: any[]; total: number }>();
    const bSnap = deferred<{ items: any[]; total: number }>();
    getLoopSnapshotsApi.mockReturnValueOnce(aSnap.promise);
    getLoopSnapshotsApi.mockReturnValueOnce(bSnap.promise);

    const wrapper = mountDetail(baseRow('loop-A'));
    await flushPromises();

    await wrapper.setProps({ record: baseRow('loop-B') });
    bSnap.resolve({ items: [snapRow('loop-B', 77)], total: 1 });
    await flushPromises();
    expect(wrapper.text()).toContain('77.00');

    // A 的慢响应迟到返回：不得覆盖 B 的数据
    aSnap.resolve({ items: [snapRow('loop-A', 66)], total: 1 });
    await flushPromises();

    const text = wrapper.text();
    expect(text).toContain('77.00');
    expect(text).not.toContain('66.00');
  });

  it('组件关闭后迟到返回不污染：unmount 后旧响应不写入，新实例不受影响', async () => {
    const aSnap = deferred<{ items: any[]; total: number }>();
    getLoopSnapshotsApi.mockReturnValueOnce(aSnap.promise);
    const wrapper = mountDetail(baseRow('loop-A'));
    await flushPromises();

    wrapper.unmount();
    // 迟到返回（unmount 已 cancelAll 递增代次，guard 全拒）
    aSnap.resolve({ items: [snapRow('loop-A', 66)], total: 1 });
    await flushPromises();

    // 新实例（重新打开详情）正常加载，不受旧实例迟到响应污染
    getLoopSnapshotsApi.mockResolvedValueOnce({
      items: [snapRow('loop-B', 55)],
      total: 1,
    });
    const second = mountDetail(baseRow('loop-B'));
    await flushPromises();

    const text = second.text();
    expect(text).toContain('55.00');
    expect(text).not.toContain('66.00');
  });
});

describe('G3-R03 describeRequestError 错误分类（两历史入口共用）', () => {
  it('403 → 权限提示', () => {
    expect(describeRequestError(httpError(403))).toContain(
      '无权限访问（403）',
    );
  });

  it('422 → 请求被拒绝（带后端 message）', () => {
    expect(describeRequestError(httpError(422, '参数无效'))).toBe(
      '请求被拒绝（422）：参数无效',
    );
    expect(describeRequestError(httpError(422))).toBe(
      '请求被拒绝（422）：参数校验失败',
    );
  });

  it('5xx → 服务异常带状态码', () => {
    expect(describeRequestError(httpError(500))).toBe('服务异常（500），请稍后重试');
    expect(describeRequestError(httpError(503))).toBe('服务异常（503），请稍后重试');
  });

  it('其他 HTTP 状态 → 带状态码与后端 message', () => {
    expect(describeRequestError(httpError(404, '不存在'))).toBe(
      '请求失败（404）：不存在',
    );
  });

  it('HTTP 200 业务错误 → 后端 message', () => {
    expect(describeRequestError(httpError(200, '评估记录损坏'))).toBe(
      '请求失败：评估记录损坏',
    );
  });

  it('超时（ECONNABORTED / message 含 timeout）→ 请求超时', () => {
    expect(describeRequestError(timeoutError())).toBe('请求超时，请稍后重试');
  });

  it('无状态无消息 → 网络异常兜底', () => {
    expect(describeRequestError(new Error('网络断开'))).toBe(
      '请求失败：网络断开',
    );
    expect(describeRequestError(undefined)).toBe(
      '网络异常或请求失败，请稍后重试',
    );
  });
});
