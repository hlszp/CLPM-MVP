/**
 * P6-03 工业信息层次（03§9 验收行 + §8 PL-03/04/05、A20）测试
 *
 * 覆盖：
 * - EvalTrendChart：Y 轴 0–100 固定定义域（低分 10/40 与 90 真实绘制）、
 *   真实时间轴（x=时间线性映射）、缺口不连线伪连续（时间缺口分段 +
 *   null 断开 + 孤立点画圆点）、--ck-* 主题变量、键盘焦点路径、截至时间；
 * - pid-dashboard：首屏常驻 N/E 与来源字段绑定（roster.scorableLoopCount/
 *   eligibleLoopCount，非 evaluatedLoops 冒充）、N<E 状态原因、F 参考同显块
 *   （均分/参考数量/动态证据不足/动态估计覆盖/来源，不冒充正式 F/P）、
 *   趋势图 series 断言（名册断点 null + connectNulls:false）、无名册诚实态；
 * - AssessSection：评估任务终态区分（取消≠失败）+ 陈腐"待 G2"脚注清除；
 * - task/detail：CANCELLED 进度条不再渲染 active（排队/运行/失败/取消区分）。
 *
 * 注：pid-dashboard 的 API 全部走动态 import——vitest 对同一工厂 mock 模块
 * 的并发动态 import 存在竞态（一个解析到 mock、另一个挂起或返回真实模块，
 * 本 Slice 实测复现），故 '#/api/dashboard' 与 '#/api/metric' 一律静态引入
 * 真实模块后 vi.spyOn 替换出口函数，不做模块级替换。
 */
import { flushPromises, mount, shallowMount } from '@vue/test-utils';

import { beforeEach, describe, expect, it, vi } from 'vitest';

// ===== 共享 hoisted 桩 =====
const { drillSpy, echartsCalls, runnerState, taskDetailMock } = vi.hoisted(
  () => ({
    drillSpy: vi.fn(),
    echartsCalls: [] as any[],
    runnerState: {
      error: null as null | string,
      isRunning: false,
      mode: null,
      progress: null as null | number,
      stage: null as null | string,
      taskId: null,
    },
    taskDetailMock: vi.fn(),
  }),
);

