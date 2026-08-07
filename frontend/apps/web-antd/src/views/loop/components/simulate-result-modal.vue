<script lang="ts" setup>
/**
 * 工作台 · 模拟仿真结果弹窗（单页四区重构 v2 · 2026-08-07）
 *
 * 展示 simulateTuningApi 返回的闭环仿真结果：
 * - 当前 PID vs 推荐 PID 的响应曲线对比（PV 曲线）
 * - 关键性能指标对比（超调量 / 上升时间 / 稳定时间 / ITAE）
 * - 改善幅度
 */
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { TuningApi } from '#/api/tuning';

import { computed, ref, watch } from 'vue';

import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import { Empty, Modal, Table } from 'ant-design-vue';

import { useClpmTheme } from '#/composables/use-clpm-theme';
import { useEchartsPreset } from '#/composables/use-echarts-preset';

defineOptions({ name: 'SimulateResultModal' });

const props = defineProps<{
  loopTagName?: string;
  open: boolean;
  result: null | TuningApi.SimulationResult;
}>();

const emit = defineEmits<{
  (e: 'update:open', val: boolean): void;
}>();

const { themeColors } = useClpmTheme();
const { getTooltipPreset } = useEchartsPreset();

const chartRef = ref<EchartsUIType>();
const { renderEcharts } = useEcharts(chartRef);

const hasResult = computed(() => props.result !== null);

const metricsRows = computed(() => {
  const r = props.result;
  if (!r) return [];
  const c = r.currentMetrics;
  const m = r.recommendedMetrics;
  const fmt = (v: null | number | undefined, unit = '') =>
    v == null ? '—' : `${Number(v).toFixed(2)}${unit}`;
  return [
    {
      key: 'overshoot',
      label: '超调量(%)',
      current: fmt(c?.overshoot),
      recommended: fmt(m?.overshoot),
    },
    {
      key: 'riseTime',
      label: '上升时间(s)',
      current: fmt(c?.riseTime),
      recommended: fmt(m?.riseTime),
    },
    {
      key: 'settlingTime',
      label: '稳定时间(s)',
      current: fmt(c?.settlingTime),
      recommended: fmt(m?.settlingTime),
    },
    {
      key: 'itae',
      label: 'ITAE',
      current: fmt(c?.itae),
      recommended: fmt(m?.itae),
    },
  ];
});

const improvementText = computed(() => {
  const r = props.result;
  if (!r?.improvement) return '';
  const parts: string[] = [];
  for (const [k, v] of Object.entries(r.improvement)) {
    if (v == null) continue;
    parts.push(`${k}: ${Number(v).toFixed(2)}`);
  }
  return parts.join('， ');
});

function buildOption() {
  const r = props.result;
  if (!r) return null;
  const ts = r.timestamps.map(Number);
  const xData = ts.map((t) => `${t}s`);
  return {
    grid: { top: 32, right: 16, bottom: 32, left: 48, containLabel: true },
    legend: {
      data: ['当前PID', '推荐PID', '设定值'],
      top: 4,
      textStyle: { fontSize: 11 },
      itemWidth: 12,
      itemHeight: 8,
    },
    tooltip: { ...getTooltipPreset(), trigger: 'axis' as const },
    xAxis: {
      type: 'category' as const,
      boundaryGap: false,
      data: xData,
      axisLabel: {
        color: themeColors.value.NEUTRAL,
        fontSize: 10,
        hideOverlap: true,
      },
    },
    yAxis: {
      type: 'value' as const,
      axisLabel: { color: themeColors.value.NEUTRAL, fontSize: 10 },
      splitLine: {
        lineStyle: {
          color: themeColors.value.NEUTRAL,
          type: 'dashed' as const,
          opacity: 0.4,
        },
      },
    },
    series: [
      {
        name: '当前PID',
        type: 'line' as const,
        smooth: false,
        showSymbol: false,
        lineStyle: {
          width: 1.5,
          color: themeColors.value.NEUTRAL,
          type: 'dashed' as const,
        },
        itemStyle: { color: themeColors.value.NEUTRAL },
        data: r.currentResponse.pv,
      },
      {
        name: '推荐PID',
        type: 'line' as const,
        smooth: false,
        showSymbol: false,
        lineStyle: { width: 1.8, color: themeColors.value.INFO },
        itemStyle: { color: themeColors.value.INFO },
        data: r.recommendedResponse.pv,
      },
      {
        name: '设定值',
        type: 'line' as const,
        smooth: false,
        showSymbol: false,
        lineStyle: {
          width: 1,
          color: themeColors.value.WARNING,
          type: 'dotted' as const,
        },
        itemStyle: { color: themeColors.value.WARNING },
        data: r.recommendedResponse.sp,
      },
    ],
  };
}

function refresh() {
  const opt = buildOption();
  if (opt) renderEcharts(opt);
}

watch(
  [() => props.open, () => props.result],
  ([open]) => {
    if (open && props.result) {
      refresh();
    }
  },
  { flush: 'post' },
);

function handleClose() {
  emit('update:open', false);
}
</script>

<template>
  <Modal
    :open="open"
    title="模拟仿真结果"
    :width="720"
    :footer="null"
    @cancel="handleClose"
  >
    <div v-if="loopTagName" class="mb-2 text-sm text-gray-500">
      回路：<span class="font-medium text-gray-700">{{ loopTagName }}</span>
    </div>

    <template v-if="hasResult">
      <Table
        :data-source="metricsRows"
        :pagination="false"
        size="small"
        :columns="[
          { title: '指标', dataIndex: 'label', key: 'label' },
          { title: '当前 PID', dataIndex: 'current', key: 'current' },
          { title: '推荐 PID', dataIndex: 'recommended', key: 'recommended' },
        ]"
        class="mb-3"
      />

      <div v-if="improvementText" class="mb-2 text-xs text-gray-500">
        改善幅度：{{ improvementText }}
      </div>

      <div class="sim-canvas">
        <EchartsUI ref="chartRef" height="320px" />
      </div>
    </template>

    <Empty v-else description="暂无仿真结果" />
  </Modal>
</template>

<style scoped>
.sim-canvas {
  width: 100%;
}
</style>
