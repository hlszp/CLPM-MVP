/* eslint-disable vue/one-component-per-file -- 对账测试需多个轻量桩组件
   （Table/RadioGroup/Menu/Dropdown 等渲染桩），非产品代码拆分对象 */
/**
 * P6 Slice N：PL-01/02/06 对账与收口验证（2026-10-14）
 *
 * 与既有单测的分工（不重复覆盖）：
 * - p6-02b-drill-context.test.ts 覆盖 02b 构造点/解析/回写单点行为；
 * - wb360-route-adapter.test.ts 覆盖 Adapter 优先级矩阵与受管键往返；
 * - p6-03-info-hierarchy.test.ts 覆盖信息层次（N/E、F 参考、趋势图）；
 * - 本文件做**跨端对账**（任务书 Slice N 工作项 1/2）：
 *
 * PL-01（装置→单元→回路稳定 scope 链）：EvalRankTable 行（装置/单元两级
 * 稳定 nodeId）→ 节点回路清单（ranking API 同 scope 下推 + 范围回显）→
 * 清单行 loopId → wb360 深链（loopId+精确窗+同 nodeId scope+from）→
 * Adapter 消费还原——链上每一跳 scope 身份对账不漂移。
 *
 * PL-02（精确非整日/跨日窗+时区+指标+profile/证据往返）：发送→消费→
 * 回显→单项清除→返回恢复全链；等长比较口径（equalLengthPrevRange）。
 *
 * PL-06（页面/驾驶舱/报告导出同范围同窗同版本数字一致）：构造同一 mock
 * 数据集（node 聚合 + 两回路 + 混合算法版本快照）三端渲染断言——
 * pid-dashboard（页面）/ cockpit（EvalRankTable+EvalSummary+drill 清单）/
 * reports/performance（报表+CSV 导出）同值对账；跨版本窗口只聚合最新版本
 * （R08），导出末列带版本与旧版本排除数。
 *
 * 已登记缺口处理状态（主协调裁决：02b Owner 返修，本文件同步断言）：
 * - G-N1（已返修）：use-drill.ts pct() 对 ranking API 率字段（0–100 口径，
 *   实测 steadyRate=65.42/goodValueRate=100）不再 ×100——指标清单弹窗
 *   显示 '81.00%'，与 pid-dashboard/reports/驾驶舱热力格同数据集一致。
 * - G-N2（已返修）：装置排行行 id=int source_node_id 经 plant-node 树
 *   双身份（id+nodeId）解析为 plant_node UUID 再下推 ranking 端点
 *   （int 直推触发后端 500，浏览器/API 实证）；无映射/多映射显式失败
 *   不回退全厂。PL-01 用例树 mock 已带双身份，断言为 UUID 值。
 */
import { flushPromises, mount } from '@vue/test-utils';
import { Comment, defineComponent, h, ref } from 'vue';

import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';

// ===== hoisted 共享桩 =====
const {
  exportDataSpy,
  messageSpy,
  routeState,
  routerBack,
  routerPush,
  routerReplace,
} = vi.hoisted(() => ({
  exportDataSpy: vi.fn(),
  messageSpy: { error: vi.fn(), info: vi.fn(), success: vi.fn(), warning: vi.fn() },
  routeState: {
    fullPath: '/reports/performance',
    hash: '',
    params: {},
    query: {} as Record<string, any>,
  },
  routerBack: vi.fn(),
  routerPush: vi.fn(),
  routerReplace: vi.fn(),
}));

vi.mock('vue-router', () => ({
  useRoute: () => routeState,
  useRouter: () => ({
    back: routerBack,
    push: routerPush,
    replace: routerReplace,
  }),
}));

vi.mock('@vben/stores', () => ({
  useAccessStore: () => ({ accessCodes: ['*'], accessToken: 'test-token' }),
  useUserStore: () => ({ userInfo: { roles: ['ADMIN'], userId: 'u-1' } }),
}));

vi.mock('@vben/common-ui', () => ({
  Page: { name: 'Page', template: '<div><slot /></div>' },
}));

vi.mock('@vben/icons', () => ({
  IconifyIcon: { name: 'IconifyIcon', template: '<i />' },
}));

vi.mock('@vben/plugins/echarts', () => ({
  EchartsUI: { name: 'EchartsUI', template: '<div class="echarts-ui-stub" />' },
  useEcharts: () => ({ renderEcharts: vi.fn() }),
}));

