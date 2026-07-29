import type { TuningApi } from '../api/tuning';

import { flushPromises, mount } from '@vue/test-utils';
import { nextTick, reactive } from 'vue';

import { beforeEach, describe, expect, expectTypeOf, it, vi } from 'vitest';

import TuningModel from '../views/tuning/model.vue';

const getLoopListApiMock = vi.fn();
const identifyModelApiMock = vi.fn();
const previewSegmentsApiMock = vi.fn();
const routerPushMock = vi.fn();
const submitIdentifyMock = vi.fn();

const tuningStore = reactive({
  identifyResult: null as null | TuningApi.IdentifyHistoryResult,
  setCurrentLoop: vi.fn(),
  setModelSource: vi.fn(),
  setLoopTimeRange: vi.fn(),
  currentStep: 0,
  // P1-021：回路/时间窗由 flow 统一上下文头写入 store，model.vue 从 store 读取
  currentLoopId: 'loop-1',
  currentLoopTagName: 'FIC-101',
  currentLoopTimeRange: null as null | [string, string],
  startPolling: vi.fn(),
  submitIdentify: submitIdentifyMock,
  taskProgress: null as null | TuningApi.TaskProgress,
});

vi.mock('#/api/loop', () => ({
  getLoopListApi: (...args: unknown[]) => getLoopListApiMock(...args),
}));

vi.mock('#/api/tuning', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/tuning')>();
  return {
    ...actual,
    identifyModelApi: (...args: unknown[]) => identifyModelApiMock(...args),
    previewSegmentsApi: (...args: unknown[]) => previewSegmentsApiMock(...args),
  };
});

vi.mock('#/store/tuning', () => ({
  useTuningStore: () => tuningStore,
}));

vi.mock('vue-router', () => ({
  useRouter: () => ({
    push: routerPushMock,
  }),
}));

vi.mock('@vben/common-ui', () => ({
  Page: { template: '<main><slot /></main>' },
}));

vi.mock('@vben/plugins/echarts', () => ({
  EchartsUI: { template: '<div />' },
  useEcharts: () => ({
    renderEcharts: vi.fn(),
  }),
}));

vi.mock('#/components/clpm', () => ({
  ClpmDataCanvas: {
    props: ['title'],
    template: '<section><h2>{{ title }}</h2><slot /></section>',
  },
  ClpmPageToolbar: {
    props: ['subtitle', 'title'],
    template: '<header>{{ title }}{{ subtitle }}<slot /></header>',
  },
}));

vi.mock('#/components/metric/confidence-badge.vue', () => ({
  default: {
    props: ['level'],
    template: '<span>{{ level }}</span>',
  },
}));

vi.mock('#/composables/use-clpm-theme', () => ({
  useClpmTheme: () => ({
    isDark: { value: false },
    themeColors: {
      value: {
        DANGER: '#f43f5e',
        INFO: '#3b82f6',
        SUCCESS: '#10b981',
        WARNING: '#f59e0b',
      },
    },
  }),
}));

// P1-022：默认以 ADMIN 身份测试，高级参数可见
vi.mock('#/composables/use-clpm-roles', () => ({
  useClpmRoles: () => ({
    canEditAdvancedParams: { value: true },
  }),
}));

