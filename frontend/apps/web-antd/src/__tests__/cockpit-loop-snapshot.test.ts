import type { LoopApi } from '#/api/loop';

import { flushPromises, mount } from '@vue/test-utils';
import { defineComponent, h } from 'vue';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const { api } = vi.hoisted(() => ({
  api: {
    detail: vi.fn(),
    diagnoses: vi.fn(),
    orders: vi.fn(),
  },
}));

vi.mock('#/api/loop', () => ({ getLoopMonitorDetailApi: api.detail }));
vi.mock('#/api/diagnosis', () => ({
  getDiagnosisRunsLatestApi: api.diagnoses,
}));
vi.mock('#/api/handling', () => ({ getHandlingOrdersApi: api.orders }));
vi.mock('#/api/metric', () => ({ getNodeSnapshotApi: vi.fn() }));
vi.mock('@vben/plugins/echarts', () => ({
  EchartsUI: { template: '<div />' },
  useEcharts: () => ({ renderEcharts: vi.fn() }),
}));
vi.mock('@vben/stores', () => ({
  useUserStore: () => ({ userInfo: { roles: [] } }),
}));
vi.mock('vue-router', () => ({
  useRoute: () => ({ fullPath: '/cockpit/loops' }),
  useRouter: () => ({ push: vi.fn() }),
}));
vi.mock('#/store/cockpit', () => ({
  useCockpitStore: () => ({ theme: 'dark', timeWindow: 'last_24_hours' }),
}));
vi.mock('#/composables/use-loop-realtime', async () => {
  const { ref } = await import('vue');
  return {
    registerInterest: vi.fn(),
    useLoopRealtime: () => ({
      applyMessage: vi.fn(),
      connectionStatus: ref('online'),
      onMessage: vi.fn(),
      start: vi.fn(),
      startFallback: vi.fn(),
      stopFallback: vi.fn(),
    }),
  };
});
vi.mock('../views/cockpit/wb-comps/use-drill', () => ({
  canOpenWorkbench360: () => false,
  preciseCockpitWindow: vi.fn(),
  wb360DrillQuery: vi.fn(),
}));

import LoopDetailPanel from '../views/cockpit/components/loop-detail-panel.vue';
import { toLoopCardModel } from '../views/cockpit/loops-shared';

const RadarProbe = defineComponent({
  name: 'RadarProbe',
  props: {
    dims: { type: Object, default: null },
    grade: { type: Object, default: null },
    score: { type: Number, default: null },
  },
  setup: () => () => h('div'),
});

function snapshot(
  overrides: Partial<LoopApi.KpiSummary> = {},
): LoopApi.KpiSummary {
  return {
    accuracy_rate: 0,
    algorithm_version: 'KPI_CALC_v2.0',
    auto_mode_rate: 60,
    calculatedAt: '2026-10-21T02:00:00',
    composite_score: 7.18,
    confidence_level: 'A',
    effective_auto_rate: 51.46,
    fast_rate: 0,
    good_value_rate: 100,
    oscillation_rate: 0,
    saturation_rate: 0,
    status: 'SUCCESS',
    steady_rate: 37.22,
    ...overrides,
  };
}

function listItem(kpiSummary?: LoopApi.KpiSummary): LoopApi.MonitorListItem {
  return {
    controlMode: 'Auto',
    currentValues: {
      mode: 1,
      modeLabel: 'Auto',
      op: 50,
      pv: 100,
      pvQuality: 'GOOD',
      sp: 100,
    },
    description: '液位回路',
    isActive: true,
    kpiSummary,
    loopId: 'loop-80LIC32103',
    loopStatus: 'READY',
    readAt: '2026-10-21T02:01:00Z',
    score: kpiSummary?.composite_score ?? 0,
    tagName: '80LIC32103_PIDA',
    unitName: '装置一',
  };
}

