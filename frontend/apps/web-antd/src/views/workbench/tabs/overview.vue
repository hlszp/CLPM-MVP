<script setup lang="ts">
/**
 * 工作台 Tab1：系统总览（原型对齐版 · M2 G-总览填充）
 *
 * 布局（对齐原型 · 12 列网格 · 非等宽 · 2 行）：
 *   ┌──────────────────────────────────────────────────────────────┐
 *   │ KpiCards（6 横向 KPI 卡片 · 全宽）                              │
 *   ├──────────────────┬────────────────────┬───────────────────────┤
 *   │ 单元平稳率(span4)│ 数据流与治理闭环   │ 装置风险 (span3)      │
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
import type { HandlingApi } from '#/api/handling';
import type { WorkbenchApi } from '#/api/workbench';

import { computed, onMounted, ref, watch } from 'vue';

import { getWorkbenchOverviewApi } from '#/api/workbench';
import { useWorkbenchStore } from '#/store/workbench';

import DataFlowDiagram from '../components/DataFlowDiagram.vue';
import DeviceRiskList from '../components/DeviceRiskList.vue';
import EventTimeline from '../components/EventTimeline.vue';
import FunnelStats from '../components/FunnelStats.vue';
import KpiCards from '../components/KpiCards.vue';
import ScoreTrendChart from '../components/ScoreTrendChart.vue';
import SteadyRateBars from '../components/SteadyRateBars.vue';

const store = useWorkbenchStore();

// ============ 全局健康带（B5）：5 模块状态色 + 在线/维护计数 ============
// 状态色映射与 ModuleStatusDot 对齐：绿运行 / 橙维护 / 灰未安装
const STATUS_COLOR: Record<WorkbenchApi.ModuleStatus, string> = {
  CORE: '#52C41A',
  ENABLED: '#52C41A',
  MAINTENANCE: '#FA8C16',
  UNINSTALLED: '#BFBFBF',
};
const STATUS_LABEL: Record<WorkbenchApi.ModuleStatus, string> = {
  CORE: '内置',
  ENABLED: '在线',
  MAINTENANCE: '维护中',
  UNINSTALLED: '未安装',
};
// 5 模块顺序与 index.vue TABS.moduleKey 对齐
const HEALTH_MODULES = [
  { key: 'monitor', name: '监控' },
  { key: 'assess', name: '评估' },
  { key: 'diagnosis', name: '诊断' },
  { key: 'tuning', name: '整定' },
  { key: 'handling', name: '处置' },
] as const;
const healthItems = computed(() =>
  HEALTH_MODULES.map((m) => {
    const p = store.plugins.find((x) => x.module_key === m.key);
    return { ...m, status: (p?.status ?? 'UNINSTALLED') as WorkbenchApi.ModuleStatus };
  }),
);
const onlineCount = computed(
  () =>
    healthItems.value.filter((h) => h.status === 'CORE' || h.status === 'ENABLED').length,
);
const maintenanceCount = computed(
  () => healthItems.value.filter((h) => h.status === 'MAINTENANCE').length,
);
function statusColor(s: WorkbenchApi.ModuleStatus) {
  return STATUS_COLOR[s] ?? '#BFBFBF';
}
function statusLabel(s: WorkbenchApi.ModuleStatus) {
  return STATUS_LABEL[s] ?? '未知';
}

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

/** F-OV-05 漏斗联动：点泳道条 → 切处置 Tab + 高亮对应泳道 */
function onFunnelLaneClick(status: HandlingApi.OrderStatus) {
  store.setActiveTab('handling');
  store.setHandlingLaneFilter(status);
}
</script>

<template>
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

    <!-- 全局健康带（B5）：5 模块状态色 + 在线/维护计数，一眼可知系统是否正常 -->
    <div
      class="flex flex-none items-center gap-3 rounded border border-[#E4E7ED] bg-white px-3 py-1.5 text-xs"
    >
      <span class="flex-none font-medium text-[#1F4E79]">系统健康</span>
      <span class="flex-none text-gray-300">|</span>
      <div class="flex flex-1 items-center gap-4 overflow-x-auto">
        <span
          v-for="h in healthItems"
          :key="h.key"
          class="flex flex-none items-center gap-1"
          :title="`${h.name}：${statusLabel(h.status)}`"
        >
          <span
            class="inline-block h-2 w-2 rounded-full"
            :style="{ backgroundColor: statusColor(h.status) }"
          ></span>
          <span class="text-gray-700">{{ h.name }}</span>
          <span class="text-gray-400">{{ statusLabel(h.status) }}</span>
        </span>
      </div>
      <span class="flex flex-none items-center gap-3 text-gray-500">
        <span>在线 <span class="font-medium text-green-600">{{ onlineCount }}</span></span>
        <span v-if="maintenanceCount > 0">
          维护 <span class="font-medium text-orange-600">{{ maintenanceCount }}</span>
        </span>
      </span>
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

    <!-- Row 2: 单元平稳率(4) + 数据流(5) + 装置风险(3) · 非等宽 12 列 -->
    <div class="grid min-h-0 flex-1 grid-cols-12 gap-2">
      <div class="col-span-4 min-h-0">
        <SteadyRateBars
          :units="overview?.units"
          :global-steady="currentWindowBlock?.metrics?.steady_rate ?? null"
        />
      </div>
      <div class="col-span-5 min-h-0">
        <DataFlowDiagram
          :plugins="plugins"
          :loop-count="loopCount"
          :funnel="overview?.funnel"
          :roots-count="
            (overview?.roots ?? []).reduce((s, r) => s + (r.count ?? 0), 0)
          "
        />
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
        <FunnelStats :funnel="overview?.funnel" @lane-click="onFunnelLaneClick" />
      </div>
    </div>
    </div>
</template>
