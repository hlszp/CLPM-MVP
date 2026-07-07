<script lang="ts" setup>
import { computed, ref, watch, onMounted } from 'vue';
import type { EchartsUIType } from '@vben/plugins/echarts';
import { EchartsUI, useEcharts } from '@vben/plugins/echarts';
import { useClpmTheme } from '#/composables/use-clpm-theme';

const props = defineProps<{
  title: string;
  value: number | null;
  compareValue?: number | null;
  unit?: string;
  max?: number;
  min?: number;
  color?: string;
}>();

const { themeColors, chartColors } = useClpmTheme();

const chartRef = ref<EchartsUIType>();
const { renderEcharts } = useEcharts(chartRef);

const displayValue = computed(() => {
  if (props.value === null || props.value === undefined) return '--';
  return Number(props.value).toFixed(1);
});

const compareDelta = computed(() => {
  if (props.value === null || props.value === undefined || props.compareValue === null || props.compareValue === undefined) return null;
  return Number(props.value) - Number(props.compareValue);
});

const compareText = computed(() => {
  if (compareDelta.value === null) return '';
  const sign = compareDelta.value >= 0 ? '+' : '';
  return `${sign}${Number(compareDelta.value).toFixed(1)}`;
});

const isPositive = computed(() => {
  if (compareDelta.value === null) return null;
  return compareDelta.value > 0;
});

const gaugeColor = computed(() => {
  if (props.color) return props.color;
  if (props.value === null || props.value === undefined) return themeColors.value.NEUTRAL;
  const percent = ((props.value - (props.min || 0)) / ((props.max || 100) - (props.min || 0))) * 100;
  if (percent >= 80) return themeColors.value.SUCCESS;
  if (percent >= 60) return themeColors.value.WARNING;
  return themeColors.value.DANGER;
});

function renderChart() {
  const val = props.value;
  if (val === null || val === undefined) return;

  const maxVal = props.max ?? 100;
  const minVal = props.min ?? 0;
  const percent = ((val - minVal) / (maxVal - minVal)) * 100;

  let color = themeColors.value.SUCCESS as string;
  if (percent >= 80) color = themeColors.value.SUCCESS as string;
  else if (percent >= 60) color = themeColors.value.WARNING as string;
  else color = themeColors.value.DANGER as string;

  renderEcharts({
    backgroundColor: 'transparent',
    series: [
      {
        type: 'gauge',
        startAngle: 200,
        endAngle: -20,
        min: minVal,
        max: maxVal,
        splitNumber: 5,
        radius: '95%',
        center: ['50%', '65%'],
        axisLine: {
          lineStyle: {
            width: 8,
            color: [
              [0.3, '#fef2f2'],
              [0.5, '#fffbeb'],
              [0.75, '#ecfdf5'],
              [1, '#f3f4f6'],
            ],
          },
        },
        pointer: {
          show: true,
          length: '55%',
          width: 3,
          itemStyle: {
            color: color,
            borderRadius: 2,
          },
        },
        axisTick: {
          show: false,
        },
        splitLine: {
          show: true,
          length: 6,
          lineStyle: {
            color: '#e5e7eb',
            width: 1,
          },
        },
        axisLabel: {
          show: true,
          distance: 16,
          fontSize: 10,
          color: chartColors.value.text,
          formatter: (v: number) => `${Math.round(v)}`,
        },
        detail: {
          show: false,
        },
        data: [{ value: val, name: '' }],
      },
      {
        type: 'gauge',
        startAngle: 200,
        endAngle: -20,
        min: minVal,
        max: maxVal,
        splitNumber: 5,
        radius: '95%',
        center: ['50%', '65%'],
        axisLine: {
          lineStyle: {
            width: 8,
            color: [[percent / 100, color], [percent / 100, 'transparent']],
          },
        },
        pointer: {
          show: false,
        },
        axisTick: {
          show: false,
        },
        splitLine: {
          show: false,
        },
        axisLabel: {
          show: false,
        },
        detail: {
          show: false,
        },
        data: [{ value: val, name: '' }],
      },
    ],
  });
}

watch(
  () => props.value,
  () => {
    renderChart();
  },
);

onMounted(() => {
  renderChart();
});
</script>

<template>
  <div class="clpm-kpi-gauge">
    <div class="clpm-kpi-gauge__header">
      <span class="clpm-kpi-gauge__title">{{ title }}</span>
      <span
        v-if="compareText"
        class="clpm-kpi-gauge__compare"
        :class="{
          'clpm-kpi-gauge__compare--positive': isPositive === true,
          'clpm-kpi-gauge__compare--negative': isPositive === false,
        }"
      >
        <svg v-if="isPositive === true" width="10" height="10" viewBox="0 0 10 10" fill="none">
          <path d="M5 2L9 6L5 10L1 6L5 2Z" fill="currentColor"/>
        </svg>
        <svg v-else-if="isPositive === false" width="10" height="10" viewBox="0 0 10 10" fill="none">
          <path d="M5 8L1 4L5 0L9 4L5 8Z" fill="currentColor"/>
        </svg>
        {{ compareText }}
      </span>
    </div>
    <div class="clpm-kpi-gauge__chart">
      <EchartsUI ref="chartRef" height="90px" />
    </div>
    <div class="clpm-kpi-gauge__footer">
      <span class="clpm-kpi-gauge__value" :style="{ color: gaugeColor }">
        {{ displayValue }}
      </span>
      <span v-if="unit" class="clpm-kpi-gauge__unit">{{ unit }}</span>
    </div>
  </div>
</template>

<style scoped>
.clpm-kpi-gauge {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 12px 8px;
  height: 100%;
  justify-content: space-between;
}

.clpm-kpi-gauge__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  padding: 0 4px;
}

.clpm-kpi-gauge__title {
  font-size: 13px;
  font-weight: 500;
  color: hsl(var(--muted-foreground));
}

.clpm-kpi-gauge__compare {
  display: flex;
  align-items: center;
  gap: 2px;
  font-size: 11px;
  padding: 2px 6px;
  border-radius: 4px;
  background: hsl(var(--muted));
}

.clpm-kpi-gauge__compare--positive {
  color: hsl(var(--success));
}

.clpm-kpi-gauge__compare--negative {
  color: hsl(var(--danger));
}

.clpm-kpi-gauge__chart {
  width: 100%;
  flex: 1;
  display: flex;
  align-items: center;
}

.clpm-kpi-gauge__footer {
  display: flex;
  align-items: baseline;
  gap: 4px;
}

.clpm-kpi-gauge__value {
  font-size: 24px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.clpm-kpi-gauge__unit {
  font-size: 12px;
  color: hsl(var(--muted-foreground));
  font-weight: 400;
}
</style>