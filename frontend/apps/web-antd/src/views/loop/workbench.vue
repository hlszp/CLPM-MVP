<script lang="ts" setup>
/**
 * 回路工作台（单页两区 · MVP 精简版）
 *
 * 双轴导航 · 实体轴：单回路 360° 一站式处置
 * master-detail 布局：左侧回路列表 + 右侧单页两区
 *
 * 两区垂直布局（概览 + 评估）：
 *   ① 回路概览：位号/名称/量程/控制方式/设定值/实时值/数据健康度
 *   ② 性能评估：12 大指标卡片 + 评分趋势图（8/12/24/48/72h 可切）
 *
 * 一页内一览概况并可直接发起评估任务、实时反写。详情走弹窗。
 * 点击左侧回路 → router.replace 更新 URL query；路由 meta.fullPathKey=false
 * 确保不新增 tab/面包屑，仅更新右侧子页面。
 */
import type { LoopApi } from '#/api/loop';
import type {
  KpiSnapshotItem,
  LoopConfidenceLatestItem,
  MetricApi,
} from '#/api/metric';
import type { MonitorApi } from '#/api/monitor';
import type { PlantNodeApi } from '#/api/plant-node';

import {
  computed,
  onMounted,
  onUnmounted,
  provide,
  reactive,
  ref,
  watch,
} from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';

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
  ClpmAiDrawer,
  ClpmDecisionDock,
  ClpmPageToolbar,
  ClpmStandardActions,
  ClpmToolbarButton,
} from '#/components/clpm';
import DayDeltaBadge from '#/components/loop/day-delta-badge.vue';
import LoopFleetView from '#/components/monitor/loop-fleet-view.vue';
import WorkbenchActiveAttention from '#/components/monitor/workbench-active-attention.vue';
import { useAiInsightGate } from '#/composables/use-ai-insight-gate';
import { useLatestRequest } from '#/composables/use-latest-request';
import { useLoopRealtime } from '#/composables/use-loop-realtime';
import { useMonitorContext } from '#/composables/use-monitor-context';
import { showPageHelp, usePageToolbar } from '#/composables/use-page-toolbar';
import { useScoreColor } from '#/composables/use-score-color';
import { useVirtualList } from '#/composables/use-virtual-list';
import { formatTime } from '#/utils/format';

import AssessTriggerModal from './components/assess-trigger-modal.vue';
import WorkbenchKpiHistory from './components/workbench-kpi-history.vue';
import WorkbenchMetricBars from './components/workbench-metric-bars.vue';
import WorkbenchProcessTrend from './components/workbench-process-trend.vue';
import WorkbenchRadar6 from './components/workbench-radar6.vue';
import { useWorkbenchTaskRunner } from './composables/use-workbench-task-runner';

defineOptions({ name: 'MonitorLoopWorkbench' });

const route = useRoute();
const router = useRouter();
// router 由 monitorCtx.update 内部调用 router.replace，此页面不再直接使用

/** 返回系统概览（面包屑导航） */
function goBackToOverview() {
  router.push({ path: '/dashboard/workbench' });
}

/** 初始化：从 URL query 消费 plantNodeId 上下文（从系统概览跳转时带过来） */
function initFromRouteQuery() {
  const plantNodeId = route.query.plantNodeId as string | undefined;
  if (plantNodeId) {
    monitorCtx.update({ plantNodeId });
    plantTreeSelectedKeys.value = [plantNodeId];
  }
}

// ===== 请求代次保护（MW-P0-04）=====
// 每次切换回路递增 epoch；异步响应写入前校验 epoch+loopId，丢弃旧响应。
const requestGuard = useLatestRequest<string>();

// ===== 共享监控上下文（MW-P1-01）=====
// URL 是真相源：view/loopId/plantNodeId/loopType/keyword/timeWindow 等
const monitorCtx = useMonitorContext();

// ===== 路由模式：左侧导航驱动右侧切换（选装置→清单，选回路→详情） =====

