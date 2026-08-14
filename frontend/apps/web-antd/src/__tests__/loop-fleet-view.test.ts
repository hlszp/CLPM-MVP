/**
 * LoopFleetView 单元测试（表格列增强 — 导航驱动模式）
 *
 * 覆盖：
 * - 默认排序：评分升序（最差在前）
 * - 列顺序符合 02 标杆 v1.4 的 12 列清单（无操作列）
 * - 量程/单位列默认收起
 * - 列标题使用标杆命名
 */
import { mount } from '@vue/test-utils';

import { beforeEach, describe, expect, it, vi } from 'vitest';

// ===== Mock 数据 =====
const mockMonitorItems = [
  {
    loopId: 'loop-1',
    tagName: 'LIC-101',
    description: '常压塔液位',
    unitName: '常减压·常压塔',
    loopType: 'LC',
    score: 92.5,
    scoreDelta: 1.2,
    dayTrend: 'IMPROVED',
    confidenceLevel: 'A',
    currentValues: {
      sp: 50,
      pv: 49.8,
      op: 45.2,
      mode: 'AUTO',
      modeLabel: '自动',
    },
    pvRange: { min: 0, max: 100 },
    pvUnit: '%',
  },
  {
    loopId: 'loop-2',
    tagName: 'FIC-205',
    description: '进料流量',
    unitName: '催化裂化·反应区',
    loopType: 'FC',
    score: 45.3,
    scoreDelta: -3.1,
    dayTrend: 'WORSENED',
    confidenceLevel: 'C',
    currentValues: {
      sp: 80,
      pv: 82.5,
      op: 55.0,
      mode: 'MAN',
      modeLabel: '手动',
    },
    pvRange: { min: 0, max: 200 },
    pvUnit: 't/h',
  },
  {
    loopId: 'loop-3',
    tagName: 'TIC-308',
    description: '反应温度',
    unitName: '加氢精制·反应器',
    loopType: 'TC',
    score: 75.8,
    scoreDelta: 0,
    dayTrend: 'FLAT',
    confidenceLevel: 'B',
    currentValues: {
      sp: 350,
      pv: 348,
      op: 30,
      mode: 'CAS',
      modeLabel: '串级',
    },
    pvRange: { min: 0, max: 500 },
    pvUnit: '℃',
  },
] as any[];

// ===== Mock API =====
const getLoopMonitorListApiMock = vi.fn().mockResolvedValue({
  items: mockMonitorItems,
  total: mockMonitorItems.length,
});

vi.mock('#/api/loop', () => ({
  getLoopMonitorListApi: (...args: unknown[]) =>
    getLoopMonitorListApiMock(...(args as [])),
  getLoopTypeStatsApi: vi.fn().mockResolvedValue({ LC: 1, FC: 1, TC: 1 }),
}));

vi.mock('#/api/diagnosis', () => ({
  getDiagnosisListApi: vi.fn().mockResolvedValue({ items: [] }),
}));

// ===== Mock composables =====
vi.mock('#/composables/use-monitor-context', () => ({
  useMonitorContext: () => ({
    plantNodeId: { value: null },
    loopType: { value: null },
    keyword: { value: '' },
    attentionOnly: { value: false },
    view: { value: 'table' },
    update: vi.fn(),
  }),
}));

vi.mock('#/composables/use-clpm-preferences', () => ({
  usePagePreference: () => ({
    preferences: { value: { columns: [], density: 'middle' } },
    updateColumns: vi.fn(),
  }),
}));

vi.mock('#/composables/use-saved-view', () => ({
  useSavedView: () => ({
    savedFilters: { value: [] },
    applyFilter: vi.fn(),
    saveCurrentFilter: vi.fn(),
    deleteFilter: vi.fn(),
  }),
}));

vi.mock('#/composables/use-table-density', () => ({
  useTableDensity: () => ({
    tableSize: { value: 'middle' },
    densityLabel: { value: '中等' },
    cycleDensity: vi.fn(),
  }),
}));

vi.mock('#/composables/use-loop-palettes', () => ({
  LOOP_TYPE_LABEL_MAP: { FC: '流量', LC: '液位', TC: '温度', PC: '压力' },
  MODE_LABEL_MAP: { AUTO: '自动', CAS: '串级', MAN: '手动' },
  useLoopPalettes: () => ({ modeLabelColor: () => 'default' }),
}));

vi.mock('#/composables/use-loop-realtime', () => ({
  useLoopRealtime: () => ({
    applyMessage: vi.fn(),
    connectionStatus: { value: 'online' },
    lastMessageAt: { value: null },
    onMessage: vi.fn(),
    start: vi.fn(),
    startFallback: vi.fn(),
    stop: vi.fn(),
    stopFallback: vi.fn(),
  }),
}));

