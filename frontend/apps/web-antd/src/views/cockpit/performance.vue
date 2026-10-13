<script lang="ts" setup>
/**
 * 驾驶舱 · 性能页签（2026-10-05 整合裁决 D1/D3；P2 自运维工作台迁移）
 *
 * 内容源自原「运维工作台-性能评估」Tab（A-02 摘要带/排名/热力/趋势/分布），
 * 页内联动全部改为舱内摘要弹窗（use-drill），不跳管理后台。
 * 布局对齐原 tab（12 列网格 3 行）。
 *
 * 数据流：A-02 getWorkbenchAssessmentApi；R2（2026-10-12）排名区改树形
 * （装置×单元同列表）：并行请求 plant+unit 两视图 + node-tree 组树，
 * 不再有视图按钮切换；时间窗由驾驶舱顶栏三 pill 驱动（cockpit store →
 * 桥接 workbench store 供 scopeParams）。
 *
 * P5-04（03 号文 §8）：
 * - 竞态：use-latest-request 守卫接入列表加载（范围/窗口快速切换时迟到的
 *   旧响应丢弃，不覆盖新数据）——复用既有守卫不分叉；
 * - block_states 前端接线（P5-03 后端块级状态）：summary/ranking/heatmap/
 *   trend 各块独立渲染 OK/UNAVAILABLE——失败块显式"该来源暂不可用"+可
 *   重试（诚实化：不做全局 error 兜底掩盖部分成功；重试只重失败块，成功
 *   块命中缓存不再执行）。unit 视图 ranking 单独跟踪（树形排名行缺失时
 *   显式提示，不静默只剩装置行）。
 */
import type { CockpitApi } from '#/api/cockpit';
import type { WorkbenchApi } from '#/api/workbench';

import { computed, onMounted, onUnmounted, ref, watch } from 'vue';

import { getCockpitNodeTreeApi } from '#/api/cockpit';
import { getWorkbenchAssessmentApi } from '#/api/workbench';
import { useLatestRequest } from '#/composables/use-latest-request';
import { useCockpitStore } from '#/store/cockpit';
import { useWorkbenchStore } from '#/store/workbench';

import BlockStateOverlay from './components/block-state-overlay.vue';
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
/** R2 树形排名：unit 视图行 + node-tree（与 plant 视图并行拉取） */
const unitAssessment = ref<WorkbenchApi.AssessmentRankRow[]>([]);
/** unit 视图块状态（树形排名的单元行来源；UNAVAILABLE 时显式提示可重试） */
const unitBlockStates = ref<WorkbenchApi.AssessmentResult['block_states']>({});
const nodeTree = ref<CockpitApi.NodeTreeNode[] | null>(null);
const loading = ref(false);
const errorMsg = ref<null | string>(null);

const summary = computed(() => assessment.value?.summary ?? null);
const ranking = computed(() => assessment.value?.ranking ?? []);
const heatmap = computed(() => assessment.value?.heatmap);
const trend = computed(() => assessment.value?.trend ?? null);
const evaluated = computed(() => summary.value?.participation.evaluated ?? 0);
const total = computed(() => summary.value?.participation.total ?? 0);

// P5-04 块状态（additive 键缺省视为 OK——旧响应兼容）
const blockStates = computed(() => assessment.value?.block_states ?? {});
const blockState = (key: 'heatmap' | 'ranking' | 'summary' | 'trend') =>
  blockStates.value?.[key] ?? 'OK';
const unitRankingState = computed(
  () => unitBlockStates.value?.ranking ?? 'OK',
);

// ===== P5-04 竞态守卫（复用 use-latest-request，不分叉）：
// 范围/窗口快速切换时迟到的旧响应丢弃 =====
const guard = useLatestRequest<string>();

