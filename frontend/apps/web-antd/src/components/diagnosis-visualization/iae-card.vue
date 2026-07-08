<script lang="ts" setup>
import type { DiagnosisApi } from '#/api/diagnosis';

import { computed } from 'vue';

import { useEchartsPreset } from '#/composables/use-echarts-preset';

const props = defineProps<{
  data: DiagnosisApi.IaeAnalysisData;
}>();

const { getSeriesColor, themeColors } = useEchartsPreset();

const isOscillating = computed(() => props.data.similarity > 0.7);
const statusColor = computed(() => (isOscillating.value ? themeColors.value.DANGER : getSeriesColor('ok')));

const similarityPercent = computed(() => (props.data.similarity * 100).toFixed(1));
</script>

<template>
  <div class="iae-card">
    <div class="card-title">IAE 零交叉分析</div>
    <div class="card-content">
      <div class="metric-row">
        <span class="metric-label">相似率</span>
        <span class="metric-value" :style="{ color: statusColor }">{{ similarityPercent }}%</span>
      </div>
      <div class="metric-row">
        <span class="metric-label">零交叉次数</span>
        <span class="metric-value">{{ data.zeroCrossingCount }}</span>
      </div>
      <div class="metric-row">
        <span class="metric-label">平均周期</span>
        <span class="metric-value">{{ data.meanPeriod.toFixed(2) }} s</span>
      </div>
      <div class="status-badge" :style="{ backgroundColor: statusColor, color: '#fff' }">
        {{ isOscillating ? '振荡检测' : '正常' }}
      </div>
    </div>
  </div>
</template>

<style lang="scss" scoped>
.iae-card {
  border-radius: 8px;
  background: #fff;
  padding: 16px;
  height: 100%;
}

.card-title {
  font-size: 14px;
  font-weight: 600;
  margin-bottom: 12px;
}

.card-content {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.metric-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 4px 0;

  &:first-child {
    padding: 8px;
    background: rgba(0, 0, 0, 0.04);
    border-radius: 4px;
  }
}

.metric-label {
  font-size: 12px;
  color: #6b7280;
}

.metric-value {
  font-size: 14px;
  font-weight: 600;
  color: #1f2937;
}

.status-badge {
  margin-top: 8px;
  padding: 4px 12px;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 500;
  align-self: flex-start;
}
</style>