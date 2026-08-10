<script lang="ts" setup>
import type { ComponentPublicInstance } from 'vue';

/**
 * 回路工作台（单页四区重构 v2 · 2026-08-07）
 *
 * 双轴导航 · 实体轴：单回路 360° 一站式处置
 * master-detail 布局：左侧回路列表 + 右侧单页四区
 *
 * 四区垂直布局（概览自适应 + 三行均分）：
 *   ① 回路概览：位号/名称/量程/控制方式/设定值/实时值/数据健康度
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
import type { MonitorApi } from '#/api/monitor';
import type { TuningApi } from '#/api/tuning';

import {
  computed,
  nextTick,
  onMounted,
  onUnmounted,
  provide,
  ref,
  watch,
} from 'vue';
import { useRoute } from 'vue-router';

import { useUserStore } from '@vben/stores';

import { Page } from '@vben/common-ui';

import {
  Button,
  Empty,
  Input,
  message,
  Segmented,
  Spin,
  Tag,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import { getDiagnosisDetailApi } from '#/api/diagnosis';
import { getLoopDetailApi, getLoopMonitorListApi } from '#/api/loop';
import { getLoopConfidenceLatestApi, getLoopSnapshotsApi } from '#/api/metric';
import { getWorkbenchSummaryApi } from '#/api/monitor';
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
import DayDeltaBadge from '#/components/loop/day-delta-badge.vue';
import LoopTrendModal from '#/components/loop/loop-trend-modal.vue';
import LoopFleetView from '#/components/monitor/loop-fleet-view.vue';
import LoopLiveStatusBar from '#/components/monitor/loop-live-status-bar.vue';
import MonitorContextToolbar from '#/components/monitor/monitor-context-toolbar.vue';
import WorkbenchActiveAttention from '#/components/monitor/workbench-active-attention.vue';
import WorkbenchLifecycleBar from '#/components/monitor/workbench-lifecycle-bar.vue';
import WorkbenchNextAction from '#/components/monitor/workbench-next-action.vue';
import WorkbenchTrackerTimeline from '#/components/monitor/workbench-tracker-timeline.vue';
import { useAiInsightGate } from '#/composables/use-ai-insight-gate';
import { useLatestRequest } from '#/composables/use-latest-request';
import { useLoopRealtime } from '#/composables/use-loop-realtime';
import { useMonitorContext } from '#/composables/use-monitor-context';
import { showPageHelp, usePageToolbar } from '#/composables/use-page-toolbar';
import { useSectionVisibility } from '#/composables/use-section-visibility';
import { useVirtualList } from '#/composables/use-virtual-list';
import { formatTime } from '#/utils/format';

import AssessTriggerModal from './components/assess-trigger-modal.vue';
import DiagnosisTriggerModal from './components/diagnosis-trigger-modal.vue';
import KpiMetricCards from './components/kpi-metric-cards.vue';
import ScoreTrendChart from './components/score-trend-chart.vue';
import SimulateResultModal from './components/simulate-result-modal.vue';
import TuneParamModal from './components/tune-param-modal.vue';
import TuningTriggerModal from './components/tuning-trigger-modal.vue';
import WorkbenchDiagnosisChart from './components/workbench-diagnosis-chart.vue';
import WorkbenchSectionCard from './components/workbench-section-card.vue';
import { useWorkbenchTaskRunner } from './composables/use-workbench-task-runner';

defineOptions({ name: 'MonitorLoopWorkbench' });

const route = useRoute();
// router 由 monitorCtx.update 内部调用 router.replace，此页面不再直接使用

// ===== 请求代次保护（MW-P0-04）=====
// 每次切换回路递增 epoch；异步响应写入前校验 epoch+loopId，丢弃旧响应。
const requestGuard = useLatestRequest<string>();

// ===== 区级延迟加载（MW-P3-10）=====
// 评估趋势/诊断波形/整定仿真在可见时加载，避免首屏并发 6 路 API。
// summary + loopDetail 立即加载（轻量概览）；其余三区在进入视口时加载。
const assessVisibility = useSectionVisibility();
const diagVisibility = useSectionVisibility();
const tuneVisibility = useSectionVisibility();
const assessSectionRef = ref<ComponentPublicInstance | null>(null);
const diagSectionRef = ref<ComponentPublicInstance | null>(null);
const tuneSectionRef = ref<ComponentPublicInstance | null>(null);

/** 将组件实例 ref 转为 DOM 元素并注册到可见性追踪器 */
function registerSectionVisibility() {
  nextTick(() => {
    assessVisibility.register(
      (assessSectionRef.value?.$el as Element | undefined) ?? null,
    );
    diagVisibility.register(
      (diagSectionRef.value?.$el as Element | undefined) ?? null,
    );
    tuneVisibility.register(
      (tuneSectionRef.value?.$el as Element | undefined) ?? null,
    );
  });
}

