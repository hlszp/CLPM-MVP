<script lang="ts" setup>
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { DiagnosisApi } from '#/api/diagnosis';

import { computed, onMounted, ref, watch } from 'vue';

import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import { useEchartsPreset } from '#/composables/use-echarts-preset';

const props = defineProps<{
  data: DiagnosisApi.CusumAnalysisData;
  disabled?: boolean;
}>();

const { getTooltipPreset, getSeriesColor, axisBase } = useEchartsPreset();

const chartRef = ref<EchartsUIType>();
const { renderEcharts } = useEcharts(chartRef);

const hasData = computed(() => props.data.cusumPos.length > 0);

const options = computed(() => {
  if (!hasData.value) {
    return {
      title: { left: 'center', text: '暂无 CUSUM 数据' },
    };
  }

  const posColor = getSeriesColor('error');
  const negColor = getSeriesColor('warning');
  const thresholdColor = '#9ca3af';

  const cusumPosData = props.data.cusumPos.map((val, i) => ({
    value: [i, val],
  }));

  const cusumNegData = props.data.cusumNeg.map((val, i) => ({
    value: [i, val],
  }));

  const thresholdLine = Array(props.data.cusumPos.length)
    .fill(0)
    .map((_, i) => ({ value: [i, props.data.threshold] }));

  const negThresholdLine = Array(props.data.cusumPos.length)
    .fill(0)
    .map((_, i) => ({ value: [i, -props.data.threshold] }));

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
      nameTextStyle: { color: '#6b7280', fontSize: 11 },
    },
    yAxis: {
      ...axisBase.value,
      type: 'value',
      name: '累积和',
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
        name: '正累积和',
        type: 'line',
        data: cusumPosData,
        smooth: false,
        symbol: 'circle',
        symbolSize: 3,
        showSymbol: false,
        lineStyle: { width: 2, color: posColor },
        itemStyle: { color: posColor },
      },
      {
        name: '负累积和',
        type: 'line',
        data: cusumNegData,
        smooth: false,
        symbol: 'circle',
        symbolSize: 3,
        showSymbol: false,
        lineStyle: { width: 2, color: negColor },
        itemStyle: { color: negColor },
      },
      {
        name: '阈值上限',
        type: 'line',
        data: thresholdLine,
        smooth: false,
        symbol: 'none',
        lineStyle: { width: 1, color: thresholdColor, type: 'dashed' },
      },
      {
        name: '阈值下限',
        type: 'line',
        data: negThresholdLine,
        smooth: false,
        symbol: 'none',
        lineStyle: { width: 1, color: thresholdColor, type: 'dashed' },
      },
    ],
    title: {
      left: 'center',
      top: 10,
      text: 'CUSUM 累积和曲线',
      subtext: `突变次数: ${props.data.shiftCount} | 最大累积和: ${props.data.maxCusum.toFixed(4)}`,
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
  <div class="cusum-chart-container">
    <EchartsUI ref="chartRef" class="w-full h-full" />
  </div>
</template>

<style lang="scss" scoped>
.cusum-chart-container {
  width: 100%;
  height: 100%;
  min-height: 200px;
}
</style>