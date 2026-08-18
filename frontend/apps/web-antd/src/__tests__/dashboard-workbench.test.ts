/**
 * 装置工作台（dashboard/workbench.vue v4.3）单元测试
 *
 * 覆盖 2026-08-15 密度增强 + 树形排名版核心交互：
 * - 行1 标题行 + 行2 综合指标行：七仪表按序（实时自控率 → 好值率），
 *   且不含"实时在线"等已删除元素
 * - 中排：§2 全厂雷达（迁至中排左侧）/ §3 装置-单元树形排名
 *   （装置行折叠/展开单元行，工厂树 join 层级）/ §6 运行状态
 * - §7 装置指标对比：SVG 柱组渲染
 * - 排名即导航：点击 §3 单元行 → §5 趋势标题 + §4 范围联动；再次点击恢复全厂
 * - §3 表头点击排序：装置/单元按平稳率降序重排
 * - 页面级时间窗总开关：切换后 §3 排名接口带新 timeWindow 重新加载
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
}));

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

// ClpmBulletChart 打桩：渲染 label/value/meta/delta 便于断言行2 仪表盘带与环比
vi.mock('#/components/clpm', () => ({
  ClpmBulletChart: {
    name: 'ClpmBulletChart',
    props: [
      'delta',
      'fair',
      'good',
      'invert',
      'label',
      'max',
      'meta',
      'target',
      'unit',
      'value',
    ],
    template:
      '<div class="bullet-stub">{{ label }}|{{ value }}|{{ meta }}|{{ delta }}</div>',
  },
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
  getPlantNodeTreeApiMock.mockResolvedValue([
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
  ]);
  // 快照：2 条越限（min≤5 / max≥95）+ 1 条正常
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
  // onMounted 的五组加载 + 后续微任务全部落地
  for (let i = 0; i < 6; i++) {
    await Promise.resolve();
  }
  return w;
}

/** 定位 §3 树形排名的数据行（按装置/单元名匹配） */
function findUnitRow(wrapper: ReturnType<typeof mount>, name: string) {
  return wrapper
    .findAll('.cursor-pointer')
    .find((el) => el.text().includes(name));
}

