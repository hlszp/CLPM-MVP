<script lang="ts" setup>
import type { EchartsUIType } from '@vben/plugins/echarts';

import type {
  DashboardApi,
  GradeDistributionResult,
  MetricApi,
  TimeWindow,
} from '#/api';
import type { PlantNodeApi } from '#/api/plant-node';

import { computed, nextTick, onMounted, ref, watch } from 'vue';

import { Page } from '@vben/common-ui';
import { IconifyIcon } from '@vben/icons';
import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import { Button, Drawer, Select, Table, Tag, Tooltip } from 'ant-design-vue';
import dayjs from 'dayjs';

import {
  ClpmBulletChart,
  ClpmEmptyState,
  ClpmPageToolbar,
  ClpmStandardActions,
} from '#/components/clpm';
import PlantNodeTree from '#/components/plant-node/plant-node-tree.vue';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import { useConfigAccess } from '#/composables/use-config-access';
import { useEchartsPreset } from '#/composables/use-echarts-preset';
import { showPageHelp, usePageToolbar } from '#/composables/use-page-toolbar';
import { useScoreColor } from '#/composables/use-score-color';
import { normalizeUtcTimestamp } from '#/utils/format';

defineOptions({ name: 'PidDashboard' });

const { isDark, themeColors, chartColors } = useClpmTheme();
const { canReadConfig } = useConfigAccess();
const { axisBase, getTooltipPreset } = useEchartsPreset();

const timeWindowOptions = [
  { label: '近8小时', value: 'last_8_hours' },
  { label: '24小时', value: 'today' },
  { label: '168小时', value: 'last_7_days' },
  { label: '近1月', value: 'last_30_days' },
];

/** P2 IA优化：fitness tag 中文映射（与其他模块共用） */
const PID_NA_TAG_CN: Record<string, string> = {
  T_UNKNOWN: '未知',
  T_LOCAL_DATA_MISSING: '本地无历史数据',
  T_LOW_COVERAGE_7D: '近 7 日覆盖不足 50%',
  T_LOW_COVERAGE_30D: '近 30 日覆盖不足 50%',
  T_BAD_QUALITY: '数据质量差（PV 坏值/不确定）',
  T_MODE_NOT_AUTO: '当前处于手动控制模式',
  T_SETPOINT_MISSING: 'OPC 未绑定 SP 位号',
  T_OUTPUT_MISSING: 'OPC 未绑定 OP 位号',
  T_PID_PARAMS_INCOMPLETE: 'OPC 未绑定 P/I/D 位号',
  T_CONSTANT_SETPOINT: 'SP 长时间未变（如 30 天全恒定）',
  T_OOS_PV: 'PV 量程外点比例过高',
  T_BAD_OP_RANGE: 'OP 长期顶边或贴底（<5% / >95%）',
  T_DAMPED_OSC: '存在阻尼振荡趋势',
  T_SUSTAINED_OSC: '存在持续振荡趋势',
  T_VALVE_STICTION: '阀门疑似粘滞',
  T_DEADTIME_HIGH: '纯滞后/惯性比偏高',
  T_DRIFT: 'SP-PV 长期偏移（均值偏差）',
  T_HIGH_PV_NOISE: 'PV 高频噪声过大',
};
const pidNATagToCn = (t: string) => PID_NA_TAG_CN[t] ?? t;
/** 不适用（L0/L1）时的 Tooltip */
function fitnessNATip(
  level: null | string | undefined,
  tags: null | string[] | undefined,
): string {
  const lv = level ?? '';
  const tagText =
    tags && tags.length > 0 ? tags.map((t) => pidNATagToCn(t)).join('、') : '适用性不足';
  return `不适用（${lv || 'NA'}）：${tagText}`;
}
/** 不适用时统一中性灰 slate（与其他模块一致，不红不警告） */
const FITNESS_NA_COLOR = 'var(--color-slate-500)';

const timeWindow = ref<TimeWindow>('today');

/** 当前时间窗中文标签（gauges 卡片统计窗口标注） */
const timeWindowLabel = computed(
  () =>
    timeWindowOptions.find((o) => o.value === timeWindow.value)?.label ?? '',
);

const selectedPlantNodeId = ref<string | undefined>(undefined);
const selectedPlantNodeName = ref<string>('全厂');

/** 整改 A-13：工厂导航树抽屉化（默认收起，释放主区 15% 宽度） */
const treeDrawerOpen = ref(false);

function onTreeSelect(node: null | PlantNodeApi.PlantNode) {
  if (node) {
    selectedPlantNodeId.value = node.id;
    selectedPlantNodeName.value = node.name;
  } else {
    selectedPlantNodeId.value = undefined;
    selectedPlantNodeName.value = '全厂';
  }
  treeDrawerOpen.value = false;
  loadAll();
}

function handleTimeWindowChange() {
  loadAll();
}

const boardAggregate = ref<DashboardApi.BoardAggregateResult | null>(null);
const boardTrend = ref<DashboardApi.BoardTrendResult | null>(null);
const autoRateRt = ref<DashboardApi.AutoRateRt | null>(null);

/**
 * 实时数据过期阈值（分钟），超过则标灰/警示
 * P3-17：改为从环境变量读取，可在 .env 中配置 VITE_RT_STALE_MINUTES
 */
const RT_STALE_MINUTES =
  Number(import.meta.env.VITE_RT_STALE_MINUTES ?? 10) || 10;

/** 实时数据新鲜度：readAt 为空（DB 回退）或超过阈值视为过期 */
const rtStale = computed(() => {
  const readAt = autoRateRt.value?.readAt;
  if (!readAt) return true;
  return dayjs().diff(dayjs(readAt), 'minute') > RT_STALE_MINUTES;
});

