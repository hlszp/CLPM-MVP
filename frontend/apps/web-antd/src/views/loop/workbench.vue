<script lang="ts" setup>
/**
 * 回路工作台（单页四区重构 v2 · 2026-08-07）
 *
 * 双轴导航 · 实体轴：单回路 360° 一站式处置
 * master-detail 布局：左侧回路列表 + 右侧单页四区
 *
 * 四区垂直布局（高度占比 10/30/30/30）：
 *   ① 回路概览（10%）：位号/名称/量程/控制方式/设定值/实时值/数据健康度
 *   ② 性能评估（30%）：12 大指标卡片（50%）+ 评分趋势图（50%，8/12/24/48/72h 可切）
 *   ③ 回路诊断（30%）：诊断标签+置信度卡片（50%）+ PV/OP·FFT 曲线（50%）
 *   ④ 回路整定（30%）：当前 PID/模型/时间常数/超调量（50%）+ 推荐 PID（50%）
 *      按钮：回路辨识 / 参数整定 / 模拟仿真
 *
 * 一页内一览概况并可直接发起任务、实时反写。详情走弹窗。
 * 点击左侧回路 → router.replace 更新 URL query；路由 meta.fullPathKey=false
 * 确保不新增 tab/面包屑，仅更新右侧子页面。
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

import { Button, Empty, Input, message, Spin, Tag } from 'ant-design-vue';
import dayjs from 'dayjs';

import { getDiagnosisDetailApi } from '#/api/diagnosis';
import { getLoopDetailApi, getLoopMonitorListApi } from '#/api/loop';
import { getLoopConfidenceLatestApi, getLoopSnapshotsApi } from '#/api/metric';
import {
  getTuningTaskDetailApi,
  getTuningTasksApi,
  simulateTuningApi,
  tunePidApi,
} from '#/api/tuning';
import {
  ClpmAiDrawer,
  ClpmDangerConfirmModal,
  ClpmPageToolbar,
  ClpmStandardActions,
} from '#/components/clpm';
import { useAiInsightGate } from '#/composables/use-ai-insight-gate';
import { showPageHelp, usePageToolbar } from '#/composables/use-page-toolbar';
import { useVirtualList } from '#/composables/use-virtual-list';
import { formatTime } from '#/utils/format';

import AssessTriggerModal from './components/assess-trigger-modal.vue';
import DiagnosisTriggerModal from './components/diagnosis-trigger-modal.vue';
import KpiMetricCards from './components/kpi-metric-cards.vue';
import LoopTrendModal from '#/components/loop/loop-trend-modal.vue';
import DayDeltaBadge from '#/components/loop/day-delta-badge.vue';
import ScoreTrendChart from './components/score-trend-chart.vue';
import SimulateResultModal from './components/simulate-result-modal.vue';
import TuneParamModal from './components/tune-param-modal.vue';
import TuningTriggerModal from './components/tuning-trigger-modal.vue';
import WorkbenchDiagnosisChart from './components/workbench-diagnosis-chart.vue';
import WorkbenchSectionCard from './components/workbench-section-card.vue';
import { useWorkbenchTaskRunner } from './composables/use-workbench-task-runner';

defineOptions({ name: 'LoopWorkbench' });

const route = useRoute();
const router = useRouter();

// ===== 左侧回路列表 =====
const loopList = ref<LoopApi.MonitorListItem[]>([]);

/**
 * 左栏虚拟滚动（D4）：行高约 57px（py-2 + 两行文本 + border），
 * pageSize=100 时仅渲染可视窗口 + 5 行缓冲，长列表滚动不卡。
 */
const {
  containerRef: loopListRef,
  offsetY: loopListOffsetY,
  onScroll: onLoopListScroll,
  totalHeight: loopListTotalHeight,
  visibleItems: visibleLoopItems,
} = useVirtualList({ itemHeight: 57, items: loopList });

