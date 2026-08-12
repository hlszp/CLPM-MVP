<script lang="ts" setup>
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
import type {
  KpiSnapshotItem,
  LoopConfidenceLatestItem,
  MetricApi,
} from '#/api/metric';
import type { MonitorApi } from '#/api/monitor';
import type { PlantNodeApi } from '#/api/plant-node';
import type { TuningApi } from '#/api/tuning';

import { computed, onMounted, onUnmounted, provide, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';
import { useUserStore } from '@vben/stores';

import {
  Button,
  Empty,
  Input,
  message,
  Segmented,
  Spin,
  Tag,
  Tooltip,
  Tree,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import { getDiagnosisDetailApi } from '#/api/diagnosis';
import {
  getLoopDetailApi,
  getLoopMonitorDetailApi,
  getLoopMonitorListApi,
} from '#/api/loop';
import {
  getGradingThresholdsApi,
  getLoopConfidenceLatestApi,
  getLoopSnapshotsApi,
} from '#/api/metric';
import { getWorkbenchSummaryApi } from '#/api/monitor';
import { getPlantNodeTreeApi } from '#/api/plant-node';
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
import LoopFleetView from '#/components/monitor/loop-fleet-view.vue';
import MonitorContextToolbar from '#/components/monitor/monitor-context-toolbar.vue';
import WorkbenchActiveAttention from '#/components/monitor/workbench-active-attention.vue';
import WorkbenchNextAction from '#/components/monitor/workbench-next-action.vue';
import WorkbenchTrackerTimeline from '#/components/monitor/workbench-tracker-timeline.vue';
import { useAiInsightGate } from '#/composables/use-ai-insight-gate';
import { useLatestRequest } from '#/composables/use-latest-request';
import { useLoopRealtime } from '#/composables/use-loop-realtime';
import { useMonitorContext } from '#/composables/use-monitor-context';
import { showPageHelp, usePageToolbar } from '#/composables/use-page-toolbar';
import { useScoreColor } from '#/composables/use-score-color';
import { useVirtualList } from '#/composables/use-virtual-list';
import { formatTime } from '#/utils/format';

import AssessTriggerModal from './components/assess-trigger-modal.vue';
import DiagnosisTriggerModal from './components/diagnosis-trigger-modal.vue';
import SimulateResultModal from './components/simulate-result-modal.vue';
import TuneParamModal from './components/tune-param-modal.vue';
import TuningTriggerModal from './components/tuning-trigger-modal.vue';
import WorkbenchEffectCompare from './components/workbench-effect-compare.vue';
import WorkbenchKpiHistory from './components/workbench-kpi-history.vue';
import WorkbenchMetricBars from './components/workbench-metric-bars.vue';
import WorkbenchProcessTrend from './components/workbench-process-trend.vue';
import WorkbenchRadar6 from './components/workbench-radar6.vue';
import { useWorkbenchTaskRunner } from './composables/use-workbench-task-runner';

defineOptions({ name: 'MonitorLoopWorkbench' });

const route = useRoute();
const router = useRouter();
// router 由 monitorCtx.update 内部调用 router.replace，此页面不再直接使用

// ===== 请求代次保护（MW-P0-04）=====
// 每次切换回路递增 epoch；异步响应写入前校验 epoch+loopId，丢弃旧响应。
const requestGuard = useLatestRequest<string>();

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

// ===== 左脊柱装置树（仅到装置/单元级，回路列表独立成区）=====
interface PlantTreeNode {
  key: string;
  title: string;
  nodeType: PlantNodeApi.NodeType;
  children?: PlantTreeNode[];
}
const plantTreeData = ref<PlantTreeNode[]>([]);
const plantTreeLoading = ref(false);
const plantTreeExpandedKeys = ref<string[]>([]);
const plantTreeSelectedKeys = ref<string[]>([]);

/** 将 PlantNodeApi.PlantNode 转为 ant Tree 节点（仅 FACTORY → AREA → UNIT） */
function buildPlantTree(nodes: PlantNodeApi.PlantNode[]): PlantTreeNode[] {
  return nodes.map((n) => ({
    children: n.children?.length ? buildPlantTree(n.children) : undefined,
    key: n.id,
    nodeType: n.type,
    title: n.name,
  }));
}

async function loadPlantTree(): Promise<void> {
  plantTreeLoading.value = true;
  try {
    const tree = await getPlantNodeTreeApi();
    plantTreeData.value = buildPlantTree(tree);
    // 默认展开第一层（工厂）
    plantTreeExpandedKeys.value = tree.map((n) => n.id);
  } catch {
    plantTreeData.value = [];
  } finally {
    plantTreeLoading.value = false;
  }
}

/** 装置树节点选中：更新 plantNodeId 过滤回路列表 */
function handlePlantTreeSelect(keys: (number | string)[]): void {
  const key = keys[0] as string | undefined;
  plantTreeSelectedKeys.value = key ? [key] : [];
  monitorCtx.update({ plantNodeId: key ?? '' });
}

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
  await requestGuard.run(async (_signal, capturedEpoch) => {
    const detail = await getLoopDetailApi(loopId).catch(() => null);
    if (!requestGuard.guard(loopId, capturedEpoch)) return;
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
  await requestGuard.run(async (_signal, capturedEpoch) => {
    const detail = await getDiagnosisDetailApi(loopId).catch(() => null);
    if (!requestGuard.guard(loopId, capturedEpoch)) {
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
  await requestGuard.run(async (_signal, capturedEpoch) => {
    const [latest, snapshots] = await Promise.all([
      getLoopConfidenceLatestApi(loopId).catch(() => null),
      loadScoreHistory(loopId),
    ]);
    if (!requestGuard.guard(loopId, capturedEpoch)) {
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
  await requestGuard.run(async (_signal, capturedEpoch) => {
    const res = await getTuningTasksApi({
      loopId,
      page: 1,
      pageSize: 10,
    }).catch(() => ({ items: [], total: 0 }));
    if (!requestGuard.guard(loopId, capturedEpoch)) {
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
    if (!requestGuard.guard(loopId, capturedEpoch)) {
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
  await requestGuard.run(async (_signal, capturedEpoch) => {
    const data = await getWorkbenchSummaryApi(loopId).catch(() => null);
    if (!requestGuard.guard(loopId, capturedEpoch)) return;

    // === MOCK 开关：测试验证卡数据展示（测试完改为 false） ===
    const USE_MOCK = false;
    if (USE_MOCK && data) {
      const { getMockSummary } = await import('./components/workbench-mock');
      const mock = getMockSummary();
      summary.value = {
        ...data,
        trackerTimeline: mock.trackerTimeline,
        nextAction: mock.nextAction ?? data.nextAction,
        lifecycle: mock.lifecycle ?? data.lifecycle,
        activeAttention: mock.activeAttention ?? data.activeAttention,
      };
      summaryLoading.value = false;
      return;
    }

    summary.value = data;
    summaryLoading.value = false;
  });
}

/** 生命周期条点击：滚动到对应 R 区 */
function handleLifecycleStageClick(stage: MonitorApi.LifecycleStageName): void {
  const map: Record<MonitorApi.LifecycleStageName, string> = {
    ASSESS: '.wb-r5__card--assess',
    DIAGNOSE: '.wb-r5__card--diag',
    MONITOR: '.wb-r1',
    TUNE: '.wb-r5__card--tune',
    VERIFY: '.wb-r6',
  };
  const selector = map[stage];
  if (selector) {
    const el = document.querySelector(selector);
    el?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
}

/** nextAction 主动作点击：按 actionType 触发对应行为（FP-P0-01：9 类动作全部接通） */
function handleNextAction(actionType: MonitorApi.NextActionType): void {
  const loopId = selectedLoopId.value;
  switch (actionType) {
    case 'CONTINUE_MONITORING': {
      message.info('回路当前无开放问题，持续监控中');
      break;
    }
    case 'CREATE_TRACKER': {
      if (!loopId) return;
      router.push({ path: '/diagnosis/tracker', query: { loopId } });
      break;
    }
    case 'FIX_TAG_CONFIG': {
      router.push({ path: '/config/loop', query: loopId ? { loopId } : {} });
      break;
    }
    case 'IMPORT_DATA': {
      router.push({
        path: '/config/datasource',
        query: loopId ? { loopId } : {},
      });
      break;
    }
    case 'RECORD_IMPLEMENTATION': {
      if (!loopId) return;
      router.push({ path: '/diagnosis/tracker', query: { loopId } });
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
    case 'VERIFY_EFFECT': {
      if (!loopId) return;
      router.push({ path: '/diagnosis/tracker', query: { loopId } });
      break;
    }
    default: {
      break;
    }
  }
}

/** 闭环时间线：查看 Tracker 详情（FP-P0-02：事件绑定修复） */
function handleTrackerViewDetail(trackerId: string) {
  const loopId = selectedLoopId.value;
  router.push({
    path: '/diagnosis/tracker',
    query: { loopId: loopId ?? undefined, trackerId },
  });
}

/** 闭环时间线：进入效果验证（FP-P0-02：事件绑定修复） */
function handleTrackerVerify(trackerId: string) {
  const loopId = selectedLoopId.value;
  router.push({
    path: '/diagnosis/tracker',
    query: { loopId: loopId ?? undefined, trackerId, action: 'verify' },
  });
}

/** summary 评分趋势的 dayTrend 类型收窄（供 DayDeltaBadge 使用） */
type DayTrend = 'FLAT' | 'IMPROVED' | 'NEW' | 'WORSENED';

const summaryDayTrend = computed<DayTrend | null>(
  () =>
    (summary.value?.scoreTrend.dayTrend as DayTrend | null | undefined) ?? null,
);

// ===== 三区任务运行器（评估/诊断/辨识为异步任务） =====
// MW-P3-10：任务完成后同时刷新 summary（生命周期/nextAction/活跃关注/验证时间线）
const { triggerAssessment, triggerDiagnosis, triggerTuning } =
  useWorkbenchTaskRunner(
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
  OSCILLATION: 'red',
  VALVE_STICTION: 'orange',
  OVERAGGRESSIVE: 'volcano',
  OVERCONSERVATIVE: 'gold',
  EXTERNAL_DISTURBANCE: 'blue',
  QUALITY_ABNORMAL: 'magenta',
  OUTPUT_SATURATION: 'purple',
  MANUAL_REVIEW: 'default',
};
const DIAGNOSIS_LABEL_NAME_MAP: Record<string, string> = {
  OSCILLATION: '振荡',
  VALVE_STICTION: '阀门粘滞',
  OVERAGGRESSIVE: '参数过激',
  OVERCONSERVATIVE: '参数过保守',
  EXTERNAL_DISTURBANCE: '外扰频繁',
  QUALITY_ABNORMAL: 'PV 质量异常',
  OUTPUT_SATURATION: '输出饱和',
  MANUAL_REVIEW: '人工复核',
};

/**
 * 诊断标签 → 关键特征值字段映射（对齐后端 ALGORITHM_META_STATIC.featureKeys）。
 * 每个标签提取 2-4 个最有诊断价值的特征值展示。
 * featureKey 别名兼容 data_simulator 实际写入的 key。
 */
const DIAG_FEATURE_DEFS: Record<
  string,
  { key: string; aliases?: string[]; label: string; unit?: string; fmt?: (v: number) => string }[]
> = {
  OSCILLATION: [
    { key: 'oscillation_index', label: '振荡指数', fmt: (v) => v.toFixed(3) },
    { key: 'oscillation_frequency', aliases: ['dominant_freq', 'frequency_hz', 'peak_frequency_hz'], label: '主频', unit: 'Hz', fmt: (v) => v.toFixed(4) },
    { key: 'oscillation_amplitude', aliases: ['amplitude'], label: '振幅', fmt: (v) => v.toFixed(2) },
    { key: 'iae_similarity', aliases: ['similarity'], label: 'IAE 相似度', fmt: (v) => `${(v * 100).toFixed(0)}%` },
  ],
  VALVE_STICTION: [
    { key: 'stiction_index', label: '粘滞指数', fmt: (v) => v.toFixed(3) },
    { key: 'fitting_score', aliases: ['r2'], label: '拟合度', fmt: (v) => `${(v * 100).toFixed(1)}%` },
    { key: 'ngi', label: 'NGI', fmt: (v) => v.toFixed(3) },
    { key: 'nli', label: 'NLI', fmt: (v) => v.toFixed(3) },
  ],
  OVERAGGRESSIVE: [
    { key: 'overshoot', aliases: ['overshoot_pct'], label: '超调量', unit: '%', fmt: (v) => v.toFixed(1) },
    { key: 'decay_ratio', label: '衰减比', fmt: (v) => v.toFixed(2) },
    { key: 'harris_index', label: 'Harris 指数', fmt: (v) => v.toFixed(2) },
  ],
  OVERCONSERVATIVE: [
    { key: 'time_constant', aliases: ['settling_time_s'], label: '时间常数', unit: 's', fmt: (v) => v.toFixed(0) },
    { key: 'expected_time_constant', label: '期望时间常数', unit: 's', fmt: (v) => v.toFixed(0) },
    { key: 'ratio', aliases: ['iae_ratio'], label: '比值', fmt: (v) => v.toFixed(2) },
  ],
  EXTERNAL_DISTURBANCE: [
    { key: 'shift_count', label: '漂移次数', fmt: (v) => v.toFixed(0) },
    { key: 'max_cusum', label: '最大累积和', fmt: (v) => v.toFixed(2) },
  ],
  QUALITY_ABNORMAL: [
    { key: 'bad_quality_rate', label: '坏值率', fmt: (v) => `${(v * 100).toFixed(1)}%` },
    { key: 'bad_points', label: '坏值点数', fmt: (v) => v.toFixed(0) },
  ],
  OUTPUT_SATURATION: [
    { key: 'saturation_rate', label: '饱和率', fmt: (v) => `${(v * 100).toFixed(1)}%` },
    { key: 'high_saturation_count', label: '高限次数', fmt: (v) => v.toFixed(0) },
    { key: 'low_saturation_count', label: '低限次数', fmt: (v) => v.toFixed(0) },
  ],
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
// rangeText 保留用于未来概览字段扩展
void rangeText;
function currentValueText(
  value: null | number | undefined,
  unit?: null | string,
) {
  if (value == null) return '—';
  return `${value}${unit ? ` ${unit}` : ''}`;
}

// ===== 派生：整定行字段 =====
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
  loadPlantTree();
  // 加载定级阈值（动态配置，降级 GB/T 44693.2 §6.3 默认）
  getGradingThresholdsApi()
    .then((res) => {
      gradingThresholds.value = res?.thresholds ?? [];
    })
    .catch(() => {
      gradingThresholds.value = [];
    });
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

// R4 画布 loading 状态（提前声明，供 watch else 分支重置）
const processTrendLoading = ref(false);
const kpiHistoryLoading = ref(false);

// 选中回路变化时加载数据（三栏布局：R5 证据区始终可见，无需延迟加载）
// summary + loopDetail + 评估/诊断/整定数据一并加载。
watch(
  selectedLoopId,
  (newId) => {
    if (newId) {
      loadLoopDetail(newId);
      loadSummary(newId);
      loadAssessment(newId);
      loadDiagnosis(newId);
      loadTuning(newId);
    } else {
      diagnosisDetail.value = null;
      assessmentDetail.value = null;
      scoreHistory.value = [];
      tuningLatest.value = null;
      tuningHistory.value = [];
      tuningDetail.value = null;
      loopDetail.value = null;
      summary.value = null;
      // 重置 loading 状态（guard 取消时不会重置，此处兜底）
      summaryLoading.value = false;
      diagnosisLoading.value = false;
      assessmentLoading.value = false;
      tuningLoading.value = false;
    }
  },
  { immediate: true },
);

// ===== Phase 1 重构：R4 主画布数据（过程趋势 + KPI 历史）=====
// 过程趋势数据（GET /loops/{id}/monitor 的 trend 字段）
const processTrendData = ref<LoopApi.MonitorTrend | null>(null);
// KPI 历史快照（用于 R4 性能指标模式）
const kpiHistory = ref<KpiSnapshotItem[]>([]);
// 画布模式：过程变量 | 性能指标
const canvasMode = ref<'kpi' | 'process'>('process');
// 时间窗（设计文档 §2.4：8h/24h/72h/168h/自定义）
const timeWindow = ref<'8h' | '24h' | '72h' | '168h' | 'custom'>('24h');
// 自定义时间窗起止（分钟精度）
const customStartTime = ref<string>('');
const customEndTime = ref<string>('');
// 定级阈值（动态加载，设计红线：禁止硬编码）
const gradingThresholds = ref<MetricApi.GradingThresholdItem[]>([]);
// 左脊柱折叠（沉浸模式）
const sidebarCollapsed = ref(false);

/** 事件标记类型 */
interface ProcessEventMark {
  type: 'diagnosis' | 'gap' | 'tuning' | 'verify';
  timestamp: number;
  label?: string;
}

/** MODE 背景带 */
interface ModeBand {
  start: number;
  end: number;
  mode: string;
  color?: string;
}

const modeBands = ref<ModeBand[]>([]);
const eventMarks = ref<ProcessEventMark[]>([]);

/** 时间窗选项映射到后端 TrendWindow（168h/custom 暂不支持过程趋势） */
const TREND_WINDOW_MAP: Record<string, LoopApi.TrendWindow> = {
  '8h': 'last_8_hours',
  '24h': 'last_24_hours',
  '72h': 'last_72_hours',
};

/** 从趋势数据提取 MODE 背景带 */
function extractModeBands(trend: LoopApi.MonitorTrend): ModeBand[] {
  const bands: ModeBand[] = [];
  if (!trend.timestamps || trend.timestamps.length === 0) return bands;
  let currentMode = '';
  let bandStart: null | number = null;
  for (let i = 0; i < trend.timestamps.length; i++) {
    const ts = trend.timestamps[i]!;
    const mode = trend.mode[i];
    let modeLabel: string;
    switch (mode) {
      case 0: { modeLabel = 'MANUAL'; break; }
      case 1: { modeLabel = 'AUTO'; break; }
      case 2: { modeLabel = 'CAS'; break; }
      default: { modeLabel = String(mode ?? 'UNKNOWN'); }
    }
    if (modeLabel !== currentMode) {
      if (currentMode && bandStart !== null) {
        bands.push({ end: ts, mode: currentMode, start: bandStart });
      }
      bandStart = ts;
      currentMode = modeLabel;
    }
  }
  if (currentMode && bandStart !== null) {
    bands.push({
      end: trend.timestamps[trend.timestamps.length - 1]!,
      mode: currentMode,
      start: bandStart,
    });
  }
  return bands;
}

/** 加载过程趋势数据（R4 主画布-过程变量模式） */
async function loadProcessTrend(loopId: string): Promise<void> {
  const tw = TREND_WINDOW_MAP[timeWindow.value];
  if (!tw) {
    // 168h/custom 暂不支持过程趋势（后端无对应档位）
    processTrendData.value = null;
    modeBands.value = [];
    return;
  }
  processTrendLoading.value = true;
  await requestGuard.run(async (_signal, capturedEpoch) => {
    const detail = await getLoopMonitorDetailApi(loopId, tw).catch(() => null);
    if (!requestGuard.guard(loopId, capturedEpoch)) return;
    if (detail?.trend) {
      processTrendData.value = detail.trend;
      modeBands.value = extractModeBands(detail.trend);
    } else {
      processTrendData.value = null;
      modeBands.value = [];
    }
    processTrendLoading.value = false;
  });
}

/** 加载 KPI 历史快照（R4 主画布-性能指标模式） */
async function loadKpiHistory(loopId: string): Promise<void> {
  kpiHistoryLoading.value = true;
  await requestGuard.run(async (_signal, capturedEpoch) => {
    const now = dayjs();
    let startTime: dayjs.Dayjs;
    let endTime: dayjs.Dayjs = now;
    if (
      timeWindow.value === 'custom' &&
      customStartTime.value &&
      customEndTime.value
    ) {
      startTime = dayjs(customStartTime.value);
      endTime = dayjs(customEndTime.value);
    } else switch (timeWindow.value) {
 case '8h': {
      startTime = now.subtract(8, 'hour');
    
 break;
 }
 case '72h': {
      startTime = now.subtract(3, 'day');
    
 break;
 }
 case '168h': {
      startTime = now.subtract(7, 'day');
    
 break;
 }
 default: {
      startTime = now.subtract(24, 'hour');
    }
 }
    const res = await getLoopSnapshotsApi({
      endTime: endTime.toISOString(),
      latestOnly: false,
      loopId,
      page: 1,
      pageSize: 100,
      sortBy: 'tsStart',
      sortOrder: 'asc',
      startTime: startTime.toISOString(),
    }).catch(() => ({ items: [], total: 0 }));
    if (!requestGuard.guard(loopId, capturedEpoch)) return;
    kpiHistory.value = (res.items || []).toSorted((a, b) =>
      (a.tsStart || '').localeCompare(b.tsStart || ''),
    );
    kpiHistoryLoading.value = false;
  });
}

/** 从 summary 构建事件标记（诊断/整定/验证） */
const computedEventMarks = computed<ProcessEventMark[]>(() => {
  if (!summary.value) return [];
  const marks: ProcessEventMark[] = [];
  const diagTime = summary.value.diagnosis?.resultAt;
  if (diagTime) {
    marks.push({
      label: '诊断',
      timestamp: dayjs(diagTime).valueOf(),
      type: 'diagnosis',
    });
  }
  const tuneTime = summary.value.tuning?.resultAt;
  if (tuneTime) {
    marks.push({
      label: '整定',
      timestamp: dayjs(tuneTime).valueOf(),
      type: 'tuning',
    });
  }
  return marks;
});

watch(
  computedEventMarks,
  (v) => {
    eventMarks.value = v;
  },
  { immediate: true },
);

// 时间窗变化时重新加载趋势和 KPI 历史
watch(
  [selectedLoopId, timeWindow],
  ([newLoopId]) => {
    if (newLoopId) {
      loadProcessTrend(newLoopId);
      loadKpiHistory(newLoopId);
    } else {
      processTrendData.value = null;
      kpiHistory.value = [];
      modeBands.value = [];
      eventMarks.value = [];
    }
  },
  { immediate: false },
);

// ===== R5 评估证据：雷达图 + 指标横道图数据 =====
/** 最新快照（雷达/横道图数据源，取 scoreHistory 最后一条） */
const latestSnapshot = computed(
  () => scoreHistory.value[scoreHistory.value.length - 1] ?? null,
);

/** R5 六轴雷达数据（平稳/准确/快速/自控/好值/饱和） */
const radarAxes = computed(() => {
  const s = latestSnapshot.value;
  if (!s) return null;
  return {
    accuracyRate: s.accuracyRate ?? 0,
    autoModeRate: s.autoModeRate ?? 0,
    fastRate: s.fastRate ?? 0,
    goodValueRate: s.goodValueRate ?? 0,
    saturationRate: s.saturationRate ?? 0,
    steadyRate: s.steadyRate ?? 0,
  };
});

/** R5 指标横道图数据（8 项主要指标达成度） */
const metricBarsData = computed(() => {
  const s = latestSnapshot.value;
  if (!s) return [];
  return [
    { name: '振荡率', threshold: 40, value: s.oscillationRate ?? 0 },
    { name: '仪表故障率', threshold: 30, value: s.instrumentFaultRate ?? 0 },
    { name: '有效自控率', threshold: 60, value: s.effectiveAutoRate ?? 0 },
    { name: '调节时间', threshold: 60, value: s.settlingTime ?? 0 },
    { name: '卡涩指数', threshold: 40, value: s.stictionIndex ?? 0 },
    { name: '行程指数', threshold: 60, value: s.outputTravelIndex ?? 0 },
    { name: 'PV标准差', threshold: 50, value: s.pvStd ?? 0 },
    { name: 'OP标准差', threshold: 50, value: s.opStd ?? 0 },
  ];
});

/** R5 诊断扩展指标（负向横道：饱和率/粘滞系数/稳定时间/振荡率） */
const diagExtendedMetrics = computed(() => {
  const s = latestSnapshot.value;
  if (!s) return [];
  return [
    { name: '饱和率', threshold: 40, value: s.saturationRate ?? 0 },
    { name: '粘滞系数', threshold: 40, value: s.stictionIndex ?? 0 },
    { name: '稳定时间', threshold: 60, value: s.settlingTime ?? 0 },
    { name: '振荡率', threshold: 40, value: s.oscillationRate ?? 0 },
  ];
});

/**
 * R5 诊断卡：按诊断标签类型提取算法特征值（§2.2 表格第 13 行 v1.8）。
 * 从 diagnosisDetail.featureValues 中按 DIAG_FEATURE_DEFS 映射提取关键字段，
 * 只展示有值的字段，无值或无映射时不渲染（叙述类文字无来源时禁止显示）。
 */
interface DiagFeatureItem {
  label: string;
  text: string;
}

const diagFeatures = computed<DiagFeatureItem[]>(() => {
  const detail = diagnosisDetail.value;
  if (!detail?.featureValues) return [];
  const fv = detail.featureValues;
  const items: DiagFeatureItem[] = [];
  const seen = new Set<string>();
  for (const labelItem of diagnosisLabels.value) {
    const defs = DIAG_FEATURE_DEFS[labelItem.label];
    if (!defs) continue;
    for (const def of defs) {
      const keys = [def.key, ...(def.aliases ?? [])];
      let raw: number | undefined;
      for (const k of keys) {
        const v = fv[k];
        if (typeof v === 'number' && !Number.isNaN(v)) {
          raw = v;
          break;
        }
      }
      if (raw == null) continue;
      const dedupKey = `${labelItem.label}:${def.key}`;
      if (seen.has(dedupKey)) continue;
      seen.add(dedupKey);
      const fmt = def.fmt ?? ((v: number) => String(v));
      const text = def.unit ? `${fmt(raw)} ${def.unit}` : fmt(raw);
      items.push({ label: def.label, text });
    }
  }
  return items;
});

/** R5 诊断卡：规则模板建议（evidenceChain.reasoning，有则显示） */
const diagReasoning = computed<string | null>(() => {
  const r = diagnosisDetail.value?.evidenceChain?.reasoning;
  if (!r || typeof r !== 'string' || r.trim().length === 0) return null;
  return r.trim();
});

/** R5 整定卡：风险等级颜色映射 */
function riskLevelColor(level?: null | string): string {
  if (!level) return 'default';
  const upper = level.toUpperCase();
  if (upper === 'HIGH' || upper === 'CRITICAL') return 'red';
  if (upper === 'MEDIUM' || upper === 'MODERATE') return 'orange';
  if (upper === 'LOW') return 'green';
  return 'default';
}

/** R5 验证卡：Tracker 状态颜色映射 */
function trackerStatusColor(status?: null | string): string {
  if (!status) return 'default';
  const upper = status.toUpperCase();
  if (upper === 'CLOSED') return 'green';
  if (upper === 'VERIFYING') return 'blue';
  if (upper === 'IN_PROGRESS') return 'processing';
  if (upper === 'REOPENED') return 'orange';
  if (upper === 'IGNORED') return 'default';
  return 'default';
}

/** R5 验证卡：验证结论颜色映射 */
function effectConclusionColor(conclusion?: null | string): string {
  if (!conclusion) return 'default';
  const upper = conclusion.toUpperCase();
  if (upper === 'IMPROVED') return 'green';
  if (upper === 'DETERIORATED') return 'red';
  return 'default';
}

/** R5 整定卡：G(s) 传递函数格式化（FOPDT / SOPDT） */
function transferFunctionText(
  params?: null | { K?: null | number; T1?: null | number; T2?: null | number; tau?: null | number; theta?: null | number },
): string {
  if (!params) return '—';
  const { K, T1, T2, tau, theta } = params;
  const delay = theta ?? tau;
  // SOPDT：有两个时间常数
  if (T1 != null && T2 != null && K != null) {
    const delayPart = delay != null && delay > 0 ? ` · e^(-${delay.toFixed(0)}s)` : '';
    return `G(s) = ${K.toFixed(2)} / [(${T1.toFixed(0)}s+1)(${T2.toFixed(0)}s+1)]${delayPart}`;
  }
  // FOPDT：单时间常数
  if (tau != null && K != null) {
    const delayPart = delay != null && delay > 0 && delay !== tau ? ` · e^(-${delay.toFixed(0)}s)` : '';
    return `G(s) = ${K.toFixed(2)} / (${tau.toFixed(0)}s+1)${delayPart}`;
  }
  if (T1 != null && K != null) {
    const delayPart = delay != null && delay > 0 ? ` · e^(-${delay.toFixed(0)}s)` : '';
    return `G(s) = ${K.toFixed(2)} / (${T1.toFixed(0)}s+1)${delayPart}`;
  }
  return '—';
}

// ===== R2 等级标签（动态阈值，useScoreColor 降级 GB/T 44693.2 §6.3 默认）=====
const summaryScore = computed(() => summary.value?.scoreTrend.score ?? null);
const { color: gradeColor, label: gradeLabel } = useScoreColor(
  summaryScore,
  gradingThresholds,
);

const gradeInfo = computed(() => ({
  color: gradeColor.value,
  label: gradeLabel.value ?? '—',
}));

/** 告警线：取定级阈值中"警告"档 minScore（默认 60），随配置动态变化 */
const alarmLine = computed<number>(() => {
  const warn = gradingThresholds.value.find((t) => t.level === 4);
  return warn?.minScore ?? 60;
});

// ===== 实时点值（R4 图例行右侧显示 SP/PV/OP/MODE）=====
const runtimePointValues = computed(() => {
  const r = summary.value?.runtime;
  if (!r) return null;
  return {
    mode:
      r.modeLabel ?? (r.mode === 1 ? 'AUTO' : (r.mode === 0 ? 'MANUAL' : '—')),
    op: r.op,
    pv: r.pv,
    pvQuality: r.pvQuality,
    pvUnit: r.pvUnit ?? '',
    sp: r.sp,
  };
});

/** PV 偏离 SP 超阈值或质量码非 GOOD 时着色 */
function pvValueColor(): string {
  const r = runtimePointValues.value;
  if (!r) return 'inherit';
  if (r.pvQuality && r.pvQuality !== 'GOOD') return '#c23434';
  return 'inherit';
}

/** R2 评分 tooltip：B 类语义——计算窗口与时间可见（§2.6 配套规则1） */
const scoreTooltip = computed<string>(() => {
  const a = summary.value?.assessment;
  const tw = a?.timeWindow ?? '24h';
  const at = a?.resultAt ? formatTime(a.resultAt) : '—';
  return `最近评估快照（计算窗口 ${tw} · ${at}），不随页面时间窗变化`;
});

/** R2 日 delta tooltip */
const dayDeltaTooltip = computed<string>(() => {
  return '与昨日同时段快照比较';
});

/** 数据新鲜度 tooltip */
const freshnessTooltip = computed<string>(() => {
  const f = summary.value?.dataFreshness;
  if (!f) return '';
  const status =
    f.status === 'FRESH'
      ? '数据新鲜'
      : (f.status === 'DELAYED'
        ? '数据延迟'
        : '未知');
  return `${status}${f.reason ? `：${f.reason}` : ''}`;
});

// ===== 时间窗选项 =====
const timeWindowOptions = [
  { label: '8h', value: '8h' as const },
  { label: '24h', value: '24h' as const },
  { label: '72h', value: '72h' as const },
  { label: '168h', value: '168h' as const },
  { label: '自定义', value: 'custom' as const },
];

/** 自定义时间窗确认 */
const customTimePopOpen = ref(false);
function applyCustomTime() {
  if (customStartTime.value && customEndTime.value) {
    timeWindow.value = 'custom';
    customTimePopOpen.value = false;
    if (selectedLoopId.value) {
      loadKpiHistory(selectedLoopId.value);
    }
  }
}

// ===== 生命周期内联标签（R2 右侧紧凑态）=====
const lifecycleStages = computed(() => {
  if (!summary.value?.lifecycle?.stages) return [];
  return summary.value.lifecycle.stages.map((s) => ({
    label: stageLabelMap[s.stage] ?? s.stage,
    stage: s.stage,
    status: s.status,
  }));
});

const stageLabelMap: Record<string, string> = {
  ASSESS: '评估',
  DIAGNOSE: '诊断',
  MONITOR: '数据',
  TUNE: '整定',
  VERIFY: '验证',
};
</script>

<template>
  <Page>
    <ClpmPageToolbar
      title="回路工作台"
      subtitle="单回路 360° 一站式处置"
      :loading="loopListLoading"
    >
      <template #actions>
        <Segmented
          :value="monitorCtx.view.value"
          :options="viewModeOptions"
          size="small"
          @change="handleViewChange"
        />
        <MonitorContextToolbar
          :attention-only-hidden="true"
          page-key="monitor-workbench"
          @filter-change="() => loadLoopList(true)"
        />
        <ClpmStandardActions :items="toolbarItems" />
      </template>
    </ClpmPageToolbar>

    <!-- ===== 批量表格模式 ===== -->
    <LoopFleetView v-if="isTableView" @loop-click="handleFleetLoopClick" />

    <!-- ===== 工作台模式（三栏布局：左脊柱 + 主区域 + 右决策栏） ===== -->
    <template v-if="!isTableView">
      <div
        class="wb-layout"
        :class="{ 'wb-layout--collapsed': sidebarCollapsed }"
      >
        <!-- ===== 左脊柱：装置树 + 回路列表 ===== -->
        <aside class="wb-sidebar">
          <div class="wb-sidebar__search">
            <Input
              v-model:value="searchKeyword"
              placeholder="搜索位号/装置/描述..."
              allow-clear
              size="small"
              @input="handleSearchInput"
              @press-enter="() => loadLoopList(true)"
            />
          </div>
          <!-- 装置树（仅到装置/单元级，范围轴） -->
          <div class="wb-sidebar__tree">
            <div class="wb-sidebar__tree-title">
              <span>装置</span>
              <button
                v-if="plantTreeSelectedKeys.length > 0"
                class="wb-sidebar__tree-clear"
                @click="handlePlantTreeSelect([])"
              >
                清除
              </button>
            </div>
            <Spin :spinning="plantTreeLoading" size="small">
              <Tree
                v-if="plantTreeData.length > 0"
                v-model:expanded-keys="plantTreeExpandedKeys"
                v-model:selected-keys="plantTreeSelectedKeys"
                :tree-data="plantTreeData as any"
                :block-node="true"
                :show-line="false"
                class="wb-plant-tree"
                @select="handlePlantTreeSelect"
              />
              <div v-else class="wb-sidebar__tree-empty">暂无装置数据</div>
            </Spin>
          </div>
          <!-- 回路列表（当前选中单元的回路，独立成区） -->
          <div class="wb-sidebar__list-title">
            <span>回路</span>
            <span class="wb-sidebar__list-count">{{ loopList.length }} 条</span>
          </div>
          <Spin :spinning="loopListLoading" size="small">
            <div
              :ref="setLoopListRef"
              class="wb-sidebar__list"
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
                    class="wb-loop-item"
                    :class="{
                      'wb-loop-item--active': item.loopId === selectedLoopId,
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
                    <div class="wb-loop-item__header">
                      <span class="wb-loop-item__tag">{{ item.tagName }}</span>
                      <span
                        v-if="item.confidenceLevel"
                        class="wb-loop-item__conf"
                        :class="{
                          'wb-loop-item__conf--ok': ['A', 'B'].includes(
                            item.confidenceLevel,
                          ),
                          'wb-loop-item__conf--warn':
                            item.confidenceLevel === 'C',
                          'wb-loop-item__conf--err': ['D', 'E'].includes(
                            item.confidenceLevel,
                          ),
                        }"
                        >{{ item.confidenceLevel }}</span
                      >
                    </div>
                    <div class="wb-loop-item__desc">
                      <span class="wb-loop-item__name">{{
                        item.description || '—'
                      }}</span>
                      <span class="wb-loop-item__score">
                        {{ item.score ?? '—' }}
                        <DayDeltaBadge
                          :delta="item.scoreDelta"
                          :trend="item.dayTrend"
                        />
                      </span>
                    </div>
                    <div class="wb-loop-item__vals">
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
                      <span class="wb-loop-item__mode">{{
                        item.currentValues?.modeLabel || '—'
                      }}</span>
                    </div>
                  </div>
                </div>
              </div>
              <div
                v-if="!loopListLoading && loopListError"
                class="wb-sidebar__error"
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
                class="wb-sidebar__empty"
              />
            </div>
          </Spin>
          <!-- 沉浸模式折叠按钮 -->
          <button
            class="wb-sidebar__toggle"
            @click="sidebarCollapsed = !sidebarCollapsed"
          >
            <span v-if="sidebarCollapsed">▶</span>
            <span v-else>◀</span>
          </button>
        </aside>

        <!-- ===== 主区域 ===== -->
        <main class="wb-main">
          <!-- 深链接提示 -->
          <div
            v-if="selectedLoop && injectedLoop"
            class="wb-deeplink-hint"
            role="status"
          >
            当前回路不在筛选结果中，已从深链接定位。可清空筛选以在左侧列表查看。
          </div>

          <!-- 未选回路 / 回路不存在 -->
          <div v-if="loopNotFound" class="wb-state wb-state--error">
            <Empty
              description="回路不存在或已停用"
              :image="Empty.PRESENTED_IMAGE_SIMPLE"
            />
            <div class="wb-state__hint">
              URL 中的回路 ID 无效或已停用，请从左侧选择其他回路。
            </div>
          </div>
          <div v-else-if="!selectedLoop" class="wb-state wb-state--empty">
            <Empty
              description="请从左侧选择回路"
              :image="Empty.PRESENTED_IMAGE_SIMPLE"
            />
          </div>

          <template v-else-if="selectedLoop">
            <!-- ===== R1 页头 ===== -->
            <header class="wb-r1">
              <div class="wb-r1__left">
                <span class="wb-r1__tag">{{ selectedLoop.tagName }}</span>
                <span class="wb-r1__desc">{{
                  selectedLoop.description || '—'
                }}</span>
                <span class="wb-r1__crumb">
                  {{ selectedLoop.unitName || '—' }} ·
                  {{ selectedLoop.loopType || '—' }}
                </span>
              </div>
              <div class="wb-r1__right">
                <span
                  v-if="runtimePointValues"
                  class="wb-r1__mode-pill"
                  :class="{
                    'wb-r1__mode-pill--auto':
                      runtimePointValues.mode === 'AUTO',
                    'wb-r1__mode-pill--man':
                      runtimePointValues.mode === 'MANUAL' ||
                      runtimePointValues.mode === 'MAN',
                  }"
                  >{{ runtimePointValues.mode }}</span
                >
                <Tooltip
                  v-if="summary?.dataFreshness"
                  :title="freshnessTooltip"
                  placement="bottom"
                >
                  <span class="wb-r1__fresh">
                    <span
                      class="wb-r1__dot"
                      :class="{
                        'wb-r1__dot--stale':
                          summary.dataFreshness.status === 'DELAYED',
                      }"
                    ></span>
                    {{
                      summary.dataFreshness.status === 'FRESH'
                        ? '数据新鲜'
                        : summary.dataFreshness.status === 'DELAYED'
                          ? '数据延迟'
                          : '未知'
                    }}
                  </span>
                </Tooltip>
              </div>
            </header>

            <!-- ===== R2 状态条（评分+等级+可信度+有效率+生命周期内联） ===== -->
            <div v-if="summary" class="wb-r2">
              <div class="wb-r2__left">
                <Tooltip :title="scoreTooltip" placement="bottom">
                  <span class="wb-r2__score">
                    评分
                    <span class="wb-r2__score-val">{{
                      summary.scoreTrend.score?.toFixed(1) ?? '—'
                    }}</span>
                    <Tooltip :title="dayDeltaTooltip" placement="bottom">
                      <DayDeltaBadge
                        :delta="summary.scoreTrend.scoreDelta"
                        :trend="summaryDayTrend ?? undefined"
                      />
                    </Tooltip>
                  </span>
                </Tooltip>
                <span
                  class="wb-r2__grade"
                  :style="{
                    color: gradeInfo.color,
                    borderColor: gradeInfo.color,
                  }"
                  >{{ gradeInfo.label }}</span
                >
                <span
                  v-if="summary.dataHealth.confidenceLevel"
                  class="wb-r2__item"
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
                  class="wb-r2__item"
                >
                  有效率 {{ (summary.dataHealth.validRate * 100).toFixed(1) }}%
                </span>
                <span
                  v-if="summary.partial"
                  class="wb-r2__partial"
                  title="部分摘要数据源不可用，但不影响整体判断"
                >
                  部分数据不可用
                </span>
              </div>
              <!-- 生命周期内联（v1.2 自 R3 并入） -->
              <div class="wb-r2__lifecycle">
                <template v-for="(s, idx) in lifecycleStages" :key="s.stage">
                  <span
                    class="wb-r2__stage"
                    :class="{
                      'wb-r2__stage--done': s.status === 'COMPLETED',
                      'wb-r2__stage--cur':
                        s.status === 'RUNNING' || s.status === 'READY',
                    }"
                    @click="
                      handleLifecycleStageClick(
                        s.stage as MonitorApi.LifecycleStageName,
                      )
                    "
                    >{{ s.label }}</span
                  >
                  <span
                    v-if="idx < lifecycleStages.length - 1"
                    class="wb-r2__stage-sep"
                    >─</span
                  >
                </template>
              </div>
            </div>
            <!-- summary 加载中骨架 -->
            <div
              v-else-if="summaryLoading && selectedLoop"
              class="wb-r2 wb-r2--loading"
            >
              <Spin size="small" />
              <span class="wb-r2__loading-text">正在加载工作台摘要…</span>
            </div>

            <!-- ===== R4 主画布（过程变量 / 性能指标 双模式） ===== -->
            <section class="wb-r4">
              <!-- 画布头部第一行：模式切换 + 标题 + 时间窗 -->
              <div class="wb-r4__header">
                <div class="wb-r4__mode">
                  <Segmented
                    v-model:value="canvasMode"
                    :options="[
                      { label: '过程变量', value: 'process' },
                      { label: '性能指标', value: 'kpi' },
                    ]"
                    size="small"
                  />
                </div>
                <div class="wb-r4__time-window">
                  <Segmented
                    v-model:value="timeWindow"
                    :options="timeWindowOptions"
                    size="small"
                  />
                  <button
                    v-if="timeWindow === 'custom'"
                    class="wb-r4__custom-btn"
                    @click="customTimePopOpen = !customTimePopOpen"
                  >
                    自定义
                  </button>
                </div>
              </div>
              <!-- 自定义时间窗弹层 -->
              <div v-if="customTimePopOpen" class="wb-r4__custom-pop">
                <label
                  >起 <input v-model="customStartTime" type="datetime-local"
                /></label>
                <label
                  >止 <input v-model="customEndTime" type="datetime-local"
                /></label>
                <button @click="applyCustomTime">应用</button>
              </div>
              <!-- 画布头部第二行：图例 + 实时点值 -->
              <div class="wb-r4__legend">
                <template v-if="canvasMode === 'process'">
                  <span class="wb-r4__legend-item"
                    ><span
                      class="wb-r4__legend-line wb-r4__legend-line--pv"
                    ></span
                    >PV</span
                  >
                  <span class="wb-r4__legend-item"
                    ><span
                      class="wb-r4__legend-line wb-r4__legend-line--sp"
                    ></span
                    >SP</span
                  >
                  <span class="wb-r4__legend-item"
                    ><span
                      class="wb-r4__legend-line wb-r4__legend-line--op"
                    ></span
                    >OP</span
                  >
                  <span v-if="eventMarks.length > 0" class="wb-r4__legend-item"
                    >▼诊断 ◆整定 ▐验证</span
                  >
                </template>
                <template v-else>
                  <span class="wb-r4__legend-item"
                    ><span
                      class="wb-r4__legend-line wb-r4__legend-line--score"
                    ></span
                    >综合评分</span
                  >
                  <span class="wb-r4__legend-item"
                    ><span
                      class="wb-r4__legend-line wb-r4__legend-line--steady"
                    ></span
                    >平稳率</span
                  >
                  <span class="wb-r4__legend-item"
                    ><span
                      class="wb-r4__legend-line wb-r4__legend-line--accuracy"
                    ></span
                    >准确率</span
                  >
                  <span class="wb-r4__legend-item"
                    ><span
                      class="wb-r4__legend-line wb-r4__legend-line--fast"
                    ></span
                    >快速率</span
                  >
                </template>
                <!-- 实时点值 -->
                <span v-if="runtimePointValues" class="wb-r4__live-vals">
                  <span class="wb-r4__live-val">
                    PV
                    <span
                      class="wb-r4__live-num"
                      :style="{ color: pvValueColor() }"
                      >{{ runtimePointValues.pv?.toFixed(2) ?? '—' }}</span
                    >
                  </span>
                  <span class="wb-r4__live-val">
                    SP
                    <span class="wb-r4__live-num">{{
                      runtimePointValues.sp?.toFixed(2) ?? '—'
                    }}</span>
                  </span>
                  <span class="wb-r4__live-val">
                    OP
                    <span class="wb-r4__live-num"
                      >{{ runtimePointValues.op?.toFixed(1) ?? '—' }}%</span
                    >
                  </span>
                </span>
              </div>
              <!-- 趋势图 -->
              <div class="wb-r4__chart">
                <Spin
                  :spinning="
                    canvasMode === 'process'
                      ? processTrendLoading
                      : kpiHistoryLoading
                  "
                  size="small"
                >
                  <WorkbenchProcessTrend
                    v-if="canvasMode === 'process'"
                    :trend="processTrendData"
                    :pv-unit="selectedLoop?.pvUnit || ''"
                    :op-unit="selectedLoop?.opUnit || '%'"
                    :event-marks="eventMarks"
                    :mode-bands="modeBands"
                  />
                  <WorkbenchKpiHistory
                    v-else
                    :snapshots="kpiHistory"
                    :alarm-line="alarmLine"
                    :time-window="timeWindow"
                  />
                </Spin>
              </div>
            </section>

            <!-- ===== R5 证据四区（评估雷达+横道 / 诊断卡 / 整定卡） ===== -->
            <section class="wb-r5">
              <!-- 评估卡（雷达图 + 指标横道图） -->
              <div class="wb-r5__card wb-r5__card--assess">
                <div class="wb-r5__card-header">
                  <span class="wb-r5__card-title">评估</span>
                  <span class="wb-r5__card-meta">
                    {{
                      latestSnapshot
                        ? `24h 窗口 · ${formatTime(latestSnapshot.tsStart)}`
                        : '—'
                    }}
                  </span>
                  <router-link
                    v-if="selectedLoopId"
                    :to="{
                      path: '/performance/loops',
                      query: { loopId: selectedLoopId },
                    }"
                    class="wb-r5__card-link"
                    >完整证据 →</router-link
                  >
                </div>
                <div class="wb-r5__assess-body">
                  <div class="wb-r5__radar">
                    <WorkbenchRadar6
                      v-if="radarAxes"
                      :axes="radarAxes"
                      :score="summary?.scoreTrend.score ?? null"
                      :grade="gradeInfo.label"
                      :grade-color="gradeInfo.color"
                    />
                    <div v-else class="wb-r5__empty-mini">暂无评估数据</div>
                  </div>
                  <div class="wb-r5__bars">
                    <WorkbenchMetricBars :metrics="metricBarsData" />
                  </div>
                </div>
              </div>

              <!-- 诊断卡 -->
              <div class="wb-r5__card wb-r5__card--diag">
                <div class="wb-r5__card-header">
                  <span class="wb-r5__card-title">诊断</span>
                  <span class="wb-r5__card-meta">
                    {{
                      summary?.diagnosis?.resultAt
                        ? formatTime(summary.diagnosis.resultAt)
                        : '—'
                    }}
                  </span>
                  <router-link
                    v-if="selectedLoopId"
                    :to="{
                      path: '/diagnosis/detail',
                      query: { loopId: selectedLoopId },
                    }"
                    class="wb-r5__card-link"
                    >完整证据 →</router-link
                  >
                </div>
                <div class="wb-r5__diag-body">
                  <div
                    v-if="diagnosisDetail || summary?.diagnosis"
                    class="wb-r5__diag-content"
                  >
                    <div class="wb-r5__diag-labels">
                      <Tag
                        v-for="(item, idx) in diagnosisLabels"
                        :key="idx"
                        :color="
                          DIAGNOSIS_LABEL_COLOR_MAP[item.label] || 'default'
                        "
                        class="!text-[11px]"
                      >
                        {{
                          item.labelName ||
                          DIAGNOSIS_LABEL_NAME_MAP[item.label] ||
                          item.label
                        }}
                        <span class="ml-1 opacity-60">{{
                          Number(item.confidence).toFixed(2)
                        }}</span>
                      </Tag>
                      <span
                        v-if="diagnosisLabels.length === 0"
                        class="wb-r5__diag-empty-label"
                        >未检测到异常标签</span
                      >
                    </div>
                    <div v-if="diagnosisDetail" class="wb-r5__diag-stats">
                      <div class="wb-r5__diag-stat">
                        <span class="wb-r5__diag-stat-label">融合置信度</span>
                        <span class="wb-r5__diag-stat-val">{{
                          diagnosisDetail.fusedConfidence == null
                            ? '—'
                            : Number(diagnosisDetail.fusedConfidence).toFixed(2)
                        }}</span>
                      </div>
                      <div
                        v-if="diagnosisDetail.confidenceLevel"
                        class="wb-r5__diag-stat"
                      >
                        <span class="wb-r5__diag-stat-label">可信度等级</span>
                        <Tag
                          :color="confidenceTagColor(diagnosisDetail.confidenceLevel)"
                          class="!text-[10px] !leading-none !px-1 !py-0"
                        >{{ diagnosisDetail.confidenceLevel }}</Tag>
                      </div>
                    </div>
                    <!-- 算法特征值（§2.2 v1.8：FFT 主频/振幅等算法输出） -->
                    <div
                      v-if="diagFeatures.length > 0"
                      class="wb-r5__diag-features"
                    >
                      <div
                        v-for="(feat, fi) in diagFeatures"
                        :key="fi"
                        class="wb-r5__diag-feature"
                      >
                        <span class="wb-r5__diag-feature-label">{{ feat.label }}</span>
                        <span class="wb-r5__diag-feature-val">{{ feat.text }}</span>
                      </div>
                    </div>
                    <!-- 规则模板建议（evidenceChain.reasoning，有则显示） -->
                    <div
                      v-if="diagReasoning"
                      class="wb-r5__diag-reasoning"
                    >
                      {{ diagReasoning }}
                    </div>
                    <!-- 诊断扩展指标（负向横道） -->
                    <div
                      v-if="diagExtendedMetrics.length > 0"
                      class="wb-r5__diag-ext"
                    >
                      <WorkbenchMetricBars
                        :metrics="diagExtendedMetrics"
                        :negative="true"
                      />
                    </div>
                  </div>
                  <div v-else class="wb-r5__empty-mini">暂无诊断数据</div>
                </div>
              </div>

              <!-- 整定卡 -->
              <div class="wb-r5__card wb-r5__card--tune">
                <div class="wb-r5__card-header">
                  <span class="wb-r5__card-title">整定</span>
                  <span class="wb-r5__card-meta">
                    {{
                      summary?.tuning?.resultAt
                        ? formatTime(summary.tuning.resultAt)
                        : '—'
                    }}
                  </span>
                  <router-link
                    v-if="selectedLoopId"
                    :to="{
                      path: '/tuning/workbench',
                      query: { loopId: selectedLoopId },
                    }"
                    class="wb-r5__card-link"
                    >完整证据 →</router-link
                  >
                </div>
                <div class="wb-r5__tune-body">
                  <div
                    v-if="summary?.tuning || tuningLatest"
                    class="wb-r5__tune-rows"
                  >
                    <div class="wb-r5__tune-row">
                      <span class="wb-r5__tune-label">当前 PID</span>
                      <span class="wb-r5__tune-val">{{
                        pidText(currentPid ?? tuningDetail?.currentPid ?? undefined)
                      }}</span>
                    </div>
                    <div class="wb-r5__tune-row">
                      <span class="wb-r5__tune-label">推荐 PID</span>
                      <span
                        class="wb-r5__tune-val wb-r5__tune-val--highlight"
                        >{{ pidText(tuningDetail?.recommendedPid ?? tuningLatest?.recommendedPid) }}</span
                      >
                    </div>
                    <div class="wb-r5__tune-row">
                      <span class="wb-r5__tune-label">辨识模型</span>
                      <span class="wb-r5__tune-val wb-r5__tune-val--mono">{{
                        transferFunctionText(tuningDetail?.modelParams ?? tuningLatest?.modelParams ?? undefined)
                      }}</span>
                    </div>
                    <div class="wb-r5__tune-row">
                      <span class="wb-r5__tune-label">拟合度</span>
                      <span class="wb-r5__tune-val">
                        {{
                          (tuningDetail?.fittingScore ?? tuningLatest?.fittingScore) == null
                            ? '—'
                            : `${((tuningDetail?.fittingScore ?? tuningLatest?.fittingScore)! * 100).toFixed(1)}%`
                        }}
                      </span>
                    </div>
                    <div class="wb-r5__tune-row">
                      <span class="wb-r5__tune-label">风险等级</span>
                      <span class="wb-r5__tune-val">
                        <Tag
                          v-if="summary?.tuning?.riskLevel"
                          :color="riskLevelColor(summary.tuning.riskLevel)"
                          class="!text-[10px] !leading-none !px-1.5 !py-0"
                        >{{ summary.tuning.riskLevel }}</Tag>
                        <span v-else>—</span>
                      </span>
                    </div>
                    <div class="wb-r5__tune-row">
                      <span class="wb-r5__tune-label">可信度</span>
                      <span class="wb-r5__tune-val">
                        <Tag
                          v-if="summary?.tuning?.confidenceLevel"
                          :color="confidenceTagColor(summary.tuning.confidenceLevel)"
                          class="!text-[10px] !leading-none !px-1 !py-0"
                        >{{ summary.tuning.confidenceLevel }}</Tag>
                        <span v-else>—</span>
                      </span>
                    </div>
                    <div class="wb-r5__tune-row">
                      <span class="wb-r5__tune-label">状态</span>
                      <span class="wb-r5__tune-val">{{
                        summary?.tuning?.status || tuningLatest?.status || '—'
                      }}</span>
                    </div>
                  </div>
                  <div v-else class="wb-r5__empty-mini">暂无整定数据</div>
                  <div class="wb-r5__tune-safety">
                    安全边界：只读建议 · 人工实施 · 需留痕
                  </div>
                </div>
              </div>

              <!-- ===== 验证卡（第四张：闭环验证状态） ===== -->
              <div class="wb-r5__card wb-r5__card--verify">
                <div class="wb-r5__card-header">
                  <span class="wb-r5__card-title">验证</span>
                  <span class="wb-r5__card-meta">
                    {{
                      summary?.trackerTimeline?.effectVerifiedAt
                        ? formatTime(summary.trackerTimeline.effectVerifiedAt)
                        : summary?.trackerTimeline?.createdAt
                          ? formatTime(summary.trackerTimeline.createdAt)
                          : '—'
                    }}
                  </span>
                  <a
                    v-if="summary?.trackerTimeline?.trackerId"
                    class="wb-r5__card-link"
                    @click="handleTrackerViewDetail(summary.trackerTimeline.trackerId)"
                  >完整记录 →</a>
                </div>
                <div class="wb-r5__verify-body">
                  <template v-if="summary?.trackerTimeline">
                    <div class="wb-r5__tune-rows">
                      <div class="wb-r5__tune-row">
                        <span class="wb-r5__tune-label">Tracker 状态</span>
                        <span class="wb-r5__tune-val">
                          <Tag
                            :color="trackerStatusColor(summary.trackerTimeline.actionStatus)"
                            class="!text-[10px] !leading-none !px-1.5 !py-0"
                          >{{ summary.trackerTimeline.actionStatus }}</Tag>
                        </span>
                      </div>
                      <div class="wb-r5__tune-row">
                        <span class="wb-r5__tune-label">验证结论</span>
                        <span class="wb-r5__tune-val">
                          <Tag
                            v-if="summary.trackerTimeline.effectCompare?.conclusion"
                            :color="effectConclusionColor(summary.trackerTimeline.effectCompare.conclusion)"
                            class="!text-[10px] !leading-none !px-1.5 !py-0"
                          >{{ summary.trackerTimeline.effectCompare.conclusionLabel ?? summary.trackerTimeline.effectCompare.conclusion }}</Tag>
                          <span v-else>—</span>
                        </span>
                      </div>
                      <div class="wb-r5__tune-row">
                        <span class="wb-r5__tune-label">评分变化</span>
                        <span class="wb-r5__tune-val">
                          <template v-if="summary.trackerTimeline.effectCompare?.scoreChange">
                            {{ summary.trackerTimeline.effectCompare.scoreChange.before ?? '—' }}
                            → {{ summary.trackerTimeline.effectCompare.scoreChange.after ?? '—' }}
                            <span
                              v-if="summary.trackerTimeline.effectCompare.scoreChange.change != null"
                              :style="{ color: summary.trackerTimeline.effectCompare.scoreChange.improved ? '#1a7f4b' : '#c23434' }"
                            >({{ summary.trackerTimeline.effectCompare.scoreChange.change > 0 ? '+' : '' }}{{ summary.trackerTimeline.effectCompare.scoreChange.change }})</span>
                          </template>
                          <span v-else>—</span>
                        </span>
                      </div>
                      <div class="wb-r5__tune-row">
                        <span class="wb-r5__tune-label">实施时间</span>
                        <span class="wb-r5__tune-val">{{
                          summary.trackerTimeline.implementedAt
                            ? formatTime(summary.trackerTimeline.implementedAt)
                            : '—'
                        }}</span>
                      </div>
                      <div class="wb-r5__tune-row">
                        <span class="wb-r5__tune-label">超期</span>
                        <span class="wb-r5__tune-val">
                          <Tag
                            v-if="summary.trackerTimeline.isOverdue"
                            color="red"
                            class="!text-[10px] !leading-none !px-1.5 !py-0"
                          >超 {{ summary.trackerTimeline.overdueHours ?? 0 }}h</Tag>
                          <span v-else style="color: hsl(var(--foreground) / 45%)">否</span>
                        </span>
                      </div>
                    </div>
                  </template>
                  <div v-else class="wb-r5__empty-mini">暂无验证数据</div>
                </div>
              </div>
            </section>
            <section
              v-if="summary?.trackerTimeline?.effectCompare"
              class="wb-r6"
            >
              <WorkbenchEffectCompare
                :effect-compare="summary.trackerTimeline.effectCompare"
                :tracker-status="summary.trackerTimeline.actionStatus"
              />
            </section>
          </template>
        </main>

        <!-- ===== 右决策栏 ===== -->
        <aside v-if="selectedLoop" class="wb-decision">
          <!-- Decision Dock（唯一下一步） -->
          <div v-if="summary?.nextAction" class="wb-decision__dock">
            <WorkbenchNextAction
              :next-action="summary.nextAction"
              @action="handleNextAction"
            />
          </div>
          <!-- 活跃关注 -->
          <div v-if="summary?.activeAttention" class="wb-decision__attention">
            <div class="wb-decision__section-title">活跃关注</div>
            <WorkbenchActiveAttention
              :active-attention="summary.activeAttention"
              :loop-id="selectedLoopId ?? ''"
            />
          </div>
          <!-- 闭环时间线 -->
          <div class="wb-decision__timeline">
            <div class="wb-decision__section-title">闭环时间线</div>
            <WorkbenchTrackerTimeline
              v-if="summary?.trackerTimeline"
              :tracker="summary.trackerTimeline"
              :unavailable="
                summary?.unavailableSections?.includes('trackerTimeline') ??
                false
              "
              :compact="true"
              @view-detail="handleTrackerViewDetail"
              @verify="handleTrackerVerify"
            />
            <div v-else class="wb-decision__empty">暂无时间线</div>
          </div>
        </aside>
      </div>
    </template>

    <!-- ===== 弹窗组件 ===== -->
    <AssessTriggerModal
      v-model:open="assessModalOpen"
      :loop-tag-name="selectedLoop?.tagName"
      @trigger="triggerAssessment"
    />
    <DiagnosisTriggerModal
      v-model:open="diagModalOpen"
      :loop-tag-name="selectedLoop?.tagName"
      @trigger="triggerDiagnosis"
    />
    <TuningTriggerModal
      v-model:open="tuningModalOpen"
      :loop-tag-name="selectedLoop?.tagName"
      @trigger="triggerTuning"
    />
    <TuneParamModal
      v-model:open="tuneParamModalOpen"
      :loop-tag-name="selectedLoop?.tagName"
      :model-type="tuningLatest?.modelType ?? null"
      :model-params="tuningLatest?.modelParams ?? null"
      :current-pid="currentPid"
      @tune="requestTune"
    />
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
    <SimulateResultModal
      v-model:open="simulateModalOpen"
      :loop-tag-name="selectedLoop?.tagName"
      :result="simulateResult"
    />
    <ClpmAiDrawer
      v-model:open="aiDrawerOpen"
      scene="performance"
      :loop-id="selectedLoopId"
    />
  </Page>
</template>

<style scoped>
/* ===== 三栏布局 ===== */
.wb-layout {
  display: flex;
  gap: 6px;
  height: calc(100vh - 110px);
  min-height: 0;
}

.wb-layout--collapsed .wb-sidebar {
  width: 28px;
  overflow: hidden;
}

.wb-layout--collapsed .wb-sidebar__search,
.wb-layout--collapsed .wb-sidebar__tree,
.wb-layout--collapsed .wb-sidebar__list-title,
.wb-layout--collapsed .wb-sidebar__list,
.wb-layout--collapsed .wb-sidebar__toggle span {
  display: none;
}

/* ===== 左脊柱 ===== */
.wb-sidebar {
  display: flex;
  flex: 0 0 240px;
  flex-direction: column;
  overflow: hidden;
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border) / 60%);
  border-radius: 6px;
  transition:
    flex-basis 0.2s ease,
    width 0.2s ease;
}

.wb-sidebar__search {
  flex: 0 0 auto;
  padding: 6px 8px;
  border-bottom: 1px solid hsl(var(--border) / 40%);
}

/* 装置树区 */
.wb-sidebar__tree {
  display: flex;
  flex: 0 0 auto;
  flex-direction: column;
  min-height: 0;
  max-height: 35%;
  border-bottom: 1px solid hsl(var(--border) / 40%);
}

.wb-sidebar__tree-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 8px 2px;
  font-size: 11px;
  font-weight: 600;
  color: hsl(var(--foreground) / 50%);
}

.wb-sidebar__tree-clear {
  padding: 0 4px;
  font-size: 10px;
  color: hsl(var(--primary));
  cursor: pointer;
  background: none;
  border: 0;
}

.wb-sidebar__tree .ant-spin-nested-loading {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}

.wb-sidebar__tree-empty {
  padding: 16px;
  font-size: 11px;
  color: hsl(var(--foreground) / 40%);
  text-align: center;
}

/* 紧凑型装置树 */
.wb-plant-tree {
  font-size: 12px;
}

.wb-plant-tree :deep(.ant-tree-node-content-wrapper) {
  min-height: 26px;
  padding: 0 4px;
  font-size: 12px;
  line-height: 26px;
}

.wb-plant-tree :deep(.ant-tree-switcher) {
  width: 16px;
}

.wb-plant-tree :deep(.ant-tree-title) {
  font-size: 12px;
}

.wb-plant-tree
  :deep(.ant-tree .ant-tree-node-content-wrapper.ant-tree-node-selected) {
  background: hsl(var(--primary) / 12%);
}

/* 回路列表标题 */
.wb-sidebar__list-title {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  justify-content: space-between;
  padding: 4px 8px 2px;
  font-size: 11px;
  font-weight: 600;
  color: hsl(var(--foreground) / 50%);
  border-bottom: 1px solid hsl(var(--border) / 40%);
}

.wb-sidebar__list-count {
  font-size: 10px;
  font-weight: 400;
  color: hsl(var(--foreground) / 35%);
}

.wb-sidebar__list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}

.wb-sidebar__empty {
  padding: 32px 0;
}

.wb-sidebar__error {
  display: flex;
  flex-direction: column;
  gap: 8px;
  align-items: center;
  padding: 24px;
  font-size: 12px;
  color: hsl(var(--destructive));
  text-align: center;
}

.wb-sidebar__toggle {
  display: flex;
  flex: 0 0 24px;
  align-items: center;
  justify-content: center;
  height: 24px;
  font-size: 10px;
  color: hsl(var(--foreground) / 50%);
  cursor: pointer;
  background: hsl(var(--muted) / 30%);
  border: 0;
  border-top: 1px solid hsl(var(--border) / 40%);
}

.wb-sidebar__toggle:hover {
  background: hsl(var(--muted) / 50%);
}

/* 回路列表项 */
.wb-loop-item {
  padding: 6px 10px;
  cursor: pointer;
  border-bottom: 1px solid hsl(var(--border) / 30%);
  transition: background 0.15s;
}

.wb-loop-item:hover {
  background: hsl(var(--primary) / 5%);
}

.wb-loop-item--active {
  padding-left: 7px;
  background: hsl(var(--primary) / 8%);
  border-left: 3px solid hsl(var(--primary));
}

.wb-loop-item__header {
  display: flex;
  gap: 6px;
  align-items: center;
  justify-content: space-between;
}

.wb-loop-item__tag {
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 13px;
  font-weight: 600;
  color: hsl(var(--foreground));
  white-space: nowrap;
}

.wb-loop-item__conf {
  flex-shrink: 0;
  font-size: 11px;
  font-weight: 700;
}

.wb-loop-item__conf--ok {
  color: #1a7f4b;
}

.wb-loop-item__conf--warn {
  color: #b45309;
}

.wb-loop-item__conf--err {
  color: #c23434;
}

.wb-loop-item__desc {
  display: flex;
  gap: 6px;
  align-items: center;
  justify-content: space-between;
  margin-top: 2px;
}

.wb-loop-item__name {
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 11px;
  color: hsl(var(--foreground) / 50%);
  white-space: nowrap;
}

.wb-loop-item__score {
  flex-shrink: 0;
  font-size: 11px;
  color: hsl(var(--foreground) / 50%);
}

.wb-loop-item__vals {
  display: flex;
  gap: 6px;
  margin-top: 3px;
  font-size: 10px;
  color: hsl(var(--foreground) / 45%);
  white-space: nowrap;
}

.wb-loop-item__mode {
  overflow: hidden;
  text-overflow: ellipsis;
}

/* ===== 主区域 ===== */
.wb-main {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
  min-height: 0;
}

.wb-deeplink-hint {
  padding: 4px 10px;
  font-size: 11px;
  color: #b45309;
  background: #fff8ec;
  border: 1px solid #f0d5a8;
  border-radius: 4px;
}

.wb-state {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 8px;
  align-items: center;
  justify-content: center;
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border) / 60%);
  border-radius: 6px;
}

.wb-state__hint {
  font-size: 12px;
  color: hsl(var(--foreground) / 45%);
}

/* ===== R1 页头 ===== */
.wb-r1 {
  display: flex;
  flex: 0 0 36px;
  gap: 10px;
  align-items: center;
  justify-content: space-between;
  padding: 0 10px;
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border) / 60%);
  border-radius: 4px;
}