// ===== 共享监控上下文（MW-P1-01）=====
// URL 是真相源：view/loopId/plantNodeId/loopType/keyword/timeWindow 等
const monitorCtx = useMonitorContext();

// ===== MW-P4-02：workspace/table 模式切换 =====
const userStore = useUserStore();
const userRoles = computed(() => userStore.userInfo?.roles ?? []);
/** EXPERT/SPONSOR 无 table 模式权限（对齐 use-saved-view canUseTableViewByRoles） */
const canUseTableView = computed(() =>
  userRoles.value.some((r) =>
    ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER'].includes(r),
  ),
);
const isTableView = computed(
  () => monitorCtx.view.value === 'table' && canUseTableView.value,
);

/**
 * URL 规范化守卫：EXPERT/SPONSOR 直接输入 view=table 时回退到 workspace。
 * 避免渲染无权限的表格组件 + URL 与实际视图一致（MW-P5-03 冒烟测试验证项）。
 */
watch(
  () => monitorCtx.view.value,
  (v) => {
    if (v === 'table' && !canUseTableView.value) {
      monitorCtx.update({ view: 'workspace' });
    }
  },
  { immediate: true },
);

/** 视图模式切换（Segmented） */
const viewModeOptions = computed<
  { label: string; value: 'table' | 'workspace' }[]
>(() => {
  const opts: { label: string; value: 'table' | 'workspace' }[] = [
    { label: '单回路工作台', value: 'workspace' },
  ];
  if (canUseTableView.value) {
    opts.push({ label: '批量表格', value: 'table' });
  }
  return opts;
});

function handleViewChange(val: number | string) {
  monitorCtx.update({ view: val === 'table' ? 'table' : 'workspace' });
}

/** table 模式点击回路 → 切换到 workspace 并携带 loopId */
function handleFleetLoopClick(loopId: string) {
  monitorCtx.update({ view: 'workspace', loopId });
}

// ===== 实时数据（MW-P1-04/05/06）=====
// 复用全局 realtimeWs 单例；WS 断连时 30 秒轮询降级
const {
  applyMessage: applyRealtimeMessage,
  connectionStatus: wsConnectionStatus,
  lastMessageAt: wsLastMessageAt,
  onMessage: onRealtimeMessage,
  start: startRealtime,
  startFallback: startRealtimeFallback,
  stop: stopRealtime,
  stopFallback: stopRealtimeFallback,
} = useLoopRealtime();

// ===== 左侧回路列表 =====
const loopList = ref<LoopApi.MonitorListItem[]>([]);

/**
 * 左栏虚拟滚动（MW-P0-01）：行高 76px（py-2 + 三行文本 + border）。
 * 模板已变为三行（位号/置信度 + 描述/评分 + PV/SP/OP/MODE），57px 会裁切。
 * pageSize=100 时仅渲染可视窗口 + 5 行缓冲，长列表滚动不卡。
 */
const {
  containerRef: loopListRef,
  offsetY: loopListOffsetY,
  onScroll: onLoopListScroll,
  totalHeight: loopListTotalHeight,
  visibleItems: visibleLoopItems,
} = useVirtualList({ itemHeight: 76, items: loopList });

/** 模板函数 ref：把容器元素写入组合式函数的 containerRef（函数 ref 对齐 VNodeRef 类型） */
function setLoopListRef(el: unknown) {
  loopListRef.value = (el as HTMLElement) || null;
}
const loopListLoading = ref(false);
const loopListError = ref('');
const searchKeyword = ref('');

// ===== 右侧工作台状态 =====
const selectedLoopId = ref<null | string>(null);
/** 深链接目标回路（MW-P0-03）：不在当前筛选结果中时，单独显示在上下文区 */
const loopNotFound = ref(false);
/** 深链接回路不在当前筛选结果中时，从精确查询注入的上下文回路 */
const injectedLoop = ref<LoopApi.MonitorListItem | null>(null);
const selectedLoop = computed(
  () =>
    loopList.value.find((l) => l.loopId === selectedLoopId.value) ??
    injectedLoop.value,
);

// ===== 回路详情（提供当前 PID 等运行态参数） =====
const loopDetail = ref<LoopApi.LoopDetail | null>(null);

async function loadLoopDetail(loopId: string): Promise<void> {
  await requestGuard.run(async (signal, capturedEpoch) => {
    const detail = await getLoopDetailApi(loopId).catch(() => null);
    if (signal.aborted || !requestGuard.guard(loopId, capturedEpoch)) return;
    loopDetail.value = detail;
  });
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
  await requestGuard.run(async (signal, capturedEpoch) => {
    const detail = await getDiagnosisDetailApi(loopId).catch(() => null);
    if (signal.aborted || !requestGuard.guard(loopId, capturedEpoch)) {
      return;
    }
    diagnosisDetail.value = detail;
    diagnosisLoading.value = false;
  });
}