/** 模板函数 ref：把容器元素写入组合式函数的 containerRef（函数 ref 对齐 VNodeRef 类型） */
function setLoopListRef(el: unknown) {
  loopListRef.value = (el as HTMLElement) || null;
}
const loopListLoading = ref(false);
const loopListError = ref('');
const searchKeyword = ref('');

// ===== 右侧工作台状态 =====
const selectedLoopId = ref<null | string>(null);
const selectedLoop = computed(() =>
  loopList.value.find((l) => l.loopId === selectedLoopId.value),
);

// ===== 回路详情（提供当前 PID 等运行态参数） =====
const loopDetail = ref<LoopApi.LoopDetail | null>(null);

async function loadLoopDetail(loopId: string): Promise<void> {
  try {
    loopDetail.value = await getLoopDetailApi(loopId).catch(() => null);
  } catch {
    loopDetail.value = null;
  }
}

// 当前 PID（P/I/D），来自回路运行态参数
const currentPid = computed<null | TuningApi.PidParams>(() => {
  const r = loopDetail.value?.runtimeParams;
  if (!r) return null;
  return { kp: r.pidP, td: r.pidD, ti: r.pidI };
});

// ===== 诊断数据（provide 给诊断行共用） =====
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

// ===== 评估数据（provide 给评估行 / KpiMetricCards / ScoreTrendChart 共用） =====
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
/** 最新整定任务详情（含 simulationResult，用于超调量等指标） */
const tuningDetail = ref<null | TuningApi.TuningTaskDetail>(null);

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
    // 拉取最新任务详情以获取仿真指标（超调量等）
    tuningDetail.value = items[0]?.id
      ? await getTuningTaskDetailApi(items[0].id).catch(() => null)
      : null;
  } finally {
    tuningLoading.value = false;
  }
}

provide('tuningLatest', tuningLatest);
provide('tuningLoading', tuningLoading);
provide('loadTuning', loadTuning);

// ===== 三区任务运行器（评估/诊断/辨识为异步任务） =====
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
      if (items[0]?.id) {
        tuningDetail.value = await getTuningTaskDetailApi(items[0].id).catch(
          () => null,
        );
      }
    },
  },
);

// ===== 发起弹窗状态 =====
const assessModalOpen = ref(false);
const diagModalOpen = ref(false);
const tuningModalOpen = ref(false);
const tuneParamModalOpen = ref(false);
const simulateModalOpen = ref(false);
const simulateResult = ref<null | TuningApi.SimulationResult>(null);

// ===== 参数整定（同步 tunePidApi） =====
// 整改 B2（D1 签认）：不再以 riskConfirmed:true 静默跳过风险确认——
// 先经 ClpmDangerConfirmModal（WARNING 简化级）用户确认后再调 API。
const tuneLoading = ref(false);
const riskConfirmOpen = ref(false);
const riskConfirmKind = ref<'simulate' | 'tune'>('tune');
const pendingTunePayload = ref<{ algorithm: TuningApi.Algorithm } | null>(
  null,
);

const riskConfirmContent = computed(() =>
  riskConfirmKind.value === 'tune'
    ? {
        impactScope:
          '将基于最新辨识模型计算推荐 PID 并反写至整定区。仅输出建议，不会修改 DCS 参数。',
        title: '参数整定计算确认',
      }
    : {
        impactScope:
          '将基于推荐 PID 与当前 PID 做闭环响应对比仿真。仅输出对比结果，不会修改 DCS 参数。',
        title: '闭环仿真计算确认',
      },
);

/** 参数整定入口（TuneParamModal @tune）：前置检查 → 打开确认窗 */
function requestTune(payload: { algorithm: TuningApi.Algorithm }) {
  if (!selectedLoopId.value || !tuningLatest.value) {
    message.warning('请先进行回路辨识生成过程模型');
    return;
  }
  const latest = tuningLatest.value;
  if (!latest.modelType || !latest.modelParams) {
    message.warning('当前无可用过程模型');
    return;
  }
  pendingTunePayload.value = payload;
  riskConfirmKind.value = 'tune';
  riskConfirmOpen.value = true;
}

