<script setup lang="ts">
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { DiagnosisApi } from '#/api/diagnosis';
import type { LoopApi } from '#/api/loop';
import type { KpiSnapshotItem } from '#/api/metric';
import type { PlantNodeApi } from '#/api/plant-node';

/**
 * 诊断详情弹窗 —— 遮罩模式，点击概览行弹出（2026-08-18 v5）。
 *
 * 交互：标题栏可拖动移动；右下角手柄可调整宽高（默认 860×600，
 * 宽度按"性能评估指标单行不换行"测算：KPI 行 ≈795px + body padding）。
 * 结构：
 * - 顶部三信息行（回路基本信息 / 性能评估指标 / 诊断基本信息，均单行 nowrap）
 * - Tab1 诊断结论：AI 结论卡 + 人工复核表单（复核时间/复核人自动填入）
 * - Tab2 诊断证据：数据质量 / 波形快照 / 特征值（默认全展开）
 * - Tab3 处置建议：系统按诊断/复核结论自动带出 + 人工新增处置措施
 * - Tab4 前后对比（16 号文 F2）：相邻对比（上一条 SUCCESS）恒可用；
 *   验证对比（处置工单关联前后）按响应 verifyPair 能力显隐；
 *   底部"证据波形"折叠块左右并排渲染 base/target 证据波形（懒加载，
 *   超期清理显示保留策略占位）
 */
import { computed, nextTick, ref, watch } from 'vue';
import { useRouter } from 'vue-router';

import { EchartsUI, useEcharts } from '@vben/plugins/echarts';
import { useUserStore } from '@vben/stores';

