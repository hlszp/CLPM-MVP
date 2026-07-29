import { flushPromises, mount } from '@vue/test-utils';

import { beforeEach, describe, expect, it, vi } from 'vitest';

import TuningWorkbench from '../views/tuning/workbench.vue';

const getTuningHistoryApiMock = vi.fn();

vi.mock('#/api/tuning', () => ({
  getTuningHistoryApi: () => getTuningHistoryApiMock(),
}));

vi.mock('#/composables/use-clpm-theme', () => ({
  useClpmTheme: () => ({
    themeColors: {
      value: {
        DANGER: '#f43f5e',
        SUCCESS: '#10b981',
        WARNING: '#f59e0b',
      },
    },
  }),
}));

vi.mock('vue-router', () => ({
  useRouter: () => ({
    push: vi.fn(),
  }),
}));

vi.mock('@vben/common-ui', () => ({
  Page: { template: '<div><slot /></div>' },
}));

vi.mock('@vben/icons', () => ({
  IconifyIcon: { template: '<span />' },
}));

vi.mock('ant-design-vue', () => ({
  Alert: { template: '<div />' },
  Button: { template: '<button><slot /></button>' },
  Card: { template: '<div><slot /></div>' },
  Spin: { template: '<div><slot /></div>' },
  Table: { template: '<div />' },
  Tag: { template: '<span><slot /></span>' },
}));

vi.mock('#/components/clpm', () => ({
  ClpmDataCanvas: { template: '<div><slot /></div>' },
  ClpmKpiStrip: {
    props: ['items'],
    template: `
      <div>
        <div
          v-for="item in items"
          :key="item.key"
          :data-kpi="item.key"
          :data-status="item.status"
        >
          {{ item.label }}:{{ item.value }}{{ item.unit || '' }}
        </div>
      </div>
    `,
  },
  ClpmPageToolbar: { template: '<div><slot /></div>' },
  ClpmToolbarButton: { template: '<button />' },
}));

describe('TuningWorkbench', () => {
  beforeEach(() => {
    getTuningHistoryApiMock.mockReset();
    getTuningHistoryApiMock.mockResolvedValue({
      avgFittingScore: null,
      byAlgorithm: {},
      byStatus: {},
      recentTasks: [],
      totalTasks: 0,
    });
  });

  it('将尚未计算的风险统计显示为未知而不是伪 0', async () => {
    const wrapper = mount(TuningWorkbench);
    await flushPromises();

    const highRisk = wrapper.get('[data-kpi="highRisk"]');
    const overThreshold = wrapper.get('[data-kpi="overThreshold"]');

    expect(highRisk.text()).toBe('风险任务数:—未计算');
    expect(highRisk.attributes('data-status')).toBe('neutral');
    expect(overThreshold.text()).toBe('超阈值任务数:—未计算');
    expect(overThreshold.attributes('data-status')).toBe('neutral');

    expect(wrapper.text()).not.toContain('风险任务数:0');
    expect(wrapper.text()).not.toContain('超阈值任务数:0');
  });
});
