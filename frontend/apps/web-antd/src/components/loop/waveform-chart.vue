<script lang="ts" setup>
/**
 * ECharts 波形图组件
 *
 * 对齐 D06 §6 + IDS v3.2 §2.2.14：
 * - 展示 PV/SP/OP 趋势线
 * - PV 线按质量码断线渲染：Bad 质量时段断线（connectNulls: false）
 * - 数据超过 1 万点时启用 dataZoom 平滑渲染
 */
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { LoopApi } from '#/api/loop';

import { ref, watch } from 'vue';

import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

defineOptions({ name: 'WaveformChart' });

const props = defineProps<{
  height?: string;
  trend: LoopApi.MonitorTrend;
}>();

const chartRef = ref<EchartsUIType>();
const { renderEcharts } = useEcharts(chartRef);

/**
 * 构建 PV 数据：Bad 质量时段置 null，实现断线效果
 */
function buildPvData(
  timestamps: number[],
  pv: (null | number)[],
  pvQuality: LoopApi.Quality[],
): [number, null | number][] {
  return timestamps.map((ts, i) => {
    const q = pvQuality[i];
    const v = pv[i] ?? null;
    // Bad 质量时段断线
    if (q === 'Bad') return [ts, null];
    return [ts, v];
  });
}

function buildSimpleData(
  timestamps: number[],
  values: (null | number)[],
): [number, null | number][] {
  return timestamps.map((ts, i) => [ts, values[i] ?? null]);
}

function render() {
  const { timestamps, pv, sp, op, pvQuality } = props.trend;
  if (!timestamps || timestamps.length === 0) return;

  const pvData = buildPvData(timestamps, pv, pvQuality);
  const spData = buildSimpleData(timestamps, sp);
  const opData = buildSimpleData(timestamps, op);
  const enableDataZoom = timestamps.length > 10_000;

  renderEcharts({
    backgroundColor: 'transparent',
    dataZoom: enableDataZoom
      ? [
          { end: 100, start: 0, type: 'inside' },
          {
            end: 100,
            handleSize: '100%',
            start: 0,
            type: 'slider',
          },
        ]
      : [],
    grid: {
      bottom: enableDataZoom ? 60 : 30,
      containLabel: true,
      left: '2%',
      right: '2%',
      top: 40,
    },
    legend: {
      data: ['PV', 'SP', 'OP'],
      top: 5,
    },
    series: [
      {
        connectNulls: false,
        data: pvData,
        itemStyle: { color: '#0D6EFD' },
        lineStyle: { width: 2 },
        name: 'PV',
        showSymbol: false,
        type: 'line',
      },
      {
        connectNulls: false,
        data: spData,
        itemStyle: { color: '#52c41a' },
        lineStyle: { type: 'dashed', width: 1.5 },
        name: 'SP',
        showSymbol: false,
        type: 'line',
      },
      {
        connectNulls: false,
        data: opData,
        itemStyle: { color: '#fa8c16' },
        lineStyle: { width: 1.5 },
        name: 'OP',
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
        formatter: (val: number) => {
          const d = new Date(val);
          const hh = String(d.getHours()).padStart(2, '0');
          const mm = String(d.getMinutes()).padStart(2, '0');
          const dd = String(d.getDate()).padStart(2, '0');
          const mo = String(d.getMonth() + 1).padStart(2, '0');
          return `${mo}-${dd} ${hh}:${mm}`;
        },
      },
      type: 'time',
    },
    yAxis: {
      axisLabel: { formatter: '{value}' },
      type: 'value',
    },
  });
}

watch(
  () => props.trend,
  () => render(),
  { deep: true, immediate: true },
);
</script>

<template>
  <EchartsUI ref="chartRef" :style="{ height: height || '360px' }" />
</template>
