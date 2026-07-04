<script lang="ts" setup>
/**
 * S3-METRIC-011 性能统计报表页
 *
 * 对齐 IDS v3.2 §2.3 + PRD §4.3
 * - 顶部 PageToolbar（时间范围/装置/指标/粒度筛选 + 刷新/导出按钮）
 * - KpiStrip：平均评分 / 低效回路数 / 同比 / 环比
 * - Partial 警告横幅（INCONCLUSIVE 回路时显示）
 * - ECharts 趋势图（KPI 趋势折线图，支持多指标对比）
 * - 装置评分对比柱状图
 * - 差等生分布饼图
 * - 图表区域用 ClpmDataCanvas 包裹，支持 empty/error/loading 状态
 * - 粒度切换时图表自动更新
 */
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { Granularity, MetricApi } from '#/api/metric';
import type { PlantNodeApi } from '#/api/plant-node';
import type { KpiStripItem } from '#/components/clpm';

import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue';

import { Page } from '@vben/common-ui';
import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import { Alert, DatePicker, message, Select } from 'ant-design-vue';
import dayjs from 'dayjs';

import {
  ClpmDataCanvas,
  ClpmKpiStrip,
  ClpmPageToolbar,
  ClpmToolbarButton,
} from '#/components/clpm';
import { exportAnalyticsApi, getAnalyticsApi } from '#/api/metric';
import { getPlantNodeTreeApi } from '#/api/plant-node';
import { flattenNodes } from '#/utils/plant-node';
import { useClpmTheme } from '#/composables/use-clpm-theme';

defineOptions({ name: 'MetricStatistics' });

const { isDark, themeColors } = useClpmTheme();

// 初始为 true，避免首屏闪烁空态（onMounted 立即触发 loadData）
const loading = ref(true);
const loadError = ref(false);
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
  { label: '有效自控率', value: 'effective_auto_rate' },
  { label: '平稳率', value: 'steady_rate' },
  { label: '准确率', value: 'accuracy_rate' },
  { label: '快速率', value: 'fast_rate' },
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

// ===== KpiStrip 派生指标 =====

/** 平均评分（装置评分加权均值；无数据时返回 null） */
const avgScore = computed(() => {
  const ranking = analyticsData.value?.unitRanking || [];
  if (ranking.length === 0) return null;
  const total = ranking.reduce(
    (sum, r) => sum + (Number(r.score) || 0) * (r.loopCount || 1),
    0,
  );
  const weight = ranking.reduce((sum, r) => sum + (r.loopCount || 1), 0);
  return weight > 0 ? total / weight : null;
});

/** 低效回路数（装置评分 < 60 的回路总数） */
const lowPerformerCount = computed(() => {
  const ranking = analyticsData.value?.unitRanking || [];
  return ranking
    .filter((r) => r.score < 60)
    .reduce((sum, r) => sum + (r.loopCount || 0), 0);
});

/** 不确定回路数（基于差等生分布中含"不确定/INCONCLUSIVE"标签派生） */
const inconclusiveCount = computed(() => {
  const dist = analyticsData.value?.badActorDistribution || [];
  const item = dist.find((d) => /不确定|INCONCLUSIVE/i.test(d.label));
  return item?.count ?? 0;
});

const kpiStripItems = computed<KpiStripItem[]>(() => {
  const avg = avgScore.value;
  const avgValue = avg === null ? '—' : avg.toFixed(1);
  const avgStatus: KpiStripItem['status'] =
    avg === null
      ? 'neutral'
      : avg >= 80
        ? 'success'
        : avg >= 60
          ? 'warning'
          : 'danger';
  const lowCount = lowPerformerCount.value;
  return [
    {
      key: 'avgScore',
      label: '平均评分',
      value: avgValue,
      status: avgStatus,
    },
    {
      key: 'lowPerformer',
      label: '低效回路数',
      value: lowCount,
      status: lowCount > 0 ? 'danger' : 'success',
    },
    // 同比/环比后端暂未提供，使用占位
    {
      key: 'yoy',
      label: '同比',
      value: '—',
      status: 'neutral',
    },
    {
      key: 'mom',
      label: '环比',
      value: '—',
      status: 'neutral',
    },
  ];
});

// ===== 图表空态派生 =====

const trendEmpty = computed(() => {
  const t = analyticsData.value?.kpiTrend;
  return !t || !t.timestamps || t.timestamps.length === 0;
});

const unitEmpty = computed(
  () => (analyticsData.value?.unitRanking || []).length === 0,
);

const badActorEmpty = computed(
  () => (analyticsData.value?.badActorDistribution || []).length === 0,
);

