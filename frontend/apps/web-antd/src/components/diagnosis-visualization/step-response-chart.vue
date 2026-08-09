<script lang="ts" setup>
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { DiagnosisApi } from '#/api/diagnosis';

import { computed, onMounted, ref, watch } from 'vue';

import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import { useEchartsPreset } from '#/composables/use-echarts-preset';

const props = defineProps<{
  data: DiagnosisApi.StepResponseData;
  disabled?: boolean;
}>();

const { getTooltipPreset, getSeriesColor, themeColors, chartColors, axisBase } =
  useEchartsPreset();

const chartRef = ref<EchartsUIType>();
const { renderEcharts } = useEcharts(chartRef);

const hasData = computed(() => props.data.pvResponse.length > 0);

const options = computed(() => {
  if (!hasData.value) {
    return {
      title: { left: 'center', text: '暂无阶跃响应数据' },
    };
  }

  const pvColor = getSeriesColor('info');
  const spColor = getSeriesColor('ok');
  const overshootColor = themeColors.value.DANGER;

  const pvData = props.data.pvResponse.map((pv, i) => ({
    value: [i, pv],
  }));

  const spData = props.data.spValues.map((sp, i) => ({
    value: [i, sp],
  }));

  const peakIdx = props.data.pvResponse.indexOf(
    Math.max(...props.data.pvResponse),
  );
  const peakValue = props.data.pvResponse[peakIdx];

  const option: any = {
    tooltip: {
      ...getTooltipPreset(),
      formatter: (params: any) => {
        const v = params.value?.[1] ?? 0;
        return `${params.seriesName}: ${v.toFixed(4)}`;
      },
    },
    xAxis: {
      ...axisBase.value,
      type: 'value',
      name: '时间 (采样点)',
      nameTextStyle: { color: chartColors.value.text, fontSize: 11 },
    },
    yAxis: {
      ...axisBase.value,
      type: 'value',
      name: 'PV 值',
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
        name: 'PV 响应',
        type: 'line',
        data: pvData,
        smooth: false,
        symbol: 'circle',
        symbolSize: 4,
        showSymbol: false,
        lineStyle: { width: 2, color: pvColor },
        itemStyle: { color: pvColor },
      },
      {
        name: 'SP 设定值',
        type: 'line',
        data: spData,
        smooth: false,
        symbol: 'none',
        lineStyle: { width: 2, color: spColor, type: 'dashed' },
      },
      {
        type: 'scatter',
        data: [
          {
            value: [peakIdx, peakValue],
            symbolSize: 12,
            itemStyle: {
              color: overshootColor,
              borderColor: chartColors.value.border,
              borderWidth: 2,
            },
          },
        ],
        label: {
          show: true,
          position: 'top',
          formatter: `过冲: ${(props.data.overshoot * 100).toFixed(1)}%`,
          fontSize: 11,
          color: overshootColor,
        },
      },
    ],
    title: {
      left: 'center',
      top: 10,
      text: '阶跃响应曲线',
      subtext: `衰减比: ${props.data.decayRatio.toFixed(3)} | 稳态误差: ${(props.data.steadyStateError * 100).toFixed(1)}%`,
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
  <div class="step-response-chart-container">
    <EchartsUI ref="chartRef" class="w-full h-full" />
  </div>
</template>

<style lang="scss" scoped>
.step-response-chart-container {
  width: 100%;
  height: 100%;
  min-height: 200px;
}
</style>
