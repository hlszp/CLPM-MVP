<script lang="ts" setup>
/**
 * S4-DIAG-011 A/B 对比页
 *
 * 对齐 IDS v3.2 §2.4 + PRD §4.4
 * - 时间范围选择（处置前/处置后）
 * - ECharts 对比图表：
 *   - PV 趋势叠加图（处置前 vs 处置后）
 *   - KPI 柱状对比图（6 大 KPI before vs after）
 * - 统计摘要（改善幅度百分比）
 * - 可从 Tracker 页抽屉打开，也可独立访问
 */
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { DiagnosisApi } from '#/api/diagnosis';

import { computed, onMounted, reactive, ref, watch } from 'vue';
import { useRoute } from 'vue-router';

import { Page } from '@vben/common-ui';
import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import {
  Alert,
  Button,
  Card,
  DatePicker,
  Drawer,
  message,
  Select,
  Spin,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import { getAbCompareApi } from '#/api/diagnosis';
import { getLoopListApi } from '#/api/loop';

defineOptions({ name: 'DiagnosisABCompare' });

const props = withDefaults(
  defineProps<{
    /** 抽屉模式（从 Tracker 页打开） */
    drawerMode?: boolean;
    /** 实施时间点 T（FDS §5.4.4：标记"已实施"后自动截取 [T-7天,T] 与 [T,T+7天]） */
    implementedAt?: string;
    /** 指定回路 ID（抽屉模式） */
    loopId?: string;
  }>(),
  {
    drawerMode: false,
    loopId: '',
    implementedAt: '',
  },
);

const emit = defineEmits<{
  (e: 'close'): void;
}>();

const route = useRoute();

const loading = ref(false);
const compareData = ref<DiagnosisApi.AbCompareResult | null>(null);
const loopOptions = ref<{ label: string; value: string }[]>([]);

const filter = reactive({
  loopId: props.loopId || (route.query.loopId as string) || '',
  beforeRange: [dayjs().subtract(7, 'day'), dayjs().subtract(1, 'day')] as [
    dayjs.Dayjs,
    dayjs.Dayjs,
  ],
  afterRange: [dayjs().subtract(1, 'day'), dayjs()] as [
    dayjs.Dayjs,
    dayjs.Dayjs,
  ],
});

/** FDS §5.4.4：标记"已实施"后自动截取 [T-7天,T] 与 [T,T+7天] */
function autoSetWindows(implementedAtStr: string) {
  if (!implementedAtStr) return;
  const t = dayjs(implementedAtStr);
  if (!t.isValid()) return;
  filter.beforeRange = [t.subtract(7, 'day'), t];
  filter.afterRange = [t, t.add(7, 'day')];
}

/** After 窗口数据是否不足 24h（FDS §5.4.4 提示"评估数据采集中"） */
const isAfterDataInsufficient = computed(() => {
  if (!filter.afterRange || filter.afterRange.length !== 2) return false;
  const [aStart, aEnd] = filter.afterRange;
  if (!aStart || !aEnd) return false;
  // After 窗口结束时间超过当前时间 → 数据尚未采集完整
  const now = dayjs();
  const actualEnd = aEnd.isAfter(now) ? now : aEnd;
  const durationHours = actualEnd.diff(aStart, 'hour');
  return durationHours < 24;
});

// ECharts refs
const trendChartRef = ref<EchartsUIType>();
const kpiChartRef = ref<EchartsUIType>();
const { renderEcharts: renderTrend } = useEcharts(trendChartRef);
const { renderEcharts: renderKpi } = useEcharts(kpiChartRef);

const pageTitle = computed(() => {
  if (compareData.value?.tagName) {
    return `A/B 对比 - ${compareData.value.tagName}`;
  }
  return 'A/B 对比';
});

/** 加载回路下拉选项 */
async function loadLoopOptions() {
  try {
    const data = await getLoopListApi({ page: 1, pageSize: 100 });
    const list = data.items || [];
    loopOptions.value = list.map((l) => ({
      label: l.tagName,
      value: l.loopId,
    }));
    if (!filter.loopId && list.length > 0) {
      const first = list[0];
      if (first) {
        filter.loopId = first.loopId;
      }
    }
  } catch {
    // 错误已由拦截器处理
  }
}

/** 加载对比数据 */
async function loadData() {
  if (!filter.loopId) {
    message.warning('请选择回路');
    return;
  }
  if (
    !filter.beforeRange ||
    filter.beforeRange.length !== 2 ||
    !filter.afterRange ||
    filter.afterRange.length !== 2
  ) {
    message.warning('请选择时间范围');
    return;
  }
  const [bStart, bEnd] = filter.beforeRange;
  const [aStart, aEnd] = filter.afterRange;
  if (!bStart || !bEnd || !aStart || !aEnd) {
    message.warning('请选择时间范围');
    return;
  }
  loading.value = true;
  try {
    const data = await getAbCompareApi({
      loopId: filter.loopId,
      beforeStartTime: bStart.format('YYYY-MM-DD HH:mm:ss'),
      beforeEndTime: bEnd.format('YYYY-MM-DD HH:mm:ss'),
      afterStartTime: aStart.format('YYYY-MM-DD HH:mm:ss'),
      afterEndTime: aEnd.format('YYYY-MM-DD HH:mm:ss'),
    });
    compareData.value = data;
    renderTrendChart();
    renderKpiChart();
  } catch {
    // 错误已由拦截器处理
  } finally {
    loading.value = false;
  }
}

/** 渲染 PV 趋势叠加图 */
function renderTrendChart() {
  const data = compareData.value;
  if (!data || !data.trend) {
    renderTrend({
      title: { left: 'center', text: '暂无数据' },
    });
    return;
  }

  const { before, after } = data.trend;
  renderTrend({
    backgroundColor: 'transparent',
    dataZoom: [
      { end: 100, start: 0, type: 'inside' },
      { end: 100, start: 0, type: 'slider' },
    ],
    grid: {
      bottom: 60,
      containLabel: true,
      left: '2%',
      right: '2%',
      top: 50,
    },
    legend: {
      data: ['处置前 PV', '处置后 PV'],
      top: 5,
    },
    series: [
      {
        connectNulls: false,
        data: before.pv,
        itemStyle: { color: '#ff4d4f' },
        lineStyle: { width: 2 },
        name: '处置前 PV',
        showSymbol: false,
        type: 'line',
      },
      {
        connectNulls: false,
        data: after.pv,
        itemStyle: { color: '#1890ff' },
        lineStyle: { width: 2 },
        name: '处置后 PV',
        showSymbol: false,
        type: 'line',
      },
    ],
    tooltip: {
      axisPointer: { type: 'cross' },
      trigger: 'axis',
      valueFormatter: (val) =>
        val === null || val === undefined ? '—' : Number(val).toFixed(3),
    },
    xAxis: {
      axisLabel: {
        formatter: (val: string) => {
          const d = new Date(Number(val));
          const hh = String(d.getHours()).padStart(2, '0');
          const mm = String(d.getMinutes()).padStart(2, '0');
          const dd = String(d.getDate()).padStart(2, '0');
          const mo = String(d.getMonth() + 1).padStart(2, '0');
          return `${mo}-${dd} ${hh}:${mm}`;
        },
      },
      data: before.timestamps,
      type: 'category',
    },
    yAxis: {
      axisLabel: { formatter: '{value}' },
      type: 'value',
    },
  });
}

/** 渲染 KPI 柱状对比图 */
function renderKpiChart() {
  const data = compareData.value;
  if (!data || !data.kpiComparison || data.kpiComparison.length === 0) {
    renderKpi({
      title: { left: 'center', text: '暂无数据' },
    });
    return;
  }

  const kpis = data.kpiComparison;
  renderKpi({
    backgroundColor: 'transparent',
    grid: {
      bottom: 60,
      containLabel: true,
      left: '2%',
      right: '2%',
      top: 40,
    },
    legend: {
      data: ['处置前', '处置后'],
      top: 5,
    },
    series: [
      {
        barGap: 0,
        data: kpis.map((k) => k.before),
        itemStyle: { color: '#ff4d4f' },
        name: '处置前',
        type: 'bar',
      },
      {
        data: kpis.map((k) => k.after),
        itemStyle: { color: '#1890ff' },
        name: '处置后',
        type: 'bar',
      },
    ],
    tooltip: {
      axisPointer: { type: 'shadow' },
      trigger: 'axis',
      valueFormatter: (val) =>
        val === null || val === undefined ? '—' : Number(val).toFixed(2),
    },
    xAxis: {
      axisLabel: { interval: 0, rotate: 20 },
      data: kpis.map((k) => k.metricName),
      type: 'category',
    },
    yAxis: { type: 'value' },
  });
}

function handleSearch() {
  loadData();
}

/** 计算改善幅度百分比 */
function improvementText(key: string): string {
  if (!compareData.value?.improvement) return '—';
  const val = compareData.value.improvement[key];
  if (val === null || val === undefined) return '—';
  const sign = val >= 0 ? '+' : '';
  return `${sign}${Number(val).toFixed(2)}%`;
}

function improvementColor(key: string): string {
  if (!compareData.value?.improvement) return '';
  const val = compareData.value.improvement[key];
  if (val === null || val === undefined) return '';
  // 对于评分/好值率等正向指标，>0 为绿色；对于振荡率/饱和率等负向指标，<0 为绿色
  const negativeMetrics = ['oscillation_rate', 'saturation_rate'];
  if (negativeMetrics.includes(key)) {
    return val <= 0 ? 'text-green-600' : 'text-red-500';
  }
  return val >= 0 ? 'text-green-600' : 'text-red-500';
}

watch(
  () => props.loopId,
  (val) => {
    if (val) {
      filter.loopId = val;
      // 有 implementedAt 时自动截取窗口
      if (props.implementedAt) {
        autoSetWindows(props.implementedAt);
      }
      loadData();
    }
  },
  { immediate: true },
);

onMounted(() => {
  // 有 implementedAt 时自动截取窗口（FDS §5.4.4）
  if (props.implementedAt) {
    autoSetWindows(props.implementedAt);
  }
  if (!props.drawerMode) {
    loadLoopOptions().then(() => {
      if (filter.loopId) {
        loadData();
      }
    });
  } else if (filter.loopId) {
    loadData();
  }
});
</script>

<template>
  <!-- 抽屉模式 -->
  <Drawer
    v-if="drawerMode"
    :open="true"
    title="A/B 对比分析"
    width="80%"
    placement="right"
    @close="emit('close')"
  >
    <Spin :spinning="loading">
      <!-- 时间范围选择 -->
      <Card class="mb-4">
        <div class="flex flex-wrap items-center gap-3">
          <div class="flex items-center gap-2">
            <span class="text-sm text-gray-500">处置前：</span>
            <DatePicker.RangePicker
              v-model:value="filter.beforeRange"
              :show-time="{ format: 'HH:mm' }"
              format="YYYY-MM-DD HH:mm"
              :placeholder="['开始', '结束']"
            />
          </div>
          <div class="flex items-center gap-2">
            <span class="text-sm text-gray-500">处置后：</span>
            <DatePicker.RangePicker
              v-model:value="filter.afterRange"
              :show-time="{ format: 'HH:mm' }"
              format="YYYY-MM-DD HH:mm"
              :placeholder="['开始', '结束']"
            />
          </div>
          <Button type="primary" :loading="loading" @click="handleSearch">
            查询
          </Button>
        </div>
      </Card>

      <!-- 数据不足提示（FDS §5.4.4） -->
      <Alert
        v-if="isAfterDataInsufficient"
        class="mb-4"
        type="warning"
        show-icon
        message="评估数据采集中，请稍后查看"
        description="处置后数据不足 24 小时，A/B 对比结果可能不准确。建议等待数据采集完整后再进行评估。"
      />

      <!-- 统计摘要 -->
      <Card v-if="compareData" class="mb-4" title="改善摘要">
        <div class="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
          <div
            v-for="kpi in compareData.kpiComparison"
            :key="kpi.metricKey"
            class="rounded border p-3 text-center"
          >
            <div class="text-xs text-gray-500">{{ kpi.metricName }}</div>
            <div class="mt-1 text-sm">
              <span class="text-red-500">{{
                Number(kpi.before).toFixed(2)
              }}</span>
              →
              <span class="text-blue-600">{{
                Number(kpi.after).toFixed(2)
              }}</span>
            </div>
            <div
              class="mt-1 text-xs font-medium"
              :class="improvementColor(kpi.metricKey)"
            >
              {{ improvementText(kpi.metricKey) }}
            </div>
          </div>
        </div>
      </Card>

      <!-- PV 趋势叠加图 -->
      <Card title="PV 趋势对比" class="mb-4">
        <EchartsUI ref="trendChartRef" height="360px" />
      </Card>

      <!-- KPI 柱状对比图 -->
      <Card title="KPI 对比">
        <EchartsUI ref="kpiChartRef" height="360px" />
      </Card>
    </Spin>
  </Drawer>

  <!-- 独立页面模式 -->
  <Page v-else :title="pageTitle">
    <!-- 时间范围选择 -->
    <Card class="mb-4">
      <div class="flex flex-wrap items-center gap-3">
        <div class="flex items-center gap-2">
          <span class="text-sm text-gray-500">回路：</span>
          <Select
            v-model:value="filter.loopId"
            placeholder="选择回路"
            style="width: 240px"
            show-search
            :options="loopOptions"
            :filter-option="
              (input: string, option: any) => option.label.includes(input)
            "
            @change="handleSearch"
          />
        </div>
        <div class="flex items-center gap-2">
          <span class="text-sm text-gray-500">处置前：</span>
          <DatePicker.RangePicker
            v-model:value="filter.beforeRange"
            :show-time="{ format: 'HH:mm' }"
            format="YYYY-MM-DD HH:mm"
            :placeholder="['开始', '结束']"
          />
        </div>
        <div class="flex items-center gap-2">
          <span class="text-sm text-gray-500">处置后：</span>
          <DatePicker.RangePicker
            v-model:value="filter.afterRange"
            :show-time="{ format: 'HH:mm' }"
            format="YYYY-MM-DD HH:mm"
            :placeholder="['开始', '结束']"
          />
        </div>
        <Button type="primary" :loading="loading" @click="handleSearch">
          查询
        </Button>
      </div>
    </Card>

    <!-- 数据不足提示（FDS §5.4.4） -->
    <Alert
      v-if="isAfterDataInsufficient"
      class="mb-4"
      type="warning"
      show-icon
      message="评估数据采集中，请稍后查看"
      description="处置后数据不足 24 小时，A/B 对比结果可能不准确。建议等待数据采集完整后再进行评估。"
    />

    <!-- 统计摘要 -->
    <Card v-if="compareData" class="mb-4" title="改善摘要">
      <div class="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
        <div
          v-for="kpi in compareData.kpiComparison"
          :key="kpi.metricKey"
          class="rounded border p-3 text-center"
        >
          <div class="text-xs text-gray-500">{{ kpi.metricName }}</div>
          <div class="mt-1 text-sm">
            <span class="text-red-500">{{
              Number(kpi.before).toFixed(2)
            }}</span>
            →
            <span class="text-blue-600">{{
              Number(kpi.after).toFixed(2)
            }}</span>
          </div>
          <div
            class="mt-1 text-xs font-medium"
            :class="improvementColor(kpi.metricKey)"
          >
            {{ improvementText(kpi.metricKey) }}
          </div>
        </div>
      </div>
    </Card>

    <!-- PV 趋势叠加图 -->
    <Card title="PV 趋势对比" class="mb-4">
      <Spin :spinning="loading">
        <EchartsUI ref="trendChartRef" height="360px" />
      </Spin>
    </Card>

    <!-- KPI 柱状对比图 -->
    <Card title="KPI 对比">
      <Spin :spinning="loading">
        <EchartsUI ref="kpiChartRef" height="360px" />
      </Spin>
    </Card>
  </Page>
</template>
