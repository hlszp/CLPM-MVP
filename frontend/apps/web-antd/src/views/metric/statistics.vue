<script lang="ts" setup>
/**
 * S3-METRIC-011 性能统计报表页
 *
 * 对齐 IDS v3.2 §2.3 + PRD §4.3
 * - 顶部筛选栏（时间范围/装置/指标/粒度 hour/day/week/month）
 * - ECharts 趋势图（KPI 趋势折线图，支持多指标对比）
 * - 装置评分对比柱状图
 * - 差等生分布饼图
 * - 支持导出 CSV 按钮
 * - 粒度切换时图表自动更新
 */
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { Granularity, MetricApi } from '#/api/metric';
import type { PlantNodeApi } from '#/api/plant-node';

import { onMounted, reactive, ref, watch } from 'vue';

import { Page } from '@vben/common-ui';
import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import { Button, Card, DatePicker, message, Select } from 'ant-design-vue';
import dayjs from 'dayjs';

import { exportAnalyticsApi, getAnalyticsApi } from '#/api/metric';
import { getPlantNodeTreeApi } from '#/api/plant-node';
import { flattenNodes } from '#/utils/plant-node';

defineOptions({ name: 'MetricStatistics' });

const loading = ref(false);
const exporting = ref(false);
const analyticsData = ref<MetricApi.AnalyticsResult | null>(null);
const plantNodes = ref<PlantNodeApi.PlantNode[]>([]);

const filter = reactive({
  timeRange: [dayjs().subtract(7, 'day'), dayjs()] as [
    dayjs.Dayjs,
    dayjs.Dayjs,
  ],
  plantNodeId: undefined as string | undefined,
  metricKey: 'score' as string,
  granularity: 'day' as Granularity,
});

const metricOptions = [
  { label: '综合评分', value: 'score' },
  { label: '好值率', value: 'good_value_rate' },
  { label: '自控率', value: 'auto_mode_rate' },
  { label: '平稳率', value: 'steady_rate' },
  { label: '准确率', value: 'accuracy_rate' },
  { label: '振荡率', value: 'oscillation_rate' },
  { label: '饱和率', value: 'saturation_rate' },
];

const granularityOptions = [
  { label: '小时', value: 'hour' },
  { label: '天', value: 'day' },
  { label: '周', value: 'week' },
  { label: '月', value: 'month' },
];

// ECharts refs
const trendChartRef = ref<EchartsUIType>();
const unitChartRef = ref<EchartsUIType>();
const badActorChartRef = ref<EchartsUIType>();

const { renderEcharts: renderTrend } = useEcharts(trendChartRef);
const { renderEcharts: renderUnit } = useEcharts(unitChartRef);
const { renderEcharts: renderBadActor } = useEcharts(badActorChartRef);

/** 加载工厂节点 */
async function loadPlantNodes() {
  try {
    const tree = await getPlantNodeTreeApi();
    plantNodes.value = flattenNodes(tree);
  } catch {
    // 错误已由拦截器处理
  }
}

/** 加载报表数据 */
async function loadData() {
  if (!filter.timeRange || filter.timeRange.length !== 2) {
    message.warning('请选择时间范围');
    return;
  }
  const [start, end] = filter.timeRange;
  if (!start || !end) {
    message.warning('请选择时间范围');
    return;
  }
  loading.value = true;
  try {
    const data = await getAnalyticsApi({
      startTime: start.format('YYYY-MM-DD HH:mm:ss'),
      endTime: end.format('YYYY-MM-DD HH:mm:ss'),
      plantNodeId: filter.plantNodeId,
      metricKey: filter.metricKey,
      granularity: filter.granularity,
    });
    analyticsData.value = data;
    renderAllCharts();
  } catch {
    // 错误已由拦截器处理
  } finally {
    loading.value = false;
  }
}

/** 渲染所有图表 */
function renderAllCharts() {
  renderTrendChart();
  renderUnitChart();
  renderBadActorChart();
}

/** 渲染 KPI 趋势折线图 */
function renderTrendChart() {
  const trend = analyticsData.value?.kpiTrend;
  if (!trend || !trend.timestamps || trend.timestamps.length === 0) {
    renderTrend({
      title: { left: 'center', text: '暂无数据' },
    });
    return;
  }

  renderTrend({
    grid: { bottom: 30, containLabel: true, left: '2%', right: '2%', top: 40 },
    legend: {
      data: trend.series.map((s) => s.metricName),
      top: 5,
    },
    series: trend.series.map((s) => ({
      data: s.values,
      name: s.metricName,
      smooth: true,
      type: 'line',
    })),
    tooltip: {
      axisPointer: { type: 'cross' },
      trigger: 'axis',
      valueFormatter: (val) =>
        val === null || val === undefined ? '—' : Number(val).toFixed(1),
    },
    xAxis: {
      axisLabel: {
        formatter: (val: string) => {
          try {
            const d = new Date(val);
            const mm = String(d.getMonth() + 1).padStart(2, '0');
            const dd = String(d.getDate()).padStart(2, '0');
            const hh = String(d.getHours()).padStart(2, '0');
            return `${mm}-${dd} ${hh}:00`;
          } catch {
            return val;
          }
        },
      },
      boundaryGap: false,
      data: trend.timestamps,
      type: 'category',
    },
    yAxis: { type: 'value' },
  });
}