import {
  Button,
  Collapse,
  CollapsePanel,
  Empty,
  Form,
  FormItem,
  Input,
  message,
  Modal,
  Segmented,
  Select,
  Skeleton,
  Spin,
  TabPane,
  Tabs,
  Tag,
  Textarea,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import {
  createRunActionApi,
  deleteRunActionApi,
  getDiagnosisCompareApi,
  getDiagnosisRunDetailApi,
  getRunActionsApi,
  reviewDiagnosisRunApi,
  updateRunActionApi,
} from '#/api/diagnosis';
import { getLoopListApi } from '#/api/loop';
import { getLoopSnapshotsApi } from '#/api/metric';
import { getPlantNodeTreeApi } from '#/api/plant-node';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import { useModules } from '#/composables/use-modules';
// v2.0 处置双实体：建议状态映射改用建议侧常量（原 HANDLING_STATUS_* 为 v1.x 工单旧口径）
import {
  SUGGESTION_STATUS_COLOR,
  SUGGESTION_STATUS_TEXT,
} from '#/views/handling/constants';

import {
  CATEGORY_META,
  CATEGORY_OPTIONS,
  IMPORTANCE_LEVEL_COLOR,
  IMPORTANCE_LEVEL_TEXT,
  scoreGrade,
  SEVERITY_TEXT,
  TRIGGER_TYPE_COLOR,
  TRIGGER_TYPE_TEXT,
} from '../constants';
import DiagnosisResultPanel from './diagnosis-result-panel.vue';

const props = defineProps<{
  item: DiagnosisApi.LatestRunItem | null;
}>();

const emit = defineEmits<{ reviewed: [] }>();

const open = defineModel<boolean>('open', { default: false });

const router = useRouter();
const { moduleEnabled } = useModules();

/** 「去处置」：携带建议 actionId 跳诊断建议入口，自动打开建议详情抽屉
 * （深链接契约：/handling/suggestions?focus={suggestionId}） */
function gotoHandling(actionId: string): void {
  open.value = false;
  router.push({ path: '/handling/suggestions', query: { focus: actionId } });
}

/** 「去整定」：TUNING 类建议跳整定工作台并预填回路（09 设计方案 §6.5 联动） */
function gotoTuning(loopId: string): void {
  open.value = false;
  router.push({
    path: '/tuning/workbench',
    query: { from: 'diagnosis', loopId },
  });
}

const userStore = useUserStore();
/** 当前用户（复核人自动填入；后端以登录态为准，前端仅展示） */
const currentUserName = computed(
  () => userStore.userInfo?.realName || userStore.userInfo?.username || '—',
);

// ===== 数据加载 =====
const detailLoading = ref(false);
const runDetail = ref<DiagnosisApi.RunDetail | null>(null);
const kpiLoading = ref(false);
const kpi = ref<KpiSnapshotItem | null>(null);
/** 回路台账（量程/单元等概览行没有的字段） */
const loopInfo = ref<LoopApi.LoopListItem | null>(null);

/** plant node 平铺索引（"装置.单元"路径回溯；加载失败回退 unitName） */
const nodeIndex = ref(
  new Map<string, { name: string; parentId: null | string }>(),
);
let nodeIndexLoaded = false;

async function ensureNodeIndex(): Promise<void> {
  if (nodeIndexLoaded) return;
  try {
    const tree = await getPlantNodeTreeApi();
    const idx = new Map<string, { name: string; parentId: null | string }>();
    const walk = (nodes: PlantNodeApi.PlantNode[]) => {
      for (const n of nodes) {
        idx.set(n.id, { name: n.name, parentId: n.parentId });
        if (n.children?.length) walk(n.children);
      }
    };
    walk(tree);
    nodeIndex.value = idx;
  } catch {
    /* 树加载失败时 unitPath 回退 unitName */
  }
  nodeIndexLoaded = true;
}

/** naive UTC → 本地时间 */
function fmtLocal(naiveIso?: null | string): string {
  if (!naiveIso) return '—';
  const withZ = /[Zz]|[+-]\d{2}:?\d{2}$/.test(naiveIso)
    ? naiveIso
    : `${naiveIso}Z`;
  return dayjs(withZ).format('MM-DD HH:mm');
}

function fmtRate(v?: null | number): string {
  return v == null ? '—' : `${v.toFixed(1)}%`;
}

async function load(item: DiagnosisApi.LatestRunItem) {
  // KPI 快照 / 回路台账 / 诊断详情并行加载
  kpiLoading.value = true;
  kpi.value = null;
  getLoopSnapshotsApi({ loopId: item.loopId, latestOnly: true, pageSize: 1 })
    .then((res) => {
      kpi.value = res.items?.[0] ?? null;
    })
    .catch(() => {
      kpi.value = null;
    })
    .finally(() => {
      kpiLoading.value = false;
    });

  loopInfo.value = null;
  getLoopListApi({ keyword: item.loopTagName, page: 1, pageSize: 20 })
    .then((res) => {
      loopInfo.value = res.items.find((l) => l.loopId === item.loopId) ?? null;
    })
    .catch(() => {
      loopInfo.value = null;
    });
  ensureNodeIndex();

  if (!item.runId) {
    runDetail.value = null;
    detailLoading.value = false;
    return;
  }
  detailLoading.value = true;
  runDetail.value = null;
  try {
    runDetail.value = await getDiagnosisRunDetailApi(item.runId);
  } catch {
    runDetail.value = null;
  } finally {
    detailLoading.value = false;
  }
}

// ===== 顶部信息区 =====
const grade = computed(() => scoreGrade(kpi.value?.score));

/** 所属装置.单元（plant node 树回溯路径） */
const unitPath = computed(() => {
  const info = loopInfo.value;
  if (!info) return '—';
  const parts: string[] = [];
  let cur = nodeIndex.value.get(info.unitId);
  while (cur) {
    parts.unshift(cur.name);
    cur = cur.parentId ? nodeIndex.value.get(cur.parentId) : undefined;
  }
  return parts.length > 0 ? parts.join('.') : info.unitName || '—';
});

/** PV 量程文本（min~max 单位） */
const rangeText = computed(() => {
  const r = loopInfo.value?.pvRange;
  if (!r || (r.min == null && r.max == null)) return '—';
  const unit = loopInfo.value?.pvUnit ?? '';
  return `${r.min ?? '?'}~${r.max ?? '?'}${unit ? ` ${unit}` : ''}`;
});

/** 性能评估指标 6 率（单行紧凑展示） */
const kpiRates = computed(() => {
  const k = kpi.value;
  return [
    { label: '有效自控率', value: fmtRate(k?.effectiveAutoRate) },
    { label: '平稳率', value: fmtRate(k?.steadyRate) },
    { label: '准确率', value: fmtRate(k?.accuracyRate) },
    { label: '快速率', value: fmtRate(k?.fastRate) },
    { label: '振荡率', value: fmtRate(k?.oscillationRate) },
    { label: '饱和率', value: fmtRate(k?.saturationRate) },
  ];
});

/** 诊断时间窗口（概览行优先，详情兜底） */
const twStart = computed(
  () => props.item?.timeWindowStart ?? runDetail.value?.timeWindowStart,
);
const twEnd = computed(
  () => props.item?.timeWindowEnd ?? runDetail.value?.timeWindowEnd,
);

// ===== Tab1：人工复核表单 =====
const reviewForm = ref<{ reviewComment: string; reviewResults: string[] }>({
  reviewResults: [],
  reviewComment: '',
});
const reviewSubmitting = ref(false);
/** 复核时间展示：未复核=当前系统时间（自动填入）；已复核=上次复核时间 */
const reviewTimeText = computed(() => {
  if (props.item?.reviewStatus === 'REVIEWED' && props.item.reviewedAt) {
    return fmtLocal(props.item.reviewedAt);
  }
  return dayjs().format('YYYY-MM-DD HH:mm:ss');
});
/** 复核人展示：未复核=当前登录用户；已复核=上次复核人 */
const reviewerText = computed(() => {
  if (props.item?.reviewStatus === 'REVIEWED' && props.item.reviewedBy) {
    return props.item.reviewedBy;
  }
  return currentUserName.value;
});

async function submitReview() {
  if (!props.item?.runId) return;
  if (reviewForm.value.reviewResults.length === 0) {
    message.warning('请至少选择一项复核结论');
    return;
  }
  reviewSubmitting.value = true;
  try {
    await reviewDiagnosisRunApi(props.item.runId, {
      reviewComment: reviewForm.value.reviewComment || null,
      reviewResults: reviewForm.value.reviewResults,
    });
    message.success('复核已记录');
    emit('reviewed');
    // 刷新诊断详情 + 处置建议（后端已按复核结论重置系统建议）
    if (props.item.runId) {
      load(props.item);
      loadActions();
    }
  } finally {
    reviewSubmitting.value = false;
  }
}

// ===== Tab3：处置建议 =====
const actionsLoading = ref(false);
const actionItems = ref<DiagnosisApi.ActionItem[]>([]);
const newActionContent = ref('');
const newActionSubmitting = ref(false);

/** 行内编辑状态（仅 MANUAL）：editingId + 编辑内容 */
const editingActionId = ref('');
const editingContent = ref('');

async function loadActions(): Promise<void> {
  const runId = props.item?.runId;
  if (!runId) return;
  actionsLoading.value = true;
  try {
    const res = await getRunActionsApi(runId);
    actionItems.value = res.items;
  } catch {
    actionItems.value = [];
  } finally {
    actionsLoading.value = false;
  }
}

async function submitNewAction(): Promise<void> {
  const runId = props.item?.runId;
  const content = newActionContent.value.trim();
  if (!runId || !content) {
    if (!content) message.warning('请输入处置措施内容');
    return;
  }
  newActionSubmitting.value = true;
  try {
    await createRunActionApi(runId, { content });
    message.success('处置措施已添加');
    newActionContent.value = '';
    loadActions();
  } finally {
    newActionSubmitting.value = false;
  }
}

function startEditAction(a: DiagnosisApi.ActionItem): void {
  editingActionId.value = a.id;
  editingContent.value = a.content;
}

function cancelEditAction(): void {
  editingActionId.value = '';
  editingContent.value = '';
}

async function saveEditAction(): Promise<void> {
  const content = editingContent.value.trim();
  if (!editingActionId.value || !content) {
    if (!content) message.warning('处置措施内容不能为空');
    return;
  }
  try {
    await updateRunActionApi(editingActionId.value, { content });
    message.success('处置措施已更新');
    cancelEditAction();
    loadActions();
  } catch {
    /* 错误提示由请求拦截器统一弹出 */
  }
}

async function removeAction(a: DiagnosisApi.ActionItem): Promise<void> {
  Modal.confirm({
    title: '删除处置建议',
    content: `确定删除该${a.source === 'SYSTEM' ? '系统建议' : '人工新增措施'}吗？${
      a.source === 'SYSTEM' ? '（删除后不会自动重建，除非重新提交复核）' : ''
    }`,
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    onOk: async () => {
      try {
        await deleteRunActionApi(a.id);
        message.success('已删除');
        loadActions();
      } catch {
        /* 错误提示由请求拦截器统一弹出 */
      }
    },
  });
}

// ===== Tab4：前后对比（16 号文 F2 · D3 双模式） =====
// 相邻对比=与上一条 SUCCESS run（纯诊断域恒可用）；验证对比=处置工单关联前后
// （响应 verifyPair 能力声明可用才显示，隐藏而非置灰）
const compareMode = ref<DiagnosisApi.CompareMode>('adjacent');
const compareLoading = ref(false);
const adjacentData = ref<DiagnosisApi.CompareResult | null>(null);
/** 相邻前序不存在（首条 run） */
const compareAdjacentMissing = ref(false);
const verifyAvailable = ref(false);
const verifyData = ref<DiagnosisApi.CompareResult | null>(null);

/** 方向着色：恶化红 / 改善绿 / 持平灰（工业状态色） */
const DIR_COLOR = { better: '#16a34a', flat: '#6c757d', worse: '#dc2626' } as
  const;
const DIR_TEXT = { better: '改善', flat: '持平', worse: '恶化' } as const;
type Dir = keyof typeof DIR_TEXT;

/** 后端 direction 归一（未知取值按持平处理） */
function normDir(d?: null | string): Dir {
  const s = (d ?? '').toLowerCase();
  if (s === 'better' || s === 'improved') return 'better';
  if (s === 'worse' || s === 'degraded') return 'worse';
  return 'flat';
}

/** KPI 对照指标中文名（metric key → label；未知 key 原样展示） */
const METRIC_LABEL: Record<string, string> = {
  accuracyRate: '准确率',
  autoModeRate: '自动模式率',
  badValueRate: '坏值率',
  effectiveAutoRate: '有效自控率',
  fastRate: '快速率',
  goodValueRate: '好值率',
  oscillationRate: '振荡率',
  outputTravelIndex: '行程指数',
  saturationRate: '饱和率',
  score: '综合评分',
  settlingTime: '稳定时间',
  steadyRate: '平稳率',
  stictionIndex: '粘滞指数',
};

const SEV_RANK: Record<string, number> = { HIGH: 3, LOW: 1, MEDIUM: 2 };

function fmtNum(v?: null | number): string {
  if (v == null || Number.isNaN(v)) return '—';
  return String(Number.parseFloat(v.toFixed(3)));
}

function fmtDelta(v?: null | number): string {
  if (v == null || Number.isNaN(v)) return '—';
  return `${v > 0 ? '+' : ''}${fmtNum(v)}`;
}

function fmtConf(v?: null | number): string {
  return v == null ? '—' : `${Math.round(v * 100)}%`;
}

function catLabelOf(cat?: DiagnosisApi.Category | null): string {
  return cat ? (CATEGORY_META[cat]?.label ?? cat) : '无结论';
}

function catColorOf(cat?: DiagnosisApi.Category | null): string {
  return cat ? (CATEGORY_META[cat]?.color ?? '#6c757d') : '#6c757d';
}

function sevLabelOf(sev?: DiagnosisApi.Severity | null): string {
  return sev ? (SEVERITY_TEXT[sev] ?? sev) : '—';
}

const modeOptions = computed(() => {
  const opts: Array<{ label: string; value: DiagnosisApi.CompareMode }> = [
    { label: '相邻对比', value: 'adjacent' },
  ];
  // 验证对比仅能力可用时显示（隐藏而非置灰）
  if (verifyAvailable.value) opts.push({ label: '验证对比', value: 'verify' });
  return opts;
});

const modeHint = computed(() =>
  compareMode.value === 'adjacent'
    ? '与上一条成功诊断（SUCCESS）对比，纯诊断域数据'
    : '处置工单关联的处置前 ↔ 处置后两次诊断对比',
);

const currentCompare = computed(() =>
  compareMode.value === 'adjacent' ? adjacentData.value : verifyData.value,
);

/** 结论变化方向（前端判定：分类消除=改善、新增问题=恶化、迁移=持平） */
const catDir = computed<Dir>(() => {
  const c = currentCompare.value?.conclusion.primaryCategory;
  if (!c || c.base === c.target) return 'flat';
  if (c.base && !c.target) return 'better';
  if (!c.base && c.target) return 'worse';
  return 'flat';
});

const sevDir = computed<Dir>(() => {
  const c = currentCompare.value?.conclusion.severity;
  if (!c || !c.base || !c.target) return 'flat';
  const d = (SEV_RANK[c.target] ?? 0) - (SEV_RANK[c.base] ?? 0);
  return d < 0 ? 'better' : (d > 0 ? 'worse' : 'flat');
});

const confDir = computed<Dir>(() => {
  const d = currentCompare.value?.conclusion.confidence.delta;
  if (d == null) return 'flat';
  return d > 0.005 ? 'better' : (d < -0.005 ? 'worse' : 'flat');
});

const confDeltaText = computed(() => {
  const d = currentCompare.value?.conclusion.confidence.delta;
  if (d == null) return '—';
  const pp = Math.round(d * 100);
  return `${pp > 0 ? '+' : ''}${pp}pp`;
});

/** 静默拉取对比（无前序/无验证对时 404 属预期分支，不弹全局错误） */
async function fetchCompare(
  runId: string,
  mode: DiagnosisApi.CompareMode,
): Promise<DiagnosisApi.CompareResult | null> {
  try {
    return await getDiagnosisCompareApi(runId, mode, {
      skipErrorMessage: true,
    });
  } catch {
    return null;
  }
}

async function loadCompare(): Promise<void> {
  const runId = props.item?.runId;
  if (!runId || compareLoading.value) return;
  compareLoading.value = true;
  try {
    const adj = await fetchCompare(runId, 'adjacent');
    if (adj) {
      adjacentData.value = adj;
      compareAdjacentMissing.value = !adj.base;
      if (adj.verifyPair) {
        // 响应已声明验证对能力：直接拉取 verify 数据供模式切换
        verifyData.value = await fetchCompare(runId, 'verify');
        verifyAvailable.value = verifyData.value !== null;
      }
    } else {
      adjacentData.value = null;
      compareAdjacentMissing.value = true;
      // 契约兜底：响应未声明能力时以 verify 404 探测
      const ver = await fetchCompare(runId, 'verify');
      verifyAvailable.value = ver?.verifyPair === true;
      verifyData.value = ver;
    }
  } finally {
    compareLoading.value = false;
  }
}

// 切到验证模式但数据缺失（能力边界）时兜底拉取一次
watch(compareMode, (mode) => {
  if (mode === 'verify' && props.item?.runId && !verifyData.value) {
    fetchCompare(props.item.runId, 'verify').then((res) => {
      if (res) verifyData.value = res;
    });
  }
});

// ===== Tab4：证据波形并排（16 号文 F2 功能点 2-4） =====
// 懒加载：展开"证据波形"折叠块才复用 run 详情接口分别拉 base/target 的
// evidenceCharts；超期清理（后端返回空/缺省）时按保留策略占位（对齐
// evidence-drawer 文案）；对比模式切换后 base/target 变化需重置重拉
const evidenceActiveKeys = ref<string[]>([]);
const evidenceLoading = ref(false);
const evidenceLoaded = ref(false);
const baseCharts = ref<DiagnosisApi.ChartSnapshot | null>(null);
const targetCharts = ref<DiagnosisApi.ChartSnapshot | null>(null);

function resetEvidence(): void {
  evidenceActiveKeys.value = [];
  evidenceLoading.value = false;
  evidenceLoaded.value = false;
  baseCharts.value = null;
  targetCharts.value = null;
}

async function loadEvidence(): Promise<void> {
  const cmp = currentCompare.value;
  if (!cmp || evidenceLoading.value || evidenceLoaded.value) return;
  const fetchCharts = async (
    runId: null | string | undefined,
  ): Promise<DiagnosisApi.ChartSnapshot | null> => {
    if (!runId) return null;
    try {
      const d = await getDiagnosisRunDetailApi(runId);
      return d.evidenceCharts ?? null;
    } catch {
      return null; // 请求失败按无证据处理（错误提示由请求拦截器统一弹出）
    }
  };
  evidenceLoading.value = true;
  try {
    [baseCharts.value, targetCharts.value] = await Promise.all([
      fetchCharts(cmp.base?.runId),
      fetchCharts(cmp.target.runId),
    ]);
    evidenceLoaded.value = true;
  } finally {
    evidenceLoading.value = false;
    nextTick(renderEvidenceCharts);
  }
}

watch(evidenceActiveKeys, (keys) => {
  if (keys.includes('evidence')) loadEvidence();
});

// 相邻 ↔ 验证切换后 base/target runId 变化：重置折叠块，重新懒加载
watch(compareMode, () => {
  resetEvidence();
});

const baseTrendRef = ref<EchartsUIType>();
const baseScatterRef = ref<EchartsUIType>();
const targetTrendRef = ref<EchartsUIType>();
const targetScatterRef = ref<EchartsUIType>();
const { renderEcharts: renderBaseTrend } = useEcharts(baseTrendRef);
const { renderEcharts: renderBaseScatter } = useEcharts(baseScatterRef);
const { renderEcharts: renderTargetTrend } = useEcharts(targetTrendRef);
const { renderEcharts: renderTargetScatter } = useEcharts(targetScatterRef);

/** 波形 option 复用 DiagnosisResultPanel 口径（双 Y 轴 + PV/OP 量程定标） */
function buildCmpTrendOption(chart?: DiagnosisApi.ChartSnapshot['trend']) {
  const ts = chart?.ts ?? [];
  const toPoints = (arr?: (null | number)[]) =>
    (arr ?? []).map((v, i) => [ts[i], v ?? null]);
  const pvRange = chart?.pvRange;
  const opRange = chart?.opRange;
  return {
    animation: false,
    color: ['#1d4ed8', '#6b7280', '#b45309'],
    grid: { bottom: 44, left: 44, right: 20, top: 32 },
    legend: { data: ['PV', 'SP', 'OP'], top: 0 },
    series: [
      {
        connectNulls: false,
        data: toPoints(chart?.pv),
        name: 'PV',
        showSymbol: false,
        type: 'line',
        yAxisIndex: 0,
      },
      {
        lineStyle: { type: 'dashed' },
        data: toPoints(chart?.sp),
        name: 'SP',
        showSymbol: false,
        type: 'line',
        yAxisIndex: 0,
      },
      {
        data: toPoints(chart?.op),
        name: 'OP',
        showSymbol: false,
        type: 'line',
        yAxisIndex: 1,
      },
    ],
    tooltip: { trigger: 'axis' },
    xAxis: {
      axisLabel: { formatter: (v: number) => `${Math.round(v / 60_000)}m` },
      type: 'time',
    },
    yAxis: [
      {
        name: 'PV/SP',
        scale: !pvRange,
        type: 'value',
        ...(pvRange ? { max: pvRange.max, min: pvRange.min } : {}),
      },
      {
        name: 'OP',
        scale: !opRange,
        splitLine: false,
        type: 'value',
        ...(opRange ? { max: opRange.max, min: opRange.min } : {}),
      },
    ],
  };
}

function buildCmpScatterOption(chart?: DiagnosisApi.ChartSnapshot['scatter']) {
  return {
    animation: false,
    color: ['#b45309'],
    grid: { bottom: 36, left: 44, right: 16, top: 24 },
    series: [
      {
        data: (chart?.pv ?? []).map((pv, i) => [chart?.op?.[i], pv]),
        itemStyle: { opacity: 0.35 },
        name: 'PV-OP',
        symbolSize: 3,
        type: 'scatter',
      },
    ],
    tooltip: { trigger: 'item' },
    xAxis: { name: 'OP', scale: true, type: 'value' },
    yAxis: { name: 'PV', scale: true, type: 'value' },
  };
}

function renderEvidenceCharts(): void {
  if (!evidenceActiveKeys.value.includes('evidence')) return;
  if (baseCharts.value) {
    renderBaseTrend(buildCmpTrendOption(baseCharts.value.trend) as any);
    renderBaseScatter(buildCmpScatterOption(baseCharts.value.scatter) as any);
  }
  if (targetCharts.value) {
    renderTargetTrend(buildCmpTrendOption(targetCharts.value.trend) as any);
    renderTargetScatter(
      buildCmpScatterOption(targetCharts.value.scatter) as any,
    );
  }
}

const { isDark } = useClpmTheme();
// 暗色切换时重渲（与 DiagnosisResultPanel 波形快照同纪律）
watch(isDark, () => {
  if (evidenceActiveKeys.value.includes('evidence')) {
    nextTick(renderEvidenceCharts);
  }
});

// ===== Tabs =====
const activeTab = ref('conclusion');

// 首次切到"前后对比"页签时懒加载对比数据
watch(activeTab, (tab) => {
  if (
    tab === 'compare' &&
    props.item?.runId &&
    !adjacentData.value &&
    !compareLoading.value
  ) {
    loadCompare();
  }
});

// ===== 拖动 + 调整宽高 =====
/** 默认 860：KPI 单行（评分+等级+6率 ≈795px）+ body padding 32px */
const modalW = ref(860);
/** 高度按视口自适应（尽量完整显示 Tab 内容）：视口 - 标题栏/页边余量，clamp [520, 880] */
const bodyH = ref(Math.min(Math.max(window.innerHeight - 200, 520), 880));
const MIN_W = 720;
const MIN_H = 360;

function getModalEl(): HTMLElement | null {
  return document.querySelector<HTMLElement>('.diag-detail-modal .ant-modal');
}

/** 标题栏按下 → 拖动移动（切绝对定位，clamp 在视口内） */
function onHeaderMouseDown(e: MouseEvent) {
  if (e.button !== 0) return;
  const modal = getModalEl();
  const wrap = document.querySelector<HTMLElement>('.diag-detail-modal');
  if (!modal || !wrap) return;
  const rect = modal.getBoundingClientRect();
  const wrapRect = wrap.getBoundingClientRect();
  modal.style.position = 'absolute';
  modal.style.margin = '0';
  modal.style.left = `${rect.left - wrapRect.left}px`;
  modal.style.top = `${rect.top - wrapRect.top}px`;
  const startX = e.clientX;
  const startY = e.clientY;
  const origLeft = rect.left - wrapRect.left;
  const origTop = rect.top - wrapRect.top;
  const move = (ev: MouseEvent) => {
    modal.style.left = `${Math.min(Math.max(origLeft + ev.clientX - startX, 0), window.innerWidth - 80)}px`;
    modal.style.top = `${Math.min(Math.max(origTop + ev.clientY - startY, 0), window.innerHeight - 48)}px`;
  };
  const up = () => {
    document.removeEventListener('mousemove', move);
    document.removeEventListener('mouseup', up);
  };
  document.addEventListener('mousemove', move);
  document.addEventListener('mouseup', up);
  e.preventDefault();
}

/** 右下角手柄按下 → 调整宽高 */
function onResizeStart(e: MouseEvent) {
  if (e.button !== 0) return;
  const startX = e.clientX;
  const startY = e.clientY;
  const startW = modalW.value;
  const startH = bodyH.value;
  const move = (ev: MouseEvent) => {
    modalW.value = Math.min(
      Math.max(startW + ev.clientX - startX, MIN_W),
      window.innerWidth - 32,
    );
    bodyH.value = Math.min(
      Math.max(startH + ev.clientY - startY, MIN_H),
      window.innerHeight - 96,
    );
  };
  const up = () => {
    document.removeEventListener('mousemove', move);
    document.removeEventListener('mouseup', up);
    // 通知 echarts 自适应新宽度（vben useEcharts 监听 window resize）
    window.dispatchEvent(new Event('resize'));
  };
  document.addEventListener('mousemove', move);
  document.addEventListener('mouseup', up);
  e.preventDefault();
  e.stopPropagation();
}

/** 关闭时恢复居中定位（尺寸保留用户偏好） */
function resetModalPosition() {
  const modal = getModalEl();
  if (!modal) return;
  modal.style.position = '';
  modal.style.margin = '';
  modal.style.left = '';
  modal.style.top = '';
}

let dragBound = false;

function bindDragOnce() {
  if (dragBound) return;
  const header = document.querySelector<HTMLElement>(
    '.diag-detail-modal .ant-modal-header',
  );
  if (!header) return;
  header.addEventListener('mousedown', onHeaderMouseDown);
  dragBound = true;
}

watch(open, (v) => {
  if (v && props.item) {
    activeTab.value = 'conclusion';
    // 复核表单回显：已复核预填上次结论（可改判）；未复核默认勾选 AI 主分类
    reviewForm.value.reviewResults = props.item.reviewResults?.length
      ? [...props.item.reviewResults]!
      : (props.item.primaryCategory
        ? [props.item.primaryCategory]
        : []);
    reviewForm.value.reviewComment = '';
    actionItems.value = [];
    newActionContent.value = '';
    // 前后对比状态重置（每次打开按当前 run 重新懒加载）
    compareMode.value = 'adjacent';
    adjacentData.value = null;
    compareAdjacentMissing.value = false;
    verifyAvailable.value = false;
    verifyData.value = null;
    // 证据波形并排状态重置（展开折叠才重新拉取）
    resetEvidence();
    load(props.item);
    loadActions();
    nextTick(bindDragOnce);
  } else if (!v) {
    resetModalPosition();
  }
});
</script>

<template>
  <Modal
    v-model:open="open"
    :title="`诊断详情 · ${item?.loopTagName ?? ''}`"
    :footer="null"
    :width="modalW"
    :body-style="{ height: `${bodyH}px`, overflow: 'hidden' }"
    wrap-class-name="diag-detail-modal"
  >
    <div v-if="item" class="diag-detail-body">
      <!-- ===== 顶部三信息行（均单行 nowrap） ===== -->
      <div class="diag-detail-top">
        <!-- ① 回路基本信息 -->
        <div class="diag-detail-card">
          <div class="diag-detail-card__title">回路基本信息</div>
          <div class="diag-info-row">
            <span class="diag-info__item">
              <span class="diag-info__k">位号</span>
              <span class="diag-info__v font-semibold">{{
                item.loopTagName
              }}</span>
            </span>
            <span class="diag-info__item">
              <span class="diag-info__k">名称</span>
              <span
                class="diag-info__v diag-ellipsis"
                :title="item.loopDescription ?? ''"
              >
                {{ item.loopDescription || '—' }}
              </span>
            </span>
            <span class="diag-info__item">
              <span class="diag-info__k">等级</span>
              <span
                v-if="item.importanceLevel"
                class="diag-info__v"
                :style="{ color: IMPORTANCE_LEVEL_COLOR[item.importanceLevel] }"
              >
                {{ IMPORTANCE_LEVEL_TEXT[item.importanceLevel] }}
              </span>
              <span v-else class="diag-info__v">—</span>
            </span>
            <span class="diag-info__item">
              <span class="diag-info__k">量程</span>
              <span class="diag-info__v tabular-nums">{{ rangeText }}</span>
            </span>
            <span class="diag-info__item">
              <span class="diag-info__k">装置.单元</span>
              <span class="diag-info__v diag-ellipsis" :title="unitPath">
                {{ unitPath }}
              </span>
            </span>
          </div>
        </div>

        <!-- ② 性能评估指标（单行；评估窗口在标题右侧） -->
        <div class="diag-detail-card">
          <div class="diag-detail-card__title">
            <span>性能评估指标</span>
            <span
              v-if="!kpiLoading && kpi"
              class="diag-detail-card__extra tabular-nums"
            >
              评估窗口 {{ fmtLocal(kpi.tsStart) }}~{{ fmtLocal(kpi.tsEnd) }}
            </span>
          </div>
          <Skeleton
            v-if="kpiLoading"
            :paragraph="{ rows: 1 }"
            active
            class="diag-kpi-skeleton"
          />
          <div v-else-if="kpi" class="diag-info-row">
            <span class="diag-info__item">
              <span class="diag-info__k">综合评分</span>
              <span
                class="diag-info__v font-semibold tabular-nums"
                :style="{ color: grade?.color }"
              >
                {{ kpi.score != null ? kpi.score.toFixed(1) : '—' }}
              </span>
            </span>
            <span class="diag-info__item">
              <span class="diag-info__k">等级</span>
              <span class="diag-info__v" :style="{ color: grade?.color }">
                {{ grade?.label ?? '—' }}
              </span>
            </span>
            <span v-for="r in kpiRates" :key="r.label" class="diag-info__item">
              <span class="diag-info__k">{{ r.label }}</span>
              <span class="diag-info__v tabular-nums">{{ r.value }}</span>
            </span>
          </div>
          <div v-else class="diag-detail-card__empty">
            暂无性能评估数据（尚未生成 KPI 快照）
          </div>
        </div>

        <!-- ③ 诊断基本信息 -->
        <div class="diag-detail-card">
          <div class="diag-detail-card__title">诊断基本信息</div>
          <div class="diag-info-row">
            <span class="diag-info__item">
              <span class="diag-info__k">诊断次序</span>
              <span class="diag-info__v">
                {{ item.runCount ? `第 ${item.runCount} 次` : '未诊断' }}
              </span>
            </span>
            <span class="diag-info__item">
              <span class="diag-info__k">诊断时间</span>
              <span class="diag-info__v tabular-nums">
                {{ fmtLocal(item.lastDiagnosedAt) }}
              </span>
            </span>
            <span class="diag-info__item">
              <span class="diag-info__k">触发方式</span>
              <span
                v-if="item.triggerType"
                class="diag-info__v"
                :style="{ color: TRIGGER_TYPE_COLOR[item.triggerType] }"
              >
                {{
                  item.triggerTypeLabel ??
                  TRIGGER_TYPE_TEXT[item.triggerType] ??
                  item.triggerType
                }}
              </span>
              <span v-else class="diag-info__v">—</span>
            </span>
            <span class="diag-info__item diag-info__item--end">
              <span class="diag-info__k">时间窗口</span>
              <span class="diag-info__v tabular-nums">
                {{ fmtLocal(twStart) }}~{{ fmtLocal(twEnd) }}
              </span>
            </span>
          </div>
        </div>
      </div>

      <!-- ===== 三 Tab：诊断结论 / 诊断证据 / 处置建议 ===== -->
      <Tabs
        v-model:active-key="activeTab"
        class="diag-detail-tabs"
        size="small"
      >
        <!-- Tab1 诊断结论：上=AI 结论；下=人工复核 -->
        <TabPane key="conclusion" tab="诊断结论">
          <Empty v-if="!item.runId" class="py-4" description="该回路尚未诊断" />
          <template v-else>
            <Spin v-if="detailLoading" class="block py-4" />
            <template v-else>
              <DiagnosisResultPanel
                v-if="runDetail"
                :detail="runDetail"
                section="conclusion"
              />
              <Empty v-else class="py-4" description="诊断详情加载失败" />
            </template>

            <!-- 人工复核（复核时间/复核人自动填入） -->
            <div class="diag-review">
              <div class="diag-review__title">
                人工复核
                <Tag
                  v-if="item.reviewStatus === 'REVIEWED'"
                  color="green"
                  style="margin-left: 6px"
                >
                  已复核
                </Tag>
                <Tag v-else color="orange" style="margin-left: 6px">待复核</Tag>
              </div>
              <Form layout="vertical" class="diag-review__form">
                <FormItem label="复核结论（多选）" required>
                  <Select
                    v-model:value="reviewForm.reviewResults"
                    :options="CATEGORY_OPTIONS"
                    mode="multiple"
                    placeholder="选择人工确认的问题分类（可多选）"
                    :max-tag-count="4"
                  />
                </FormItem>
                <FormItem label="复核意见">
                  <Textarea
                    v-model:value="reviewForm.reviewComment"
                    :maxlength="500"
                    placeholder="记录现场核实情况、处理安排等（可选，≤500 字）"
                    :rows="2"
                    show-count
                  />
                </FormItem>
                <div class="diag-review__meta">
                  <div class="diag-review__field">
                    <span class="diag-review__k">复核时间</span>
                    <Input :value="reviewTimeText" readonly size="small" />
                  </div>
                  <div class="diag-review__field">
                    <span class="diag-review__k">复核人</span>
                    <Input :value="reviewerText" readonly size="small" />
                  </div>
                  <Button
                    :loading="reviewSubmitting"
                    type="primary"
                    @click="submitReview"
                  >
                    {{
                      item.reviewStatus === 'REVIEWED' ? '更新复核' : '提交复核'
                    }}
                  </Button>
                </div>
              </Form>
            </div>
          </template>
        </TabPane>

        <!-- Tab2 诊断证据：数据质量/波形快照/特征值（默认全展开） -->
        <TabPane key="evidence" tab="诊断证据">
          <Empty v-if="!item.runId" class="py-4" description="该回路尚未诊断" />
          <Spin v-else-if="detailLoading" class="block py-4" />
          <DiagnosisResultPanel
            v-else-if="runDetail"
            :detail="runDetail"
            section="evidence"
          />
          <Empty v-else class="py-4" description="诊断详情加载失败" />
        </TabPane>

        <!-- Tab3 处置建议：系统带出 + 人工新增 -->
        <TabPane key="advice" tab="处置建议">
          <Empty v-if="!item.runId" class="py-4" description="该回路尚未诊断" />
          <template v-else>
            <Spin v-if="actionsLoading" class="block py-4" />
            <template v-else>
              <div v-if="actionItems.length > 0" class="diag-action-list">
                <div
                  v-for="a in actionItems"
                  :key="a.id"
                  class="diag-action-item"
                >
                  <!-- 行内编辑态（仅 MANUAL） -->
                  <template v-if="editingActionId === a.id">
                    <Textarea
                      v-model:value="editingContent"
                      :maxlength="500"
                      :rows="2"
                      auto-focus
                    />
                    <div class="mt-1 flex justify-end gap-2">
                      <Button size="small" @click="cancelEditAction"
                        >取消</Button
                      >
                      <Button
                        size="small"
                        type="primary"
                        @click="saveEditAction"
                      >
                        保存
                      </Button>
                    </div>
                  </template>
                  <!-- 展示态 -->
                  <template v-else>
                    <div class="flex items-start gap-2">
                      <Tag
                        :color="a.source === 'SYSTEM' ? 'blue' : 'green'"
                        class="mt-0.5 shrink-0"
                      >
                        {{
                          a.source === 'SYSTEM'
                            ? `系统建议 R${a.priority}`
                            : '人工新增'
                        }}
                      </Tag>
                      <!-- 处置建议状态 tag（PENDING 之外的状态在诊断侧可见，§8.4；
                           v2.0 建议状态机：ACCEPTED/CONVERTED/REJECTED/IGNORED） -->
                      <Tag
                        v-if="a.status && a.status !== 'PENDING'"
                        :color="
                          SUGGESTION_STATUS_COLOR[
                            a.status as keyof typeof SUGGESTION_STATUS_COLOR
                          ] ?? 'default'
                        "
                        class="mt-0.5 shrink-0"
                      >
                        {{
                          SUGGESTION_STATUS_TEXT[
                            a.status as keyof typeof SUGGESTION_STATUS_TEXT
                          ] ?? a.status
                        }}
                      </Tag>
                      <!-- TODO(v2.0-H2): CONVERTED 行额外显示 convertedOrderNo
                           （诊断侧 actions 接口需回链该字段后启用） -->
                      <div class="min-w-0 flex-1">
                        <div>{{ a.content }}</div>
                        <div class="mt-0.5 text-xs text-neutral-500">
                          依据：{{ a.basis || '—' }} · 建议人
                          {{ a.suggestedBy }} ·
                          {{ fmtLocal(a.suggestedAt) }}
                        </div>
                      </div>
                      <div class="flex shrink-0 gap-1" @click.stop>
                        <Button
                          v-if="a.category === 'TUNING' && moduleEnabled('tuning')"
                          size="small"
                          type="link"
                          @click="gotoTuning(a.loopId)"
                        >
                          去整定
                        </Button>
                        <Button
                          v-if="moduleEnabled('handling')"
                          size="small"
                          type="link"
                          @click="gotoHandling(a.id)"
                        >
                          去处置
                        </Button>
                        <Button
                          v-if="a.source === 'MANUAL'"
                          size="small"
                          type="link"
                          @click="startEditAction(a)"
                        >
                          编辑
                        </Button>
                        <Button
                          danger
                          size="small"
                          type="link"
                          @click="removeAction(a)"
                        >
                          删除
                        </Button>
                      </div>
                    </div>
                  </template>
                </div>
              </div>
              <Empty v-else class="py-4" description="暂无处置建议" />

              <!-- 新增处置措施（建议人/建议时间由系统自动带入） -->
              <div class="diag-action-new">
                <div class="diag-review__title">新增处置措施</div>
                <Textarea
                  v-model:value="newActionContent"
                  :maxlength="500"
                  :rows="2"
                  placeholder="输入处置措施（建议人与建议时间将自动记录为当前登录用户与系统时间）"
                />
                <div class="diag-action-new__footer">
                  <span class="text-xs text-neutral-400">
                    建议人 {{ currentUserName }} ·
                    {{ dayjs().format('YYYY-MM-DD HH:mm') }}
                  </span>
                  <Button
                    :loading="newActionSubmitting"
                    size="small"
                    type="primary"
                    @click="submitNewAction"
                  >
                    添加
                  </Button>
                </div>
              </div>
            </template>
          </template>
        </TabPane>

        <!-- Tab4 前后对比（16 号文 F2：相邻对比恒可用；验证对比按 verifyPair 能力显隐） -->
        <TabPane key="compare" tab="前后对比">
          <Empty v-if="!item.runId" class="py-4" description="该回路尚未诊断" />
          <template v-else>
            <div class="diag-cmp-bar">
              <Segmented
                v-model:value="compareMode"
                :options="modeOptions"
                size="small"
              />
              <span class="diag-cmp-hint">{{ modeHint }}</span>
            </div>
            <Spin v-if="compareLoading" class="block py-4" />
            <template v-else>
              <!-- 无相邻前序（首条 run）占位说明 -->
              <Empty
                v-if="compareMode === 'adjacent' && compareAdjacentMissing"
                class="py-4"
                description="该 run 之前没有可对比的诊断记录（首条诊断）"
              />
              <Empty
                v-else-if="!currentCompare"
                class="py-4"
                description="暂无对比数据"
              />
              <template v-else>
                <!-- 两次 run 概要（左=基准，右=对比；时间窗不同不归一化，各自明示） -->
                <div class="diag-cmp-runs">
                  <div class="diag-cmp-run">
                    <div class="diag-cmp-run__tag">基准 run</div>
                    <div class="diag-cmp-run__row tabular-nums">
                      {{ fmtLocal(currentCompare.base?.diagnosedAt) }}
                      <span class="diag-cmp-run__win">
                        窗口 {{ fmtLocal(currentCompare.base?.windowStart) }}~{{
                          fmtLocal(currentCompare.base?.windowEnd)
                        }}
                      </span>
                    </div>
                  </div>
                  <span class="diag-cmp-arrow">→</span>
                  <div class="diag-cmp-run diag-cmp-run--target">
                    <div class="diag-cmp-run__tag">对比 run</div>
                    <div class="diag-cmp-run__row tabular-nums">
                      {{ fmtLocal(currentCompare.target?.diagnosedAt) }}
                      <span class="diag-cmp-run__win">
                        窗口 {{ fmtLocal(currentCompare.target?.windowStart) }}~{{
                          fmtLocal(currentCompare.target?.windowEnd)
                        }}
                      </span>
                    </div>
                  </div>
                </div>

                <!-- 结论变化卡：方向着色（恶化红/改善绿/持平灰） -->
                <div class="diag-cmp-cards">
                  <div class="diag-cmp-card">
                    <div class="diag-cmp-card__k">主分类</div>
                    <div class="diag-cmp-card__v">
                      <span
                        :style="{
                          color: catColorOf(
                            currentCompare.conclusion.primaryCategory.base,
                          ),
                        }"
                      >
                        {{
                          catLabelOf(
                            currentCompare.conclusion.primaryCategory.base,
                          )
                        }}
                      </span>
                      <span class="diag-cmp-sep">→</span>
                      <span
                        :style="{
                          color: catColorOf(
                            currentCompare.conclusion.primaryCategory.target,
                          ),
                        }"
                      >
                        {{
                          catLabelOf(
                            currentCompare.conclusion.primaryCategory.target,
                          )
                        }}
                      </span>
                      <span
                        class="diag-cmp-dir"
                        :style="{ color: DIR_COLOR[catDir] }"
                      >
                        {{ DIR_TEXT[catDir] }}
                      </span>
                    </div>
                  </div>
                  <div class="diag-cmp-card">
                    <div class="diag-cmp-card__k">严重度</div>
                    <div class="diag-cmp-card__v">
                      <span>{{
                        sevLabelOf(currentCompare.conclusion.severity.base)
                      }}</span>
                      <span class="diag-cmp-sep">→</span>
                      <span>{{
                        sevLabelOf(currentCompare.conclusion.severity.target)
                      }}</span>
                      <span
                        class="diag-cmp-dir"
                        :style="{ color: DIR_COLOR[sevDir] }"
                      >
                        {{ DIR_TEXT[sevDir] }}
                      </span>
                    </div>
                  </div>
                  <div class="diag-cmp-card">
                    <div class="diag-cmp-card__k">置信度</div>
                    <div class="diag-cmp-card__v">
                      <span>{{
                        fmtConf(currentCompare.conclusion.confidence.base)
                      }}</span>
                      <span class="diag-cmp-sep">→</span>
                      <span>{{
                        fmtConf(currentCompare.conclusion.confidence.target)
                      }}</span>
                      <span
                        class="diag-cmp-dir"
                        :style="{ color: DIR_COLOR[confDir] }"
                      >
                        {{ confDeltaText }} · {{ DIR_TEXT[confDir] }}
                      </span>
                    </div>
                  </div>
                </div>

                <!-- 特征值对照（同算子同特征逐行对照） -->
                <div class="diag-cmp-sec">特征值对照</div>
                <table
                  v-if="currentCompare.features?.length"
                  class="diag-cmp-table"
                >
                  <thead>
                    <tr>
                      <th>算子</th>
                      <th>特征</th>
                      <th class="num">基准</th>
                      <th class="num">对比</th>
                      <th class="num">变化</th>
                      <th>方向</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr
                      v-for="(f, i) in currentCompare.features"
                      :key="`f${i}`"
                    >
                      <td>{{ f.operator }}</td>
                      <td>{{ f.feature }}</td>
                      <td class="num">{{ fmtNum(f.baseValue) }}</td>
                      <td class="num">{{ fmtNum(f.targetValue) }}</td>
                      <td class="num">{{ fmtDelta(f.delta) }}</td>
                      <td
                        :style="{ color: DIR_COLOR[normDir(f.direction)] }"
                      >
                        {{ DIR_TEXT[normDir(f.direction)] }}
                      </td>
                    </tr>
                  </tbody>
                </table>
                <div v-else class="diag-cmp-empty">
                  两次 run 无同名特征值可对照
                </div>

                <!-- KPI 对照（验证模式下与处置域 kpi_before/after 同源） -->
                <div class="diag-cmp-sec">KPI 对照</div>
                <table v-if="currentCompare.kpi?.length" class="diag-cmp-table">
                  <thead>
                    <tr>
                      <th>指标</th>
                      <th class="num">基准</th>
                      <th class="num">对比</th>
                      <th class="num">变化</th>
                      <th>方向</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="(k, i) in currentCompare.kpi" :key="`k${i}`">
                      <td>{{ METRIC_LABEL[k.metric] ?? k.metric }}</td>
                      <td class="num">{{ fmtNum(k.base) }}</td>
                      <td class="num">{{ fmtNum(k.target) }}</td>
                      <td class="num">{{ fmtDelta(k.delta) }}</td>
                      <td :style="{ color: DIR_COLOR[normDir(k.direction)] }">
                        {{ DIR_TEXT[normDir(k.direction)] }}
                      </td>
                    </tr>
                  </tbody>
                </table>
                <div v-else class="diag-cmp-empty">
                  两次 run 均无 KPI 汇总数据可对照
                </div>

                <!-- 证据波形并排（16 号文 F2：懒加载，展开才拉两次 run 详情；
                     超期清理显示保留策略占位） -->
                <Collapse
                  v-model:active-key="evidenceActiveKeys"
                  class="diag-cmp-evidence"
                >
                  <CollapsePanel
                    key="evidence"
                    header="证据波形（左=基准 run，右=对比 run）"
                  >
                    <Spin v-if="evidenceLoading" class="block py-4" />
                    <div v-else class="diag-cmp-charts">
                      <div class="diag-cmp-chart-col">
                        <div class="diag-cmp-chart-title">
                          基准 run ·
                          {{ fmtLocal(currentCompare.base?.diagnosedAt) }}
                        </div>
                        <template v-if="baseCharts">
                          <div class="diag-cmp-chart-label">
                            PV/SP/OP 趋势（诊断时间窗）
                          </div>
                          <EchartsUI ref="baseTrendRef" height="200px" />
                          <div class="diag-cmp-chart-label">
                            PV-OP 散点（回环/粘滞形态）
                          </div>
                          <EchartsUI ref="baseScatterRef" height="180px" />
                        </template>
                        <Empty
                          v-else
                          class="py-4"
                          description="该记录超过证据保留期（1 个月），证据已按保留策略清理；结论字段仍完整保留"
                        />
                      </div>
                      <div class="diag-cmp-chart-col">
                        <div class="diag-cmp-chart-title">
                          对比 run ·
                          {{ fmtLocal(currentCompare.target?.diagnosedAt) }}
                        </div>
                        <template v-if="targetCharts">
                          <div class="diag-cmp-chart-label">
                            PV/SP/OP 趋势（诊断时间窗）
                          </div>
                          <EchartsUI ref="targetTrendRef" height="200px" />
                          <div class="diag-cmp-chart-label">
                            PV-OP 散点（回环/粘滞形态）
                          </div>
                          <EchartsUI ref="targetScatterRef" height="180px" />
                        </template>
                        <Empty
                          v-else
                          class="py-4"
                          description="该记录超过证据保留期（1 个月），证据已按保留策略清理；结论字段仍完整保留"
                        />
                      </div>
                    </div>
                  </CollapsePanel>
                </Collapse>
              </template>
            </template>
          </template>
        </TabPane>
      </Tabs>

      <!-- 右下角宽高手柄 -->
      <div class="diag-detail-resize" @mousedown="onResizeStart"></div>
    </div>
  </Modal>
