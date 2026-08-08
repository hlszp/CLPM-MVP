<script lang="ts" setup>
/**
 * F2h Step 4 — A/B 对比（处置前后 / 任意两时段）
 *
 * 复用 ab-compare.vue 的 ECharts 逻辑（PV 趋势叠加 + KPI 柱状对比），
 * 增量：includeDiagnosis=true，额外渲染标签变更对比区块。
 * 默认预填：before = Step 1 时间窗，after = Step 1 结束时间 + 等长时段。
 */
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { UseLoopAnalysisReturn } from './use-loop-analysis';

import type { DiagnosisApi } from '#/api/diagnosis';

import { nextTick, onMounted, reactive, ref, watch } from 'vue';

import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import { Alert, Button, DatePicker, message, Spin, Tag } from 'ant-design-vue';
import dayjs from 'dayjs';

import { getAbCompareApi, getWaveformApi } from '#/api/diagnosis';
import { ClpmDataCanvas } from '#/components/clpm';
import { useClpmTheme } from '#/composables/use-clpm-theme';

defineOptions({ name: 'StepAbCompare' });

const props = defineProps<{
  state: UseLoopAnalysisReturn;
}>();

const { isDark, themeColors } = useClpmTheme();

/** 时间戳精度转换：纳秒/微秒级→毫秒级 */
function toMs(ts: number): number {
  const absTs = Math.abs(ts);
  if (absTs >= 10_000_000_000_000_000) return Math.floor(ts / 1_000_000);
  if (absTs >= 10_000_000_000_000) return Math.floor(ts / 1000);
  return ts;
}

const loading = ref(false);
const compareData = ref<DiagnosisApi.AbCompareResult | null>(null);

interface AbTrendWindow {
  pv: (null | number)[];
  timestamps: number[];
}
const trendData = ref<null | { after: AbTrendWindow; before: AbTrendWindow }>(
  null,
);

/** 默认预填：before = Step 1 时间窗，after = 结束时间 + 等长时段 */
function buildDefaultRange() {
  const start = dayjs(props.state.config.startTime);
  const end = dayjs(props.state.config.endTime);
  const duration = end.diff(start);
  return {
    beforeRange: [start, end] as [dayjs.Dayjs, dayjs.Dayjs],
    afterRange: [end, end.add(duration)] as [dayjs.Dayjs, dayjs.Dayjs],
  };
}

const filter = reactive(buildDefaultRange());

// ECharts refs
const trendChartRef = ref<EchartsUIType>();
const kpiChartRef = ref<EchartsUIType>();
const { renderEcharts: renderTrend } = useEcharts(trendChartRef);
const { renderEcharts: renderKpi } = useEcharts(kpiChartRef);

/** 图表空态：无数据时不渲染空框架，由 ClpmDataCanvas 空态接管 */
const trendEmpty = ref(false);
const kpiEmpty = ref(false);

async function loadData() {
  if (!props.state.config.loopId) {
    message.warning('回路未选择');
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
      afterEndTime: aEnd.format('YYYY-MM-DD HH:mm:ss'),
      afterStartTime: aStart.format('YYYY-MM-DD HH:mm:ss'),
      beforeEndTime: bEnd.format('YYYY-MM-DD HH:mm:ss'),
      beforeStartTime: bStart.format('YYYY-MM-DD HH:mm:ss'),
      includeDiagnosis: true,
      loopId: props.state.config.loopId,
    });
    compareData.value = data;
    renderKpiChart();
    await loadTrend();
  } catch {
    // 错误已由拦截器处理
  } finally {
    loading.value = false;
  }
}

async function loadTrend() {
  const data = compareData.value;
  if (!data) {
    trendData.value = null;
    renderTrendChart();
    return;
  }
  try {
    const [beforeWf, afterWf] = await Promise.all([
      getWaveformApi(data.loopId, {
        endTime: data.beforeWindow.endTime,
        startTime: data.beforeWindow.startTime,
      }),
      getWaveformApi(data.loopId, {
        endTime: data.afterWindow.endTime,
        startTime: data.afterWindow.startTime,
      }),
    ]);
    trendData.value = {
      after: { pv: afterWf.pv, timestamps: afterWf.timestamps },
      before: { pv: beforeWf.pv, timestamps: beforeWf.timestamps },
    };
  } catch {
    trendData.value = null;
  } finally {
    renderTrendChart();
  }
}

