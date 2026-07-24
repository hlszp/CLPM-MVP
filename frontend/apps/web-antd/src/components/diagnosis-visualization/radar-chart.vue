<script lang="ts" setup>
import type { EchartsUIType } from '@vben/plugins/echarts';

import { computed, onMounted, ref, watch } from 'vue';

import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import { useEchartsPreset } from '#/composables/use-echarts-preset';

/**
 * B3 核心指标雷达图
 *
 * 三维雷达：准确率 A / 快速率 F / 稳定率 S（0~100）。
 * 双层叠加：当前值（实色填充）vs 目标值（虚线，默认 90）。
 */
const props = withDefaults(
  defineProps<{
    accuracy: null | number;
    fast: null | number;
    stability: null | number;
    targetAccuracy?: null | number;
    targetFast?: null | number;
    targetStability?: null | number;
  }>(),
  {
    targetAccuracy: 90,
    targetFast: 90,
    targetStability: 90,
  },
);

const { getTooltipPreset, getSeriesColor, themeColors, chartColors } =
  useEchartsPreset();

const chartRef = ref<EchartsUIType>();
const { renderEcharts } = useEcharts(chartRef);

const hasData = computed(
  () =>
    props.accuracy !== null || props.fast !== null || props.stability !== null,
);

/** 安全归一化到 0~100 */
function safeVal(v: null | number | undefined): number {
  if (v === null || v === undefined) return 0;
  return Math.max(0, Math.min(100, v));
}

const options = computed(() => {
  if (!hasData.value) {
    return {
      title: {
        left: 'center',
        top: 10,
        text: '暂无雷达数据',
        textStyle: {
          fontSize: 14,
          fontWeight: 600,
          color: chartColors.value.textStrong,
        },
      },
    };
  }

  const currentColor = getSeriesColor('ok');
  const targetColor = themeColors.value.WARNING;

  const option: any = {
    animation: false,
    tooltip: {
      ...getTooltipPreset(),
      trigger: 'item',
    },
    legend: {
      data: ['当前值', '目标值'],
      bottom: 4,
      textStyle: {
        color: chartColors.value.text,
        fontSize: 11,
      },
      icon: 'roundRect',
      itemWidth: 12,
      itemHeight: 8,
    },
    radar: {
      indicator: [
        { name: '准确率 A', max: 100 },
        { name: '快速率 F', max: 100 },
        { name: '稳定率 S', max: 100 },
      ],
      radius: '60%',
      center: ['50%', '50%'],
      axisName: {
        color: chartColors.value.textStrong,
        fontSize: 12,
      },
      splitLine: {
        lineStyle: {
          color: chartColors.value.splitLine,
        },
      },
      splitArea: {
        areaStyle: {
          color: ['transparent', chartColors.value.mutedFill],
        },
      },
      axisLine: {
        lineStyle: {
          color: chartColors.value.splitLine,
        },
      },
    },
    series: [
      {
        name: '当前 vs 目标',
        type: 'radar',
        emphasis: { focus: 'series' },
        data: [
          {
            value: [
              safeVal(props.accuracy),
              safeVal(props.fast),
              safeVal(props.stability),
            ],
            name: '当前值',
            areaStyle: { color: currentColor, opacity: 0.25 },
            lineStyle: { width: 2, color: currentColor },
            itemStyle: { color: currentColor },
          },
          {
            value: [
              safeVal(props.targetAccuracy),
              safeVal(props.targetFast),
              safeVal(props.targetStability),
            ],
            name: '目标值',
            areaStyle: { opacity: 0 },
            lineStyle: { width: 1.5, color: targetColor, type: 'dashed' },
            itemStyle: { color: targetColor },
          },
        ],
      },
    ],
    title: {
      left: 'center',
      top: 8,
      text: '核心指标雷达图',
      textStyle: {
        fontSize: 14,
        fontWeight: 600,
        color: chartColors.value.textStrong,
      },
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
  <div class="radar-chart-container">
    <EchartsUI ref="chartRef" class="w-full h-full" />
  </div>
</template>

<style lang="scss" scoped>
.radar-chart-container {
  width: 100%;
  height: 100%;
  min-height: 200px;
}
</style>
