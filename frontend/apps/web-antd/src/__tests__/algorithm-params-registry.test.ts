/**
 * KPI 算法参数配置（views/metric/algorithm-params.vue）单元测试
 * — P2-01 前端同步批（CFG-01 修复）
 *
 * 背景（P2-01 验收报告 H 项）：基线前端本地维护第二份参数键清单，
 * accuracy_rate 仍是旧键 e_max_percentile，而后端注册表/校验器已切换到
 * e_max_tolerance_ratio——旧键保存必被后端以未知键 400 拒绝，
 * accuracy_rate 保存链在基线已破损（CFG-01 既有事实）。
 *
 * 本批修复口径：参数键集合以远端注册表（GET paramMeta）为全集，
 * 本地仅保留展示名/步进/精度增强（不含键集合、不含 min/max）。
 *
 * 覆盖：
 * 1. 注册表全集渲染：paramMetaOf 键集合 = 远端 paramMeta 键集合
 *    （title 回落注册表 label，min/max 以注册表为准）
 * 2. 旧键不出现：注册表无 e_max_percentile → 不进参数列
 *    （存量 DB 覆盖行携带旧键也不渲染）
 * 3. 新键自动进列：注册表新增键零前端改动进列；
 *    type=bool 渲染开关、type=int 步进取整（注册类型消费）
 * 4. 保存载荷只含注册键（accuracy_rate 保存链修复证据）：
 *    编辑态经 handleOpenEdit 只装注册键、handleSave 再按注册键过滤
 *    ——手动注入的旧键不进保存载荷，修复基线 400 破损链
 */
import { mount } from '@vue/test-utils';
import { nextTick } from 'vue';

import { beforeEach, describe, expect, it, vi } from 'vitest';

const { getAlgorithmParamsApiMock, saveApiMock } = vi.hoisted(() => ({
  getAlgorithmParamsApiMock: vi.fn(),
  saveApiMock: vi.fn().mockResolvedValue({}),
}));

vi.mock('#/api/metric', () => ({
  getAlgorithmParamsApi: getAlgorithmParamsApiMock,
  saveMetricAlgorithmParamsApi: saveApiMock,
}));

vi.mock('#/composables/use-clpm-theme', () => ({
  useClpmTheme: () => ({
    themeColors: { NEUTRAL: '#5b6472' },
  }),
}));

vi.mock('#/components/clpm', () => ({
  ClpmDataCanvas: {
    name: 'ClpmDataCanvas',
    template: '<section><slot /><slot name="extra" /></section>',
  },
  ClpmHelpIcon: { name: 'ClpmHelpIcon', template: '<span>help</span>' },
  ClpmToolbarButton: { name: 'ClpmToolbarButton', template: '<button />' },
}));

// oxlint-disable-next-line import/first -- Vitest mocks must be registered before these imports
import AlgorithmParams from '../views/metric/algorithm-params.vue';

/** 构造指标组（4 控制类型齐全，对齐真实后端 GET 响应） */
function makeGroup(
  metricCode: string,
  paramMeta: Record<string, Record<string, unknown>>,
  storedParams: Record<string, unknown>,
  defaults: Record<string, unknown>,
) {
  const controlTypes = ['STABLE', 'SLOW', 'FAST', 'LOGIC'] as const;
  return {
    metricCode,
    metricName: metricCode,
    items: controlTypes.map((controlType) => ({
      controlType,
      // STABLE 存量覆盖行携带废弃旧键（模拟 DB 覆盖行，修复前会被原样发回后端 → 400）
      params: controlType === 'STABLE' ? storedParams : { ...defaults },
      defaults: { ...defaults },
      overridden: controlType === 'STABLE',
    })),
    paramMeta,
  };
}

/**
 * accuracy_rate：注册表只有新键 e_max_tolerance_ratio；
 * 存量覆盖行携带旧键 e_max_percentile（基线破损现场）。
 */
const ACCURACY_GROUP = makeGroup(
  'accuracy_rate',
  {
    e_max_tolerance_ratio: {
      label: '工程容限比例',
      type: 'float',
      min: 0.002,
      max: 0.2,
      unit: '',
      description: '工程容限比例（|E|max = ratio×量程，默认 2%）',
      category: '判定阈值',
    },
  },
  { e_max_percentile: 0.1, e_max_tolerance_ratio: 0.02 },
  { e_max_tolerance_ratio: 0.02 },
);

/**
 * stability_rate：注册表含 bool 开关键、int 点数键与一个
 * "未来新增键"（前端无任何本地条目）——验证新键自动进列。
 */
const STABILITY_GROUP = makeGroup(
  'stability_rate',
  {
    band_in_score_enabled: {
      label: '带内率计入分值',
      type: 'bool',
      category: '石化惯例',
    },
    decay_ratio: {
      label: '衰减比',
      type: 'float',
      min: 0.01,
      max: 0.5,
      category: '判定阈值',
    },
    brand_new_key: {
      label: '未来新增参数',
      type: 'int',
      min: 1,
      max: 10,
      category: '判定阈值',
    },
  },
  { decay_ratio: 0.05 },
  { decay_ratio: 0.05 },
);

function mockSchema() {
  getAlgorithmParamsApiMock.mockResolvedValue({
    metrics: [ACCURACY_GROUP, STABILITY_GROUP],
    updatedAt: '2026-10-11T02:00:00',
    updatedBy: 'admin',
  });
}