.wb-r1__left {
  display: flex;
  gap: 8px;
  align-items: center;
  min-width: 0;
}

.wb-r1__tag {
  flex-shrink: 0;
  font-family: 'SF Mono', Consolas, monospace;
  font-size: 14px;
  font-weight: 700;
  color: hsl(var(--foreground));
}

.wb-r1__desc {
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 12px;
  color: hsl(var(--foreground) / 70%);
  white-space: nowrap;
}

.wb-r1__crumb {
  flex-shrink: 0;
  font-size: 11px;
  color: hsl(var(--foreground) / 40%);
}

.wb-r1__right {
  display: flex;
  flex-shrink: 0;
  gap: 8px;
  align-items: center;
}

.wb-r1__mode-pill {
  padding: 1px 7px;
  font-size: 10px;
  font-weight: 600;
  border: 1px solid;
  border-radius: 3px;
}

.wb-r1__mode-pill--auto {
  color: #1a7f4b;
  background: #e7f6ec;
  border-color: #bfe6cd;
}

.wb-r1__mode-pill--man {
  color: #c23434;
  background: #fde8e8;
  border-color: #f5b0b0;
}

.wb-r1__fresh {
  display: flex;
  gap: 4px;
  align-items: center;
  font-size: 10px;
  color: hsl(var(--foreground) / 45%);
  white-space: nowrap;
}

