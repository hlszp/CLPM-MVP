<script lang="ts" setup>
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { DashboardApi, MetricApi, TimeWindow } from '#/api';
import type { PlantNodeApi } from '#/api/plant-node';

import { computed, nextTick, onMounted, ref, watch } from 'vue';
import { useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';
import { IconifyIcon } from '@vben/icons';
import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import { Button, message, Select, Table, Tooltip } from 'ant-design-vue';
import dayjs from 'dayjs';

import PlantNodeTree from '#/components/plant-node/plant-node-tree.vue';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import { normalizeUtcTimestamp } from '#/utils/format';

defineOptions({ name: 'PidDashboard' });

const { isDark, themeColors, chartColors } = useClpmTheme();
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

const top5Sort = ref<'asc' | 'desc'>('desc');

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

// 默认定级阈值（国标 GB/T 44693.2-2024 §6.3）
const DEFAULT_THRESHOLDS: MetricApi.GradingThresholdItem[] = [
  {
    level: 1,
    name: 'EXCELLENT',
    label: '优秀',
    minScore: 90,
    maxScore: 100,
    color: '#52c41a',
  },
  {
    level: 2,
    name: 'GOOD',
    label: '良好',
    minScore: 80,
    maxScore: 90,
    color: '#1890ff',
  },
  {
    level: 3,
    name: 'FAIR',
    label: '合格',
    minScore: 60,
    maxScore: 80,
    color: '#faad14',
  },
  {
    level: 4,
    name: 'WARNING',
    label: '警告',
    minScore: 40,
    maxScore: 60,
    color: '#fa8c16',
  },
  {
    level: 5,
    name: 'POOR',
    label: '不合格',
    minScore: 0,
    maxScore: 40,
    color: '#f5222d',
  },
];

// 定级阈值等级中文显示名（从配置读取，降级用默认值）
const ratingLabels = computed<Record<string, string>>(() => {
  const labels: Record<string, string> = {};
  const thresholds =
    gradingThresholds.value.length > 0
      ? gradingThresholds.value
      : DEFAULT_THRESHOLDS;
  for (const t of thresholds) {
    labels[String(t.level)] = t.label ?? t.name;
  }
  return labels;
});

function getRatingLevel(score: number): string {
  const thresholds =
    gradingThresholds.value.length > 0
      ? gradingThresholds.value
      : DEFAULT_THRESHOLDS;
  // 按 minScore 降序匹配（level 1 = 最高分区间）
  for (const t of [...thresholds].toSorted(
    (a: MetricApi.GradingThresholdItem, b: MetricApi.GradingThresholdItem) =>
      b.minScore - a.minScore,
  )) {
    if (score >= t.minScore) return String(t.level);
  }
  return '5'; // 最低等级
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
    const score = item.avgScore ?? 0;
    return {
      key: item.nodeId,
      index: index + 1,
      name: item.nodeName ?? '',
      rating: getRatingLevel(score),
      score: formatNumber(score),
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
      steadyRate: `${formatNumber(item.steadyRate)}%`,
    };
  });
});

const gauge1Ref = ref<EchartsUIType>();
const gauge2Ref = ref<EchartsUIType>();
const gauge3Ref = ref<EchartsUIType>();
const gauge4Ref = ref<EchartsUIType>();
const gauge5Ref = ref<EchartsUIType>();
const trendChartRef = ref<EchartsUIType>();
const pieChartRef = ref<EchartsUIType>();
const statusPieChartRef = ref<EchartsUIType>();

const { renderEcharts: renderGauge1 } = useEcharts(gauge1Ref);
const { renderEcharts: renderGauge2 } = useEcharts(gauge2Ref);
const { renderEcharts: renderGauge3 } = useEcharts(gauge3Ref);
const { renderEcharts: renderGauge4 } = useEcharts(gauge4Ref);
const { renderEcharts: renderGauge5 } = useEcharts(gauge5Ref);
const { renderEcharts: renderTrend } = useEcharts(trendChartRef);
const { renderEcharts: renderPie } = useEcharts(pieChartRef);
const { renderEcharts: renderStatusPie } = useEcharts(statusPieChartRef);

