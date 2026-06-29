<script lang="ts" setup>
/**
 * S6-PORTAL-002 工作台性能总览首页
 * UI/UX v4.2 §6.1.1 规范实现
 *
 * 布局（上中下三行）：
 * - 左侧：PlantNodeTree 工厂导航树
 * - 右侧上行（20%）：6 项 KPI 卡片
 * - 右侧中行（50%）：左低效回路列表 | 右选中回路摘要
 * - 右侧下行（30%）：自控投用率 + 平稳率双轴折线趋势
 */
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { DashboardApi } from '#/api/dashboard';
import type { MetricApi } from '#/api/metric';
import type { PlantNodeApi } from '#/api/plant-node';

import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue';
import { useRouter } from 'vue-router';

import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import { RadioGroup, Switch, Table, Tag, Tooltip } from 'ant-design-vue';
import dayjs from 'dayjs';

import {
  DIAGNOSIS_LABEL_COLOR_MAP,
  DIAGNOSIS_LABEL_NAME_MAP,
} from '#/constants/diagnosis';
import { ClpmDataCanvas, ClpmKpiStrip, ClpmObjectSummaryBar, ClpmPageToolbar, ClpmToolbarButton } from '#/components/clpm';
import type { KpiStripItem } from '#/components/clpm';
import PlantNodeTree from '#/components/plant-node/plant-node-tree.vue';
import { useClpmTheme } from '#/composables/use-clpm-theme';

const {
  isDark,
  themeColors,
  chartTextColor,
  chartSplitLineColor,
  chartTrackColor,
  chartBorderColor,
  chartTextStrongColor,
} = useClpmTheme();

// ============ API 接口 ============
import { getDashboardOverviewApi } from '#/api/dashboard';
import { getAnalyticsApi, getRealtimeAutoRateApi } from '#/api/metric';

const router = useRouter();

// ============ 工厂树导航 ============
const selectedPlantNodeId = ref<string | undefined>(undefined);
const selectedPlantNodeName = ref<string>('全厂');
const apiTreeData = ref<PlantNodeApi.PlantNode[]>([]);

function onTreeSelect(node: PlantNodeApi.PlantNode | null) {
  if (node) {
    selectedPlantNodeId.value = node.id;
    selectedPlantNodeName.value = node.name;
  } else {
    selectedPlantNodeId.value = undefined;
    selectedPlantNodeName.value = '全厂';
  }
  loadAll();
}

function onTreeLoadComplete(treeData: PlantNodeApi.PlantNode[]) {
  apiTreeData.value = treeData;
}

// ============ 筛选区 ============
type GranularityType = 'day' | 'week' | 'month';
type TrendGranularityType = 'day' | 'hour';

const granularity = ref<GranularityType>('day');
const trendGranularity = ref<TrendGranularityType>('day');

const granularityOptions = [
  { label: '日', value: 'day' as const },
  { label: '周', value: 'week' as const },
  { label: '月', value: 'month' as const },
];

const trendGranularityOptions = [
  { label: '日', value: 'day' as const },
  { label: '小时', value: 'hour' as const },
];

// ============ 数据加载 ============
const loading = ref(false);
const overviewData = ref<DashboardApi.OverviewResult | null>(null);
const realtimeAutoRate = ref<MetricApi.RealtimeAutoRateResult | null>(null);
const analyticsData = ref<MetricApi.AnalyticsResult | null>(null);

// 选中的低效回路
const selectedLoop = ref<DashboardApi.InefficientLoop | null>(null);

// ============ 自动刷新 + 状态反馈 ============
const autoRefresh = ref(false);
const autoRefreshInterval = ref(60); // 秒
const lastRefreshAt = ref<Date | null>(null);
let autoRefreshTimer: null | ReturnType<typeof setInterval> = null;

const lastRefreshText = computed(() => {
  if (!lastRefreshAt.value) return '尚未刷新';
  const diff = dayjs().diff(lastRefreshAt.value, 'second');
  if (diff < 60) return `${diff} 秒前`;
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  return dayjs(lastRefreshAt.value).format('HH:mm:ss');
});