</template>

<style>
/* 弹窗 DOM 挂载于 body（wrapClassName 定位），需全局样式 */
.diag-detail-modal .ant-modal-header {
  cursor: move;
  user-select: none;
}

/* 压缩 body 内边距，为单行信息行留宽 */
.diag-detail-modal .ant-modal-body {
  padding: 12px 16px;
}

.diag-detail-modal .diag-detail-body {
  position: relative;
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
}

.diag-detail-modal .diag-detail-top {
  display: flex;
  flex-shrink: 0;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 8px;
}

.diag-detail-modal .diag-detail-card {
  background: hsl(var(--accent) / 20%);
  border: 1px solid hsl(var(--border));
  border-radius: 6px;
}

.diag-detail-modal .diag-detail-card__title {
  display: flex;
  gap: 8px;
  align-items: baseline;
  justify-content: space-between;
  padding: 3px 10px 0;
  font-size: 11px;
  font-weight: 600;
  color: hsl(var(--muted-foreground));
}

.diag-detail-modal .diag-detail-card__extra {
  font-size: 11px;
  font-weight: 400;
  white-space: nowrap;
}

.diag-detail-modal .diag-detail-card__empty {
  padding: 4px 10px 6px;
  font-size: 11px;
  color: hsl(var(--muted-foreground));
}

