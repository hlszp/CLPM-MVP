import { flushPromises, mount } from '@vue/test-utils';
import { reactive } from 'vue';

import { beforeEach, describe, expect, it, vi } from 'vitest';

import TuningSimulation from '../views/tuning/simulation.vue';

const routeQuery = reactive<Record<string, string>>({});
const simulateTuningApiMock = vi.fn();
const comparePidsApiMock = vi.fn();
const createTuningTaskApiMock = vi.fn();

vi.mock('vue-router', () => ({
  useRoute: () => ({ query: routeQuery }),
}));

vi.mock('#/api/tuning', () => ({
  comparePidsApi: (...args: unknown[]) => comparePidsApiMock(...args),
  createTuningTaskApi: (...args: unknown[]) => createTuningTaskApiMock(...args),
  simulateTuningApi: (...args: unknown[]) => simulateTuningApiMock(...args),
}));

// Phase D：simulation.vue 新增 store 调用（simulationResult 同步）
const tuningStoreMock = {
  simulationResult: null,
  currentStep: 0,
};
vi.mock('#/store/tuning', () => ({
  useTuningStore: () => tuningStoreMock,
}));

vi.mock('@vben/common-ui', () => ({
  Page: { template: '<main><slot /></main>' },
}));

vi.mock('@vben/icons', () => ({
  IconifyIcon: {
    props: ['icon'],
    template: '<span class="icon-stub">{{ icon }}</span>',
  },
}));

vi.mock('@vben/plugins/echarts', () => ({
  EchartsUI: { template: '<div class="echarts-stub" />' },
  useEcharts: () => ({ renderEcharts: vi.fn() }),
}));

vi.mock('#/components/clpm', () => ({
  ClpmDataCanvas: {
    props: ['title', 'description'],
    template: '<section><h2>{{ title }}</h2><slot /></section>',
  },
  ClpmObjectSummaryBar: {
    props: ['title', 'primaryItem', 'items', 'actions'],
    emits: ['action'],
    template: '<div class="summary-stub">{{ title }}</div>',
  },
  ClpmPageToolbar: {
    props: ['subtitle', 'title', 'loading', 'lastRefresh'],
    template:
      '<header>{{ title }}{{ subtitle }}<slot /><slot name="actions" /></header>',
  },
  ClpmStateOverlay: {
    props: ['status', 'emptyDescription', 'errorMessage', 'errorDetail'],
    emits: ['retry'],
    template: `<div class="state-overlay-stub" :data-status="status">
      <span v-if="status === 'empty'" class="overlay-empty">{{ emptyDescription }}</span>
      <span v-else-if="status === 'error'" class="overlay-error">{{ errorMessage }}{{ errorDetail }}</span>
      <slot v-else />
      <button v-if="status === 'error'" class="overlay-retry" @click="$emit('retry')">重试</button>
    </div>`,
  },
  ClpmToolbarButton: {
    props: ['icon', 'label', 'variant', 'loading', 'disabled'],
    emits: ['click'],
    template:
      '<button :disabled="disabled" @click="$emit(\'click\')">{{ label }}</button>',
  },
}));

vi.mock('#/composables/use-clpm-theme', () => ({
  useClpmTheme: () => ({
    isDark: { value: false },
    themeColors: {
      value: {
        DANGER: '#f43f5e',
        INFO: '#3b82f6',
        NEUTRAL: '#9ca3af',
        SUCCESS: '#10b981',
        WARNING: '#f59e0b',
      },
    },
    chartTextColor: { value: '#666' },
  }),
}));

