<script lang="ts" setup>
/**
 * 回路工作台（单页四区重构 · 2026-08-07）
 *
 * 双轴导航 · 实体轴：单回路 360° 一站式处置
 * master-detail 布局：左侧回路列表 + 右侧单页四区
 *
 * 四区垂直布局（高度占比 16/28/28/28）：
 *   ① 顶部回路摘要（16%）：位号/名称/类型/控制方式/状态 + 趋势/历史按钮
 *   ② 评估行（28%）：8 大指标卡片 + 发起评估 + 72h 综合评分小时趋势
 *   ③ 诊断行（28%）：诊断标签 + 严重度 + 发起诊断
 *   ④ 整定行（28%）：最新辨识模型 + 推荐 PID + 拟合度 + 发起整定
 *
 * 一页内一览概况并可直接发起任务、实时反写。详情走弹窗。
 * 点击左侧回路 → router.replace 更新 URL query，不新增页面/面包屑。
 *
 * 后端零改动：全部组合现有 API
 */
import type { DiagnosisApi } from '#/api/diagnosis';
import type { LoopApi } from '#/api/loop';
import type { KpiSnapshotItem, LoopConfidenceLatestItem } from '#/api/metric';
import type { TuningApi } from '#/api/tuning';

import { computed, onMounted, provide, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';

import { Button, Empty, Input, Spin, Tag } from 'ant-design-vue';
import dayjs from 'dayjs';

import { getDiagnosisDetailApi } from '#/api/diagnosis';
import { getLoopMonitorListApi } from '#/api/loop';
import { getLoopConfidenceLatestApi, getLoopSnapshotsApi } from '#/api/metric';
import { getTuningTasksApi } from '#/api/tuning';
import {
  ClpmAiDrawer,
  ClpmPageToolbar,
  ClpmStandardActions,
} from '#/components/clpm';
import ScoreSparkline from '#/components/metric/score-sparkline.vue';
import { useAiInsightGate } from '#/composables/use-ai-insight-gate';
import { showPageHelp, usePageToolbar } from '#/composables/use-page-toolbar';
import { formatTime } from '#/utils/format';

import AssessTriggerModal from './components/assess-trigger-modal.vue';
import DiagnosisTriggerModal from './components/diagnosis-trigger-modal.vue';
import KpiMetricCards from './components/kpi-metric-cards.vue';
import TuningTriggerModal from './components/tuning-trigger-modal.vue';
import WorkbenchSectionCard from './components/workbench-section-card.vue';
import { useWorkbenchTaskRunner } from './composables/use-workbench-task-runner';

defineOptions({ name: 'LoopWorkbench' });

const route = useRoute();
const router = useRouter();

// ===== 左侧回路列表 =====
const loopList = ref<LoopApi.MonitorListItem[]>([]);
const loopListLoading = ref(false);
const loopListError = ref('');
const searchKeyword = ref('');

// ===== 右侧工作台状态 =====
const selectedLoopId = ref<null | string>(null);
const selectedLoop = computed(() =>
  loopList.value.find((l) => l.loopId === selectedLoopId.value),
);

// ===== 诊断数据（provide 给诊断行 / 顶部摘要共用） =====
const diagnosisDetail = ref<DiagnosisApi.DiagnosisDetail | null>(null);
const diagnosisLoading = ref(false);

async function loadDiagnosis(loopId: string): Promise<void> {
  diagnosisLoading.value = true;
  try {
    diagnosisDetail.value = await getDiagnosisDetailApi(loopId).catch(
      () => null,
    );
  } finally {
    diagnosisLoading.value = false;
  }
}

provide('diagnosisDetail', diagnosisDetail);
provide('diagnosisLoading', diagnosisLoading);
provide('loadDiagnosis', loadDiagnosis);

// ===== 评估数据（provide 给评估行 / KpiMetricCards 共用） =====
const assessmentDetail = ref<LoopConfidenceLatestItem | null>(null);
const assessmentLoading = ref(false);
const scoreHistory = ref<KpiSnapshotItem[]>([]);

async function loadScoreHistory(loopId: string): Promise<KpiSnapshotItem[]> {
  const endTime = dayjs();
  const startTime = endTime.subtract(3, 'day'); // 72h
  const allItems: KpiSnapshotItem[] = [];
  let page = 1;
  const pageLimit = 100;
  let total: number;
  do {
    const res = await getLoopSnapshotsApi({
      loopId,
      startTime: startTime.toISOString(),
      endTime: endTime.toISOString(),
      latestOnly: false,
      page,
      pageSize: pageLimit,
    }).catch(() => ({ items: [], total: 0 }));
    allItems.push(...(res.items || []));
    total = res.total ?? 0;
    page += 1;
  } while ((page - 1) * pageLimit < total);
  return allItems.toSorted((a, b) =>
    (a.tsStart || '').localeCompare(b.tsStart || ''),
  );
}

async function loadAssessment(loopId: string): Promise<void> {
  assessmentLoading.value = true;
  try {
    const [latest, snapshots] = await Promise.all([
      getLoopConfidenceLatestApi(loopId).catch(() => null),
      loadScoreHistory(loopId),
    ]);
    assessmentDetail.value = latest;
    scoreHistory.value = snapshots;
  } finally {
    assessmentLoading.value = false;
  }
}

provide('assessmentDetail', assessmentDetail);
provide('assessmentLoading', assessmentLoading);
provide('scoreHistory', scoreHistory);
provide('loadAssessment', loadAssessment);

// ===== 整定数据（provide 给整定行） =====
const tuningLatest = ref<null | TuningApi.TuningTaskItem>(null);
const tuningLoading = ref(false);
const tuningHistory = ref<TuningApi.TuningTaskItem[]>([]);

async function loadTuning(loopId: string): Promise<void> {
  tuningLoading.value = true;
  try {
    const res = await getTuningTasksApi({
      loopId,
      page: 1,
      pageSize: 10,
    }).catch(() => ({ items: [], total: 0 }));
    const items = (res.items || []).toSorted((a, b) =>
      b.createdAt.localeCompare(a.createdAt),
    );
    tuningHistory.value = items;
    tuningLatest.value = items[0] ?? null;
  } finally {
    tuningLoading.value = false;
  }
}

provide('tuningLatest', tuningLatest);
provide('tuningLoading', tuningLoading);
provide('loadTuning', loadTuning);

// ===== 三区任务运行器 =====
const {
  assessment: assessTask,
  diagnosis: diagTask,
  tuning: tuneTask,
  triggerAssessment,
  triggerDiagnosis,
  triggerTuning,
} = useWorkbenchTaskRunner(
  computed(() => selectedLoopId.value),
  {
    onAssessDone: async (loopId: string) => {
      const [latest, snapshots] = await Promise.all([
        getLoopConfidenceLatestApi(loopId).catch(() => null),
        loadScoreHistory(loopId),
      ]);
      assessmentDetail.value = latest;
      scoreHistory.value = snapshots;
    },
    onDiagnosisDone: async (loopId: string) => {
      diagnosisDetail.value = await getDiagnosisDetailApi(loopId).catch(
        () => null,
      );
    },
    onTuningDone: async (loopId: string) => {
      const res = await getTuningTasksApi({
        loopId,
        page: 1,
        pageSize: 10,
      }).catch(() => ({ items: [], total: 0 }));
      const items = (res.items || []).toSorted((a, b) =>
        b.createdAt.localeCompare(a.createdAt),
      );
      tuningHistory.value = items;
      tuningLatest.value = items[0] ?? null;
    },
  },
);

// ===== 发起弹窗状态 =====
const assessModalOpen = ref(false);
const diagModalOpen = ref(false);
const tuningModalOpen = ref(false);

// ===== 派生：72h 综合评分小时趋势（取整点小时评分值） =====
const score72h = computed(() => {
  const now = dayjs();
  const data = scoreHistory.value.filter((s) => {
    const t = s.tsStart;
    return t && now.diff(dayjs(t), 'hour') <= 72;
  });
  return data.map((s) => (s.score ?? 0) as number);
});

// ===== 派生：整定拟合度趋势 =====
const fittingTrend = computed(() =>
  tuningHistory.value
    .map((t) => t.fittingScore)
    .filter((v): v is number => v !== null && v !== undefined)
    .map((v) => v * 100)
    .toReversed(),
);

// ===== 派生：诊断标签列表 =====
const diagnosisLabels = computed(
  () => diagnosisDetail.value?.diagnosisLabels ?? [],
);
const DIAGNOSIS_LABEL_COLOR_MAP: Record<string, string> = {
  HIGH_OSCILLATION: 'red',
  LOOP_SATURATION: 'volcano',
  STICKY_VALVE: 'orange',
  POOR_TRACKING: 'gold',
  SLUGGISH_RESPONSE: 'blue',
  TIGHT_CONTROL: 'geekblue',
  LOOSE_CONTROL: 'purple',
};
const DIAGNOSIS_LABEL_NAME_MAP: Record<string, string> = {
  HIGH_OSCILLATION: '高频振荡',
  LOOP_SATURATION: '回路饱和',
  STICKY_VALVE: '阀门粘滞',
  POOR_TRACKING: '跟踪不良',
  SLUGGISH_RESPONSE: '响应迟缓',
  TIGHT_CONTROL: '控制过紧',
  LOOSE_CONTROL: '控制过松',
};

// ===== AI 洞察两级门禁 =====
const { gateStatus, gateTooltip, init: initAiGate } = useAiInsightGate();
initAiGate();
const aiDrawerOpen = ref(false);
const aiGateStatus = computed(() => gateStatus(selectedLoopId.value, true));
const aiGateTooltip = computed(() => gateTooltip(aiGateStatus.value));

function handleHelp() {
  showPageHelp({
    title: '回路工作台 帮助',
    content:
      '左侧选择回路，右侧单页展示评估/诊断/整定概况。可直接发起评估、诊断、整定任务，任务完成后自动反写。「AI 洞察」基于当前回路生成性能分析。',
  });
}

// ===== 工具栏 =====
const { toolbarItems } = usePageToolbar(() => ({
  refresh: { onClick: loadLoopList, loading: loopListLoading.value },
  ai: {
    onClick: () => {
      aiDrawerOpen.value = true;
    },
    disabled: aiGateStatus.value !== 'active',
    disabledReason: aiGateTooltip.value,
    tooltip: aiGateTooltip.value || 'AI 洞察',
  },
  help: { onClick: handleHelp },
}));

// ===== 顶部区按钮：趋势 / 历史 =====
function goTrend() {
  if (selectedLoopId.value) {
    router.push({
      path: '/loop/monitor',
      query: { loopId: selectedLoopId.value },
    });
  }
}

function goHistory() {
  if (selectedLoopId.value) {
    router.push({
      path: '/loop/history',
      query: { loopId: selectedLoopId.value },
    });
  }
}

// ===== 整定行辅助 =====
const ALGORITHM_LABEL: Record<string, string> = {
  COHEN_COON: 'Cohen-Coon',
  IMC: 'IMC',
  LAMBDA: 'Lambda',
  SIMC: 'SIMC',
  ZN: 'Ziegler-Nichols',
};

function pidText(pid?: null | TuningApi.PidParams): string {
  if (!pid) return '—';
  return `P=${pid.kp}, Ti=${pid.ti}s, Td=${pid.td}s`;
}

// ===== 数据加载 =====
async function loadLoopList(): Promise<void> {
  loopListLoading.value = true;
  loopListError.value = '';
  try {
    const res = await getLoopMonitorListApi({
      page: 1,
      pageSize: 100,
      keyword: searchKeyword.value || undefined,
    });
    loopList.value = res.items;
    const queryLoopId = route.query.loopId as string | undefined;
    const matched =
      queryLoopId && loopList.value.some((l) => l.loopId === queryLoopId)
        ? queryLoopId
        : (loopList.value[0]?.loopId ?? null);
    if (matched !== selectedLoopId.value) {
      selectLoop(matched);
    } else if (matched === null) {
      selectedLoopId.value = null;
    }
  } catch (error: any) {
    loopListError.value = error?.message ?? '加载回路列表失败';
    loopList.value = [];
  } finally {
    loopListLoading.value = false;
  }
}

function selectLoop(loopId: null | string): void {
  selectedLoopId.value = loopId;
  if (loopId) {
    // router.replace 不新增历史记录/面包屑
    router.replace({ query: { ...route.query, loopId } });
  }
}

let searchTimer: null | ReturnType<typeof setTimeout> = null;
function handleSearchInput(): void {
  if (searchTimer) clearTimeout(searchTimer);
  searchTimer = setTimeout(() => loadLoopList(), 300);
}

// ===== 生命周期 =====
onMounted(() => {
  const queryLoopId = route.query.loopId as string | undefined;
  if (queryLoopId) {
    selectedLoopId.value = queryLoopId;
  }
  loadLoopList();
});

watch(
  () => route.query.loopId,
  (newLoopId) => {
    if (newLoopId && newLoopId !== selectedLoopId.value) {
      selectedLoopId.value = newLoopId as string;
    }
  },
);

// 选中回路变化时加载三区数据
watch(
  selectedLoopId,
  (newId) => {
    if (newId) {
      loadDiagnosis(newId);
      loadAssessment(newId);
      loadTuning(newId);
    } else {
      diagnosisDetail.value = null;
      assessmentDetail.value = null;
      scoreHistory.value = [];
      tuningLatest.value = null;
      tuningHistory.value = [];
    }
  },
  { immediate: true },
);
</script>

<template>
  <Page>
    <ClpmPageToolbar
      title="回路工作台"
      subtitle="单回路 360° 一站式处置"
      :loading="loopListLoading"
    >
      <template #actions>
        <ClpmStandardActions :items="toolbarItems" />
      </template>
    </ClpmPageToolbar>

    <div class="flex h-[calc(100vh-140px)] gap-3">
      <!-- ===== 左侧：回路列表 ===== -->
      <div
        class="flex w-60 shrink-0 flex-col overflow-hidden rounded-lg border bg-white"
      >
        <div class="border-b p-2">
          <Input
            v-model:value="searchKeyword"
            placeholder="搜索回路位号..."
            allow-clear
            size="small"
            @input="handleSearchInput"
            @press-enter="loadLoopList"
          />
        </div>
        <Spin :spinning="loopListLoading" size="small">
          <div class="max-h-[calc(100vh-210px)] overflow-y-auto">
            <div
              v-for="item in loopList"
              :key="item.loopId"
              class="cursor-pointer border-b px-3 py-2 transition-colors last:border-b-0 hover:bg-blue-50"
              :class="{
                'border-l-[3px] border-l-blue-500 bg-blue-50':
                  item.loopId === selectedLoopId,
              }"
              @click="selectLoop(item.loopId)"
            >
              <div class="flex items-center justify-between gap-2">
                <span class="truncate text-sm font-medium">{{
                  item.tagName
                }}</span>
                <span
                  v-if="item.confidenceLevel"
                  class="shrink-0 text-xs font-semibold"
                  :class="{
                    'text-green-600': ['A', 'B'].includes(item.confidenceLevel),
                    'text-orange-500': item.confidenceLevel === 'C',
                    'text-red-500': ['D', 'E'].includes(item.confidenceLevel),
                  }"
                >
                  {{ item.confidenceLevel }}
                </span>
              </div>
              <div class="mt-0.5 flex items-center justify-between gap-2">
                <span class="truncate text-xs text-gray-400">{{
                  item.description || '—'
                }}</span>
                <span class="shrink-0 text-xs text-gray-400"
                  >评分 {{ item.score ?? '—' }}</span
                >
              </div>
            </div>
            <div
              v-if="!loopListLoading && loopListError"
              class="flex flex-col items-center gap-2 py-8 text-center text-xs text-red-500"
            >
              <span>{{ loopListError }}</span>
              <Button size="small" @click="loadLoopList">重试</Button>
            </div>
            <Empty
              v-else-if="!loopListLoading && loopList.length === 0"
              description="暂无回路"
              :image="Empty.PRESENTED_IMAGE_SIMPLE"
              class="py-8"
            />
          </div>
        </Spin>
      </div>

      <!-- ===== 右侧：单页四区垂直布局 ===== -->
      <div class="flex min-w-0 flex-1 flex-col gap-2 overflow-hidden">
        <template v-if="selectedLoop">
          <!-- ① 顶部区 16%：回路摘要 + 趋势/历史按钮 -->
          <div class="wb-top">
            <div class="wb-top__info">
              <span class="wb-top__tag">{{ selectedLoop.tagName }}</span>
              <span class="wb-top__desc">{{
                selectedLoop.description || '—'
              }}</span>
              <span class="wb-top__meta"
                >类型 {{ selectedLoop.loopType || '—' }}</span
              >
              <span class="wb-top__meta"
                >控制 {{ selectedLoop.controlMode || '—' }}</span
              >
              <span class="wb-top__meta"
                >装置 {{ selectedLoop.unitName || '—' }}</span
              >
              <Tag
                v-if="selectedLoop.confidenceLevel"
                :color="
                  ['A', 'B'].includes(selectedLoop.confidenceLevel)
                    ? 'green'
                    : selectedLoop.confidenceLevel === 'C'
                      ? 'gold'
                      : 'red'
                "
              >
                可信度 {{ selectedLoop.confidenceLevel }}
              </Tag>
            </div>
            <div class="wb-top__actions">
              <Button size="small" @click="goTrend">趋势</Button>
              <Button size="small" @click="goHistory">历史</Button>
            </div>
          </div>

          <!-- ② 评估行 28% -->
          <WorkbenchSectionCard
            class="wb-row"
            title="性能评估"
            icon="📊"
            :loading="assessmentLoading"
            :empty="!assessmentLoading && !assessmentDetail"
            empty-text="暂无评估数据"
            :progress="assessTask.isRunning ? (assessTask.progress ?? 0) : null"
            :progress-stage="
              assessTask.isRunning ? assessTask.progressStage : null
            "
          >
            <KpiMetricCards />
            <template #actions>
              <Button
                type="primary"
                size="small"
                :loading="assessTask.isRunning"
                :disabled="assessTask.isRunning"
                @click="assessModalOpen = true"
              >
                {{ assessTask.isRunning ? '评估中…' : '发起评估' }}
              </Button>
            </template>
            <template #chart>
              <div class="wb-chart">
                <div class="wb-chart__label">72h 评分趋势</div>
                <ScoreSparkline
                  v-if="score72h.length >= 2"
                  :data="score72h"
                  :width="200"
                  :height="48"
                />
                <span v-else class="wb-chart__empty">数据不足</span>
              </div>
            </template>
          </WorkbenchSectionCard>

          <!-- ③ 诊断行 28% -->
          <WorkbenchSectionCard
            class="wb-row"
            title="回路诊断"
            icon="🔍"
            :loading="diagnosisLoading"
            :empty="!diagnosisLoading && !diagnosisDetail"
            empty-text="暂无诊断数据"
            :progress="diagTask.isRunning ? (diagTask.progress ?? 0) : null"
            :progress-stage="diagTask.isRunning ? diagTask.progressStage : null"
          >
            <div v-if="diagnosisDetail" class="wb-diag">
              <div class="wb-diag__row">
                <span class="wb-diag__item">
                  综合评分：
                  <span class="font-semibold text-blue-600">
                    {{ Number(diagnosisDetail.compositeScore).toFixed(2) }}
                  </span>
                </span>
                <span class="wb-diag__item">
                  融合置信度：
                  <span class="font-semibold">
                    {{
                      diagnosisDetail.fusedConfidence == null
                        ? '—'
                        : Number(diagnosisDetail.fusedConfidence).toFixed(2)
                    }}
                  </span>
                </span>
                <span class="wb-diag__item">
                  诊断时间：{{ formatTime(diagnosisDetail.diagnosedAt) }}
                </span>
              </div>
              <div class="wb-diag__labels">
                <span class="wb-diag__item">诊断标签：</span>
                <Tag
                  v-for="(item, idx) in diagnosisLabels"
                  :key="idx"
                  :color="DIAGNOSIS_LABEL_COLOR_MAP[item.label] || 'default'"
                >
                  {{
                    item.labelName ||
                    DIAGNOSIS_LABEL_NAME_MAP[item.label] ||
                    item.label
                  }}
                  <span class="ml-1 text-gray-400">
                    {{ Number(item.confidence).toFixed(2) }}
                  </span>
                </Tag>
                <span
                  v-if="diagnosisLabels.length === 0"
                  class="text-xs text-gray-400"
                >
                  未检测到异常标签
                </span>
              </div>
            </div>
            <template #actions>
              <Button
                type="primary"
                size="small"
                :loading="diagTask.isRunning"
                :disabled="diagTask.isRunning"
                @click="diagModalOpen = true"
              >
                {{ diagTask.isRunning ? '诊断中…' : '发起诊断' }}
              </Button>
            </template>
          </WorkbenchSectionCard>

          <!-- ④ 整定行 28% -->
          <WorkbenchSectionCard
            class="wb-row"
            title="回路整定"
            icon="🔧"
            :loading="tuningLoading"
            :empty="!tuningLoading && !tuningLatest"
            empty-text="暂无整定记录"
            :progress="tuneTask.isRunning ? (tuneTask.progress ?? 0) : null"
            :progress-stage="tuneTask.isRunning ? tuneTask.progressStage : null"
          >
            <div v-if="tuningLatest" class="wb-tune">
              <div class="wb-tune__row">
                <span class="wb-tune__item">
                  算法：
                  <span class="font-medium">
                    {{
                      ALGORITHM_LABEL[tuningLatest.algorithm] ||
                      tuningLatest.algorithm
                    }}
                  </span>
                </span>
                <span class="wb-tune__item">
                  模型：{{ tuningLatest.modelType || '—' }}
                </span>
                <span class="wb-tune__item">
                  拟合度：
                  <span class="font-semibold">
                    {{
                      tuningLatest.fittingScore == null
                        ? '—'
                        : `${(tuningLatest.fittingScore * 100).toFixed(1)}%`
                    }}
                  </span>
                </span>
                <span v-if="tuningLatest.confidenceLevel" class="wb-tune__item">
                  可信度：
                  <Tag
                    :color="
                      ['A', 'B'].includes(tuningLatest.confidenceLevel)
                        ? 'green'
                        : tuningLatest.confidenceLevel === 'C'
                          ? 'gold'
                          : 'red'
                    "
                  >
                    {{ tuningLatest.confidenceLevel }}
                  </Tag>
                </span>
                <span class="wb-tune__item">
                  时间：{{ formatTime(tuningLatest.createdAt) }}
                </span>
              </div>
              <div class="wb-tune__pid">
                推荐 PID：
                <span class="font-medium">{{
                  pidText(tuningLatest.recommendedPid)
                }}</span>
              </div>
            </div>
            <template #actions>
              <Button
                type="primary"
                size="small"
                :loading="tuneTask.isRunning"
                :disabled="tuneTask.isRunning"
                @click="tuningModalOpen = true"
              >
                {{ tuneTask.isRunning ? '辨识中…' : '发起整定' }}
              </Button>
            </template>
            <template #chart>
              <div class="wb-chart">
                <div class="wb-chart__label">拟合度趋势</div>
                <ScoreSparkline
                  v-if="fittingTrend.length >= 2"
                  :data="fittingTrend"
                  :width="200"
                  :height="48"
                />
                <span v-else class="wb-chart__empty">数据不足</span>
              </div>
            </template>
          </WorkbenchSectionCard>
        </template>

        <div
          v-else
          class="flex flex-1 items-center justify-center rounded-lg border bg-white"
        >
          <Empty
            description="请从左侧选择回路"
            :image="Empty.PRESENTED_IMAGE_SIMPLE"
            class="py-12"
          />
        </div>
      </div>
    </div>

    <!-- ===== 发起评估弹窗 ===== -->
    <AssessTriggerModal
      v-model:open="assessModalOpen"
      :loop-tag-name="selectedLoop?.tagName"
      @trigger="triggerAssessment"
    />

    <!-- ===== 发起诊断弹窗 ===== -->
    <DiagnosisTriggerModal
      v-model:open="diagModalOpen"
      :loop-tag-name="selectedLoop?.tagName"
      @trigger="triggerDiagnosis"
    />

    <!-- ===== 发起整定弹窗 ===== -->
    <TuningTriggerModal
      v-model:open="tuningModalOpen"
      :loop-tag-name="selectedLoop?.tagName"
      @trigger="triggerTuning"
    />

    <!-- ===== AI 洞察右抽屉 ===== -->
    <ClpmAiDrawer
      v-model:open="aiDrawerOpen"
      scene="performance"
      :loop-id="selectedLoopId"
    />
  </Page>