.diag-detail-modal .diag-kpi-skeleton {
  padding: 4px 10px 6px;
}

/* 信息行：标签+值紧凑单行（不换行；超宽横向滚动兜底） */
.diag-detail-modal .diag-info-row {
  display: flex;
  gap: 2px 12px;
  align-items: baseline;
  padding: 4px 10px 6px;
  overflow-x: auto;
  font-size: 12px;
  line-height: 18px;
  white-space: nowrap;
}

.diag-detail-modal .diag-info__item {
  display: inline-flex;
  flex-shrink: 0;
  gap: 3px;
  align-items: baseline;
}

.diag-detail-modal .diag-info__item--end {
  margin-left: auto;
}

.diag-detail-modal .diag-info__k {
  font-size: 11px;
  color: hsl(var(--accent-foreground) / 55%);
}

.diag-detail-modal .diag-info__v {
  font-weight: 500;
}

/* 超长文本（名称/装置路径）截断省略，不换行 */
.diag-detail-modal .diag-ellipsis {
  display: inline-block;
  max-width: 176px;
  overflow: hidden;
  text-overflow: ellipsis;
  vertical-align: bottom;
  white-space: nowrap;
}

/* Tabs 占满剩余高度，tab 内容区滚动 */
.diag-detail-modal .diag-detail-tabs {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
  font-size: 12px;
}

