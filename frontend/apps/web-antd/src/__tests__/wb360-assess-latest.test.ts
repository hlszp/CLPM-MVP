/**
 * 评估历史"最新评估独立查询"测试（P1-03，IA-03 / DEC-09）
 *
 * C26：历史翻页/过滤不改变当前评估——
 * - latest 由 latestOnly=true 单行独立查询（小时表最新业务窗标准评估），
 *   不再从历史列表当前页第一条非 custom 行推导；
 * - 翻页/切来源档后 latest 保持不变（头部事实不随列表漂移）；
 * - 独立查询失败显式置空 + latestError（不保留旧值冒充当前事实）。
 */
import type { Mock } from 'vitest';

import { nextTick, ref } from 'vue';

import { beforeEach, describe, expect, it, vi } from 'vitest';

const { getLoopSnapshotsApi } = vi.hoisted(() => ({
  getLoopSnapshotsApi: vi.fn(),
}));

vi.mock('#/api/metric', () => ({
  getLoopSnapshotsApi: (...a: unknown[]) => getLoopSnapshotsApi(...a),
}));

import { useAssessHistory } from '../views/loop/workbench360/composables/use-assess-history';

function snap(tsStart: string, score: number, source = 'SCHEDULED') {
  return {
    accuracyRate: 80,
    autoModeRate: 90,
    effectiveAutoRate: 82,
    fastRate: 75,
    goodValueRate: 96,
    loopId: 'loop-1',
    score,
    source,
    steadyRate: 85,
    status: 'SUCCESS',
    tsEnd: tsStart,
    tsStart,
  };
}

describe('use-assess-history 最新评估独立查询（P1-03 IA-03）', () => {
  beforeEach(() => {
    getLoopSnapshotsApi.mockReset();
  });

  it('latest 来自 latestOnly 独立查询，翻页/来源过滤不改变头部事实', async () => {
    const latestRow = snap('2026-10-10T10:00:00', 88.5);
    getLoopSnapshotsApi.mockImplementation(async (params: any) => {
      if (params.latestOnly) {
        // 独立查询：恒返回最新业务窗标准评估（不受历史列表过滤影响）
        expect(params.loopId).toBe('loop-1');
        expect(params.sortBy).toBe('tsStart');
        expect(params.sortOrder).toBe('desc');
        return { items: [latestRow], total: 1 };
      }
      // 历史列表：翻到第 2 页/切手动档时返回的是别的行
      if (params.source === 'MANUAL_CUSTOM') {
        return { items: [snap('2026-10-01T02:00:00', 12, 'MANUAL_CUSTOM')], total: 40 };
      }
      return {
        items: [snap('2026-10-08T01:00:00', 40)],
        page: params.page,
        total: 40,
      };
    });

    const loopId = ref<null | string>('loop-1');
    // provide 在组件外调用会告警但不影响断言（模块逻辑不依赖注入消费方）
    const history = useAssessHistory(loopId);
    await nextTick();
    await Promise.resolve();

    expect(history.latest.value?.score).toBe(88.5);

    // 翻页 → latest 不变
    await history.loadHistory(2);
    expect(history.latest.value?.score).toBe(88.5);

    // 切来源档（manual）→ 历史行变化，latest 仍是标准评估最新行
    history.sourceFilter.value = 'manual';
    await nextTick();
    await Promise.resolve();
    expect(history.rows.value[0]?.source).toBe('MANUAL_CUSTOM');
    expect(history.latest.value?.score).toBe(88.5);
    expect(history.latest.value?.source).toBe('SCHEDULED');

    // 独立查询确实发生（与历史列表查询分离）
    const latestCalls = (getLoopSnapshotsApi as Mock).mock.calls
      .map((c) => c[0] as Record<string, unknown>)
      .filter((p) => p.latestOnly === true);
    expect(latestCalls.length).toBeGreaterThanOrEqual(2);
  });

  it('独立查询失败：latest 置空 + latestError（不渲染旧事实）', async () => {
    getLoopSnapshotsApi.mockImplementation(async (params: any) => {
      if (params.latestOnly) {
        throw new Error('Request failed with status code 500');
      }
      return { items: [snap('2026-10-08T01:00:00', 40)], total: 1 };
    });

    const history = useAssessHistory(ref('loop-1'));
    await nextTick();
    await Promise.resolve();

    expect(history.latest.value).toBeNull();
    expect(history.latestError.value).toContain('500');
    // 历史列表本身不受独立查询失败影响
    expect(history.rows.value).toHaveLength(1);
  });
});