.wb-r1__dot {
  display: inline-block;
  width: 7px;
  height: 7px;
  background: #1a7f4b;
  border-radius: 50%;
}

.wb-r1__dot--stale {
  background: #c23434;
}

/* ===== R2 状态条 ===== */
.wb-r2 {
  display: flex;
  flex: 0 0 32px;
  gap: 16px;
  align-items: center;
  justify-content: space-between;
  padding: 0 10px;
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border) / 60%);
  border-radius: 4px;
}

.wb-r2--loading {
  gap: 6px;
  justify-content: flex-start;
  font-size: 12px;
  color: hsl(var(--foreground) / 45%);
}

.wb-r2__left {
  display: flex;
  gap: 16px;
  align-items: center;
}

.wb-r2__score {
  display: flex;
  gap: 4px;
  align-items: center;
  font-size: 12px;
  color: hsl(var(--foreground) / 60%);
  white-space: nowrap;
}

.wb-r2__score-val {
  font-family: 'SF Mono', Consolas, monospace;
  font-size: 14px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  color: hsl(var(--foreground));
}

.wb-r2__grade {
  padding: 1px 6px;
  font-size: 10px;
  font-weight: 700;
  border: 1px solid;
  border-radius: 3px;
}

.wb-r2__item {
  display: flex;
  gap: 4px;
  align-items: center;
  font-size: 12px;
  color: hsl(var(--foreground) / 60%);
  white-space: nowrap;
}