</template>

<style scoped>
/* 四区垂直布局：顶部 16% + 三行各 28%（gap 用 2*8px=16px 计入） */
.wb-top {
  display: flex;
  flex: 0 0 16%;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  background: #fff;
  border: 1px solid hsl(var(--border) / 60%);
  border-radius: 6px;
}

.wb-top__info {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  min-width: 0;
  font-size: 13px;
}

.wb-top__tag {
  font-size: 16px;
  font-weight: 600;
  color: hsl(var(--foreground));
}

.wb-top__desc {
  color: hsl(var(--foreground) / 60%);
  white-space: nowrap;
}

.wb-top__meta {
  font-size: 12px;
  color: hsl(var(--foreground) / 50%);
  white-space: nowrap;
}

.wb-top__actions {
  display: flex;
  flex-shrink: 0;
  gap: 6px;
}

.wb-row {
  flex: 1 1 0;
  min-height: 0;
}

.wb-diag,
.wb-tune {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 13px;
}

.wb-diag__row,
.wb-tune__row {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
}

.wb-diag__item,
.wb-tune__item {
  color: hsl(var(--foreground) / 70%);
  white-space: nowrap;
}

.wb-diag__labels {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  align-items: center;
}

.wb-tune__pid {
  color: hsl(var(--foreground) / 70%);
}

.wb-chart {
  display: flex;
  flex-direction: column;
  gap: 4px;
  align-items: center;
}

.wb-chart__label {
  font-size: 11px;
  color: hsl(var(--foreground) / 45%);
}

.wb-chart__empty {
  font-size: 11px;
  color: hsl(var(--foreground) / 35%);
}
</style>
