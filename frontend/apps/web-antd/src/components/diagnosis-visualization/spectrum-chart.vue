<script lang="ts" setup>
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { DiagnosisApi } from '#/api/diagnosis';

import { computed, onMounted, ref, watch } from 'vue';

import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import { useEchartsPreset } from '#/composables/use-echarts-preset';

const props = defineProps<{
  data: DiagnosisApi.SpectrumData;
  disabled?: boolean;
}>();

const { getTooltipPreset, getSeriesColor, themeColors, chartColors, axisBase } =
  useEchartsPreset();

const chartRef = ref<EchartsUIType>();
const { renderEcharts } = useEcharts(chartRef);

const hasData = computed(() => props.data.frequencies.length > 0);

const options = computed(() => {
  if (!hasData.value) {
    return {
      title: { left: 'center', text: '暂无频谱数据' },
    };
  }

  const data = props.data.frequencies.map((freq, i) => ({
    value: [freq, props.data.amplitudes[i] ?? 0],
  }));

  const peakColor = themeColors.value.DANGER;
  const normalColor = getSeriesColor('info');

  const option: any = {
    tooltip: {
      ...getTooltipPreset(),
      formatter: (params: any) => {
        const x = params.value?.[0] ?? 0;
        const y = params.value?.[1] ?? 0;
        return `频率: ${x.toFixed(4)} Hz<br/>振幅: ${y.toFixed(4)}`;
      },
    },
    xAxis: {
      ...axisBase.value,
      type: 'value',
      name: '频率 (Hz)',
      nameTextStyle: { color: chartColors.value.text, fontSize: 11 },
    },
    yAxis: {
      ...axisBase.value,
      type: 'value',
      name: '振幅',
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
        type: 'bar',
        data,
        barWidth: '80%',
        itemStyle: {
          color: normalColor,
          borderRadius: [2, 2, 0, 0],
        },
        emphasis: {
          itemStyle: {
            color: peakColor,
          },
        },
      },
      {
        type: 'scatter',
        data: [
          {
            value: [props.data.peakFrequency, props.data.peakAmplitude],
            symbolSize: 12,
            itemStyle: {
              color: peakColor,
              borderColor: chartColors.value.border,
              borderWidth: 2,
            },
          },
        ],
        label: {
          show: true,
          position: 'top',
          formatter: `主频: ${props.data.peakFrequency.toFixed(4)} Hz`,
          fontSize: 11,
          color: peakColor,
        },
      },
    ],
    title: {
      left: 'center',
      top: 10,
      text: 'FFT 频谱图',
      subtext: `振荡指数: ${props.data.oscillationIndex.toFixed(3)}`,
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
  <div class="spectrum-chart-container">
    <EchartsUI ref="chartRef" class="w-full h-full" />
  </div>
</template>

<style lang="scss" scoped>
.spectrum-chart-container {
  width: 100%;
  height: 100%;
  min-height: 200px;
}
</style>
