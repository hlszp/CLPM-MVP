/**
 * 诊断配置页（views/diagnosis/config.vue）单元测试
 * — P2-01 前端同步批（CFG-02 渲染侧）
 *
 * 背景：GET /configs/diagnosis 响应新增可选 methodMeta（字段级元数据，
 * P2-01 后端批已落地）：algorithmType/calcMethod/params 标
 * readOnly=true / consumedByLiveEngine=false（可存但 v2 活执行引擎不消费）；
 * threshold/isEnabled/diagName 为真实可调字段。
 *
 * 覆盖：
 * 1. methodMeta 渲染"只读说明（引擎未消费标注）vs 真实可调字段"分离：
 *    只读说明区显式渲染（含"仅供参考 · 引擎未消费"标注与 note 说明），
 *    algorithmType/calcMethod/params 控件 disabled；
 *    threshold/isEnabled/diagName 可编辑（控件不带 disabled）
 * 2. methodMeta 缺失（旧后端）→ 保持存量可编辑形态（向后兼容，零破坏）
 * 3. 保存 payload 结构不变：readOnly 字段仍随 PUT 发送
 *    （后端兼容接收存量字段，本批不改保存契约）
 */
import { mount } from '@vue/test-utils';
import { ref } from 'vue';

import { beforeEach, describe, expect, it, vi } from 'vitest';

const {
  createApiMock,
  deleteApiMock,
  getconfigsApiMock,
  historyApiMock,
  operatorsApiMock,
  rollbackApiMock,
  updateApiMock,
} = vi.hoisted(() => ({
  createApiMock: vi.fn().mockResolvedValue({}),
  deleteApiMock: vi.fn().mockResolvedValue({}),
  getconfigsApiMock: vi.fn(),
  historyApiMock: vi.fn().mockResolvedValue({ items: [], currentVersion: 3 }),
  operatorsApiMock: vi.fn().mockResolvedValue([]),
  rollbackApiMock: vi.fn().mockResolvedValue({}),
  updateApiMock: vi.fn().mockResolvedValue({ items: [], updatedCount: 1 }),
}));

vi.mock('#/api/diagnosis', () => ({
  createDiagnosisConfigApi: createApiMock,
  deleteDiagnosisConfigApi: deleteApiMock,
  getDiagnosisConfigHistoryApi: historyApiMock,
  getDiagnosisConfigsApi: getconfigsApiMock,
  getDiagnosisOperatorsApi: operatorsApiMock,
  getDiagnosisReviewFeedbackApi: vi.fn().mockResolvedValue({
    operators: [],
    categories: [],
    sampleMin: 10,
  }),
  rollbackDiagnosisConfigApi: rollbackApiMock,
  updateDiagnosisConfigsApi: updateApiMock,
}));

vi.mock('#/composables/use-clpm-roles', () => ({
  useClpmRoles: () => ({ isAdmin: ref(true) }),
}));

vi.mock('#/composables/use-page-toolbar', () => ({
  usePageToolbar: () => ({ toolbarItems: [] }),
}));

vi.mock('#/components/clpm', () => ({
  ClpmHelpIcon: { name: 'ClpmHelpIcon', template: '<span>help</span>' },
  ClpmPageToolbar: {
    name: 'ClpmPageToolbar',
    template: '<header><slot name="actions" /></header>',
  },
  ClpmStandardActions: { name: 'ClpmStandardActions', template: '<span />' },
  ClpmVersionHistoryModal: {
    name: 'ClpmVersionHistoryModal',
    template: '<div />',
  },
}));

// oxlint-disable-next-line import/first -- Vitest mocks must be registered before these imports
import type { DiagnosisConfigApi } from '#/api/diagnosis';

// oxlint-disable-next-line import/first -- Vitest mocks must be registered before these imports
import ConfigPage from '../views/diagnosis/config.vue';

/** 对齐后端 DIAGNOSIS_FIELD_META（P2-01 后端批）的字段级元数据 */
const METHOD_META: Record<string, DiagnosisConfigApi.FieldMeta> = {
  algorithmType: {
    label: '算法类型',
    readOnly: true,
    consumedByLiveEngine: false,
    note: 'v2 诊断执行引擎不消费该字段；仅作注册表只读说明保留（CFG-02）',
  },
  calcMethod: {
    label: '计算方法',
    readOnly: true,
    consumedByLiveEngine: false,
    note: 'v2 诊断执行引擎不消费该字段；仅作注册表只读说明保留（CFG-02）',
  },
  params: {
    label: '算法参数',
    readOnly: true,
    consumedByLiveEngine: false,
    note: 'v2 诊断执行引擎不消费该字段；仅作注册表只读说明保留（CFG-02）',
  },
  threshold: {
    label: '阈值',
    readOnly: false,
    consumedByLiveEngine: true,
    note: '全局默认层，真实可调（层级覆盖：全局默认 < 模板 < 装置 < 回路）',
  },
  isEnabled: {
    label: '启用',
    readOnly: false,
    consumedByLiveEngine: true,
    note: null,
  },
  diagName: {
    label: '名称',
    readOnly: false,
    consumedByLiveEngine: true,
    note: null,
  },
};

const CONFIG_ITEM: DiagnosisConfigApi.ConfigItem = {
  diagId: 'diag-1',
  diagKey: 'OSCILLATION',
  diagName: '振荡',
  label: null,
  algorithmType: 'IAE_FFT',
  calcMethod: 'IAE_ZERO_CROSSING',
  params: { min_half_period_samples: 8 },
  threshold: { amplitude_ratio: 0.02 },
  isEnabled: true,
  algorithmVersion: 'v2',
  updatedAt: '2026-10-11T02:00:00',
  updatedBy: 'admin',
};

