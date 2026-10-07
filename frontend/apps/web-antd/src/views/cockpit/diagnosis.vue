<script lang="ts" setup>
/**
 * 驾驶舱 · 诊断页签（2026-10-05 整合裁决 D1/D3；P3 自运维工作台迁移）
 *
 * 内容源自原「运维工作台-回路诊断」Tab（门禁横幅/摘要带/帕累托/诊断队列/
 * 规则统计/装置堆叠），页内联动全部改为舱内摘要弹窗或页内抽屉，
 * 不跳管理后台。布局对齐原 tab。
 *
 * 数据流：A-03 getWorkbenchDiagnosisApi；时间窗由驾驶舱顶栏驱动
 * （cockpit store → 桥接 workbench store 供 scopeParams）。
 */
import type { DiagnosisApi } from '#/api/diagnosis';
import type { WorkbenchApi } from '#/api/workbench';

import { computed, onMounted, onUnmounted, ref, watch } from 'vue';

import { getWorkbenchDiagnosisApi } from '#/api/workbench';
import { useCockpitStore } from '#/store/cockpit';
import { useWorkbenchStore } from '#/store/workbench';
import CohortCompareDrawer from '#/views/diagnosis/components/cohort-compare-drawer.vue';
import { normalizeCategory } from '#/views/diagnosis/constants';

import CockpitHeader from './components/cockpit-header.vue';
import AbnormalLoopsTable from './wb-comps/AbnormalLoopsTable.vue';
import DgRuleStats from './wb-comps/DgRuleStats.vue';
import DgSummaryBand from './wb-comps/DgSummaryBand.vue';
import DgUnitStackedBar from './wb-comps/DgUnitStackedBar.vue';
import DrillModals from './wb-comps/drill-modals.vue';
import GateBanner from './wb-comps/GateBanner.vue';
import LoopDetailDrawer from './wb-comps/LoopDetailDrawer.vue';
import ParetoBarLine from './wb-comps/ParetoBarLine.vue';
import { closeDrillModals } from './wb-comps/use-drill';

import './styles/theme.css';
import './wb-comps/wb-theme.css';

const cockpitStore = useCockpitStore();
const store = useWorkbenchStore();
const theme = computed(() => cockpitStore.theme);

const diagnosis = ref<null | WorkbenchApi.DiagnosisResult>(null);
const loading = ref(false);
const errorMsg = ref<null | string>(null);
const selectedTag = ref<null | WorkbenchApi.DiagnosisOpenTag>(null);

const summaryBand = computed(() => diagnosis.value?.summary_band ?? null);
const openTags = computed(() => diagnosis.value?.open_tags ?? []);
const conclTimeline = computed(() => diagnosis.value?.concl_timeline ?? []);
const fitnessGates = computed(() => diagnosis.value?.fitness_gates ?? null);
const pareto = computed(() => diagnosis.value?.pareto ?? []);
const ruleStats = computed(() => diagnosis.value?.rule_stats ?? []);

async function loadDiagnosis() {
  loading.value = true;
  errorMsg.value = null;
  try {
    const res = await getWorkbenchDiagnosisApi(store.scopeParams);
    diagnosis.value = res;
  } catch (error) {
    errorMsg.value = error instanceof Error ? error.message : '诊断数据加载失败';
    diagnosis.value = null;
  } finally {
    loading.value = false;
    store.markRefreshed();
  }
}

onMounted(() => {
  void loadDiagnosis();
});

onUnmounted(() => {
  closeDrillModals();
});

// 驾驶舱顶栏时间窗 → workbench store（scopeParams 联动重载）
watch(
  () => cockpitStore.timeWindow,
  (w) => store.setWindow(w),
  { immediate: true },
);

watch(
  () => store.scopeParams,
  () => void loadDiagnosis(),
);

// 顶栏手动刷新联动本页
watch(
  () => cockpitStore.refreshTick,
  () => void loadDiagnosis(),
);

function onRowClick(row: WorkbenchApi.DiagnosisOpenTag) {
  selectedTag.value = row;
}

// ===== 16 号文 F4：共性问题回路组对比抽屉（Pareto 柱 / 堆叠条段第二层下钻） =====
const cohortOpen = ref(false);
const cohortCategory = ref<DiagnosisApi.Category | null>(null);
const cohortPlantNodeId = ref<string | undefined>(undefined);
const cohortPlantName = ref<string | undefined>(undefined);

