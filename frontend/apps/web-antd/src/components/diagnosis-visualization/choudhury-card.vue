<script lang="ts" setup>
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { DiagnosisApi } from '#/api/diagnosis';

import { computed, onMounted, ref, watch } from 'vue';

import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import { useEchartsPreset } from '#/composables/use-echarts-preset';

const props = defineProps<{
  data: DiagnosisApi.ChoudhuryData;
}>();

const { getSeriesColor, themeColors } = useEchartsPreset();

const chartRef = ref<EchartsUIType>();
const { renderEcharts } = useEcharts(chartRef);

const isSticky = computed(() => props.data.stictionIndex > 0.5);
const statusColor = computed(() =>
  isSticky.value ? themeColors.value.DANGER : getSeriesColor('ok'),
);

const stictionPercent = computed(() =>
  (props.data.stictionIndex * 100).toFixed(1),
);
const stictionBarValue = computed(() => props.data.stictionIndex * 100);

const options = computed(() => {
  const barColor = isSticky.value
    ? themeColors.value.DANGER
    : getSeriesColor('ok');

  const option: any = {
    xAxis: {
      type: 'category',
      data: [''],
      axisLabel: { show: false },
      axisLine: { show: false },
      axisTick: { show: false },
    },
    yAxis: {
      type: 'value',
      show: false,
      max: 100,
      min: 0,
    },
    grid: {
      top: 5,
      right: 5,
      bottom: 5,
      left: 5,
      containLabel: true,
    },
    series: [
      {
        type: 'bar',
        data: [stictionBarValue.value],
        itemStyle: {
          color: barColor,
          borderRadius: [0, 4, 4, 0],
        },
        barWidth: '60%',
      },
    ],
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
  <div class="choudhury-card">
    <div class="card-header">
      <div class="card-title">非线性检测</div>
      <div class="status-badge" :style="{ backgroundColor: statusColor }">
        {{ isSticky ? '阀门粘滞' : '正常' }}
      </div>
    </div>
    <div class="card-body">
      <div class="chart-wrapper">
        <EchartsUI ref="chartRef" class="w-full h-full" />
        <div class="chart-label">粘滞指数: {{ stictionPercent }}%</div>
      </div>
      <div class="metrics-grid">
        <div class="metric-item">
          <span class="metric-label">NGI (增益指数)</span>
          <span class="metric-value">{{ data.ngi.toFixed(3) }}</span>
        </div>
        <div class="metric-item">
          <span class="metric-label">NLI (滞后指数)</span>
          <span class="metric-value">{{ data.nli.toFixed(3) }}</span>
        </div>
        <div class="metric-item">
          <span class="metric-label">粘滞指数</span>
          <span class="metric-value" :style="{ color: statusColor }">{{
            data.stictionIndex.toFixed(3)
          }}</span>
        </div>
        <div class="metric-item">
          <span class="metric-label">判定阈值</span>
          <span class="metric-value">0.5</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style lang="scss" scoped>
.choudhury-card {
  display: flex;
  flex-direction: column;
  height: 100%;
  padding: 12px;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}

.card-title {
  font-size: 14px;
  font-weight: 600;
  color: hsl(var(--foreground));
}

.status-badge {
  padding: 2px 10px;
  font-size: 11px;
  font-weight: 500;
  color: hsl(0deg 0% 100%);
  border-radius: 4px;
}

.card-body {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 8px;
}

.chart-wrapper {
  position: relative;
  height: 40px;
  background: rgb(0 0 0 / 4%);
  border-radius: 4px;
}

.chart-label {
  position: absolute;
  top: 50%;
  right: 8px;
  font-size: 11px;
  font-weight: 600;
  color: hsl(var(--foreground));
  transform: translateY(-50%);
}

.metrics-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px;
}

.metric-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 6px;
  background: rgb(0 0 0 / 2%);
  border-radius: 4px;
}

.metric-label {
  font-size: 11px;
  color: hsl(var(--muted-foreground));
}

.metric-value {
  font-size: 12px;
  font-weight: 600;
  color: hsl(var(--foreground));
}
</style>
