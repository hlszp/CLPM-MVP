<script lang="ts" setup>
import type { DiagnosisApi } from '#/api/diagnosis';

import { computed } from 'vue';

import { useEchartsPreset } from '#/composables/use-echarts-preset';

const props = defineProps<{
  data: DiagnosisApi.KanoData;
}>();

const { getSeriesColor, themeColors } = useEchartsPreset();

const isSticky = computed(() => props.data.stictionRatio > 0.3);
const statusColor = computed(() => (isSticky.value ? themeColors.value.DANGER : getSeriesColor('ok')));

const stictionPercent = computed(() => (props.data.stictionRatio * 100).toFixed(1));
</script>

<template>
  <div class="kano-card">
    <div class="card-title">Kano 统计法</div>
    <div class="card-content">
      <div class="metric-row">
        <span class="metric-label">粘滞比</span>
        <span class="metric-value" :style="{ color: statusColor }">{{ stictionPercent }}%</span>
      </div>
      <div class="metric-row">
        <span class="metric-label">相关系数</span>
        <span class="metric-value">{{ data.correlation.toFixed(3) }}</span>
      </div>
      <div class="metric-row">
        <span class="metric-label">标准差比</span>
        <span class="metric-value">{{ data.stdRatio.toFixed(3) }}</span>
      </div>
      <div class="status-badge" :style="{ backgroundColor: statusColor, color: '#fff' }">
        {{ isSticky ? '阀门粘滞' : '正常' }}
      </div>
    </div>
  </div>
</template>

<style lang="scss" scoped>
.kano-card {
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