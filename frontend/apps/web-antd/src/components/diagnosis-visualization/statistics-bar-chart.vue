<script lang="ts" setup>
import type { EchartsUIType } from '@vben/plugins/echarts';

import { computed, onMounted, ref, watch } from 'vue';

import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import { useEchartsPreset } from '#/composables/use-echarts-preset';

/**
 * B5 信号统计与阀门诊断柱状图
 *
 * 两个子图（内部 Tab 切换）：
 * 1. 信号统计：PV/SP/OP 三组，每组含均值 + 标准差双柱
 * 2. 阀门诊断与振荡：水平柱状图，阀门线性度/非线性度/OP 行程范围/振荡振幅
 *
 * 所有字段可为 null，全 null 时显示"暂无数据"。
 */
const props = defineProps<{
  opMean: null | number;
  opStd: null | number;
  oscillationAmplitude: null | number;
  pvMean: null | number;
  pvStd: null | number;
  setpointCrossingCount: null | number;
  spMean: null | number;
  spStd: null | number;
  valveLinearity: null | number;
  valveNonlinearity: null | number;
  valveOpMax: null | number;
  valveOpMin: null | number;
}>();

const { getTooltipPreset, getSeriesColor, themeColors, chartColors, axisBase } =
  useEchartsPreset();

const chartRef = ref<EchartsUIType>();
const { renderEcharts } = useEcharts(chartRef);

const activeTab = ref<'signal' | 'valve'>('signal');

/** OP 行程范围（opMax - opMin） */
const opRange = computed(() => {
  if (
    props.valveOpMax === null ||
    props.valveOpMin === null ||
    props.valveOpMax === undefined ||
    props.valveOpMin === undefined
  ) {
    return null;
  }
  return props.valveOpMax - props.valveOpMin;
});

const signalHasData = computed(
  () =>
    props.pvMean !== null ||
    props.pvStd !== null ||
    props.spMean !== null ||
    props.spStd !== null ||
    props.opMean !== null ||
    props.opStd !== null,
);

const valveHasData = computed(
  () =>
    props.valveLinearity !== null ||
    props.valveNonlinearity !== null ||
    opRange.value !== null ||
    props.oscillationAmplitude !== null,
);

function fmtVal(v: null | number | undefined): string {
  if (v === null || v === undefined) return '—';
  return v.toFixed(4);
}

const signalOptions = computed(() => {
  if (!signalHasData.value) {
    return {
      title: {
        left: 'center',
        top: 10,
        text: '暂无信号统计数据',
        textStyle: {
          fontSize: 14,
          fontWeight: 600,
          color: chartColors.value.textStrong,
        },
      },
    };
  }

  const meanColor = getSeriesColor('info');
  const stdColor = themeColors.value.WARNING;

  const option: any = {
    animation: false,
    tooltip: {
      ...getTooltipPreset(),
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (params: any) => {
        const cat = params[0]?.axisValue ?? '';
        const lines = [cat];
        for (const p of params) {
          const v = p.value;
          lines.push(
            `${p.marker} ${p.seriesName}: ${v === null || v === undefined || v === '-' ? '暂无数据' : Number(v).toFixed(4)}`,
          );
        }
        return lines.join('<br/>');
      },
    },
    legend: {
      data: ['均值', '标准差'],
      top: 30,
      right: 16,
      textStyle: {
        color: chartColors.value.text,
        fontSize: 11,
      },
      icon: 'roundRect',
      itemWidth: 12,
      itemHeight: 8,
    },
    xAxis: {
      ...axisBase.value,
      type: 'category',
      data: ['PV', 'SP', 'OP'],
      name: '信号',
      nameTextStyle: { color: chartColors.value.text, fontSize: 11 },
    },
    yAxis: {
      ...axisBase.value,
      type: 'value',
    },
    grid: {
      top: 60,
      right: 16,
      bottom: 40,
      left: 56,
      containLabel: true,
    },
    series: [
      {
        name: '均值',
        type: 'bar',
        data: [props.pvMean, props.spMean, props.opMean],
        barGap: '10%',
        barCategoryGap: '40%',
        itemStyle: {
          color: meanColor,
          borderRadius: [2, 2, 0, 0],
          shadowBlur: 0,
        },
        emphasis: {
          itemStyle: { color: meanColor, opacity: 0.85 },
        },
      },
      {
        name: '标准差',
        type: 'bar',
        data: [props.pvStd, props.spStd, props.opStd],
        itemStyle: {
          color: stdColor,
          opacity: 0.6,
          borderRadius: [2, 2, 0, 0],
          shadowBlur: 0,
        },
        emphasis: {
          itemStyle: { color: stdColor, opacity: 0.8 },
        },
      },
    ],
    title: {
      left: 'center',
      top: 8,
      text: '信号统计（均值 + 标准差）',
      textStyle: {
        fontSize: 14,
        fontWeight: 600,
        color: chartColors.value.textStrong,
      },
    },
  };

  return option;
});

