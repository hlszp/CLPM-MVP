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
import { useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';
import { IconifyIcon } from '@vben/icons';
import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import { Button, message, Select, Table, Tooltip } from 'ant-design-vue';
import dayjs from 'dayjs';

import {
  ClpmEmptyState,
  ClpmBulletChart,
  ClpmPageToolbar,
  ClpmStandardActions,
} from '#/components/clpm';
import PlantNodeTree from '#/components/plant-node/plant-node-tree.vue';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import { useConfigAccess } from '#/composables/use-config-access';
import { MODE_COLOR_MAP } from '#/composables/use-loop-palettes';
import { showPageHelp, usePageToolbar } from '#/composables/use-page-toolbar';
import { useScoreColor } from '#/composables/use-score-color';
import { normalizeUtcTimestamp } from '#/utils/format';
import DiagnosisSummaryCard from '#/views/diagnosis/components/diagnosis-summary-card.vue';
import TrackerEffectivenessCard from '#/views/diagnosis/components/tracker-effectiveness-card.vue';

defineOptions({ name: 'PidDashboard' });

const { isDark, themeColors, chartColors } = useClpmTheme();
const { canReadConfig } = useConfigAccess();
const router = useRouter();

const timeWindowOptions = [
  { label: '近8小时', value: 'last_8_hours' },
  { label: '24小时', value: 'today' },
  { label: '168小时', value: 'last_7_days' },
  { label: '近1月', value: 'last_30_days' },
];

const timeWindow = ref<TimeWindow>('today');

/** 当前时间窗中文标签（gauges 卡片统计窗口标注） */
const timeWindowLabel = computed(
  () =>
    timeWindowOptions.find((o) => o.value === timeWindow.value)?.label ?? '',
);

const selectedPlantNodeId = ref<string | undefined>(undefined);
const selectedPlantNodeName = ref<string>('全厂');

function onTreeSelect(node: null | PlantNodeApi.PlantNode) {
  if (node) {
    selectedPlantNodeId.value = node.id;
    selectedPlantNodeName.value = node.name;
  } else {
    selectedPlantNodeId.value = undefined;
    selectedPlantNodeName.value = '全厂';
  }
  loadAll();
}

function handleTimeWindowChange() {
  loadAll();
}

const boardAggregate = ref<DashboardApi.BoardAggregateResult | null>(null);
const boardTrend = ref<DashboardApi.BoardTrendResult | null>(null);
const autoRateRt = ref<DashboardApi.AutoRateRt | null>(null);

/** 实时数据过期阈值（分钟），超过则标灰/警示 */
const RT_STALE_MINUTES = 10;

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
const diagnosisLoading = ref(false);
const gradingThresholds = ref<MetricApi.GradingThresholdItem[]>([]);
/** 各性能等级回路数分布（服务端 SQL 聚合，喂"回路等级占比"饼图） */
const gradeDistribution = ref<GradeDistributionResult | null>(null);

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
  {
    title: '',
    dataIndex: 'diagnosis',
    key: 'diagnosis',
    width: 40,
    align: 'center' as const,
  },
];

const top5TableData = computed(() => {
  return top5List.value.map((item, index) => {
    return {
      key: item.loopId,
      index: index + 1,
      loopId: item.loopId,
      tagName: item.tagName,
      loopName: item.loopName || item.tagName || '—',
      score: formatNumber(item.score),
      scoreColor: scoreColor(item.score),
      steadyRate: `${formatNumber(item.steadyRate)}%`,
    };
  });
});

const trendChartRef = ref<EchartsUIType>();
const pieChartRef = ref<EchartsUIType>();
const statusPieChartRef = ref<EchartsUIType>();