/** 确认窗确认后执行 */
async function handleRiskConfirm() {
  riskConfirmOpen.value = false;
  if (riskConfirmKind.value === 'tune' && pendingTunePayload.value) {
    await handleTune(pendingTunePayload.value);
  } else if (riskConfirmKind.value === 'simulate') {
    await handleSimulate();
  }
}

async function handleTune(payload: { algorithm: TuningApi.Algorithm }) {
  if (!selectedLoopId.value || !tuningLatest.value) return;
  const latest = tuningLatest.value;
  // 前置检查已在 requestTune 完成，此处仅作类型收窄兜底
  if (!latest.modelType || !latest.modelParams) return;
  tuneLoading.value = true;
  try {
    const result = await tunePidApi({
      algorithm: payload.algorithm,
      currentPid: currentPid.value ?? undefined,
      loopId: selectedLoopId.value,
      modelParams: latest.modelParams,
      modelSource: 'IDENTIFICATION_RECORD',
      modelType: latest.modelType,
      riskConfirmed: true,
      sourceRecordId: latest.id,
    });
    // 反写推荐 PID 到整定行
    tuningLatest.value = { ...latest, recommendedPid: result.recommendedPid };
    message.success('参数整定完成，已更新推荐 PID');
  } catch (error: any) {
    message.error(error?.message ?? '参数整定失败');
  } finally {
    tuneLoading.value = false;
  }
}

// ===== 模拟仿真（同步 simulateTuningApi） =====
const simulateLoading = ref(false);

/** 模拟仿真入口：前置检查 → 打开确认窗 */
function requestSimulate() {
  if (!selectedLoopId.value || !tuningLatest.value) {
    message.warning('请先进行回路辨识生成过程模型');
    return;
  }
  const latest = tuningLatest.value;
  if (!latest.modelType || !latest.modelParams) {
    message.warning('当前无可用过程模型');
    return;
  }
  if (!latest.recommendedPid) {
    message.warning('请先进行参数整定生成推荐 PID');
    return;
  }
  if (!currentPid.value) {
    message.warning('未获取到当前 PID 参数');
    return;
  }
  riskConfirmKind.value = 'simulate';
  riskConfirmOpen.value = true;
}

async function handleSimulate() {
  if (!selectedLoopId.value || !tuningLatest.value) return;
  const latest = tuningLatest.value;
  if (
    !latest.modelType ||
    !latest.modelParams ||
    !latest.recommendedPid ||
    !currentPid.value
  )
    return;
  simulateLoading.value = true;
  try {
    const result = await simulateTuningApi({
      currentPid: currentPid.value,
      loopId: selectedLoopId.value,
      modelParams: latest.modelParams,
      modelSource: 'IDENTIFICATION_RECORD',
      modelType: latest.modelType,
      recommendedPid: latest.recommendedPid,
      riskConfirmed: true,
      sourceRecordId: latest.id,
    });
    simulateResult.value = result;
    simulateModalOpen.value = true;
    message.success('模拟仿真完成');
  } catch (error: any) {
    message.error(error?.message ?? '模拟仿真失败');
  } finally {
    simulateLoading.value = false;
  }
}

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

// ===== 派生：概览区字段 =====
function rangeText(
  range: null | undefined | { max: null | number; min: null | number },
  unit?: null | string,
): string {
  if (!range) return '—';
  const { min, max } = range;
  if (min == null && max == null) return '—';
  return `${min ?? '—'} ~ ${max ?? '—'}${unit ? ` ${unit}` : ''}`;
}