/** 回路清单中点击回路行 → 切换到该回路详情 */
function handleFleetLoopClick(loopId: string) {
  selectLoop(loopId);
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
 * 左栏虚拟滚动（MW-P0-01）：行高 32px（单行位号+置信度）。
 * 卡片精简为单行，回路名称/评分/实时 PV/SP/OP/MODE 等信息通过 Tooltip 悬停展示。
 * pageSize=100 时仅渲染可视窗口 + 5 行缓冲，长列表滚动不卡。
 */
// 按性能等级 + 关键词前端筛选（null = 全部）
const filteredLoopList = computed(() => {
  let list = loopList.value;
  const kw = searchKeyword.value.trim().toLowerCase();
  if (kw) {
    list = list.filter(
      (l) =>
        l.tagName?.toLowerCase().includes(kw) ||
        l.description?.toLowerCase().includes(kw) ||
        l.unitName?.toLowerCase().includes(kw),
    );
  }
  if (filterGrade.value) {
    list = list.filter((l) => performanceLevel(l.score) === filterGrade.value);
  }
  return list;
});

// 各性能等级的回路数量（基于当前已加载的回路列表）
const gradeCounts = computed(() => {
  const counts: Record<PerfGrade, number> = {
    A: 0,
    B: 0,
    C: 0,
    D: 0,
    E: 0,
  };
  for (const l of loopList.value) {
    const g = performanceLevel(l.score);
    if (g) counts[g]++;
  }
  return counts;
});

const {
  containerRef: loopListRef,
  offsetY: loopListOffsetY,
  onScroll: onLoopListScroll,
  totalHeight: loopListTotalHeight,
  visibleItems: visibleLoopItems,
} = useVirtualList({ itemHeight: 32, items: filteredLoopList });

/** 模板函数 ref：把容器元素写入组合式函数的 containerRef（函数 ref 对齐 VNodeRef 类型） */
function setLoopListRef(el: unknown) {
  loopListRef.value = (el as HTMLElement) || null;
}
const loopListLoading = ref(false);
const loopListError = ref('');
const searchKeyword = ref('');

// ===== 性能等级（基于综合评分 score）=====
// A(优)≥90  B(良)≥80  C(中)≥70  D(合格)≥60  E(差)<60
type PerfGrade = 'A' | 'B' | 'C' | 'D' | 'E';
function performanceLevel(score: null | number | undefined): null | PerfGrade {
  if (score == null) return null;
  if (score >= 90) return 'A';
  if (score >= 80) return 'B';
  if (score >= 70) return 'C';
  if (score >= 60) return 'D';
  return 'E';
}
const PERF_GRADES: PerfGrade[] = ['A', 'B', 'C', 'D', 'E'];
const filterGrade = ref<null | PerfGrade>(null);
function toggleGradeFilter(g: PerfGrade) {
  filterGrade.value = filterGrade.value === g ? null : g;
}

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
  // 切换装置时清除回路选择，右侧自动切换回回路清单
  if (selectedLoopId.value) {
    selectLoop(null);
    injectedLoop.value = null;
  }
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

// ===== 工作台摘要 summary（MW-P3-05~08）=====
// 首屏一次返回全部摘要（运行态/数据健康度/评分趋势/活跃关注/
// 评估/生命周期/nextAction）
const summary = ref<MonitorApi.WorkbenchSummary | null>(null);
const summaryLoading = ref(false);

async function loadSummary(loopId: string): Promise<void> {
  summaryLoading.value = true;
  await requestGuard.run(async (_signal, capturedEpoch) => {
    const data = await getWorkbenchSummaryApi(loopId).catch(() => null);
    if (!requestGuard.guard(loopId, capturedEpoch)) return;

    summary.value = data;
    summaryLoading.value = false;
  });
}

/** 生命周期条点击：滚动到对应 R 区 */
function handleLifecycleStageClick(stage: MonitorApi.LifecycleStageName): void {
  const map: Partial<Record<MonitorApi.LifecycleStageName, string>> = {
    ASSESS: '.wb-r5__card--assess',
    MONITOR: '.wb-r1',
  };
  const selector = map[stage];
  if (selector) {
    const el = document.querySelector(selector);
    el?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
}

/** nextAction 主动作点击：按 actionType 触发对应行为 */
function handleNextAction(actionType: MonitorApi.NextActionType): void {
  const loopId = selectedLoopId.value;
  switch (actionType) {
    case 'CONTINUE_MONITORING': {
      message.info('回路当前无开放问题，持续监控中');
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
    case 'RUN_ASSESSMENT': {
      assessModalOpen.value = true;
      break;
    }
    default: {
      break;
    }
  }
}

/** 发起诊断：跳转诊断工作台并携带当前回路上下文（MVP v2 诊断模块入口） */
function goDiagnose(): void {
  const loopId = selectedLoopId.value;
  if (!loopId) return;
  router.push({
    path: '/diagnosis/workbench',
    query: { loopId, from: 'workbench' },
  });
}

/** summary 评分趋势的 dayTrend 类型收窄（供 DayDeltaBadge 使用） */
type DayTrend = 'FLAT' | 'IMPROVED' | 'NEW' | 'WORSENED';

const summaryDayTrend = computed<DayTrend | null>(
  () =>
    (summary.value?.scoreTrend.dayTrend as DayTrend | null | undefined) ?? null,
);

// ===== 评估任务运行器 =====
// MW-P3-10：任务完成后同时刷新 summary（生命周期/nextAction/活跃关注）
const { triggerAssessment } = useWorkbenchTaskRunner(
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
  },
);

// ===== 发起弹窗状态 =====
const assessModalOpen = ref(false);

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

/** 左脊柱回路悬停 Tooltip：显示回路名称、评分、实时 PV/SP/OP/MODE 等完整信息 */
function buildLoopTooltip(item: LoopApi.MonitorListItem): string {
  const lines: string[] = [`位号：${item.tagName}`];
  if (item.description) lines.push(`名称：${item.description}`);
  if (item.confidenceLevel)
    lines.push(`数据可信度：${item.confidenceLevel} 级`);
  const perf = performanceLevel(item.score);
  if (perf) lines.push(`性能等级：${perf} 级`);
  if (item.score != null) {
    let line = `综合评分：${Number(item.score).toFixed(1)}`;
    if (item.scoreDelta != null) {
      const sign = item.scoreDelta > 0 ? '+' : '';
      line += `（日 ${sign}${Number(item.scoreDelta).toFixed(2)}）`;
    }
    lines.push(line);
  }
  const pvText = currentValueText(item.currentValues?.pv, item.pvUnit);
  const spText = currentValueText(item.currentValues?.sp, item.pvUnit);
  const opText = currentValueText(item.currentValues?.op, item.opUnit);
  const modeText = item.currentValues?.modeLabel || '—';
  lines.push(
    `PV ${pvText}  |  SP ${spText}`,
    `OP ${opText}  |  MODE ${modeText}`,
  );
  return lines.join('\n');
}

// ===== 派生：可信度标签颜色 =====
function confidenceTagColor(level?: string): string {
  if (level === 'A' || level === 'B') return 'green';
  if (level === 'C') return 'blue';
  if (level === 'D') return 'gold';
  return 'default';
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
      '左侧选择回路，右侧单页展示概览/评估概况。可直接发起评估任务，任务完成后自动反写。「AI 洞察」基于当前回路生成性能分析。',
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
        // 无深链接且未选中：保持未选中状态，右侧显示回路清单
        selectedLoopId.value = null;
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
  // 选中或清除都同步到 URL（清除时传空串移除 loopId 参数）
  monitorCtx.update({ loopId: loopId ?? '' });
}

// ===== 生命周期 =====
// MW-P0-03：不在 onMounted 预设 selectedLoopId——由 loadLoopList 解析深链接，
// 确认目标存在后再 selectLoop，避免对不存在回路发起无用请求。
onMounted(() => {
  // 初始化：从 URL query 消费跨页上下文（plantNodeId 等）
  initFromRouteQuery();
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
    } else {
      assessmentDetail.value = null;
      scoreHistory.value = [];
      loopDetail.value = null;
      summary.value = null;
      // 重置 loading 状态（guard 取消时不会重置，此处兜底）
      summaryLoading.value = false;
      assessmentLoading.value = false;
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
// 过程变量模式：曲线显隐控制（由图例点击驱动）
const processSeriesVisible = reactive({ pv: true, sp: true, op: true });
function toggleProcessSeries(key: 'op' | 'pv' | 'sp') {
  processSeriesVisible[key] = !processSeriesVisible[key];
}
// 时间窗（设计文档 §2.4：8h/24h/72h/168h/自定义）
const timeWindow = ref<'8h' | '24h' | '72h' | '168h' | 'custom'>('24h');
// 自定义时间窗起止（分钟精度）
const customStartTime = ref<string>('');
const customEndTime = ref<string>('');
// 定级阈值（动态加载，设计红线：禁止硬编码）
const gradingThresholds = ref<MetricApi.GradingThresholdItem[]>([]);
// 左脊柱折叠（沉浸模式）
const sidebarCollapsed = ref(false);
// 左脊柱完全隐藏（全屏布局：主区域扩展至全宽）
const leftSpineHidden = ref(false);

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
      case 0: {
        modeLabel = 'MANUAL';
        break;
      }
      case 1: {
        modeLabel = 'AUTO';
        break;
      }
      case 2: {
        modeLabel = 'CAS';
        break;
      }
      default: {
        modeLabel = String(mode ?? 'UNKNOWN');
      }
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
    } else
      switch (timeWindow.value) {
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

/** R5 指标横道图数据（7 项正向指标达成度） */
const metricBarsData = computed(() => {
  const s = latestSnapshot.value;
  if (!s) return [];
  const score = summary.value?.scoreTrend.score ?? 0;
  return [
    { name: '综合评分', threshold: 80, value: score },
    { name: '准确率', threshold: 80, value: s.accuracyRate ?? 0 },
    { name: '快速率', threshold: 80, value: s.fastRate ?? 0 },
    { name: '平稳率', threshold: 80, value: s.steadyRate ?? 0 },
    { name: '有效自控率', threshold: 80, value: s.effectiveAutoRate ?? 0 },
    { name: '自控率', threshold: 80, value: s.autoModeRate ?? 0 },
    { name: '好值率', threshold: 80, value: s.goodValueRate ?? 0 },
  ];
});

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
// 从 selectedLoop.currentValues 读取（由 WS 实时推送更新），不再依赖 REST summary 快照
const runtimePointValues = computed(() => {
  const loop = selectedLoop.value;
  const r = loop?.currentValues;
  if (!r) return null;
  return {
    mode:
      r.modeLabel ?? (r.mode === 1 ? 'AUTO' : (r.mode === 0 ? 'MANUAL' : '—')),
    op: r.op,
    pv: r.pv,
    pvQuality: r.pvQuality,
    pvUnit: loop?.pvUnit ?? '',
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
  const raw = summary.value.lifecycle.stages;
  // MVP 精简：仅保留评估和数据两个阶段
  const filtered = raw.filter((s) => ['ASSESS', 'MONITOR'].includes(s.stage));
  return filtered.map((s) => ({
    label: stageLabelMap[s.stage] ?? s.stage,
    stage: s.stage,
    status: s.status,
  }));
});

const stageLabelMap: Record<string, string> = {
  ASSESS: '评估',
  MONITOR: '数据',
};
</script>

<template>
  <Page>
    <ClpmPageToolbar :loading="loopListLoading">
      <!-- 从系统概览跳转过来时显示返回面包屑 -->
      <template #context v-if="route.query.from === 'overview'">
        <button
          class="flex items-center gap-1 rounded border border-transparent px-2 py-0.5 text-xs text-blue-600 hover:border-blue-200 hover:bg-blue-50"
          @click="goBackToOverview"
        >
          <span>←</span>
          <span>系统概览</span>
        </button>
      </template>
      <template #actions>
        <ClpmToolbarButton
          :disabled="!selectedLoopId"
          :disabled-reason="selectedLoopId ? undefined : '先选择回路'"
          icon="lucide:stethoscope"
          label="发起诊断"
          @click="goDiagnose"
        />
        <ClpmStandardActions :items="toolbarItems" />
      </template>
    </ClpmPageToolbar>

    <!-- ===== 工作台布局：左脊柱 + 右侧动态切换（清单/详情） ===== -->
    <div
      class="wb-layout"
      :class="{
        'wb-layout--collapsed': sidebarCollapsed,
        'wb-layout--fullscreen': leftSpineHidden,
      }"
    >
      <!-- ===== 统一 CSS Grid：左脊柱通高 + 上部(趋势+决策) + 下部(4卡片) ===== -->
      <!-- ===== 左脊柱：装置树 + 回路列表（grid-area: sidebar，跨全部3行全高） ===== -->
      <aside v-show="!leftSpineHidden" class="wb-sidebar">
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
          <span class="wb-sidebar__list-count"
            >{{ filteredLoopList.length }}/{{ loopList.length }} 条</span
          >
        </div>
        <div class="wb-sidebar__search">
          <Input
            v-model:value="searchKeyword"
            placeholder="搜索位号/描述..."
            allow-clear
            size="small"
          />
          <div class="wb-sidebar__grade-filter">
            <button
              v-for="g in PERF_GRADES"
              :key="g"
              class="wb-grade-chip"
              :class="[
                `wb-grade-chip--${g.toLowerCase()}`,
                { 'wb-grade-chip--active': filterGrade === g },
              ]"
              :title="`筛选 ${g} 级回路`"
              @click="toggleGradeFilter(g)"
            >
              {{ g
              }}<span class="wb-grade-chip__count">{{ gradeCounts[g] }}</span>
            </button>
          </div>
        </div>
        <div class="wb-sidebar__list-wrap">
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
                  <div v-for="{ item } in visibleLoopItems" :key="item.loopId">
                    <Tooltip :title="buildLoopTooltip(item)" placement="right">
                      <div
                        class="wb-loop-item"
                        :class="{
                          'wb-loop-item--active':
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
                        <div class="wb-loop-item__header">
                          <span class="wb-loop-item__tag">{{
                            item.tagName
                          }}</span>
                          <span
                            v-if="performanceLevel(item.score)"
                            class="wb-loop-item__conf"
                            :class="`wb-loop-item__conf--${performanceLevel(item.score)!.toLowerCase()}`"
                            >{{ performanceLevel(item.score) }}</span
                          >
                        </div>
                      </div>
                    </Tooltip>
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
        </div>
        <!-- 沉浸模式折叠按钮 -->
        <button
          class="wb-sidebar__toggle"
          @click="sidebarCollapsed = !sidebarCollapsed"
        >
          <span v-if="sidebarCollapsed">▶</span>
          <span v-else>◀</span>
        </button>
      </aside>

      <!-- ===== 回路不存在空态 ===== -->
      <div
        v-if="loopNotFound"
        class="wb-state wb-state--error wb-state--gridfull"
      >
        <Empty
          description="回路不存在或已停用"
          :image="Empty.PRESENTED_IMAGE_SIMPLE"
        />
        <div class="wb-state__hint">
          URL 中的回路 ID 无效或已停用，请从左侧选择其他回路。
        </div>
      </div>

      <!-- ===== 回路清单区（恢复：未选回路时显示当前装置范围的回路清单） ===== -->
      <div v-else-if="!selectedLoop" class="wb-fleet-area">
        <LoopFleetView @loop-click="handleFleetLoopClick" />
      </div>

      <!-- ===== 顶部行（grid-area: toprow，跨中间+决策两列宽）：R1页头 + R2状态条 ===== -->
      <div class="wb-top-row" v-if="selectedLoop">
        <!-- 深链接提示 -->
        <div v-if="injectedLoop" class="wb-deeplink-hint" role="status">
          当前回路不在筛选结果中，已从深链接定位。可清空筛选以在左侧列表查看。
        </div>

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
                'wb-r1__mode-pill--auto': runtimePointValues.mode === 'AUTO',
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
            <span v-if="summary.dataHealth.confidenceLevel" class="wb-r2__item">
              可信度
              <Tag
                :color="confidenceTagColor(summary.dataHealth.confidenceLevel)"
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
          <!-- 全屏布局切换按钮 -->
          <button
            class="wb-r2__fullscreen-btn"
            :title="leftSpineHidden ? '恢复三栏布局' : '主区域扩展至全宽'"
            @click="leftSpineHidden = !leftSpineHidden"
          >
            <span style="font-size: 13px">{{
              leftSpineHidden ? '▦' : '⬌'
            }}</span>
            <span style="margin-left: 3px; font-size: 11px">
              {{ leftSpineHidden ? '退出全宽' : '全宽' }}
            </span>
          </button>
        </div>
        <!-- summary 加载中骨架 -->
        <div
          v-else-if="summaryLoading && selectedLoop"
          class="wb-r2 wb-r2--loading"
        >
          <Spin size="small" />
          <span class="wb-r2__loading-text">正在加载工作台摘要…</span>
        </div>
      </div>

      <!-- ===== R4 主画布（grid-area: r4，仅中间列） ===== -->
      <div class="wb-r4-wrapper">
        <template v-if="selectedLoop">
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
                <span
                  class="wb-r4__legend-item"
                  :class="{
                    'wb-r4__legend-item--hidden': !processSeriesVisible.pv,
                  }"
                  @click="toggleProcessSeries('pv')"
                  ><span
                    class="wb-r4__legend-line wb-r4__legend-line--pv"
                  ></span
                  >PV</span
                >
                <span
                  class="wb-r4__legend-item"
                  :class="{
                    'wb-r4__legend-item--hidden': !processSeriesVisible.sp,
                  }"
                  @click="toggleProcessSeries('sp')"
                  ><span
                    class="wb-r4__legend-line wb-r4__legend-line--sp"
                  ></span
                  >SP</span
                >
                <span
                  class="wb-r4__legend-item"
                  :class="{
                    'wb-r4__legend-item--hidden': !processSeriesVisible.op,
                  }"
                  @click="toggleProcessSeries('op')"
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
                  :pv-range="null"
                  :event-marks="eventMarks"
                  :mode-bands="modeBands"
                  :series-visible="processSeriesVisible"
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
        </template>
      </div>

      <!-- ===== 右决策栏（grid-area: decision，仅最右列，只与 R4 等高） ===== -->
      <aside v-if="selectedLoop" class="wb-decision">
        <!-- Decision Dock（唯一下一步） -->
        <div class="wb-decision__dock">
          <ClpmDecisionDock
            :floating="false"
            :next-action="summary?.nextAction ?? null"
            :loading="summaryLoading"
            :has-data="summary != null"
            :partial="summary?.partial ?? false"
            :stale="summary?.dataFreshness?.status === 'DELAYED'"
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
      </aside>

      <!-- ===== 下层：R5证据区（仅回路详情模式显示） ===== -->
      <div v-if="selectedLoop" class="wb-main-lower">
        <!-- ===== R5 证据区（评估.综合性能 / 评估.指标详情） ===== -->
        <section class="wb-r5">
          <!-- 评估.综合性能卡（雷达图） -->
          <div class="wb-r5__card wb-r5__card--assess">
            <div class="wb-r5__card-header">
              <span class="wb-r5__card-title">评估.综合性能</span>
              <span class="wb-r5__card-meta">
                {{
                  latestSnapshot
                    ? `24h · ${formatTime(latestSnapshot.tsStart)}`
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
                >详情 →</router-link
              >
            </div>
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
          </div>

          <!-- 评估.指标详情卡（指标横道图） -->
          <div class="wb-r5__card wb-r5__card--metric">
            <div class="wb-r5__card-header">
              <Tooltip
                title="正向指标：横道条越长表示指标达成度越高（性能越好）"
              >
                <span class="wb-r5__card-title">评估.指标详情</span>
              </Tooltip>
              <span class="wb-r5__card-meta">8 项指标达成度</span>
            </div>
            <div class="wb-r5__bars">
              <WorkbenchMetricBars
                :metrics="metricBarsData"
                :show-hint="false"
              />
            </div>
          </div>
        </section>
      </div>
    </div>

    <!-- ===== 弹窗组件 ===== -->
    <AssessTriggerModal
      v-model:open="assessModalOpen"
      :loop-tag-name="selectedLoop?.tagName"
      @trigger="triggerAssessment"
    />
    <ClpmAiDrawer
      v-model:open="aiDrawerOpen"
      scene="performance"
      :loop-id="selectedLoopId"
    />
  </Page>
</template>

<style scoped>
/* ===== 统一 CSS Grid 布局：左脊柱通高 + 上部(趋势+决策) + 下部(4卡片) =====
 *   列宽：左脊柱(240px / 折叠 28px / 全屏 0) · 主区域(1fr) · 决策栏(280px)
 *   行高：R1R2(auto) · R4+决策栏(1fr) · R5(auto)
 * Grid Areas:
 *   ┌─────────┬─────────┬──────────┐
 *   │ sidebar │ toprow  │ toprow   │  行1 (auto)
 *   ├─────────┼─────────┼──────────┤
 *   │ sidebar │ r4      │ decision │  行2 (1fr)
 *   ├─────────┼─────────┼──────────┤
 *   │ sidebar │ lower   │ lower    │  行3 (auto)
 *   └─────────┴─────────┴──────────┘
 * sidebar 跨全部 3 行 = 页面全高
 */

.wb-layout {
  display: grid;
  grid-template: 'sidebar toprow   toprow' auto 'sidebar r4       decision' 1fr 'sidebar lower    lower' auto / 240px 1fr 280px;
  gap: 6px;
  height: calc(100vh - 110px);
  min-height: 0;
}

/* 回路清单模式：跨右侧全部区域（toprow + r4 + decision + lower） */
.wb-fleet-area {
  grid-row: 1 / -1;
  grid-column: 2 / -1;
  min-width: 0;
  min-height: 0;
  overflow: auto;
}

/* 回路清单区（未选回路时显示，恢复内嵌 LoopFleetView） */

/* 折叠态：左脊柱 28px 窄条 */
.wb-layout--collapsed {
  grid-template-columns: 28px 1fr 280px;
}

/* 全屏态：左脊柱隐藏，只剩中间+决策两列 */
.wb-layout--fullscreen {
  grid-template-areas:
    'toprow   toprow'
    'r4       decision'
    'lower    lower';
  grid-template-columns: 1fr 280px;
}

/* 各子项 grid-area 分配（.wb-sidebar 的 grid-area 见左脊柱完整样式块） */
.wb-top-row {
  display: flex;
  flex-direction: column;
  grid-area: toprow;
  gap: 4px;
  min-width: 0;
}

.wb-r4-wrapper {
  display: flex;
  grid-area: r4;
  width: 100%;
  min-width: 0;
  height: 100%;
  min-height: 0;
}

/* R4 内部撑满 wrapper */
.wb-r4-wrapper .wb-r4 {
  flex: 1;
  width: 100%;
  min-width: 0;
  min-height: 0;
}

.wb-decision {
  display: flex;
  flex-direction: column;
  grid-area: decision;
  gap: 4px;
  min-width: 0;
  min-height: 0;

  /* 决策栏整体不做滚动：dock+attention 固定，timeline 独立溢出 */
  overflow: visible;
}

/* 空态跨中间+决策两列通高（行1→行3，覆盖 toprow+r4+decision+lower 全部行） */
.wb-state--gridfull {
  grid-row: 1 / 4;
  grid-column: 2 / 4;
  width: 100%;
  height: 100%;
}

.wb-layout--fullscreen .wb-state--gridfull {
  grid-row: 1 / 4;
  grid-column: 1 / 3;
}

.wb-main-lower {
  display: flex;
  flex-direction: column;
  grid-area: lower;
  gap: 6px;
  min-width: 0;
  min-height: 0;
}

/* 左脊柱折叠：隐藏内部内容（保留 grid 列宽占位 28px） */
.wb-layout--collapsed .wb-sidebar {
  overflow: hidden;
}

.wb-layout--collapsed .wb-sidebar__search,
.wb-layout--collapsed .wb-sidebar__tree,
.wb-layout--collapsed .wb-sidebar__list-title,
.wb-layout--collapsed .wb-sidebar__list,
.wb-layout--collapsed .wb-sidebar__toggle span {
  display: none;
}

/* 全屏模式（左脊柱隐藏）：旧 wb-main 类的 max-width 兜底保留，当前布局不依赖 */
.wb-layout--fullscreen .wb-main {
  max-width: 100%;
}

/* ===== 左脊柱 ===== */
.wb-sidebar {
  display: flex;
  flex-direction: column;
  grid-area: sidebar;
  overflow: hidden;
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border) / 60%);
  border-radius: 6px;
}

