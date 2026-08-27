/**
 * 装置总览（dashboard/workbench.vue 管理者版，2026-08-24 重排）单元测试
 *
 * 覆盖方案 A 管理者版布局核心交互：
 * - 行1 标题行 + 行2 全厂结论带：6 张结论卡（综合评分/参评回路/问题回路/
 *   处置待办/本期闭环/实时自控率），原雷达/子弹图带/装置指标对比已删除
 * - 行2 治理卡 + C 列治理漏斗：governance-summary 计数映射、转化率、时间窗联动
 * - A 列全厂健康结构：等级分布饼图 + 适用性 L0~L4 + MODE 分布 5 行 + 阀门越限计数
 * - B 列装置-单元树形排名：折叠/展开、未挂载兜底组、表头排序、排名即导航联动
 * - D 列绩效趋势：默认图例收敛（评分主线+自控率）、悬浮十字线统一悬浮框
 * - E 列重点关注回路：最低/最高 10 切换、点击跳回路工作台
 * - 结论卡导航：问题回路 → /monitor/attention；处置待办 → /handling/tasks
 */
import { mount } from '@vue/test-utils';

import dayjs from 'dayjs';
import { beforeEach, describe, expect, it, vi } from 'vitest';

// ===== Mock API（#/api） =====
const getBoardAggregateApiMock = vi.fn();
const getAutoRateRtApiMock = vi.fn();
const getBoardTrendApiMock = vi.fn();
const getGradingThresholdsApiMock = vi.fn();

vi.mock('#/api', () => ({
  getAutoRateRtApi: (...a: unknown[]) => getAutoRateRtApiMock(...a),
  getBoardAggregateApi: (...a: unknown[]) => getBoardAggregateApiMock(...a),
  getBoardTrendApi: (...a: unknown[]) => getBoardTrendApiMock(...a),
  getGradingThresholdsApi: (...a: unknown[]) =>
    getGradingThresholdsApiMock(...a),
}));

// ===== Mock API（#/api/metric） =====
const getNodeRankingApiMock = vi.fn();
const getGradeDistributionApiMock = vi.fn();
const getNodeTrendApiMock = vi.fn();
const getRankingApiMock = vi.fn();
const getLoopSnapshotsApiMock = vi.fn();

vi.mock('#/api/metric', () => ({
  getGradeDistributionApi: (...a: unknown[]) =>
    getGradeDistributionApiMock(...a),
  getLoopSnapshotsApi: (...a: unknown[]) => getLoopSnapshotsApiMock(...a),
  getNodeRankingApi: (...a: unknown[]) => getNodeRankingApiMock(...a),
  getNodeTrendApi: (...a: unknown[]) => getNodeTrendApiMock(...a),
  getRankingApi: (...a: unknown[]) => getRankingApiMock(...a),
}));

// ===== Mock API（#/api/governance） =====
const getGovernanceSummaryApiMock = vi.fn();

vi.mock('#/api/governance', () => ({
  getGovernanceSummaryApi: (...a: unknown[]) =>
    getGovernanceSummaryApiMock(...a),
}));

// ===== Mock API（#/api/plant-node） =====
const getPlantNodeTreeApiMock = vi.fn();

vi.mock('#/api/plant-node', () => ({
  getPlantNodeTreeApi: (...a: unknown[]) => getPlantNodeTreeApiMock(...a),
}));

// ===== Mock 通用组件 =====
vi.mock('@vben/common-ui', () => ({
  Page: { template: '<div data-testid="page"><slot /></div>' },
}));

vi.mock('ant-design-vue', () => ({
  RangePicker: {
    name: 'RangePicker',
    props: ['allowClear', 'format', 'showTime', 'value'],
    emits: ['change'],
    template: '<div data-testid="range-picker" />',
  },
  Spin: {
    props: ['spinning'],
    template: '<div data-testid="spin"><slot /></div>',
  },
  // 适用性堆叠条/单元行工程指标等 Tooltip 打桩：仅透传默认插槽
  Tooltip: {
    name: 'Tooltip',
    props: ['placement', 'title'],
    template: '<div data-testid="tooltip"><slot /></div>',
  },
}));