provide('diagnosisDetail', diagnosisDetail);
provide('diagnosisLoading', diagnosisLoading);
provide('loadDiagnosis', loadDiagnosis);

// ===== 评估数据（provide 给评估行 / KpiMetricCards / ScoreTrendChart 共用） =====
const assessmentDetail = ref<LoopConfidenceLatestItem | null>(null);
const assessmentLoading = ref(false);
const scoreHistory = ref<KpiSnapshotItem[]>([]);

async function loadScoreHistory(loopId: string): Promise<KpiSnapshotItem[]> {
  // MW-P0-05：移除无上限分页循环。72h 小时快照最多 72 点，
  // 单次请求 pageSize=100 即可覆盖；避免切换回路时循环翻页产生请求风暴。
  const endTime = dayjs();
  const startTime = endTime.subtract(3, 'day'); // 72h
  const res = await getLoopSnapshotsApi({
    loopId,
    startTime: startTime.toISOString(),
    endTime: endTime.toISOString(),
    latestOnly: false,
    sortBy: 'tsStart',
    sortOrder: 'asc',
    page: 1,
    pageSize: 100,
  }).catch(() => ({ items: [], total: 0 }));
  return (res.items || []).toSorted((a, b) =>
    (a.tsStart || '').localeCompare(b.tsStart || ''),
  );
}

async function loadAssessment(loopId: string): Promise<void> {
  assessmentLoading.value = true;
  await requestGuard.run(async (signal, capturedEpoch) => {
    const [latest, snapshots] = await Promise.all([
      getLoopConfidenceLatestApi(loopId).catch(() => null),
      loadScoreHistory(loopId),
    ]);
    if (signal.aborted || !requestGuard.guard(loopId, capturedEpoch)) {
      return;
    }
    assessmentDetail.value = latest;
    scoreHistory.value = snapshots;
    assessmentLoading.value = false;
  });
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
  await requestGuard.run(async (signal, capturedEpoch) => {
    const res = await getTuningTasksApi({
      loopId,
      page: 1,
      pageSize: 10,
    }).catch(() => ({ items: [], total: 0 }));
    if (signal.aborted || !requestGuard.guard(loopId, capturedEpoch)) {
      return;
    }
    const items = (res.items || []).toSorted((a, b) =>
      b.createdAt.localeCompare(a.createdAt),
    );
    tuningHistory.value = items;
    tuningLatest.value = items[0] ?? null;
    // 拉取最新任务详情以获取仿真指标（超调量等）
    const detailId = items[0]?.id;
    const detail = detailId
      ? await getTuningTaskDetailApi(detailId).catch(() => null)
      : null;
    if (signal.aborted || !requestGuard.guard(loopId, capturedEpoch)) {
      return;
    }
    tuningDetail.value = detail;
    tuningLoading.value = false;
  });
}

provide('tuningLatest', tuningLatest);
provide('tuningLoading', tuningLoading);
provide('loadTuning', loadTuning);

// ===== 工作台摘要 summary（MW-P3-05~08）=====
// 首屏一次返回全部摘要（运行态/数据健康度/评分趋势/活跃关注/
// 评估/诊断/整定摘要/Tracker 时间线/生命周期/nextAction）
const summary = ref<MonitorApi.WorkbenchSummary | null>(null);
const summaryLoading = ref(false);

async function loadSummary(loopId: string): Promise<void> {
  summaryLoading.value = true;
  await requestGuard.run(async (signal, capturedEpoch) => {
    const data = await getWorkbenchSummaryApi(loopId).catch(() => null);
    if (signal.aborted || !requestGuard.guard(loopId, capturedEpoch)) {
      return;
    }
    summary.value = data;
    summaryLoading.value = false;
  });
}