.wb-sidebar__search {
  flex: 0 0 auto;
  padding: 4px 8px 6px;
}

/* 装置树区：占左脊柱 1/3 高度（比例分配，非固定%） */
.wb-sidebar__tree {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
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

/* ===== 左侧两区域滚动条规范 =====
 * 高度传递策略：
 *   - 外层（wb-sidebar__tree / wb-sidebar__list-wrap）使用 Flex flex-basis:0% 分配比例空间。
 *   - 中间层（ant-spin-nested-loading）留 relative 定位作为 absolute 定位上下文，
 *     ant-spin-container 使用 position:absolute + inset:0 强制贴满父容器，
 *     彻底绕开 Ant Design 对 Spin 容器默认样式的高度竞争。
 *   - 最内层实际内容区（plant-tree / wb-sidebar__list）也使用 absolute+inset:0，
 *     并统一设置 overflow-y:auto + scrollbar-gutter:stable。
 *
 * 这样保证：
 *   - 虚拟滚动占位 phantom（864px）无法反向撑破任何一层容器；
 *   - 装置树节点展开后超出时，滚动条只出现在 plant-tree 本身；
 *   - 回路标题、装置标题永远不参与滚动（始终固定）。
 */

/* —— 装置树：标题固定 + 内容滚动 —— */
.wb-sidebar__tree .ant-spin-nested-loading {
  position: relative;
  display: flex;
  flex: 1 1 0%;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}

/* 强制 ant-spin-container 填满父容器，不再被内部内容反撑
 *
 * 【scoped CSS 穿透说明】— 极其重要（改坏会导致左侧两区块高度完全失控）：
 *   Vue \3c style scoped> 会给选择器最后一个元素追加 [data-v-hash]，而 AntD 组件内部
 *   的 .ant-spin-container / .ant-spin-nested-loading 根本没有这个属性，
 *   导致规则匹配失败（相当于整段代码白写）。
 *
 *   解决方案：使用 Vue :deep() 伪元素，data-v 只加在 :deep() 前面的
 *   .wb-sidebar__tree / .wb-sidebar__list-wrap 上（这两个是我 SFC 模板里写的，
 *   天生带 data-v），而 :deep() 内部选择器不再加属性标记，
 *   可以准确命中 AntD 内部 DOM。
 *
 * 特异性策略：
 *   Vue 编译后 = `.wb-sidebar__tree[data-v-hash] > .ant-spin-nested-loading > .ant-spin-container`
 *     特异性 = (0,3,1) = 3 class（含 [data-v] 与 .ant-spin-nested-loading 与
 *                           .ant-spin-container）+ 1 .wb-sidebar__tree 的 class
 *     已完胜 AntD 默认链 (0,2,1)。
 *
 * !important：第三方组件样式冲突的工业界标准做法，非妥协。
 */
.wb-sidebar__tree > :deep(.ant-spin-nested-loading > .ant-spin-container),
.wb-sidebar__list-wrap > :deep(.ant-spin-nested-loading > .ant-spin-container) {
  position: absolute !important;
  inset: 0 !important;
  overflow: hidden !important;
}

/* 实际树容器：贴满 spinContainer + 溢出滚动
 * .wb-plant-tree / .wb-sidebar__tree-empty 是我 SFC 模板里写的，自带 data-v，
 * 但保险起见仍保留 :deep() 以防今后重构到子组件内部，以及避免数据类竞争。 */
.wb-sidebar__tree :deep(.wb-plant-tree),
.wb-sidebar__tree :deep(.wb-sidebar__tree-empty) {
  position: absolute !important;
  inset: 0 !important;
  padding: 0 4px 4px !important;
  overflow: clip auto !important;
  scrollbar-gutter: stable !important;
}

/* 虚拟滚动的可视窗口：overflow-y:auto 滚动条在这里显示，高度恒等于 spinContainer(=listWrap) */
.wb-sidebar__list {
  position: absolute !important;
  inset: 0 !important;
  overflow: clip auto !important;
  scrollbar-gutter: stable !important;
}

/* —— 回路列表外层（Flex 比例 2/3）与 Spin 包装层 —— */
.wb-sidebar__list-wrap {
  display: flex;
  flex: 2 1 0%;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
}

.wb-sidebar__list-wrap .ant-spin-nested-loading {
  position: relative;
  display: flex;
  flex: 1 1 0%;
  flex-direction: column;
  min-height: 0;
  overflow: hidden;
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
  display: flex;
  align-items: center;
  height: 32px;
  padding: 0 10px;
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
  flex: 1;
  gap: 6px;
  align-items: center;
  justify-content: space-between;
  min-width: 0;
}

.wb-loop-item__tag {
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 13px;
  font-weight: 400;
  color: hsl(var(--foreground));
  white-space: nowrap;
}

.wb-loop-item__conf {
  flex-shrink: 0;
  font-size: 11px;
  font-weight: 700;
}

.wb-loop-item__conf--a {
  color: #1a7f4b;
}

.wb-loop-item__conf--b {
  color: #2563eb;
}

.wb-loop-item__conf--c {
  color: #b45309;
}

.wb-loop-item__conf--d {
  color: #7c3aed;
}

.wb-loop-item__conf--e {
  color: #c23434;
}

/* ===== 性能等级筛选图符 ===== */
.wb-sidebar__grade-filter {
  display: flex;
  gap: 3px;
  margin-top: 4px;
}

.wb-grade-chip {
  display: flex;
  flex: 1;
  align-items: center;
  justify-content: center;
  height: 18px;
  font-size: 11px;
  font-weight: 400;
  color: hsl(var(--muted-foreground));
  cursor: pointer;
  user-select: none;
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border) / 60%);
  border-radius: 3px;
  opacity: 0.55;
  transition: all 0.15s;
}