// ant-design-vue：Table 用轻量桩真实驱动页面 #bodyCell 插槽（三端渲染断言），
// 其余组件桩化保留插槽；message 截获。
vi.mock('ant-design-vue', () => {
  const passthrough = (name: string) =>
    defineComponent({
      name,
      setup(_, { slots }) {
        return () => h('div', { class: `${name}-stub` }, slots.default?.());
      },
    });
  const TableStub = defineComponent({
    name: 'ATable',
    props: {
      columns: { type: Array, default: () => [] },
      dataSource: { type: Array, default: () => [] },
    },
    setup(props, { slots }) {
      const flat = (rows: any[]): any[] =>
        rows.flatMap((r) => [r, ...flat(r?.children ?? [])]);
      return () =>
        h(
          'div',
          { class: 'at-table-stub' },
          flat(props.dataSource as any[]).map((record, ri) =>
            h(
              'div',
              { class: 'at-tr', key: ri },
              (props.columns as any[]).map((column) => {
                const out = slots.bodyCell?.({ column, index: ri, record }) ?? [];
                const meaningful = out.filter(
                  (v) =>
                    v.type !== Comment &&
                    !(typeof v.children === 'string' && v.children.trim() === ''),
                );
                const content =
                  meaningful.length > 0
                    ? meaningful
                    : String(record[column.dataIndex as string] ?? '—');
                return h(
                  'span',
                  { class: 'at-td', 'data-col': column.key ?? column.dataIndex },
                  content as any,
                );
              }),
            ),
          ),
        );
    },
  });
  const RadioGroupStub = defineComponent({
    name: 'ARadioGroup',
    props: {
      options: { type: Array, default: () => [] },
      value: { type: String, default: '' },
    },
    emits: ['update:value'],
    setup(props, { emit }) {
      return () =>
        h(
          'div',
          { class: 'radio-group-stub' },
          (props.options as any[]).map((o) =>
            h(
              'button',
              {
                class: 'radio-opt',
                'data-value': o.value,
                onClick: () => emit('update:value', o.value),
              },
              o.label,
            ),
          ),
        );
    },
  });
  const MenuStub = defineComponent({
    name: 'AMenu',
    emits: ['click'],
    setup(_, { emit, slots }) {
      return () =>
        h(
          'div',
          { class: 'menu-stub' },
          (slots.default?.() ?? []).map((vnode: any) =>
            h(
              'div',
              {
                class: 'menu-item-stub',
                onClick: () => emit('click', { key: vnode.key }),
              },
              vnode,
            ),
          ),
        );
    },
  });
  const DropdownStub = defineComponent({
    name: 'ADropdown',
    setup(_, { slots }) {
      return () =>
        h('div', { class: 'dropdown-stub' }, [
          slots.default?.(),
          slots.overlay?.(),
        ]);
    },
  });
  const MenuWithItem = Object.assign(MenuStub, {
    Item: defineComponent({
      name: 'AMenuItem',
      setup(_, { slots }) {
        return () => h('div', { class: 'menu-item-real-stub' }, slots.default?.());
      },
    }),
  });
  return {
    Alert: passthrough('Alert'),
    Button: passthrough('Button'),
    Card: passthrough('Card'),
    DatePicker: passthrough('DatePicker'),
    Drawer: passthrough('Drawer'),
    Dropdown: DropdownStub,
    Input: passthrough('Input'),
    Menu: MenuWithItem,
    Modal: passthrough('Modal'),
    RadioGroup: RadioGroupStub,
    RangePicker: passthrough('RangePicker'),
    Select: passthrough('Select'),
    Skeleton: passthrough('Skeleton'),
    Spin: passthrough('Spin'),
    Switch: passthrough('Switch'),
    Table: TableStub,
    TabPane: passthrough('TabPane'),
    Tabs: passthrough('Tabs'),
    Tag: passthrough('Tag'),
    Textarea: passthrough('Textarea'),
    Tooltip: passthrough('Tooltip'),
    message: messageSpy,
  };
});

vi.mock('#/components/clpm', async () => {
  const bulletModule = await import('#/components/clpm/bullet-chart.vue');
  return {
    ClpmBulletChart: bulletModule.default,
    ClpmDataCanvas: defineComponent({
    name: 'ClpmDataCanvas',
    setup(_, { slots }) {
      return () => h('div', { class: 'data-canvas-stub' }, slots.default?.());
    },
  }),
  ClpmEmptyState: { name: 'ClpmEmptyState', template: '<div />' },
  ClpmPageToolbar: defineComponent({
    name: 'ClpmPageToolbar',
    setup(_, { slots }) {
      return () =>
        h('div', { class: 'page-toolbar-stub' }, [
          slots.default?.(),
          slots.actions?.(),
        ]);
    },
  }),
  ClpmStandardActions: { name: 'ClpmStandardActions', template: '<div />' },
  ClpmToolbarButton: { name: 'ClpmToolbarButton', template: '<button />' },
  };
});

vi.mock('#/components/plant-node/plant-node-tree.vue', () => ({
  default: { name: 'PlantNodeTree', template: '<div />' },
}));

vi.mock('#/views/metric/components/gate-health-panel.vue', () => ({
  default: { name: 'GateHealthPanel', template: '<div />' },
}));

// wb360 Adapter 双宿主桩（B3/B4 用例经 emit 驱动真实 Adapter 回写路径）
vi.mock('#/views/loop/workbench360/components/SingleLoopWorkbench.vue', () => ({
  default: defineComponent({
    name: 'SingleLoopWorkbench',
    props: {
      context: { type: Object, default: null },
      presentation: { type: String, default: 'page' },
    },
    emits: ['navigateRequest', 'resultChanged'],
    setup() {
      return () => h('div', { class: 'single-loop-stub' });
    },
  }),
}));
vi.mock('#/views/loop/workbench360/components/AllLoopWorkbenchHost.vue', () => ({
  default: defineComponent({
    name: 'AllLoopWorkbenchHost',
    props: {
      contextExtras: { type: Object, default: () => ({}) },
      initialLoopId: { type: String, default: null },
      section: { type: String, default: undefined },
    },
    emits: ['navigateRequest', 'resultChanged', 'selectLoop'],
    setup() {
      return () => h('div', { class: 'all-loop-host-stub' });
    },
  }),
}));

vi.mock('#/composables/use-config-access', () => ({
  useConfigAccess: () => ({ canReadConfig: ref(false) }),
}));

vi.mock('#/composables/use-clpm-theme', () => ({
  useClpmTheme: () => ({
    chartColors: ref([]),
    isDark: ref(false),
    themeColors: ref({
      DANGER: '#444',
      INFO: '#222',
      NEUTRAL: '#555',
      SUCCESS: '#111',
      WARNING: '#333',
    }),
  }),
}));

vi.mock('#/composables/use-clpm-preferences', () => ({
  usePagePreference: () => ({
    preferences: ref({}),
    updateColumns: vi.fn(),
    updateDensity: vi.fn(),
  }),
}));

vi.mock('#/composables/use-page-toolbar', () => ({
  showPageHelp: vi.fn(),
  usePageToolbar: () => ({ toolbarItems: [] }),
}));

vi.mock('#/utils/export', () => ({
  exportData: (...a: unknown[]) => exportDataSpy(...(a as [])),
}));

vi.mock('#/api/personal-work', () => ({
  claimInvestigationApi: vi.fn(),
  clearWorkResumeApi: vi.fn(),
  deleteWorkResumeApi: vi.fn(),
  extractRevisionConflict: () => null,
  getInvestigationApi: vi.fn(),
  getMyWorkApi: vi.fn(),
  getWorkResumeApi: vi.fn(async () => ({
    currentGeneration: 1,
    inaccessibleCount: 0,
    items: [],
    readAt: '',
    readStatus: 'COMPLETE',
  })),
  handoffInvestigationApi: vi.fn(),
  newRequestId: () => 'req-test',
  putWorkResumeApi: vi.fn(async () => ({
    generation: 1,
    lastVisitedAt: '',
    replayed: false,
    resourceId: 'x',
    resourceKind: 'loop',
    revision: 1,
  })),
  setLoopFollowApi: vi.fn(),
}));

