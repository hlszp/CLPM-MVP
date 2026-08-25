<script setup lang="ts">
/**
 * 工作台 Tab1：系统总览（原型对齐版 · M2 G-总览填充）
 *
 * 布局（对齐原型 · 12 列网格 · 非等宽 · 2 行）：
 *   ┌──────────────────────────────────────────────────────────────┐
 *   │ KpiCards（6 横向 KPI 卡片 · 全宽）                              │
 *   ├──────────────────┬────────────────────┬───────────────────────┤
 *   │ 模块健康 (span4) │ 数据流与治理闭环   │ 装置风险 (span3)      │
 *   │                   │ (span5)            │                       │
 *   ├──────────────────┼────────────────────┼───────────────────────┤
 *   │ 综合评分趋势     │ 预警事件 (span4)  │ 处置待办·闭环质量     │
 *   │ (span5)           │                    │ (span3)               │
 *   └──────────────────┴────────────────────┴───────────────────────┘
 *
 * 数据流：
 * - A-01 getWorkbenchOverviewApi → windows/plants/units/pareto/roots/funnel
 * - A-10 getWorkbenchPluginsApi → modules（store.plugins 已预加载）
 * - 范围/窗口切换自动联动刷新（watch store.scopeParams）
 */
import type { WorkbenchApi } from '#/api/workbench';

import { computed, onMounted, ref, watch } from 'vue';

import { getWorkbenchOverviewApi } from '#/api/workbench';
import { useWorkbenchStore } from '#/store/workbench';

import DataFlowDiagram from '../components/DataFlowDiagram.vue';
import DeviceRiskList from '../components/DeviceRiskList.vue';
import EventTimeline from '../components/EventTimeline.vue';
import FunnelStats from '../components/FunnelStats.vue';
import KpiCards from '../components/KpiCards.vue';
import ModuleHealth from '../components/ModuleHealth.vue';
import ScoreTrendChart from '../components/ScoreTrendChart.vue';
import WorkbenchShell from '../components/WorkbenchShell.vue';

const store = useWorkbenchStore();

const overview = ref<null | WorkbenchApi.OverviewResult>(null);
const loading = ref(false);
const errorMsg = ref<null | string>(null);

const plugins = computed(() => store.plugins);
/** 当前选中窗口的 KPI 块（跟随 HeaderBar 时间胶囊联动） */
const currentWindow = computed(() => store.timeWindow);
const currentWindowBlock = computed(
  () => overview.value?.windows?.[currentWindow.value] ?? null,
);
const loopCount = computed(
  () => currentWindowBlock.value?.loop_count ?? 0,
);
const scoreTrend = computed(
  () => currentWindowBlock.value?.score_trend ?? [],
);

async function loadOverview() {
  loading.value = true;
  errorMsg.value = null;
  try {
    const res = await getWorkbenchOverviewApi(store.scopeParams);
    overview.value = res;
  } catch (error) {
    errorMsg.value = error instanceof Error ? error.message : '总览数据加载失败';
    overview.value = null;
  } finally {
    loading.value = false;
    store.markRefreshed();
  }
}

onMounted(() => {
  loadOverview();
});

watch(
  () => store.scopeParams,
  () => loadOverview(),
  { deep: true },
);
</script>

<template>
  <WorkbenchShell>
    <div class="flex h-full flex-col gap-2 p-2">
    <!-- 加载/错误提示 -->
    <div
      v-if="loading"
      class="flex-none rounded border border-blue-100 bg-blue-50 px-3 py-1 text-[11px] text-blue-600"
    >
      正在加载总览数据…
    </div>
    <div
      v-else-if="errorMsg"
      class="flex-none rounded border border-red-100 bg-red-50 px-3 py-1 text-[11px] text-red-600"
    >
      {{ errorMsg }}
      <button class="ml-2 underline" @click="loadOverview">重试</button>
    </div>

    <!-- Row 1: 6 KPI 卡片 -->
    <div class="flex-none">
      <KpiCards
        :windows="overview?.windows"
        :funnel="overview?.funnel"
        :plants="overview?.plants"
        :current-window="currentWindow"
      />
    </div>

    <!-- Row 2: 模块健康(4) + 数据流(5) + 装置风险(3) · 非等宽 12 列 -->
    <div class="grid min-h-0 flex-1 grid-cols-12 gap-2">
      <div class="col-span-4 min-h-0">
        <ModuleHealth :plugins="plugins" />
      </div>
      <div class="col-span-5 min-h-0">
        <DataFlowDiagram :plugins="plugins" :loop-count="loopCount" />
      </div>
      <div class="col-span-3 min-h-0">
        <DeviceRiskList :plants="overview?.plants" :total-loops="loopCount" />
      </div>
    </div>

    <!-- Row 3: 评分趋势(5) + 预警事件(4) + 处置漏斗(3) · 非等宽 12 列 -->
    <div class="grid min-h-0 flex-1 grid-cols-12 gap-2">
      <div class="col-span-5 min-h-0">
        <ScoreTrendChart :trend="scoreTrend" :flags="currentWindowBlock?.flags" />
      </div>
      <div class="col-span-4 min-h-0">
        <EventTimeline :roots="overview?.roots" />
      </div>
      <div class="col-span-3 min-h-0">
        <FunnelStats :funnel="overview?.funnel" />
      </div>
    </div>
    </div>
  </WorkbenchShell>
</template>