.diag-detail-modal .diag-detail-tabs > .ant-tabs-content-holder {
  flex: 1;
  min-height: 0;
  padding-right: 2px;
  overflow: auto;
}

/* Tab 内容紧凑（表格/标签/文本统一 12px） */
.diag-detail-modal .diag-detail-tabs .ant-table {
  font-size: 12px;
}

.diag-detail-modal .diag-detail-tabs .ant-table-cell {
  padding: 4px 8px;
  font-size: 12px;
}

.diag-detail-modal .diag-detail-tabs .ant-tag {
  font-size: 11px;
}

/* 人工复核区（诊断结论 Tab 下半） */
.diag-detail-modal .diag-review {
  padding: 10px 12px;
  margin-top: 10px;
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border));
  border-radius: 6px;
}

.diag-detail-modal .diag-review__title {
  display: flex;
  align-items: center;
  margin-bottom: 6px;
  font-size: 12px;
  font-weight: 600;
  color: hsl(var(--foreground) / 85%);
}

.diag-detail-modal .diag-review__form .ant-form-item {
  margin-bottom: 8px;
}

.diag-detail-modal .diag-review__form .ant-form-item-label > label {
  font-size: 12px;
}

.diag-detail-modal .diag-review__meta {
  display: flex;
  gap: 12px;
  align-items: flex-end;
}