function renderTrendChart() {
  const trend = trendData.value;
  if (!trend) {
    trendEmpty.value = true;
    return;
  }
  trendEmpty.value = false;
  const { before, after } = trend;
  renderTrend({
    backgroundColor: 'transparent',
    dataZoom: [
      { end: 100, start: 0, type: 'inside' },
      { end: 100, start: 0, type: 'slider' },
    ],
    grid: { bottom: 60, containLabel: true, left: '2%', right: '2%', top: 50 },
    legend: { data: ['处置前 PV', '处置后 PV'], top: 5 },
    series: [
      {
        connectNulls: false,
        data: before.pv,
        itemStyle: { color: themeColors.value.DANGER },
        lineStyle: { width: 2 },
        name: '处置前 PV',
        showSymbol: false,
        type: 'line',
      },
      {
        connectNulls: false,
        data: after.pv,
        itemStyle: { color: themeColors.value.INFO },
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
          const d = new Date(toMs(Number(val)) + 8 * 3600 * 1000);
          const hh = String(d.getUTCHours()).padStart(2, '0');
          const mm = String(d.getUTCMinutes()).padStart(2, '0');
          const dd = String(d.getUTCDate()).padStart(2, '0');
          const mo = String(d.getUTCMonth() + 1).padStart(2, '0');
          return `${mo}-${dd} ${hh}:${mm}`;
        },
      },
      data: before.timestamps,
      type: 'category',
    },
    yAxis: { axisLabel: { formatter: '{value}' }, type: 'value' },
  });
}