const overviewFields = computed(() => {
  const l = selectedLoop.value;
  if (!l) return [];
  const cv = l.currentValues;
  const dh = l.dataHealth;
  const validRatePct =
    dh?.validRate == null ? null : `${(dh.validRate * 100).toFixed(1)}%`;
  return [
    { label: '位号', value: l.tagName },
    { label: '名称', value: l.description || '—' },
    { label: '量程', value: rangeText(l.pvRange, l.pvUnit) },
    { label: '控制方式', value: l.controlMode || '—' },
    {
      label: '设定值',
      value: cv?.sp == null ? '—' : `${cv.sp}${l.pvUnit ? ` ${l.pvUnit}` : ''}`,
    },
    {
      label: '实时值',
      value: cv?.pv == null ? '—' : `${cv.pv}${l.pvUnit ? ` ${l.pvUnit}` : ''}`,
    },
    {
      label: '数据健康度',
      value:
        validRatePct == null
          ? (dh?.confidenceLevel ?? '—')
          : `${validRatePct} · ${dh?.confidenceLevel ?? '—'}`,
    },
  ];
});

// ===== 派生：整定行字段 =====
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

const modelParamsText = computed(() => {
  const p = tuningLatest.value?.modelParams;
  if (!p) return '—';
  const parts: string[] = [];
  if (p.K != null) parts.push(`K=${Number(p.K).toFixed(3)}`);
  if (p.tau != null) parts.push(`τ=${Number(p.tau).toFixed(1)}s`);
  if (p.theta != null) parts.push(`θ=${Number(p.theta).toFixed(1)}s`);
  return parts.join(' / ') || '—';
});

const overshoot = computed(() => {
  const m = tuningDetail.value?.simulationResult?.recommendedMetrics;
  return m?.overshoot ?? null;
});

/** 上升时间（秒） */
const riseTime = computed(() => {
  const m = tuningDetail.value?.simulationResult?.recommendedMetrics;
  return m?.riseTime ?? null;
});

/** 稳定时间（秒） */
const settlingTime = computed(() => {
  const m = tuningDetail.value?.simulationResult?.recommendedMetrics;
  return m?.settlingTime ?? null;
});

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
      '左侧选择回路，右侧单页展示概览/评估/诊断/整定概况。可直接发起评估、诊断、辨识、整定、仿真任务，任务完成后自动反写。「AI 洞察」基于当前回路生成性能分析。',
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

