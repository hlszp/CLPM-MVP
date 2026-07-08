<script lang="ts" setup>
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { DiagnosisApi } from '#/api/diagnosis';

import { computed, onMounted, ref, watch } from 'vue';

import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import { useEchartsPreset } from '#/composables/use-echarts-preset';

const props = defineProps<{
  data: DiagnosisApi.IaeAnalysisData;
}>();

const { getSeriesColor, themeColors } = useEchartsPreset();

const chartRef = ref<EchartsUIType>();
const { renderEcharts } = useEcharts(chartRef);

const isOscillating = computed(() => props.data.oscillationIndex > 0.5);
const statusColor = computed(() => (isOscillating.value ? themeColors.value.DANGER : getSeriesColor('ok')));

const similarityPercent = computed(() => (props.data.similarityRate * 100).toFixed(1));
const oscillationPercent = computed(() => (props.data.oscillationIndex * 100).toFixed(1));

const options = computed(() => {
  const bars = [
    { name: 'OP零交叉', value: props.data.opZeroCrossCount, color: '#60a5fa' },
    { name: 'PV零交叉', value: props.data.pvZeroCrossCount, color: '#f472b6' },
  ];
  
  const option: any = {
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
    },
    xAxis: {
      type: 'category',
      data: bars.map(b => b.name),
      axisLabel: { 
        show: true,
        fontSize: 10,
        color: '#6b7280',
      },
      axisLine: { show: false },
      axisTick: { show: false },
    },
    yAxis: {
      type: 'value',
      show: false,
      max: Math.max(...bars.map(b => b.value), 10),
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
        data: bars.map(b => ({
          value: b.value,
          itemStyle: { color: b.color, borderRadius: 4 },
        })),
        barWidth: '40%',
      },
    ],
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
  <div class="iae-card">
    <div class="card-header">
      <div class="card-title">IAE 零交叉分析</div>
      <div class="status-badge" :style="{ backgroundColor: statusColor, color: '#fff' }">
        {{ isOscillating ? '存在振荡' : '正常' }}
      </div>
    </div>
    <div class="card-body">
      <div class="chart-wrapper">
        <EchartsUI ref="chartRef" class="w-full h-full" />
      </div>
      <div class="metrics-grid">
        <div class="metric-item">
          <span class="metric-label">OP零交叉次数</span>
          <span class="metric-value">{{ data.opZeroCrossCount }}</span>
        </div>
        <div class="metric-item">
          <span class="metric-label">PV零交叉次数</span>
          <span class="metric-value">{{ data.pvZeroCrossCount }}</span>
        </div>
        <div class="metric-item">
          <span class="metric-label">相似率</span>
          <span class="metric-value">{{ similarityPercent }}%</span>
        </div>
        <div class="metric-item">
          <span class="metric-label">振荡指数</span>
          <span class="metric-value" :style="{ color: statusColor }">{{ oscillationPercent }}%</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style lang="scss" scoped>
.iae-card {
  display: flex;
  flex-direction: column;
  height: 100%;
  padding: 12px;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.card-title {
  font-size: 14px;
  font-weight: 600;
  color: #1f2937;
}

.status-badge {
  padding: 2px 10px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
}

.card-body {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.chart-wrapper {
  height: 50px;
  background: rgba(0, 0, 0, 0.04);
  border-radius: 4px;
}

.metrics-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px;
}

.metric-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 4px 6px;
  background: rgba(0, 0, 0, 0.02);
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