.wb-r2__partial {
  padding: 1px 7px;
  font-size: 10px;
  color: #b45309;
  background: #fff8ec;
  border: 1px solid #f0d5a8;
  border-radius: 3px;
}

/* 生命周期内联 */
.wb-r2__lifecycle {
  display: flex;
  gap: 2px;
  align-items: center;
  font-size: 10px;
  white-space: nowrap;
}

.wb-r2__stage {
  padding: 1px 4px;
  color: hsl(var(--foreground) / 40%);
  cursor: pointer;
  border-radius: 2px;
  transition: color 0.15s;
}

.wb-r2__stage:hover {
  color: hsl(var(--foreground) / 70%);
}

.wb-r2__stage--done {
  color: #1a7f4b;
}

.wb-r2__stage--cur {
  font-weight: 700;
  color: #b45309;
}

.wb-r2__stage-sep {
  color: hsl(var(--foreground) / 25%);
}

/* ===== R4 主画布 ===== */
.wb-r4 {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border) / 60%);
  border-radius: 4px;
}

.wb-r4__header {
  display: flex;
  flex: 0 0 auto;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
  padding: 4px 10px;
  border-bottom: 1px solid hsl(var(--border) / 40%);
}

.wb-r4__mode {
  display: flex;
  gap: 4px;
  align-items: center;
}