const valveOptions = computed(() => {
  if (!valveHasData.value) {
    return {
      title: {
        left: 'center',
        top: 10,
        text: '暂无阀门诊断数据',
        textStyle: {
          fontSize: 14,
          fontWeight: 600,
          color: chartColors.value.textStrong,
        },
      },
    };
  }

  // 颜色映射：线性度=绿，非线性度=橙，OP 行程=蓝，振荡振幅=红（危险语义）
  const linearityColor = themeColors.value.SUCCESS;
  const nonlinearityColor = themeColors.value.WARNING;
  const opRangeColor = themeColors.value.INFO;
  // themeColors 无紫色，振荡为异常语义，用 DANGER 保持四色区分
  const amplitudeColor = themeColors.value.DANGER;

  // yAxis category data[0] 在底部，按"振幅→行程→非线性→线性"自下而上排列
  const categoryData = [
    '振荡振幅',
    'OP 行程范围',
    '阀门非线性度',
    '阀门线性度',
  ];

  const data = [
    { value: props.oscillationAmplitude, itemStyle: { color: amplitudeColor } },
    { value: opRange.value, itemStyle: { color: opRangeColor } },
    { value: props.valveNonlinearity, itemStyle: { color: nonlinearityColor } },
    { value: props.valveLinearity, itemStyle: { color: linearityColor } },
  ];

  const crossingText =
    props.setpointCrossingCount !== null &&
    props.setpointCrossingCount !== undefined
      ? `设定值穿越次数: ${props.setpointCrossingCount}`
      : '设定值穿越次数: —';

  const option: any = {
    animation: false,
    tooltip: {
      ...getTooltipPreset(),
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (params: any) => {
        const p = Array.isArray(params) ? params[0] : params;
        const v = p?.value;
        return `${p?.axisValue}<br/>${p?.marker} ${fmtVal(v)}`;
      },
    },
    xAxis: {
      ...axisBase.value,
      type: 'value',
    },
    yAxis: {
      ...axisBase.value,
      type: 'category',
      data: categoryData,
      inverse: false,
    },
    grid: {
      top: 60,
      right: 24,
      bottom: 40,
      left: 100,
      containLabel: true,
    },
    series: [
      {
        type: 'bar',
        data,
        barWidth: '55%',
        itemStyle: {
          borderRadius: [0, 2, 2, 0],
          shadowBlur: 0,
        },
        label: {
          show: true,
          position: 'right',
          formatter: (p: any) => fmtVal(p.value),
          fontSize: 11,
          color: chartColors.value.text,
          fontFamily: 'var(--font-mono)',
        },
      },
    ],
    title: {
      left: 'center',
      top: 8,
      text: '阀门诊断与振荡',
      subtext: crossingText,
      textStyle: {
        fontSize: 14,
        fontWeight: 600,
        color: chartColors.value.textStrong,
      },
      subtextStyle: {
        fontSize: 11,
        color: chartColors.value.text,
      },
    },
  };

  return option;
});

const options = computed(() =>
  activeTab.value === 'signal' ? signalOptions.value : valveOptions.value,
);

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
  <div class="statistics-bar-chart-container">
    <div class="chart-tabs">
      <button
        class="chart-tab"
        :class="{ active: activeTab === 'signal' }"
        type="button"
        @click="activeTab = 'signal'"
      >
        信号统计
      </button>
      <button
        class="chart-tab"
        :class="{ active: activeTab === 'valve' }"
        type="button"
        @click="activeTab = 'valve'"
      >
        阀门诊断
      </button>
    </div>
    <div class="chart-body">
      <EchartsUI ref="chartRef" class="w-full h-full" />
    </div>
  </div>
</template>

<style lang="scss" scoped>
.statistics-bar-chart-container {
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100%;
  min-height: 200px;
}

.chart-tabs {
  display: flex;
  gap: 4px;
  padding: 8px 12px 0;
}

.chart-tab {
  padding: 3px 10px;
  font-size: 12px;
  color: hsl(var(--muted-foreground));
  cursor: pointer;
  background: transparent;
  border: 1px solid hsl(var(--border));
  border-radius: 4px;
  transition: none;

  &.active {
    color: hsl(0deg 0% 100%);
    background-color: var(--status-info);
    border-color: var(--status-info);
  }
}

.chart-body {
  flex: 1;
  min-height: 0;
}
</style>