/** 生命周期条点击：滚动到对应区 */
function handleLifecycleStageClick(stage: MonitorApi.LifecycleStageName): void {
  const map: Record<MonitorApi.LifecycleStageName, string> = {
    MONITOR: '.wb-overview',
    ASSESS: '.wb-assess',
    DIAGNOSE: '.wb-diag',
    TUNE: '.wb-tune',
    VERIFY: '.wb-verify',
  };
  const selector = map[stage];
  if (selector) {
    const el = document.querySelector(selector);
    el?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
}

/** nextAction 主动作点击：按 actionType 触发对应行为 */
function handleNextAction(actionType: MonitorApi.NextActionType): void {
  switch (actionType) {
    case 'CONTINUE_MONITORING':
    case 'CREATE_TRACKER':
    case 'FIX_TAG_CONFIG':
    case 'IMPORT_DATA':
    case 'RECORD_IMPLEMENTATION':
    case 'VERIFY_EFFECT': {
      // 这些动作由对应区组件处理或跳转
      break;
    }
    case 'RUN_ASSESSMENT': {
      assessModalOpen.value = true;
      break;
    }
    case 'RUN_DIAGNOSIS': {
      diagModalOpen.value = true;
      break;
    }
    case 'RUN_TUNING': {
      tuningModalOpen.value = true;
      break;
    }
    default: {
      break;
    }
  }
}

/** summary 评分趋势的 dayTrend 类型收窄（供 DayDeltaBadge 使用） */
type DayTrend = 'FLAT' | 'IMPROVED' | 'NEW' | 'WORSENED';

const summaryDayTrend = computed<DayTrend | null>(
  () =>
    (summary.value?.scoreTrend.dayTrend as DayTrend | null | undefined) ?? null,
);

// ===== 三区任务运行器（评估/诊断/辨识为异步任务） =====
// MW-P3-10：任务完成后同时刷新 summary（生命周期/nextAction/活跃关注/验证时间线）
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
      // 刷新 summary（生命周期/评分趋势/活跃关注）
      void loadSummary(loopId);
    },
    onDiagnosisDone: async (loopId: string) => {
      diagnosisDetail.value = await getDiagnosisDetailApi(loopId).catch(
        () => null,
      );
      // 刷新 summary（诊断阶段状态/nextAction）
      void loadSummary(loopId);
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
      // 刷新 summary（整定阶段状态/nextAction/trackerTimeline 含 effectCompare）
      void loadSummary(loopId);
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
const pendingTunePayload = ref<null | { algorithm: TuningApi.Algorithm }>(null);

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

function currentValueText(
  value: null | number | undefined,
  unit?: null | string,
) {
  if (value == null) return '—';
  return `${value}${unit ? ` ${unit}` : ''}`;
}

// ===== 派生：整定行字段 =====
const ALGORITHM_LABEL: Record<string, string> = {
  COHEN_COON: 'Cohen-Coon',
  IMC: 'IMC',
  LAMBDA: 'Lambda',
  SIMC: 'SIMC',
  ZN: 'Ziegler-Nichols',
  IDENTIFICATION_ONLY: '仅过程辨识',
};

function confidenceTagColor(level?: string): string {
  if (level === 'A' || level === 'B') return 'green';
  if (level === 'C') return 'blue';
  if (level === 'D') return 'gold';
  return 'default';
}

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
  refresh: {
    onClick: () => {
      void loadLoopList(true);
      // MW-P3-05：手工刷新同时刷新当前摘要
      if (selectedLoopId.value) {
        void loadSummary(selectedLoopId.value);
      }
    },
    loading: loopListLoading.value || summaryLoading.value,
  },
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
/**
 * 加载回路列表并解析深链接（MW-P0-03 / MW-P1-03 服务端分页）。
 *
 * 分页策略（MW-P1-03）：
 * - 默认 pageSize=50，接近底部时加载下一页（无限加载）；
 * - 搜索/筛选变化时清空旧页并回到第 1 页；
 * - 去重键固定为 loopId，重复响应不产生重复条目。
 *
 * 深链接策略（MW-P0-03）：
 * - URL 有 loopId 时先精确查询（不依赖分页是否包含目标）；
 * - 目标存在但不在当前筛选结果中：注入到上下文区，提示"不在当前筛选结果中"；
 * - 目标不存在/已停用/无权限：显示"回路不存在或已停用"，保留 URL，不选择第一条；
 * - 无 loopId 时：选择列表第一条（原有行为）。
 */
const LIST_PAGE_SIZE = 50;
let currentPage = 1;
let hasMorePages = true;

async function loadLoopList(reset = true): Promise<void> {
  if (reset) {
    currentPage = 1;
    hasMorePages = true;
    loopList.value = [];
  }
  if (!hasMorePages) return;

  loopListLoading.value = true;
  loopListError.value = '';
  if (reset) loopNotFound.value = false;
  const queryLoopId = route.query.loopId as string | undefined;
  try {
    const res = await getLoopMonitorListApi({
      page: currentPage,
      pageSize: LIST_PAGE_SIZE,
      keyword: searchKeyword.value || undefined,
      plantNodeId: monitorCtx.plantNodeId.value || undefined,
      loopType: (monitorCtx.loopType.value as LoopApi.LoopType) || undefined,
    });
    // MW-P1-03：去重键 loopId，避免分页重复
    const existing = new Set(loopList.value.map((l) => l.loopId));
    const newItems = res.items.filter((l) => !existing.has(l.loopId));
    loopList.value = reset ? res.items : [...loopList.value, ...newItems];
    hasMorePages = loopList.value.length < (res.total ?? 0);

    if (reset) {
      if (queryLoopId) {
        // 深链接：精确查询目标回路是否存在（不回退其他回路）
        const inList = loopList.value.some((l) => l.loopId === queryLoopId);
        if (inList) {
          injectedLoop.value = null;
          if (queryLoopId !== selectedLoopId.value) {
            selectLoop(queryLoopId);
          }
        } else {
          // 不在当前筛选结果中：单独精确查询，注入上下文区
          const precise = await getLoopMonitorListApi({
            loopId: queryLoopId,
            page: 1,
            pageSize: 1,
          }).catch(() => ({ items: [], total: 0 }));
          if (precise.items.length > 0) {
            injectedLoop.value = precise.items[0]!;
            if (queryLoopId !== selectedLoopId.value) {
              selectLoop(queryLoopId);
            }
          } else {
            // 目标不存在/已停用/无权限：不回退，不选择第一条
            loopNotFound.value = true;
            selectedLoopId.value = null;
            injectedLoop.value = null;
          }
        }
      } else if (selectedLoopId.value === null) {
        // 无深链接且未选中：选择第一条
        const first = loopList.value[0]?.loopId ?? null;
        if (first) {
          selectLoop(first);
        } else {
          selectedLoopId.value = null;
        }
      }
    }
  } catch (error: any) {
    loopListError.value = error?.message ?? '加载回路列表失败';
    if (reset) loopList.value = [];
  } finally {
    loopListLoading.value = false;
  }
}

/** MW-P1-03：无限加载下一页 */
async function loadNextPage(): Promise<void> {
  if (loopListLoading.value || !hasMorePages) return;
  currentPage += 1;
  await loadLoopList(false);
}

/** MW-P1-03：滚动接近底部时加载下一页 */
function handleLoopListScroll(event: Event): void {
  onLoopListScroll(event);
  const target = event.target as HTMLElement;
  const { scrollTop, scrollHeight, clientHeight } = target;
  // 距底部 200px 时预加载下一页
  if (scrollHeight - scrollTop - clientHeight < 200) {
    loadNextPage();
  }
}

function selectLoop(loopId: null | string): void {
  // MW-P0-04：递增请求代次并记录目标，使所有在途响应失效
  requestGuard.bump(loopId);
  loopNotFound.value = false;
  selectedLoopId.value = loopId;
  if (loopId) {
    // MW-P1-01：通过 monitorCtx 更新 URL（router.replace，保留其他筛选上下文）
    monitorCtx.update({ loopId });
  }
}

let searchTimer: null | ReturnType<typeof setTimeout> = null;
function handleSearchInput(): void {
  if (searchTimer) clearTimeout(searchTimer);
  searchTimer = setTimeout(() => loadLoopList(), 300);
}

// ===== 生命周期 =====
// MW-P0-03：不在 onMounted 预设 selectedLoopId——由 loadLoopList 解析深链接，
// 确认目标存在后再 selectLoop，避免对不存在回路发起无用请求。
onMounted(() => {
  loadLoopList();
  // MW-P3-10：注册区级可见性追踪（IntersectionObserver 延迟加载）
  registerSectionVisibility();
  // MW-P1-04/05：启动 WS 连接并注册消息回调
  startRealtime();
  onRealtimeMessage((msg) => {
    // 只更新当前选中回路的实时值（WS 消息按 tagName 匹配）
    if (selectedLoop.value) {
      applyRealtimeMessage(msg, [selectedLoop.value as any]);
    }
  });

  // MW-P1-06：WS 断连时启动 30 秒轮询降级
  // 监听连接状态变化，断连→启动轮询，重连→停止轮询并刷新
  const checkConnection = () => {
    if (wsConnectionStatus.value === 'online') {
      stopRealtimeFallback();
      // 重连成功后主动刷新一次当前回路列表
      loadLoopList();
    } else {
      // 离线或重连中：启动轮询降级
      startRealtimeFallback(async () => {
        await loadLoopList();
      }, 30_000);
    }
  };
  // 初始检查
  checkConnection();
  // 监听变化
  const stopWatch = watch(wsConnectionStatus, checkConnection);

  // 注册清理
  onUnmounted(() => {
    stopWatch();
    stopRealtime();
  });
});

// 浏览器前进/后退触发 loopId 变化时，走 selectLoop（含 epoch bump），
// 保证在途响应不会覆盖新选中回路的数据。
watch(
  () => route.query.loopId,
  (newLoopId) => {
    const next = (newLoopId as string | undefined) ?? null;
    if (next !== selectedLoopId.value) {
      selectLoop(next);
    }
  },
);

// MW-P1-02：筛选条件变化时重新加载列表（回到第 1 页）
watch(
  [() => monitorCtx.plantNodeId.value, () => monitorCtx.loopType.value],
  () => {
    searchKeyword.value = monitorCtx.keyword.value;
    loadLoopList(true);
  },
);

// 选中回路变化时加载数据（MW-P3-05 + MW-P3-10 区级延迟加载）
// summary + loopDetail 立即加载（轻量概览）；评估/诊断/整定在区可见时加载。
watch(
  selectedLoopId,
  (newId) => {
    if (newId) {
      // 立即加载轻量概览数据
      loadLoopDetail(newId);
      loadSummary(newId);
      // 重置区级可见标记——新回路的各区需重新等待可见
      assessVisibility.reset();
      diagVisibility.reset();
      tuneVisibility.reset();
      // 若区已可见（首屏可视区），立即触发加载
      if (assessVisibility.shouldLoad(newId)) loadAssessment(newId);
      if (diagVisibility.shouldLoad(newId)) loadDiagnosis(newId);
      if (tuneVisibility.shouldLoad(newId)) loadTuning(newId);
    } else {
      diagnosisDetail.value = null;
      assessmentDetail.value = null;
      scoreHistory.value = [];
      tuningLatest.value = null;
      tuningHistory.value = [];
      tuningDetail.value = null;
      loopDetail.value = null;
      summary.value = null;
      assessVisibility.reset();
      diagVisibility.reset();
      tuneVisibility.reset();
    }
  },
  { immediate: true },
);

// MW-P3-10：区级可见时触发加载（首次进入视口或切换回路后再次可见）
watch(
  () => assessVisibility.onceVisible.value,
  (visible) => {
    if (visible && selectedLoopId.value) {
      loadAssessment(selectedLoopId.value);
    }
  },
);
watch(
  () => diagVisibility.onceVisible.value,
  (visible) => {
    if (visible && selectedLoopId.value) {
      loadDiagnosis(selectedLoopId.value);
    }
  },
);
watch(
  () => tuneVisibility.onceVisible.value,
  (visible) => {
    if (visible && selectedLoopId.value) {
      loadTuning(selectedLoopId.value);
    }
  },
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
        <!-- MW-P4-02：workspace/table 模式切换 -->
        <Segmented
          :value="monitorCtx.view.value"
          :options="viewModeOptions"
          size="small"
          @change="handleViewChange"
        />
        <!-- MW-P1-02：共享监控工具栏（装置/类型/搜索/保存视图） -->
        <MonitorContextToolbar
          :attention-only-hidden="true"
          page-key="monitor-workbench"
          @filter-change="() => loadLoopList(true)"
        />
        <ClpmStandardActions :items="toolbarItems" />
      </template>
    </ClpmPageToolbar>

    <!-- ===== MW-P4-02/P4-03：批量表格模式（筛选共享 useMonitorContext）===== -->
    <LoopFleetView v-if="isTableView" @loop-click="handleFleetLoopClick" />

    <!-- ===== MW-P4-02：工作台模式（workspace）===== -->
    <template v-if="!isTableView">
      <!-- MW-P1-05：回路实时状态条（PV/SP/OP/MODE + WS 连接状态 + 采样时间） -->
      <LoopLiveStatusBar
        v-if="selectedLoop"
        :loop="selectedLoop"
        :connection-status="wsConnectionStatus"
        :last-message-at="wsLastMessageAt"
        :data-freshness="summary?.dataFreshness"
        class="mb-2"
      />

      <!-- MW-P3-07：生命周期条 + 推荐下一步 + 活跃关注项（首屏 summary 接入） -->
      <template v-if="summary">
        <div class="mb-2 flex flex-col gap-2">
          <WorkbenchLifecycleBar
            :lifecycle="summary.lifecycle"
            :unavailable-sections="summary.unavailableSections"
            @stage-click="handleLifecycleStageClick"
          />
          <!-- emit: stageClick（Vue 模板 @stage-click 自动映射） -->
          <div class="flex gap-2">
            <div class="flex-1">
              <WorkbenchNextAction
                :next-action="summary.nextAction"
                @action="handleNextAction"
              />
            </div>
            <div class="flex-1">
              <WorkbenchActiveAttention
                :active-attention="summary.activeAttention"
                :loop-id="selectedLoopId ?? ''"
              />
            </div>
          </div>
        </div>
      </template>
      <!-- summary 加载中骨架 -->
      <div
        v-else-if="summaryLoading && selectedLoop"
        class="mb-2 flex items-center gap-2 rounded border bg-white px-3 py-2 text-xs text-gray-400"
      >
        <Spin size="small" />
        <span>正在加载工作台摘要…</span>
      </div>

      <div class="flex h-[calc(100vh-180px)] gap-2">
        <!-- ===== 左侧：回路列表（MW-P1-03 服务端分页 + 无限加载） ===== -->
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
              @press-enter="() => loadLoopList(true)"
            />
          </div>
          <Spin :spinning="loopListLoading" size="small">
            <div
              :ref="setLoopListRef"
              class="max-h-[calc(100vh-250px)] overflow-y-auto"
              @scroll="handleLoopListScroll"
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
                    <div
                      class="mt-1 flex items-center gap-2 whitespace-nowrap text-[11px] text-gray-500"
                    >
                      <span
                        >PV
                        {{
                          currentValueText(item.currentValues?.pv, item.pvUnit)
                        }}</span
                      >
                      <span
                        >SP
                        {{
                          currentValueText(item.currentValues?.sp, item.pvUnit)
                        }}</span
                      >
                      <span
                        >OP
                        {{
                          currentValueText(item.currentValues?.op, item.opUnit)
                        }}</span
                      >
                      <span class="truncate">{{
                        item.currentValues?.modeLabel || '模式—'
                      }}</span>
                    </div>
                  </div>
                </div>
              </div>
              <div
                v-if="!loopListLoading && loopListError"
                class="flex flex-col items-center gap-2 py-8 text-center text-xs text-red-500"
              >
                <span>{{ loopListError }}</span>
                <Button size="small" @click="() => loadLoopList(true)"
                  >重试</Button
                >
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

        <!-- ===== 右侧：单页四区垂直布局（概览自适应 + 三行均分） ===== -->
        <div class="flex min-w-0 flex-1 flex-col gap-2 overflow-hidden">
          <!-- MW-P0-03：深链接目标不在当前筛选结果中时提示 -->
          <div
            v-if="selectedLoop && injectedLoop"
            class="rounded border border-amber-300 bg-amber-50 px-3 py-1.5 text-xs text-amber-700"
            role="status"
          >
            当前回路不在筛选结果中，已从深链接定位。可清空筛选以在左侧列表查看。
          </div>
          <!-- MW-P0-03：深链接目标不存在/已停用/无权限 -->
          <div
            v-if="loopNotFound"
            class="flex flex-1 items-center justify-center rounded-lg border bg-white"
          >
            <div class="flex flex-col items-center gap-2 py-12 text-center">
              <Empty
                description="回路不存在或已停用"
                :image="Empty.PRESENTED_IMAGE_SIMPLE"
              />
              <div class="text-xs text-gray-400">
                URL 中的回路 ID
                无效或已停用，已保留原链接。请从左侧选择其他回路。
              </div>
            </div>
          </div>
          <template v-else-if="selectedLoop">
            <!-- ① 回路概览 10% -->
            <WorkbenchSectionCard
              class="wb-overview"
              title="回路概览"
              icon="lucide:activity"
              :loading="false"
              :empty="false"
            >
              <!-- MW-P3-05：summary 评分趋势 + 数据健康度（首屏摘要接入） -->
              <div v-if="summary" class="mb-1 flex items-center gap-3 text-xs">
                <span
                  v-if="summary.scoreTrend.score != null"
                  class="text-gray-600"
                >
                  评分
                  <span class="font-semibold">{{
                    summary.scoreTrend.score.toFixed(1)
                  }}</span>
                  <DayDeltaBadge
                    :delta="summary.scoreTrend.scoreDelta"
                    :trend="summaryDayTrend"
                  />
                </span>
                <span
                  v-if="summary.dataHealth.confidenceLevel"
                  class="text-gray-600"
                >
                  可信度
                  <Tag
                    :color="
                      confidenceTagColor(summary.dataHealth.confidenceLevel)
                    "
                    class="!m-0 !text-[10px]"
                  >
                    {{ summary.dataHealth.confidenceLevel }}
                  </Tag>
                </span>
                <span
                  v-if="summary.dataHealth.validRate != null"
                  class="text-gray-600"
                >
                  有效率 {{ (summary.dataHealth.validRate * 100).toFixed(1) }}%
                </span>
                <span v-if="summary.partial" class="text-amber-600">
                  部分数据不可用
                </span>
              </div>
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
              ref="assessSectionRef"
              class="wb-row wb-assess"
              title="性能评估"
              icon="lucide:chart-column"
              :loading="assessmentLoading"
              :empty="!assessmentLoading && !assessmentDetail"
              empty-text="暂无评估数据"
              :progress="
                assessTask.isRunning ? (assessTask.progress ?? 0) : null
              "
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
              <template #empty>
                <div>当前回路暂无有效评估快照</div>
                <div class="mt-1 text-[11px] text-gray-400">
                  数据范围：近 72 小时；可调整时间窗或发起性能评估
                </div>
              </template>
            </WorkbenchSectionCard>

            <!-- ③ 回路诊断 30%（50/50：标签+置信度 + PV/OP·FFT 曲线） -->
            <WorkbenchSectionCard
              ref="diagSectionRef"
              class="wb-row wb-diag"
              title="回路诊断"
              icon="lucide:stethoscope"
              :loading="diagnosisLoading"
              :empty="!diagnosisLoading && !diagnosisDetail"
              empty-text="暂无诊断数据"
              :progress="diagTask.isRunning ? (diagTask.progress ?? 0) : null"
              :progress-stage="
                diagTask.isRunning ? diagTask.progressStage : null
              "
            >
              <div class="wb-split">
                <div class="wb-split__left">
                  <div v-if="diagnosisDetail" class="wb-diag">
                    <div class="wb-diag__cards">
                      <div class="wb-diag__card">
                        <span class="wb-diag__card-label">综合评分</span>
                        <span class="wb-diag__card-value">
                          {{
                            Number(diagnosisDetail.compositeScore).toFixed(2)
                          }}
                        </span>
                      </div>
                      <div class="wb-diag__card">
                        <span class="wb-diag__card-label">融合置信度</span>
                        <span class="wb-diag__card-value">
                          {{
                            diagnosisDetail.fusedConfidence == null
                              ? '—'
                              : Number(diagnosisDetail.fusedConfidence).toFixed(
                                  2,
                                )
                          }}
                        </span>
                      </div>
                      <div class="wb-diag__card">
                        <span class="wb-diag__card-label">诊断时间</span>
                        <span
                          class="wb-diag__card-value wb-diag__card-value--sm"
                        >
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
              <template #empty>
                <div>当前回路暂无诊断记录</div>
                <div class="mt-1 text-[11px] text-gray-400">
                  需要先具备可用历史数据；可直接发起一次诊断
                </div>
              </template>
            </WorkbenchSectionCard>

            <!-- ④ 回路整定 30%（50/50：当前PID+模型+指标 + 推荐 PID） -->
            <WorkbenchSectionCard
              ref="tuneSectionRef"
              class="wb-row wb-tune"
              title="回路整定"
              icon="lucide:settings-2"
              :loading="tuningLoading"
              :empty="!tuningLoading && !tuningLatest"
              empty-text="暂无整定记录"
              :progress="tuneTask.isRunning ? (tuneTask.progress ?? 0) : null"
              :progress-stage="
                tuneTask.isRunning ? tuneTask.progressStage : null
              "
            >
              <div class="wb-split">
                <div class="wb-split__left">
                  <div class="wb-tune">
                    <div class="wb-tune__row">
                      <span class="wb-tune__item">
                        当前 PID：
                        <span class="font-medium">{{
                          pidText(currentPid)
                        }}</span>
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
                            (tuningLatest?.algorithm ? '未知辨识算法' : '—')
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
                          {{
                            riseTime == null ? '—' : `${riseTime.toFixed(1)}s`
                          }}
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
                            confidenceTagColor(tuningLatest.confidenceLevel)
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
                  :title="
                    tuningLatest
                      ? '基于最新辨识结果生成参数建议'
                      : '请先完成一次回路辨识'
                  "
                  @click="tuneParamModalOpen = true"
                >
                  参数整定
                </Button>
                <Button
                  size="small"
                  :loading="simulateLoading"
                  :disabled="simulateLoading || !tuningLatest"
                  :title="
                    tuningLatest
                      ? '使用最新辨识结果执行只读仿真'
                      : '请先完成一次回路辨识'
                  "
                  @click="requestSimulate"
                >
                  模拟仿真
                </Button>
              </template>
              <template #empty>
                <div>当前回路暂无整定辨识记录</div>
                <div class="mt-1 text-[11px] text-gray-400">
                  参数整定和模拟仿真依赖最新过程辨识结果
                </div>
              </template>
            </WorkbenchSectionCard>

            <!-- ⑤ 闭环验证时间线 30%（MW-P3-08） -->
            <div class="wb-verify wb-row">
              <WorkbenchTrackerTimeline
                :tracker="summary?.trackerTimeline"
                :unavailable="
                  summary?.unavailableSections?.includes('trackerTimeline') ??
                  false
                "
              />
            </div>
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
    </template>
    <!-- ===== /MW-P4-02 工作台模式 ===== -->

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
/* 四区垂直布局：概览自适应高度 + 三行共享剩余空间 */
.wb-overview {
  flex: 0 0 auto;
  min-height: 62px;
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
  height: auto;
  min-height: 36px;
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