const routerPushMock = vi.fn();
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: routerPushMock }),
}));

import Workbench from '#/views/dashboard/workbench.vue';

// ===== Mock 数据 =====
const areaRanking = [
  {
    rank: 1,
    plantNodeId: 'area-1',
    plantNodeName: '常减压装置',
    score: 91.2,
    steadyRate: 93,
    fastRate: 88,
    accuracyRate: 90,
    autoModeRate: 80,
    loopCount: 10,
    status: 'NORMAL',
  },
  {
    rank: 2,
    plantNodeId: 'area-2',
    plantNodeName: '催化裂化装置',
    score: 85.4,
    steadyRate: 89,
    fastRate: 82,
    accuracyRate: 84,
    autoModeRate: 76,
    loopCount: 17,
    status: 'NORMAL',
  },
];

// 评分降序：unit-2(92) > unit-3(90) > unit-1(88)
// 平稳率降序：unit-1(95.5) > unit-2(91) > unit-3(88) → 切换排序后顺序应反转
const unitRanking = [
  {
    rank: 1,
    plantNodeId: 'unit-1',
    plantNodeName: '脱甲烷单元',
    score: 88,
    steadyRate: 95.5,
    fastRate: 84,
    accuracyRate: 86,
    autoModeRate: 77,
    loopCount: 6,
    status: 'NORMAL',
  },
  {
    rank: 2,
    plantNodeId: 'unit-2',
    plantNodeName: '脱乙烷单元',
    score: 92,
    steadyRate: 91,
    fastRate: 92,
    accuracyRate: 93,
    autoModeRate: 88,
    loopCount: 8,
    status: 'NORMAL',
  },
  {
    rank: 3,
    plantNodeId: 'unit-3',
    plantNodeName: '脱丙烷单元',
    score: 90,
    steadyRate: 88,
    fastRate: 89,
    accuracyRate: 90,
    autoModeRate: 82,
    loopCount: 5,
    status: 'NORMAL',
  },
];

const plantTree = [
  {
    id: 'area-1',
    name: '常减压装置',
    type: 'AREA',
    parentId: null,
    children: [
      { id: 'unit-1', name: '脱甲烷单元', type: 'UNIT', parentId: 'area-1' },
      { id: 'unit-2', name: '脱乙烷单元', type: 'UNIT', parentId: 'area-1' },
    ],
  },
  {
    id: 'area-2',
    name: '催化裂化装置',
    type: 'AREA',
    parentId: null,
    children: [
      { id: 'unit-3', name: '脱丙烷单元', type: 'UNIT', parentId: 'area-2' },
    ],
  },
];