vi.mock('vue-router', () => ({
  useRoute: () => ({ fullPath: '/metric/pid-dashboard', params: {}, query: {} }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

vi.mock('#/views/cockpit/wb-comps/use-drill', () => ({
  canOpenWorkbench360: () => false,
  localTimezone: () => 'Asia/Shanghai',
  useCockpitDrill: () => ({ drill: drillSpy }),
  wb360DrillQuery: () => ({ path: '/loop/workbench360', query: {} }),
}));

vi.mock('#/api/task', () => ({
  cancelTaskApi: vi.fn(),
  getTaskDetailApi: (...args: unknown[]) => taskDetailMock(...(args as [])),
  getTaskNotificationsApi: vi.fn().mockResolvedValue({ items: [] }),
  markNotificationReadApi: vi.fn(),
}));

vi.mock('@vben/plugins/echarts', () => ({
  EchartsUI: { name: 'EchartsUI', template: '<div class="echarts-ui-stub" />' },
  useEcharts: () => ({
    renderEcharts: (opt: any) => echartsCalls.push(opt),
  }),
}));

vi.mock('@vben/stores', () => ({
  useAccessStore: () => ({ accessCodes: [], accessToken: null }),
  useUserStore: () => ({ userInfo: { roles: [] } }),
}));

vi.mock('@vben/icons', () => ({
  IconifyIcon: { name: 'IconifyIcon', template: '<i />' },
}));

vi.mock('@vben/common-ui', () => ({
  Page: { name: 'Page', template: '<div><slot /></div>' },
}));

vi.mock('#/components/clpm', () => ({
  ClpmBulletChart: { name: 'ClpmBulletChart', template: '<div />' },
  ClpmEmptyState: { name: 'ClpmEmptyState', template: '<div />' },
  ClpmPageToolbar: { name: 'ClpmPageToolbar', template: '<div><slot /></div>' },
  ClpmStandardActions: { name: 'ClpmStandardActions', template: '<div />' },
}));

vi.mock('#/components/plant-node/plant-node-tree.vue', () => ({
  default: { name: 'PlantNodeTree', template: '<div />' },
}));

vi.mock('#/views/metric/components/gate-health-panel.vue', () => ({
  default: { name: 'GateHealthPanel', template: '<div />' },
}));

vi.mock('#/composables/use-config-access', () => ({
  useConfigAccess: () => ({ canReadConfig: { value: false } }),
}));

vi.mock('ant-design-vue', async () => {
  const actual = await vi.importActual('ant-design-vue');
  return {
    ...actual,
    message: { error: vi.fn(), success: vi.fn(), warning: vi.fn() },
  };
});

// wb360 AssessSection 依赖桩（剖面壳 composable 不在本 Slice 范围，整体替换）
vi.mock('#/views/loop/workbench360/composables/use-assess-history', async () => {
  const { ref } = await import('vue');
  return {
    deriveLossSummary: () => null,
    useInjectedAssessHistory: () => ({
      error: ref(null),
      latest: ref(null),
      loadHistory: vi.fn(),
      loading: ref(false),
      page: ref(1),
      pageSize: 20,
      rows: ref([]),
      sourceFilter: ref('all'),
      total: ref(0),
    }),
  };
});

vi.mock('#/views/loop/workbench360/composables/use-assess-runner', () => ({
  useAssessRunner: () => ({ state: runnerState, stop: vi.fn(), trigger: vi.fn() }),
}));

vi.mock('#/views/loop/components/assess-trigger-modal.vue', () => ({
  default: { name: 'AssessTriggerModal', template: '<div />' },
}));
vi.mock('#/views/metric/components/loop-confidence-content.vue', () => ({
  default: { name: 'LoopConfidenceContent', template: '<div />' },
}));
vi.mock('#/views/metric/components/loop-performance-detail-content.vue', () => ({
  default: { name: 'LoopPerformanceDetailContent', template: '<div />' },
}));
vi.mock('#/views/loop/workbench360/components/WbDrawer.vue', () => ({
  default: { name: 'WbDrawer', template: '<div />' },
}));

// 真实 API 模块（spyOn 替换出口函数，规避动态 import 工厂 mock 竞态）
import * as dashboardApi from '#/api/dashboard';
import * as metricApi from '#/api/metric';
import EvalTrendChart from '#/views/cockpit/wb-comps/EvalTrendChart.vue';
import AssessSection from '#/views/loop/workbench360/components/sections/AssessSection.vue';
import PidDashboard from '#/views/metric/pid-dashboard.vue';
import TaskDetail from '#/views/task/detail.vue';

// ---------------------------------------------------------------------------
// EvalTrendChart
// ---------------------------------------------------------------------------

/** 构造 ScoreTrendPoint（h=当日小时；v 传 null 表示缺口点） */
function pt(day: string, h: number, v: null | number) {
  return {
    t: `${day}T${String(h).padStart(2, '0')}:00:00`,
    v,
  } as { t: string; v: number };
}

function makeTrend(
  current: { t: string; v: number }[],
  extra: Record<string, unknown> = {},
) {
  return {
    data_quality: [],
    level_dist: [],
    mode_dist: [],
    series: { current, previous: [] },
    snapshot_at: '2026-10-13T05:00:00',
    slopes: [],
    target: 90,
    ...extra,
  } as any;
}

/** 主线段（stroke-width=2）path 的 d 串 */
function mainLinePaths(wrapper: ReturnType<typeof mount>) {
  return wrapper
    .findAll('path')
    .filter((p) => p.attributes('stroke-width') === '2')
    .map((p) => p.attributes('d') ?? '');
}

/** 解析 "M<x> <y> L<x> <y> …" 为坐标序列 */
function pathCoords(d: string): { x: number; y: number }[] {
  const nums = d
    .replaceAll(/[ML]\s*/g, ' ')
    .trim()
    .split(/\s+/)
    .map(Number);
  const out: { x: number; y: number }[] = [];
  for (let i = 0; i + 1 < nums.length; i += 2) {
    out.push({ x: nums[i] ?? 0, y: nums[i + 1] ?? 0 });
  }
  return out;
}

describe('EvalTrendChart（P6-03：真实时间轴/缺口/0–100/主题/键盘）', () => {
  beforeEach(() => {
    drillSpy.mockClear();
  });

  it('Y 轴固定 0–100：刻度含 0/20/…/100，目标线 90 在定义域内', () => {
    const wrapper = mount(EvalTrendChart, {
      props: {
        trend: makeTrend([pt('2026-10-13', 0, 80), pt('2026-10-13', 1, 82)]),
      },
    });
    const tickTexts = wrapper.findAll('text').map((t) => t.text());
    for (const v of ['0', '20', '40', '60', '80', '100']) {
      expect(tickTexts).toContain(v);
    }
  });

  it('低分 10/40 与 90 同尺真实绘制：y(90)<y(40)<y(10) 且不钳平', () => {
    // 旧实现 yMin=78 会把 10/40 都钳到基线同一点（伪高分）——回归断言三点互异
    const wrapper = mount(EvalTrendChart, {
      props: {
        trend: makeTrend([
          pt('2026-10-13', 0, 10),
          pt('2026-10-13', 1, 40),
          pt('2026-10-13', 2, 90),
        ]),
      },
    });
    const lines = mainLinePaths(wrapper);
    expect(lines).toHaveLength(1);
    const coords = pathCoords(lines[0]!);
    expect(coords).toHaveLength(3);
    const [y10, y40, y90] = coords.map((c) => c.y);
    // SVG y 向下为正：高分在上方（y 小）
    expect(y90).toBeLessThan(y40!);
    expect(y40).toBeLessThan(y10!);
    // 无钳平：三个 y 两两互异（旧实现 10/40 同 y）
    expect(new Set([y10, y40, y90]).size).toBe(3);
  });

  it('真实时间轴缺口不连线：空 2 桶断为两段，x 间距=真实时间比', () => {
    // 00:00/01:00 有值，02:00/03:00 空桶（后端跳过），04:00/05:00 有值
    const wrapper = mount(EvalTrendChart, {
      props: {
        trend: makeTrend([
          pt('2026-10-13', 0, 80),
          pt('2026-10-13', 1, 80),
          pt('2026-10-13', 4, 82),
          pt('2026-10-13', 5, 82),
        ]),
      },
    });
    const lines = mainLinePaths(wrapper);
    // 缺口不连线伪连续：断为两段（旧索引均布实现只有一条穿越缺口的线）
    expect(lines).toHaveLength(2);
    const seg1 = pathCoords(lines[0]!);
    const seg2 = pathCoords(lines[1]!);
    const x0 = seg1[0]!.x;
    const x1 = seg1[seg1.length - 1]!.x;
    const x4 = seg2[0]!.x;
    const x5 = seg2[seg2.length - 1]!.x;
    const span = x5 - x0;
    expect(span).toBeGreaterThan(0);
    // x 按真实时间映射：01:00 位于全程 1/5 处，缺口 01:00→04:00 占 3/5
    expect((x1 - x0) / span).toBeCloseTo(1 / 5, 2);
    expect((x4 - x1) / span).toBeCloseTo(3 / 5, 2);
  });

  it('null 点断开且不画作值：两个孤立单点渲染为圆点，无连线', () => {
    const wrapper = mount(EvalTrendChart, {
      props: {
        trend: makeTrend([
          pt('2026-10-13', 0, 80),
          pt('2026-10-13', 1, null),
          pt('2026-10-13', 2, 82),
        ]),
      },
    });
    expect(mainLinePaths(wrapper)).toHaveLength(0);
    // 孤立单点画圆点（有数据但不可连线，不消失也不伪连线）
    expect(wrapper.findAll('circle').length).toBe(2);
  });

  it('上一周期真实序列分段渲染（灰虚线），不派生伪造', () => {
    const current = [
      pt('2026-10-13', 0, 80),
      pt('2026-10-13', 1, 81),
      pt('2026-10-13', 2, 82),
    ];
    const previous = [pt('2026-10-12', 0, 70), pt('2026-10-12', 6, 74)];
    const wrapper = mount(EvalTrendChart, {
      props: {
        trend: makeTrend(current, { series: { current, previous } }),
      },
    });
    const prevPaths = wrapper
      .findAll('path')
      .filter((p) => p.attributes('stroke-dasharray') === '4 4');
    expect(prevPaths.length).toBe(1);
  });

  it('主题变量：SVG 色码走 --ck-*（无硬编码 #2563EB/#8A94A6）', () => {
    const wrapper = mount(EvalTrendChart, {
      props: {
        trend: makeTrend([pt('2026-10-13', 0, 80), pt('2026-10-13', 1, 82)]),
      },
    });
    const html = wrapper.html();
    expect(html).not.toContain('#2563EB');
    expect(html).not.toContain('#8A94A6');
    expect(html).not.toContain('#52C41A');
    expect(html).not.toContain('#FF4D4F');
    const mainPaths = wrapper
      .findAll('path')
      .filter((p) => p.attributes('style')?.includes('--ck-accent-2'));
    expect(mainPaths.length).toBeGreaterThan(0);
  });

  it('键盘焦点路径：图表区 role=button tabindex=0，Enter 触发下钻', async () => {
    const wrapper = mount(EvalTrendChart, {
      props: {
        trend: makeTrend([pt('2026-10-13', 0, 80), pt('2026-10-13', 1, 82)]),
      },
    });
    const region = wrapper.find('[role="button"][tabindex="0"]');
    expect(region.exists()).toBe(true);
    await region.trigger('keydown', { key: 'Enter' });
    expect(drillSpy).toHaveBeenCalledWith(
      expect.objectContaining({ kind: 'trend' }),
    );
  });

  it('截至时间：snapshot_at 显式展示在头部', () => {
    const wrapper = mount(EvalTrendChart, {
      props: {
        trend: makeTrend([pt('2026-10-13', 0, 80), pt('2026-10-13', 1, 82)]),
      },
    });
    expect(wrapper.find('[data-testid="trend-asof"]').text()).toContain('截至');
  });

  it('G-V1 返修：tz-aware snapshot_at（+00:00 / +08:00 / Z）截至正确渲染', () => {
    // G5 库 snapshot_at 为 timestamptz，API 实返带 '+00:00' 后缀——
    // 旧 parseTs 恒拼 'Z' 生成 Invalid Date 致「截至」静默不渲染
    const naiveRef = mount(EvalTrendChart, {
      props: {
        trend: makeTrend([pt('2026-10-13', 0, 80), pt('2026-10-13', 1, 82)], {
          snapshot_at: '2026-10-13T05:00:00',
        }),
      },
    });
    const refText = naiveRef.find('[data-testid="trend-asof"]').text();
    expect(refText).toContain('截至');

    // 三种 tz 形态与 naive（视作 UTC）表示同一时刻 → 渲染文本必须一致
    for (const s of [
      '2026-10-13T05:00:00+00:00',
      '2026-10-13T05:00:00.643274+00:00', // G5 实返形态（微秒+偏移）
      '2026-10-13T05:00:00Z',
      '2026-10-13T13:00:00+08:00',
    ]) {
      const wrapper = mount(EvalTrendChart, {
        props: {
          trend: makeTrend([pt('2026-10-13', 0, 80), pt('2026-10-13', 1, 82)], {
            snapshot_at: s,
          }),
        },
      });
      const asof = wrapper.find('[data-testid="trend-asof"]');
      expect(asof.exists(), `snapshot_at=${s} 应渲染截至`).toBe(true);
      expect(asof.text(), `snapshot_at=${s} 时刻应与 naive UTC 一致`).toBe(
        refText,
      );
    }
  });

  it('G-V1 返修：非法 snapshot_at 显式不渲染且不崩溃', () => {
    const wrapper = mount(EvalTrendChart, {
      props: {
        trend: makeTrend([pt('2026-10-13', 0, 80), pt('2026-10-13', 1, 82)], {
          snapshot_at: 'not-a-date',
        }),
      },
    });
    expect(wrapper.find('[data-testid="trend-asof"]').exists()).toBe(false);
    // 图本体正常渲染（缺陷仅限截至行，不扩散）
    expect(mainLinePaths(wrapper)).toHaveLength(1);
  });
});

// ---------------------------------------------------------------------------
// pid-dashboard（首屏常驻 N/E + F 参考同显块 + series 断言）
// ---------------------------------------------------------------------------

function monitorSnapshot(tsStart: string, withStats = true) {
  return {
    assessmentStats: withStats
      ? {
          fastRateReference: {
            dynamicEvidenceCoverageRatio: 0.25,
            dynamicEstimateCount: 2,
            dynamicSampleCount: 2,
            dynamicWeightedNumerator: 1.1,
            dynamicWeightSum: 2,
            fastRateDynamicEstimateMean: 55,
            fastRateReferenceMean: 62.5,
            legacyUnclassifiedCount: 0,
            numericCount: 3,
            policyReferenceCount: 1,
            policyReferenceWeightShare: 0.333_333,
            referenceWeightedNumerator: 1.875,
            referenceWeightSum: 3,
            unavailableCount: 5,
          },
          metricStats: {
            score: { numericCount: 5, objectCount: 8, weightedMean: 81.5 },
          },
          roster: {
            eligibleLoopCount: 8,
            excludedLoopCount: 0,
            scorableLoopCount: 5,
            scorableLoopRatio: 0.625,
            states: {
              cancelled: 0,
              failed: 1,
              incomplete: 2,
              missing: 0,
              pending: 0,
              scorable: 5,
            },
          },
        }
      : null,
    dimension: 'hour',
    loopCount: 8,
    plantNodeId: 'node-1',
    score: 82,
    status: 'SUCCESS',
    tsEnd: null,
    tsStart,
  };
}

/** spyOn 装置看板全部 API 出口（真实模块静态引入后替换，见文件头注） */
function applyApiSpies(snapshots: ReturnType<typeof monitorSnapshot>[]) {
  const dash = dashboardApi as any;
  const metric = metricApi as any;
  vi.spyOn(dash, 'getBoardTreeApi').mockResolvedValue({
    items: [
      {
        autoModeRate: 90,
        avgScore: 82,
        children: [],
        fastRate: null,
        hasSnapshot: true,
        nodeId: 'node-1',
        nodeName: '一联合',
        nodeType: 'AREA',
        stabilityRate: 70,
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
      '2026-10-13T00:00:00',
      '2026-10-13T01:00:00',
      '2026-10-13T02:00:00',
    ],
    totalLoops: 8,
  });
  vi.spyOn(dash, 'getBoardAggregateApi').mockResolvedValue({
    aggregate: {
      accuracyRate: null,
      autoModeRate: 91,
      avgScore: 82,
      effectiveAutoRate: null,
      evaluatedLoops: 3,
      excludedLoops: 0,
      fastRate: null,
      goodValueRate: 99,
      inconclusiveLoops: 0,
      instrumentFaultRate: 1,
      nodeId: 'node-1',
      nodeName: '一联合',
      stabilityRate: 71,
      totalLoops: 8,
    },
    items: [],
    total: 0,
  });
  vi.spyOn(dash, 'getAutoRateRtApi').mockResolvedValue({
    autoCount: 7,
    manualCount: 1,
    modeCounts: {},
    rate: 87.5,
    readAt: '2026-10-13T02:00:00Z',
    totalCount: 8,
  });
  vi.spyOn(metric, 'getNodeMonitorApi').mockResolvedValue({ snapshots });
  vi.spyOn(metric, 'getValveAlertsApi').mockResolvedValue({ items: [] });
  vi.spyOn(metric, 'getRankingApi').mockResolvedValue([]);
  vi.spyOn(metric, 'getGradeDistributionApi').mockResolvedValue({
    latestTs: null,
    total: 0,
  });
  vi
    .spyOn(metric, 'getGradingThresholdsApi')
    .mockResolvedValue({ thresholds: [] });
}

async function mountDashboard() {
  const wrapper = shallowMount(PidDashboard);
  await flushPromises();
  await flushPromises();
  await flushPromises();
  return wrapper;
}

describe('pid-dashboard（PL-04/PL-05：常驻 N/E + F 参考同显 + series 断言）', () => {
  beforeEach(() => {
    echartsCalls.length = 0;
    vi.restoreAllMocks();
  });

  it('常驻 N/E 绑定名册字段（scorableLoopCount/eligibleLoopCount）+ 截至 + 原因', async () => {
    // 第 3 桶（02:00）无快照 → 最新可知名册桶=01:00；evaluatedLoops=3 不得冒充 N/E
    applyApiSpies([
      monitorSnapshot('2026-10-13T00:00:00'),
      monitorSnapshot('2026-10-13T01:00:00'),
    ]);
    const wrapper = await mountDashboard();

    const ne = wrapper.find('[data-testid="coverage-ne"]');
    expect(ne.exists()).toBe(true);
    expect(ne.text()).toContain('N=5');
    expect(ne.text()).toContain('E=8');
    expect(ne.text()).toContain('63%');
    expect(ne.text()).not.toContain('N=3');

    expect(wrapper.find('[data-testid="coverage-asof"]').text()).toContain(
      '截至',
    );

    const reason = wrapper.find('[data-testid="coverage-reason"]');
    expect(reason.exists()).toBe(true);
    expect(reason.text()).toContain('数据不足 2');
    expect(reason.text()).toContain('计算失败 1');
  });

  it('F 参考同显块：均分/参考数量/动态证据不足/覆盖/来源，不冒充正式 F/P', async () => {
    applyApiSpies([
      monitorSnapshot('2026-10-13T00:00:00'),
      monitorSnapshot('2026-10-13T01:00:00'),
    ]);
    const wrapper = await mountDashboard();

    const block = wrapper.find('[data-testid="fastref-block"]');
    expect(block.exists()).toBe(true);
    const text = block.text();
    // 均分由充分统计量重算：1.875/3×100 = 62.5
    expect(text).toContain('F 参考均分');
    expect(text).toContain('62.5');
    expect(text).toContain('参考数量 3');
    expect(text).toContain('动态证据充分 2');
    expect(text).toContain('动态证据不足 1');
    expect(text).toContain('无数据 5');
    // 动态估计覆盖 = 2/(3+5) = 25%
    expect(text).toContain('动态估计覆盖 25%');
    expect(text).toContain('来源：小时快照名册统计');
    expect(text).toContain('参考');
    expect(text).toContain('不计入正式 F/P');
  });

  it('series 断言：无名册桶为 null 断点（不画作 0），折线 connectNulls=false', async () => {
    applyApiSpies([
      monitorSnapshot('2026-10-13T00:00:00'),
      monitorSnapshot('2026-10-13T01:00:00'),
    ]);
    await mountDashboard();

    const last = echartsCalls[echartsCalls.length - 1];
    expect(last?.series).toBeDefined();
    const bar = last.series.find((s: any) => s.name === '可评分 N/E');
    // 第 3 桶无快照 → null 断点（不猜算 0）
    expect(bar.data).toEqual([5, 5, null]);
    const lines = last.series.filter((s: any) => s.type === 'line');
    expect(lines.length).toBeGreaterThan(0);
    for (const l of lines) {
      expect(l.connectNulls).toBe(false);
    }
  });

  it('全无名册（历史快照不可追溯）：诚实态，不编造 N/E 与 F 参考', async () => {
    applyApiSpies([
      monitorSnapshot('2026-10-13T00:00:00', false),
      monitorSnapshot('2026-10-13T01:00:00', false),
    ]);
    const wrapper = await mountDashboard();

    const ne = wrapper.find('[data-testid="coverage-ne"]');
    expect(ne.text()).toContain('暂无名册数据');
    expect(wrapper.find('[data-testid="fastref-block"]').exists()).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// AssessSection（评估任务终态区分 + 陈腐脚注清除）
// ---------------------------------------------------------------------------

describe('AssessSection（P6-03：排队/运行/失败/取消区分）', () => {
  beforeEach(() => {
    runnerState.error = null;
    runnerState.isRunning = false;
    runnerState.progress = null;
    runnerState.stage = null;
  });

  it('CANCELLED 终态 → 「已取消」标签，不渲染成失败', () => {
    runnerState.error = '任务CANCELLED';
    const wrapper = shallowMount(AssessSection, {
      props: { loopTagName: 'LIC-101', selectedLoopId: 'loop-1' },
    });
    expect(wrapper.text()).toContain('上次评估已取消');
    expect(wrapper.text()).not.toContain('上次评估失败');
  });

  it('FAILED 终态 → 「失败」标签', () => {
    runnerState.error = '评估失败：TDengine 连接超时';
    const wrapper = shallowMount(AssessSection, {
      props: { loopTagName: 'LIC-101', selectedLoopId: 'loop-1' },
    });
    expect(wrapper.text()).toContain('上次评估失败');
  });

  it('历史表脚注不再含陈腐「手动来源待 G2」（来源行内可区分）', () => {
    const wrapper = shallowMount(AssessSection, {
      props: { loopTagName: 'LIC-101', selectedLoopId: 'loop-1' },
    });
    expect(wrapper.text()).not.toContain('待 G2');
    expect(wrapper.text()).toContain('来源行内标注');
  });
});

// ---------------------------------------------------------------------------
// task/detail（CANCELLED 进度条不渲染 active；完整挂载走 antd 真实类名）
// ---------------------------------------------------------------------------

describe('task/detail（P6-03：任务进度状态区分）', () => {
  function taskWith(status: string) {
    return {
      createdAt: '2026-10-13T00:00:00',
      createdBy: 'admin',
      currentStage: 'METRIC_CALC',
      errorMessage: null,
      finishedAt: '2026-10-13T00:02:00',
      loopsDone: 2,
      loopsTotal: 5,
      progress: 0.4,
      startedAt: '2026-10-13T00:01:00',
      status,
      taskId: 't-1',
      taskType: 'STANDARD',
    };
  }

  it('CANCELLED → 进度条 status=normal（不是 active）', async () => {
    taskDetailMock.mockResolvedValue(taskWith('CANCELLED'));
    const wrapper = mount(TaskDetail);
    await flushPromises();
    expect(wrapper.find('.ant-progress-status-normal').exists()).toBe(true);
    expect(wrapper.find('.ant-progress-status-active').exists()).toBe(false);
    wrapper.unmount();
  });

  it('FAILED → 进度条 status=exception', async () => {
    taskDetailMock.mockResolvedValue(taskWith('FAILED'));
    const wrapper = mount(TaskDetail);
    await flushPromises();
    expect(wrapper.find('.ant-progress-status-exception').exists()).toBe(true);
    wrapper.unmount();
  });
});