/** 实时数据更新时间小字（状态饼图/实时自控率卡片角标） */
const rtReadAtText = computed(() => {
  const readAt = autoRateRt.value?.readAt;
  if (!readAt) return '实时数据中断';
  return `数据更新于 ${dayjs(readAt).format('HH:mm')}`;
});

const rankingList = ref<MetricApi.RankingItem[]>([]);
const gradingThresholds = ref<MetricApi.GradingThresholdItem[]>([]);
/** 各性能等级回路数分布（服务端 SQL 聚合，喂"回路等级占比"饼图） */
const gradeDistribution = ref<GradeDistributionResult | null>(null);

// ===== 整改 A-13：分布行列表数据（donut/饼图退役） =====

/** 回路状态统计行（MODE 分布；手动>0 时红色强调） */
const modeRows = computed(() => {
  const rt = autoRateRt.value;
  const total = rt?.totalCount ?? 0;
  const counts = rt?.modeCounts ?? {};
  const order: { key: string; label: string }[] = [
    { key: '1', label: '自动' },
    { key: '2', label: '串级' },
    { key: '3', label: '远程' },
    { key: '4', label: '先控' },
    { key: '0', label: '手动' },
  ];
  return order
    .map((o) => {
      const count = counts[o.key] ?? 0;
      return {
        label: o.label,
        count,
        pct: total > 0 ? Math.round((count / total) * 100) : 0,
        color: o.key === '0' ? 'var(--status-error)' : 'var(--color-slate-400)',
        emphasis: o.key === '0' && count > 0,
      };
    })
    .filter((r) => r.count > 0 || total === 0);
});

/** 等级分布行（按定级阈值顺序 + 数据不足；等级语义色） */
const gradeRows = computed(() => {
  const dist = gradeDistribution.value;
  if (!dist) return [];
  const distMap = dist as unknown as Record<string, number>;
  const total = dist.total ?? 0;
  const rows = [...effectiveThresholds.value]
    .toSorted((a, b) => a.level - b.level)
    .map((t) => {
      const count = distMap[t.name] ?? 0;
      return {
        label: ratingLabels.value[String(t.level)] ?? t.name,
        count,
        pct: total > 0 ? Math.round((count / total) * 100) : 0,
        color: gradeColor(t.level),
      };
    });
  rows.push({
    label: '数据不足',
    count: dist.INCONCLUSIVE ?? 0,
    pct: total > 0 ? Math.round(((dist.INCONCLUSIVE ?? 0) / total) * 100) : 0,
    color: 'var(--status-neutral)',
  });
  return rows;
});

// ===== 整改 F4：阀门运行区间异常（OP 行程越限 5%~95%） =====
interface ValveAlertItem {
  loopId: string;
  tagName: string;
  range: string;
}
const valveAlerts = ref<ValveAlertItem[]>([]);

async function loadValveAlerts() {
  try {
    const { getLoopSnapshotsApi } = await import('#/api/metric');
    const res = await getLoopSnapshotsApi({ page: 1, pageSize: 50 });
    const items = res.items ?? [];
    const alerts: ValveAlertItem[] = [];
    for (const snap of items) {
      const lo = snap.valveOpMin;
      const hi = snap.valveOpMax;
      if (lo === null || lo === undefined || hi === null || hi === undefined)
        continue;
      if (lo <= 5 || hi >= 95) {
        alerts.push({
          loopId: snap.loopId ?? '',
          tagName: snap.loopTagName ?? snap.loopId ?? '—',
          range: `OP ${lo.toFixed(1)}% ~ ${hi.toFixed(1)}%`,
        });
      }
    }
    valveAlerts.value = alerts;
  } catch {
    valveAlerts.value = [];
  }
}

// 整改 C2-3：默认评分升序（最差优先），管理者注意力直达 Bad Actor
const top5Sort = ref<'asc' | 'desc'>('asc');

const aggregateData = computed(() => boardAggregate.value?.aggregate);

const top5List = computed(() => {
  const items = [...rankingList.value];
  if (top5Sort.value === 'asc') {
    items.sort((a, b) => (a.score ?? 0) - (b.score ?? 0));
  } else {
    items.sort((a, b) => (b.score ?? 0) - (a.score ?? 0));
  }
  return items.slice(0, 5);
});

// 默认定级阈值（国标 GB/T 44693.2-2024 §6.3，与 use-score-color 内部默认值同口径；
// 不配置 color 字段：配色统一走 gradeColor 的阈值配置色 > ZL 语义色降级链）
const DEFAULT_THRESHOLDS: MetricApi.GradingThresholdItem[] = [
  { level: 1, name: 'EXCELLENT', label: '优秀', minScore: 90, maxScore: 100 },
  { level: 2, name: 'GOOD', label: '良好', minScore: 80, maxScore: 90 },
  { level: 3, name: 'FAIR', label: '合格', minScore: 60, maxScore: 80 },
  { level: 4, name: 'WARNING', label: '警告', minScore: 40, maxScore: 60 },
  { level: 5, name: 'POOR', label: '不合格', minScore: 0, maxScore: 40 },
];

/** 生效阈值集：动态配置优先，为空时降级默认阈值 */
const effectiveThresholds = computed<MetricApi.GradingThresholdItem[]>(() =>
  gradingThresholds.value.length > 0
    ? gradingThresholds.value
    : DEFAULT_THRESHOLDS,
);

