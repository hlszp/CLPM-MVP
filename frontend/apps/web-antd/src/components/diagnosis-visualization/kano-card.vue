<script lang="ts" setup>
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { DiagnosisApi } from '#/api/diagnosis';

import { computed, onMounted, ref, watch } from 'vue';

import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import { useEchartsPreset } from '#/composables/use-echarts-preset';

const props = defineProps<{
  data: DiagnosisApi.KanoData;
}>();

const { getSeriesColor, themeColors } = useEchartsPreset();

const chartRef = ref<EchartsUIType>();
const { renderEcharts } = useEcharts(chartRef);

const isBias = computed(() => props.data.biasIndex > 0.5);
const statusColor = computed(() =>
  isBias.value ? themeColors.value.DANGER : getSeriesColor('ok'),
);

const biasPercent = computed(() => (props.data.biasIndex * 100).toFixed(1));

const options = computed(() => {
  const types = [
    { name: 'P', value: props.data.countP, color: '#60a5fa' },
    { name: 'N', value: props.data.countN, color: '#f472b6' },
    { name: 'Z', value: props.data.countZ, color: '#94a3b8' },
  ];

  const option: any = {
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
    },
    xAxis: {
      type: 'category',
      data: types.map((t) => t.name),
      axisLabel: {
        show: true,
        fontSize: 11,
        color: '#6b7280',
      },
      axisLine: { show: false },
      axisTick: { show: false },
    },
    yAxis: {
      type: 'value',
      show: false,
      max: Math.max(...types.map((t) => t.value), 5),
    },
    grid: {
      top: 5,
      right: 5,
      bottom: 20,
      left: 5,
      containLabel: true,
    },
    series: [
      {
        type: 'bar',
        data: types.map((t) => ({
          value: t.value,
          itemStyle: { color: t.color, borderRadius: 4 },
        })),
        barWidth: '50%',
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
  <div class="kano-card">
    <div class="card-header">
      <div class="card-title">Kano 统计分析</div>
      <div
        class="status-badge"
        :style="{ backgroundColor: statusColor, color: '#fff' }"
      >
        {{ isBias ? '存在偏差' : '正常' }}
      </div>
    </div>
    <div class="card-body">
      <div class="chart-wrapper">
        <EchartsUI ref="chartRef" class="w-full h-full" />
      </div>
      <div class="metrics-grid">
        <div class="metric-item">
          <span class="metric-label">正偏差次数 (P)</span>
          <span class="metric-value">{{ data.countP }}</span>
        </div>
        <div class="metric-item">
          <span class="metric-label">负偏差次数 (N)</span>
          <span class="metric-value">{{ data.countN }}</span>
        </div>
        <div class="metric-item">
          <span class="metric-label">零偏差次数 (Z)</span>
          <span class="metric-value">{{ data.countZ }}</span>
        </div>
        <div class="metric-item">
          <span class="metric-label">偏差指数</span>
          <span class="metric-value" :style="{ color: statusColor }"
            >{{ biasPercent }}%</span
          >
        </div>
      </div>
    </div>
  </div>
</template>

<style lang="scss" scoped>
.kano-card {
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
  color: #1f2937;
}

.status-badge {
  padding: 2px 10px;
  font-size: 11px;
  font-weight: 500;
  border-radius: 4px;
}

.card-body {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 8px;
}

.chart-wrapper {
  height: 50px;
  background: rgb(0 0 0 / 4%);
  border-radius: 4px;
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
  color: #6b7280;
}

.metric-value {
  font-size: 12px;
  font-weight: 600;
  color: #374151;
}
</style>