vi.mock('ant-design-vue', () => ({
  Alert: {
    props: ['description', 'message'],
    template:
      '<div class="alert-stub">{{ message }}{{ description }}<slot name="description" /></div>',
  },
  Button: {
    emits: ['click'],
    props: ['disabled'],
    template:
      '<button :disabled="disabled" @click="$emit(\'click\')"><slot /></button>',
  },
  Checkbox: {
    emits: ['update:checked'],
    props: ['checked'],
    template:
      '<label><input data-testid="risk-confirmation" type="checkbox" :checked="checked" @change="$emit(\'update:checked\', $event.target.checked)" /><slot /></label>',
  },
  Card: {
    props: ['title'],
    template: '<div>{{ title }}<slot /></div>',
  },
  Collapse: {
    props: ['bordered'],
    template: '<div class="collapse-stub"><slot /></div>',
  },
  CollapsePanel: {
    props: ['header', 'key'],
    template: '<div class="collapse-panel-stub"><div>{{ header }}</div><slot /></div>',
  },
  DatePicker: {
    RangePicker: { template: '<div />' },
  },
  Descriptions: { template: '<dl><slot /></dl>' },
  DescriptionsItem: {
    props: ['label'],
    template: '<div><dt>{{ label }}</dt><dd><slot /></dd></div>',
  },
  Form: { template: '<form><slot /></form>' },
  FormItem: {
    props: ['label'],
    // 用 div 而非 label：label 会把对内部按钮的点击隐式转发给首个可标记后代，
    // 导致 Select 选项点击被首个 option（如 AUTO）二次触发而重置 v-model。
    template: '<div class="form-item-stub" :data-label="label"><slot /></div>',
  },
  InputNumber: {
    emits: ['update:value'],
    props: ['min', 'placeholder', 'value'],
    template:
      '<input :min="min" :placeholder="placeholder" :value="value ?? \'\'" @input="$emit(\'update:value\', $event.target.value === \'\' ? null : Number($event.target.value))" />',
  },
  Progress: { template: '<div />' },
  Select: {
    emits: ['update:value'],
    props: ['mode', 'options', 'value'],
    template: `
      <div class="select-stub" :data-mode="mode || 'single'">
        <button
          v-for="option in options || []"
          :key="option.value"
          class="option-stub"
          :data-value="option.value"
          type="button"
          @click="$emit('update:value', option.value)"
        >
          {{ option.label }}
        </button>
      </div>
    `,
  },
  Spin: { template: '<div><slot /></div>' },
  Tag: { template: '<span><slot /></span>' },
  message: {
    error: vi.fn(),
    info: vi.fn(),
    loading: vi.fn(() => vi.fn()),
    success: vi.fn(),
    warning: vi.fn(),
  },
}));

function mountModel() {
  return mount(TuningModel);
}

function makeHistoryResult(
  overrides: Partial<TuningApi.IdentifyHistoryResult> = {},
): TuningApi.IdentifyHistoryResult {
  return {
    algorithmVersion: 'TUNE_IDENT_v1.0',
    confidenceLevel: 'A',
    dataPoints: 720,
    fittingScore: 78.5,
    identifyMethod: 'HISTORICAL_ARX',
    modelType: 'FOPDT',
    params: { K: 1.2, tau: 30, theta: 2 },
    recordId: 'record-001',
    success: true,
    thetaSource: 'EXPLICIT',
    ...overrides,
  };
}

function findButtonByText(
  wrapper: ReturnType<typeof mountModel>,
  text: string,
) {
  return wrapper.findAll('button').find((node) => node.text().includes(text));
}