// 定级阈值等级中文显示名（从配置读取，降级用默认值）
const ratingLabels = computed<Record<string, string>>(() => {
  const labels: Record<string, string> = {};
  for (const t of effectiveThresholds.value) {
    labels[String(t.level)] = t.label ?? t.name;
  }
  return labels;
});

/**
 * 等级配色：阈值项自带 color 优先，未配置时按档位降级到 ZL 语义色
 * （降级链与 use-score-color 一致；无评分场景不调用本函数）
 */
function gradeColor(level: number): string {
  const t = effectiveThresholds.value.find((item) => item.level === level);
  if (t?.color) return t.color;
  const fallbackByLevel: Record<number, string> = {
    1: themeColors.value.SUCCESS,
    2: themeColors.value.INFO,
    3: themeColors.value.WARNING,
    4: themeColors.value.DANGER,
    5: themeColors.value.DANGER,
  };
  return fallbackByLevel[level] ?? themeColors.value.NEUTRAL;
}

/**
 * 按评分判定等级（level 字符串，'1' 最优；无评分返回 null）
 * 匹配逻辑与 useScoreColor 一致：按 minScore 降序首个 score >= minScore，都不命中取最低档
 */
function getRatingLevel(score: null | number | undefined): null | string {
  if (score === null || score === undefined || Number.isNaN(score)) return null;
  for (const t of [...effectiveThresholds.value].toSorted(
    (a, b) => b.minScore - a.minScore,
  )) {
    if (score >= t.minScore) return String(t.level);
  }
  return String(
    effectiveThresholds.value[effectiveThresholds.value.length - 1]?.level ?? 5,
  );
}

const tableColumns = [
  {
    title: '序号',
    dataIndex: 'index',
    key: 'index',
    width: 60,
    align: 'center' as const,
  },
  {
    title: '名称',
    dataIndex: 'name',
    key: 'name',
    width: 150,
    align: 'left' as const,
  },
  {
    title: '性能评级',
    dataIndex: 'rating',
    key: 'rating',
    width: 80,
    align: 'center' as const,
  },
  {
    title: '性能评分',
    dataIndex: 'score',
    key: 'score',
    width: 80,
    align: 'right' as const,
  },
  {
    title: '平稳率',
    dataIndex: 'smoothRate',
    key: 'smoothRate',
    width: 80,
    align: 'right' as const,
  },
  {
    title: '自控率',
    dataIndex: 'autoRate',
    key: 'autoRate',
    width: 80,
    align: 'right' as const,
  },
  {
    title: '回路总数',
    dataIndex: 'totalLoops',
    key: 'totalLoops',
    width: 80,
    align: 'right' as const,
  },
];

const tableData = computed(() => {
  const items = boardAggregate.value?.items ?? [];
  // 第一行固定为当前选中节点，其后仅列当前节点的下一层子节点（按评分降序）
  // （后端 board/aggregate 已只返回当前节点 + 直接子节点）
  const currentId = selectedPlantNodeId.value;
  const currentRows = currentId
    ? items.filter((it) => it.nodeId === currentId)
    : [];
  const childRows = items
    .filter((it) => it.nodeId !== currentId)
    .toSorted((a, b) => (b.avgScore ?? 0) - (a.avgScore ?? 0));
  return [...currentRows, ...childRows].map((item, index) => {
    const rating = getRatingLevel(item.avgScore);
    return {
      key: item.nodeId,
      index: index + 1,
      name: item.nodeName ?? '',
      rating,
      ratingColor: rating ? gradeColor(Number(rating)) : '',
      score: formatNumber(item.avgScore),
      totalLoops: item.totalLoops ?? 0,
      autoRate: formatNumber(item.autoModeRate),
      smoothRate: formatNumber(item.stabilityRate),
    };
  });
});

const top5Columns = [
  {
    title: '序号',
    dataIndex: 'index',
    key: 'index',
    width: 40,
    align: 'center' as const,
  },
  {
    title: '位号',
    dataIndex: 'tagName',
    key: 'tagName',
    width: 140,
    ellipsis: true,
  },
  { title: '名称', dataIndex: 'loopName', key: 'loopName', ellipsis: true },
  {
    title: '性能评级',
    dataIndex: 'rating',
    key: 'rating',
    width: 80,
    align: 'center' as const,
  },
  {
    title: '性能评分',
    dataIndex: 'score',
    key: 'score',
    width: 70,
    align: 'right' as const,
  },
  {
    title: '平稳率',
    dataIndex: 'steadyRate',
    key: 'steadyRate',
    width: 65,
    align: 'right' as const,
  },
];

const top5TableData = computed(() => {
  return top5List.value.map((item, index) => {
    const fitnessLevel = item.fitnessLevel ?? null;
    const fitnessTags = Array.isArray(item.fitnessTags) ? item.fitnessTags : null;
    const isFitnessNA = fitnessLevel === 'L0' || fitnessLevel === 'L1';
    const ratingLevel = getRatingLevel(item.score);
    const ratingLabel = ratingLevel
      ? ratingLabels.value[ratingLevel] ?? `L${ratingLevel}`
      : '—';
    return {
      key: item.loopId,
      index: index + 1,
      loopId: item.loopId,
      tagName: item.tagName,
      loopName: item.loopName || item.tagName || '—',
      // P2 IA优化：L0/L1 显示"不适用"中性灰
      ratingText: isFitnessNA ? '不适用' : ratingLabel,
      ratingColor: isFitnessNA
        ? FITNESS_NA_COLOR
        : (ratingLevel
          ? gradeColor(Number(ratingLevel))
          : ''),
      isFitnessNA,
      fitnessNATipText: isFitnessNA ? fitnessNATip(fitnessLevel, fitnessTags) : '',
      score: isFitnessNA ? '—' : formatNumber(item.score),
      scoreColor: isFitnessNA ? FITNESS_NA_COLOR : scoreColor(item.score),
      steadyRate: `${formatNumber(item.steadyRate)}%`,
    };
  });
});

