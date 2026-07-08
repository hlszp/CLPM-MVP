<script lang="ts" setup>
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { DiagnosisApi } from '#/api/diagnosis';

import { computed, onMounted, ref, watch } from 'vue';

import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import { useEchartsPreset } from '#/composables/use-echarts-preset';

const props = defineProps<{
  data: DiagnosisApi.QualityTimelineData;
  disabled?: boolean;
}>();

const { getSeriesColor, axisBase } = useEchartsPreset();

const chartRef = ref<EchartsUIType>();
const { renderEcharts } = useEcharts(chartRef);

const options = computed(() => {
  const goodPoints = props.data.totalPoints - props.data.badPoints;

  const goodColor = getSeriesColor('ok');
  const badColor = getSeriesColor('error');

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
      data: ['数据质量'],
      nameTextStyle: { color: '#6b7280', fontSize: 11 },
    },
    yAxis: {
      ...axisBase.value,
      type: 'value',
      name: '点数',
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
        name: 'Good',
        type: 'bar',
        stack: 'total',
        data: [goodPoints],
        itemStyle: { color: goodColor },
        barWidth: '40%',
      },
      {
        name: 'Bad/Uncertain',
        type: 'bar',
        stack: 'total',
        data: [props.data.badPoints],
        itemStyle: { color: badColor },
      },
    ],
    title: {
      left: 'center',
      top: 10,
      text: 'PV 质量码统计',
      subtext: `坏点率: ${(props.data.badRate * 100).toFixed(1)}% | 总点数: ${props.data.totalPoints}`,
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
  <div class="quality-timeline-chart-container">
    <EchartsUI ref="chartRef" class="w-full h-full" />
  </div>
</template>

<style lang="scss" scoped>
.quality-timeline-chart-container {
  width: 100%;
  height: 100%;
  min-height: 180px;
}
</style>