<script lang="ts" setup>
/**
 * 回路监视独立页面（页型 B：对象表 / 面点分离）
 *
 * 路由：/monitor/loops（canonical）
 * 角色：全角色可见（ADMIN/IC_ENGINEER/PE_ENGINEER/SPONSOR/EXPERT）
 *
 * 菜单重构 Phase1（2026-08-24）：
 * - 列表只呈现干净结论（评分/性能等级/适用性），佐证走右侧抽屉
 * - 行点击/位号链接 → 打开回路详情抽屉（基本信息/最新指标/等级/适用性原因）
 * - 抽屉内"进入回路工作台"携带 from=/monitor/loops 及筛选上下文
 */
import type { LoopApi } from '#/api/loop';
import type { PlantNodeApi } from '#/api/plant-node';

import { computed, onMounted, ref, watch } from 'vue';

import { Input, Select, Tooltip, TreeSelect } from 'ant-design-vue';

import { getPlantNodeTreeApi } from '#/api/plant-node';
import { ClpmPageToolbar } from '#/components/clpm';
import LoopTrendModal from '#/components/loop/loop-trend-modal.vue';
import LoopDetailDrawer from '#/components/monitor/loop-detail-drawer.vue';
import LoopFleetView from '#/components/monitor/loop-fleet-view.vue';
import { useMonitorContext } from '#/composables/use-monitor-context';

defineOptions({ name: 'MonitorLoops' });

const monitorCtx = useMonitorContext();

// ===== 装置筛选（工厂层级树，URL 真相源） =====
const plantTree = ref<PlantNodeApi.PlantNode[]>([]);

onMounted(async () => {
  try {
    plantTree.value = await getPlantNodeTreeApi();
  } catch {
    plantTree.value = [];
  }
});

const plantNodeId = computed<string | undefined>({
  get: () => monitorCtx.plantNodeId.value ?? undefined,
  set: (val) => monitorCtx.update({ plantNodeId: val ?? null }),
});

// ===== 模式筛选（实时控制模式，与列表 modeLabel 口径一致） =====
const controlModeOptions = [
  { label: '自动（Auto）', value: 'Auto' },
  { label: '串级（Cascade）', value: 'Cascade' },
  { label: '手动（Manual）', value: 'Manual' },
];

const controlMode = computed<'Auto' | 'Cascade' | 'Manual' | undefined>({
  get: () =>
    (monitorCtx.controlMode.value as 'Auto' | 'Cascade' | 'Manual' | null) ??
    undefined,
  set: (val) => monitorCtx.update({ controlMode: val ?? null }),
});

// ===== 关键词搜索（防抖 300ms）=====
const keyword = ref(monitorCtx.keyword.value);
let keywordTimer: null | ReturnType<typeof setTimeout> = null;

watch(keyword, (val) => {
  if (keywordTimer) clearTimeout(keywordTimer);
  keywordTimer = setTimeout(() => {
    monitorCtx.update({ keyword: val });
  }, 300);
});

// 从 URL 同步（浏览器前进/后退）
watch(
  () => monitorCtx.keyword.value,
  (val) => {
    if (val !== keyword.value) keyword.value = val;
  },
);

// ===== 行点击 → 打开右侧详情抽屉 =====
const drawerOpen = ref(false);
const drawerLoop = ref<LoopApi.MonitorListItem | null>(null);

function handleLoopClick(_loopId: string, record: LoopApi.MonitorListItem) {
  drawerLoop.value = record;
  drawerOpen.value = true;
}

// ===== 操作列"趋势" → 趋势图弹窗 =====
const trendOpen = ref(false);
const trendLoop = ref<LoopApi.MonitorListItem | null>(null);

function handleTrendClick(record: LoopApi.MonitorListItem) {
  trendLoop.value = record;
  trendOpen.value = true;
}

// ===== 抽屉内进入回路工作台（携带监控上下文）=====
function handleGotoWorkbench(loopId: string) {
  drawerOpen.value = false;
  monitorCtx.navigateWithMonitorContext('/monitor/loop-workbench', {
    loopId,
    from: '/monitor/loops',
  });
}
</script>

<template>
  <div class="monitor-loops-page flex h-full flex-col">
    <!-- R1 页头工具栏 -->
    <ClpmPageToolbar
      title="回路监视"
      subtitle="全厂回路绩效扫视，点击位号查看详情，锁定例外后进入工作台处置"
    >
      <template #actions>
        <div class="flex items-center gap-2">
          <TreeSelect
            v-model:value="plantNodeId"
            :tree-data="plantTree"
            :field-names="{ label: 'name', value: 'id', children: 'children' }"
            allow-clear
            placeholder="全部装置"
            class="!w-48"
            tree-default-expand-all
          />
          <Select
            v-model:value="controlMode"
            :options="controlModeOptions"
            allow-clear
            placeholder="模式"
            class="!w-36"
          />
          <Input
            v-model:value="keyword"
            allow-clear
            placeholder="搜索位号、描述、装置"
            class="!w-64"
          >
            <template #prefix>
              <div class="i-lucide:search w-4 h-4 text-gray-400"></div>
            </template>
          </Input>
          <Tooltip title="支持位号、回路描述、装置名称模糊匹配">
            <span class="cursor-help text-xs text-gray-400">?</span>
          </Tooltip>
        </div>
      </template>
    </ClpmPageToolbar>

    <!-- R4 主画布：LoopFleetView 承载统计卡 + 表格 -->
    <div class="flex-1 overflow-auto p-4">
      <LoopFleetView
        :show-stats="true"
        :show-auto-refresh="true"
        :show-toolbar="false"
        @loop-click="handleLoopClick"
        @trend-click="handleTrendClick"
      />
    </div>

    <!-- 回路详情抽屉（右侧；列表结论的佐证承载） -->
    <LoopDetailDrawer
      v-model:open="drawerOpen"
      :loop="drawerLoop"
      @goto-workbench="handleGotoWorkbench"
    />

    <!-- 回路趋势弹窗（历史/实时，PV/SP/OP/MODE） -->
    <LoopTrendModal
      v-model:open="trendOpen"
      :loop-id="trendLoop?.loopId ?? null"
      :tag-name="trendLoop?.tagName ?? ''"
    />
  </div>
</template>

<style scoped>
.monitor-loops-page {
  background: var(--clpm-bg-canvas, #f5f6f8);
}
</style>