const trendChartRef = ref<EchartsUIType>();

const { renderEcharts: renderTrend } = useEcharts(trendChartRef);

function renderTrendChart() {
  const trend = boardTrend.value;
  if (!trend || !trend.timestamps?.length) return;

  // 性能 #11：用"补 Z 转本地"约定替代 +8h hack。
  // 后端 timestamps 为无时区后缀的 ISO8601（如 "2026-07-22T10:00:00"），
  // 补 "Z" 标记为 UTC 后由 dayjs 按本地时区渲染，跨时区正确。
  const timestamps = trend.timestamps.map((ts) =>
    dayjs(normalizeUtcTimestamp(ts)).format('M-D H:00'),
  );

  const barDataTotal =
    (trend.totalLoops ?? 0) > 0 ? timestamps.map(() => trend.totalLoops) : [];
  const barDataEvaluated = trend.evaluatedLoops ?? [];

  const showBar = timestamps.length <= 24;

  // 整改 A-15：轴/工具提示走 ECharts 工业 preset；X 轴标签不再 45° 旋转（hideOverlap 自动抽稀）
  renderTrend({
    grid: { bottom: 40, left: '2%', right: '2%', top: 20, containLabel: true },
    xAxis: {
      ...axisBase.value,
      type: 'category',
      data: timestamps,
      axisTick: { show: false },
    },
    yAxis: [
      {
        ...axisBase.value,
        type: 'value',
        name: '回路数',
        nameTextStyle: { color: chartColors.value.text, fontSize: 11 },
      },
      {
        ...axisBase.value,
        type: 'value',
        name: '百分比(%)',
        nameTextStyle: { color: chartColors.value.text, fontSize: 11 },
        axisLabel: {
          color: chartColors.value.text,
          fontSize: 10,
          formatter: '{value}%',
          hideOverlap: true,
        },
        max: 100,
        splitLine: { show: false },
      },
    ],
    series: [
      {
        name: '总回路数',
        type: showBar ? ('bar' as const) : ('line' as const),
        data: barDataTotal,
        itemStyle: { color: themeColors.value.INFO },
        areaStyle: showBar
          ? undefined
          : {
              color: {
                type: 'linear',
                x: 0,
                y: 0,
                x2: 0,
                y2: 1,
                colorStops: [
                  { offset: 0, color: `${themeColors.value.INFO}40` },
                  { offset: 1, color: `${themeColors.value.INFO}05` },
                ],
              },
            },
        lineStyle: showBar ? undefined : { width: 2 },
        smooth: !showBar,
        symbol: 'circle',
        symbolSize: 4,
      },
      {
        name: '参评回路数',
        type: showBar ? ('bar' as const) : ('line' as const),
        data: barDataEvaluated,
        itemStyle: { color: themeColors.value.SUCCESS },
        areaStyle: showBar
          ? undefined
          : {
              color: {
                type: 'linear',
                x: 0,
                y: 0,
                x2: 0,
                y2: 1,
                colorStops: [
                  { offset: 0, color: `${themeColors.value.SUCCESS}40` },
                  { offset: 1, color: `${themeColors.value.SUCCESS}05` },
                ],
              },
            },
        lineStyle: showBar ? undefined : { width: 2 },
        smooth: !showBar,
        symbol: 'circle',
        symbolSize: 4,
      },
      {
        name: '性能评分',
        type: 'line' as const,
        yAxisIndex: 1,
        data: trend.avgScore ?? [],
        smooth: true,
        itemStyle: { color: themeColors.value.WARNING },
        lineStyle: { width: 2 },
        symbol: 'circle',
        symbolSize: 6,
        areaStyle: {
          color: {
            type: 'linear',
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [
              { offset: 0, color: `${themeColors.value.WARNING}30` },
              { offset: 1, color: `${themeColors.value.WARNING}05` },
            ],
          },
        },
      },
      {
        name: '自控率',
        type: 'line' as const,
        yAxisIndex: 1,
        data: trend.autoModeRate ?? [],
        smooth: true,
        itemStyle: { color: themeColors.value.INFO },
        lineStyle: { width: 2 },
        symbol: 'circle',
        symbolSize: 6,
        areaStyle: {
          color: {
            type: 'linear',
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [
              { offset: 0, color: `${themeColors.value.INFO}30` },
              { offset: 1, color: `${themeColors.value.INFO}05` },
            ],
          },
        },
      },
      {
        name: '平稳率',
        type: 'line' as const,
        yAxisIndex: 1,
        data: trend.stabilityRate ?? [],
        smooth: true,
        itemStyle: { color: themeColors.value.SUCCESS },
        lineStyle: { width: 2 },
        symbol: 'circle',
        symbolSize: 6,
        areaStyle: {
          color: {
            type: 'linear',
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [
              { offset: 0, color: `${themeColors.value.SUCCESS}30` },
              { offset: 1, color: `${themeColors.value.SUCCESS}05` },
            ],
          },
        },
      },
    ],
    tooltip: {
      ...getTooltipPreset(),
      trigger: 'axis',
      axisPointer: { type: 'cross' },
    },
    legend: {
      bottom: 0,
      textStyle: { color: chartColors.value.text, fontSize: 11 },
      data: ['总回路数', '参评回路数', '性能评分', '自控率', '平稳率'],
    },
  });
}