vi.mock('#/constants/diagnosis', () => ({
  DIAGNOSIS_LABEL_COLOR_MAP: {},
  getDiagnosisLabelName: (label: string) => label,
}));

// ===== Stub ant-design-vue 组件（使用 vi.hoisted 确保 mock 提升后可用）=====
const { TableStub } = vi.hoisted(() => ({
  TableStub: {
    name: 'Table',
    props: [
      'columns',
      'dataSource',
      'loading',
      'pagination',
      'size',
      'scroll',
      'rowKey',
      'rowClassName',
    ],
    emits: ['change'],
    template: `
      <div data-testid="ant-table">
        <div class="table-headers">
          <span v-for="col in columns" :key="col.key" :data-col-key="col.key">{{ col.title }}</span>
        </div>
        <div v-for="record in dataSource" :key="record.loopId" :data-loop-id="record.loopId" class="table-row">
          <span data-field="tagName">{{ record.tagName }}</span>
          <span data-field="score">{{ record.score ?? '—' }}</span>
        </div>
      </div>
    `,
  },
}));

vi.mock('ant-design-vue', async () => {
  const actual = await vi.importActual('ant-design-vue');
  return {
    ...actual,
    Table: TableStub,
    Button: {
      name: 'Button',
      props: ['type', 'size'],
      template: '<button data-testid="ant-button"><slot /></button>',
    },
    Card: { template: '<div data-testid="ant-card"><slot /></div>' },
    Switch: { template: '<input type="checkbox" />' },
    Tag: {
      props: ['color'],
      template:
        '<span data-testid="ant-tag" :data-color="color"><slot /></span>',
    },
    message: { warning: vi.fn(), success: vi.fn() },
  };
});

// ===== Mock vue-router =====
vi.mock('vue-router', () => ({
  useRoute: () => ({ query: {} }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

import LoopFleetView from '#/components/monitor/loop-fleet-view.vue';

function mountFleetView(props: Record<string, unknown> = {}) {
  return mount(LoopFleetView, {
    props: { showStats: false, showAutoRefresh: false, ...props },
    global: {
      stubs: {
        ClpmNumeric: {
          props: ['value', 'precision', 'mono', 'size', 'weight'],
          template: '<span data-testid="clpm-numeric">{{ value }}</span>',
        },
        DayDeltaBadge: {
          props: ['delta', 'trend'],
          template: '<span data-testid="day-delta" />',
        },
        MonitorContextToolbar: {
          template: '<div data-testid="monitor-context-toolbar" />',
        },
      },
    },
  });
}

describe('LoopFleetView 表格列增强', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getLoopMonitorListApiMock.mockResolvedValue({
      items: [...mockMonitorItems],
      total: mockMonitorItems.length,
    });
  });

  it('列顺序符合 02 标杆 v1.4 的 12 列清单（MVP 移除诊断标签列）', async () => {
    const w = mountFleetView();
    await vi.dynamicImportSettled();

    const headers = w.findAll('.table-headers span');
    const keys = headers.map((h) => h.attributes('data-col-key'));

    const expectedKeys = [
      'tagName',
      'description',
      'unitName',
      'loopType',
      'grade',
      'score',
      'sp',
      'pv',
      'op',
      'mode',
      'dataHealth',
    ];
    expect(keys).toEqual(expectedKeys);
  });

  it('量程/单位列默认收起（不在可见列中）', async () => {
    const w = mountFleetView();
    await vi.dynamicImportSettled();

    const headers = w.findAll('.table-headers span');
    const keys = headers.map((h) => h.attributes('data-col-key'));

    expect(keys).not.toContain('pvRange');
    expect(keys).not.toContain('pvUnit');
  });

  it('默认排序：评分升序（最差在前）', async () => {
    getLoopMonitorListApiMock.mockResolvedValue({
      items: [...mockMonitorItems],
      total: 3,
    });

    const w = mountFleetView();
    await vi.dynamicImportSettled();

    const rows = w.findAll('.table-row');
    // 排序后应为：45.3（loop-2）→ 75.8（loop-3）→ 92.5（loop-1）
    expect(rows[0]!.attributes('data-loop-id')).toBe('loop-2');
    expect(rows[1]!.attributes('data-loop-id')).toBe('loop-3');
    expect(rows[2]!.attributes('data-loop-id')).toBe('loop-1');
  });

  it('列标题使用标杆命名', async () => {
    const w = mountFleetView();
    await vi.dynamicImportSettled();

    const headerTexts = w
      .findAll('.table-headers span')
      .map((h) => h.text());

    expect(headerTexts).toContain('描述');
    expect(headerTexts).toContain('装置·单元');
    expect(headerTexts).toContain('回路类型');
    expect(headerTexts).toContain('回路等级');
    expect(headerTexts).toContain('性能评分');
    expect(headerTexts).toContain('MODE');
    expect(headerTexts).toContain('可信度');
  });
});