async function loadAssessment() {
  const key = JSON.stringify(store.scopeParams);
  guard.bump(key);
  loading.value = true;
  errorMsg.value = null;
  await guard.run(async (_signal, capturedEpoch) => {
    const isCurrent = () => guard.guard(key, capturedEpoch);
    try {
      // R2：plant 视图为主数据（summary/heatmap/trend 与视图无关），
      // unit 视图行 + node-tree 仅用于排名区组树
      const [plantRes, unitRes, treeRes] = await Promise.all([
        getWorkbenchAssessmentApi({ ...store.scopeParams, view: 'plant' }),
        getWorkbenchAssessmentApi({ ...store.scopeParams, view: 'unit' }),
        getCockpitNodeTreeApi().catch(() => null),
      ]);
      if (!isCurrent()) return; // A→B 竞态：迟到的旧响应丢弃
      assessment.value = plantRes;
      unitAssessment.value = unitRes?.ranking ?? [];
      unitBlockStates.value = unitRes?.block_states ?? {};
      nodeTree.value = treeRes ?? null;
    } catch (error) {
      if (!isCurrent()) return;
      errorMsg.value = error instanceof Error ? error.message : '评估数据加载失败';
      assessment.value = null;
      unitAssessment.value = [];
      unitBlockStates.value = {};
      nodeTree.value = null;
    } finally {
      if (isCurrent()) {
        loading.value = false;
        store.markRefreshed();
      }
    }
  });
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
      <!-- 加载/错误提示（整页失败域：部分块失败见各块覆盖层，不在此掩盖） -->
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

      <!-- Row 1: 摘要带（summary 块） -->
      <div class="relative flex-none" style="height: 96px">
        <EvalSummary :summary="summary" />
        <BlockStateOverlay
          :state="blockState('summary')"
          @retry="loadAssessment"
        />
      </div>

      <!-- Row 2: 装置×单元树形排名 + 单元×指标热力 -->
      <div class="grid min-h-0 flex-1 grid-cols-12 gap-2">
        <div class="relative col-span-7 min-h-0">
          <EvalRankTable
            :node-tree="nodeTree"
            :plants="ranking"
            :units="unitAssessment"
            :total="total"
            @lose-click="onLoseClick"
          />
          <BlockStateOverlay
            :state="blockState('ranking')"
            @retry="loadAssessment"
          />
          <!-- unit 视图块（树形排名的单元行来源）：装置行可用而单元行缺失时显式提示 -->
          <div
            v-if="unitRankingState === 'UNAVAILABLE' && blockState('ranking') !== 'UNAVAILABLE'"
            class="unit-rows-unavailable"
          >
            单元行暂不可用
            <button class="ml-1 underline" @click="loadAssessment">重试</button>
          </div>
        </div>
        <div class="relative col-span-5 min-h-0">
          <EvalHeatMatrix :heatmap="heatmap" />
          <BlockStateOverlay
            :state="blockState('heatmap')"
            @retry="loadAssessment"
          />
        </div>
      </div>

      <!-- Row 3: 综合评分+分项趋势 + 等级/模式/质量（trend 块两处消费） -->
      <div class="grid min-h-0 flex-1 grid-cols-12 gap-2">
        <div class="relative col-span-7 min-h-0">
          <EvalTrendChart :trend="trend" />
          <BlockStateOverlay
            :state="blockState('trend')"
            @retry="loadAssessment"
          />
        </div>
        <div class="relative col-span-5 min-h-0">
          <EvalDistributions
            :evaluated="evaluated"
            :total="total"
            :trend="trend"
          />
          <BlockStateOverlay
            :state="blockState('trend')"
            @retry="loadAssessment"
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

/* unit 视图 ranking 块失败的行内提示（装置行仍正常显示，不掩盖部分成功） */
.unit-rows-unavailable {
  position: absolute;
  right: 8px;
  bottom: 6px;
  z-index: 3;
  padding: 2px 8px;
  font-size: 11px;
  color: var(--ck-text-2);
  background: color-mix(in srgb, var(--ck-panel, #fff) 88%, transparent);
  border-radius: 4px;
}
</style>