/**
 * 评分 → 颜色（表单元格等按值取色场景）。
 * 统一走 useScoreColor：动态 gradingThresholds 定档，null/NaN → ZL 中性灰
 * （"数据不足"不是"不合格"，严禁渲染为故障红）。
 */
function scoreColor(score: null | number | undefined): string {
  return useScoreColor(score, gradingThresholds).color.value;
}

function formatNumber(val: null | number | undefined, digits = 1): string {
  if (val === null || val === undefined || Number.isNaN(val)) return '--';
  return Number(val).toFixed(digits);
}

async function loadBoard() {
  try {
    const { getBoardAggregateApi, getBoardTrendApi } =
      await import('#/api/dashboard');
    const [aggregate, trend] = await Promise.all([
      getBoardAggregateApi({
        ...(selectedPlantNodeId.value && {
          plantId: selectedPlantNodeId.value,
        }),
        timeWindow: timeWindow.value,
      }),
      getBoardTrendApi({
        ...(selectedPlantNodeId.value && {
          plantId: selectedPlantNodeId.value,
        }),
        timeWindow: timeWindow.value,
      }),
    ]);
    boardAggregate.value = aggregate;
    boardTrend.value = trend;
    await nextTick();
    renderTrendChart();
  } catch (error) {
    console.error('[CLPM] 加载看板数据失败:', error);
  }
}

async function loadAutoRateRt() {
  try {
    const { getAutoRateRtApi } = await import('#/api/dashboard');
    const data = await getAutoRateRtApi(
      selectedPlantNodeId.value ? { plantId: selectedPlantNodeId.value } : {},
    );
    autoRateRt.value = data;
    await nextTick();
  } catch {
    // ignore
  }
}

/**
 * TOP5 排行：服务端排序 + limit 单次请求（原循环分页拉全量仅为喂饼图，
 * 饼图已改走 /grade-distribution 服务端聚合，排行只需首屏 5 条）
 */
async function loadRanking() {
  try {
    const { getRankingApi } = await import('#/api/metric');
    const items = await getRankingApi({
      plantNodeId: selectedPlantNodeId.value,
      timeWindow: timeWindow.value,
      sortBy: 'score',
      sortOrder: top5Sort.value,
      limit: 5,
    });
    rankingList.value = items.filter((it) => it.includeInEvaluation !== false);
  } catch {
    // 错误 toast 由拦截器统一处理；保留旧数据
  }
}

/** timeWindow → 滚动窗口毫秒数（口径同后端 TIME_WINDOWS：today=近 24h） */
const TIME_WINDOW_DURATION_MS: Record<string, number> = {
  last_8_hours: 8 * 3_600_000,
  today: 24 * 3_600_000,
  yesterday: 24 * 3_600_000,
  last_7_days: 7 * 24 * 3_600_000,
  last_30_days: 30 * 24 * 3_600_000,
};

/** 加载等级分布（服务端 GROUP BY 聚合，替代前端全量拉取统计） */
async function loadGradeDistribution() {
  try {
    const { getGradeDistributionApi } = await import('#/api/metric');
    const end = dayjs();
    const durationMs =
      TIME_WINDOW_DURATION_MS[timeWindow.value] ?? 24 * 3_600_000;
    gradeDistribution.value = await getGradeDistributionApi({
      ...(selectedPlantNodeId.value && {
        plantNodeId: selectedPlantNodeId.value,
      }),
      startTime: end.subtract(durationMs, 'millisecond').toISOString(),
      endTime: end.toISOString(),
    });
    await nextTick();
  } catch {
    // 错误 toast 由拦截器统一处理
  }
}

async function loadGradingThresholds() {
  // 整改 C2-1：SPONSOR/EXPERT 无 /configs/* 读取权限，前置跳过避免 403 toast
  if (!canReadConfig.value) return;
  try {
    const { getGradingThresholdsApi } = await import('#/api/metric');
    const data = await getGradingThresholdsApi();
    gradingThresholds.value = data.thresholds ?? [];
  } catch {
    // 加载失败时使用默认阈值
  }
}

function loadAll() {
  loadBoard();
  loadAutoRateRt();
  loadRanking();
  loadGradeDistribution();
  loadValveAlerts();
}

watch(top5Sort, () => loadRanking());

watch(isDark, () => {
  nextTick(() => {
    renderTrendChart();
  });
});

/** 工具栏刷新态（刷新时短暂保持供工具栏反馈） */
const loading = ref(false);

/** 工具栏刷新：重新加载看板全部数据 */
function handleRefresh() {
  loading.value = true;
  loadAll();
  // loadAll 为非阻塞（内部各子任务各自 await），加保护性复位
  setTimeout(() => {
    loading.value = false;
  }, 600);
}

/** 工具栏帮助 */
function handleHelp() {
  showPageHelp({
    title: '评估看板 帮助',
    content:
      '工厂级 KPI 评估看板：实时自控率、性能评分、自控率/平稳率/好值率/仪表故障率 6 仪表盘 + 性能指标趋势图 + 回路等级占比饼图 + 装置/单元性能明细表 + TOP5 回路（可切换升降序）。支持按工厂节点树筛选与时间窗口切换（近 8h / 24h / 168h / 近 1 月）。',
  });
}

// ===== 统一工具栏（标准 2 工具：刷新 / 帮助） =====
const { toolbarItems } = usePageToolbar(() => ({
  refresh: { onClick: handleRefresh, loading: loading.value },
  help: { onClick: handleHelp },
}));

onMounted(() => {
  loadGradingThresholds();
  loadAll();
});
</script>

