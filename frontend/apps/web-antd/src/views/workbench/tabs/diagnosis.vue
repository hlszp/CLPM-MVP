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
 *   │ Row3 c5：DgRootsAndConfidence│ Row3 c7：ConclTimeline + conclSeg │
 *   │   根因水平柱 + 置信度 3 段分   │   全部 / 高置信 / 已采纳             │
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
import ConclTimeline from '../components/ConclTimeline.vue';
import DgRootsAndConfidence from '../components/DgRootsAndConfidence.vue';
import DgSummaryBand from '../components/DgSummaryBand.vue';
import GateBanner from '../components/GateBanner.vue';
import LoopDetailDrawer from '../components/LoopDetailDrawer.vue';
import ParetoBarLine from '../components/ParetoBarLine.vue';
import WorkbenchShell from '../components/WorkbenchShell.vue';

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
const rootcauseTop = computed(() => diagnosis.value?.rootcause_top ?? []);

/** 从 concl_timeline 聚合置信度 3 段分布（给 DgRootsAndConfidence 右区） */
const confidenceDist = computed<{ high: number; low: number; mid: number }>(() => {
  let high = 0;
  let mid = 0;
  let low = 0;
  for (const it of conclTimeline.value) {
    const c = it.confidence;
    if (c === null || c === undefined) continue;
    if (c >= 0.8) high += 1;
    else if (c >= 0.6) mid += 1;
    else low += 1;
  }
  return { high, mid, low };
});

/** 低置信人工复核提示（原型截图底部） */
const lowConfNotice = computed<null | { action?: string; count: number; loop_name?: string }>(
  () => {
    const lowItems = conclTimeline.value.filter(
      (it) => it.confidence !== null && it.confidence !== undefined && it.confidence < 0.6,
    );
    if (lowItems.length === 0) return null;
    return {
      count: lowItems.length,
      loop_name: lowItems[0]?.loop_name ?? undefined,
      action: '已转入人工复核',
    };
  },
);

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

function onConclClick(item: WorkbenchApi.DiagnosisConclItem) {
  const matched = openTags.value.find((t) => t.loop_id === item.loop_id);
  if (matched) {
    selectedTag.value = matched;
    return;
  }
  // 未在队列中的结论：仍构造一个伪 tag 打开抽屉（仅带 loop_id/loop_name）
  selectedTag.value = {
    tag_id: `concl-${item.result_id ?? item.id ?? item.loop_id}`,
    loop_id: item.loop_id,
    loop_name: item.loop_name,
    symptom: item.category ?? item.tag_code,
    category: item.category,
    severity: item.severity,
    spark: [],
    sla_due_sec: null,
    sla_stage: null,
    conclusion: item.evidence_summary,
    fitness_level: null,
    confidence: item.confidence,
    triggered_at: item.ts,
  };
}
</script>

<template>
  <WorkbenchShell>
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

      <!-- Row2 grid c5/c7：Pareto 柱+折线 / 诊断队列 + diagSeg（300px，一屏） -->
      <div class="grid flex-none min-h-0 grid-cols-12 gap-2">
        <div class="col-span-5 min-h-0 overflow-hidden rounded border border-[#E4E7ED]">
          <ParetoBarLine :pareto="pareto" :window="store.timeWindow" />
        </div>
        <div class="col-span-7 min-h-0 overflow-hidden rounded border border-[#E4E7ED]">
          <AbnormalLoopsTable
            :rows="openTags"
            :window="store.timeWindow"
            @row-click="onRowClick"
          />
        </div>
      </div>

      <!-- Row3 grid c5/c7：根因分布+置信度 / 结论流 + conclSeg（300px，一屏，剩余空间吃 flex 不超） -->
      <div class="grid min-h-0 flex-1 grid-cols-12 gap-2">
        <div class="col-span-5 min-h-0 overflow-hidden rounded border border-[#E4E7ED]">
          <DgRootsAndConfidence
            :roots="rootcauseTop"
            :confidence-dist="confidenceDist"
            :low-conf-notice="lowConfNotice ?? undefined"
          />
        </div>
        <div class="col-span-7 min-h-0 overflow-hidden rounded border border-[#E4E7ED]">
          <ConclTimeline :items="conclTimeline" @loop-click="onConclClick" />
        </div>
      </div>

      <!-- 回路详情抽屉（用户决策：点击行打开，不再路由到整定 Tab） -->
      <LoopDetailDrawer :row="selectedTag" @close="selectedTag = null" />
    </div>
  </WorkbenchShell>
</template>
