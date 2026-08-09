<script lang="ts" setup>
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { DiagnosisApi } from '#/api/diagnosis';

import { computed, onMounted, ref, watch } from 'vue';

import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import { useEchartsPreset } from '#/composables/use-echarts-preset';

const props = defineProps<{
  data: DiagnosisApi.ScatterPlotData;
  disabled?: boolean;
}>();

const { getTooltipPreset, getSeriesColor, themeColors, chartColors, axisBase } =
  useEchartsPreset();

const chartRef = ref<EchartsUIType>();
const { renderEcharts } = useEcharts(chartRef);

const hasData = computed(() => props.data.x.length > 0);

const options = computed(() => {
  if (!hasData.value) {
    return {
      title: { left: 'center', text: '暂无散点数据' },
    };
  }

  const data = props.data.x.map((x, i) => ({
    value: [x, props.data.y[i] ?? 0],
  }));

  const scatterColor = getSeriesColor('info');
  const fittingColor = themeColors.value.DANGER;

  const avgX = props.data.x.reduce((a, b) => a + b, 0) / props.data.x.length;
  const avgY = props.data.y.reduce((a, b) => a + b, 0) / props.data.y.length;
  const n = props.data.x.length;
  const numerator = props.data.x.reduce(
    (sum, x, i) => sum + (x - avgX) * ((props.data.y[i] ?? avgY) - avgY),
    0,
  );
  const denominator = Math.sqrt(
    props.data.x.reduce((sum, x) => sum + (x - avgX) ** 2, 0) *
      props.data.y.reduce((sum, y) => sum + (y - avgY) ** 2, 0),
  );
  const correlation = denominator === 0 ? 0 : numerator / denominator;

  const sx = props.data.x.reduce((sum, x) => sum + (x - avgX) ** 2, 0) / n;
  const sy = props.data.y.reduce((sum, y) => sum + (y - avgY) ** 2, 0) / n;
  const slope = sx === 0 ? 0 : (correlation * sy) / sx;
  const intercept = avgY - slope * avgX;

  const minX = Math.min(...props.data.x);
  const maxX = Math.max(...props.data.x);
  const fitLine = [
    { value: [minX, slope * minX + intercept] },
    { value: [maxX, slope * maxX + intercept] },
  ];

  const option: any = {
    tooltip: {
      ...getTooltipPreset(),
      formatter: (params: any) => {
        const x = params.value?.[0] ?? 0;
        const y = params.value?.[1] ?? 0;
        return `PV: ${x.toFixed(4)}<br/>OP: ${y.toFixed(4)}`;
      },
    },
    xAxis: {
      ...axisBase.value,
      type: 'value',
      name: 'PV 值',
      nameTextStyle: { color: chartColors.value.text, fontSize: 11 },
    },
    yAxis: {
      ...axisBase.value,
      type: 'value',
      name: 'OP 值',
      nameTextStyle: { color: chartColors.value.text, fontSize: 11 },
    },
    grid: {
      top: 40,
      right: 16,
      bottom: 50,
      left: 56,
      containLabel: true,
    },
    series: [
      {
        type: 'scatter',
        data,
        symbolSize: 6,
        itemStyle: {
          color: scatterColor,
          opacity: 0.8,
        },
      },
      {
        name: '拟合线',
        type: 'line',
        data: fitLine,
        smooth: false,
        symbol: 'none',
        lineStyle: { width: 2, color: fittingColor, type: 'dashed' },
      },
    ],
    title: {
      left: 'center',
      top: 10,
      text: 'PV-OP 散点图',
      subtext: `拟合度: ${props.data.fittingScore.toFixed(3)} | 粘滞指数: ${props.data.stictionIndex.toFixed(3)}`,
      textStyle: { fontSize: 14, fontWeight: 600 },
      subtextStyle: { fontSize: 11 },
    },
  };

  return option;
});

watch(
  options,
  (newOptions) => {
    renderEcharts(newOptions);
  },
  { immediate: true },
);

onMounted(() => {
  renderEcharts(options.value);
});
</script>

<template>
  <div class="scatter-chart-container">
    <EchartsUI ref="chartRef" class="w-full h-full" />
  </div>
</template>

<style lang="scss" scoped>
.scatter-chart-container {
  width: 100%;
  height: 100%;
  min-height: 200px;
}
</style>