.wb-r4__time-window {
  display: flex;
  gap: 4px;
  align-items: center;
}

.wb-r4__custom-btn {
  height: 24px;
  padding: 0 8px;
  font-size: 11px;
  color: hsl(var(--primary));
  cursor: pointer;
  background: hsl(var(--primary) / 8%);
  border: 1px solid hsl(var(--primary) / 20%);
  border-radius: 3px;
}

.wb-r4__custom-pop {
  position: absolute;
  right: 10px;
  z-index: 6;
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 6px 10px;
  font-size: 11px;
  color: hsl(var(--foreground) / 60%);
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border) / 60%);
  border-radius: 4px;
  box-shadow: 0 4px 16px rgb(15 23 42 / 12%);
}

.wb-r4__custom-pop input {
  width: 130px;
  padding: 2px 4px;
  font-family: 'SF Mono', Consolas, monospace;
  font-size: 10px;
  border: 1px solid hsl(var(--border) / 60%);
  border-radius: 3px;
}

.wb-r4__custom-pop button {
  height: 24px;
  padding: 0 10px;
  font-size: 11px;
  color: #fff;
  cursor: pointer;
  background: hsl(var(--primary));
  border: 0;
  border-radius: 3px;
}

/* 图例行 */
.wb-r4__legend {
  display: flex;
  flex: 0 0 auto;
  gap: 12px;
  align-items: center;
  padding: 2px 10px;
  font-size: 11px;
  color: hsl(var(--foreground) / 60%);
  border-bottom: 1px solid hsl(var(--border) / 40%);
}