.wb-grade-chip:hover {
  opacity: 1;
}

.wb-grade-chip--a {
  color: #1a7f4b;
  border-color: #1a7f4b;
}

.wb-grade-chip--b {
  color: #2563eb;
  border-color: #2563eb;
}

.wb-grade-chip--c {
  color: #b45309;
  border-color: #b45309;
}

.wb-grade-chip--d {
  color: #7c3aed;
  border-color: #7c3aed;
}

.wb-grade-chip--e {
  color: #c23434;
  border-color: #c23434;
}

.wb-grade-chip__count {
  margin-left: 2px;
  font-size: 10px;
  font-weight: 400;
  opacity: 0.7;
}

.wb-grade-chip--a.wb-grade-chip--active {
  color: #fff;
  background: #1a7f4b;
  opacity: 1;
}

.wb-grade-chip--b.wb-grade-chip--active {
  color: #fff;
  background: #2563eb;
  opacity: 1;
}

.wb-grade-chip--c.wb-grade-chip--active {
  color: #fff;
  background: #b45309;
  opacity: 1;
}

.wb-grade-chip--d.wb-grade-chip--active {
  color: #fff;
  background: #7c3aed;
  opacity: 1;
}

.wb-grade-chip--e.wb-grade-chip--active {
  color: #fff;
  background: #c23434;
  opacity: 1;
}