// 真实 API 模块（spyOn 替换出口函数，规避 pid-dashboard 动态 import 与工厂
// mock 的竞态——沿用 p6-03 已验证模式）
import * as dashboardApi from '#/api/dashboard';
import * as loopApi from '#/api/loop';
import * as metricApi from '#/api/metric';
import * as plantNodeApi from '#/api/plant-node';
import EvalRankTable from '#/views/cockpit/wb-comps/EvalRankTable.vue';
import EvalSummary from '#/views/cockpit/wb-comps/EvalSummary.vue';
import {
  buildSourceNodeUuidIndex,
  nodeLoopsDrillTarget,
  preciseCockpitWindow,
  useCockpitDrill,
  wb360DrillQuery,
} from '#/views/cockpit/wb-comps/use-drill';
import Wb360Adapter from '#/views/loop/workbench360/index.vue';
import {
  resolveWb360Route,
  wb360ContextKey,
  wb360QueryFromContext,
  type WorkbenchContext,
} from '#/views/loop/workbench360/workbench-context';
import {
  snapshotDrillQuery,
} from '#/views/metric/components/loop-confidence-content.vue';
import {
  buildIndicatorQuery,
  equalLengthPrevRange,
  parseIndicatorQuery,
} from '#/views/metric/indicator-analysis.vue';
import PidDashboard from '#/views/metric/pid-dashboard.vue';
import { buildWorkbenchTarget } from '#/views/my-work/use-my-work';
import ReportsPerformance from '#/views/reports/performance.vue';

const TZ = Intl.DateTimeFormat().resolvedOptions().timeZone;

/* ---------------------------------------------------------------------------
 * 同一 mock 数据集（PL-06 三端对账基准）
 * - node：装置聚合（avgScore 82.34 / autoModeRate 91.23 / stabilityRate 71.56）
 * - loops：TIC-101（score 66.00 = v2 快照 [60,72] 均值；窗口内另有 1 条旧
 *   版本 v1 快照 99.99 必须被排除）、TIC-102（score 55.44）
 * ------------------------------------------------------------------------- */
const DS = {
  node: {
    accuracyRate: 80.5,
    autoModeRate: 91.23,
    avgScore: 82.34,
    effectiveAutoRate: 88.1,
    evaluatedLoops: 3,
    excludedLoops: 0,
    fastRate: null,
    goodValueRate: 98.76,
    inconclusiveLoops: 0,
    instrumentFaultRate: 4.25,
    nodeId: 'node-1',
    nodeName: '一联合',
    stabilityRate: 71.56,
    status: 'SUCCESS',
    totalLoops: 8,
  },
  version: 'algo-v2',
};
const DS_LOOPS = [
  {
    accuracyRate: 72,
    algorithmVersion: 'algo-v2',
    autoModeRate: 85,
    fitnessLevel: 'L2',
    goodValueRate: 99,
    includeInEvaluation: true,
    loopId: 'loop-001',
    loopName: '常压一回路',
    score: 66,
    steadyRate: 81,
    tagName: 'TIC-101',
    unitName: '一联合',
  },
  {
    accuracyRate: 68.5,
    algorithmVersion: 'algo-v2',
    autoModeRate: 90,
    fitnessLevel: 'L2',
    goodValueRate: 98,
    includeInEvaluation: true,
    loopId: 'loop-002',
    loopName: '常压二回路',
    score: 55.44,
    steadyRate: 63.25,
    tagName: 'TIC-102',
    unitName: '一联合',
  },
];
/** TIC-101 窗口内快照：v2 两条（60/72 → 均值 66）+ v1 旧版本一条（99.99 排除） */
function dsSnapshots() {
  const snap = (
    loop: (typeof DS_LOOPS)[number],
    score: number,
    steady: number,
    accuracy: number,
    version: string,
    ts: string,
    confidence: string,
  ) => ({
    accuracyRate: accuracy,
    algorithmVersion: version,
    autoModeRate: null,
    confidenceLevel: confidence,
    effectiveAutoRate: null,
    fastRate: null,
    goodValueRate: null,
    idealSettlingTime: null,
    loopId: loop.loopId,
    loopTagName: loop.tagName,
    oscillationRate: null,
    outputTravelIndex: null,
    saturationRate: null,
    score,
    settlingTime: null,
    steadyRate: steady,
    stictionIndex: null,
    tsStart: ts,
    validRate: null,
  });
  return [
    snap(DS_LOOPS[0]!, 60, 80, 70, 'algo-v2', '2026-10-14T01:00:00Z', 'A'),
    snap(DS_LOOPS[0]!, 72, 82, 74, 'algo-v2', '2026-10-14T02:00:00Z', 'B'),
    snap(DS_LOOPS[0]!, 99.99, 99.99, 99.99, 'algo-v1', '2026-10-14T00:00:00Z', 'A'),
    snap(DS_LOOPS[1]!, 55.44, 63.25, 68.5, 'algo-v2', '2026-10-14T01:30:00Z', 'A'),
  ];
}
function dsBoardItem() {
  return {
    ...DS.node,
    algorithmVersion: 'algo-v2',
    nodeName: '一联合',
    snapshotTime: '2026-10-14T02:00:00Z',
  };
}
function dsRankItem(loop: (typeof DS_LOOPS)[number]) {
  return {
    accuracyRate: loop.accuracyRate,
    autoModeRate: loop.autoModeRate,
    effectiveAutoRate: null,
    fastRate: null,
    fitnessLevel: loop.fitnessLevel,
    fitnessTags: null,
    goodValueRate: loop.goodValueRate,
    includeInEvaluation: true,
    loopId: loop.loopId,
    loopName: loop.loopName,
    oscillationRate: null,
    saturationRate: null,
    score: loop.score,
    steadyRate: loop.steadyRate,
    tagName: loop.tagName,
    unitName: loop.unitName,
  };
}

/** 1dp 显示精度回解析对账：显示串解析值 == 源值按该端精度舍入 */
function round1(v: number): number {
  return Math.round(v * 10) / 10;
}

