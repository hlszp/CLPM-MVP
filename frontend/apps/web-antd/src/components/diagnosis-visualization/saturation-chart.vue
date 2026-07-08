<script lang="ts" setup>
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { DiagnosisApi } from '#/api/diagnosis';

import { computed, onMounted, ref, watch } from 'vue';

import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import { useEchartsPreset } from '#/composables/use-echarts-preset';

const props = defineProps<{
  data: DiagnosisApi.SaturationAnalysisData;
  disabled?: boolean;
}>();

const { getSeriesColor, axisBase } = useEchartsPreset();

const chartRef = ref<EchartsUIType>();
const { renderEcharts } = useEcharts(chartRef);

const options = computed(() => {
  const highColor = getSeriesColor('error');
  const lowColor = getSeriesColor('warning');

  const option: any = {
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (params: any) => {
        return params.map((item: any) => `${item.seriesName}: ${item.value}`).join('<br/>');
      },
    },
    xAxis: {
      ...axisBase.value,
      type: 'category',
      data: ['OP 饱和'],
      nameTextStyle: { color: '#6b7280', fontSize: 11 },
    },
    yAxis: {
      ...axisBase.value,
      type: 'value',
      name: '饱和次数',
      nameTextStyle: { color: '#6b7280', fontSize: 11 },
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
        name: '高饱和 (≥95%)',
        type: 'bar',
        data: [props.data.highSaturationCount],
        itemStyle: { color: highColor },
        barWidth: '40%',
      },
      {
        name: '低饱和 (≤5%)',
        type: 'bar',
        data: [props.data.lowSaturationCount],
        itemStyle: { color: lowColor },
        barWidth: '40%',
      },
    ],
    title: {
      left: 'center',
      top: 10,
      text: 'OP 饱和分析',
      subtext: `饱和率: ${(props.data.saturationRate * 100).toFixed(1)}%`,
      textStyle: { fontSize: 14, fontWeight: 600 },
      subtextStyle: { fontSize: 11 },
    },
  };

  return option;
});

watch(options, (newOptions) => {
  renderEcharts(newOptions);
}, { immediate: true });

onMounted(() => {
  renderEcharts(options.value);
});
</script>

<template>
  <div class="saturation-chart-container">
    <EchartsUI ref="chartRef" class="w-full h-full" />
  </div>
</template>

<style lang="scss" scoped>
.saturation-chart-container {
  width: 100%;
  height: 100%;
  min-height: 180px;
}
</style>