.wb-grade-chip--active .wb-grade-chip__count {
  color: #fff;
  opacity: 0.85;
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

.wb-r2__fullscreen-btn {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  justify-content: center;
  height: 22px;
  padding: 0 6px;
  margin-left: auto;
  font-size: 11px;
  color: hsl(var(--foreground) / 60%);
  cursor: pointer;
  background: hsl(var(--muted) / 30%);
  border: 1px solid hsl(var(--border) / 40%);
  border-radius: 3px;
  transition: all 0.15s ease;
}

.wb-r2__fullscreen-btn:hover {
  color: hsl(var(--primary));
  background: hsl(var(--primary) / 5%);
  border-color: hsl(var(--primary) / 40%);
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
  cursor: pointer;
  user-select: none;
  transition: opacity 0.15s;
}

.wb-r4__legend-item--hidden {
  text-decoration: line-through;
  opacity: 0.35;
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

/* ===== R5 证据五区 ===== */
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

/* 评估.综合性能卡（雷达图撑满） */
.wb-r5__radar {
  position: relative;
  flex: 1;
  min-height: 0;
}

/* 评估.指标详情卡（横道图撑满） */
.wb-r5__bars {
  position: relative;
  flex: 1;
  min-height: 0;
  padding: 6px 8px;
}

.wb-r5__empty-mini {
  display: flex;
  flex: 1;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  color: hsl(var(--foreground) / 40%);
}

/* ===== 右决策栏 ===== */

.wb-decision__dock {
  flex: 0 0 auto;
  background: hsl(var(--card));
  border: 1px solid hsl(var(--primary) / 20%);
  border-radius: 4px;
  box-shadow: 0 2px 8px hsl(var(--primary) / 8%);
}

.wb-decision__attention {
  flex: 0 0 auto;
  max-height: 200px;
  overflow: hidden;
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