.wb-r4__legend-item {
  display: flex;
  gap: 4px;
  align-items: center;
  white-space: nowrap;
}

.wb-r4__legend-line {
  display: inline-block;
  width: 16px;
  height: 2px;
  border-radius: 1px;
}

.wb-r4__legend-line--pv {
  background: #1d4ed8;
}

.wb-r4__legend-line--sp {
  background: #6b7280;
}

.wb-r4__legend-line--op {
  background: #b45309;
}

.wb-r4__legend-line--score {
  background: #1d4ed8;
}

.wb-r4__legend-line--steady {
  background: #1a7f4b;
}

.wb-r4__legend-line--accuracy {
  background: #7c3aed;
}

.wb-r4__legend-line--fast {
  background: #b45309;
}

/* 实时点值 */
.wb-r4__live-vals {
  display: flex;
  gap: 10px;
  margin-left: auto;
  white-space: nowrap;
}

.wb-r4__live-val {
  font-size: 11px;
  color: hsl(var(--foreground) / 50%);
}

.wb-r4__live-num {
  font-family: 'SF Mono', Consolas, monospace;
  font-size: 12px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: hsl(var(--foreground));
}

/* 图表容器 */
.wb-r4__chart {
  position: relative;
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 200px;
}

/* Spin 包装器高度传递 */
.wb-r4__chart :deep(.ant-spin-nested-loading) {
  flex: 1;
  min-height: 0;
}