function mountPanel(item = listItem(snapshot())) {
  return mount(LoopDetailPanel, {
    props: { loop: toLoopCardModel(item), view: 'loop' },
    global: { stubs: { CockpitRadar: RadarProbe } },
  });
}

const wrappers: ReturnType<typeof mountPanel>[] = [];

beforeEach(() => {
  vi.clearAllMocks();
  // 明确不同于最新小时快照，防止评分与近 24h 均值再次混用。
  api.detail.mockResolvedValue({
    currentValues: listItem().currentValues,
    kpiSummary: snapshot({
      accuracy_rate: 88,
      composite_score: 78.47,
      confidence_level: 'B',
      effective_auto_rate: 97,
      fast_rate: 66,
      steady_rate: 87,
    }),
    runtimeParams: {},
    trend: { op: [], pv: [], sp: [], timestamps: [] },
  });
  api.diagnoses.mockResolvedValue({ items: [] });
  api.orders.mockResolvedValue({ items: [] });
});

afterEach(() => {
  wrappers.splice(0).forEach((wrapper) => wrapper.unmount());
});

describe('驾驶舱回路详情的小时评估快照', () => {
  it('评分、雷达和可信度使用同一小时快照，24h 请求只提供趋势', async () => {
    const wrapper = mountPanel();
    wrappers.push(wrapper);
    await flushPromises();

    expect(api.detail).toHaveBeenCalledWith('loop-80LIC32103', 'last_24_hours');
    expect(wrapper.findComponent(RadarProbe).props()).toMatchObject({
      dims: {
        accuracyRate: 0,
        autoModeRate: 60,
        effectiveAutoRate: 51.46,
        fastRate: 0,
        goodValueRate: 100,
        steadyRate: 37.22,
      },
      score: 7.18,
    });
    expect(wrapper.find('.ldp__grid').text()).toContain('可信度A');
    expect(wrapper.text()).toContain('最新小时快照');
    // naive UTC 必须转成本地时间，不能直接按浏览器本地时区解析。
    expect(wrapper.text()).toContain('10-21 10:00');
  });

  it('同一回路刷新小时快照时同步更新分项、评分及截止时间', async () => {
    const wrapper = mountPanel();
    wrappers.push(wrapper);
    await flushPromises();
    await wrapper.setProps({
      loop: toLoopCardModel(
        listItem(
          snapshot({
            calculatedAt: '2026-10-21T03:00:00Z',
            composite_score: 88,
            confidence_level: 'C',
            effective_auto_rate: 90,
            steady_rate: 95,
          }),
        ),
      ),
    });

    expect(api.detail).toHaveBeenCalledTimes(1);
    expect(wrapper.findComponent(RadarProbe).props()).toMatchObject({
      dims: { effectiveAutoRate: 90, steadyRate: 95 },
      score: 88,
    });
    expect(wrapper.find('.ldp__grid').text()).toContain('可信度C');
    expect(wrapper.text()).toContain('10-21 11:00');
  });

  it('趋势读取失败时保留已有小时评估', async () => {
    api.detail.mockRejectedValueOnce(new Error('trend unavailable'));
    const wrapper = mountPanel();
    wrappers.push(wrapper);
    await flushPromises();

    expect(wrapper.findComponent(RadarProbe).props()).toMatchObject({
      dims: { effectiveAutoRate: 51.46, steadyRate: 37.22 },
      score: 7.18,
    });
    expect(wrapper.find('.ldp__grid').text()).toContain('可信度A');
  });

  it('缺少小时快照时不以 24h 汇总值补入雷达', async () => {
    const wrapper = mountPanel(listItem());
    wrappers.push(wrapper);
    await flushPromises();

    expect(wrapper.findComponent(RadarProbe).props('dims')).toBeNull();
    expect(wrapper.find('.ldp__grid').text()).toContain('可信度—');
    expect(wrapper.text()).toContain('暂无小时快照');
  });
});
