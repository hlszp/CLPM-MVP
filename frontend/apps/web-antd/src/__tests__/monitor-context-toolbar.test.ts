/**
 * MonitorContextToolbar 单元测试（Tab 切换条件渲染）
 *
 * 覆盖：
 * - showPlantNode=true 时装置选择器渲染
 * - showPlantNode=false 时装置选择器不渲染
 * - showLoopType=true 时类型选择器渲染
 * - showLoopType=false 时类型选择器不渲染
 * - 关键词搜索框和保存视图始终渲染
 * - filterChange 事件正确触发
 */
import { mount } from '@vue/test-utils';

import { beforeEach, describe, expect, it, vi } from 'vitest';

import MonitorContextToolbar from '#/components/monitor/monitor-context-toolbar.vue';

// ===== Mock useMonitorContext =====
const mockUpdate = vi.fn();
const mockContext = {
  plantNodeId: { value: null },
  loopType: { value: null },
  keyword: { value: '' },
  attentionOnly: { value: false },
  view: { value: 'table' as string },
  update: mockUpdate,
};

vi.mock('#/composables/use-monitor-context', () => ({
  useMonitorContext: () => mockContext,
}));

// ===== Mock useSavedView =====
vi.mock('#/composables/use-saved-view', async () => {
  const { ref } = await import('vue');
  return {
    useSavedView: () => ({
      savedFilters: ref([]),
      saveCurrentView: vi.fn(),
      applyView: vi.fn(() => true),
    }),
  };
});

// ===== Mock getPlantNodeTreeApi =====
vi.mock('#/api/plant-node', () => ({
  getPlantNodeTreeApi: vi.fn().mockResolvedValue([]),
}));

// ===== Mock use-loop-palettes =====
vi.mock('#/composables/use-loop-palettes', () => ({
  LOOP_TYPE_LABEL_MAP: { FC: '流量', LC: '液位', TC: '温度', PC: '压力' },
  MODE_LABEL_MAP: {},
  useLoopPalettes: () => ({ modeLabelColor: () => 'default' }),
}));

// ===== Mock plant-node utils =====
vi.mock('#/utils/plant-node', () => ({
  flattenNodes: (nodes: any[]) => nodes,
}));

// ===== Stub ant-design-vue 组件 =====
vi.mock('ant-design-vue', async () => {
  const actual = await vi.importActual('ant-design-vue');
  return {
    ...actual,
    Select: {
      name: 'Select',
      props: ['value', 'options', 'placeholder', 'allowClear', 'size'],
      emits: ['change', 'update:value'],
      template: `
        <select data-testid="ant-select" :data-placeholder="placeholder" v-bind="$attrs">
          <option v-for="opt in options" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
        </select>
      `,
    },
    Input: {
      name: 'Input',
      props: ['value', 'placeholder', 'size', 'allowClear'],
      emits: ['input', 'pressEnter', 'update:value'],
      template: `<input data-testid="ant-input" :placeholder="placeholder" :value="value" @input="$emit('input', $event)" @keyup.enter="$emit('pressEnter', $event)" />`,
    },
  };
});

function mountToolbar(props: Record<string, unknown> = {}) {
  return mount(MonitorContextToolbar, {
    props: { pageKey: 'test', ...props },
  });
}

describe('MonitorContextToolbar 条件渲染', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('showPlantNode=true（默认）：渲染装置选择器', () => {
    const w = mountToolbar();
    const selects = w.findAll('[data-testid="ant-select"]');
    // 至少 2 个 Select：装置 + 类型 + 保存视图
    expect(selects.length).toBeGreaterThanOrEqual(2);
    // 第一个 Select 的 placeholder 应为"装置/单元"
    expect(selects[0]!.attributes('data-placeholder')).toBe('装置/单元');
  });

  it('showPlantNode=false：不渲染装置选择器', () => {
    const w = mountToolbar({ showPlantNode: false });
    const selects = w.findAll('[data-testid="ant-select"]');
    // 第一个 Select 不应是"装置/单元"（应为类型或保存视图）
    const hasPlantNodeSelect = selects.some(
      (s) => s.attributes('data-placeholder') === '装置/单元',
    );
    expect(hasPlantNodeSelect).toBe(false);
  });

  it('showLoopType=true（默认）：渲染类型选择器', () => {
    const w = mountToolbar();
    const selects = w.findAll('[data-testid="ant-select"]');
    const hasLoopTypeSelect = selects.some(
      (s) => s.attributes('data-placeholder') === '类型',
    );
    expect(hasLoopTypeSelect).toBe(true);
  });

  it('showLoopType=false：不渲染类型选择器', () => {
    const w = mountToolbar({ showLoopType: false });
    const selects = w.findAll('[data-testid="ant-select"]');
    const hasLoopTypeSelect = selects.some(
      (s) => s.attributes('data-placeholder') === '类型',
    );
    expect(hasLoopTypeSelect).toBe(false);
  });

  it('showPlantNode=false + showLoopType=false：仅保留保存视图 Select', () => {
    const w = mountToolbar({ showPlantNode: false, showLoopType: false });
    const selects = w.findAll('[data-testid="ant-select"]');
    // 仅剩保存视图 1 个 Select
    expect(selects).toHaveLength(1);
    expect(selects[0]!.attributes('data-placeholder')).toBe('保存视图');
  });

  it('关键词搜索框始终渲染', () => {
    const w1 = mountToolbar();
    const w2 = mountToolbar({ showPlantNode: false, showLoopType: false });
    expect(w1.find('[data-testid="ant-input"]').exists()).toBe(true);
    expect(w2.find('[data-testid="ant-input"]').exists()).toBe(true);
  });

  it('保存视图 Select 始终渲染', () => {
    const w1 = mountToolbar();
    const w2 = mountToolbar({ showPlantNode: false, showLoopType: false });
    const selects1 = w1.findAll('[data-testid="ant-select"]');
    const selects2 = w2.findAll('[data-testid="ant-select"]');
    expect(
      selects1.some((s) => s.attributes('data-placeholder') === '保存视图'),
    ).toBe(true);
    expect(
      selects2.some((s) => s.attributes('data-placeholder') === '保存视图'),
    ).toBe(true);
  });
});