.wb-r4__chart :deep(.ant-spin-container) {
  position: relative;
  height: 100%;
}

/* ===== R5 证据四区 ===== */
.wb-r5 {
  display: flex;
  flex: 0 0 200px;
  gap: 4px;
}

.wb-r5__card {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border) / 60%);
  border-radius: 4px;
}

.wb-r5__card-header {
  display: flex;
  flex: 0 0 auto;
  gap: 6px;
  align-items: center;
  padding: 4px 8px;
  border-bottom: 1px solid hsl(var(--border) / 40%);
}

.wb-r5__card-title {
  font-size: 12px;
  font-weight: 600;
  color: hsl(var(--foreground));
}

.wb-r5__card-meta {
  font-size: 10px;
  color: hsl(var(--foreground) / 40%);
}

.wb-r5__card-link {
  margin-left: auto;
  font-size: 10px;
  color: hsl(var(--primary));
  white-space: nowrap;
  text-decoration: none;
}

.wb-r5__card-link:hover {
  text-decoration: underline;
}

/* 评估卡 */
.wb-r5__assess-body {
  display: flex;
  flex: 1;
  gap: 4px;
  min-height: 0;
}

.wb-r5__radar {
  position: relative;
  flex: 0 0 45%;
  min-height: 0;
}

.wb-r5__bars {
  position: relative;
  flex: 1;
  min-height: 0;
}