<template>
  <Page>
    <div class="clpm-pid-dashboard">
      <ClpmPageToolbar
        title="评估看板"
        subtitle="工厂级 KPI 仪表盘 · 趋势 · 等级分布 · TOP5"
        :loading="loading"
      >
        <Button size="small" @click="treeDrawerOpen = true">
          <template #icon>
            <IconifyIcon icon="lucide:git-fork" />
          </template>
          {{ selectedPlantNodeName }}
        </Button>
        <Select
          v-model:value="timeWindow"
          style="width: 140px"
          size="small"
          :options="timeWindowOptions"
          @change="handleTimeWindowChange"
        />
        <template #actions>
          <ClpmStandardActions :items="toolbarItems" />
        </template>
      </ClpmPageToolbar>

      <div class="clpm-pid-dashboard__body">
        <div class="clpm-pid-dashboard__main">
          <div class="clpm-pid-dashboard__top-row">
            <div class="clpm-pid-dashboard__gauge-card">
              <ClpmBulletChart
                label="实时自控率"
                :value="autoRateRt?.rate ?? null"
                :meta="rtReadAtText"
              />
            </div>

            <div class="clpm-pid-dashboard__gauge-card">
              <ClpmBulletChart
                label="性能评分"
                :value="aggregateData?.avgScore ?? null"
                :meta="`统计窗口：${timeWindowLabel}`"
              />
            </div>

            <div class="clpm-pid-dashboard__gauge-card">
              <ClpmBulletChart
                label="自控率"
                :value="aggregateData?.autoModeRate ?? null"
                :meta="`统计窗口：${timeWindowLabel}`"
              />
            </div>

            <div class="clpm-pid-dashboard__gauge-card">
              <ClpmBulletChart
                label="平稳率"
                :value="aggregateData?.stabilityRate ?? null"
                :meta="`统计窗口：${timeWindowLabel}`"
              />
            </div>

            <div class="clpm-pid-dashboard__gauge-card">
              <ClpmBulletChart
                label="好值率"
                :value="aggregateData?.goodValueRate ?? null"
                :meta="`统计窗口：${timeWindowLabel}`"
              />
            </div>

            <div class="clpm-pid-dashboard__gauge-card">
              <ClpmBulletChart
                label="仪表故障率"
                :value="aggregateData?.instrumentFaultRate ?? null"
                :max="30"
                :fair="5"
                :good="10"
                invert
                :meta="`统计窗口：${timeWindowLabel}`"
              />
            </div>
          </div>

          <div class="clpm-pid-dashboard__middle-row">
            <div
              class="clpm-pid-dashboard__chart-card clpm-pid-dashboard__chart-card--status-pie"
            >
              <div class="clpm-pid-dashboard__card-header">
                <span>回路状态统计</span>
                <span
                  class="clpm-pid-dashboard__card-meta"
                  :class="{
                    'clpm-pid-dashboard__card-meta--stale': rtStale,
                  }"
                >
                  {{ rtReadAtText }}
                </span>
              </div>
              <div class="clpm-pid-dashboard__mode-list">
                <div
                  v-for="row in modeRows"
                  :key="row.label"
                  class="clpm-pid-dashboard__dist-row"
                >
                  <span class="clpm-pid-dashboard__dist-label">{{
                    row.label
                  }}</span>
                  <span class="clpm-pid-dashboard__dist-track">
                    <i
                      :style="{
                        width: `${row.pct}%`,
                        background: row.color,
                      }"
                    ></i>
                  </span>
                  <span
                    class="clpm-pid-dashboard__dist-count"
                    :style="
                      row.emphasis ? { color: 'var(--status-error)' } : {}
                    "
                    >{{ row.count }}</span
                  >
                </div>
                <div
                  v-if="modeRows.length === 0"
                  class="py-6 text-center text-xs text-gray-400"
                >
                  暂无实时数据
                </div>
              </div>
            </div>

            <div
              class="clpm-pid-dashboard__chart-card clpm-pid-dashboard__chart-card--trend"
            >
              <div class="clpm-pid-dashboard__card-header">
                <span>性能指标趋势图</span>
              </div>
              <EchartsUI ref="trendChartRef" height="240px" />
            </div>

            <div
              class="clpm-pid-dashboard__chart-card clpm-pid-dashboard__chart-card--pie"
            >
              <div class="clpm-pid-dashboard__card-header">
                <span>回路等级占比</span>
              </div>
              <div class="clpm-pid-dashboard__grade-list">
                <div
                  v-for="row in gradeRows"
                  :key="row.label"
                  class="clpm-pid-dashboard__dist-row"
                >
                  <span class="clpm-pid-dashboard__dist-label">{{
                    row.label
                  }}</span>
                  <span class="clpm-pid-dashboard__dist-track">
                    <i
                      :style="{
                        width: `${row.pct}%`,
                        background: row.color,
                      }"
                    ></i>
                  </span>
                  <span class="clpm-pid-dashboard__dist-count">{{
                    row.count
                  }}</span>
                </div>
              </div>
            </div>
          </div>

          <div class="clpm-pid-dashboard__bottom-row">
            <div class="clpm-pid-dashboard__table-card">
              <div class="clpm-pid-dashboard__card-header">
                <span>装置/单元性能明细表</span>
              </div>
              <Table
                :columns="tableColumns"
                :data-source="tableData"
                :pagination="false"
                :scroll="{ y: 200 }"
              >
                <template #bodyCell="{ column, record }">
                  <template v-if="column.key === 'rating'">
                    <span
                      v-if="record.rating"
                      class="clpm-pid-dashboard__rating-tag"
                      :style="{
                        color: record.ratingColor,
                        backgroundColor: `${record.ratingColor}1A`,
                      }"
                    >
                      {{ ratingLabels[record.rating] }}
                    </span>
                    <span v-else>—</span>
                  </template>
                  <template v-if="column.key === 'autoRate'">
                    <span>{{ record.autoRate }}%</span>
                  </template>
                  <template v-if="column.key === 'smoothRate'">
                    <span>{{ record.smoothRate }}%</span>
                  </template>
                </template>
                <template #emptyText>
                  <ClpmEmptyState
                    scene="data"
                    description="当前装置节点与时间窗内无聚合明细；可切换时间窗或选择其他节点。"
                  />
                </template>
              </Table>
            </div>

            <div class="clpm-pid-dashboard__top5-card">
              <div class="clpm-pid-dashboard__card-header">
                <span>TOP5 治理台账</span>
                <Tooltip
                  :title="
                    top5Sort === 'desc'
                      ? '当前：评分最高，点击切换为最低'
                      : '当前：评分最低，点击切换为最高'
                  "
                >
                  <Button
                    type="text"
                    size="small"
                    class="clpm-pid-dashboard__sort-btn"
                    :aria-label="
                      top5Sort === 'desc'
                        ? '当前按评分最低排序，切换为最高'
                        : '当前按评分最高排序，切换为最低'
                    "
                    @click="top5Sort = top5Sort === 'desc' ? 'asc' : 'desc'"
                  >
                    <IconifyIcon
                      :icon="
                        top5Sort === 'desc'
                          ? 'ant-design:sort-descending-outlined'
                          : 'ant-design:sort-ascending-outlined'
                      "
                    />
                  </Button>
                </Tooltip>
              </div>
              <Table
                :columns="top5Columns"
                :data-source="top5TableData"
                :pagination="false"
                :scroll="{ y: 200 }"
              >
                <template #bodyCell="{ column, record }">
                  <template v-if="column.key === 'rating'">
                    <Tooltip
                      v-if="record.isFitnessNA"
                      :title="record.fitnessNATipText"
                      placement="top"
                    >
                      <Tag :color="record.ratingColor || 'default'" class="mr-0">
                        {{ record.ratingText }}
                      </Tag>
                    </Tooltip>
                    <Tag
                      v-else-if="record.ratingColor"
                      :color="record.ratingColor"
                      class="mr-0"
                    >
                      {{ record.ratingText }}
                    </Tag>
                    <span v-else class="text-neutral-400">—</span>
                  </template>
                  <template v-else-if="column.key === 'score'">
                    <Tooltip
                      v-if="record.isFitnessNA"
                      :title="record.fitnessNATipText"
                      placement="top"
                    >
                      <span :style="{ color: record.scoreColor }">{{
                        record.score
                      }}</span>
                    </Tooltip>
                    <span v-else :style="{ color: record.scoreColor }">{{
                      record.score
                    }}</span>
                  </template>
                </template>
                <template #emptyText>
                  <ClpmEmptyState
                    scene="data"
                    description="当前时间窗内暂无参评回路快照；可先发起性能评估。"
                  />
                </template>
              </Table>
            </div>

            <!-- 整改 F4：阀门运行区间异常卡 -->
            <div class="clpm-pid-dashboard__valve-card">
              <div class="clpm-pid-dashboard__card-header">
                <span>阀门运行区间异常</span>
                <span class="clpm-pid-dashboard__card-meta">
                  {{ valveAlerts.length }} 回路越限
                </span>
              </div>
              <div
                v-if="valveAlerts.length === 0"
                class="py-4 text-center text-xs text-gray-400"
              >
                无越限回路（OP 行程超出 5%~95% 为越限）
              </div>
              <div
                v-for="item in valveAlerts"
                :key="item.loopId"
                class="clpm-pid-dashboard__valve-row"
              >
                <span class="font-mono text-xs">{{ item.tagName }}</span>
                <span
                  class="text-xs"
                  :style="{ color: 'var(--status-warning)' }"
                  >{{ item.range }}</span
                >
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 整改 A-13：工厂导航抽屉 -->
    <Drawer
      v-model:open="treeDrawerOpen"
      title="工厂导航"
      placement="left"
      :width="300"
    >
      <PlantNodeTree card-title="" :width="260" @select="onTreeSelect" />
    </Drawer>
  </Page>