.diag-detail-modal .diag-review__field {
  display: flex;
  flex: 1;
  gap: 6px;
  align-items: center;
}

.diag-detail-modal .diag-review__k {
  flex-shrink: 0;
  font-size: 11px;
  color: hsl(var(--muted-foreground));
}

/* 处置建议列表 */
.diag-detail-modal .diag-action-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.diag-detail-modal .diag-action-item {
  padding: 6px 10px;
  font-size: 12px;
  background: hsl(var(--accent) / 20%);
  border: 1px solid hsl(var(--border));
  border-radius: 6px;
}

.diag-detail-modal .diag-action-new {
  padding: 8px 10px;
  margin-top: 8px;
  background: hsl(var(--card));
  border: 1px dashed hsl(var(--border));
  border-radius: 6px;
}

.diag-detail-modal .diag-action-new__footer {
  display: flex;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
  margin-top: 6px;
}

/* Tab4 前后对比（16 号文 F2） */
.diag-detail-modal .diag-cmp-bar {
  display: flex;
  gap: 10px;
  align-items: center;
  margin-bottom: 8px;
}

.diag-detail-modal .diag-cmp-hint {
  font-size: 11px;
  color: hsl(var(--muted-foreground));
}

.diag-detail-modal .diag-cmp-runs {
  display: flex;
  gap: 8px;
  align-items: stretch;
}