const mountOptions = {
  global: {
    directives: { permission: {} },
    stubs: {
      // Modal 换成渲染插槽的桩（antd Modal 默认传送 body，jsdom 内不可断言）
      AModal: { template: '<div class="amodal-stub"><slot /></div>' },
      AButton: true,
      ACard: true,
      ADescriptions: true,
      ADescriptionsItem: true,
      AEmpty: true,
      APopconfirm: true,
      ASelect: true,
      ASpin: true,
      ATable: true,
    },
  },
};

async function mountPage(
  methodMeta: null | Record<string, DiagnosisConfigApi.FieldMeta>,
) {
  getconfigsApiMock.mockResolvedValue({
    items: [CONFIG_ITEM],
    updatedCount: 1,
    methodMeta,
  });
  const wrapper = mount(ConfigPage, mountOptions);
  await new Promise((resolve) => setTimeout(resolve, 0));
  return wrapper;
}

describe('诊断配置 methodMeta 只读说明分离（P2-01 前端同步 CFG-02）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    historyApiMock.mockResolvedValue({ items: [], currentVersion: 3 });
    updateApiMock.mockResolvedValue({ items: [CONFIG_ITEM], updatedCount: 1 });
  });

  it('uT-DCM-001: methodMeta 下 readOnly 字段进入只读说明区，控件 disabled 且带"引擎未消费"标注', async () => {
    const wrapper = await mountPage(METHOD_META);
    const vm = wrapper.vm as any;

    // 逻辑层：readOnly 判定与真实可调字段分离
    expect(vm.hasReadonlySection).toBe(true);
    expect(vm.fieldReadOnly('algorithmType')).toBe(true);
    expect(vm.fieldReadOnly('calcMethod')).toBe(true);
    expect(vm.fieldReadOnly('params')).toBe(true);
    expect(vm.fieldReadOnly('threshold')).toBe(false);
    expect(vm.fieldReadOnly('isEnabled')).toBe(false);
    expect(vm.fieldReadOnly('diagName')).toBe(false);
    // 说明文案消费后端 note
    expect(String(vm.readonlyNoteText)).toContain('不消费');

    // 渲染层：打开编辑弹窗，只读说明区显式呈现
    vm.openEditModal(CONFIG_ITEM);
    await Promise.resolve();
    const text = wrapper.text();
    expect(text).toContain('注册表说明（只读）');
    expect(text).toContain('仅供参考 · 引擎未消费');
    expect(text).toContain('引擎未消费');

    // algorithmType/calcMethod 输入框 disabled；名称（真实可调）可编辑
    const disabledInputs = wrapper.findAll('input[disabled]');
    const disabledValues = disabledInputs.map((i) => (i.element as HTMLInputElement).value);
    expect(disabledValues).toContain('IAE_FFT');
    expect(disabledValues).toContain('IAE_ZERO_CROSSING');
    expect(disabledValues).not.toContain('振荡');

    // params 文本域 disabled；threshold 文本域可编辑
    const textareas = wrapper.findAll('textarea');
    expect(textareas).toHaveLength(2);
    const paramsArea = textareas.find((t) =>
      (t.element as HTMLTextAreaElement).value.includes('min_half_period_samples'),
    );
    const thresholdArea = textareas.find((t) =>
      (t.element as HTMLTextAreaElement).value.includes('amplitude_ratio'),
    );
    expect(paramsArea).toBeDefined();
    expect(thresholdArea).toBeDefined();
    expect(
      (paramsArea!.element as HTMLTextAreaElement).hasAttribute('disabled'),
    ).toBe(true);
    expect(
      (thresholdArea!.element as HTMLTextAreaElement).hasAttribute('disabled'),
    ).toBe(false);
  });

  it('uT-DCM-002: methodMeta 缺失（旧后端）→ 字段保持存量可编辑形态（向后兼容）', async () => {
    const wrapper = await mountPage(null);
    const vm = wrapper.vm as any;

    expect(vm.hasReadonlySection).toBe(false);
    expect(vm.fieldReadOnly('algorithmType')).toBe(false);

    vm.openEditModal(CONFIG_ITEM);
    await Promise.resolve();
    // 无只读说明区；算法字段可编辑（存量形态）
    expect(wrapper.text()).not.toContain('注册表说明（只读）');
    const enabledInputs = wrapper.findAll('input:not([disabled])');
    const enabledValues = enabledInputs.map(
      (i) => (i.element as HTMLInputElement).value,
    );
    expect(enabledValues).toContain('IAE_FFT');
    expect(enabledValues).toContain('IAE_ZERO_CROSSING');
  });

  it('uT-DCM-003: 保存 payload 结构不变——readOnly 字段仍随 PUT 批量更新发送', async () => {
    const wrapper = await mountPage(METHOD_META);
    const vm = wrapper.vm as any;

    vm.openEditModal(CONFIG_ITEM);
    vm.form.thresholdText = '{"amplitude_ratio": 0.03}';
    vm.form.isEnabled = false;
    await vm.handleSave();

    expect(updateApiMock).toHaveBeenCalledTimes(1);
    const [items] = updateApiMock.mock.calls[0] as [
      Array<Record<string, unknown>>,
    ];
    expect(items).toHaveLength(1);
    const item = items[0]!;
    // 结构与基线一致：diagId/algorithmType/calcMethod/params/threshold/isEnabled 全在
    expect(item.diagId).toBe('diag-1');
    expect(item.algorithmType).toBe('IAE_FFT');
    expect(item.calcMethod).toBe('IAE_ZERO_CROSSING');
    expect(item.params).toEqual({ min_half_period_samples: 8 });
    expect(item.threshold).toEqual({ amplitude_ratio: 0.03 });
    expect(item.isEnabled).toBe(false);
  });
});
