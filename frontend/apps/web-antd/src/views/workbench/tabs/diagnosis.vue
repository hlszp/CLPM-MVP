<script setup lang="ts">
/**
 * 工作台 Tab3：回路诊断（原型截图 #tab-diag 1:1 对齐）
 *
 * 布局（对齐原型截图 · 12 列网格 · 3 行 + L0 横幅）：
 *   ┌──────────────────────────────────────────────────────────────────┐
 *   │ Row0 flex-none：GateBanner（L0/L1 才显示，无阻断时不渲染，自适应高）  │
 *   ├──────────────────────────────────────────────────────────────────┤
 *   │ Row1 c12：DgSummaryBand 5 项摘要横向并排（确诊异常/劣化/时延/置信/引擎）│
 *   ├──────────────────────────────┬───────────────────────────────────┤
 *   │ Row2 c5：ParetoBarLine       │ Row2 c7：诊断队列 Top6 + diagSeg   │
 *   │   柱+累计%折线 + 前2类占比标   │   风险优先 / 恶化最快 / 长期手动     │
 *   ├──────────────────────────────┼───────────────────────────────────┤
 *   │ Row3 c5：DgDiagTimeDist      │ Row3 c7：DgUnitStackedBar × Pareto  │
 *   │   诊断频次时间分布堆叠柱       │   装置Top8 × 全局Top5类别水平堆叠   │
 *   └──────────────────────────────┴───────────────────────────────────┘
 *
 * 数据流：
 * - A-03 getWorkbenchDiagnosisApi → 聚合（summary_band + 6 块）
 * - 范围/窗口切换自动联动刷新（watch store.scopeParams）
 * - 异常行点击 / 结论行点击 → 打开回路详情抽屉（用户决策）
 */
import type { WorkbenchApi } from '#/api/workbench';

import { computed, onMounted, ref, watch } from 'vue';

import { getWorkbenchDiagnosisApi } from '#/api/workbench';
import { useWorkbenchStore } from '#/store/workbench';

import AbnormalLoopsTable from '../components/AbnormalLoopsTable.vue';
import DgDiagTimeDist from '../components/DgDiagTimeDist.vue';
import DgSummaryBand from '../components/DgSummaryBand.vue';
import DgUnitStackedBar from '../components/DgUnitStackedBar.vue';
import GateBanner from '../components/GateBanner.vue';
import LoopDetailDrawer from '../components/LoopDetailDrawer.vue';
import ParetoBarLine from '../components/ParetoBarLine.vue';

const store = useWorkbenchStore();

const diagnosis = ref<null | WorkbenchApi.DiagnosisResult>(null);
const loading = ref(false);
const errorMsg = ref<null | string>(null);
const selectedTag = ref<null | WorkbenchApi.DiagnosisOpenTag>(null);

const summaryBand = computed(() => diagnosis.value?.summary_band ?? null);
const openTags = computed(() => diagnosis.value?.open_tags ?? []);
const conclTimeline = computed(() => diagnosis.value?.concl_timeline ?? []);
const fitnessGates = computed(() => diagnosis.value?.fitness_gates ?? null);
const pareto = computed(() => diagnosis.value?.pareto ?? []);

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
  loadDiagnosis();
});

watch(
  () => store.scopeParams,
  () => loadDiagnosis(),
  { deep: true },
);

function onRowClick(row: WorkbenchApi.DiagnosisOpenTag) {
  selectedTag.value = row;
}
</script>

<template>
  <div class="flex h-full min-h-0 flex-col gap-2 overflow-hidden p-2">
      <!-- 加载/错误提示（单行，不再撑高容器） -->
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

      <!-- Row0：L0/L1 阻断横幅（仅 fitness_gates.level ∈ {L0,L1} 时渲染，原型 renderDiag gate 行为） -->
      <GateBanner :gates="fitnessGates" />

      <!-- Row1 c12：摘要带 5 项（原型截图首行，84px） -->
      <div class="flex-none min-h-0">
        <DgSummaryBand :band="summaryBand" :window="store.timeWindow" />
      </div>

      <!-- Row2 grid c5/c7：Pareto 柱+折线 / 诊断队列 + diagSeg（固定 302px，与 Row3 保持等高，避免 1080p 下高度失衡 & 900p 下外溢） -->
      <div class="grid flex-none min-h-0 h-[302px] grid-cols-12 gap-2">
        <div class="col-span-5 min-h-0 h-full overflow-hidden rounded border border-[#E4E7ED]">
          <ParetoBarLine :pareto="pareto" :window="store.timeWindow" />
        </div>
        <div class="col-span-7 min-h-0 h-full overflow-hidden rounded border border-[#E4E7ED]">
          <AbnormalLoopsTable
            :rows="openTags"
            :window="store.timeWindow"
            @row-click="onRowClick"
          />
        </div>
      </div>

      <!-- Row3 grid c5/c7：诊断频次×时间堆叠柱 / 装置Top8×ParetoTop5 水平堆叠（固定 302px，与 Row2 等高，900p 下不再撑出外层滚动） -->
      <div class="grid flex-none min-h-0 h-[302px] grid-cols-12 gap-2">
        <div class="col-span-5 min-h-0 h-full overflow-hidden rounded border border-[#E4E7ED]">
          <DgDiagTimeDist
            :concl-items="conclTimeline"
            :open-tags="openTags"
            :window="store.timeWindow"
          />
        </div>
        <div class="col-span-7 min-h-0 h-full overflow-hidden rounded border border-[#E4E7ED]">
          <DgUnitStackedBar
            :concl-items="conclTimeline"
            :open-tags="openTags"
            :pareto="pareto"
          />
        </div>
      </div>

      <!-- 回路详情抽屉（用户决策：点击行打开，不再路由到整定 Tab） -->
      <LoopDetailDrawer :row="selectedTag" @close="selectedTag = null" />
    </div>
</template>
