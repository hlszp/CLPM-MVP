<script lang="ts" setup>
/**
 * 驾驶舱下钻弹窗 E3：绩效趋势摘要（2026-10-05 整合裁决 D3）
 *
 * 原运维工作台 ScoreTrendChart / EvalTrendChart 整图点击下钻
 * /metric/history 的替代：舱内弹窗呈现全厂绩效趋势（复用总览
 * TrendPanel 组件，含时间窗联动与降级口径说明，不造数）。
 */
import CockpitModal from '../components/modals/cockpit-modal.vue';
import TrendPanel from '../components/trend-panel.vue';

defineProps<{ open: boolean; title?: string }>();
defineEmits<{ close: [] }>();
</script>

<template>
  <CockpitModal :open="open" :title="title ?? '绩效趋势'" :width="980" @close="$emit('close')">
    <div class="dt-modal-body">
      <TrendPanel />
    </div>
  </CockpitModal>
</template>

<style scoped>
.dt-modal-body {
  height: 380px;
}
</style>