// ===== 顶部区按钮：趋势（页面内弹窗，复用 LoopTrendModal） =====
// 整改 B1 调整（用户决策）：概览区只保留"趋势"，"历史"按钮下线；
// 趋势不再跳转路由，改为页内弹窗（与回路实时趋势弹窗同组件）。
const trendModalOpen = ref(false);
function goTrend() {
  if (selectedLoopId.value) {
    trendModalOpen.value = true;
  }
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
    // router.replace 不新增历史记录；配合 meta.fullPathKey=false 不新增 tab。
    // 用 name 明确路由，仅保留 loopId query，避免残留参数。
    router.replace({ name: 'LoopWorkbench', query: { loopId } });
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

// 选中回路变化时加载四区数据
watch(
  selectedLoopId,
  (newId) => {
    if (newId) {
      loadDiagnosis(newId);
      loadAssessment(newId);
      loadTuning(newId);
      loadLoopDetail(newId);
    } else {
      diagnosisDetail.value = null;
      assessmentDetail.value = null;
      scoreHistory.value = [];
      tuningLatest.value = null;
      tuningHistory.value = [];
      tuningDetail.value = null;
      loopDetail.value = null;
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

    <div class="flex h-[calc(100vh-140px)] gap-2">
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
          <div
            :ref="setLoopListRef"
            class="max-h-[calc(100vh-210px)] overflow-y-auto"
            @scroll="onLoopListScroll"
          >
            <div
              :style="{
                height: `${loopListTotalHeight}px`,
                position: 'relative',
              }"
            >
              <div :style="{ transform: `translateY(${loopListOffsetY}px)` }">
                <div
                  v-for="{ item } in visibleLoopItems"
                  :key="item.loopId"
                  class="cursor-pointer border-b px-3 py-2 transition-colors last:border-b-0 hover:bg-blue-50"
                  :class="{
                    'border-l-[3px] border-l-blue-500 bg-blue-50':
                      item.loopId === selectedLoopId,
                  }"
                  role="button"
                  tabindex="0"
                  :aria-current="
                    item.loopId === selectedLoopId ? 'true' : undefined
                  "
                  @click="selectLoop(item.loopId)"
                  @keydown.enter="selectLoop(item.loopId)"
                  @keydown.space.prevent="selectLoop(item.loopId)"
                >
                  <div class="flex items-center justify-between gap-2">
                    <span class="truncate text-sm font-medium">{{
                      item.tagName
                    }}</span>
                    <span
                      v-if="item.confidenceLevel"
                      class="shrink-0 text-xs font-semibold"
                      :class="{
                        'text-green-600': ['A', 'B'].includes(
                          item.confidenceLevel,
                        ),
                        'text-orange-500': item.confidenceLevel === 'C',
                        'text-red-500': ['D', 'E'].includes(
                          item.confidenceLevel,
                        ),
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
                      >评分 {{ item.score ?? '—' }}
                      <DayDeltaBadge
                        :delta="item.scoreDelta"
                        :trend="item.dayTrend"
                      />
                    </span>
                  </div>
                </div>
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

      <!-- ===== 右侧：单页四区垂直布局（10/30/30/30） ===== -->
      <div class="flex min-w-0 flex-1 flex-col gap-2 overflow-hidden">
        <template v-if="selectedLoop">
          <!-- ① 回路概览 10% -->
          <WorkbenchSectionCard
            class="wb-overview"
            title="回路概览"
            icon="🛰️"
            :loading="false"
            :empty="false"
          >
            <div class="wb-overview__grid">
              <div
                v-for="field in overviewFields"
                :key="field.label"
                class="wb-overview__field"
              >
                <span class="wb-overview__label">{{ field.label }}</span>
                <span class="wb-overview__value">{{ field.value }}</span>
              </div>
            </div>
            <template #actions>
              <Button size="small" @click="goTrend">趋势</Button>
            </template>
          </WorkbenchSectionCard>

          <!-- ② 性能评估 30%（50/50：12 卡片 + 评分趋势） -->
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
            <div class="wb-split">
              <div class="wb-split__left">
                <KpiMetricCards />
              </div>
              <div class="wb-split__right">
                <ScoreTrendChart />
              </div>
            </div>
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
          </WorkbenchSectionCard>

          <!-- ③ 回路诊断 30%（50/50：标签+置信度 + PV/OP·FFT 曲线） -->
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
            <div class="wb-split">
              <div class="wb-split__left">
                <div v-if="diagnosisDetail" class="wb-diag">
                  <div class="wb-diag__cards">
                    <div class="wb-diag__card">
                      <span class="wb-diag__card-label">综合评分</span>
                      <span class="wb-diag__card-value">
                        {{ Number(diagnosisDetail.compositeScore).toFixed(2) }}
                      </span>
                    </div>
                    <div class="wb-diag__card">
                      <span class="wb-diag__card-label">融合置信度</span>
                      <span class="wb-diag__card-value">
                        {{
                          diagnosisDetail.fusedConfidence == null
                            ? '—'
                            : Number(diagnosisDetail.fusedConfidence).toFixed(2)
                        }}
                      </span>
                    </div>
                    <div class="wb-diag__card">
                      <span class="wb-diag__card-label">诊断时间</span>
                      <span class="wb-diag__card-value wb-diag__card-value--sm">
                        {{ formatTime(diagnosisDetail.diagnosedAt) }}
                      </span>
                    </div>
                  </div>
                  <div class="wb-diag__labels">
                    <Tag
                      v-for="(item, idx) in diagnosisLabels"
                      :key="idx"
                      :color="
                        DIAGNOSIS_LABEL_COLOR_MAP[item.label] || 'default'
                      "
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
              </div>
              <div class="wb-split__right">
                <WorkbenchDiagnosisChart :loop-id="selectedLoopId" />
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

          <!-- ④ 回路整定 30%（50/50：当前PID+模型+指标 + 推荐 PID） -->
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
            <div class="wb-split">
              <div class="wb-split__left">
                <div class="wb-tune">
                  <div class="wb-tune__row">
                    <span class="wb-tune__item">
                      当前 PID：
                      <span class="font-medium">{{ pidText(currentPid) }}</span>
                    </span>
                    <span class="wb-tune__item">
                      模型：{{ tuningLatest?.modelType || '—' }}
                    </span>
                    <span class="wb-tune__item">
                      时间常数/参数：{{ modelParamsText }}
                    </span>
                  </div>
                  <div class="wb-tune__row">
                    <span class="wb-tune__item">
                      算法：
                      <span class="font-medium">
                        {{
                          ALGORITHM_LABEL[tuningLatest?.algorithm || ''] ||
                          tuningLatest?.algorithm ||
                          '—'
                        }}
                      </span>
                    </span>
                    <span class="wb-tune__item">
                      拟合度：
                      <span class="font-semibold">
                        {{
                          tuningLatest?.fittingScore == null
                            ? '—'
                            : `${(tuningLatest.fittingScore * 100).toFixed(1)}%`
                        }}
                      </span>
                    </span>
                    <span class="wb-tune__item">
                      超调量：
                      <span class="font-semibold">
                        {{
                          overshoot == null ? '—' : `${overshoot.toFixed(2)}%`
                        }}
                      </span>
                    </span>
                    <span class="wb-tune__item">
                      上升时间：
                      <span class="font-semibold">
                        {{ riseTime == null ? '—' : `${riseTime.toFixed(1)}s` }}
                      </span>
                    </span>
                    <span class="wb-tune__item">
                      稳定时间：
                      <span class="font-semibold">
                        {{
                          settlingTime == null
                            ? '—'
                            : `${settlingTime.toFixed(1)}s`
                        }}
                      </span>
                    </span>
                    <span
                      v-if="tuningLatest?.confidenceLevel"
                      class="wb-tune__item"
                    >
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
                  </div>
                </div>
              </div>
              <div class="wb-split__right">
                <div class="wb-tune__recommend">
                  <div class="wb-tune__recommend-label">推荐 PID</div>
                  <div class="wb-tune__recommend-value">
                    {{ pidText(tuningLatest?.recommendedPid) }}
                  </div>
                  <div class="wb-tune__recommend-time">
                    更新时间：{{
                      tuningLatest ? formatTime(tuningLatest.createdAt) : '—'
                    }}
                  </div>
                </div>
              </div>
            </div>
            <template #actions>
              <Button
                size="small"
                :loading="tuneTask.isRunning"
                :disabled="tuneTask.isRunning"
                @click="tuningModalOpen = true"
              >
                {{ tuneTask.isRunning ? '辨识中…' : '回路辨识' }}
              </Button>
              <Button
                size="small"
                :loading="tuneLoading"
                :disabled="tuneLoading || !tuningLatest"
                @click="tuneParamModalOpen = true"
              >
                参数整定
              </Button>
              <Button
                size="small"
                :loading="simulateLoading"
                :disabled="simulateLoading || !tuningLatest"
                @click="requestSimulate"
              >
                模拟仿真
              </Button>
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

    <!-- ===== 回路辨识弹窗 ===== -->
    <TuningTriggerModal
      v-model:open="tuningModalOpen"
      :loop-tag-name="selectedLoop?.tagName"
      @trigger="triggerTuning"
    />

    <!-- ===== 参数整定弹窗 ===== -->
    <TuneParamModal
      v-model:open="tuneParamModalOpen"
      :loop-tag-name="selectedLoop?.tagName"
      :model-type="tuningLatest?.modelType ?? null"
      :model-params="tuningLatest?.modelParams ?? null"
      :current-pid="currentPid"
      @tune="requestTune"
    />

    <!-- ===== 整定/仿真风险确认窗（整改 B2，WARNING 简化级） ===== -->
    <ClpmDangerConfirmModal
      v-model:open="riskConfirmOpen"
      :title="riskConfirmContent.title"
      action="计算"
      :impact-scope="riskConfirmContent.impactScope"
      rollback-tip="安全边界：只读建议 · 人工实施 · 需留痕；本平台不直接修改 DCS 的 P/I/D 参数。"
      :require-confirm-code="false"
      :require-reason="false"
      :show-audit-note="false"
      confirm-text="确认计算"
      :loading="tuneLoading || simulateLoading"
      @confirm="handleRiskConfirm"
    />

    <!-- ===== 模拟仿真结果弹窗 ===== -->
    <SimulateResultModal
      v-model:open="simulateModalOpen"
      :loop-tag-name="selectedLoop?.tagName"
      :result="simulateResult"
    />

    <!-- ===== 趋势弹窗（整改 B1：页内弹窗，复用 LoopTrendModal） ===== -->
    <LoopTrendModal
      v-model:open="trendModalOpen"
      :loop-id="selectedLoopId"
      :tag-name="selectedLoop?.tagName"
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
/* 四区垂直布局：概览 10% + 三行各 30%（gap 用 3*8px=24px 计入，行共享剩余空间） */
.wb-overview {
  flex: 0 0 10%;
  min-height: 0;
}

.wb-row {
  flex: 1 1 0;
  min-height: 0;
}

/* 概览区字段网格（紧凑单行） */
.wb-overview__grid {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 20px;
  align-items: center;
  height: 100%;
  font-size: 13px;
}

.wb-overview__field {
  display: flex;
  gap: 4px;
  align-items: baseline;
  white-space: nowrap;
}

.wb-overview__label {
  font-size: 12px;
  color: hsl(var(--foreground) / 45%);
}

.wb-overview__value {
  font-weight: 500;
  color: hsl(var(--foreground) / 85%);
}

/* 通用 50/50 分栏 */
.wb-split {
  display: flex;
  gap: 12px;
  height: 100%;
  min-height: 0;
}

.wb-split__left {
  display: flex;
  flex: 1 1 50%;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
}

.wb-split__right {
  display: flex;
  flex: 1 1 50%;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  padding: 4px;
  background: hsl(var(--muted) / 20%);
  border: 1px solid hsl(var(--border) / 40%);
  border-radius: 4px;
}

/* 诊断行 */
.wb-diag {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 13px;
}

.wb-diag__cards {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.wb-diag__card {
  display: flex;
  flex: 1 1 30%;
  flex-direction: column;
  gap: 2px;
  padding: 4px 8px;
  background: hsl(var(--muted) / 30%);
  border: 1px solid hsl(var(--border) / 50%);
  border-radius: 4px;
}

.wb-diag__card-label {
  font-size: 11px;
  color: hsl(var(--foreground) / 50%);
}

.wb-diag__card-value {
  font-size: 16px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: hsl(var(--foreground));
}

.wb-diag__card-value--sm {
  font-size: 12px;
  font-weight: 500;
}

.wb-diag__labels {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  align-items: center;
}

/* 整定行 */
.wb-tune {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 13px;
}

.wb-tune__row {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
}

.wb-tune__item {
  color: hsl(var(--foreground) / 70%);
  white-space: nowrap;
}

.wb-tune__recommend {
  display: flex;
  flex-direction: column;
  gap: 6px;
  justify-content: center;
  height: 100%;
}

.wb-tune__recommend-label {
  font-size: 12px;
  color: hsl(var(--foreground) / 50%);
}

.wb-tune__recommend-value {
  font-size: 16px;
  font-weight: 600;
  color: hsl(var(--primary));
}

.wb-tune__recommend-time {
  font-size: 11px;
  color: hsl(var(--foreground) / 45%);
}
</style>
