<script setup lang="ts">
/**
 * 工作台 Tab2：性能评估（原型对齐 1:1 · M2 G-评估 F-EV-01~03）
 *
 * 布局（对齐原型 #tab-eval · 12 列网格 · 3 行）：
 *   ┌──────────────────────────────────────────────────────────────┐
 *   │ Row1 摘要带（c12 · 96px）：gauge + 参评 + 距目标 + 环比 + 结论 + 风险速览   │
 *   ├────────────────────────────────┬─────────────────────────────┤
 *   │ Row2 左 装置/单元综合排名（c7） │ Row2 右 单元×指标热力（c5）   │
 *   ├────────────────────────────────┼─────────────────────────────┤
 *   │ Row3 左 综合评分+分项趋势（c7） │ Row3 右 等级+模式+质量（c5）  │
 *   └────────────────────────────────┴─────────────────────────────┘
 *
 * 数据流：
 * - A-02 getWorkbenchAssessmentApi → summary/ranking/heatmap/trend（view 联动）
 * - 范围/窗口切换自动联动刷新（watch store.scopeParams）
 * - 装置/单元视图切换（view）独立触发 ranking 重载
 * - 失分 tag / 长期手动 / 结论跳转 → 跨 Tab 切诊断
 */
import type { WorkbenchApi } from '#/api/workbench';

import { computed, onMounted, ref, watch } from 'vue';
import { useRouter } from 'vue-router';

import { getWorkbenchAssessmentApi } from '#/api/workbench';
import { useWorkbenchStore } from '#/store/workbench';

import EvalDistributions from '../components/EvalDistributions.vue';
import EvalHeatMatrix from '../components/EvalHeatMatrix.vue';
import EvalRankTable from '../components/EvalRankTable.vue';
import EvalSummary from '../components/EvalSummary.vue';
import EvalTrendChart from '../components/EvalTrendChart.vue';
import WorkbenchShell from '../components/WorkbenchShell.vue';

const store = useWorkbenchStore();
const router = useRouter();

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
  loadAssessment();
});

// 范围/窗口切换联动
watch(
  () => store.scopeParams,
  () => loadAssessment(),
  { deep: true },
);

// 视图切换（装置/单元）独立触发
watch(view, () => loadAssessment());

function onLoseClick(_tag: string) {
  // 失分 tag → 跨 Tab 切诊断
  router.push('/workbench/diagnosis');
}
</script>

<template>
  <WorkbenchShell>
    <div class="flex h-full flex-col gap-2 p-2">
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

      <!-- Row 1: 摘要带（c12 · 96px） -->
      <div class="flex-none" style="height: 96px">
        <EvalSummary :summary="summary" />
      </div>

      <!-- Row 2: 装置/单元排名(c7) + 单元×指标热力(c5) -->
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

      <!-- Row 3: 综合评分+分项趋势(c7) + 等级+模式+质量(c5) -->
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
  </WorkbenchShell>
</template>