.diag-detail-modal .diag-cmp-run {
  flex: 1;
  min-width: 0;
  padding: 6px 10px;
  font-size: 12px;
  background: hsl(var(--accent) / 20%);
  border: 1px solid hsl(var(--border));
  border-radius: 6px;
}

.diag-detail-modal .diag-cmp-run--target {
  border-color: hsl(var(--primary) / 45%);
}

.diag-detail-modal .diag-cmp-run__tag {
  margin-bottom: 2px;
  font-size: 11px;
  font-weight: 600;
  color: hsl(var(--muted-foreground));
}

.diag-detail-modal .diag-cmp-run__win {
  margin-left: 8px;
  font-size: 11px;
  color: hsl(var(--accent-foreground) / 55%);
  white-space: nowrap;
}

.diag-detail-modal .diag-cmp-arrow {
  align-self: center;
  font-size: 14px;
  color: hsl(var(--muted-foreground));
}

.diag-detail-modal .diag-cmp-cards {
  display: flex;
  gap: 8px;
  margin: 8px 0;
}

.diag-detail-modal .diag-cmp-card {
  flex: 1;
  min-width: 0;
  padding: 6px 10px;
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border));
  border-radius: 6px;
}

.diag-detail-modal .diag-cmp-card__k {
  font-size: 11px;
  color: hsl(var(--muted-foreground));
}