describe('TuningModel Phase 0 历史辨识边界', () => {
  beforeEach(() => {
    getLoopListApiMock.mockReset();
    identifyModelApiMock.mockReset();
    previewSegmentsApiMock.mockReset();
    routerPushMock.mockReset();
    submitIdentifyMock.mockReset();
    tuningStore.identifyResult = null;
    tuningStore.taskProgress = null;
    tuningStore.currentLoopId = 'loop-1';
    tuningStore.currentLoopTagName = 'FIC-101';
    tuningStore.currentLoopTimeRange = null;
    tuningStore.setCurrentLoop.mockReset();
    tuningStore.setLoopTimeRange.mockReset();
    tuningStore.startPolling.mockReset();

    getLoopListApiMock.mockResolvedValue({
      items: [{ loopId: 'loop-1', tagName: 'FIC-101' }],
    });
    submitIdentifyMock.mockResolvedValue('task-12345678');
  });

  it('历史候选排除 IPDT，阶跃实验仍保留 IPDT', async () => {
    const wrapper = mountModel();
    await flushPromises();

    const historicalSelector = wrapper.get('[data-mode="multiple"]');
    expect(historicalSelector.find('[data-value="IPDT"]').exists()).toBe(false);
    expect(historicalSelector.find('[data-value="FOPDT"]').exists()).toBe(true);
    expect(historicalSelector.find('[data-value="SOPDT"]').exists()).toBe(true);

    const strategySelector = wrapper
      .findAll('.select-stub')
      .find((node) => node.find('[data-value="STEP_ONLY"]').exists());
    expect(strategySelector).toBeDefined();
    await strategySelector!.get('[data-value="STEP_ONLY"]').trigger('click');
    await nextTick();

    const stepModelSelector = wrapper
      .findAll('.select-stub')
      .find((node) => node.find('[data-value="IPDT"]').exists());
    expect(stepModelSelector).toBeDefined();
    expect(stepModelSelector!.text()).toContain('IPDT 积分加纯滞后');
  });

  it('透传可选的非负 theta 预估值', async () => {
    const wrapper = mountModel();
    await flushPromises();

    const thetaInput = wrapper.get('[data-label="纯滞后预估 θ (秒)"] input');
    expect(thetaInput.attributes('min')).toBe('0');
    await thetaInput.setValue('12.5');

    const identifyButton = wrapper
      .findAll('button')
      .find((node) => node.text().includes('开始辨识'));
    expect(identifyButton).toBeDefined();
    await identifyButton!.trigger('click');
    await flushPromises();

    expect(submitIdentifyMock).toHaveBeenCalledWith(
      expect.objectContaining({
        candidateModelTypes: ['FOPDT', 'SOPDT'],
        thetaEstimate: 12.5,
      }),
    );
  });

  it('2Ts 启发结果明确提示可信度上限并禁止直接整定', async () => {
    tuningStore.identifyResult = makeHistoryResult({
      thetaSource: 'HEURISTIC_2TS',
    });

    const wrapper = mountModel();
    await flushPromises();

    expect(wrapper.text()).toContain('纯滞后采用 2Ts 启发值');
    expect(wrapper.text()).toContain('可信度最高为 C');
    expect(wrapper.text()).toContain('不可直接用于整定');

    expect(findButtonByText(wrapper, '使用此模型进行整定')).toBeUndefined();
    expect(findButtonByText(wrapper, '进行闭环仿真')).toBeUndefined();
  });

  it.each(['A', 'B'] as const)(
    '可信度 %s 且记录可追溯时可进入整定，并携带来源契约',
    async (confidenceLevel) => {
      tuningStore.identifyResult = makeHistoryResult({ confidenceLevel });

      const wrapper = mountModel();
      await flushPromises();

      const tuningButton = findButtonByText(wrapper, '使用此模型进行整定');
      expect(tuningButton).toBeDefined();
      expect(tuningButton!.attributes('disabled')).toBeUndefined();
      await tuningButton!.trigger('click');

      expect(routerPushMock).toHaveBeenCalledWith({
        path: '/tuning/flow/algorithm',
        query: expect.objectContaining({
          modelSource: 'IDENTIFICATION_RECORD',
          sourceRecordId: 'record-001',
        }),
      });
      expect(routerPushMock.mock.calls[0]?.[0]?.query).not.toHaveProperty(
        'riskConfirmed',
      );
    },
  );

  it('可信度 C 必须人工确认后才携带 riskConfirmed=true', async () => {
    tuningStore.identifyResult = makeHistoryResult({ confidenceLevel: 'C' });

    const wrapper = mountModel();
    await flushPromises();

    expect(wrapper.text()).toContain('可信度 C：需人工风险确认');
    const tuningButton = findButtonByText(wrapper, '使用此模型进行整定');
    expect(tuningButton).toBeDefined();
    expect(tuningButton!.attributes('disabled')).toBeDefined();

    await wrapper.get('[data-testid="risk-confirmation"]').setValue(true);
    await nextTick();
    expect(tuningButton!.attributes('disabled')).toBeUndefined();
    await tuningButton!.trigger('click');

    expect(routerPushMock).toHaveBeenCalledWith({
      path: '/tuning/flow/algorithm',
      query: expect.objectContaining({
        modelSource: 'IDENTIFICATION_RECORD',
        riskConfirmed: 'true',
        sourceRecordId: 'record-001',
      }),
    });
  });

  it.each([
    ['可信度 D', { confidenceLevel: 'D' as const }, '可信度 D'],
    ['可信度 E', { confidenceLevel: 'E' as const }, '可信度 E'],
    [
      'INCONCLUSIVE',
      { confidenceLevel: 'INCONCLUSIVE' as const, success: false },
      'INCONCLUSIVE',
    ],
    [
      '实验性 IV',
      {
        confidenceLevel: 'A' as const,
        identifyMethod: 'HISTORICAL_IV' as const,
      },
      'IV',
    ],
    ['缺少记录 ID', { confidenceLevel: 'A' as const, recordId: null }, '记录'],
  ])('%s 不提供整定或推荐仿真入口', async (_name, overrides, reason) => {
    tuningStore.identifyResult = makeHistoryResult(overrides);

    const wrapper = mountModel();
    await flushPromises();

    expect(findButtonByText(wrapper, '使用此模型进行整定')).toBeUndefined();
    expect(findButtonByText(wrapper, '进行闭环仿真')).toBeUndefined();
    expect(wrapper.text()).toContain(reason);
  });

  it('仅通过单阶跃验证的 STEP_ONLY 结果可进入受控来源流程', async () => {
    identifyModelApiMock.mockResolvedValue({
      algorithmVersion: 'TUNE_v1.0',
      dataPoints: 120,
      fittingScore: 88,
      modelType: 'IPDT',
      params: { K: 1.1, theta: 2 },
      recordId: 'step-record-001',
      stepValidationPassed: true,
    });

    const wrapper = mountModel();
    await flushPromises();
    const strategySelector = wrapper
      .findAll('.select-stub')
      .find((node) => node.find('[data-value="STEP_ONLY"]').exists());
    await strategySelector!.get('[data-value="STEP_ONLY"]').trigger('click');
    await nextTick();

    const identifyButton = findButtonByText(wrapper, '开始辨识');
    await identifyButton!.trigger('click');
    await flushPromises();

    const tuningButton = findButtonByText(wrapper, '使用此模型进行整定');
    expect(tuningButton).toBeDefined();
    await tuningButton!.trigger('click');
    expect(routerPushMock).toHaveBeenCalledWith({
      path: '/tuning/flow/algorithm',
      query: expect.objectContaining({
        modelSource: 'STEP_EXPERIMENT',
        sourceRecordId: 'step-record-001',
      }),
    });
    expect(routerPushMock.mock.calls[0]?.[0]?.query).not.toHaveProperty(
      'stepValidationPassed',
    );
  });

  it('未带单阶跃验证凭据的 STEP_ONLY 结果不提供后续入口', async () => {
    identifyModelApiMock.mockResolvedValue({
      algorithmVersion: 'TUNE_v1.0',
      dataPoints: 120,
      fittingScore: 88,
      modelType: 'FOPDT',
      params: { K: 1.1, tau: 20, theta: 2 },
      recordId: 'step-record-002',
    });

    const wrapper = mountModel();
    await flushPromises();
    const strategySelector = wrapper
      .findAll('.select-stub')
      .find((node) => node.find('[data-value="STEP_ONLY"]').exists());
    await strategySelector!.get('[data-value="STEP_ONLY"]').trigger('click');
    await nextTick();
    await findButtonByText(wrapper, '开始辨识')!.trigger('click');
    await flushPromises();

    expect(findButtonByText(wrapper, '使用此模型进行整定')).toBeUndefined();
    expect(findButtonByText(wrapper, '进行闭环仿真')).toBeUndefined();
    expect(wrapper.text()).toContain('未通过受控单阶跃验证');
  });

  it('受控 STEP_ONLY 结果缺少记录 ID 时仍不提供后续入口', async () => {
    identifyModelApiMock.mockResolvedValue({
      algorithmVersion: 'TUNE_v1.0',
      dataPoints: 120,
      fittingScore: 88,
      modelType: 'FOPDT',
      params: { K: 1.1, tau: 20, theta: 2 },
      stepValidationPassed: true,
    });

    const wrapper = mountModel();
    await flushPromises();
    const strategySelector = wrapper
      .findAll('.select-stub')
      .find((node) => node.find('[data-value="STEP_ONLY"]').exists());
    await strategySelector!.get('[data-value="STEP_ONLY"]').trigger('click');
    await nextTick();
    await findButtonByText(wrapper, '开始辨识')!.trigger('click');
    await flushPromises();

    expect(findButtonByText(wrapper, '使用此模型进行整定')).toBeUndefined();
    expect(findButtonByText(wrapper, '进行闭环仿真')).toBeUndefined();
    expect(wrapper.text()).toContain('记录 ID');
  });

  it('API 类型收紧历史候选并声明 theta 来源', () => {
    expectTypeOf<
      TuningApi.IdentifyHistoryRequest['candidateModelTypes']
    >().toEqualTypeOf<Array<'FOPDT' | 'SOPDT'> | undefined>();
    expectTypeOf<
      TuningApi.IdentifyHistoryResult['thetaSource']
    >().toEqualTypeOf<'EXPLICIT' | 'HEURISTIC_2TS' | null | undefined>();
    expectTypeOf<TuningApi.ModelSource>().toEqualTypeOf<
      'IDENTIFICATION_RECORD' | 'MANUAL' | 'STEP_EXPERIMENT'
    >();
  });
});