/** 渲染装置评分柱状图 */
function renderUnitChart() {
  const ranking = analyticsData.value?.unitRanking || [];
  if (ranking.length === 0) {
    renderUnit({
      title: { left: 'center', text: '暂无数据' },
    });
    return;
  }

  renderUnit({
    grid: { bottom: 40, containLabel: true, left: '2%', right: '2%', top: 30 },
    series: [
      {
        barWidth: '50%',
        data: ranking.map((r) => r.score),
        itemStyle: { color: '#0D6EFD' },
        name: '评分',
        type: 'bar',
      },
    ],
    tooltip: {
      axisPointer: { type: 'shadow' },
      trigger: 'axis',
      valueFormatter: (val) =>
        val === null || val === undefined ? '—' : Number(val).toFixed(1),
    },
    xAxis: {
      axisLabel: { interval: 0, rotate: ranking.length > 5 ? 30 : 0 },
      data: ranking.map((r) => r.unitName),
      type: 'category',
    },
    yAxis: { type: 'value' },
  });
}

/** 渲染差等生分布饼图 */
function renderBadActorChart() {
  const dist = analyticsData.value?.badActorDistribution || [];
  if (dist.length === 0) {
    renderBadActor({
      title: { left: 'center', text: '暂无数据' },
    });
    return;
  }

  renderBadActor({
    legend: { bottom: 0, orient: 'horizontal' },
    series: [
      {
        avoidLabelOverlap: false,
        data: dist.map((d) => ({ name: d.label, value: d.count })),
        emphasis: {
          itemStyle: {
            shadowBlur: 10,
            shadowOffsetX: 0,
            shadowColor: 'rgba(0, 0, 0, 0.5)',
          },
        },
        label: { formatter: '{b}: {c} ({d}%)', show: true },
        radius: ['40%', '70%'],
        type: 'pie',
      },
    ],
    tooltip: { trigger: 'item' },
  });
}

/** 导出 CSV */
async function handleExport() {
  if (!filter.timeRange || filter.timeRange.length !== 2) {
    message.warning('请选择时间范围');
    return;
  }
  const [start, end] = filter.timeRange;
  if (!start || !end) {
    message.warning('请选择时间范围');
    return;
  }
  exporting.value = true;
  try {
    const result = await exportAnalyticsApi({
      startTime: start.format('YYYY-MM-DD HH:mm:ss'),
      endTime: end.format('YYYY-MM-DD HH:mm:ss'),
      plantNodeId: filter.plantNodeId,
      metricKey: filter.metricKey,
      granularity: filter.granularity,
      format: 'csv',
    });
    message.success(`导出任务已提交，任务 ID：${result.taskId}`);
  } catch {
    // 错误已由拦截器处理
  } finally {
    exporting.value = false;
  }
}

function handleSearch() {
  loadData();
}

// 粒度切换时图表自动更新
watch(
  () => filter.granularity,
  () => {
    loadData();
  },
);

onMounted(() => {
  loadPlantNodes();
  loadData();
});
</script>

<template>
  <Page title="性能统计报表">
    <!-- 筛选栏 -->
    <Card class="mb-4">
      <div class="flex flex-wrap items-center gap-3">
        <DatePicker.RangePicker
          v-model:value="filter.timeRange"
          :show-time="{ format: 'HH:mm' }"
          format="YYYY-MM-DD HH:mm"
          :placeholder="['开始时间', '结束时间']"
        />
        <Select
          v-model:value="filter.plantNodeId"
          placeholder="装置/单元筛选"
          style="width: 220px"
          allow-clear
          :options="plantNodes.map((n) => ({ label: n.name, value: n.id }))"
          @change="handleSearch"
        />
        <Select
          v-model:value="filter.metricKey"
          style="width: 160px"
          :options="metricOptions"
          @change="handleSearch"
        />
        <Select
          v-model:value="filter.granularity"
          style="width: 120px"
          :options="granularityOptions"
        />
        <Button type="primary" :loading="loading" @click="handleSearch">
          查询
        </Button>
        <Button :loading="exporting" @click="handleExport"> 导出 CSV </Button>
      </div>
    </Card>

    <!-- KPI 趋势折线图 -->
    <Card title="KPI 趋势" class="mb-4" :loading="loading">
      <EchartsUI ref="trendChartRef" height="360px" />
    </Card>

    <div class="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <!-- 装置评分对比柱状图 -->
      <Card title="装置评分对比" :loading="loading">
        <EchartsUI ref="unitChartRef" height="320px" />
      </Card>

      <!-- 差等生分布饼图 -->
      <Card title="差等生分布" :loading="loading">
        <EchartsUI ref="badActorChartRef" height="320px" />
      </Card>
    </div>
  </Page>
</template>
