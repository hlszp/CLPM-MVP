<script lang="ts" setup>
/**
 * 回路监视独立页面（页型 B：对象表 / 面点分离）
 *
 * 路由：/monitor/loops（canonical）
 * 角色：全角色可见（ADMIN/IC_ENGINEER/PE_ENGINEER/SPONSOR/EXPERT）
 *
 * 回路监视页改版 P1（2026-10-03，含当日裁决回退）：
 * - 微型卡片（类型/等级/综合/自控率）收缩进筛选区（类型/等级 Select），顶部统计区移除
 * - 行点击/位号/操作列"详情" → 右侧详情抽屉（保留原有佐证内容；用户裁决：
 *   不直接跳工作台，抽屉内"进入回路工作台"承载入口）
 * - 抽屉按钮 → 精简版回路工作台（?embed=1：无左脊柱、直接定位、带返回）
 */
import type { LoopApi } from '#/api/loop';
import type { PlantNodeApi } from '#/api/plant-node';

import { onMounted, ref, watch } from 'vue';
import { useRoute } from 'vue-router';

import { Button, Input, Select, Tooltip, TreeSelect } from 'ant-design-vue';

import { getPlantNodeTreeApi } from '#/api/plant-node';
import { ClpmPageToolbar } from '#/components/clpm';
import LoopTrendModal from '#/components/loop/loop-trend-modal.vue';
import LoopDetailDrawer from '#/components/monitor/loop-detail-drawer.vue';
import LoopFleetView from '#/components/monitor/loop-fleet-view.vue';
import { LOOP_TYPE_LABEL_MAP } from '#/composables/use-loop-palettes';
import { useMonitorContext } from '#/composables/use-monitor-context';
import { GRADE_THRESHOLDS } from '#/constants/clpm-ui';

defineOptions({ name: 'MonitorLoops' });

const monitorCtx = useMonitorContext();
const route = useRoute();

// ===== 筛选草稿态：本地编辑，点“查询”一次性提交到 context 触发列表刷新 =====
const plantTree = ref<PlantNodeApi.PlantNode[]>([]);

onMounted(async () => {
  try {
    plantTree.value = await getPlantNodeTreeApi();
  } catch {
    plantTree.value = [];
  }
});

// 装置筛选（工厂层级树草稿，初始从 URL 上下文同步）
const plantNodeDraft = ref<string | undefined>(
  monitorCtx.plantNodeId.value ?? undefined,
);

// ===== 模式筛选（实时控制模式草稿，与列表 modeLabel 口径一致） =====
const controlModeOptions = [
  { label: '自动（Auto）', value: 'Auto' },
  { label: '串级（Cascade）', value: 'Cascade' },
  { label: '手动（Manual）', value: 'Manual' },
];

const controlModeDraft = ref<
  'Auto' | 'Cascade' | 'Manual' | undefined
>(
  (monitorCtx.controlMode.value as 'Auto' | 'Cascade' | 'Manual' | null) ??
    undefined,
);

// ===== 类型/等级筛选草稿（回路监视页改版 P1-1：微型卡片收缩进筛选区） =====
const loopTypeOptions = Object.entries(LOOP_TYPE_LABEL_MAP).map(
  ([value, label]) => ({ label, value }),
);

const gradeOptions = [
  ...GRADE_THRESHOLDS.map((t) => ({ label: t.label ?? t.name, value: t.name })),
  { label: '无评分', value: 'INCONCLUSIVE' },
];

const loopTypeDraft = ref<string | undefined>(
  monitorCtx.loopType.value ?? undefined,
);
const gradeDraft = ref<string | undefined>(
  monitorCtx.grade.value ?? undefined,
);

// ===== 关键词搜索草稿（初始从 URL 同步；不再即时/防抖提交） =====
const keywordDraft = ref(monitorCtx.keyword.value);

/** 点“查询”才把草稿筛选写入 context（URL），列表 watch 到变化后统一刷新 */
function applyFilters() {
  monitorCtx.update({
    plantNodeId: plantNodeDraft.value ?? null,
    controlMode: controlModeDraft.value ?? null,
    loopType: loopTypeDraft.value ?? null,
    grade: gradeDraft.value ?? null,
    keyword: keywordDraft.value,
  });
}