function setupMocks() {
  getBoardAggregateApiMock.mockResolvedValue({
    items: [],
    total: 0,
    aggregate: {
      accuracyRate: 90.2,
      autoModeRate: 78.9,
      avgScore: 88.5,
      effectiveAutoRate: 82.4,
      evaluatedLoops: 27,
      excludedLoops: 0,
      fastRate: 85.3,
      goodValueRate: 96,
      inconclusiveLoops: 0,
      instrumentFaultRate: 0.5,
      nodeId: null,
      nodeName: null,
      stabilityRate: 92.1,
      totalLoops: 27,
    },
  });
  getAutoRateRtApiMock.mockResolvedValue({
    rate: 75,
    autoCount: 20,
    manualCount: 7,
    totalCount: 27,
    modeCounts: { '0': 7, '1': 15, '2': 5 },
    readAt: new Date().toISOString(),
  });
  getGradeDistributionApiMock.mockResolvedValue({
    EXCELLENT: 5,
    GOOD: 10,
    FAIR: 8,
    WARNING: 3,
    POOR: 1,
    INCONCLUSIVE: 0,
    total: 27,
  });
  getGovernanceSummaryApiMock.mockResolvedValue({
    timeWindow: 'last_24_hours',
    handling: {
      openItems: 5,
      openOrders: 4,
      overdueOrders: 2,
      closedInWindow: 6,
    },
    funnel: { discovered: 5, diagnosed: 7, planned: 9, closed: 6 },
    badLoops: { warning: 3, poor: 2 },
  });
  getNodeRankingApiMock.mockImplementation((params: { nodeType: string }) =>
    Promise.resolve(
      params.nodeType === 'AREA' ? [...areaRanking] : [...unitRanking],
    ),
  );
  getRankingApiMock.mockResolvedValue([]);
  getNodeTrendApiMock.mockResolvedValue({ timestamps: [], series: [] });
  getBoardTrendApiMock.mockResolvedValue({
    timestamps: ['2026-08-14T00:00:00'],
    avgScore: [88],
    stabilityRate: [92],
    fastRate: [85],
    accuracyRate: [90],
    autoModeRate: [78],
    evaluatedLoops: [27],
  });
  getGradingThresholdsApiMock.mockResolvedValue({ thresholds: [] });
  // 工厂树：area-1 → unit-1/unit-2，area-2 → unit-3（供树形排名 join 层级）
  getPlantNodeTreeApiMock.mockResolvedValue(plantTree);
  // 快照：2 条越限（min≤5 / max≥95）+ 1 条正常（仅计数展示，列表已删除）
  getLoopSnapshotsApiMock.mockResolvedValue({
    items: [
      {
        loopId: 'loop-1',
        loopTagName: 'FIC-101',
        valveOpMin: 2,
        valveOpMax: 60,
      },
      {
        loopId: 'loop-2',
        loopTagName: 'FIC-202',
        valveOpMin: 20,
        valveOpMax: 98,
      },
      {
        loopId: 'loop-3',
        loopTagName: 'FIC-303',
        valveOpMin: 12,
        valveOpMax: 80,
      },
    ],
    total: 3,
    page: 1,
    pageSize: 50,
  });
}

async function mountWorkbench() {
  const w = mount(Workbench);
  await vi.dynamicImportSettled();
  // onMounted 的七组加载 + 后续微任务全部落地
  for (let i = 0; i < 8; i++) {
    await Promise.resolve();
  }
  return w;
}

/** 定位 B 列树形排名的数据行（按装置/单元名匹配） */
function findUnitRow(wrapper: ReturnType<typeof mount>, name: string) {
  return wrapper
    .findAll('.cursor-pointer')
    .find((el) => el.text().includes(name));
}