/* 诊断卡 */
.wb-r5__diag-body {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 4px;
  min-height: 0;
  padding: 4px;
  font-size: 12px;
}

.wb-r5__diag-labels {
  display: flex;
  flex-wrap: wrap;
  gap: 3px;
}

.wb-r5__diag-empty-label {
  font-size: 11px;
  color: hsl(var(--foreground) / 40%);
}

.wb-r5__diag-stats {
  display: flex;
  gap: 8px;
}

.wb-r5__diag-stat {
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.wb-r5__diag-stat-label {
  font-size: 10px;
  color: hsl(var(--foreground) / 45%);
}

.wb-r5__diag-stat-val {
  font-family: 'SF Mono', Consolas, monospace;
  font-size: 13px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: hsl(var(--foreground));
}

.wb-r5__diag-ext {
  flex: 1;
  min-height: 0;
}

/* 算法特征值网格 */
.wb-r5__diag-features {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 2px 8px;
  padding: 2px 0;
  font-size: 11px;
}

.wb-r5__diag-feature {
  display: flex;
  gap: 4px;
  align-items: baseline;
}

.wb-r5__diag-feature-label {
  color: hsl(var(--foreground) / 45%);
  white-space: nowrap;
}

.wb-r5__diag-feature-val {
  font-family: 'SF Mono', Consolas, monospace;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: hsl(var(--foreground) / 85%);
}

/* 规则模板建议 */
.wb-r5__diag-reasoning {
  padding: 4px 6px;
  font-size: 11px;
  line-height: 1.4;
  color: hsl(var(--foreground) / 65%);
  background: hsl(var(--muted) / 40%);
  border-radius: 3px;
}

/* 整定卡 */
.wb-r5__tune-body {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
  padding: 4px;
}

.wb-r5__tune-rows {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 2px;
  min-height: 0;
}

.wb-r5__tune-row {
  display: flex;
  flex: 1;
  gap: 6px;
  align-items: center;
  padding: 2px 4px;
  font-size: 11px;
}

.wb-r5__tune-label {
  flex: 0 0 60px;
  color: hsl(var(--foreground) / 50%);
}

.wb-r5__tune-val {
  font-family: 'SF Mono', Consolas, monospace;
  font-weight: 500;
  font-variant-numeric: tabular-nums;
  color: hsl(var(--foreground) / 85%);
}

.wb-r5__tune-val--highlight {
  font-weight: 700;
  color: hsl(var(--primary));
}

.wb-r5__tune-val--mono {
  font-size: 10px;
  line-height: 1.3;
  word-break: break-all;
}

.wb-r5__tune-safety {
  flex: 0 0 auto;
  padding: 2px 4px;
  font-size: 10px;
  color: hsl(var(--foreground) / 35%);
  text-align: center;
  border-top: 1px dashed hsl(var(--border) / 40%);
}

/* 验证卡 */
.wb-r5__verify-body {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
  padding: 4px 6px;
}

.wb-r5__empty-mini {
  display: flex;
  flex: 1;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  color: hsl(var(--foreground) / 40%);
}

/* ===== R6 验证对比条 ===== */
.wb-r6 {
  flex: 0 0 auto;
}

/* ===== 右决策栏 ===== */
.wb-decision {
  display: flex;
  flex: 0 0 280px;
  flex-direction: column;
  gap: 4px;
  min-height: 0;
}

.wb-decision__dock {
  flex: 0 0 auto;
  background: hsl(var(--card));
  border: 1px solid hsl(var(--primary) / 20%);
  border-radius: 4px;
  box-shadow: 0 2px 8px hsl(var(--primary) / 8%);
}

.wb-decision__attention {
  flex: 0 0 auto;
  max-height: 120px;
  overflow: hidden;
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border) / 60%);
  border-radius: 4px;
}

.wb-decision__timeline {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 200px;
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border) / 60%);
  border-radius: 4px;
}

.wb-decision__section-title {
  flex: 0 0 auto;
  padding: 4px 8px;
  font-size: 11px;
  font-weight: 600;
  color: hsl(var(--foreground) / 60%);
  border-bottom: 1px solid hsl(var(--border) / 40%);
}

.wb-decision__empty {
  display: flex;
  flex: 1;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  color: hsl(var(--foreground) / 40%);
}
</style>