.diag-detail-modal .diag-cmp-card__v {
  margin-top: 2px;
  font-size: 13px;
  white-space: nowrap;
}

.diag-detail-modal .diag-cmp-sep {
  margin: 0 6px;
  color: hsl(var(--muted-foreground));
}

.diag-detail-modal .diag-cmp-dir {
  margin-left: 8px;
  font-size: 12px;
  font-weight: 500;
}

.diag-detail-modal .diag-cmp-sec {
  margin: 10px 0 4px;
  font-size: 12px;
  font-weight: 600;
  color: hsl(var(--foreground) / 85%);
}

.diag-detail-modal .diag-cmp-table {
  width: 100%;
  font-size: 12px;
  border-collapse: collapse;
}

.diag-detail-modal .diag-cmp-table th,
.diag-detail-modal .diag-cmp-table td {
  padding: 3px 8px;
  text-align: left;
  border-bottom: 1px solid hsl(var(--border));
}

.diag-detail-modal .diag-cmp-table th {
  font-size: 11px;
  font-weight: 600;
  color: hsl(var(--muted-foreground));
  white-space: nowrap;
}

.diag-detail-modal .diag-cmp-table td.num {
  font-variant-numeric: tabular-nums;
  text-align: right;
  white-space: nowrap;
}

.diag-detail-modal .diag-cmp-empty {
  padding: 6px 0;
  font-size: 11px;
  color: hsl(var(--muted-foreground));
}

/* 证据波形并排（16 号文 F2） */
.diag-detail-modal .diag-cmp-evidence {
  margin-top: 10px;
}

.diag-detail-modal .diag-cmp-charts {
  display: flex;
  gap: 12px;
}

.diag-detail-modal .diag-cmp-chart-col {
  flex: 1;
  min-width: 0;
}

.diag-detail-modal .diag-cmp-chart-title {
  margin-bottom: 4px;
  font-size: 12px;
  font-weight: 600;
  color: hsl(var(--foreground) / 85%);
}

.diag-detail-modal .diag-cmp-chart-label {
  margin: 6px 0 2px;
  font-size: 11px;
  color: hsl(var(--muted-foreground));
}

/* 右下角宽高手柄 */
.diag-detail-modal .diag-detail-resize {
  position: absolute;
  right: 0;
  bottom: 0;
  z-index: 10;
  width: 16px;
  height: 16px;
  cursor: nwse-resize;
  background: linear-gradient(135deg, transparent 50%, hsl(var(--border)) 50%);
  border-end-end-radius: 8px;
}

.diag-detail-modal .diag-detail-resize:hover {
  background: linear-gradient(
    135deg,
    transparent 50%,
    hsl(var(--primary) / 45%) 50%
  );
}
</style>