function renderKpiChart() {
  const data = compareData.value;
  if (!data || !data.kpiComparison || data.kpiComparison.length === 0) {
    kpiEmpty.value = true;
    return;
  }
  kpiEmpty.value = false;
  const kpis = data.kpiComparison;
  renderKpi({
    backgroundColor: 'transparent',
    grid: { bottom: 60, containLabel: true, left: '2%', right: '2%', top: 40 },
    legend: { data: ['处置前', '处置后'], top: 5 },
    series: [
      {
        barGap: 0,
        data: kpis.map((k) => k.before),
        itemStyle: { color: themeColors.value.DANGER },
        name: '处置前',
        type: 'bar',
      },
      {
        data: kpis.map((k) => k.after),
        itemStyle: { color: themeColors.value.INFO },
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

/** 标签变更颜色 */
function changeTagColor(change: string): string {
  if (change === 'added') return 'success';
  if (change === 'removed') return 'error';
  return 'warning';
}

/** 标签变更文案 */
function changeText(item: DiagnosisApi.LabelChangeItem): string {
  if (item.change === 'added') return '新增';
  if (item.change === 'removed') return '消失';
  const before =
    item.beforeConfidence !== null && item.beforeConfidence !== undefined
      ? Math.round(item.beforeConfidence * 100)
      : '—';
  const after =
    item.afterConfidence !== null && item.afterConfidence !== undefined
      ? Math.round(item.afterConfidence * 100)
      : '—';
  return `置信度变化 ${before}% → ${after}%`;
}

function kpiValueText(val: null | number, unit: string): string {
  if (val === null || val === undefined) return '—';
  const text = Number(val).toFixed(2);
  return unit ? `${text}${unit}` : text;
}

function changeColor(kpi: DiagnosisApi.AbCompareKpiItem): string {
  if (kpi.improved === true) return themeColors.value.SUCCESS;
  if (kpi.improved === false) return themeColors.value.DANGER;
  return themeColors.value.NEUTRAL;
}

function changePctText(kpi: DiagnosisApi.AbCompareKpiItem): string {
  if (kpi.changePct === null || kpi.changePct === undefined) return '—';
  const sign = kpi.changePct >= 0 ? '+' : '';
  return `${sign}${Number(kpi.changePct).toFixed(2)}%`;
}

watch(isDark, () => {
  nextTick(() => {
    if (compareData.value) {
      renderTrendChart();
      renderKpiChart();
    }
  });
});

onMounted(loadData);
</script>

<template>
  <div class="flex flex-col gap-4">
    <ClpmDataCanvas title="A/B 对比筛选">
      <div class="flex flex-wrap items-center gap-3">
        <div class="flex items-center gap-2">
          <span class="text-sm" :style="{ color: themeColors.NEUTRAL }"
            >处置前：</span
          >
          <DatePicker.RangePicker
            v-model:value="filter.beforeRange"
            :show-time="{ format: 'HH:mm' }"
            format="YYYY-MM-DD HH:mm"
            :placeholder="['开始', '结束']"
          />
        </div>
        <div class="flex items-center gap-2">
          <span class="text-sm" :style="{ color: themeColors.NEUTRAL }"
            >处置后：</span
          >
          <DatePicker.RangePicker
            v-model:value="filter.afterRange"
            :show-time="{ format: 'HH:mm' }"
            format="YYYY-MM-DD HH:mm"
            :placeholder="['开始', '结束']"
          />
        </div>
        <Button type="primary" :loading="loading" @click="loadData"
          >查询</Button
        >
      </div>
    </ClpmDataCanvas>

    <Alert
      v-if="compareData?.dataInsufficient"
      type="warning"
      show-icon
      message="评估数据采集中，请稍后查看"
      description="处置后数据不足 24 小时，A/B 对比结果可能不准确。"
    />

    <Spin :spinning="loading">
      <!-- 改善摘要 -->
      <ClpmDataCanvas v-if="compareData" title="KPI 改善摘要" class="mb-4">
        <div class="grid grid-cols-2 gap-3 md:grid-cols-4">
          <div
            v-for="kpi in compareData.kpiComparison"
            :key="kpi.metricKey"
            class="rounded border border-solid p-3 text-center"
          >
            <div class="text-xs" :style="{ color: themeColors.NEUTRAL }">
              {{ kpi.metricName }}
            </div>
            <div class="mt-1 text-sm">
              <span :style="{ color: themeColors.DANGER }">{{
                kpiValueText(kpi.before, kpi.unit)
              }}</span>
              →
              <span :style="{ color: themeColors.INFO }">{{
                kpiValueText(kpi.after, kpi.unit)
              }}</span>
            </div>
            <div
              class="mt-1 text-xs font-medium"
              :style="{ color: changeColor(kpi) }"
            >
              {{ changePctText(kpi) }}
            </div>
          </div>
        </div>
      </ClpmDataCanvas>

      <!-- 标签变更对比 -->
      <ClpmDataCanvas
        v-if="compareData?.labelChanges && compareData.labelChanges.length > 0"
        title="诊断标签变更"
        class="mb-4"
      >
        <div class="flex flex-wrap gap-2">
          <Tag
            v-for="(item, idx) in compareData.labelChanges"
            :key="idx"
            :color="changeTagColor(item.change)"
          >
            {{ item.label }} · {{ changeText(item) }}
          </Tag>
        </div>
      </ClpmDataCanvas>

      <!-- PV 趋势对比 -->
      <ClpmDataCanvas
        title="PV 趋势对比"
        class="mb-4"
        :empty="trendEmpty"
        empty-reason="所选时间窗内未采集到 PV 波形数据，可调整时间范围后重新查询"
      >
        <EchartsUI ref="trendChartRef" height="360px" />
      </ClpmDataCanvas>

      <!-- KPI 柱状对比 -->
      <ClpmDataCanvas
        title="KPI 对比"
        :empty="kpiEmpty"
        empty-reason="当前回路在所选时间窗内无 KPI 统计数据"
      >
        <EchartsUI ref="kpiChartRef" height="360px" />
      </ClpmDataCanvas>
    </Spin>
  </div>
</template>
