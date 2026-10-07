<script lang="ts" setup>
/**
 * 驾驶舱 · 处置页签（2026-10-05 整合裁决 D1/D3；P5 自运维工作台迁移）
 *
 * 薄壳容器：内容组件为 wb-comps/handling-tab.vue（原「问题处置」Tab，
 * 断言黄框/SLA 侧栏/四泳道看板/人员负载/重开列表/任务详情），页内联动
 * 全部改为舱内摘要弹窗（工单清单/SLA 摘要），工单详情保留页内抽屉，
 * 不跳管理后台。数据流：orders×5 + statistics + loops；时间窗由驾驶舱顶栏驱动。
 */
import { computed, onUnmounted, watch } from 'vue';

import { useCockpitStore } from '#/store/cockpit';
import { useWorkbenchStore } from '#/store/workbench';

import CockpitHeader from './components/cockpit-header.vue';
import DrillModals from './wb-comps/drill-modals.vue';
import HandlingTab from './wb-comps/handling-tab.vue';
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
    <HandlingTab class="handling-page" />
    <DrillModals />
  </div>
</template>

<style scoped>
.handling-page {
  flex: 1;
  min-height: 0;
}
</style>