describe('装置工作台 v4.3', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setupMocks();
  });

  it('行1 标题行 + 行2 综合指标行：标题/时间窗/七仪表按序，无"实时在线"等已删除元素', async () => {
    const w = await mountWorkbench();
    const text = w.text();

    // 行1 标题在最前，时间窗组件选中项高亮（蓝色底样式）
    expect(text.trim().startsWith('装置工作台')).toBe(true);

    // 行2 七个仪表按序（实时自控率 → 好值率，子弹图形态）
    const labels = [
      '实时自控率',
      '有效自控率',
      '平稳率',
      '快速率',
      '准确率',
      '平均自控率',
      '好值率',
    ];
    let prev = -1;
    for (const label of labels) {
      const idx = text.indexOf(label);
      expect(idx).toBeGreaterThan(-1);
      expect(idx).toBeGreaterThan(prev);
      prev = idx;
    }

    // 行2 数字总览要素 + 中排雷达/树形排名标题
    expect(text).toContain('全厂综合评分');
    expect(text).toContain('总数');
    expect(text).toContain('自控');
    expect(text).toContain('手动');
    expect(text).toContain('参评');
    expect(text).toContain('等级分布');
    expect(text).toContain('全厂雷达');
    expect(text).toContain('装置-单元排名');

    // 已删除的元素不再出现
    expect(text).not.toContain('实时在线');
    expect(text).not.toContain('统计时间窗');
    expect(text).not.toContain('B 类口径');
    expect(text).not.toContain('快照');

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

  it('§6 运行状态：MODE 分布 5 行 + 阀门 OP 越限 2 条（快照前端过滤）', async () => {
    const w = await mountWorkbench();
    const text = w.text();

    // MODE 分布五类齐全
    for (const label of ['自动', '串级', '远程', '先控', '手动']) {
      expect(text).toContain(label);
    }

    // 越限回路：FIC-101（min 2≤5）与 FIC-202（max 98≥95），正常 FIC-303 不出现
    expect(text).toContain('阀门运行区间异常');
    expect(text).toContain('FIC-101');
    expect(text).toContain('FIC-202');
    expect(text).not.toContain('FIC-303');

    // 快照接口按 latestOnly 拉取 50 条
    expect(getLoopSnapshotsApiMock).toHaveBeenCalledWith({
      page: 1,
      pageSize: 50,
      latestOnly: true,
    });
  });

  it('§7 装置指标对比 + 中排全厂雷达：SVG 柱组与数据多边形渲染', async () => {
    const w = await mountWorkbench();
    const html = w.html();
    const text = w.text();

    // §7：两个装置柱组（data-id 委托点击）
    expect(html).toContain('data-id="area-1"');
    expect(html).toContain('data-id="area-2"');
    expect(text).toContain('装置指标对比');

    // 中排雷达（v4.3 自行2右端迁入中排左侧，替代原装置排名区）：
    // 数据多边形（半透明填充）+ 标题
    expect(text).toContain('全厂雷达');
    expect(html).toContain('rgba(37,99,235,.16)');
  });

  it('§3 装置-单元树形排名：默认全展开，装置行折叠/展开单元行', async () => {
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

  it('§3 树形排名：单元未挂载装置时归入"未挂载装置"兜底组', async () => {
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

  it('排名即导航：点击 §3 单元行联动 §5 趋势标题与 §4 范围，再次点击恢复全厂', async () => {
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

    // §5 标题 + §2/§3/§4 范围行联动为单元名
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

  it('§3 表头排序：点击"平稳率"后装置/单元按平稳率降序重排', async () => {
    const w = await mountWorkbench();

    // 默认评分降序：area-1 组内 脱乙烷(92) 在 脱甲烷(88) 前
    let text = w.text();
    expect(text.indexOf('脱乙烷单元')).toBeLessThan(text.indexOf('脱甲烷单元'));

    // 点击表头"平稳率"按钮（§3 树形面板内的排序按钮）
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

  it('页面级时间窗总开关：切换"近 72 小时"后排名接口按新 timeWindow 重新加载', async () => {
    const w = await mountWorkbench();
    expect(getNodeRankingApiMock).toHaveBeenCalledTimes(2); // AREA + UNIT

    const item72h = w.findAll('button').find((b) => b.text() === '近 72 小时');
    await item72h!.trigger('click');
    for (let i = 0; i < 5; i++) {
      await Promise.resolve();
    }

    expect(getNodeRankingApiMock).toHaveBeenCalledTimes(4);
    const lastCalls = getNodeRankingApiMock.mock.calls.map((c) => c[0]);
    expect(lastCalls.at(-1)).toMatchObject({ timeWindow: 'last_72_hours' });
    expect(lastCalls.at(-2)).toMatchObject({ timeWindow: 'last_72_hours' });
    // §5 标题时间窗标签同步
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
    // 标题旁显示实际时间范围（本地时间）
    expect(w.text()).toContain('08-14 08:00 ~ 08-15 08:00');
  });

  it('行2 环比：并行请求上一窗口基线，评分与仪表显示方向数值', async () => {
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

    // 评分环比：88.5 - 87.5 = ↑ 1.00（绿色上行角标）
    expect(w.text()).toContain('↑ 1.00');
    // 平稳率环比：90 - 92 = -2（下行，传给子弹图 delta）
    const steadyStub = w
      .findAll('.bullet-stub')
      .find((b) => b.text().startsWith('平稳率|'));
    expect(steadyStub?.text()).toContain('|-2');
  });

  it('§5 趋势交互：悬浮十字线统一悬浮框 + 图例五项切换', async () => {
    // jsdom 布局矩形为 0：按 viewBox 尺寸（960×240 等比缩放到 384 宽）mock 图区容器
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

    // 图例五项全部可点击（综合评分 / 四率；限定 §5 图例容器，
    // 排除 §3 树形排名表头的同名排序按钮）
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

    // 悬浮：mousemove 图区内 → 十字线 + 悬浮框（时间 + 全序列值）
    const chart = w.find('[data-testid="trend-chart"]');
    expect(chart.exists()).toBe(true);
    await chart.trigger('mousemove', { clientX: 200 });
    await Promise.resolve();
    expect(w.text()).toContain('00:00');
    expect(w.text()).toContain('88.0%'); // 综合评分（mock avgScore 88）
    expect(w.text()).toContain('92.0%'); // 平稳率

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
});