/* ===========================================================================
 * PL-01：装置→单元→回路稳定 scope 链对账
 * ========================================================================= */
describe('PL-01 装置→单元→回路稳定 scope 链', () => {
  // G-N2 返修后：树 mock 带双身份（id=int source_node_id + nodeId=plant_node UUID）
  const nodeTree = [
    {
      children: [
        { id: 101, name: '常压单元', nodeId: 'uuid-unit-101', type: 'UNIT' },
        { id: 102, name: '减压单元', nodeId: 'uuid-unit-102', type: 'UNIT' },
      ],
      id: 12,
      name: '常减压装置',
      nodeId: 'uuid-area-12',
      type: 'AREA',
    },
  ] as any[];
  const plantRow = {
    alarm_count: 2,
    delta: 1.23,
    id: 12,
    join: '6/8',
    loop_count: 6,
    lose_factors: [],
    name: '常减压装置',
    overdue_tasks: 1,
    parent_name: null,
    rank: 1,
    score: 82.34,
    sparkline: [],
  };
  const unitRow = {
    alarm_count: 0,
    delta: 0.5,
    id: 101,
    loop_count: 3,
    name: '常压单元',
    parent_name: '常减压装置',
    rank: 9,
    score: 80.1,
  };
  const orphanUnit = {
    alarm_count: 0,
    delta: null,
    id: null,
    loop_count: 1,
    name: '外挂单元X',
    parent_name: '不存在装置',
    rank: 99,
    score: null,
  };
  const rankRow = (over: Record<string, unknown>) =>
    ({
      alarm_count: 0,
      delta: null,
      id: null,
      join: null,
      loop_count: 0,
      lose_factors: [],
      overdue_tasks: 0,
      parent_name: null,
      rank: 1,
      score: null,
      sparkline: [],
      ...over,
    }) as any;

  beforeEach(() => {
    setActivePinia(createPinia());
    vi.restoreAllMocks();
    routerPush.mockClear();
    routerReplace.mockClear();
  });

  it('链跳1：单元行点击 → ranking API 以该单元稳定 nodeId 过滤（非装置/非全厂）', async () => {
    vi.spyOn(metricApi, 'getRankingApi').mockResolvedValue(
      DS_LOOPS.map((l) => dsRankItem(l)) as any,
    );
    const wrapper = mount(EvalRankTable, {
      props: {
        nodeTree,
        plants: [plantRow],
        total: 8,
        units: [rankRow(unitRow), rankRow(orphanUnit)],
      },
    });
    const rows = wrapper.findAll('tbody tr');
    // 装置行 + 2 单元行（未挂载组再 +1 装置行）——找到「常压单元」行点击
    const unitTr = rows.find((tr) => tr.text().includes('常压单元'));
    expect(unitTr).toBeDefined();
    await unitTr!.trigger('click');
    await flushPromises();

    const api = metricApi.getRankingApi as any;
    expect(api).toHaveBeenCalled();
    const called = api.mock.calls[0][0];
    // scope 对账：点击单元的稳定 id 下推服务端过滤。
    // G-N2 已返修（02b Owner）：行 id=int source_node_id 经 nodeTree 双身份
    // 解析为 plant_node UUID 下推（int 直推曾触发后端 500，probe-report G-N2）。
    expect(called.plantNodeId).toBe('uuid-unit-101');
    const { list } = useCockpitDrill();
    // 回显：清单范围口径随明细展示（对账不丢参数）
    expect(list.description).toContain('常压单元');
    expect(list.description).toContain('nodeId=uuid-unit-101');
    expect(list.title).toBe('常压单元 · 回路绩效清单');
    // 链跳2：清单行携带稳定 loopId（回路级对象）
    expect(list.rows.length).toBe(2);
    expect(list.rows[0]).toMatchObject({ loopId: 'loop-001', tagName: 'TIC-101' });

    // 链跳2续：清单行点击 → 回路详情（loopId 稳定传递，不换对象）
    const { loopDetail, onListRowClick } = useCockpitDrill();
    onListRowClick(list.rows[0]!);
    expect(loopDetail.open).toBe(true);
    expect(loopDetail.loopId).toBe('loop-001');
  });

  it('链跳1补充：装置行与单元行各自稳定 nodeId 下推（两级 scope 不串）', async () => {
    vi.spyOn(metricApi, 'getRankingApi').mockResolvedValue([]);
    const wrapper = mount(EvalRankTable, {
      props: { nodeTree, plants: [plantRow], total: 8, units: [rankRow(unitRow)] },
    });
    const rows = wrapper.findAll('tbody tr');
    await rows.find((tr) => tr.text().includes('常减压装置'))!.trigger('click');
    await flushPromises();
    await rows.find((tr) => tr.text().includes('常压单元'))!.trigger('click');
    await flushPromises();
    const api = metricApi.getRankingApi as any;
    expect(api.mock.calls[0][0].plantNodeId).toBe('uuid-area-12');
    expect(api.mock.calls[1][0].plantNodeId).toBe('uuid-unit-101');
  });

  it('链跳3：回路行 → wb360 深链（loop-detail-panel 同款参数）→ Adapter 还原同一上下文', () => {
    const now = new Date('2026-10-14T08:00:00.000Z');
    // loop-detail-panel.openInWorkbench 同款：loopId + 驾驶舱当前窗精确起止 +
    // 节点 scope + from 返回来源（scope=plant_node UUID，与清单下推同源）
    const { path, query } = wb360DrillQuery(
      {
        loopId: 'loop-001',
        scope: { nodeId: 'uuid-unit-101' },
        window: preciseCockpitWindow('7d', now),
      },
      { from: '/cockpit/performance' },
    );
    expect(path).toBe('/loop/workbench360');
    const r = resolveWb360Route(query);
    expect(r.errors).toEqual([]);
    expect(r.notices).toEqual([]);
    expect(r.context).toEqual({
      loopId: 'loop-001',
      scope: { nodeId: 'uuid-unit-101' },
      window: {
        timezone: TZ,
        tsEnd: '2026-10-14T08:00:00.000Z',
        tsStart: '2026-10-07T08:00:00.000Z',
      },
    });
    expect(r.returnRef).toBe('/cockpit/performance');
    // 全链 scope 身份对账：ranking 清单下推的 UUID 与 wb360 上下文的 nodeId 同源
    const res = nodeLoopsDrillTarget(
      { id: 101, name: '常压单元' },
      buildSourceNodeUuidIndex(nodeTree),
    );
    expect(res.ok).toBe(true);
    if (res.ok) {
      expect(String(res.target.plantNodeId)).toBe(r.context!.scope!.nodeId);
    }
  });

  it('无稳定 nodeId 行（未挂载单元兜底组）不可下钻，不回退全厂口径', () => {
    const res = nodeLoopsDrillTarget(
      { id: null, name: '未挂载单元' },
      buildSourceNodeUuidIndex(nodeTree),
    );
    expect(res.ok).toBe(false);
    if (!res.ok) expect(res.reason).toContain('缺少稳定节点 ID');
    const wrapper = mount(EvalRankTable, {
      props: { nodeTree, plants: [plantRow], total: 8, units: [rankRow(orphanUnit)] },
    });
    const disabledTr = wrapper
      .findAll('tbody tr')
      .find((tr) => tr.text().includes('未挂载单元'));
    expect(disabledTr).toBeDefined();
    expect(disabledTr!.attributes('title')).toContain('缺少稳定节点 ID');
    expect(disabledTr!.attributes('title')).toContain('不回退全厂口径');
  });
});