vi.mock('ant-design-vue', () => ({
  Alert: {
    props: ['description', 'message', 'type'],
    template:
      '<div class="alert-stub" :data-type="type">{{ message }}{{ description }}<slot /></div>',
  },
  Button: {
    emits: ['click'],
    props: ['disabled', 'type', 'size', 'danger', 'block'],
    template:
      '<button :disabled="disabled" @click="$emit(\'click\')"><slot /></button>',
  },
  Card: {
    props: ['title', 'size'],
    template: '<div>{{ title }}<slot /></div>',
  },
  Descriptions: { template: '<dl><slot /></dl>' },
  DescriptionsItem: {
    props: ['label'],
    template: '<div><dt>{{ label }}</dt><dd><slot /></dd></div>',
  },
  Form: { template: '<form><slot /></form>' },
  FormItem: {
    props: ['label'],
    template: '<div class="form-item-stub" :data-label="label"><slot /></div>',
  },
  Input: {
    emits: ['update:value'],
    props: ['placeholder', 'value'],
    template:
      '<input :placeholder="placeholder" :value="value ?? \'\'" @input="$emit(\'update:value\', $event.target.value)" />',
  },
  InputNumber: {
    emits: ['update:value'],
    props: ['min', 'max', 'placeholder', 'step', 'value'],
    template:
      '<input :min="min" :placeholder="placeholder" :value="value ?? \'\'" @input="$emit(\'update:value\', $event.target.value === \'\' ? null : Number($event.target.value))" />',
  },
  Select: {
    emits: ['update:value', 'change'],
    props: ['options', 'value'],
    template: `
      <div class="select-stub">
        <button
          v-for="option in options || []"
          :key="option.value"
          :data-value="option.value"
          type="button"
          @click="$emit('update:value', option.value)"
        >
          {{ option.label }}
        </button>
      </div>
    `,
  },
  Spin: { template: '<div class="spin-stub"><slot /></div>' },
  Switch: {
    emits: ['update:checked', 'change'],
    props: ['checked'],
    template:
      '<input type="checkbox" class="switch-stub" :checked="checked" @change="$emit(\'update:checked\', $event.target.checked); $emit(\'change\', $event.target.checked)" />',
  },
  Table: {
    props: ['columns', 'dataSource', 'pagination'],
    template: '<table><slot /></table>',
  },
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

/** a-switch / a-input 为全局注册组件，需 stub */
const switchStub = {
  emits: ['update:checked', 'change'],
  props: ['checked'],
  template:
    '<input type="checkbox" class="switch-stub" :checked="checked" @change="$emit(\'update:checked\', $event.target.checked); $emit(\'change\', $event.target.checked)" />',
};

const inputStub = {
  emits: ['update:value'],
  props: ['placeholder', 'value'],
  template:
    '<input :placeholder="placeholder" :value="value ?? \'\'" @input="$emit(\'update:value\', $event.target.value)" />',
};

function mountSim() {
  return mount(TuningSimulation, {
    global: { stubs: { 'a-switch': switchStub, 'a-input': inputStub } },
  });
}

function makeSimulationResult() {
  return {
    currentMetrics: {
      riseTime: 10,
      overshoot: 5,
      settlingTime: 30,
      itae: 1000,
    },
    currentResponse: { op: [1, 1], pv: [0, 1] },
    improvement: {
      riseTime: 0.1,
      overshoot: 0.2,
      settlingTime: 0.15,
      itae: 0.05,
    },
    recommendedMetrics: {
      riseTime: 8,
      overshoot: 3,
      settlingTime: 25,
      itae: 800,
    },
    recommendedResponse: { op: [1, 1], pv: [0, 1], sp: [0, 1] },
    timestamps: [0, 1],
  };
}

describe('tuningSimulation 状态覆盖（V62-P1-023）', () => {
  beforeEach(() => {
    setQuery({
      modelParams: JSON.stringify({ K: 1, tau: 10, theta: 2 }),
      modelType: 'FOPDT',
    });
    simulateTuningApiMock.mockReset();
    comparePidsApiMock.mockReset();
    createTuningTaskApiMock.mockReset();
    tuningStoreMock.simulationResult = null;
    simulateTuningApiMock.mockResolvedValue(makeSimulationResult());
  });

  it('empty：初次进入无结果时显示空状态覆盖', async () => {
    const wrapper = mountSim();
    await flushPromises();

    const emptyOverlay = wrapper.find('.overlay-empty');
    expect(emptyOverlay.exists()).toBe(true);
    expect(emptyOverlay.text()).toContain(
      '请配置模型与 PID 参数后点击「运行仿真」',
    );
  });

  it('error：双 PID 仿真失败时显示错误覆盖和详情', async () => {
    simulateTuningApiMock.mockRejectedValue(new Error('模型参数无效'));

    const wrapper = mountSim();
    await flushPromises();

    await findButton(wrapper, '运行仿真')!.trigger('click');
    await flushPromises();

    const errorOverlay = wrapper.find('.overlay-error');
    expect(errorOverlay.exists()).toBe(true);
    expect(errorOverlay.text()).toContain('闭环仿真失败');
    expect(errorOverlay.text()).toContain('模型参数无效');
  });

  it('error：多 PID 对比仿真失败时显示错误覆盖', async () => {
    comparePidsApiMock.mockRejectedValue(new Error('候选 PID 参数不足'));

    const wrapper = mountSim();
    await flushPromises();

    // 切换到多 PID 对比模式
    await wrapper.find('.switch-stub').setValue(true);
    await flushPromises();

    await findButton(wrapper, '运行仿真')!.trigger('click');
    await flushPromises();

    const errorOverlay = wrapper.find('.overlay-error');
    expect(errorOverlay.exists()).toBe(true);
    expect(errorOverlay.text()).toContain('多 PID 对比仿真失败');
    expect(errorOverlay.text()).toContain('候选 PID 参数不足');
  });

  it('error→retry：点击重试按钮重新触发仿真', async () => {
    simulateTuningApiMock.mockRejectedValueOnce(new Error('首次仿真失败'));

    const wrapper = mountSim();
    await flushPromises();

    await findButton(wrapper, '运行仿真')!.trigger('click');
    await flushPromises();

    expect(wrapper.find('.overlay-error').exists()).toBe(true);
    expect(simulateTuningApiMock).toHaveBeenCalledTimes(1);

    // 点击重试
    const retryButton = wrapper.find('.overlay-retry');
    expect(retryButton.exists()).toBe(true);
    await retryButton.trigger('click');
    await flushPromises();

    expect(simulateTuningApiMock).toHaveBeenCalledTimes(2);
  });

  it('success：仿真成功后错误覆盖消失，图表正常展示', async () => {
    const wrapper = mountSim();
    await flushPromises();

    await findButton(wrapper, '运行仿真')!.trigger('click');
    await flushPromises();

    expect(wrapper.find('.overlay-error').exists()).toBe(false);
    expect(wrapper.find('.overlay-empty').exists()).toBe(false);
    expect(wrapper.find('.echarts-stub').exists()).toBe(true);
  });

  it('reset：重置参数后清除错误状态和结果', async () => {
    simulateTuningApiMock.mockRejectedValue(new Error('仿真失败'));

    const wrapper = mountSim();
    await flushPromises();

    await findButton(wrapper, '运行仿真')!.trigger('click');
    await flushPromises();

    expect(wrapper.find('.overlay-error').exists()).toBe(true);

    await findButton(wrapper, '重置')!.trigger('click');
    await flushPromises();

    expect(wrapper.find('.overlay-error').exists()).toBe(false);
    expect(wrapper.find('.overlay-empty').exists()).toBe(true);
  });
});