const { renderEcharts: renderTrend } = useEcharts(trendChartRef);
const { renderEcharts: renderPie } = useEcharts(pieChartRef);
const { renderEcharts: renderStatusPie } = useEcharts(statusPieChartRef);

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

  renderTrend({
    grid: { bottom: 40, left: '2%', right: '2%', top: 20, containLabel: true },
    xAxis: {
      type: 'category',
      data: timestamps,
      axisLabel: {
        color: chartColors.value.text,
        fontSize: 10,
        rotate: timestamps.length > 12 ? 45 : 0,
      },
      axisLine: { lineStyle: { color: chartColors.value.splitLine } },
      axisTick: { show: false },
    },
    yAxis: [
      {
        type: 'value',
        name: '回路数',
        nameTextStyle: { color: chartColors.value.text, fontSize: 11 },
        axisLabel: { color: chartColors.value.text, fontSize: 10 },
        splitLine: {
          lineStyle: { color: chartColors.value.splitLine, type: 'dashed' },
        },
      },
      {
        type: 'value',
        name: '百分比(%)',
        nameTextStyle: { color: chartColors.value.text, fontSize: 11 },
        axisLabel: {
          color: chartColors.value.text,
          fontSize: 10,
          formatter: '{value}%',
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

function renderStatusPieChart() {
  const rt = autoRateRt.value;
  const total = rt?.totalCount ?? 0;

  // 5 种标准 MODE 值的回路数与中文标签（对齐 app.constants.mode）
  // 0=手动, 1=自动, 2=串级, 3=远程, 4=先控；配色统一走共享色板 use-loop-palettes
  const MODE_LABELS: Record<string, string> = {
    '0': '手动',
    '1': '自动',
    '2': '串级',
    '3': '远程',
    '4': '先控',
  };

  const modeCounts = rt?.modeCounts ?? {};
  const allPieData = Object.keys(MODE_LABELS).map((modeKey) => {
    return {
      value: modeCounts[modeKey] ?? 0,
      name: MODE_LABELS[modeKey] ?? modeKey,
      itemStyle: {
        color: MODE_COLOR_MAP[modeKey] ?? themeColors.value.NEUTRAL,
      },
    };
  });

  // 仅展示有数据的 MODE（全部为 0 时显示全部以便占位）
  const pieData =
    total === 0 ? allPieData : allPieData.filter((d) => (d.value ?? 0) > 0);
  const legendNames = pieData.map((d) => d.name);

  renderStatusPie({
    tooltip: {
      trigger: 'item',
      position: 'right',
      formatter: (params: any) => {
        const percent =
          total > 0 ? ((params.value / total) * 100).toFixed(1) : 0;
        return `${params.name}: ${params.value} (${percent}%)`;
      },
    },
    legend: {
      bottom: 0,
      textStyle: { color: chartColors.value.text, fontSize: 11 },
      data: legendNames,
    },
    series: [
      {
        type: 'pie' as const,
        radius: '70%',
        center: ['50%', '45%'],
        avoidLabelOverlap: false,
        itemStyle: {
          borderRadius: 4,
          borderColor: chartColors.value.border,
          borderWidth: 2,
        },
        label: { show: false },
        emphasis: {
          label: {
            show: true,
            fontSize: 12,
            fontWeight: 'bold',
            color: chartColors.value.textStrong,
          },
        },
        labelLine: { show: false },
        data: pieData,
      },
    ],
  });
}

function renderPieChart() {
  const dist = gradeDistribution.value;
  if (!dist) return;

  const distMap = dist as unknown as Record<string, number>;
  // 按等级顺序（1→5）生成饼图数据；INCONCLUSIVE（数据不足）以中性灰单列
  const pieData = [
    ...[...effectiveThresholds.value]
      .toSorted((a, b) => a.level - b.level)
      .map((t) => ({
        value: distMap[t.name] ?? 0,
        name: ratingLabels.value[String(t.level)] ?? t.name,
        itemStyle: { color: gradeColor(t.level) },
      })),
    {
      value: dist.INCONCLUSIVE ?? 0,
      name: '数据不足',
      itemStyle: { color: themeColors.value.NEUTRAL },
    },
  ];

  const total = dist.total ?? 0;

  renderPie({
    tooltip: {
      trigger: 'item',
      formatter: (params: any) => {
        const percent =
          total > 0 ? ((params.value / total) * 100).toFixed(1) : 0;
        return `${params.name}: ${params.value}个 (${percent}%)`;
      },
    },
    legend: {
      bottom: 0,
      textStyle: { color: chartColors.value.text, fontSize: 11 },
      data: pieData
        .filter((d: { value: number }) => (d.value ?? 0) > 0 || total === 0)
        .map((d: { name: string }) => d.name),
    },
    series: [
      {
        type: 'pie' as const,
        radius: '70%',
        center: ['50%', '45%'],
        avoidLabelOverlap: false,
        itemStyle: {
          borderRadius: 4,
          borderColor: chartColors.value.border,
          borderWidth: 2,
        },
        label: { show: false },
        emphasis: {
          label: {
            show: true,
            fontSize: 12,
            fontWeight: 'bold',
            color: chartColors.value.textStrong,
          },
        },
        labelLine: { show: false },
        data: pieData.filter(
          (d: { itemStyle: { color: string }; name: string; value: number }) =>
            (d.value ?? 0) > 0 || total === 0,
        ),
      },
    ],
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

async function handleDiagnosis(loopId: string) {
  if (diagnosisLoading.value) return;
  diagnosisLoading.value = true;
  try {
    const { triggerDiagnosisApi } = await import('#/api/diagnosis');
    await triggerDiagnosisApi({ loopIds: [loopId] });
    message.success('诊断任务已创建');
    router.push('/diagnosis/tasks');
  } catch {
    // 错误 toast 由 api/request.ts 拦截器统一弹出，视图层不重复提示
    console.error('[CLPM] 创建诊断任务失败');
  } finally {
    diagnosisLoading.value = false;
  }
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
    renderPieChart();
    renderStatusPieChart();
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
    renderStatusPieChart();
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
    renderPieChart();
  } catch {
    // 错误 toast 由拦截器统一处理；饼图保留旧数据
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
}

watch(top5Sort, () => loadRanking());

watch(isDark, () => {
  nextTick(() => {
    renderTrendChart();
    renderPieChart();
    renderStatusPieChart();
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
      '工厂级 KPI 评估看板：实时自控率、性能评分、自控率/平稳率/好值率/仪表故障率 6 仪表盘 + 性能指标趋势图 + 回路等级占比饼图 + 装置/单元性能明细表 + TOP5 回路（可切换升降序）。支持按工厂节点树筛选与时间窗口切换（近 8h / 24h / 168h / 近 1 月）。点击 TOP5 行右侧箭头可一键发起该回路诊断。',
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
        <PlantNodeTree
          card-title="工厂导航"
          :width="200"
          @select="onTreeSelect"
        />

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
              <EchartsUI ref="statusPieChartRef" height="200px" />
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
              <EchartsUI ref="pieChartRef" height="240px" />
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
                <span>TOP5回路</span>
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
                  <template v-if="column.key === 'score'">
                    <span :style="{ color: record.scoreColor }">{{
                      record.score
                    }}</span>
                  </template>
                  <template v-if="column.key === 'diagnosis'">
                    <Button
                      type="text"
                      size="small"
                      :loading="diagnosisLoading"
                      @click="handleDiagnosis(record.loopId)"
                    >
                      <template #icon>
                        <IconifyIcon icon="ant-design:right-outlined" />
                      </template>
                    </Button>
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
          </div>

          <!-- D1/D4：诊断聚合卡 + 整改有效率卡（门户卡，两列并列） -->
          <div class="clpm-pid-dashboard__diag-row">
            <DiagnosisSummaryCard />
            <TrackerEffectivenessCard />
          </div>
        </div>
      </div>
    </div>
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
  width: 50%;
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
  width: 50%;
  padding: 8px 12px;
  background: hsl(var(--card) / 80%);
  border: 1px solid hsl(var(--border));
  border-radius: 8px;
}

.clpm-pid-dashboard__top5-card :deep(.ant-table-tbody > tr > td) {
  white-space: nowrap;
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