/* ===========================================================================
 * PL-02：精确窗/时区/指标/profile/证据 往返对账 + 单项清除 + 等长比较
 * ========================================================================= */
describe('PL-02 精确窗往返（发送→消费→回显→单项清除→返回恢复）', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    routerPush.mockClear();
    routerReplace.mockClear();
    routerBack.mockClear();
  });

  it('跨日非整日精确窗双程不漂移（wb360 协议 query→context→query→context）', () => {
    // 非整日跨日窗（含毫秒）：16:30:00.500 → 后日 04:45:00.250
    const win = {
      timezone: 'Asia/Shanghai',
      tsEnd: '2026-10-12T04:45:00.250Z',
      tsStart: '2026-10-10T16:30:00.500Z',
    };
    const { query } = wb360DrillQuery(
      {
        evidenceRefs: [{ id: 'rec-9', kind: 'kpi_snapshot', version: 'v3' }],
        loopId: 'loop-1',
        profileRef: 'v3',
        scope: { nodeId: '42' },
        window: win,
      },
      { from: '/metric/indicator-analysis?metric=steady_rate' },
    );
    const r1 = resolveWb360Route(query);
    expect(r1.errors).toEqual([]);
    expect(r1.notices).toEqual([]);
    // 消费：精确起止与时区原值还原（禁近似滚动窗替换）
    expect(r1.context?.window).toEqual(win);
    expect(r1.context?.evidenceRefs).toEqual([
      { id: 'rec-9', kind: 'kpi_snapshot', version: 'v3' },
    ]);
    expect(r1.context?.profileRef).toBe('v3');
    // 回写再消费：上下文身份恒等（同回路同窗同证据=同会话）
    const q2 = wb360QueryFromContext('single', r1.context);
    const r2 = resolveWb360Route(q2);
    expect(wb360ContextKey(r2.context!)).toBe(wb360ContextKey(r1.context!));
    expect(q2.tsStart).toBe(win.tsStart);
    expect(q2.tsEnd).toBe(win.tsEnd);
    expect(q2.tz).toBe('Asia/Shanghai');
  });

  it('同一精确窗跨页同窗同口径：indicator-analysis 解析→回写往返不丢参数', () => {
    const incoming = {
      fitness: 'L1',
      from: '/metric/pid-dashboard',
      metric: 'steady_rate',
      plantNodeId: '42',
      plantNodeName: '常减压',
      tsEnd: '2026-10-12T04:45:00.250Z',
      tsStart: '2026-10-10T16:30:00.500Z',
      tz: 'Asia/Shanghai',
    };
    const opts = {
      defaultMetric: 'auto_mode_rate',
      defaultWindow: 'today',
      validMetrics: ['auto_mode_rate', 'steady_rate'],
      validWindows: ['today', 'last_8_hours', 'last_7_days'],
    };
    const s = parseIndicatorQuery(incoming, opts);
    expect(s.notices).toEqual([]);
    // 消费回显状态：精确窗 + 指标 + 节点 + fitness + from 全部就位
    expect(s.customWindow).toEqual({
      end: '2026-10-12T04:45:00.250Z',
      start: '2026-10-10T16:30:00.500Z',
      timezone: 'Asia/Shanghai',
    });
    expect(s.metric).toBe('steady_rate');
    const q = buildIndicatorQuery({
      customWindow: s.customWindow,
      fitness: s.fitness,
      fitnessFilterOn: false,
      from: s.from,
      metric: s.metric,
      plantNodeId: s.plantNodeId,
      plantNodeName: s.plantNodeName,
      window: s.window,
    });
    // 往返恒等（syncQuery 回写不丢参数；customWindow 激活不写 window 枚举）
    expect(q).toEqual(incoming);
    expect(q).not.toHaveProperty('window');
  });

  it('Adapter 单项清除：清窗口保留证据/scope/profile/from；再清 scope 其余不动；返回恢复 from', async () => {
    routeState.query = {
      from: '/reports/performance',
      loopId: 'loop-1',
      mode: 'single',
      nodeId: '42',
      profile: 'v3',
      recordId: 'rec-9',
      recordKind: 'kpi_snapshot',
      recordVersion: 'v3',
      section: 'assess',
      tsEnd: '2026-10-12T04:45:00.250Z',
      tsStart: '2026-10-10T16:30:00.500Z',
      tz: 'Asia/Shanghai',
    };
    const wrapper = mount(Wb360Adapter);
    await flushPromises();
    const single = wrapper.findComponent({ name: 'SingleLoopWorkbench' });
    expect(single.exists()).toBe(true);
    const ctx = (single.props() as any).context as WorkbenchContext;
    expect(ctx.window?.tsStart).toBe('2026-10-10T16:30:00.500Z');

    // 单项清除窗口（SingleLoopWorkbench.clearContextItem 同款 update-context）
    const { window: _w, ...noWindow } = ctx;
    single.vm.$emit('navigateRequest', { context: noWindow, target: 'update-context' });
    await flushPromises();
    expect(routerReplace).toHaveBeenCalledTimes(1);
    const q1 = routerReplace.mock.calls[0]![0].query as Record<string, string>;
    // 被清单项消失；其余携带项与 from 原样保留（未管理键不动）
    expect(q1).not.toHaveProperty('tsStart');
    expect(q1).not.toHaveProperty('tsEnd');
    expect(q1).not.toHaveProperty('tz');
    expect(q1).not.toHaveProperty('section'); // 一次性消费键随回写清除
    expect(q1.nodeId).toBe('42');
    expect(q1.recordId).toBe('rec-9');
    expect(q1.recordKind).toBe('kpi_snapshot');
    expect(q1.recordVersion).toBe('v3');
    expect(q1.profile).toBe('v3');
    expect(q1.from).toBe('/reports/performance');
    expect(q1.mode).toBe('single');
    expect(q1.loopId).toBe('loop-1');

    // 再清 scope：证据/profile/from 仍保留
    const { scope: _s, ...noScopeNoWindow } = noWindow;
    single.vm.$emit('navigateRequest', {
      context: noScopeNoWindow,
      target: 'update-context',
    });
    await flushPromises();
    const q2 = routerReplace.mock.calls[1]![0].query as Record<string, string>;
    expect(q2).not.toHaveProperty('nodeId');
    expect(q2).not.toHaveProperty('tsStart');
    expect(q2.recordId).toBe('rec-9');
    expect(q2.profile).toBe('v3');
    expect(q2.from).toBe('/reports/performance');

    // 返回恢复：from query 优先（回来源页恢复筛选现场）
    single.vm.$emit('navigateRequest', { target: 'return' });
    expect(routerPush).toHaveBeenCalledWith('/reports/performance');
  });

  it('Adapter 全模式选中回写：mode=all&loopId=…，携带窗保留、section 一次性清除、from 保留', async () => {
    routeState.query = {
      from: '/cockpit',
      mode: 'all',
      nodeId: '7',
      section: 'tuning',
      tsEnd: '2026-10-12T04:45:00.250Z',
      tsStart: '2026-10-10T16:30:00.500Z',
      tz: 'Asia/Shanghai',
    };
    const wrapper = mount(Wb360Adapter);
    await flushPromises();
    const host = wrapper.findComponent({ name: 'AllLoopWorkbenchHost' });
    expect(host.exists()).toBe(true);
    host.vm.$emit('selectLoop', 'loop-9');
    await flushPromises();
    const q = routerReplace.mock.calls[0]![0].query as Record<string, string>;
    expect(q.mode).toBe('all');
    expect(q.loopId).toBe('loop-9');
    // 携带窗口随选中保留（用户后续选中仍消费，不静默丢失）
    expect(q.tsStart).toBe('2026-10-10T16:30:00.500Z');
    expect(q.tsEnd).toBe('2026-10-12T04:45:00.250Z');
    expect(q.tz).toBe('Asia/Shanghai');
    expect(q.nodeId).toBe('7');
    // section 一次性消费：随选中回写从 URL 清除
    expect(q).not.toHaveProperty('section');
    expect(q.from).toBe('/cockpit');
  });

  it('等长比较口径：跨日非整日窗环比 [start-Δ, start]，等长且无重叠无缺口', () => {
    const start = '2026-10-10T16:30:00.500Z';
    const end = '2026-10-12T04:45:00.250Z';
    const prev = equalLengthPrevRange(start, end);
    const dur = Date.parse(end) - Date.parse(start);
    expect(Date.parse(prev.endTime) - Date.parse(prev.startTime)).toBe(dur);
    // 首尾相接：环比结束=本窗开始（无重叠无缺口）
    expect(prev.endTime).toBe(start);
    expect(prev.startTime).toBe('2026-10-09T04:15:00.750Z');
  });

  it('证据/profile 对账：snapshotDrillQuery 三元组还原；同回路异版本=异会话', () => {
    const t = snapshotDrillQuery(
      {
        algorithmVersion: 'algo-v3',
        resultRecordId: 'rec-77',
        tsEnd: '2026-10-14T00:00:00Z',
        tsStart: '2026-10-13T00:00:00Z',
      },
      { from: '/metric/loop-performance?loopId=loop-9', loopId: 'loop-9' },
    );
    const r = resolveWb360Route(t!.query);
    expect(r.context?.evidenceRefs).toEqual([
      { id: 'rec-77', kind: 'kpi_snapshot', version: 'algo-v3' },
    ]);
    expect(r.context?.profileRef).toBe('algo-v3');
    expect(r.returnRef).toBe('/metric/loop-performance?loopId=loop-9');
    // 切历史不改当前事实：同回路不同证据版本必须落不同会话
    const t2 = snapshotDrillQuery(
      {
        algorithmVersion: 'algo-v4',
        resultRecordId: 'rec-88',
        tsEnd: '2026-10-14T00:00:00Z',
        tsStart: '2026-10-13T00:00:00Z',
      },
      { loopId: 'loop-9' },
    );
    const r2 = resolveWb360Route(t2!.query);
    expect(wb360ContextKey(r2.context!)).not.toBe(wb360ContextKey(r.context!));
  });

  it('我的工作 → wb360 深链：workRef/证据/section 还原 + from 返回恢复清单位置', () => {
    const item = {
      evidenceRefs: [{ id: 'rec-1', kind: 'kpi_snapshot', version: 'v3' }],
      loopId: 'loop-1',
      sourceKind: 'handling_order',
      workId: 'order-77',
      workKind: 'ASSIGNED',
    } as any;
    const { path, query } = buildWorkbenchTarget(item, '/my-work?view=ASSIGNED');
    expect(path).toBe('/loop/workbench360');
    const r = resolveWb360Route(query);
    expect(r.errors).toEqual([]);
    expect(r.context?.workRef).toEqual({ id: 'order-77', kind: 'handling_order' });
    expect(r.context?.evidenceRefs).toEqual([
      { id: 'rec-1', kind: 'kpi_snapshot', version: 'v3' },
    ]);
    expect(r.carried.section).toBe('handling');
    // 返回恢复：回我的工作并还原清单筛选位置
    expect(r.returnRef).toBe('/my-work?view=ASSIGNED');
  });
});