</template>

<style lang="scss" scoped>
/*
 * 配色统一走 vben 设计令牌 CSS 变量（--background/--card/--foreground/
 * --muted-foreground/--border/--primary/--muted），明暗主题自动响应，
 * 不再需要 .dark 覆写块。
 */
.clpm-pid-dashboard {
  min-height: 100vh;
  color: hsl(var(--foreground));
  background: linear-gradient(
    180deg,
    hsl(var(--background)) 0%,
    hsl(var(--background-deep)) 100%
  );
}

.clpm-pid-dashboard__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 56px;
  padding: 10px 24px;
  background: linear-gradient(
    90deg,
    hsl(var(--card)) 0%,
    hsl(var(--primary) / 8%) 50%,
    hsl(var(--card)) 100%
  );
  border-bottom: 1px solid hsl(var(--border));
}

.clpm-pid-dashboard__header-left {
  display: flex;
  align-items: center;
}

.clpm-pid-dashboard__header-right {
  display: flex;
  align-items: center;
}

.clpm-pid-dashboard__title {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  color: hsl(var(--foreground));
}

.clpm-pid-dashboard__body {
  display: flex;
  gap: 12px;
  height: calc(100vh - 56px);
  padding: 12px;
}

.clpm-pid-dashboard__main {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 8px;
  overflow-y: auto;
}