// 浏览器前进/后退时 URL 变化 → 草稿态跟随（不重复触发列表刷新）
watch(
  () => [
    monitorCtx.plantNodeId.value,
    monitorCtx.controlMode.value,
    monitorCtx.loopType.value,
    monitorCtx.grade.value,
    monitorCtx.keyword.value,
  ],
  ([plantNodeId, controlMode, loopType, grade, keyword]) => {
    if (plantNodeId !== plantNodeDraft.value) {
      plantNodeDraft.value = plantNodeId ?? undefined;
    }
    const mode = controlMode as 'Auto' | 'Cascade' | 'Manual' | null;
    if (mode !== controlModeDraft.value) controlModeDraft.value = mode ?? undefined;
    if (loopType !== loopTypeDraft.value) loopTypeDraft.value = loopType ?? undefined;
    if (grade !== gradeDraft.value) gradeDraft.value = grade ?? undefined;
    if (keyword !== keywordDraft.value) keywordDraft.value = keyword ?? '';
  },
);

// ===== 行点击/位号/详情 → 右侧详情抽屉（保留原佐证内容，2026-10-03 用户裁决）=====
const drawerOpen = ref(false);
const drawerLoop = ref<LoopApi.MonitorListItem | null>(null);

function handleLoopClick(_loopId: string, record: LoopApi.MonitorListItem) {
  drawerLoop.value = record;
  drawerOpen.value = true;
}

// ===== 抽屉内进入精简版回路工作台（P1-2：无左脊柱、直接定位、带返回）=====
function handleGotoWorkbench(loopId: string) {
  drawerOpen.value = false;
  // from 携带完整路径（含筛选 query），工作台返回时筛选上下文原样恢复
  monitorCtx.navigateWithMonitorContext('/loop/workbench360', {
    loopId,
    from: route.fullPath,
    embed: '1',
  });
}

// ===== 操作列"趋势" → 趋势图弹窗 =====
const trendOpen = ref(false);
const trendLoop = ref<LoopApi.MonitorListItem | null>(null);

function handleTrendClick(record: LoopApi.MonitorListItem) {
  trendLoop.value = record;
  trendOpen.value = true;
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
            v-model:value="plantNodeDraft"
            :tree-data="plantTree"
            :field-names="{ label: 'name', value: 'id', children: 'children' }"
            allow-clear
            placeholder="全部装置"
            class="!w-44"
            tree-default-expand-all
          />
          <Select
            v-model:value="loopTypeDraft"
            :options="loopTypeOptions"
            allow-clear
            placeholder="类型"
            class="!w-32"
          />
          <Select
            v-model:value="gradeDraft"
            :options="gradeOptions"
            allow-clear
            placeholder="性能等级"
            class="!w-32"
          />
          <Select
            v-model:value="controlModeDraft"
            :options="controlModeOptions"
            allow-clear
            placeholder="模式"
            class="!w-32"
          />
          <Input
            v-model:value="keywordDraft"
            allow-clear
            placeholder="搜索位号、描述、装置"
            class="!w-64"
            @press-enter="applyFilters"
          >
            <template #prefix>
              <div class="i-lucide:search w-4 h-4 text-gray-400"></div>
            </template>
          </Input>
          <Tooltip title="支持位号、回路描述、装置名称模糊匹配">
            <span class="cursor-help text-xs text-gray-400">?</span>
          </Tooltip>
          <Button type="primary" @click="applyFilters">查询</Button>
        </div>
      </template>
    </ClpmPageToolbar>

    <!-- R4 主画布：LoopFleetView 承载表格（微型卡片已收缩进筛选区，P1-1） -->
    <div class="flex-1 overflow-auto p-4">
      <LoopFleetView
        :show-auto-refresh="true"
        :show-toolbar="false"
        @loop-click="handleLoopClick"
        @trend-click="handleTrendClick"
      />
    </div>

    <!-- 回路详情抽屉（右侧；列表结论的佐证承载，工作台入口在抽屉底部） -->
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
