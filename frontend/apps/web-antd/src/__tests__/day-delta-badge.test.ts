/**
 * DayDeltaBadge 单元测试（整改 C1-1）
 *
 * 覆盖：趋势文案/语义色/绝对值处理 + 持平/无趋势不渲染
 */
import { mount } from '@vue/test-utils';

import { describe, expect, it } from 'vitest';

import DayDeltaBadge from '#/components/loop/day-delta-badge.vue';

function mountBadge(props: Record<string, unknown>) {
  return mount(DayDeltaBadge, {
    global: { stubs: { Tooltip: { template: '<span><slot /></span>' } } },
    props,
  });
}

describe('dayDeltaBadge', () => {
  it('WORSENED：红 ▼绝对值', () => {
    const w = mountBadge({ delta: -7, trend: 'WORSENED' });
    expect(w.text()).toBe('▼7.0');
    expect(w.find('span.text-rose-600').exists()).toBe(true);
  });

  it('IMPROVED：绿 ▲delta', () => {
    const w = mountBadge({ delta: 3.5, trend: 'IMPROVED' });
    expect(w.text()).toBe('▲3.5');
    expect(w.find('span.text-emerald-600').exists()).toBe(true);
  });

  it('FLAT：持平不渲染视觉噪声', () => {
    const w = mountBadge({ delta: 0.8, trend: 'FLAT' });
    expect(w.text()).toBe('');
  });

  it('NEW：蓝「新增」', () => {
    const w = mountBadge({ delta: null, trend: 'NEW' });
    expect(w.text()).toBe('新增');
    expect(w.find('span.text-blue-500').exists()).toBe(true);
  });

  it('trend 为 null 时不渲染', () => {
    const w = mountBadge({ delta: null, trend: null });
    expect(w.text()).toBe('');
  });
});
