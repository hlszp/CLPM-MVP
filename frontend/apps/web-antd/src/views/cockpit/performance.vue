<script lang="ts" setup>
/**
 * 驾驶舱 · 性能页签（2026-10-05 整合裁决 D1/D3；P2 自运维工作台迁移）
 *
 * 内容源自原「运维工作台-性能评估」Tab（A-02 摘要带/排名/热力/趋势/分布），
 * 页内联动全部改为舱内摘要弹窗（use-drill），不跳管理后台。
 * 布局对齐原 tab（12 列网格 3 行）。
 *
 * 数据流：A-02 getWorkbenchAssessmentApi（view 联动）；时间窗由驾驶舱顶栏
 * 三 pill 驱动（cockpit store → 桥接 workbench store 供 scopeParams）。
 */
import type { WorkbenchApi } from '#/api/workbench';

import { computed, onMounted, onUnmounted, ref, watch } from 'vue';

import { getWorkbenchAssessmentApi } from '#/api/workbench';
import { useCockpitStore } from '#/store/cockpit';
import { useWorkbenchStore } from '#/store/workbench';

import CockpitHeader from './components/cockpit-header.vue';
import DrillModals from './wb-comps/drill-modals.vue';
import EvalDistributions from './wb-comps/EvalDistributions.vue';
import EvalHeatMatrix from './wb-comps/EvalHeatMatrix.vue';
import EvalRankTable from './wb-comps/EvalRankTable.vue';
import EvalSummary from './wb-comps/EvalSummary.vue';
import EvalTrendChart from './wb-comps/EvalTrendChart.vue';
import { closeDrillModals, useCockpitDrill } from './wb-comps/use-drill';

import './styles/theme.css';
import './wb-comps/wb-theme.css';

const cockpitStore = useCockpitStore();
const store = useWorkbenchStore();
const theme = computed(() => cockpitStore.theme);
const { drill } = useCockpitDrill();

const assessment = ref<null | WorkbenchApi.AssessmentResult>(null);
const view = ref<WorkbenchApi.AssessmentView>('plant');
const loading = ref(false);
const errorMsg = ref<null | string>(null);

const summary = computed(() => assessment.value?.summary ?? null);
const ranking = computed(() => assessment.value?.ranking ?? []);
const heatmap = computed(() => assessment.value?.heatmap);
const trend = computed(() => assessment.value?.trend ?? null);
const evaluated = computed(() => summary.value?.participation.evaluated ?? 0);
const total = computed(() => summary.value?.participation.total ?? 0);

async function loadAssessment() {
  loading.value = true;
  errorMsg.value = null;
  try {
    const res = await getWorkbenchAssessmentApi({
      ...store.scopeParams,
      view: view.value,
    });
    assessment.value = res;
  } catch (error) {
    errorMsg.value = error instanceof Error ? error.message : '评估数据加载失败';
    assessment.value = null;
  } finally {
    loading.value = false;
    store.markRefreshed();
  }
}

onMounted(() => {
  void loadAssessment();
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

// 范围/窗口切换联动（G41：scopeParams 身份变化即触发）
watch(
  () => store.scopeParams,
  () => void loadAssessment(),
);

// 视图切换（装置/单元）独立触发
watch(view, () => void loadAssessment());

// 顶栏手动刷新联动本页
watch(
  () => cockpitStore.refreshTick,
  () => void loadAssessment(),
);

function onLoseClick(_tag: string) {
  // 失分 tag → 舱内切诊断页签（D3：不跳管理后台）
  void _tag;
  void drill({ kind: 'tab', path: '/cockpit/diagnosis' });
}
</script>

<template>
  <div class="cockpit-root" :data-theme="theme">
    <CockpitHeader />

    <div class="perf">
      <!-- 加载/错误提示 -->
      <div
        v-if="loading"
        class="flex-none rounded border border-blue-100 bg-blue-50 px-3 py-1 text-[11px] text-blue-600"
      >
        正在加载评估数据…
      </div>
      <div
        v-else-if="errorMsg"
        class="flex-none rounded border border-red-100 bg-red-50 px-3 py-1 text-[11px] text-red-600"
      >
        {{ errorMsg }}
        <button class="ml-2 underline" @click="loadAssessment">重试</button>
      </div>

      <!-- Row 1: 摘要带 -->
      <div class="flex-none" style="height: 96px">
        <EvalSummary :summary="summary" />
      </div>

      <!-- Row 2: 装置/单元排名 + 单元×指标热力 -->
      <div class="grid min-h-0 flex-1 grid-cols-12 gap-2">
        <div class="col-span-7 min-h-0">
          <EvalRankTable
            :ranking="ranking"
            :total="total"
            :view="view"
            @lose-click="onLoseClick"
            @update:view="view = $event"
          />
        </div>
        <div class="col-span-5 min-h-0">
          <EvalHeatMatrix :heatmap="heatmap" />
        </div>
      </div>

      <!-- Row 3: 综合评分+分项趋势 + 等级/模式/质量 -->
      <div class="grid min-h-0 flex-1 grid-cols-12 gap-2">
        <div class="col-span-7 min-h-0">
          <EvalTrendChart :trend="trend" />
        </div>
        <div class="col-span-5 min-h-0">
          <EvalDistributions
            :evaluated="evaluated"
            :total="total"
            :trend="trend"
          />
        </div>
      </div>
    </div>

    <!-- 下钻弹窗组（ListModal/详情/趋势/SLA） -->
    <DrillModals />
  </div>
</template>

<style scoped>
.perf {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 8px;
  min-height: 0;
  padding: 8px 12px 12px;
}
</style>