// ===== 数据加载 =====

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
  loadError.value = false;
  try {
    const data = await getAnalyticsApi({
      startTime: start.format('YYYY-MM-DD HH:mm:ss'),
      endTime: end.format('YYYY-MM-DD HH:mm:ss'),
      plantNodeId: filter.plantNodeId,
      metricKey: filter.metricKey,
      granularity: filter.granularity,
    });
    analyticsData.value = data;
  } catch {
    loadError.value = true;
  } finally {
    loading.value = false;
  }
  // loading=false 后 EchartsUI 进入 DOM，再渲染图表
  if (!loadError.value && analyticsData.value) {
    await nextTick();
    renderAllCharts();
  }
}

/** 渲染所有图表（仅渲染有数据的图表，避免空态下触发 useEcharts 重试循环） */
function renderAllCharts() {
  if (!trendEmpty.value) renderTrendChart();
  if (!unitEmpty.value) renderUnitChart();
  if (!badActorEmpty.value) renderBadActorChart();
}

/** 渲染 KPI 趋势折线图 */
function renderTrendChart() {
  const trend = analyticsData.value?.kpiTrend;
  if (!trend || !trend.timestamps || trend.timestamps.length === 0) {
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
  if (ranking.length === 0) return;

  renderUnit({
    grid: { bottom: 40, containLabel: true, left: '2%', right: '2%', top: 30 },
    series: [
      {
        barWidth: '50%',
        data: ranking.map((r) => r.score),
        itemStyle: { color: themeColors.value.INFO },
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
  if (dist.length === 0) return;

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

function handleRetry() {
  loadData();
}

// 粒度切换时图表自动更新
watch(
  () => filter.granularity,
  () => {
    loadData();
  },
);

// ===== 主题切换重渲图表 =====
watch(isDark, () => {
  nextTick(() => {
    renderAllCharts();
  });
});

onMounted(() => {
  loadPlantNodes();
  loadData();
});
</script>

<template>
  <Page>
    <ClpmPageToolbar
      title="性能统计报表"
      subtitle="趋势、装置评分和差等生分布的统一分析入口。"
      :loading="loading"
    >
      <DatePicker.RangePicker
        v-model:value="filter.timeRange"
        :show-time="{ format: 'HH:mm' }"
        format="YYYY-MM-DD HH:mm"
        :placeholder="['开始时间', '结束时间']"
        size="small"
        @change="handleSearch"
      />
      <Select
        v-model:value="filter.plantNodeId"
        placeholder="装置/单元筛选"
        style="width: 200px"
        size="small"
        allow-clear
        :options="plantNodes.map((n) => ({ label: n.name, value: n.id }))"
        @change="handleSearch"
      />
      <Select
        v-model:value="filter.metricKey"
        style="width: 160px"
        size="small"
        :options="metricOptions"
        @change="handleSearch"
      />
      <Select
        v-model:value="filter.granularity"
        style="width: 100px"
        size="small"
        :options="granularityOptions"
      />
      <template #actions>
        <ClpmToolbarButton
          icon="refresh"
          label="刷新"
          :loading="loading"
          @click="handleSearch"
        />
        <ClpmToolbarButton
          icon="export"
          label="导出"
          :loading="exporting"
          @click="handleExport"
        />
      </template>
    </ClpmPageToolbar>

    <!-- KpiStrip：平均评分 / 低效回路数 / 同比 / 环比 -->
    <ClpmKpiStrip class="mt-3" :items="kpiStripItems" :loading="loading" />

    <!-- Partial 警告横幅：存在不确定回路时显示 -->
    <Alert
      v-if="!loading && inconclusiveCount > 0"
      class="mt-3"
      type="warning"
      show-icon
      :message="`当前有 ${inconclusiveCount} 个回路评估结果为不确定，建议检查数据质量`"
    />

    <!-- KPI 趋势折线图 -->
    <ClpmDataCanvas
      title="KPI 趋势"
      class="mt-3 mb-4"
      :loading="loading"
      :error="loadError"
      :empty="!loading && !loadError && trendEmpty"
      @retry="handleRetry"
    >
      <EchartsUI ref="trendChartRef" height="360px" />
    </ClpmDataCanvas>

    <div class="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <!-- 装置评分对比柱状图 -->
      <ClpmDataCanvas
        title="装置评分对比"
        :loading="loading"
        :error="loadError"
        :empty="!loading && !loadError && unitEmpty"
        @retry="handleRetry"
      >
        <EchartsUI ref="unitChartRef" height="320px" />
      </ClpmDataCanvas>

      <!-- 差等生分布饼图 -->
      <ClpmDataCanvas
        title="差等生分布"
        :loading="loading"
        :error="loadError"
        :empty="!loading && !loadError && badActorEmpty"
        @retry="handleRetry"
      >
        <EchartsUI ref="badActorChartRef" height="320px" />
      </ClpmDataCanvas>
    </div>
  </Page>
</template>