/* ===========================================================================
 * PL-06：页面 / 驾驶舱 / 报告导出 同范围同窗同版本数字对账
 * ========================================================================= */
function spyDashboardApis() {
  const dash = dashboardApi as any;
  vi.spyOn(dash, 'getBoardAggregateApi').mockResolvedValue({
    aggregate: { ...DS.node },
    items: [dsBoardItem()],
    total: 1,
  });
  vi.spyOn(dash, 'getBoardTreeApi').mockResolvedValue({
    items: [
      {
        autoModeRate: DS.node.autoModeRate,
        avgScore: DS.node.avgScore,
        children: [],
        fastRate: null,
        hasSnapshot: true,
        nodeId: 'node-1',
        nodeName: '一联合',
        nodeType: 'AREA',
        stabilityRate: DS.node.stabilityRate,
        totalLoops: 8,
      },
    ],
  });
  vi.spyOn(dash, 'getBoardTrendApi').mockResolvedValue({
    autoModeRate: [90, 91, 92],
    avgScore: [80, 82, 84],
    evaluatedLoops: [3, 3, 3],
    granularity: 'hour',
    stabilityRate: [70, 71, 72],
    timestamps: [
      '2026-10-14T00:00:00',
      '2026-10-14T01:00:00',
      '2026-10-14T02:00:00',
    ],
    totalLoops: 8,
  });
  vi.spyOn(dash, 'getAutoRateRtApi').mockResolvedValue({
    autoCount: 7,
    manualCount: 1,
    modeCounts: {},
    rate: 87.5,
    readAt: '2026-10-14T02:00:00Z',
    totalCount: 8,
  });
}
function spyMetricApis() {
  const metric = metricApi as any;
  vi.spyOn(metric, 'getRankingApi').mockResolvedValue(
    DS_LOOPS.map((l) => dsRankItem(l)),
  );
  vi.spyOn(metric, 'getNodeMonitorApi').mockResolvedValue({ snapshots: [] });
  vi.spyOn(metric, 'getValveAlertsApi').mockResolvedValue({ items: [] });
  vi.spyOn(metric, 'getGradeDistributionApi').mockResolvedValue({
    latestTs: null,
    total: 0,
  });
  vi.spyOn(metric, 'getGradingThresholdsApi').mockResolvedValue({
    thresholds: [
      { level: 1, maxScore: 100, minScore: 90 },
      { level: 2, maxScore: 90, minScore: 80 },
      { level: 3, maxScore: 80, minScore: 70 },
      { level: 4, maxScore: 70, minScore: 60 },
      { level: 5, maxScore: 60, minScore: 0 },
    ],
  });
  vi.spyOn(metric, 'getLoopSnapshotsApi').mockResolvedValue({
    items: dsSnapshots(),
    total: dsSnapshots().length,
  });
}