function renderGaugeOption(value: number, color: string) {
  return {
    series: [
      {
        type: 'gauge' as const,
        startAngle: 220,
        endAngle: -40,
        min: 0,
        max: 100,
        splitNumber: 5,
        radius: '108%',
        center: ['50%', '55%'],
        axisLine: {
          lineStyle: {
            width: 6,
            color: [
              [0.3, themeColors.value.DANGER],
              [0.5, themeColors.value.WARNING],
              [0.75, themeColors.value.INFO],
              [1, themeColors.value.SUCCESS],
            ],
          },
        },
        pointer: {
          length: '50%',
          width: 3,
          itemStyle: { color },
        },
        axisTick: {
          distance: -11,
          length: 5,
          lineStyle: { color: chartColors.value.text, width: 1 },
        },
        splitLine: {
          distance: -14,
          length: 18,
          lineStyle: { color: chartColors.value.text, width: 2 },
        },
        axisLabel: {
          color: chartColors.value.text,
          fontSize: 9,
          distance: 14,
        },
        detail: { show: false },
        data: [{ value, name: '' }],
      },
    ],
  } as any;
}

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

  // 5 种标准 MODE 值的回路数与中文标签 / 配色（对齐 app.constants.mode）
  // 0=手动, 1=自动, 2=串级, 3=远程, 4=先控
  const MODE_LABELS: Record<number, string> = {
    0: '手动',
    1: '自动',
    2: '串级',
    3: '远程',
    4: '先控',
  };
  const MODE_COLORS: Record<number, string> = {
    0: '#d4380d', // 红橙 - 手动（警示）
    1: '#52c41a', // 绿 - 自动（正常）
    2: '#1890ff', // 蓝 - 串级
    3: '#722ed1', // 紫 - 远程
    4: '#13c2c2', // 青 - 先控
  };

  const modeCounts = rt?.modeCounts ?? {};
  const allPieData = Object.keys(MODE_LABELS).map((modeKey) => {
    const mode = Number.parseInt(modeKey, 10);
    const count = modeCounts[modeKey] ?? 0;
    return {
      value: count,
      name: MODE_LABELS[mode] ?? modeKey,
      itemStyle: { color: MODE_COLORS[mode] ?? '#999' },
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
  // 按回路评分均值计算等级占比（使用 rankingList 中的逐回路数据）
  const loops = rankingList.value;
  const thresholds =
    gradingThresholds.value.length > 0
      ? gradingThresholds.value
      : DEFAULT_THRESHOLDS;

  // 按等级统计回路数量（level 1=优秀 ~ level 5=不合格）
  const levelCounts: Record<number, number> = { 1: 0, 2: 0, 3: 0, 4: 0, 5: 0 };
  loops.forEach((item) => {
    const score = item.score ?? 0;
    const level = Number.parseInt(getRatingLevel(score), 10);
    levelCounts[level] = (levelCounts[level] ?? 0) + 1;
  });

  const total = loops.length;

  // 按等级顺序（1→5）生成饼图数据
  const pieData = [...thresholds]
    .toSorted(
      (a: MetricApi.GradingThresholdItem, b: MetricApi.GradingThresholdItem) =>
        a.level - b.level,
    )
    .map((t: MetricApi.GradingThresholdItem) => ({
      value: levelCounts[t.level] ?? 0,
      name: ratingLabels.value[String(t.level)] ?? t.name,
      itemStyle: { color: t.color ?? themeColors.value.SUCCESS },
    }));

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

function scoreColor(score: null | number | undefined): string {
  if (score === null || score === undefined) return themeColors.value.DANGER;
  if (score >= 90) return themeColors.value.SUCCESS;
  if (score >= 80) return themeColors.value.INFO;
  if (score >= 70) return themeColors.value.WARNING;
  if (score >= 60) return '#f97316';
  return themeColors.value.DANGER;
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
    message.error('创建诊断任务失败');
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
    updateGauges();
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
    updateGauges();
    renderStatusPieChart();
  } catch {
    // ignore
  }
}

async function loadRanking() {
  try {
    const { getRankingApi } = await import('#/api/metric');
    const data = await getRankingApi({
      plantNodeId: selectedPlantNodeId.value,
      timeWindow: timeWindow.value,
      sortBy: 'score',
      sortOrder: top5Sort.value,
      limit: 100,
    });
    rankingList.value = data.filter((it) => it.includeInEvaluation !== false);
    await nextTick();
    renderPieChart();
  } catch {
    // ignore
  }
}

async function loadGradingThresholds() {
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
}

function updateGauges() {
  renderGauge1(
    renderGaugeOption(autoRateRt.value?.rate ?? 0, themeColors.value.INFO),
  );
  renderGauge2(
    renderGaugeOption(
      aggregateData.value?.avgScore ?? 0,
      scoreColor(aggregateData.value?.avgScore),
    ),
  );
  renderGauge3(
    renderGaugeOption(
      aggregateData.value?.autoModeRate ?? 0,
      themeColors.value.SUCCESS,
    ),
  );
  renderGauge4(
    renderGaugeOption(
      aggregateData.value?.stabilityRate ?? 0,
      themeColors.value.WARNING,
    ),
  );
  renderGauge5(
    renderGaugeOption(
      aggregateData.value?.goodValueRate ?? 0,
      themeColors.value.SUCCESS,
    ),
  );
}

watch(top5Sort, () => loadRanking());

watch(isDark, () => {
  nextTick(() => {
    updateGauges();
    renderTrendChart();
    renderPieChart();
    renderStatusPieChart();
  });
});

onMounted(() => {
  loadGradingThresholds();
  loadAll();
});
</script>

<template>
  <Page>
    <div class="clpm-pid-dashboard">
      <div class="clpm-pid-dashboard__header">
        <div class="clpm-pid-dashboard__header-left">
          <h1 class="clpm-pid-dashboard__title">评估看板</h1>
        </div>
        <div class="clpm-pid-dashboard__header-right">
          <Select
            v-model:value="timeWindow"
            style="width: 140px"
            size="small"
            :options="timeWindowOptions"
            @change="handleTimeWindowChange"
          />
        </div>
      </div>

      <div class="clpm-pid-dashboard__body">
        <PlantNodeTree
          card-title="工厂导航"
          :width="200"
          @select="onTreeSelect"
        />

        <div class="clpm-pid-dashboard__main">
          <div class="clpm-pid-dashboard__top-row">
            <div class="clpm-pid-dashboard__gauge-card">
              <div class="clpm-pid-dashboard__gauge-title">实时自控率</div>
              <EchartsUI ref="gauge1Ref" height="126px" />
              <div class="clpm-pid-dashboard__gauge-value">
                {{ autoRateRt?.rate ?? '--' }}%
              </div>
              <div
                class="clpm-pid-dashboard__gauge-meta"
                :class="{
                  'clpm-pid-dashboard__gauge-meta--stale': rtStale,
                }"
              >
                {{ rtReadAtText }}
              </div>
            </div>

            <div class="clpm-pid-dashboard__gauge-card">
              <div class="clpm-pid-dashboard__gauge-title">性能评分</div>
              <EchartsUI ref="gauge2Ref" height="126px" />
              <div
                class="clpm-pid-dashboard__gauge-value"
                :style="{ color: scoreColor(aggregateData?.avgScore) }"
              >
                {{ aggregateData?.avgScore ?? '--' }}%
              </div>
              <div class="clpm-pid-dashboard__gauge-meta">
                统计窗口：{{ timeWindowLabel }}
              </div>
            </div>

            <div class="clpm-pid-dashboard__gauge-card">
              <div class="clpm-pid-dashboard__gauge-title">自控率</div>
              <EchartsUI ref="gauge3Ref" height="126px" />
              <div class="clpm-pid-dashboard__gauge-value">
                {{ aggregateData?.autoModeRate ?? '--' }}%
              </div>
              <div class="clpm-pid-dashboard__gauge-meta">
                统计窗口：{{ timeWindowLabel }}
              </div>
            </div>

            <div class="clpm-pid-dashboard__gauge-card">
              <div class="clpm-pid-dashboard__gauge-title">平稳率</div>
              <EchartsUI ref="gauge4Ref" height="126px" />
              <div class="clpm-pid-dashboard__gauge-value">
                {{ aggregateData?.stabilityRate ?? '--' }}%
              </div>
              <div class="clpm-pid-dashboard__gauge-meta">
                统计窗口：{{ timeWindowLabel }}
              </div>
            </div>

            <div class="clpm-pid-dashboard__gauge-card">
              <div class="clpm-pid-dashboard__gauge-title">好值率</div>
              <EchartsUI ref="gauge5Ref" height="126px" />
              <div class="clpm-pid-dashboard__gauge-value">
                {{ aggregateData?.goodValueRate ?? '--' }}%
              </div>
              <div class="clpm-pid-dashboard__gauge-meta">
                统计窗口：{{ timeWindowLabel }}
              </div>
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
                      class="clpm-pid-dashboard__rating-tag"
                      :class="[
                        `clpm-pid-dashboard__rating-tag--${record.rating}`,
                      ]"
                    >
                      {{ ratingLabels[record.rating] }}
                    </span>
                  </template>
                  <template v-if="column.key === 'autoRate'">
                    <span>{{ record.autoRate }}%</span>
                  </template>
                  <template v-if="column.key === 'smoothRate'">
                    <span>{{ record.smoothRate }}%</span>
                  </template>
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
                    <span :style="{ color: scoreColor(record.score) }">{{
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
              </Table>
            </div>
          </div>
        </div>
      </div>
    </div>
  </Page>
</template>

<style lang="scss" scoped>
.clpm-pid-dashboard {
  min-height: 100vh;
  color: #334155;
  background: linear-gradient(180deg, #f8fafc 0%, #f1f5f9 100%);
}

.dark .clpm-pid-dashboard {
  color: #e2e8f0;
  background: linear-gradient(180deg, #0f172a 0%, #1e293b 100%);
}

.clpm-pid-dashboard__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 56px;
  padding: 10px 24px;
  background: linear-gradient(90deg, #fff 0%, #eff6ff 50%, #fff 100%);
  border-bottom: 1px solid #e2e8f0;
}

.dark .clpm-pid-dashboard__header {
  background: linear-gradient(90deg, #0f172a 0%, #1e3a5f 50%, #0f172a 100%);
  border-bottom: 1px solid #334155;
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
  color: #1e293b;
}

.dark .clpm-pid-dashboard__title {
  color: #f1f5f9;
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
  background: rgb(255 255 255 / 80%);
  border: 1px solid #e2e8f0;
  border-radius: 8px;

  &-title {
    font-size: 12px;
    color: #64748b;
  }

  &-value {
    font-size: 16px;
    font-weight: 600;
    color: #1e293b;
  }
}

.dark .clpm-pid-dashboard__gauge-card {
  background: rgb(15 23 42 / 80%);
  border: 1px solid #334155;

  &-title {
    color: #94a3b8;
  }

  &-value {
    color: #f1f5f9;
  }
}

.clpm-pid-dashboard__gauge-meta {
  font-size: 10px;
  line-height: 1.2;
  color: #94a3b8;

  &--stale {
    color: #cbd5e1;
  }
}

.dark .clpm-pid-dashboard__gauge-meta {
  color: #64748b;

  &--stale {
    color: #475569;
  }
}

.clpm-pid-dashboard__card-meta {
  font-size: 11px;
  font-weight: 400;
  color: #94a3b8;

  &--stale {
    color: #cbd5e1;
  }
}

.dark .clpm-pid-dashboard__card-meta {
  color: #64748b;

  &--stale {
    color: #475569;
  }
}

.clpm-pid-dashboard__middle-row {
  display: flex;
  gap: 8px;
}

.clpm-pid-dashboard__chart-card {
  padding: 8px 12px;
  background: rgb(255 255 255 / 80%);
  border: 1px solid #e2e8f0;
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

.dark .clpm-pid-dashboard__chart-card {
  background: rgb(15 23 42 / 80%);
  border: 1px solid #334155;
}

.clpm-pid-dashboard__card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
  font-size: 14px;
  font-weight: 500;
  color: #334155;
}

.dark .clpm-pid-dashboard__card-header {
  color: #e2e8f0;
}

.clpm-pid-dashboard__sort-btn {
  padding: 2px 6px;
  color: #64748b;
  cursor: pointer;

  &:hover {
    color: #3b82f6;
  }
}

.dark .clpm-pid-dashboard__sort-btn {
  color: #94a3b8;

  &:hover {
    color: #3b82f6;
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
  background: rgb(255 255 255 / 80%);
  border: 1px solid #e2e8f0;
  border-radius: 8px;
}

.dark .clpm-pid-dashboard__table-card {
  background: rgb(15 23 42 / 80%);
  border: 1px solid #334155;
}

.dark .clpm-pid-dashboard__table-card :deep(.ant-table-content) {
  color: #f1f5f9;
}

.dark .clpm-pid-dashboard__table-card :deep(.ant-table-thead > tr > th) {
  color: #94a3b8;
}

.dark .clpm-pid-dashboard__table-card :deep(.ant-table-tbody > tr > td) {
  color: #f1f5f9;
}

.clpm-pid-dashboard__top5-card {
  display: flex;
  flex-direction: column;
  width: 50%;
  padding: 8px 12px;
  background: rgb(255 255 255 / 80%);
  border: 1px solid #e2e8f0;
  border-radius: 8px;
}

.dark .clpm-pid-dashboard__top5-card {
  background: rgb(15 23 42 / 80%);
  border: 1px solid #334155;
}

.dark .clpm-pid-dashboard__top5-card :deep(.ant-table-content) {
  color: #f1f5f9;
}

.dark .clpm-pid-dashboard__top5-card :deep(.ant-table-thead > tr > th) {
  color: #94a3b8;
}

.dark .clpm-pid-dashboard__top5-card :deep(.ant-table-tbody > tr > td) {
  color: #f1f5f9;
}

.clpm-pid-dashboard__top5-card :deep(.ant-table-tbody > tr > td) {
  white-space: nowrap;
}

.clpm-pid-dashboard__rating-tag {
  padding: 2px 8px;
  font-size: 12px;
  border-radius: 4px;

  &--1 {
    color: #22c55e;
    background: rgb(34 197 94 / 10%);
  }

  &--2 {
    color: #3b82f6;
    background: rgb(59 130 246 / 10%);
  }

  &--3 {
    color: #f59e0b;
    background: rgb(245 158 11 / 10%);
  }

  &--4 {
    color: #f97316;
    background: rgb(249 115 22 / 10%);
  }

  &--5 {
    color: #ef4444;
    background: rgb(239 68 68 / 10%);
  }
}

.dark .clpm-pid-dashboard__rating-tag {
  &--1 {
    background: rgb(34 197 94 / 20%);
  }

  &--2 {
    background: rgb(59 130 246 / 20%);
  }

  &--3 {
    background: rgb(245 158 11 / 20%);
  }

  &--4 {
    background: rgb(249 115 22 / 20%);
  }

  &--5 {
    background: rgb(239 68 68 / 20%);
  }
}

:deep(.ant-table) {
  background: transparent;

  .ant-table-header {
    background: rgb(241 245 249 / 50%);
  }

  .ant-table-body {
    background: transparent;
  }

  .ant-table-cell {
    padding: 6px 8px;
    font-size: 12px;
    line-height: 1.4;
    color: #475569;
    border-bottom: 1px solid #e2e8f0;
  }

  .ant-table-thead > tr > th {
    padding: 8px;
    font-size: 12px;
    font-weight: 500;
    color: #64748b;
    background: rgb(241 245 249 / 50%);
    border-bottom: 1px solid #e2e8f0;
  }

  .ant-table-tbody > tr:hover > td {
    background: rgb(59 130 246 / 5%);
  }

  .ant-table-tbody > tr {
    height: 32px;
  }
}

.dark :deep(.ant-table) {
  background: transparent;

  .ant-table-header {
    background: rgb(30 41 59 / 50%);
  }

  .ant-table-body {
    background: transparent;
  }

  .ant-table-cell {
    padding: 6px 8px;
    font-size: 12px;
    line-height: 1.4;
    color: #cbd5e1;
    border-bottom: 1px solid #334155;
  }

  .ant-table-thead > tr > th {
    padding: 8px;
    font-size: 12px;
    font-weight: 500;
    color: #94a3b8;
    background: rgb(30 41 59 / 50%);
    border-bottom: 1px solid #334155;
  }

  .ant-table-tbody > tr:hover > td {
    background: rgb(59 130 246 / 10%);
  }

  .ant-table-tbody > tr {
    height: 32px;
  }
}

:deep(.ant-select-selector) {
  color: #334155 !important;
  background: rgb(241 245 249 / 50%) !important;
  border: 1px solid #e2e8f0 !important;
}

.dark :deep(.ant-select-selector) {
  color: #e2e8f0 !important;
  background: rgb(30 41 59 / 50%) !important;
  border: 1px solid #334155 !important;
}

:deep(.ant-btn) {
  color: #3b82f6;
  background: rgb(59 130 246 / 10%);
  border: 1px solid #3b82f6;
}

.dark :deep(.ant-btn) {
  background: rgb(59 130 246 / 20%);
}
</style>