.clpm-pid-dashboard__top-row {
  display: flex;
  gap: 12px;

  & > * {
    flex: 1;
  }
}

.clpm-pid-dashboard__gauge-card {
  display: flex;
  flex-direction: column;
  gap: 2px;
  align-items: center;
  padding: 8px;
  background: hsl(var(--card) / 80%);
  border: 1px solid hsl(var(--border));
  border-radius: 8px;

  &-title {
    font-size: 12px;
    color: hsl(var(--muted-foreground));
  }

  &-value {
    font-size: 16px;
    font-weight: 600;
    color: hsl(var(--foreground));
  }
}

.clpm-pid-dashboard__gauge-meta {
  font-size: 10px;
  line-height: 1.2;
  color: hsl(var(--muted-foreground));

  &--stale {
    color: hsl(var(--muted-foreground) / 60%);
  }
}

.clpm-pid-dashboard__card-meta {
  font-size: 11px;
  font-weight: 400;
  color: hsl(var(--muted-foreground));

  &--stale {
    color: hsl(var(--muted-foreground) / 60%);
  }
}

.clpm-pid-dashboard__middle-row {
  display: flex;
  gap: 8px;
}

.clpm-pid-dashboard__chart-card {
  padding: 8px 12px;
  background: hsl(var(--card) / 80%);
  border: 1px solid hsl(var(--border));
  border-radius: 8px;

  &--status-pie {
    width: 20%;
  }

  &--trend {
    width: 60%;
  }

  &--pie {
    width: 20%;
  }
}

.clpm-pid-dashboard__card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
  font-size: 14px;
  font-weight: 500;
  color: hsl(var(--foreground));
}

.clpm-pid-dashboard__sort-btn {
  padding: 2px 6px;
  color: hsl(var(--muted-foreground));
  cursor: pointer;

  &:hover {
    color: hsl(var(--primary));
  }
}

.clpm-pid-dashboard__bottom-row {
  display: flex;
  flex: 1;
  gap: 8px;
}

.clpm-pid-dashboard__table-card {
  display: flex;
  flex-direction: column;
  width: 40%;
  padding: 8px 12px;
  background: hsl(var(--card) / 80%);
  border: 1px solid hsl(var(--border));
  border-radius: 8px;
}

/* D1/D4：诊断聚合卡 + 整改有效率卡行（两列并列，窄屏堆叠） */
.clpm-pid-dashboard__diag-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
  margin-top: 8px;

  @media (width <= 1200px) {
    grid-template-columns: 1fr;
  }
}

.clpm-pid-dashboard__top5-card {
  display: flex;
  flex-direction: column;
  width: 32%;
  padding: 8px 12px;
  background: hsl(var(--card) / 80%);
  border: 1px solid hsl(var(--border));
  border-radius: 8px;
}

.clpm-pid-dashboard__top5-card :deep(.ant-table-tbody > tr > td) {
  white-space: nowrap;
}

/* 整改 A-13：分布行列表（donut/饼图替代） */
.clpm-pid-dashboard__dist-row {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 3px 0;
  font-size: 12px;
}

.clpm-pid-dashboard__dist-label {
  flex-shrink: 0;
  width: 56px;
  color: hsl(var(--muted-foreground));
}

.clpm-pid-dashboard__dist-track {
  flex: 1;
  height: 8px;
  overflow: hidden;
  background: var(--color-slate-100);
  border-radius: 2px;
}

.clpm-pid-dashboard__dist-track i {
  display: block;
  height: 100%;
  border-radius: 2px;
}

.clpm-pid-dashboard__dist-count {
  flex-shrink: 0;
  width: 32px;
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  text-align: right;
}

/* 整改 F4：阀门运行区间异常卡 */
.clpm-pid-dashboard__valve-card {
  display: flex;
  flex-direction: column;
  width: 28%;
  padding: 8px 12px;
  background: hsl(var(--card) / 80%);
  border: 1px solid hsl(var(--border));
  border-radius: 8px;
}

.clpm-pid-dashboard__valve-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 0;
  border-bottom: 1px dashed hsl(var(--border));
}

.clpm-pid-dashboard__valve-row:last-child {
  border-bottom: none;
}

/* 评级标签底色为行内 style（等级色 + 10% 透明背景，色值随阈值配置），
   此处仅保留布局属性 */
.clpm-pid-dashboard__rating-tag {
  padding: 2px 8px;
  font-size: 12px;
  border-radius: 4px;
}

:deep(.ant-table) {
  background: transparent;

  .ant-table-header {
    background: hsl(var(--muted) / 50%);
  }

  .ant-table-body {
    background: transparent;
  }

  .ant-table-cell {
    padding: 6px 8px;
    font-size: 12px;
    line-height: 1.4;
    color: hsl(var(--foreground));
    border-bottom: 1px solid hsl(var(--border));
  }

  .ant-table-thead > tr > th {
    padding: 8px;
    font-size: 12px;
    font-weight: 500;
    color: hsl(var(--muted-foreground));
    background: hsl(var(--muted) / 50%);
    border-bottom: 1px solid hsl(var(--border));
  }

  .ant-table-tbody > tr:hover > td {
    background: hsl(var(--primary) / 5%);
  }

  .ant-table-tbody > tr {
    height: 32px;
  }
}

:deep(.ant-select-selector) {
  color: hsl(var(--foreground)) !important;
  background: hsl(var(--muted) / 50%) !important;
  border: 1px solid hsl(var(--border)) !important;
}

:deep(.ant-btn) {
  color: hsl(var(--primary));
  background: hsl(var(--primary) / 10%);
  border: 1px solid hsl(var(--primary));
}
</style>