function startAutoRefresh() {
  stopAutoRefresh();
  if (autoRefresh.value) {
    autoRefreshTimer = setInterval(() => {
      loadAll();
    }, autoRefreshInterval.value * 1000);
  }
}

function stopAutoRefresh() {
  if (autoRefreshTimer) {
    clearInterval(autoRefreshTimer);
    autoRefreshTimer = null;
  }
}

function toggleAutoRefresh(val: boolean | number | string) {
  const enabled = typeof val === 'boolean' ? val : val === 'true';
  autoRefresh.value = enabled;
  if (enabled) {
    startAutoRefresh();
  } else {
    stopAutoRefresh();
  }
}

// ============ 数据质量摘要（用于环形图 + StatusFooter） ============
const dataQualitySummary = computed(() => {
  const rate = overviewData.value?.kpi_cards?.good_value_rate?.value ?? 0;
  // 简化：好值率作为 Good，剩余均分为 Bad 和 Uncertain（实际需要后端返回明细）
  const good = rate;
  const bad = (100 - rate) / 2;
  const uncertain = 100 - rate - bad;
  return { good, bad, uncertain, validRate: rate };
});

// ============ 综合健康分（用于仪表盘） ============
const compositeScore = computed(
  () => overviewData.value?.kpi_cards?.composite_score?.value ?? 0,
);

