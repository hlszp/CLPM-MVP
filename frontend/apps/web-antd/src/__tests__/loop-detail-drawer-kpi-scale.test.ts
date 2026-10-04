/**
 * LoopDetailDrawer KPI 口径回归测试（回路监视页改版 P1-0）
 *
 * 背景：kpiSummary 各率字段后端快照为 0-100 口径（kpi_calc 写库即百分数值），
 * 曾因错误假设 0-1 口径统一 ×100 导致全部放大 100 倍。
 * 守护两条口径：
 * - KPI 率字段（auto_mode_rate 等 8 项）0-100 直拼 %
 * - validRate 独立 0-1 口径（×100 展示），不得被连带修改
 *
 * 注：antd Drawer 内容 teleport 到 document.body，断言走 body 文本。
 */
import type { LoopApi } from '#/api/loop';

import { mount } from '@vue/test-utils';

import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('#/composables/use-loop-palettes', () => ({
  LOOP_TYPE_LABEL_MAP: { FC: '流量', LC: '液位', TC: '温度', PC: '压力' },
  MODE_LABEL_MAP: { AUTO: '自动', CAS: '串级', MAN: '手动' },
}));

vi.mock('#/constants/clpm-ui', () => ({
  GRADE_LEVEL_LABEL: { 1: '优秀', 2: '良好', 3: '合格', 4: '警告', 5: '不合格' },
  fitnessTagToLabel: (t: string) => t,
}));

vi.mock('#/components/clpm', () => ({
  ClpmFitnessBadge: { name: 'ClpmFitnessBadge', template: '<span />' },
}));

const loopDetailDrawerModule = await import(
  '#/components/monitor/loop-detail-drawer.vue'
);
const LoopDetailDrawer = loopDetailDrawerModule.default;

function makeLoop(
  kpiSummary: Partial<LoopApi.MonitorListItem['kpiSummary']>,
  validRate?: number,
): LoopApi.MonitorListItem {
  return {
    loopId: 'loop-1',
    tagName: 'LIC-101',
    kpiSummary: kpiSummary as LoopApi.MonitorListItem['kpiSummary'],
    dataHealth: validRate === undefined ? undefined : { validRate },
  } as unknown as LoopApi.MonitorListItem;
}

let activeWrapper: ReturnType<typeof mount> | undefined;

function renderDrawerAndGrabBody(
  loop: LoopApi.MonitorListItem | null,
): string {
  activeWrapper?.unmount();
  activeWrapper = mount(LoopDetailDrawer, {
    props: { open: true, loop },
  });
  return document.body.textContent ?? '';
}

afterEach(() => {
  activeWrapper?.unmount();
  activeWrapper = undefined;
});

describe('LoopDetailDrawer KPI 口径', () => {
  it('KPI 率字段为 0-100 口径直拼 %，不得再 ×100', () => {
    const text = renderDrawerAndGrabBody(
      makeLoop({
        effective_auto_rate: 72.5,
        steady_rate: 61.3,
        fast_rate: 58,
        accuracy_rate: 80.05,
        auto_mode_rate: 85.2,
        good_value_rate: 99.1,
        oscillation_rate: 12.34,
        saturation_rate: 3.5,
        calculatedAt: '2026-10-03T00:00:00Z',
      }),
    );
    expect(text).toContain('85.20%'); // 自控率
    expect(text).toContain('99.10%'); // 好值率
    expect(text).toContain('72.50%'); // 有效自控率
    expect(text).toContain('12.34%'); // 振荡率
    expect(text).not.toContain('8520');
    expect(text).not.toContain('9910');
  });

  it('缺失 KPI 字段显示占位符 —，不出现 NaN', () => {
    const text = renderDrawerAndGrabBody(
      makeLoop({ auto_mode_rate: undefined }),
    );
    expect(text).toContain('自控率');
    expect(text).not.toContain('NaN');
  });

  it('validRate 保持 0-1 口径 ×100 展示（不可连带修改）', () => {
    const text = renderDrawerAndGrabBody(
      makeLoop({ auto_mode_rate: 50 }, 0.972),
    );
    expect(text).toContain('97.2%');
  });
});
