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

import {
  computed,
  nextTick,
  onMounted,
  onUnmounted,
  ref,
} from 'vue';

import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import {
  Button,
  Card,
  RadioGroup,
  Table,
  Tag,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import { KPI_COLOR_MAP, THEME_COLORS } from '#/preferences';
import PlantNodeTree from '#/components/plant-node/plant-node-tree.vue';

// ============ API 接口 ============
import { getDashboardOverviewApi } from '#/api/dashboard';
import {
  getAnalyticsApi,
  getRealtimeAutoRateApi,
} from '#/api/metric';

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
  const days = granularity.value === 'day' ? 7 : granularity.value === 'week' ? 30 : 90;
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
  const timestamps = autoRes.kpiTrend?.timestamps ?? steadyRes.kpiTrend?.timestamps ?? [];
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
    await nextTick();
    renderTrendChart();
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
      { key: 'auto_mode_rate', label: '自控投用率', value: 0, unit: '%', trend: 'stable', delta: 0, goodWhenUp: true },
      { key: 'steady_rate', label: '平稳率', value: 0, unit: '%', trend: 'stable', delta: 0, goodWhenUp: true },
      { key: 'composite_score', label: '综合评分', value: 0, unit: '分', trend: 'stable', delta: 0, goodWhenUp: true },
      { key: 'alarm_count', label: '报警次数', value: 0, unit: '次', trend: 'stable', delta: 0, goodWhenUp: false },
      { key: 'operation_count', label: '操作频次', value: 0, unit: '次', trend: 'stable', delta: 0, goodWhenUp: false },
      { key: 'good_value_rate', label: '好值率', value: 0, unit: '%', trend: 'stable', delta: 0, goodWhenUp: true },
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

// ============ 中行左：低效回路列表 ============
const inefficientLoopColumns = [
  { dataIndex: 'loop_tag', key: 'loop_tag', title: '位号', width: 140, ellipsis: true },
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

// ============ 下行：趋势图 ============
const trendChartRef = ref<EchartsUIType>();
const { renderEcharts: renderTrend } = useEcharts(trendChartRef);

function renderTrendChart() {
  const analytics = analyticsData.value;
  if (!analytics?.kpiTrend?.timestamps?.length) {
    renderTrend({
      title: {
        left: 'center',
        text: '暂无趋势数据',
        textStyle: { color: '#8c8c8c', fontSize: 12, fontWeight: 'normal' },
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
    return trendGranularity.value === 'hour' ? d.format('HH:00') : d.format('MM-DD');
  });

  const autoSeries = series.find((s) => s.metricKey === 'auto_mode_rate');
  const steadySeries = series.find((s) => s.metricKey === 'steady_rate');

  renderTrend({
    color: [THEME_COLORS.INFO, THEME_COLORS.SUCCESS],
    grid: { bottom: 40, containLabel: true, left: 48, right: 48, top: 50 },
    legend: { data: ['自控投用率', '平稳率'], top: 8 },
    series: [
      {
        data: autoSeries?.values ?? [],
        itemStyle: { color: THEME_COLORS.INFO },
        lineStyle: { width: 2.5 },
        name: '自控投用率',
        smooth: true,
        symbol: 'circle',
        symbolSize: 5,
        type: 'line',
      },
      {
        data: steadySeries?.values ?? [],
        itemStyle: { color: THEME_COLORS.SUCCESS },
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
        return Number(val).toFixed(1) + '%';
      },
    },
    xAxis: {
      axisLabel: { color: '#8c8c8c', fontSize: 11 },
      boundaryGap: false,
      data: labels,
      type: 'category',
    },
    yAxis: [
      {
        axisLabel: { color: '#8c8c8c', fontSize: 11 },
        max: 100,
        min: 0,
        name: '百分比 (%)',
        nameTextStyle: { color: '#8c8c8c', fontSize: 11 },
        splitLine: { lineStyle: { color: '#E5E5E5', type: 'dashed' } },
        type: 'value',
      },
    ],
  });
}

// ============ 辅助函数 ============
function scoreColor(score: number, isRate: boolean = true): string {
  const threshold = isRate ? 80 : 60;
  const good = isRate ? 90 : 80;
  if (score >= good) return KPI_COLOR_MAP.EXCELLENT;
  if (score >= threshold) return KPI_COLOR_MAP.PASS;
  return KPI_COLOR_MAP.FAIL;
}

function trendArrow(trend: DashboardApi.Trend): string {
  if (trend === 'up') return '↑';
  if (trend === 'down') return '↓';
  return '→';
}

function trendColor(trend: DashboardApi.Trend, goodWhenUp: boolean): string {
  if (trend === 'stable') return '#6c757d';
  const isGood = goodWhenUp ? trend === 'up' : trend === 'down';
  return isGood ? KPI_COLOR_MAP.EXCELLENT : KPI_COLOR_MAP.FAIL;
}

function formatDelta(delta: number, unit: string): string {
  if (delta === 0) return '—';
  const sign = delta > 0 ? '+' : '';
  return `${sign}${delta.toFixed(1)}${unit}`;
}

function scoreLevelColor(score: number): string {
  if (score >= 80) return '#52c41a';
  if (score >= 60) return '#faad14';
  return '#ff4d4f';
}

// ============ 生命周期 ============
onMounted(() => {
  loadAll();
});

onUnmounted(() => {});
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
      <!-- ====== 筛选区 ====== -->
      <Card size="small" :body-style="{ padding: '10px 16px' }">
        <div class="flex flex-wrap items-center gap-3">
          <Tag color="processing">{{ selectedPlantNodeName }}</Tag>
          <RadioGroup
            v-model:value="granularity"
            :options="granularityOptions"
            option-type="button"
            button-style="solid"
            size="small"
            @change="loadAll"
          />
        </div>
      </Card>

      <!-- ====== 上行：6 KPI 卡片 ====== -->
      <div class="grid grid-cols-6 gap-3 flex-shrink-0">
        <Card
          v-for="card in kpiCards"
          :key="card.key"
          size="small"
          :loading="loading"
          :body-style="{ padding: '12px 14px' }"
        >
          <div class="flex flex-col">
            <span class="text-xs text-gray-400 mb-1">{{ card.label }}</span>
            <div class="flex items-baseline gap-1">
              <span
                class="text-xl font-bold font-mono"
                :style="{ color: scoreColor(card.value, card.unit === '%') }"
              >
                {{ card.unit === '%' ? card.value.toFixed(1) : card.value }}
              </span>
              <span class="text-xs text-gray-400">{{ card.unit }}</span>
            </div>
            <div
              v-if="card.delta !== 0"
              class="mt-1 flex items-center gap-1 text-xs"
              :style="{ color: trendColor(card.trend, card.goodWhenUp) }"
            >
              <span>{{ trendArrow(card.trend) }}</span>
              <span>{{ formatDelta(card.delta, card.unit) }}</span>
            </div>
          </div>
        </Card>
      </div>

      <!-- ====== 中行：低效回路列表 | 选中回路摘要 ====== -->
      <div class="flex gap-3 flex-1 min-h-0">
        <!-- 中行左：低效回路列表（60%） -->
        <Card
          class="flex-1 min-w-0"
          title="低效回路列表"
          size="small"
          :loading="loading"
          :body-style="{ padding: '8px', height: '100%' }"
        >
          <Table
            :columns="inefficientLoopColumns"
            :data-source="inefficientLoops"
            :pagination="false"
            :scroll="{ y: 'calc(100% - 40px)' }"
            size="small"
            :row-class-name="(record) => selectedLoop?.loop_id === record.loop_id ? 'ant-table-row-selected' : ''"
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
                <span class="text-xs text-gray-500">{{ record.plant_name }}</span>
              </template>
              <template v-else-if="column.key === 'key_metric'">
                <div class="flex flex-col items-end text-xs">
                  <span class="font-mono">
                    自控 {{ record.key_metric?.auto_mode_rate?.toFixed(1) ?? '—' }}%
                  </span>
                  <span class="font-mono text-gray-500">
                    平稳 {{ record.key_metric?.steady_rate?.toFixed(1) ?? '—' }}%
                  </span>
                </div>
              </template>
            </template>
          </Table>
        </Card>

        <!-- 中行右：选中回路摘要（40%） -->
        <Card
          class="w-[40%] min-w-0"
          title="回路摘要"
          size="small"
          :loading="loading && !selectedLoop"
          :body-style="{ padding: '12px', height: '100%' }"
        >
          <div v-if="loopSummary" class="flex flex-col h-full">
            <!-- 回路基本信息 -->
            <div class="mb-3">
              <div class="flex items-center gap-2 mb-2">
                <span class="font-mono text-base font-bold">{{ loopSummary.loop_tag }}</span>
                <Tag
                  :color="
                    loopSummary.composite_score >= 80
                      ? 'success'
                      : loopSummary.composite_score >= 60
                        ? 'warning'
                        : 'error'
                  "
                >
                  {{ loopSummary.composite_score.toFixed(0) }}分
                </Tag>
              </div>
              <div class="text-xs text-gray-400">
                {{ loopSummary.loop_name }}
              </div>
              <div class="text-xs text-gray-400">{{ loopSummary.plant_name }}</div>
            </div>

            <!-- 关键指标 -->
            <div class="mb-3 grid grid-cols-2 gap-2">
              <div class="bg-gray-50 rounded p-2 text-center">
                <div class="text-xs text-gray-400">自控率</div>
                <div
                  class="text-lg font-mono font-bold"
                  :style="{ color: scoreColor(loopSummary.key_metric?.auto_mode_rate ?? 0) }"
                >
                  {{ loopSummary.key_metric?.auto_mode_rate?.toFixed(1) ?? '—' }}%
                </div>
              </div>
              <div class="bg-gray-50 rounded p-2 text-center">
                <div class="text-xs text-gray-400">平稳率</div>
                <div
                  class="text-lg font-mono font-bold"
                  :style="{ color: scoreColor(loopSummary.key_metric?.steady_rate ?? 0) }"
                >
                  {{ loopSummary.key_metric?.steady_rate?.toFixed(1) ?? '—' }}%
                </div>
              </div>
            </div>

            <!-- 预诊标签 -->
            <div class="mb-3" v-if="loopSummary.diagnosis_labels?.length">
              <div class="text-xs text-gray-400 mb-1">预诊标签</div>
              <div class="flex flex-wrap gap-1">
                <Tag
                  v-for="label in loopSummary.diagnosis_labels"
                  :key="label"
                  color="warning"
                  size="small"
                >
                  {{ label }}
                </Tag>
              </div>
            </div>

            <!-- 小趋势占位 -->
            <div class="mb-3 flex-1 bg-gray-50 rounded p-2 flex items-center justify-center">
              <span class="text-sm text-gray-400">PV/SP/OP 趋势图（待接入）</span>
            </div>

            <!-- 快捷动作 -->
            <div class="flex gap-2">
              <Button size="small" type="primary">进入诊断</Button>
              <Button size="small">回路详情</Button>
            </div>
          </div>
          <div v-else class="h-full flex items-center justify-center">
            <span class="text-sm text-gray-400">请选择一个低效回路查看详情</span>
          </div>
        </Card>
      </div>

      <!-- ====== 下行：关键指标组合趋势 ====== -->
      <Card
        class="flex-shrink-0"
        title="关键指标组合趋势"
        size="small"
        :loading="loading"
        :body-style="{ padding: '8px' }"
      >
        <div class="mb-2 flex items-center gap-2">
          <span class="text-xs text-gray-400">粒度：</span>
          <RadioGroup
            v-model:value="trendGranularity"
            :options="trendGranularityOptions"
            option-type="button"
            button-style="outline"
            size="small"
            @change="loadAnalytics"
          />
        </div>
        <EchartsUI ref="trendChartRef" height="220px" />
      </Card>
    </div>
  </div>
</template>