describe('装置总览（管理者版）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setupMocks();
  });

  it('行1 标题行 + 行2 结论带：标题/时间窗/六张结论卡，雷达与装置指标对比已删除', async () => {
    const w = await mountWorkbench();
    const text = w.text();

    // 行1 标题在最前
    expect(text.trim().startsWith('装置总览')).toBe(true);

    // 行2 六张结论卡标签按序
    const labels = [
      '全厂综合评分',
      '参评回路',
      '问题回路',
      '处置待办',
      '本期闭环',
      '实时自控率',
    ];
    let prev = -1;
    for (const label of labels) {
      const idx = text.indexOf(label);
      expect(idx).toBeGreaterThan(-1);
      expect(idx).toBeGreaterThan(prev);
      prev = idx;
    }

    // 结论卡关键要素：等级徽章 / 参评口径 / 超期 / 实时角标
    expect(text).toContain('参评回路 / 总回路');
    expect(text).toContain('超期');
    expect(text).toContain('实时 ·');

    // 行3/行4 卡片标题
    expect(text).toContain('全厂健康结构');
    expect(text).toContain('装置-单元排名');
    expect(text).toContain('治理漏斗');
    expect(text).toContain('绩效趋势');
    expect(text).toContain('重点关注回路');

    // 已删除的元素不再出现（§2 雷达 / §7 装置指标对比 / §6 独立运行状态卡）
    expect(text).not.toContain('全厂雷达');
    expect(text).not.toContain('装置指标对比');
    expect(text).not.toContain('实时在线');
    expect(text).not.toContain('统计时间窗');
    expect(text).not.toContain('B 类口径');

    // 时间窗按钮组五档齐全（近 8/24/72/168 小时 + 自定义），默认选中"近 24 小时"
    const twItems = w
      .findAll('button')
      .filter((b) =>
        [
          '自定义',
          '近 8 小时',
          '近 24 小时',
          '近 72 小时',
          '近 168 小时',
        ].includes(b.text()),
      );
    expect(twItems).toHaveLength(5);
    const active = twItems.find((b) => b.text() === '近 24 小时');
    expect(active?.classes()).toContain('bg-blue-700');
  });

  it('行2 治理卡 + C 列漏斗：governance-summary 计数映射与转化率', async () => {
    const w = await mountWorkbench();
    const text = w.text();

    // 接口按当前时间窗调用
    expect(getGovernanceSummaryApiMock).toHaveBeenCalledWith({
      timeWindow: 'last_24_hours',
    });

    // 问题回路卡：警告 3 / 不合格 2（badLoops 双口径）
    expect(text).toContain('警告 3 · 不合格 2');
    // 处置待办卡：超期 2
    expect(text).toContain('超期 2');
    // C 列漏斗：四级标签齐全 + openItems 说明行
    for (const label of ['发现', '诊断', '方案', '闭环']) {
      expect(text).toContain(label);
    }
    expect(text).toContain('未闭环处置建议 5 条');
    // 转化率（相对上一级）：诊断 7/5=140%、方案 9/7≈129%、闭环 6/9≈67%
    expect(text).toContain('140%');
    expect(text).toContain('129%');
    expect(text).toContain('67%');
  });

  it('A 列健康结构：等级分布饼图 + 适用性 L0~L4 + MODE 5 行 + 阀门越限计数', async () => {
    const w = await mountWorkbench();
    const html = w.html();
    const text = w.text();

    // 等级分布饼图（5 档有计数 → path 渲染）+ 图例
    expect(html).toContain('<path');
    expect(text).toContain('适用性分层（L0~L4）');

    // MODE 分布五类齐全
    for (const label of ['自动', '串级', '远程', '先控', '手动']) {
      expect(text).toContain(label);
    }

    // 阀门越限仅计数（2 条越限：FIC-101/FIC-202；越限列表已删除，位号不再展示）
    expect(text).toContain('阀门越限 2 条');
    expect(text).not.toContain('FIC-101');
    expect(text).not.toContain('FIC-202');
    expect(text).not.toContain('FIC-303');

    // 快照接口按 latestOnly 拉取 50 条（加载编排不变）
    expect(getLoopSnapshotsApiMock).toHaveBeenCalledWith({
      page: 1,
      pageSize: 50,
      latestOnly: true,
    });
  });

  it('B 列树形排名：默认全展开，装置行折叠/展开单元行', async () => {
    const w = await mountWorkbench();

    // 工厂树接口被调用（join 单元层级）
    expect(getPlantNodeTreeApiMock).toHaveBeenCalled();

    // 默认全展开：装置行 + 全部单元行可见，装置行在前
    let text = w.text();
    expect(text).toContain('常减压装置');
    expect(text).toContain('催化裂化装置');
    expect(text.indexOf('常减压装置')).toBeLessThan(text.indexOf('脱乙烷单元'));
    expect(text).toContain('脱甲烷单元');
    expect(text).toContain('脱乙烷单元');
    expect(text).toContain('脱丙烷单元');

    // 点击"常减压装置"行首折叠箭头（▼）→ 该装置下单元行收起
    const areaRow = findUnitRow(w, '常减压装置');
    expect(areaRow).toBeDefined();
    const arrow = areaRow!.findAll('span').find((s) => s.text() === '▼');
    expect(arrow).toBeDefined();
    await arrow!.trigger('click');
    await Promise.resolve();

    text = w.text();
    expect(text).not.toContain('脱甲烷单元');
    expect(text).not.toContain('脱乙烷单元');
    expect(text).toContain('脱丙烷单元'); // 其他装置不受影响

    // 再次点击（►）→ 展开恢复
    const arrowCollapsed = findUnitRow(w, '常减压装置')!
      .findAll('span')
      .find((s) => s.text() === '►');
    expect(arrowCollapsed).toBeDefined();
    await arrowCollapsed!.trigger('click');
    await Promise.resolve();
    expect(w.text()).toContain('脱甲烷单元');
    expect(w.text()).toContain('脱乙烷单元');
  });

  it('B 列树形排名：单元未挂载装置时归入"未挂载装置"兜底组', async () => {
    // 工厂树中不包含 unit-3 → 脱丙烷单元应落入兜底组
    getPlantNodeTreeApiMock.mockResolvedValue([
      {
        id: 'area-1',
        name: '常减压装置',
        type: 'AREA',
        parentId: null,
        children: [
          {
            id: 'unit-1',
            name: '脱甲烷单元',
            type: 'UNIT',
            parentId: 'area-1',
          },
          {
            id: 'unit-2',
            name: '脱乙烷单元',
            type: 'UNIT',
            parentId: 'area-1',
          },
        ],
      },
    ]);
    const w = await mountWorkbench();

    const text = w.text();
    expect(text).toContain('未挂载装置');
    expect(text).toContain('脱丙烷单元'); // 兜底组内默认展开可见
    // 兜底组排在最后
    expect(text.indexOf('脱丙烷单元')).toBeGreaterThan(
      text.indexOf('催化裂化装置'),
    );
  });

  it('排名即导航：点击 B 列单元行联动 D 列趋势范围与 E 列范围，再次点击恢复全厂', async () => {
    const w = await mountWorkbench();

    // 默认全厂（默认窗口近 24 小时 → 短标签"近 24h"）
    expect(w.text()).toContain('全厂 · 近 24h');

    // 点击"脱甲烷单元"行
    const row = findUnitRow(w, '脱甲烷单元');
    expect(row).toBeDefined();
    await row!.trigger('click');
    for (let i = 0; i < 5; i++) {
      await Promise.resolve();
    }

    // D 列底部状态行 + E 列范围行联动为单元名
    expect(w.text()).toContain('脱甲烷单元 · 近 24h');
    expect(w.text()).toContain('范围: 脱甲烷单元');

    // 再次点击同一行 → 取消选择恢复全厂
    const rowAgain = findUnitRow(w, '脱甲烷单元');
    await rowAgain!.trigger('click');
    for (let i = 0; i < 5; i++) {
      await Promise.resolve();
    }
    expect(w.text()).toContain('全厂 · 近 24h');
  });

  it('B 列表头排序：点击"平稳率"后装置/单元按平稳率降序重排', async () => {
    const w = await mountWorkbench();

    // 默认评分降序：area-1 组内 脱乙烷(92) 在 脱甲烷(88) 前
    let text = w.text();
    expect(text.indexOf('脱乙烷单元')).toBeLessThan(text.indexOf('脱甲烷单元'));

    // 点击表头"平稳率"按钮（B 列排名表头，DOM 序先于 D 列图例同名按钮）
    const steadyBtn = w
      .findAll('button')
      .find((b) => b.text().startsWith('平稳率'));
    expect(steadyBtn).toBeDefined();
    await steadyBtn!.trigger('click');
    await Promise.resolve();

    // 平稳率降序：area-1 组内 脱甲烷(95.5) > 脱乙烷(91)；area-2 组内 脱丙烷(88)
    text = w.text();
    expect(text.indexOf('脱甲烷单元')).toBeLessThan(text.indexOf('脱乙烷单元'));
    expect(text.indexOf('脱乙烷单元')).toBeLessThan(text.indexOf('脱丙烷单元'));
  });

  it('页面级时间窗总开关：切换"近 72 小时"后排名与治理接口按新 timeWindow 重新加载', async () => {
    const w = await mountWorkbench();
    expect(getNodeRankingApiMock).toHaveBeenCalledTimes(2); // AREA + UNIT
    expect(getGovernanceSummaryApiMock).toHaveBeenCalledTimes(1);

    const item72h = w.findAll('button').find((b) => b.text() === '近 72 小时');
    await item72h!.trigger('click');
    for (let i = 0; i < 5; i++) {
      await Promise.resolve();
    }

    expect(getNodeRankingApiMock).toHaveBeenCalledTimes(4);
    const lastCalls = getNodeRankingApiMock.mock.calls.map((c) => c[0]);
    expect(lastCalls.at(-1)).toMatchObject({ timeWindow: 'last_72_hours' });
    expect(lastCalls.at(-2)).toMatchObject({ timeWindow: 'last_72_hours' });
    // 治理接口同步切换时间窗
    expect(getGovernanceSummaryApiMock).toHaveBeenCalledTimes(2);
    expect(getGovernanceSummaryApiMock.mock.calls.at(-1)?.[0]).toMatchObject({
      timeWindow: 'last_72_hours',
    });
    // D 列底部时间窗标签同步
    expect(w.text()).toContain('全厂 · 近 72h');
  });

  it('自定义时间窗：点击"自定义"弹出起止面板，选定后接口带 custom + startTime/endTime', async () => {
    const w = await mountWorkbench();
    expect(getNodeRankingApiMock).toHaveBeenCalledTimes(2); // AREA + UNIT

    // 点击"自定义" → pageTimeWindow=custom，弹出起止选择面板，未选范围提示
    const itemCustom = w.findAll('button').find((b) => b.text() === '自定义');
    await itemCustom!.trigger('click');
    await Promise.resolve();
    expect(w.find('[data-testid="range-picker"]').exists()).toBe(true);
    expect(w.text()).toContain('请选择起止时间');
    // 未选范围不触发刷新
    expect(getNodeRankingApiMock).toHaveBeenCalledTimes(2);

    // 模拟选定起止范围（小时颗粒度）→ 面板关闭 + 接口带 custom 起止参数
    // 预期值按本地→UTC 动态计算，避免测试环境时区差异
    const start = dayjs('2026-08-14T08:00:00');
    const end = dayjs('2026-08-15T08:00:00');
    const picker = w.findComponent({ name: 'RangePicker' });
    picker.vm.$emit('change', [start, end]);
    await Promise.resolve();
    // watch(customRange) 异步刷新
    for (let i = 0; i < 5; i++) {
      await Promise.resolve();
    }

    expect(w.find('[data-testid="range-picker"]').exists()).toBe(false);
    expect(getNodeRankingApiMock).toHaveBeenCalledTimes(4);
    const expected = {
      endTime: end.utc().format('YYYY-MM-DDTHH:00:00'),
      startTime: start.utc().format('YYYY-MM-DDTHH:00:00'),
      timeWindow: 'custom',
    };
    const lastCalls = getNodeRankingApiMock.mock.calls.map((c) => c[0]);
    expect(lastCalls.at(-1)).toMatchObject(expected);
    expect(lastCalls.at(-2)).toMatchObject(expected);
    // 治理接口同样带 custom 起止
    expect(getGovernanceSummaryApiMock.mock.calls.at(-1)?.[0]).toMatchObject(
      expected,
    );
    // 标题旁显示实际时间范围（本地时间）
    expect(w.text()).toContain('08-14 08:00 ~ 08-15 08:00');
  });

  it('行2 环比：并行请求上一窗口基线，综合评分卡显示方向数值', async () => {
    // 当前窗口（默认近 24 小时）与上一窗口（custom 起止）返回不同基线
    const cur = {
      items: [],
      total: 0,
      aggregate: {
        accuracyRate: 90.2,
        autoModeRate: 78.9,
        avgScore: 88.5,
        effectiveAutoRate: 82.4,
        evaluatedLoops: 27,
        excludedLoops: 0,
        fastRate: 85.3,
        goodValueRate: 96,
        inconclusiveLoops: 0,
        instrumentFaultRate: 0.5,
        nodeId: null,
        nodeName: null,
        stabilityRate: 90,
        totalLoops: 27,
      },
    };
    const prev = {
      items: [],
      total: 0,
      aggregate: {
        accuracyRate: 90.2,
        autoModeRate: 78.9,
        avgScore: 87.5,
        effectiveAutoRate: 82.4,
        evaluatedLoops: 27,
        excludedLoops: 0,
        fastRate: 85.3,
        goodValueRate: 96,
        inconclusiveLoops: 0,
        instrumentFaultRate: 0.5,
        nodeId: null,
        nodeName: null,
        stabilityRate: 92,
        totalLoops: 27,
      },
    };
    getBoardAggregateApiMock.mockImplementation(
      (params: { timeWindow?: string }) =>
        Promise.resolve(params?.timeWindow === 'custom' ? prev : cur),
    );

    const w = await mountWorkbench();

    // loadCards 并行请求当前 + 上一窗口（custom 起止）
    const aggCalls = getBoardAggregateApiMock.mock.calls.map((c) => c[0]);
    expect(aggCalls).toHaveLength(2);
    expect(aggCalls[0]).toMatchObject({ timeWindow: 'last_24_hours' });
    expect(aggCalls[1]).toMatchObject({
      endTime: expect.any(String),
      startTime: expect.any(String),
      timeWindow: 'custom',
    });

    // 评分环比：88.5 - 87.5 = ↑ 1.00（绿色上行角标）+ "较上一窗口"说明
    expect(w.text()).toContain('↑ 1.00');
    expect(w.text()).toContain('较上一窗口');
  });

  it('D 列趋势交互：默认图例收敛（评分+自控率）+ 悬浮十字线统一悬浮框', async () => {
    // jsdom 布局矩形为 0：按 viewBox 尺寸（960×310 等比缩放到 384 宽）mock 图区容器
    const rectSpy = vi
      .spyOn(HTMLElement.prototype, 'getBoundingClientRect')
      .mockReturnValue({
        x: 0,
        y: 0,
        top: 0,
        left: 0,
        right: 384,
        bottom: 240,
        width: 384,
        height: 240,
        toJSON: () => ({}),
      } as DOMRect);
    const w = await mountWorkbench();

    // 图例五项全部可点击（综合评分 / 四率）
    const legendWrap = w.find('[data-testid="trend-legend"]');
    expect(legendWrap.exists()).toBe(true);
    const legendBtns = legendWrap.findAll('button');
    expect(legendBtns.map((b) => b.text())).toEqual([
      '综合评分',
      '平稳率',
      '快速率',
      '准确率',
      '自控率',
    ]);
    // 管理者版默认：仅综合评分 + 自控率开启，平稳/快速/准确默认关闭
    const enabledCls = (b: (typeof legendBtns)[number]) =>
      b.classes().includes('text-gray-600');
    expect(enabledCls(legendBtns[0]!)).toBe(true); // 综合评分
    expect(enabledCls(legendBtns[1]!)).toBe(false); // 平稳率
    expect(enabledCls(legendBtns[2]!)).toBe(false); // 快速率
    expect(enabledCls(legendBtns[3]!)).toBe(false); // 准确率
    expect(enabledCls(legendBtns[4]!)).toBe(true); // 自控率

    // 悬浮：mousemove 图区内 → 十字线 + 悬浮框（默认仅评分+自控率行）
    const chart = w.find('[data-testid="trend-chart"]');
    expect(chart.exists()).toBe(true);
    await chart.trigger('mousemove', { clientX: 200 });
    await Promise.resolve();
    expect(w.text()).toContain('00:00');
    expect(w.text()).toContain('88.0%'); // 综合评分（mock avgScore 88）
    expect(w.text()).toContain('78.0%'); // 自控率（默认开启）
    expect(w.text()).not.toContain('92.0%'); // 平稳率默认关闭不入悬浮框

    // 开启"平稳率"图例 → 再次悬浮时显示该行
    await legendBtns[1]!.trigger('click');
    await Promise.resolve();
    await chart.trigger('mousemove', { clientX: 200 });
    await Promise.resolve();
    expect(w.text()).toContain('92.0%');

    // mouseleave → 悬浮框消失
    await chart.trigger('mouseleave');
    await Promise.resolve();

    // 切换"综合评分"图例 → 再次悬浮时不再显示该行
    await legendBtns[0]!.trigger('click'); // 综合评分
    await Promise.resolve();
    await chart.trigger('mousemove', { clientX: 200 });
    await Promise.resolve();
    const tipRows = w
      .findAll('.pointer-events-none')
      .filter((n) => n.text().includes('%'));
    expect(tipRows.length).toBeGreaterThan(0);
    expect(tipRows[0]!.text()).not.toContain('综合评分');

    rectSpy.mockRestore();
  });

  it('E 列重点关注回路：最低/最高 10 切换 + 点击跳回路工作台', async () => {
    getRankingApiMock.mockResolvedValue([
      {
        rank: 1,
        loopId: 'loop-9',
        tagName: 'FIC-901',
        loopName: null,
        unitName: '脱甲烷单元',
        score: 55.5,
        status: 'SUCCESS',
      },
    ]);
    const w = await mountWorkbench();

    // 默认"评分最低 10"（asc）
    expect(getRankingApiMock).toHaveBeenCalledWith(
      expect.objectContaining({ limit: 10, sortBy: 'score', sortOrder: 'asc' }),
    );
    expect(w.text()).toContain('FIC-901');

    // 切换"评分最高 10" → desc 重新加载
    const descBtn = w
      .findAll('button')
      .find((b) => b.text() === '评分最高 10');
    expect(descBtn).toBeDefined();
    await descBtn!.trigger('click');
    for (let i = 0; i < 5; i++) {
      await Promise.resolve();
    }
    expect(getRankingApiMock).toHaveBeenCalledWith(
      expect.objectContaining({ sortOrder: 'desc' }),
    );

    // 点击回路行 → 跳转 /monitor/loop-workbench（from=overview）
    const loopRow = w
      .findAll('.cursor-pointer')
      .find((el) => el.text().includes('FIC-901'));
    expect(loopRow).toBeDefined();
    await loopRow!.trigger('click');
    expect(routerPushMock).toHaveBeenCalledWith({
      path: '/monitor/loop-workbench',
      query: { from: 'overview', loopId: 'loop-9' },
    });
  });

  it('结论卡导航：问题回路 → 关注队列；处置待办 → 处置任务', async () => {
    const w = await mountWorkbench();

    // 问题回路卡（title 定位）→ /monitor/attention
    const badCard = w.find('[title="点击查看关注队列"]');
    expect(badCard.exists()).toBe(true);
    await badCard.trigger('click');
    expect(routerPushMock).toHaveBeenCalledWith({
      path: '/monitor/attention',
      query: { from: 'overview' },
    });

    // 处置待办卡 → /handling/tasks
    const handlingCard = w.find('[title="点击查看处置任务"]');
    expect(handlingCard.exists()).toBe(true);
    await handlingCard.trigger('click');
    expect(routerPushMock).toHaveBeenCalledWith({
      path: '/handling/tasks',
      query: { from: 'overview' },
    });
  });
});
