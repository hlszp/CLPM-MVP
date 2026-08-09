<script lang="ts" setup>
import type { DiagnosisApi } from '#/api/diagnosis';

import { computed } from 'vue';

const props = defineProps<{
  data: DiagnosisApi.QualityTimelineData;
  disabled?: boolean;
}>();

const badRatePercent = computed(() => (props.data.badRate * 100).toFixed(1));
const goodRatePercent = computed(() =>
  ((1 - props.data.badRate) * 100).toFixed(1),
);
</script>

<template>
  <div class="quality-timeline-card">
    <div class="card-header">
      <div class="card-title">PV 质量码统计</div>
    </div>
    <div class="card-body">
      <div class="metrics-grid">
        <div class="metric-item">
          <span class="metric-label">坏点率</span>
          <span class="metric-value highlight-error"
            >{{ badRatePercent }}%</span
          >
        </div>
        <div class="metric-item">
          <span class="metric-label">好点率</span>
          <span class="metric-value highlight-success"
            >{{ goodRatePercent }}%</span
          >
        </div>
        <div class="metric-item">
          <span class="metric-label">总点数</span>
          <span class="metric-value">{{ data.totalPoints }}</span>
        </div>
        <div class="metric-item">
          <span class="metric-label">坏点数</span>
          <span class="metric-value">{{ data.badPoints }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style lang="scss" scoped>
.quality-timeline-card {
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

  &.highlight-success {
    color: var(--status-ok);
  }
}
</style>
