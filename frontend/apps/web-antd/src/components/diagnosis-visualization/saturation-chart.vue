<script lang="ts" setup>
import type { DiagnosisApi } from '#/api/diagnosis';

import { computed } from 'vue';

const props = defineProps<{
  data: DiagnosisApi.SaturationAnalysisData;
  disabled?: boolean;
}>();

const saturationRatePercent = computed(() =>
  (props.data.saturationRate * 100).toFixed(1),
);
</script>

<template>
  <div class="saturation-card">
    <div class="card-header">
      <div class="card-title">OP 饱和分析</div>
    </div>
    <div class="card-body">
      <div class="metrics-grid">
        <div class="metric-item">
          <span class="metric-label">高饱和 (≥95%)</span>
          <span class="metric-value highlight-error"
            >{{ data.highSaturationCount }}次</span
          >
        </div>
        <div class="metric-item">
          <span class="metric-label">低饱和 (≤5%)</span>
          <span class="metric-value highlight-warning"
            >{{ data.lowSaturationCount }}次</span
          >
        </div>
        <div class="metric-item">
          <span class="metric-label">饱和率</span>
          <span class="metric-value">{{ saturationRatePercent }}%</span>
        </div>
        <div class="metric-item">
          <span class="metric-label">饱和总次数</span>
          <span class="metric-value"
            >{{ data.highSaturationCount + data.lowSaturationCount }}次</span
          >
        </div>
      </div>
    </div>
  </div>
</template>

<style lang="scss" scoped>
.saturation-card {
  display: flex;
  flex-direction: column;
  height: 100%;
  padding: 12px;
}

.card-header {
  margin-bottom: 8px;
}

.card-title {
  font-size: 14px;
  font-weight: 600;
  color: hsl(var(--foreground));
}

.card-body {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 8px;
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

  &.highlight-error {
    color: var(--status-error);
  }

  &.highlight-warning {
    color: var(--status-warning);
  }
}
</style>
