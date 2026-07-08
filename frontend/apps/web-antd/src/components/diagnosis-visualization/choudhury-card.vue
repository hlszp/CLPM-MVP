<script lang="ts" setup>
import type { DiagnosisApi } from '#/api/diagnosis';

import { computed } from 'vue';

import { useEchartsPreset } from '#/composables/use-echarts-preset';

const props = defineProps<{
  data: DiagnosisApi.ChoudhuryData;
}>();

const { getSeriesColor, themeColors } = useEchartsPreset();

const isSticky = computed(() => props.data.stictionIndex > 0.5);
const statusColor = computed(() => (isSticky.value ? themeColors.value.DANGER : getSeriesColor('ok')));
</script>

<template>
  <div class="choudhury-card">
    <div class="card-title">Choudhury 非线性检测</div>
    <div class="card-content">
      <div class="metric-row">
        <span class="metric-label">NGI (归一化增益指数)</span>
        <span class="metric-value">{{ data.ngi.toFixed(3) }}</span>
      </div>
      <div class="metric-row">
        <span class="metric-label">NLI (归一化滞后指数)</span>
        <span class="metric-value">{{ data.nli.toFixed(3) }}</span>
      </div>
      <div class="metric-row highlight">
        <span class="metric-label">粘滞指数</span>
        <span class="metric-value" :style="{ color: statusColor }">{{ data.stictionIndex.toFixed(3) }}</span>
      </div>
      <div class="status-badge" :style="{ backgroundColor: statusColor, color: '#fff' }">
        {{ isSticky ? '阀门粘滞' : '正常' }}
      </div>
    </div>
  </div>
</template>

<style lang="scss" scoped>
.choudhury-card {
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

  &.highlight {
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