const mountOptions = {
  global: {
    directives: { permission: {} },
    stubs: {
      ATable: true,
      ADrawer: true,
      AButton: true,
      AInputNumber: true,
      ASwitch: true,
      ATag: true,
      ATooltip: true,
    },
  },
};

async function mountPage() {
  const wrapper = mount(AlgorithmParams, mountOptions);
  await nextTick();
  await nextTick();
  return wrapper;
}

describe('算法参数配置注册表全集（P2-01 前端同步 CFG-01）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSchema();
    saveApiMock.mockResolvedValue({});
  });

  it('uT-APP-001: 参数列集合 = 注册表键全集，title/min/max 以注册表为准（本地无第二份键清单）', async () => {
    const wrapper = await mountPage();
    const vm = wrapper.vm as any;

    const accuracy = vm.paramMetaOf('accuracy_rate') as Array<{
      key: string;
      max: number;
      min: number;
      title: string;
    }>;
    expect(accuracy.map((p) => p.key)).toEqual(['e_max_tolerance_ratio']);
    // accuracy_rate 无本地增强条目：展示名/值域全部来自注册表
    expect(accuracy[0]!.title).toBe('工程容限比例');
    expect(accuracy[0]!.min).toBe(0.002);
    expect(accuracy[0]!.max).toBe(0.2);
  });

  it('uT-APP-002: 废弃旧键 e_max_percentile 不在注册表 → 不进列（存量覆盖行携带旧键也不渲染）', async () => {
    const wrapper = await mountPage();
    const vm = wrapper.vm as any;

    // 存量覆盖行 params 携带 e_max_percentile=0.1（见 ACCURACY_GROUP）
    const columns = vm.buildColumns('accuracy_rate') as Array<{
      dataIndex?: unknown;
      key: string;
    }>;
    const paramKeys = columns
      .filter((c) => Array.isArray(c.dataIndex))
      .map((c) => c.key);
    expect(paramKeys).toEqual(['e_max_tolerance_ratio']);
    expect(JSON.stringify(columns)).not.toContain('e_max_percentile');
    // 编辑态同样只装注册键（打开编辑后 editParams 无旧键）
    const group = (vm.metrics as Array<any>).find(
      (g) => g.metricCode === 'accuracy_rate',
    );
    vm.handleOpenEdit(group);
    expect(
      Object.keys(vm.editParams.accuracy_rate.STABLE),
    ).toEqual(['e_max_tolerance_ratio']);
  });

  it('uT-APP-003: 注册表新键自动进列；type=bool 渲染开关、type=int 步进取整', async () => {
    const wrapper = await mountPage();
    const vm = wrapper.vm as any;

    const params = vm.paramMetaOf('stability_rate') as Array<{
      key: string;
      precision: number;
      step: number;
      type: string;
    }>;
    const keys = params.map((p) => p.key);
    // 前端本地无 brand_new_key 条目 → 零改动自动进列
    expect(keys).toContain('brand_new_key');
    expect(keys).toContain('band_in_score_enabled');
    expect(keys).toContain('decay_ratio');

    const byKey = Object.fromEntries(params.map((p) => [p.key, p])) as Record<
      string,
      { precision: number; step: number; type: string }
    >;
    // 注册类型消费：bool → 开关；int → 整数步进/精度
    expect(byKey.band_in_score_enabled!.type).toBe('switch');
    expect(byKey.brand_new_key!.step).toBe(1);
    expect(byKey.brand_new_key!.precision).toBe(0);
    expect(byKey.decay_ratio!.type).toBe('number');
  });

  it('uT-APP-004: 保存载荷只含注册键——修复 accuracy_rate 旧键 e_max_percentile 保存链（基线 400 破损）', async () => {
    const wrapper = await mountPage();
    const vm = wrapper.vm as any;

    const group = (vm.metrics as Array<any>).find(
      (g) => g.metricCode === 'accuracy_rate',
    );
    vm.handleOpenEdit(group);
    // 制造有效变更（0.02 → 0.03，注册区间 [0.002, 0.2] 内）
    vm.editParams.accuracy_rate.STABLE.e_max_tolerance_ratio = 0.03;
    // 模拟旧键经任意路径残留在编辑态（修复前会被原样发回后端 → 未知键 400）
    vm.editParams.accuracy_rate.SLOW.e_max_percentile = 0.1;

    await vm.handleSave();

    expect(saveApiMock).toHaveBeenCalledTimes(1);
    const [metricCode, payload] = saveApiMock.mock.calls[0] as [
      string,
      { items: Array<{ controlType: string; params: Record<string, number> }> },
    ];
    expect(metricCode).toBe('accuracy_rate');
    expect(payload.items).toHaveLength(4);
    for (const item of payload.items) {
      // 保存载荷只发注册表内键——旧键被过滤，保存链恢复（不再被 400 拒绝）
      expect(Object.keys(item.params)).toEqual(['e_max_tolerance_ratio']);
    }
    expect(payload.items.find((i) => i.controlType === 'STABLE')!.params).toEqual(
      { e_max_tolerance_ratio: 0.03 },
    );
    expect(JSON.stringify(payload)).not.toContain('e_max_percentile');
  });
});
