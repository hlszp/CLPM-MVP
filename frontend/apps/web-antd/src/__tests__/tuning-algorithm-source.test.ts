import { flushPromises, mount } from '@vue/test-utils';
import { reactive } from 'vue';

import { beforeEach, describe, expect, it, vi } from 'vitest';

import TuningAlgorithm from '../views/tuning/algorithm.vue';

const routeQuery = reactive<Record<string, string>>({});
const routerPushMock = vi.fn();
const getTuningMethodsApiMock = vi.fn();
const tunePidApiMock = vi.fn();
const createTuningTaskApiMock = vi.fn();

vi.mock('vue-router', () => ({
  useRoute: () => ({ query: routeQuery }),
  useRouter: () => ({ push: routerPushMock }),
}));

vi.mock('#/api/tuning', () => ({
  createTuningTaskApi: (...args: unknown[]) =>
    createTuningTaskApiMock(...args),
  getTuningMethodsApi: (...args: unknown[]) =>
    getTuningMethodsApiMock(...args),
  tunePidApi: (...args: unknown[]) => tunePidApiMock(...args),
}));

vi.mock('@vben/common-ui', () => ({
  Page: { template: '<main><slot /></main>' },
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
      '<label><input data-testid="manual-risk-confirmation" type="checkbox" :checked="checked" @change="$emit(\'update:checked\', $event.target.checked)" /><slot /></label>',
  },
  Collapse: {
    props: ['bordered'],
    template: '<div class="collapse-stub"><slot /></div>',
  },
  CollapsePanel: {
    props: ['header', 'key'],
    template: '<div class="collapse-panel-stub"><div>{{ header }}</div><slot /></div>',
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
    // 导致 Select 选项点击被首个 option 二次触发而重置 v-model。
    template: '<div class="form-item-stub" :data-label="label"><slot /></div>',
  },
  InputNumber: {
    emits: ['update:value'],
    props: ['disabled', 'value'],
    template:
      '<input :disabled="disabled" :value="value ?? \'\'" @input="$emit(\'update:value\', Number($event.target.value))" />',
  },
  Modal: { confirm: vi.fn() },
  Select: {
    emits: ['change', 'update:value'],
    props: ['disabled', 'options', 'value'],
    template: `
      <div class="select-stub" :data-disabled="disabled">
        <button
          v-for="option in options || []"
          :key="option.value"
          :data-value="option.value"
          type="button"
          @click="$emit('update:value', option.value); $emit('change', option.value)"
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

function setQuery(query: Record<string, string>) {
  for (const key of Object.keys(routeQuery)) delete routeQuery[key];
  Object.assign(routeQuery, query);
}

function findButton(wrapper: ReturnType<typeof mount>, text: string) {
  return wrapper.findAll('button').find((node) => node.text().includes(text));
}

describe('TuningAlgorithm 模型来源门禁', () => {
  beforeEach(() => {
    setQuery({
      loopId: 'loop-1',
      modelParams: JSON.stringify({ K: 1.2, tau: 30, theta: 2 }),
      modelType: 'FOPDT',
    });
    routerPushMock.mockReset();
    getTuningMethodsApiMock.mockReset();
    tunePidApiMock.mockReset();
    createTuningTaskApiMock.mockReset();
    getTuningMethodsApiMock.mockResolvedValue([
      {
        applicableModel: 'FOPDT',
        code: 'IMC',
        description: '测试算法',
        name: 'IMC',
        params: [],
      },
    ]);
    tunePidApiMock.mockResolvedValue({
      algorithm: 'IMC',
      algorithmVersion: 'v1',
      recommendedPid: { kp: 1, ti: 10, td: 0 },
    });
  });

  it('缺少可信模型来源时禁用执行并解释原因', async () => {
    const wrapper = mount(TuningAlgorithm);
    await flushPromises();

    expect(wrapper.text()).toContain('必须明确模型来源');
    const tuneButton = findButton(wrapper, '执行整定');
    expect(tuneButton).toBeDefined();
    expect(tuneButton!.attributes('disabled')).toBeDefined();
    expect(tunePidApiMock).not.toHaveBeenCalled();
  });

  it('手工模型必须显式选择 MANUAL 并确认风险后才调用整定', async () => {
    const wrapper = mount(TuningAlgorithm);
    await flushPromises();

    const sourceSelector = wrapper
      .findAll('.select-stub')
      .find((node) => node.find('[data-value="MANUAL"]').exists());
    expect(sourceSelector).toBeDefined();
    await sourceSelector!.get('[data-value="MANUAL"]').trigger('click');
    await flushPromises();

    const tuneButton = findButton(wrapper, '执行整定');
    expect(tuneButton!.attributes('disabled')).toBeDefined();
    await wrapper
      .get('[data-testid="manual-risk-confirmation"]')
      .setValue(true);
    expect(tuneButton!.attributes('disabled')).toBeUndefined();

    await tuneButton!.trigger('click');
    await flushPromises();
    expect(tunePidApiMock).toHaveBeenCalledWith(
      expect.objectContaining({
        modelSource: 'MANUAL',
        riskConfirmed: true,
      }),
    );
    expect(tunePidApiMock.mock.calls[0]?.[0]).not.toHaveProperty(
      'sourceRecordId',
    );
  });

  it('辨识记录来源贯穿整定请求和推荐仿真路由', async () => {
    setQuery({
      loopId: 'loop-1',
      modelParams: JSON.stringify({ K: 1.2, tau: 30, theta: 2 }),
      modelSource: 'IDENTIFICATION_RECORD',
      modelType: 'FOPDT',
      riskConfirmed: 'true',
      sourceRecordId: 'record-001',
    });
    const wrapper = mount(TuningAlgorithm);
    await flushPromises();

    const tuneButton = findButton(wrapper, '执行整定');
    expect(tuneButton!.attributes('disabled')).toBeUndefined();
    await tuneButton!.trigger('click');
    await flushPromises();

    expect(tunePidApiMock).toHaveBeenCalledWith(
      expect.objectContaining({
        modelSource: 'IDENTIFICATION_RECORD',
        riskConfirmed: true,
        sourceRecordId: 'record-001',
      }),
    );

    await findButton(wrapper, '进行闭环仿真')!.trigger('click');
    expect(routerPushMock).toHaveBeenCalledWith({
      path: '/tuning/flow/simulation',
      query: expect.objectContaining({
        modelSource: 'IDENTIFICATION_RECORD',
        riskConfirmed: 'true',
        sourceRecordId: 'record-001',
      }),
    });
  });
});