describe('PL-06 三端同范围同窗同版本数字对账（同一 mock 数据集）', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.restoreAllMocks();
    exportDataSpy.mockClear();
    routerPush.mockClear();
    routeState.fullPath = '/reports/performance';
    routeState.query = {};
  });

  it('页面端（pid-dashboard）：同数据集三核心指标渲染与源值对账（1dp 精度）', async () => {
    spyDashboardApis();
    spyMetricApis();
    const wrapper = mount(PidDashboard);
    await flushPromises();
    await flushPromises();
    await flushPromises();
    const text = wrapper.text();
    // 首屏三核心指标卡（真实 bullet-chart 渲染：非整数 toFixed(1)）
    expect(text).toContain('82.3'); // 性能评分（aggregate.avgScore 82.34）
    expect(text).toContain('91.2'); // 自控率（91.23）
    expect(text).toContain('71.6'); // 平稳率（71.56）
    // 明细树节点行（同一 BoardTreeItem 数据集）
    expect(text).toContain('一联合');
    expect(text).toContain('91.2%');
    expect(text).toContain('71.6%');
    // 待治理 TOP 榜（同一 ranking 数据集）
    expect(text).toContain('TIC-101');
    expect(text).toContain('66.0');
    expect(text).toContain('81.0%');
    // 对账：1dp 显示值 == 源值按 1dp 舍入（无漂移）
    expect(round1(DS.node.avgScore)).toBe(82.3);
    expect(round1(DS.node.autoModeRate)).toBe(91.2);
    expect(round1(DS.node.stabilityRate)).toBe(71.6);
    expect(round1(DS_LOOPS[0]!.score)).toBe(66);
  });

  it('驾驶舱端（EvalRankTable + EvalSummary + drill 清单）：同数据集 2dp 对账', async () => {
    spyMetricApis();
    const rankWrapper = mount(EvalRankTable, {
      props: {
        nodeTree: [
          { children: [], id: 12, name: '一联合', type: 'AREA' },
        ] as any,
        plants: [
          {
            alarm_count: 0,
            delta: null,
            id: 12,
            join: '3/8',
            loop_count: 3,
            lose_factors: [],
            name: '一联合',
            overdue_tasks: 0,
            parent_name: null,
            rank: 1,
            score: DS.node.avgScore,
            sparkline: [],
          },
        ],
        total: DS.node.totalLoops,
        units: [],
      },
    });
    // 装置行评分 = 源值 toFixed(2)（与报告端字符串一致）
    expect(rankWrapper.text()).toContain('82.34');
    // 底部计数完整性：全厂合计 参评/总数 · 不可评数
    expect(rankWrapper.text()).toContain('全厂合计 3/8 参评');
    expect(rankWrapper.text()).toContain('不可评 5 条');

    const summaryWrapper = mount(EvalSummary, {
      props: {
        summary: {
          alerts_unacked: 0,
          conclusion: '',
          delta: 1.23,
          distance_to_target: 7.66,
          grade: '良好',
          participation: {
            evaluated: DS.node.evaluatedLoops,
            total: DS.node.totalLoops,
          },
          risks: [],
          score: DS.node.avgScore,
          score_state: 'OK',
          target: 90,
        } as any,
      },
    });
    expect(summaryWrapper.text()).toContain('82.34');
    expect(summaryWrapper.text()).toContain('3/8');

    // drill 回路清单（同一 ranking 数据集）：scoreText 2dp 与报告端互对
    const { drill, list } = useCockpitDrill();
    await drill({ kind: 'loops', nodeName: '一联合', plantNodeId: 12, sortOrder: 'desc' });
    expect(list.rows).toHaveLength(2);
    expect(list.rows[0]).toMatchObject({ scoreText: '66.00', tagName: 'TIC-101' });
    expect(list.rows[1]).toMatchObject({ scoreText: '55.44', tagName: 'TIC-102' });
  });

  it('报告端（reports/performance）：综合表 + 回路表（版本锁定聚合）渲染对账', async () => {
    spyDashboardApis();
    spyMetricApis();
    vi.spyOn(plantNodeApi as any, 'getPlantNodeTreeApi').mockResolvedValue([
      {
        children: [{ id: 'node-1', name: '一联合', type: 'AREA' }],
        id: 'root',
        name: '全厂',
        type: 'FACTORY',
      },
    ]);
    vi.spyOn(loopApi as any, 'getLoopListApi').mockResolvedValue({
      items: [
        { description: '常压一回路', loopId: 'loop-001' },
        { description: '常压二回路', loopId: 'loop-002' },
      ],
      total: 2,
    });
    const wrapper = mount(ReportsPerformance);
    await flushPromises();
    await flushPromises();
    // 综合报表（同一 BoardItem）：评分/自控率/平稳率 2dp 与驾驶舱端一致
    let text = wrapper.text();
    expect(text).toContain('82.34');
    expect(text).toContain('91.23%');
    expect(text).toContain('71.56%');
    expect(text).toContain('一联合');
    expect(text).toContain('良好'); // 82.34 → level 2

    // 综合报表导出：CSV 行与页面同值 + 版本列
    const csvItem = wrapper
      .findAll('.menu-item-stub')
      .find((d) => d.text().includes('CSV'));
    expect(csvItem).toBeDefined();
    await csvItem!.trigger('click');
    await flushPromises();
    expect(exportDataSpy).toHaveBeenCalledTimes(1);
    const compExport = exportDataSpy.mock.calls[0]![0] as any;
    expect(compExport.headers).toContain('性能评分');
    expect(compExport.headers).toContain('算法版本');
    const compRow = compExport.rows[0] as string[];
    expect(compRow).toContain('82.34');
    expect(compRow).toContain('91.23'); // CSV 率列裸值（表头语义）
    expect(compRow).toContain('71.56');
    expect(compRow[compRow.length - 1]).toBe('algo-v2');

    // 切回路报表（RadioGroup 桩触发 update:value=loop）
    const radio = wrapper
      .findAll('.radio-opt')
      .find((b) => b.attributes('data-value') === 'loop');
    expect(radio).toBeDefined();
    await radio!.trigger('click');
    await flushPromises();
    await flushPromises();
    text = wrapper.text();
    // 版本锁定聚合：TIC-101 = v2 两条均值 66.00，旧版本 v1 99.99 必须排除
    expect(text).toContain('TIC-101');
    expect(text).toContain('66.00');
    expect(text).toContain('81.00%');
    expect(text).toContain('TIC-102');
    expect(text).toContain('55.44');
    expect(text).not.toContain('99.99');
    // 三端互对：页面 TOP 榜 66.0 / 驾驶舱清单 66.00 / 报告 66.00 同源同值
    expect('66.00').toBe((66).toFixed(2));
    expect(round1(66)).toBe(66);

    // 回路报表导出：版本列带旧版本排除数（跨版本窗口不静默丢构成）
    exportDataSpy.mockClear();
    await csvItem!.trigger('click');
    await flushPromises();
    const loopExport = exportDataSpy.mock.calls[0]![0] as any;
    const row101 = loopExport.rows.find((r: string[]) => r.includes('TIC-101'))!;
    expect(row101).toContain('66.00');
    expect(row101[row101.length - 1]).toBe('algo-v2（不含旧版本 1 条）');
    const row102 = loopExport.rows.find((r: string[]) => r.includes('TIC-102'))!;
    expect(row102[row102.length - 1]).toBe('algo-v2');

    // 同窗同版本深链：回路行 → wb360（报表精确日窗 + profile=聚合版本 + from）
    const link = wrapper.find('.clpm-reports__loop-link');
    expect(link.exists()).toBe(true);
    await link.trigger('click');
    expect(routerPush).toHaveBeenCalledTimes(1);
    const nav = routerPush.mock.calls[0]![0] as any;
    expect(nav.path).toBe('/loop/workbench360');
    expect(nav.query.loopId).toBe('loop-001');
    expect(nav.query.profile).toBe('algo-v2'); // 同版本口径随深链
    expect(nav.query.mode).toBe('single');
    expect(nav.query.section).toBe('assess');
    expect(nav.query.from).toBe('/reports/performance');
    // 同窗：报表日窗精确起止随深链（Adapter 可原样还原）
    const r = resolveWb360Route(nav.query);
    expect(r.errors).toEqual([]);
    expect(r.context?.window?.timezone).toBe(TZ);
    expect(r.context?.profileRef).toBe('algo-v2');
    expect(r.returnRef).toBe('/reports/performance');
  });

  it('G-N1 已返修：驾驶舱指标清单率值 0–100 直拼（不再 ×100）', async () => {
    spyMetricApis();
    const { drill, list } = useCockpitDrill();
    await drill({ kind: 'metric', metric: 'steady_rate' });
    expect(list.rows).toHaveLength(2);
    // 修复后：ranking API 率字段 0–100 口径（steadyRate=81/63.25）直拼 %——
    // 与 pid-dashboard '81.0%'、reports '81.00%'、驾驶舱热力格原始值一致；
    // 修复前 pct() 再乘 100 显示 '8100.00%'/'6325.00%'（缺口证据见 Slice N §5）。
    expect(list.rows[0]).toMatchObject({ metricText: '81.00%' });
    expect(list.rows[1]).toMatchObject({ metricText: '63.25%' });
    // 对账基准：同数据集他端显示 81.0% / 81.00%（三端一致）
    expect(round1(DS_LOOPS[0]!.steadyRate)).toBe(81);
  });
});