// ============ 导出日报 ============
const exporting = ref(false);
async function handleExportDailyReport() {
  exporting.value = true;
  try {
    // 模拟导出（实际可调用后端 /dashboard/daily-report 接口）
    const ts = dayjs().format('YYYY-MM-DD_HHmm');
    const filename = `CLPM日报_${selectedPlantNodeName.value}_${ts}.xlsx`;
    // 简单生成 CSV 占位
    const csv = [
      ['指标', '数值', '单位'],
      ...kpiCards.value.map((c) => [c.label, c.value, c.unit]),
    ]
      .map((row) => row.join(','))
      .join('\n');
    const blob = new Blob([`\ufeff${csv}`], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename.replace('.xlsx', '.csv');
    document.body.append(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  } finally {
    exporting.value = false;
  }
}

async function loadOverview() {
  const res = await getDashboardOverviewApi({
    plantId: selectedPlantNodeId.value,
    granularity: granularity.value,
  });
  overviewData.value = res;
}

async function loadRealtimeAutoRate() {
  try {
    const res = await getRealtimeAutoRateApi({
      plantNodeId: selectedPlantNodeId.value,
    });
    realtimeAutoRate.value = res;
  } catch {
    // 实时自控率接口可能无数据，不影响整体页面
    realtimeAutoRate.value = null;
  }
}

async function loadAnalytics() {
  const days =
    granularity.value === 'day' ? 7 : granularity.value === 'week' ? 30 : 90;
  const startTime = dayjs().subtract(days, 'day').format('YYYY-MM-DD HH:mm:ss');
  const endTime = dayjs().format('YYYY-MM-DD HH:mm:ss');

  // 分别请求自控投用率和平稳率的趋势数据
  const [autoRes, steadyRes] = await Promise.all([
    getAnalyticsApi({
      plantNodeId: selectedPlantNodeId.value,
      startTime,
      endTime,
      granularity: trendGranularity.value,
      metricKey: 'auto_mode_rate',
    }),
    getAnalyticsApi({
      plantNodeId: selectedPlantNodeId.value,
      startTime,
      endTime,
      granularity: trendGranularity.value,
      metricKey: 'steady_rate',
    }),
  ]);

  // 合并两个指标的趋势数据
  const timestamps =
    autoRes.kpiTrend?.timestamps ?? steadyRes.kpiTrend?.timestamps ?? [];
  const series: MetricApi.AnalyticsSeries[] = [
    ...(autoRes.kpiTrend?.series ?? []),
    ...(steadyRes.kpiTrend?.series ?? []),
  ];

  analyticsData.value = {
    filterScope: autoRes.filterScope,
    kpiTrend: { timestamps, series },
    unitRanking: autoRes.unitRanking ?? [],
    badActorDistribution: autoRes.badActorDistribution ?? [],
  };
}

async function loadAll() {
  loading.value = true;
  selectedLoop.value = null;
  try {
    await Promise.all([
      loadOverview(),
      loadRealtimeAutoRate(),
      loadAnalytics(),
    ]);
  } finally {
    loading.value = false;
    lastRefreshAt.value = new Date();
    await nextTick();
    renderTrendChart();
    renderHealthGauge();
    renderQualityDonut();
  }
}

// ============ 上行：6 KPI 卡片 ============
interface KpiCardItem {
  key: keyof DashboardApi.KpiCards;
  label: string;
  value: number;
  unit: string;
  trend: DashboardApi.Trend;
  delta: number;
  goodWhenUp: boolean;
}

const kpiCards = computed<KpiCardItem[]>(() => {
  const kpi = overviewData.value?.kpi_cards;
  if (!kpi) {
    return [
      {
        key: 'auto_mode_rate',
        label: '自控投用率',
        value: 0,
        unit: '%',
        trend: 'stable',
        delta: 0,
        goodWhenUp: true,
      },
      {
        key: 'steady_rate',
        label: '平稳率',
        value: 0,
        unit: '%',
        trend: 'stable',
        delta: 0,
        goodWhenUp: true,
      },
      {
        key: 'composite_score',
        label: '综合评分',
        value: 0,
        unit: '分',
        trend: 'stable',
        delta: 0,
        goodWhenUp: true,
      },
      {
        key: 'alarm_count',
        label: '报警次数',
        value: 0,
        unit: '次',
        trend: 'stable',
        delta: 0,
        goodWhenUp: false,
      },
      {
        key: 'operation_count',
        label: '操作频次',
        value: 0,
        unit: '次',
        trend: 'stable',
        delta: 0,
        goodWhenUp: false,
      },
      {
        key: 'good_value_rate',
        label: '好值率',
        value: 0,
        unit: '%',
        trend: 'stable',
        delta: 0,
        goodWhenUp: true,
      },
    ];
  }
  return [
    {
      key: 'auto_mode_rate',
      label: '自控投用率',
      value: kpi.auto_mode_rate?.value ?? 0,
      unit: kpi.auto_mode_rate?.unit ?? '%',
      trend: kpi.auto_mode_rate?.trend ?? 'stable',
      delta: kpi.auto_mode_rate?.delta ?? 0,
      goodWhenUp: true,
    },
    {
      key: 'steady_rate',
      label: '平稳率',
      value: kpi.steady_rate?.value ?? 0,
      unit: kpi.steady_rate?.unit ?? '%',
      trend: kpi.steady_rate?.trend ?? 'stable',
      delta: kpi.steady_rate?.delta ?? 0,
      goodWhenUp: true,
    },
    {
      key: 'composite_score',
      label: '综合评分',
      value: kpi.composite_score?.value ?? 0,
      unit: kpi.composite_score?.unit ?? '分',
      trend: kpi.composite_score?.trend ?? 'stable',
      delta: kpi.composite_score?.delta ?? 0,
      goodWhenUp: true,
    },
    {
      key: 'alarm_count',
      label: '报警次数',
      value: kpi.alarm_count?.value ?? 0,
      unit: kpi.alarm_count?.unit ?? '次',
      trend: kpi.alarm_count?.trend ?? 'stable',
      delta: kpi.alarm_count?.delta ?? 0,
      goodWhenUp: false,
    },
    {
      key: 'operation_count',
      label: '操作频次',
      value: kpi.operation_count?.value ?? 0,
      unit: kpi.operation_count?.unit ?? '次',
      trend: kpi.operation_count?.trend ?? 'stable',
      delta: kpi.operation_count?.delta ?? 0,
      goodWhenUp: false,
    },
    {
      key: 'good_value_rate',
      label: '好值率',
      value: kpi.good_value_rate?.value ?? 0,
      unit: kpi.good_value_rate?.unit ?? '%',
      trend: kpi.good_value_rate?.trend ?? 'stable',
      delta: kpi.good_value_rate?.delta ?? 0,
      goodWhenUp: true,
    },
  ];
});

const kpiStripItems = computed<KpiStripItem[]>(() =>
  kpiCards.value.map((card) => ({
    delta:
      card.delta === 0
        ? ''
        : `${trendArrow(card.trend)} ${formatDelta(card.delta, card.unit)}`,
    key: String(card.key),
    label: card.label,
    status: kpiStatus(card.value, card.unit === '%'),
    unit: card.unit,
    value: card.unit === '%' ? card.value.toFixed(1) : card.value,
  })),
);

// ============ 中行左：低效回路列表 ============
const inefficientLoopColumns = [
  {
    dataIndex: 'loop_tag',
    key: 'loop_tag',
    title: '位号',
    width: 140,
    ellipsis: true,
  },
  {
    dataIndex: 'composite_score',
    key: 'composite_score',
    title: '评分',
    width: 70,
    align: 'right' as const,
  },
  {
    dataIndex: 'plant_name',
    key: 'plant_name',
    title: '所属装置',
    width: 100,
    ellipsis: true,
  },
  {
    dataIndex: 'key_metric',
    key: 'key_metric',
    title: '自控率/平稳率',
    width: 120,
    align: 'right' as const,
  },
];

const inefficientLoops = computed(() => {
  return overviewData.value?.inefficient_loops ?? [];
});

// ============ 中行右：选中回路摘要 ============
const loopSummary = computed(() => selectedLoop.value);

function handleLoopRowClick(record: DashboardApi.InefficientLoop) {
  selectedLoop.value = record;
}

/** 选中回路摘要条动作分发 */
function onSummaryAction(key: string) {
  const loopId = selectedLoop.value?.loop_id;
  if (!loopId) return;
  if (key === 'diagnosis') {
    router.push(`/diagnosis/detail/${loopId}`);
  } else if (key === 'detail') {
    router.push(`/loop/detail/${loopId}`);
  } else if (key === 'tuning') {
    router.push(`/tuning/workbench?loopId=${loopId}`);
  }
}

// ============ 下行：趋势图 ============
const trendChartRef = ref<EchartsUIType>();
const { renderEcharts: renderTrend } = useEcharts(trendChartRef);

// ============ 综合健康仪表盘 ============
const healthGaugeRef = ref<EchartsUIType>();
const { renderEcharts: renderHealthGaugeEcharts } = useEcharts(healthGaugeRef);

// ============ 数据质量环形图 ============
const qualityDonutRef = ref<EchartsUIType>();
const { renderEcharts: renderQualityDonutEcharts } =
  useEcharts(qualityDonutRef);

function renderTrendChart() {
  const analytics = analyticsData.value;
  if (!analytics?.kpiTrend?.timestamps?.length) {
    renderTrend({
      title: {
        left: 'center',
        text: '暂无趋势数据',
        textStyle: {
          color: chartTextColor.value,
          fontSize: 12,
          fontWeight: 'normal',
        },
        top: 'center',
      },
      xAxis: { type: 'category', data: [] },
      yAxis: { type: 'value' },
      series: [],
    });
    return;
  }

  const { timestamps, series } = analytics.kpiTrend;
  const labels = timestamps.map((t) => {
    const d = dayjs(t);
    return trendGranularity.value === 'hour'
      ? d.format('HH:00')
      : d.format('MM-DD');
  });

  const autoSeries = series.find((s) => s.metricKey === 'auto_mode_rate');
  const steadySeries = series.find((s) => s.metricKey === 'steady_rate');

  renderTrend({
    color: [themeColors.value.INFO, themeColors.value.SUCCESS],
    grid: { bottom: 40, containLabel: true, left: 48, right: 48, top: 50 },
    legend: { data: ['自控投用率', '平稳率'], top: 8 },
    series: [
      {
        data: autoSeries?.values ?? [],
        itemStyle: { color: themeColors.value.INFO },
        lineStyle: { width: 2.5 },
        name: '自控投用率',
        smooth: true,
        symbol: 'circle',
        symbolSize: 5,
        type: 'line',
      },
      {
        data: steadySeries?.values ?? [],
        itemStyle: { color: themeColors.value.SUCCESS },
        lineStyle: { width: 2.5 },
        name: '平稳率',
        smooth: true,
        symbol: 'circle',
        symbolSize: 5,
        type: 'line',
      },
    ],
    tooltip: {
      axisPointer: { type: 'cross' },
      trigger: 'axis',
      valueFormatter: (val: unknown) => {
        if (val === null || val === undefined) return '—';
        return `${Number(val).toFixed(1)  }%`;
      },
    },
    xAxis: {
      axisLabel: { color: chartTextColor.value, fontSize: 11 },
      boundaryGap: false,
      data: labels,
      type: 'category',
    },
    yAxis: [
      {
        axisLabel: { color: chartTextColor.value, fontSize: 11 },
        max: 100,
        min: 0,
        name: '百分比 (%)',
        nameTextStyle: { color: chartTextColor.value, fontSize: 11 },
        splitLine: {
          lineStyle: { color: chartSplitLineColor.value, type: 'dashed' },
        },
        type: 'value',
      },
    ],
  });
}

// ============ 综合健康仪表盘（半圆 Gauge） ============
function renderHealthGauge() {
  const score = compositeScore.value;
  const color =
    score >= 80
      ? themeColors.value.SUCCESS
      : score >= 60
        ? themeColors.value.WARNING
        : themeColors.value.DANGER;
  renderHealthGaugeEcharts({
    series: [
      {
        axisLabel: { show: false },
        axisLine: {
          lineStyle: {
            color: [[1, chartTrackColor.value]],
            width: 12,
          },
        },
        axisTick: { show: false },
        data: [{ value: score }],
        detail: {
          color,
          fontFamily: 'ui-monospace, Menlo, Consolas',
          fontSize: 28,
          fontWeight: 'bolder',
          formatter: '{value}',
          offsetCenter: [0, '30%'],
        },
        max: 100,
        min: 0,
        pointer: {
          itemStyle: { color },
          length: '60%',
          width: 4,
        },
        progress: {
          itemStyle: { color },
          roundCap: true,
          show: true,
          width: 12,
        },
        radius: '95%',
        startAngle: 180,
        endAngle: 0,
        splitLine: { show: false },
        title: {
          color: chartTextColor.value,
          fontSize: 12,
          offsetCenter: [0, '70%'],
        },
        type: 'gauge',
      },
    ],
    title: { show: false },
  });
}

// ============ 数据质量环形图 ============
function renderQualityDonut() {
  const { good, bad, uncertain } = dataQualitySummary.value;
  renderQualityDonutEcharts({
    legend: {
      bottom: 0,
      data: ['Good', 'Bad', 'Uncertain'],
      icon: 'circle',
      itemHeight: 8,
      itemWidth: 8,
      textStyle: { color: chartTextColor.value, fontSize: 11 },
    },
    series: [
      {
        avoidLabelOverlap: true,
        center: ['50%', '45%'],
        data: [
          {
            itemStyle: { color: themeColors.value.SUCCESS },
            name: 'Good',
            value: good,
          },
          {
            itemStyle: { color: themeColors.value.DANGER },
            name: 'Bad',
            value: bad,
          },
          {
            itemStyle: { color: themeColors.value.WARNING },
            name: 'Uncertain',
            value: uncertain,
          },
        ],
        emphasis: {
          label: { fontSize: 14, fontWeight: 'bold', show: true },
        },
        itemStyle: {
          borderColor: chartBorderColor.value,
          borderRadius: 4,
          borderWidth: 2,
        },
        label: {
          color: chartTextStrongColor.value,
          fontFamily: 'ui-monospace, Menlo, Consolas',
          fontSize: 16,
          fontWeight: 'bold',
          formatter: '{d}%',
          show: true,
        },
        radius: ['52%', '78%'],
        type: 'pie',
      },
    ],
    tooltip: {
      formatter: '{b}: {c} ({d}%)',
      trigger: 'item',
    },
  });
}

// ============ 辅助函数 ============
function kpiStatus(
  score: number,
  isRate: boolean = true,
): KpiStripItem['status'] {
  const threshold = isRate ? 80 : 60;
  const good = isRate ? 90 : 80;
  if (score >= good) return 'success';
  if (score >= threshold) return 'warning';
  return 'danger';
}

function trendArrow(trend: DashboardApi.Trend): string {
  if (trend === 'up') return '↑';
  if (trend === 'down') return '↓';
  return '→';
}

function formatDelta(delta: number, unit: string): string {
  if (delta === 0) return '—';
  const sign = delta > 0 ? '+' : '';
  return `${sign}${delta.toFixed(1)}${unit}`;
}

function scoreLevelColor(score: number): string {
  if (score >= 80) return themeColors.value.SUCCESS;
  if (score >= 60) return themeColors.value.WARNING;
  return themeColors.value.DANGER;
}

// ============ 主题切换重渲图表 ============
watch(isDark, () => {
  nextTick(() => {
    renderTrendChart();
    renderHealthGauge();
    renderQualityDonut();
  });
});

// ============ 生命周期 ============
onMounted(() => {
  loadAll();
});

onUnmounted(() => {
  stopAutoRefresh();
});
</script>

<template>
  <div class="flex gap-3 h-[calc(100vh-130px)]">
    <!-- 左侧工厂导航树 -->
    <PlantNodeTree
      card-title="工厂导航"
      :width="260"
      :show-collapse-buttons="true"
      :default-expand-level="3"
      max-height="calc(100vh - 160px)"
      @select="onTreeSelect"
      @load-complete="onTreeLoadComplete"
    />

    <!-- 右侧主显示区 -->
    <div class="min-w-0 flex-1 flex flex-col gap-3">
      <ClpmPageToolbar
        title="控制回路治理工作台"
        :subtitle="`${selectedPlantNodeName} · ${granularityOptions.find((o) => o.value === granularity)?.label ?? '日'}视图`"
        :loading="loading"
        :status="loading ? 'loading' : 'success'"
        :last-refresh="lastRefreshText"
      >
        <Tag color="processing">{{ selectedPlantNodeName }}</Tag>
        <RadioGroup
          v-model:value="granularity"
          :options="granularityOptions"
          option-type="button"
          button-style="solid"
          size="small"
          @change="loadAll"
        />
        <template #actions>
          <Tooltip
            :title="
              autoRefresh
                ? `自动刷新开启（${autoRefreshInterval}s）`
                : '开启自动刷新'
            "
          >
            <Switch
              :checked="autoRefresh"
              size="small"
              @change="toggleAutoRefresh"
            />
          </Tooltip>
          <ClpmToolbarButton
            icon="auto-refresh"
            :active="autoRefresh"
            icon-only
            size="small"
            :tooltip="
              autoRefresh
                ? `自动刷新中（${autoRefreshInterval}s）`
                : '开启自动刷新'
            "
            @click="toggleAutoRefresh(!autoRefresh)"
          />
          <ClpmToolbarButton
            icon="refresh"
            :loading="loading"
            icon-only
            size="small"
            tooltip="刷新数据"
            @click="loadAll"
          />
          <ClpmToolbarButton
            icon="export"
            :loading="exporting"
            size="small"
            @click="handleExportDailyReport"
          >
            导出日报
          </ClpmToolbarButton>
        </template>
      </ClpmPageToolbar>

      <!-- ====== 上行：综合健康仪表盘 + 数据质量环形图 + KPI Strip ====== -->
      <div class="flex gap-3 flex-shrink-0">
        <ClpmDataCanvas
          class="w-[240px]"
          title="综合健康"
          description="全厂综合评分"
        >
          <EchartsUI ref="healthGaugeRef" height="160px" />
        </ClpmDataCanvas>
        <ClpmDataCanvas
          class="w-[260px]"
          title="数据质量"
          description="Good / Bad / Uncertain"
        >
          <EchartsUI ref="qualityDonutRef" height="160px" />
        </ClpmDataCanvas>
        <div class="flex-1 min-w-0">
          <ClpmKpiStrip :items="kpiStripItems" :loading="loading" />
        </div>
      </div>

      <!-- ====== 中行：低效回路列表 | 选中回路摘要 ====== -->
      <div class="flex gap-3 flex-1 min-h-0">
        <!-- 中行左：低效回路列表（60%） -->
        <ClpmDataCanvas
          class="flex-1 min-w-0"
          title="低效回路列表"
          :loading="loading"
        >
          <Table
            :columns="inefficientLoopColumns"
            :data-source="inefficientLoops"
            :pagination="false"
            :scroll="{ y: 'calc(100% - 40px)' }"
            size="small"
            :row-class-name="
              (record) =>
                selectedLoop?.loop_id === record.loop_id
                  ? 'ant-table-row-selected cursor-pointer'
                  : 'cursor-pointer'
            "
            :custom-row="
              (record) => ({
                onClick: () =>
                  handleLoopRowClick(record as DashboardApi.InefficientLoop),
              })
            "
          >
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'loop_tag'">
                <span class="font-mono text-sm">{{ record.loop_tag }}</span>
              </template>
              <template v-else-if="column.key === 'composite_score'">
                <span
                  class="font-mono font-bold"
                  :style="{ color: scoreLevelColor(record.composite_score) }"
                >
                  {{ record.composite_score.toFixed(0) }}
                </span>
              </template>
              <template v-else-if="column.key === 'plant_name'">
                <span class="text-xs text-gray-500">{{
                  record.plant_name
                }}</span>
              </template>
              <template v-else-if="column.key === 'key_metric'">
                <div class="flex flex-col items-end text-xs">
                  <span class="font-mono">
                    自控
                    {{ record.key_metric?.auto_mode_rate?.toFixed(1) ?? '—' }}%
                  </span>
                  <span class="font-mono text-gray-500">
                    平稳
                    {{ record.key_metric?.steady_rate?.toFixed(1) ?? '—' }}%
                  </span>
                </div>
              </template>
            </template>
          </Table>
        </ClpmDataCanvas>

        <!-- 中行右：选中回路摘要（40%） -->
        <ClpmDataCanvas
          class="w-[40%] min-w-0"
          title="回路摘要"
          :loading="loading && !selectedLoop"
          :empty="!loopSummary"
          empty-text="点击左侧回路查看摘要"
        >
          <template v-if="loopSummary">
            <ClpmObjectSummaryBar
              :title="loopSummary.loop_tag"
              :subtitle="`${loopSummary.loop_name} · ${loopSummary.plant_name}`"
              :primary-item="{
                key: 'score',
                label: '综合评分',
                value: loopSummary.composite_score.toFixed(0),
                status: kpiStatus(loopSummary.composite_score, false),
              }"
              :items="[
                {
                  key: 'auto_rate',
                  label: '自控率',
                  value:
                    (loopSummary.key_metric?.auto_mode_rate?.toFixed(1) ??
                      '—') + '%',
                  status: kpiStatus(
                    loopSummary.key_metric?.auto_mode_rate ?? 0,
                  ),
                },
                {
                  key: 'steady_rate',
                  label: '平稳率',
                  value:
                    (loopSummary.key_metric?.steady_rate?.toFixed(1) ?? '—') +
                    '%',
                  status: kpiStatus(loopSummary.key_metric?.steady_rate ?? 0),
                },
              ]"
              :actions="[
                {
                  key: 'diagnosis',
                  label: '进入诊断',
                  type: 'primary',
                  icon: 'ant-design:experiment-outlined',
                },
                {
                  key: 'detail',
                  label: '回路详情',
                  icon: 'ant-design:profile-outlined',
                },
                {
                  key: 'tuning',
                  label: '整定建议',
                  icon: 'ant-design:sliders-outlined',
                },
              ]"
              @action="onSummaryAction"
            />

            <!-- 预诊标签 -->
            <div v-if="loopSummary.diagnosis_labels?.length" class="mt-3">
              <div class="mb-1 text-xs text-gray-400">预诊标签</div>
              <div class="flex flex-wrap gap-1">
                <Tag
                  v-for="label in loopSummary.diagnosis_labels"
                  :key="label"
                  :color="DIAGNOSIS_LABEL_COLOR_MAP[label] || 'warning'"
                  size="small"
                >
                  {{ DIAGNOSIS_LABEL_NAME_MAP[label] || label }}
                </Tag>
              </div>
            </div>
          </template>
        </ClpmDataCanvas>
      </div>

      <!-- ====== 下行：关键指标组合趋势 ====== -->
      <ClpmDataCanvas
        class="flex-shrink-0"
        title="关键指标组合趋势"
        description="自控投用率与平稳率的组合趋势，用于观察近期运行质量变化。"
      >
        <template #extra>
          <span class="text-xs text-gray-400">粒度：</span>
          <RadioGroup
            v-model:value="trendGranularity"
            :options="trendGranularityOptions"
            option-type="button"
            button-style="outline"
            size="small"
            @change="loadAnalytics"
          />
        </template>
        <EchartsUI ref="trendChartRef" height="200px" />
      </ClpmDataCanvas>

      <!-- ====== StatusFooter：数据延迟/最近刷新/数据质量 ====== -->
      <div class="clpm-status-footer">
        <div class="clpm-status-footer__item">
          <span class="clpm-status-footer__label">最近刷新</span>
          <span class="clpm-status-footer__value">{{ lastRefreshText }}</span>
        </div>
        <div class="clpm-status-footer__divider"></div>
        <div class="clpm-status-footer__item">
          <span class="clpm-status-footer__label">数据质量</span>
          <span
            class="clpm-status-footer__value"
            :class="`is-${kpiStatus(dataQualitySummary.validRate)}`"
          >
            Good {{ dataQualitySummary.good.toFixed(1) }}%
          </span>
        </div>
        <div class="clpm-status-footer__divider"></div>
        <div class="clpm-status-footer__item">
          <span class="clpm-status-footer__label">综合评分</span>
          <span
            class="clpm-status-footer__value"
            :class="`is-${kpiStatus(compositeScore, false)}`"
          >
            {{ compositeScore.toFixed(1) }}
          </span>
        </div>
        <div class="clpm-status-footer__divider"></div>
        <div class="clpm-status-footer__item">
          <span class="clpm-status-footer__label">自动刷新</span>
          <span
            class="clpm-status-footer__value"
            :class="autoRefresh ? 'is-success' : 'is-neutral'"
          >
            {{ autoRefresh ? `开启（${autoRefreshInterval}s）` : '关闭' }}
          </span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.clpm-status-footer {
  display: flex;
  flex-shrink: 0;
  gap: 14px;
  align-items: center;
  padding: 8px 14px;
  font-size: 12px;
  color: hsl(var(--muted-foreground));
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border));
  border-radius: calc(var(--radius) * 1px);
}

.clpm-status-footer__item {
  display: flex;
  gap: 6px;
  align-items: center;
}

.clpm-status-footer__label {
  color: hsl(var(--muted-foreground));
}

.clpm-status-footer__value {
  font-family: var(
    --font-mono,
    ui-monospace,
    SFMono-Regular,
    Menlo,
    Monaco,
    Consolas,
    monospace
  );
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: hsl(var(--foreground));
}

.clpm-status-footer__value.is-success {
  color: hsl(var(--success));
}

.clpm-status-footer__value.is-warning {
  color: hsl(var(--warning));
}

.clpm-status-footer__value.is-danger {
  color: hsl(var(--destructive));
}

.clpm-status-footer__value.is-neutral {
  color: hsl(var(--muted-foreground));
}

.clpm-status-footer__divider {
  width: 1px;
  height: 12px;
  background: hsl(var(--border));
}
</style>
