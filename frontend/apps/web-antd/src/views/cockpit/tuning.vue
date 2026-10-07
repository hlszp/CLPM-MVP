<script lang="ts" setup>
/**
 * 驾驶舱 · 整定页签（2026-10-05 整合裁决 D1/D3；P4 自运维工作台迁移）
 *
 * 薄壳容器：内容组件为 wb-comps/tuning-tab.vue（原「参数整定」Tab，
 * 断言黄框/Δ 散点/适用性/根因分布/待整定清单/详情卡），页内联动全部
 * 改为舱内摘要弹窗，不跳管理后台；仿真作业引导经顶栏「管理后台」。
 * 数据流：A-04 getWorkbenchTuningApi；时间窗由驾驶舱顶栏驱动。
 */
import { computed, onUnmounted, watch } from 'vue';

import { useCockpitStore } from '#/store/cockpit';
import { useWorkbenchStore } from '#/store/workbench';

import CockpitHeader from './components/cockpit-header.vue';
import DrillModals from './wb-comps/drill-modals.vue';
import TuningTab from './wb-comps/tuning-tab.vue';
import { closeDrillModals } from './wb-comps/use-drill';

import './styles/theme.css';
import './wb-comps/wb-theme.css';

const cockpitStore = useCockpitStore();
const store = useWorkbenchStore();
const theme = computed(() => cockpitStore.theme);

// 驾驶舱顶栏时间窗 → workbench store（scopeParams 联动重载）
watch(
  () => cockpitStore.timeWindow,
  (w) => store.setWindow(w),
  { immediate: true },
);

onUnmounted(() => {
  closeDrillModals();
});
</script>

<template>
  <div class="cockpit-root" :data-theme="theme">
    <CockpitHeader />
    <TuningTab class="tuning-page" />
    <DrillModals />
  </div>
</template>

<style scoped>
.tuning-page {
  flex: 1;
  min-height: 0;
}
</style>