/** Pareto 柱点击：分类已知（迁移版恒 GLOBAL，装置上下文由堆叠条行自带） */
function onParetoCohort(category: string) {
  const code = normalizeCategory(category);
  if (!code) return; // 非 8 类（脏数据）不打开
  cohortCategory.value = code;
  cohortPlantNodeId.value = undefined;
  cohortPlantName.value = undefined;
  cohortOpen.value = true;
}

/** 堆叠条分类段点击：分类 + 装置（行内 factory 名） */
function onUnitCohort(payload: {
  category: string;
  plantNodeName?: string;
}) {
  const code = normalizeCategory(payload.category);
  if (!code) return;
  cohortCategory.value = code;
  cohortPlantNodeId.value = undefined;
  cohortPlantName.value = payload.plantNodeName;
  cohortOpen.value = true;
}
</script>

<template>
  <div class="cockpit-root" :data-theme="theme">
    <CockpitHeader />

    <div class="diag">
      <!-- 加载/错误提示 -->
      <div
        v-if="loading"
        class="flex-none rounded border border-blue-100 bg-blue-50 px-3 py-1 text-[11px] text-blue-600"
      >
        正在加载诊断数据…
      </div>
      <div
        v-else-if="errorMsg"
        class="flex-none rounded border border-red-100 bg-red-50 px-3 py-1 text-[11px] text-red-600"
      >
        {{ errorMsg }}
        <button class="ml-2 underline" @click="loadDiagnosis">重试</button>
      </div>

      <!-- Row0：L0/L1 门禁横幅（无阻断时不渲染） -->
      <GateBanner :gates="fitnessGates" />

      <!-- Row1：摘要带 5 项 -->
      <div class="flex-none min-h-0">
        <DgSummaryBand :band="summaryBand" :window="store.timeWindow" />
      </div>

      <!-- Row2：Pareto 柱+折线 / 诊断队列 -->
      <div class="grid min-h-0 flex-1 grid-cols-12 gap-2">
        <div
          class="col-span-5 flex h-full min-h-0 flex-col overflow-hidden rounded border ckwb-border"
        >
          <ParetoBarLine
            :pareto="pareto"
            :window="store.timeWindow"
            @cohort="onParetoCohort"
          />
          <div class="flex-none px-2 pb-1 text-[10px] leading-tight ckwb-text-3">
            点击柱子查看「分类 × 装置」回路组对比。本图按历史诊断次数计数，
            对比列表按每回路最新结论筛选，二者口径不同、条数可能不一致。
          </div>
        </div>
        <div class="col-span-7 h-full min-h-0 overflow-hidden rounded border ckwb-border">
          <AbnormalLoopsTable
            :rows="openTags"
            :window="store.timeWindow"
            @row-click="onRowClick"
          />
        </div>
      </div>

      <!-- Row3：规则命中×解决率 / 装置堆叠 -->
      <div class="grid min-h-0 flex-1 grid-cols-12 gap-2">
        <div class="col-span-5 h-full min-h-0 overflow-hidden rounded border ckwb-border">
          <DgRuleStats :rule-stats="ruleStats" />
        </div>
        <div class="col-span-7 h-full min-h-0 overflow-hidden rounded border ckwb-border">
          <DgUnitStackedBar
            :concl-items="conclTimeline"
            :open-tags="openTags"
            :pareto="pareto"
            @cohort="onUnitCohort"
          />
        </div>
      </div>

      <!-- 回路详情抽屉（页内，非路由） -->
      <LoopDetailDrawer :row="selectedTag" @close="selectedTag = null" />

      <!-- 共性问题回路组对比抽屉（16 号文 F4） -->
      <CohortCompareDrawer
        v-model:open="cohortOpen"
        :category="cohortCategory"
        :plant-node-id="cohortPlantNodeId"
        :plant-node-name="cohortPlantName"
      />
    </div>

    <!-- 下钻弹窗组 -->
    <DrillModals />
  </div>
</template>

<style scoped>
.diag {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 8px;
  min-height: 0;
  overflow: hidden;
  padding: 8px 12px 12px;
}
</style>
