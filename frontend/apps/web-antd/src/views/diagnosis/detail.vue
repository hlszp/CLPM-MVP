<script lang="ts" setup>
/**
 * S4-DIAG 诊断详情页（P1a Tab布局重构）
 *
 * 对齐 FDS §5.4 + IDS v3.2 §2.4 + PRD §4.4 + P1a整改方案
 * - 顶部：回路基本信息 + 综合评分 + 最高标签置信度 + 风险等级 + 处理状态 + 时间窗切换
 * - 主区：Tab布局
 *   - Tab 1「诊断证据」：原趋势图（WaveformChart）+ PV-OP 散点图 + 证据链 + 推荐动作（保留原有65/35布局）
 *   - Tab 2「处置时间线」：ClpmDispositionTimeline组件 + 状态操作按钮（认领/实施/验证通过/重开/忽略）
 *   - Tab 3「整定对比」：预留（跳转回路整定模块，上下文传递）
 *   - Tab 4「A/B验证」：预留（A/B对比数据展示）
 * - FE-14：诊断建议书 PDF 导出按钮
 * - P1a：闭环状态机PENDING→IN_PROGRESS→VERIFYING→CLOSED，VERIFYING可→REOPENED
 */
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { DiagnosisApi } from '#/api/diagnosis';
import type { SummaryAction, SummaryItem } from '#/components/clpm';
import type { ImplementSubmitData } from '#/components/clpm/implement-record-modal.vue';

import { computed, nextTick, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';
import { IconifyIcon } from '@vben/icons';
import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import {
  Button,
  message,
  Modal,
  Popconfirm,
  RadioGroup,
  Skeleton,
  Spin,
  Steps,
  Table,
  Tabs,
  Tag,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import {
  generateDiagnosisReportApi,
  getDiagnosisDetailApi,
  getLoopTimelineApi,
  getRecommendationsApi,
  getTrackerListApi,
  getWaveformApi,
  updateTrackerStatusApi,
} from '#/api/diagnosis';
import {
  ClpmAiDrawer,
  ClpmDataCanvas,
  ClpmDispositionTimeline,
  ClpmImplementRecordModal,
  ClpmObjectSummaryBar,
  ClpmPageToolbar,
  ClpmStandardActions,
  ClpmStructuredDiagnosisReport,
  ClpmThresholdTuneModal,
  ClpmToolbarButton,
} from '#/components/clpm';
import Recommendations from '#/components/diagnosis/recommendations.vue';
import WaveformChart from '#/components/loop/waveform-chart.vue';
import { useAiInsightGate } from '#/composables/use-ai-insight-gate';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import { showPageHelp, usePageToolbar } from '#/composables/use-page-toolbar';
import {
  DIAGNOSIS_LABEL_COLOR_MAP,
  DIAGNOSIS_LABEL_NAME_MAP,
} from '#/constants/diagnosis';
import featureDictionary from '#/constants/feature-dictionary.json';
import { formatTime } from '#/utils/format';

defineOptions({ name: 'DiagnosisDetail' });

const { isDark, themeColors } = useClpmTheme();

const route = useRoute();
const router = useRouter();
const loopId = ref(route.params.loopId as string);

// AI 洞察两级门禁（diagnosis 场景需 loopId，本页恒有 loopId，门禁2 恒通过）
const { init: initAiGate, gateStatus, gateTooltip } = useAiInsightGate();
initAiGate();
const aiDrawerOpen = ref(false);
const aiGateStatus = computed(() => gateStatus(loopId.value, true));
const aiGateTooltip = computed(() => gateTooltip(aiGateStatus.value));

let detailVersion = 0;
let waveformVersion = 0;

const loading = ref(false);
const waveformLoading = ref(false);
const recommendationsLoading = ref(false);
const reportGenerating = ref(false);
const timelineLoading = ref(false);
const statusUpdating = ref(false);
const detail = ref<DiagnosisApi.DiagnosisDetail | null>(null);
const waveform = ref<DiagnosisApi.WaveformResult | null>(null);
const recommendations = ref<DiagnosisApi.RecommendationItem[]>([]);
const trackerItem = ref<DiagnosisApi.TrackerItem | null>(null);
const timeline = ref<DiagnosisApi.TimelineData | null>(null);
const timeWindow = ref<DiagnosisApi.TimeWindow>('last_24_hours');
const activeTab = ref('evidence');

// 实施记录弹窗
const implementModalVisible = ref(false);

// P3-02: 阈值微调弹窗
const thresholdTuneVisible = ref(false);

// ===== D2 多图联动：趋势图 ↔ 散点图 =====
const selectedTime = ref<null | { index: number; timestamp: string }>(null);
const selectedTimestamp = computed(() => selectedTime.value?.timestamp ?? null);

const timeWindowOptions: { label: string; value: DiagnosisApi.TimeWindow }[] = [
  { label: '近 24 小时', value: 'last_24_hours' },
  { label: '近 7 天', value: 'last_7_days' },
  { label: '近 30 天', value: 'last_30_days' },
];

const labelNameMap = DIAGNOSIS_LABEL_NAME_MAP;

// ===== Phase C §4.3：特征字典 + 问题定位路径 =====

/** 特征字典条目（为证据区特征值表提供工程含义列） */
interface FeatureDictEntry {
  name: string;
  meaning: string;
  engineering: string;
  threshold: string;
  unit: string;
  labels: string[];
}
const featureDict = featureDictionary as unknown as Record<
  string,
  FeatureDictEntry
>;

/** 诊断标签 → 现象描述（问题定位路径节点①：现象） */
const LABEL_PHENOMENON_MAP: Record<string, string> = {
  EXTERNAL_DISTURBANCE: 'PV 均值漂移波动',
  OSCILLATION: 'PV 周期性振荡',
  OUTPUT_SATURATION: 'OP 长期触限',
  OVERAGGRESSIVE: '阶跃响应超调振荡',
  OVERCONSERVATIVE: '响应迟缓跟不上 SP',
  QUALITY_ABNORMAL: 'PV 质量码异常',
  VALVE_STICTION: 'OP 变化 PV 响应迟缓',
};

/** 证据区-特征值表折叠状态（默认折叠，技术细节） */
const evidenceCollapsed = ref(true);
/** 证据区-波形/散点折叠状态（默认展开，保证 ECharts 首屏渲染） */
const waveformCollapsed = ref(false);

// 散点图 ECharts
const scatterChartRef = ref<EchartsUIType>();
const { renderEcharts: renderScatter, getChartInstance: getScatterInstance } =
  useEcharts(scatterChartRef);

const pageTitle = computed(() => {
  if (detail.value?.tagName) {
    return `诊断详情 - ${detail.value.tagName}`;
  }
  return '诊断详情';
});

/** 风险等级：基于综合评分推导 */
const riskLevel = computed<{
  label: string;
  status: SummaryItem['status'];
}>(() => {
  const score = detail.value?.compositeScore ?? 0;
  if (score < 60) return { label: 'HIGH', status: 'danger' };
  if (score < 80) return { label: 'MEDIUM', status: 'warning' };
  return { label: 'LOW', status: 'primary' };
});

/** B5：可信度等级角标 */
const confidenceLevelTag = computed<{
  label: string;
  status: SummaryItem['status'];
}>(() => {
  const level = detail.value?.confidenceLevel;
  const validRate = detail.value?.validRate;
  if (!level) return { label: '—', status: 'neutral' };
  const statusMap: Record<string, SummaryItem['status']> = {
    A: 'success',
    B: 'success',
    C: 'warning',
    D: 'danger',
    E: 'danger',
  };
  const rateText =
    validRate === null || validRate === undefined
      ? ''
      : `（${(validRate * 100).toFixed(1)}%）`;
  return {
    label: `${level}${rateText}`,
    status: statusMap[level] ?? 'neutral',
  };
});

/** P1a：处理状态文案与色彩（支持完整闭环状态机） */
const trackerStatusTag = computed<{
  label: string;
  status: SummaryItem['status'];
}>(() => {
  const labelMap: Record<DiagnosisApi.ActionStatus, string> = {
    PENDING: '待处理',
    IN_PROGRESS: '处理中',
    VERIFYING: '待验证',
    IMPLEMENTED: '已实施',
    CLOSED: '已闭环',
    REOPENED: '已重开',
    IGNORED: '已忽略',
  };
  const statusMap: Record<DiagnosisApi.ActionStatus, SummaryItem['status']> = {
    PENDING: 'warning',
    IN_PROGRESS: 'primary',
    VERIFYING: 'warning',
    IMPLEMENTED: 'success',
    CLOSED: 'success',
    REOPENED: 'danger',
    IGNORED: 'neutral',
  };
  if (!trackerItem.value) {
    return { label: '未跟踪', status: 'neutral' };
  }
  const status = trackerItem.value.actionStatus;
  return {
    label: labelMap[status] ?? status,
    status: statusMap[status] ?? 'neutral',
  };
});

/** 跟踪状态颜色 */
const trackerStatusColor = computed(() => {
  const map: Record<string, string> = {
    danger: themeColors.value.DANGER,
    warning: themeColors.value.WARNING,
    primary: themeColors.value.INFO,
    success: themeColors.value.SUCCESS,
    neutral: themeColors.value.NEUTRAL,
  };
  return (
    map[trackerStatusTag.value.status ?? 'neutral'] ?? themeColors.value.NEUTRAL
  );
});

function getThresholdStatus(
  value: number,
  successThreshold: number,
  warningThreshold: number,
): NonNullable<SummaryItem['status']> {
  if (value >= successThreshold) return 'success';
  if (value >= warningThreshold) return 'warning';
  return 'danger';
}

const summaryItems = computed<SummaryItem[]>(() => {
  if (!detail.value) return [];
  return [
    {
      key: 'score',
      label: '综合评分',
      value: Number(detail.value.compositeScore).toFixed(2),
      status: getThresholdStatus(detail.value.compositeScore, 80, 60),
    },
    {
      key: 'confidence',
      label: '最高标签置信度',
      value:
        detail.value.fusedConfidence === null
          ? '—'
          : Number(detail.value.fusedConfidence).toFixed(2),
      status:
        detail.value.fusedConfidence === null
          ? undefined
          : getThresholdStatus(detail.value.fusedConfidence, 0.8, 0.5),
    },
    {
      key: 'confidenceLevel',
      label: '可信度',
      value: confidenceLevelTag.value.label,
      status: confidenceLevelTag.value.status,
    },
    {
      key: 'risk',
      label: '风险等级',
      value: riskLevel.value.label,
      status: riskLevel.value.status,
    },
    {
      key: 'trackerStatus',
      label: '处理状态',
      value: trackerStatusTag.value.label,
      status: trackerStatusTag.value.status,
    },
    {
      key: 'time',
      label: '诊断时间',
      value: formatTime(detail.value.diagnosedAt),
      status: 'neutral',
    },
  ];
});

/** 摘要条右侧操作 */
const summaryActions = computed<SummaryAction[]>(() => {
  return [
    {
      key: 'track',
      label: trackerItem.value ? '处置跟踪' : '加入跟踪',
      icon: 'ant-design:flag-outlined',
      type: 'primary',
    },
    {
      key: 'tuning',
      label: '回路整定',
      icon: 'ant-design:sliders-outlined',
      type: 'default',
    },
    // Phase C: 可视化分析能力已并入诊断证据区，移除独立入口
  ];
});

/** P1a：根据当前状态计算可用操作按钮 */
const availableActions = computed(() => {
  const actions: {
    danger?: boolean;
    key: string;
    label: string;
    variant?: string;
  }[] = [];
  const status = trackerItem.value?.actionStatus;

  if (!status || status === 'PENDING') {
    actions.push({ key: 'claim', label: '认领处理', variant: 'primary' });
  }
  if (status === 'IN_PROGRESS' || status === 'REOPENED') {
    actions.push({ key: 'implement', label: '标记已实施', variant: 'primary' });
  }
  if (status === 'VERIFYING' || status === 'IMPLEMENTED') {
    actions.push(
      { key: 'verify_pass', label: '验证通过', variant: 'primary' },
      { key: 'verify_fail', label: '验证不通过', danger: true },
    );
  }
  if (status && status !== 'CLOSED' && status !== 'IGNORED') {
    actions.push({ key: 'ignore', label: '标记忽略' });
  }

  return actions;
});

/** Phase C §4.3.2：问题定位路径 —— 现象→特征→根因 横向流程（3 节点） */
const problemPathSteps = computed(() => {
  if (!detail.value || !detail.value.diagnosisLabels?.length) {
    return [
      { description: '采集 PV/SP/OP 时序数据', title: '现象' },
      { description: 'FFT/散点拟合/质量码统计', title: '特征' },
      { description: '未检测到异常标签', title: '暂无结论' },
    ];
  }
  const label = detail.value.diagnosisLabels[0];
  if (!label) {
    return [
      { description: '采集 PV/SP/OP 时序数据', title: '现象' },
      { description: 'FFT/散点拟合/质量码统计', title: '特征' },
      { description: '未检测到异常标签', title: '暂无结论' },
    ];
  }
  const phenomenon = LABEL_PHENOMENON_MAP[label.label] || '过程异常';
  // 取该标签关联的关键特征值（top 2）作为特征节点描述
  const relatedFeatures = Object.entries(detail.value.featureValues || {})
    .filter(([k]) => featureDict[k]?.labels?.includes(label.label))
    .slice(0, 2)
    .map(([k, v]) => `${featureDict[k]?.name ?? k} ${Number(v).toFixed(2)}`)
    .join(' / ');
  return [
    { description: phenomenon, title: '现象' },
    {
      description: relatedFeatures || '特征值提取',
      title: '特征',
    },
    {
      description: `${label.labelName || labelNameMap[label.label] || label.label}（置信度 ${(label.confidence * 100).toFixed(0)}%）`,
      title: '根因',
    },
  ];
});

const currentStep = computed(() => {
  return Math.max(0, problemPathSteps.value.length - 1);
});

/** P2-02：A/B 验证计划 */
const abVerifyPlan = computed(() => {
  if (!timeline.value) return null;
  const status = timeline.value.currentStatus;
  const implementedEvent = timeline.value.events.find(
    (e) => e.eventType === 'implemented',
  );
  return {
    status,
    implementedAt: implementedEvent?.timestamp ?? null,
    pendingVerificationAt: timeline.value.pendingVerificationAt ?? null,
    isVerifying: status === 'VERIFYING',
    isClosed: status === 'CLOSED',
    isPending:
      status === 'PENDING' ||
      status === 'IN_PROGRESS' ||
      status === 'REOPENED' ||
      status === 'IGNORED',
  };
});

/** A/B 验证 Steps 项 */
const abVerifySteps = computed(() => {
  if (!abVerifyPlan.value) return [];
  const plan = abVerifyPlan.value;
  return [
    {
      title: '参数已实施',
      description: plan.implementedAt
        ? `实施时间：${formatTime(plan.implementedAt)}`
        : '已记录实施参数',
    },
    {
      title: '数据采集期',
      description: '系统正在采集实施后的运行数据（24 小时）',
    },
    {
      title: '自动验证',
      description: plan.pendingVerificationAt
        ? `预计验证：${formatTime(plan.pendingVerificationAt)}`
        : '采集完成后自动生成对比报告',
    },
  ];
});

function getTimeRange(tw: DiagnosisApi.TimeWindow): [dayjs.Dayjs, dayjs.Dayjs] {
  switch (tw) {
    case 'last_7_days': {
      return [dayjs().subtract(7, 'day'), dayjs()];
    }
    case 'last_30_days': {
      return [dayjs().subtract(30, 'day'), dayjs()];
    }
    default: {
      return [dayjs().subtract(24, 'hour'), dayjs()];
    }
  }
}

/** 加载诊断详情 */
async function loadDetail() {
  const version = ++detailVersion;
  loading.value = true;
  try {
    const data = await getDiagnosisDetailApi(loopId.value, timeWindow.value);
    if (version !== detailVersion) return;
    detail.value = data;
    renderScatterChart();
  } catch {
    // 错误已由拦截器处理
  } finally {
    if (version === detailVersion) loading.value = false;
  }
}

/** 加载全部数据 */
function loadAll() {
  loadDetail();
  loadWaveform();
  loadRecommendations();
  loadTrackerAndTimeline();
}

/** 加载时序波形数据 */
async function loadWaveform() {
  if (!loopId.value) return;
  const version = ++waveformVersion;
  waveformLoading.value = true;
  try {
    const [start, end] = getTimeRange(timeWindow.value);
    const data = await getWaveformApi(loopId.value, {
      startTime: start.format('YYYY-MM-DD HH:mm:ss'),
      endTime: end.format('YYYY-MM-DD HH:mm:ss'),
      downsample: true,
      maxPoints: 2000,
    });
    if (version !== waveformVersion) return;
    waveform.value = data;
  } catch {
    // 错误已由拦截器处理
  } finally {
    if (version === waveformVersion) waveformLoading.value = false;
  }
}

/** 加载解决方案推荐 */
async function loadRecommendations() {
  if (!loopId.value) return;
  recommendationsLoading.value = true;
  try {
    const data = await getRecommendationsApi(loopId.value);
    recommendations.value = data.recommendations ?? [];
  } catch {
    // 错误已由拦截器处理
  } finally {
    recommendationsLoading.value = false;
  }
}

/** P1a：加载Tracker详情和时间线 */
async function loadTrackerAndTimeline() {
  if (!loopId.value) return;
  timelineLoading.value = true;
  try {
    const [trackerRes, timelineRes] = await Promise.all([
      getTrackerListApi({ loopId: loopId.value, page: 1, pageSize: 1 }),
      getLoopTimelineApi(loopId.value),
    ]);
    trackerItem.value = trackerRes.items[0] ?? null;
    timeline.value = timelineRes;
  } catch {
    // 错误已由拦截器处理
  } finally {
    timelineLoading.value = false;
  }
}

/** FE-14: 生成并下载诊断建议书 PDF */
async function handleGenerateReport() {
  if (!loopId.value) return;
  reportGenerating.value = true;
  try {
    const blob = await generateDiagnosisReportApi(loopId.value);
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `诊断建议书_${detail.value?.tagName ?? loopId.value}_${dayjs().format('YYYYMMDD_HHmmss')}.pdf`;
    document.body.append(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    message.success('诊断建议书已生成');
  } catch {
    // 错误已由拦截器处理
  } finally {
    reportGenerating.value = false;
  }
}

function handleRefresh() {
  // P3-27：刷新前确认——存在未保存的交互状态（波形选点 / 阈值微调弹窗）时提示数据丢失
  const hasUnsavedState =
    selectedTime.value !== null || thresholdTuneVisible.value;
  if (!hasUnsavedState) {
    loadAll();
    return;
  }
  Modal.confirm({
    title: '确认刷新？',
    content:
      '刷新将重新加载诊断数据，当前波形选点与未保存的阈值微调内容将丢失。是否继续？',
    okText: '刷新',
    cancelText: '取消',
    onOk: () => {
      selectedTime.value = null;
      thresholdTuneVisible.value = false;
      loadAll();
    },
  });
}

/** 工具栏帮助 */
function handleHelp() {
  showPageHelp({
    title: '诊断详情 帮助',
    content:
      '查看回路诊断证据（标签、特征值、波形、散点图）、处置时间线与 A/B 验证。支持时间窗切换、AI 诊断洞察、阈值微调、导出诊断建议书 PDF。处置状态机：待处理 → 处理中 → 待验证 → 已闭环/已重开。',
  });
}

// ===== 统一工具栏（标准 2 工具：刷新 / 帮助） =====
const { toolbarItems } = usePageToolbar(() => ({
  refresh: { onClick: handleRefresh, loading: loading.value },
  help: { onClick: handleHelp },
}));

/** P0-03：构建诊断→整定上下文 query 参数 */
function buildTuningContextQuery() {
  const labels = detail.value?.diagnosisLabels ?? [];
  const primaryLabel = labels[0]?.label ?? '';
  return {
    loopId: loopId.value,
    diagnosisLabel: primaryLabel,
    confidenceLevel: detail.value?.confidenceLevel ?? '',
    from: 'diagnosis',
    // P1-07：携带返回路径，整定页可一键返回诊断详情
    returnTo: `/diagnosis/detail?loopId=${loopId.value}`,
  };
}

/** 摘要条操作点击 */
function handleSummaryAction(key: string) {
  if (key === 'track') {
    activeTab.value = 'timeline';
  }
  if (key === 'tuning') {
    // P0-03：跳转回路整定工作台，传递诊断上下文（标签/可信度）
    router.push({
      path: '/tuning/workbench',
      query: buildTuningContextQuery(),
    });
  }
}

/**
 * P2-01：结构化诊断报告「前往整定」点击
 * 以指定标签为主因跳转整定工作台
 */
function handleStructuredReportTuning(label: string) {
  router.push({
    path: '/tuning/workbench',
    query: {
      loopId: loopId.value,
      diagnosisLabel: label,
      confidenceLevel: detail.value?.confidenceLevel ?? '',
      from: 'diagnosis',
      returnTo: `/diagnosis/detail?loopId=${loopId.value}`,
    },
  });
}

function handleAdoptRecommendation(_rec: DiagnosisApi.RecommendationItem) {
  activeTab.value = 'timeline';
}

/** 渲染散点图 */
function renderScatterChart() {
  const scatter = detail.value?.evidenceChain?.scatterPlot;
  if (!scatter || !scatter.x || scatter.x.length === 0) {
    renderScatter({ title: { left: 'center', text: '暂无散点数据' } });
    return;
  }

  const highlightedSet = new Set<number>();
  if (selectedTime.value && waveform.value) {
    const ts = waveform.value.timestamps;
    if (ts.length === scatter.x.length) {
      const selectedTs = Number(selectedTime.value.timestamp);
      const WINDOW = 30_000;
      for (const [i, t] of ts.entries()) {
        if (Math.abs(t - selectedTs) <= WINDOW) {
          highlightedSet.add(i);
        }
      }
    }
  }

  const dangerColor = themeColors.value.DANGER;
  const infoColor = themeColors.value.INFO;

  const data = scatter.x.map((x, i) => {
    const isHi = highlightedSet.has(i);
    return {
      itemStyle: {
        color: isHi ? dangerColor : infoColor,
        opacity: isHi ? 1 : 0.4,
      },
      symbolSize: isHi ? 10 : 5,
      value: [x, scatter.y[i] ?? 0],
    };
  });

  renderScatter({
    backgroundColor: 'transparent',
    grid: { bottom: 60, containLabel: true, left: '2%', right: '2%', top: 40 },
    series: [{ data, name: 'PV-OP', type: 'scatter' }],
    tooltip: {
      formatter: (params: any) => {
        return `X: ${Number(params.value[0]).toFixed(3)}<br/>Y: ${Number(
          params.value[1],
        ).toFixed(3)}`;
      },
      trigger: 'item',
    },
    xAxis: { name: 'OP', nameGap: 30, nameLocation: 'middle', type: 'value' },
    yAxis: { name: 'PV', nameGap: 40, nameLocation: 'middle', type: 'value' },
  }).then(() => {
    bindScatterClick();
  });
}

function scatterClickHandler(params: any) {
  if (params.componentType === 'series' && params.seriesType === 'scatter') {
    const idx = params.dataIndex;
    const scatter = detail.value?.evidenceChain?.scatterPlot;
    if (!scatter || !waveform.value) return;
    const ts = waveform.value.timestamps;
    if (ts.length === scatter.x.length && idx >= 0 && idx < ts.length) {
      selectedTime.value = { timestamp: String(ts[idx]), index: idx };
    }
  }
}

function bindScatterClick() {
  const chart = getScatterInstance();
  if (!chart) return;
  chart.off('click', scatterClickHandler);
  chart.on('click', scatterClickHandler);
}

function onTrendTimeSelect(payload: { index: number; timestamp: string }) {
  selectedTime.value = payload;
}

function clearSelection() {
  selectedTime.value = null;
}

function formatSelectedTime(ts: string): string {
  const n = Number(ts);
  if (Number.isNaN(n)) return ts;
  const d = new Date(n);
  if (Number.isNaN(d.getTime())) return ts;
  return formatTime(d.toISOString());
}

function handleBack() {
  router.back();
}

// ===== Phase C §4.3.2：三区重构辅助计算 =====

/** ① 结论区：诊断小结（模板生成，LLM 未启用时兜底始终可用） */
const diagnosisSummary = computed(() => {
  if (!detail.value) return '';
  const labels = detail.value.diagnosisLabels ?? [];
  if (labels.length === 0) return '未检测到异常标签，回路运行状态正常。';
  const primary = labels[0];
  if (!primary) return '未检测到异常标签，回路运行状态正常。';
  const labelName =
    primary.labelName || labelNameMap[primary.label] || primary.label;
  const conf = `${(primary.confidence * 100).toFixed(0)}%`;
  const keyFeatures = Object.entries(detail.value.featureValues || {})
    .filter(([k]) => featureDict[k]?.labels?.includes(primary.label))
    .slice(0, 2)
    .map(([k, v]) => `${featureDict[k]?.name ?? k} ${Number(v).toFixed(2)}`);
  const featStr = keyFeatures.length > 0 ? `（${keyFeatures.join('，')}）` : '';
  return `检测到 ${labelName}，置信度 ${conf}${featStr}，建议结合下方证据链与推荐动作处置。`;
});

/** ③ 证据区：特征值表数据（带工程含义列，对齐特征字典） */
const featureTableData = computed(() => {
  const features = detail.value?.featureValues;
  if (!features) return [];
  return Object.entries(features).map(([k, v], i) => {
    const dict = featureDict[k];
    return {
      key: k,
      index: i + 1,
      name: dict?.name ?? k,
      value: Number(v).toFixed(4),
      unit: dict?.unit ?? '',
      threshold: dict?.threshold ?? '—',
      engineering: dict?.engineering ?? '—',
    };
  });
});

/** ③ 证据区：特征值表列定义 */
const featureColumns = [
  {
    title: '#',
    dataIndex: 'index',
    key: 'index',
    width: 50,
    align: 'center' as const,
  },
  { title: '名称', dataIndex: 'name', key: 'name', width: 140 },
  {
    title: '值',
    dataIndex: 'value',
    key: 'value',
    width: 100,
    align: 'right' as const,
  },
  {
    title: '单位',
    dataIndex: 'unit',
    key: 'unit',
    width: 60,
    align: 'center' as const,
  },
  { title: '阈值参考', dataIndex: 'threshold', key: 'threshold', width: 120 },
  { title: '工程含义', dataIndex: 'engineering', key: 'engineering' },
];

/** ① 结论区：相对时间格式化（如"2小时前"） */
function formatRelativeTime(t: null | string | undefined): string {
  if (!t) return '';
  try {
    const target = dayjs(t);
    const now = dayjs();
    const diffMin = now.diff(target, 'minute');
    if (diffMin < 1) return '刚刚';
    if (diffMin < 60) return `${diffMin} 分钟前`;
    const diffHour = now.diff(target, 'hour');
    if (diffHour < 24) return `${diffHour} 小时前`;
    return `${now.diff(target, 'day')} 天前`;
  } catch {
    return '';
  }
}

/** ② 问题定位路径：节点点击展开对应证据区 */
function handlePathStepClick(stepIndex: number) {
  if (stepIndex === 1) {
    // 特征节点 → 展开特征值表
    evidenceCollapsed.value = false;
  } else if (stepIndex === 2 || stepIndex === 0) {
    // 根因/现象节点 → 展开波形证据
    waveformCollapsed.value = false;
  }
}

// ===== P1a: 状态操作逻辑 =====

/** P1a：状态操作确认文案（可逆轻操作走 Popconfirm 内联确认） */
const actionConfirmMap: Record<
  string,
  { danger?: boolean; okText: string; title: string }
> = {
  claim: { title: '确定认领该异常并开始处理吗？', okText: '确定' },
  verify_pass: {
    title: '确认整改效果验证通过，该异常将标记为已闭环？',
    okText: '确认闭环',
  },
  verify_fail: {
    title: '整改效果未达预期，将重新打开该异常进行处理。是否继续？',
    okText: '重开',
  },
  ignore: {
    title: '确定忽略该异常吗？忽略后将不再出现在待处理列表中。',
    okText: '确定忽略',
    danger: true,
  },
};

/** Popconfirm 确认后按操作类型分发 */
function handleActionConfirm(key: string) {
  switch (key) {
    case 'claim': {
      handleClaim();

      break;
    }
    case 'ignore': {
      handleIgnore();

      break;
    }
    case 'verify_fail': {
      handleVerifyFail();

      break;
    }
    case 'verify_pass': {
      handleVerifyPass();

      break;
    }
    // No default
  }
}

/** 认领处理 */
async function handleClaim() {
  await updateStatus('IN_PROGRESS');
}

/** 标记已实施 - 打开实施记录弹窗 */
function handleImplement() {
  implementModalVisible.value = true;
}

/** 实施记录提交 */
async function handleImplementSubmit(data: ImplementSubmitData) {
  await updateStatus('VERIFYING', {
    newPidP: data.newPidP,
    newPidI: data.newPidI,
    newPidD: data.newPidD,
    implementedAt: data.implementedAt,
    comment: data.comment,
    mocRef: data.mocRef,
    mocNotApplicable: data.mocNotApplicable,
    mocReason: data.mocReason,
    tuningRecordId: data.tuningRecordId ?? null,
  });
  implementModalVisible.value = false;
  // P2-02：实施后明确提示验证周期
  Modal.info({
    title: '已标记为验证中',
    content:
      '系统将在 24 小时数据采集后自动生成 A/B 对比报告。请稍后在「A/B 验证」Tab 查看对比结果，或等待系统自动验证通知。',
    okText: '知道了',
  });
}

/** 验证通过 */
async function handleVerifyPass() {
  await updateStatus('CLOSED');
}

/** 验证不通过 - 重开 */
async function handleVerifyFail() {
  // 重开需要填写原因，此处简化处理
  await updateStatus('REOPENED', {
    reopenReason: '自动验证不通过，请重新整定参数',
  });
}

/** 标记忽略 */
async function handleIgnore() {
  await updateStatus('IGNORED');
}

/** 统一状态更新 */
async function updateStatus(
  status: DiagnosisApi.ActionStatus,
  extraData: Partial<DiagnosisApi.TrackerStatusUpdateParams> = {},
) {
  statusUpdating.value = true;
  try {
    await updateTrackerStatusApi(loopId.value, {
      status,
      ...extraData,
    });
    message.success('状态更新成功');
    await loadTrackerAndTimeline();
  } catch {
    // 错误已由拦截器处理
  } finally {
    statusUpdating.value = false;
  }
}

/** Tab切换时按需加载数据 */
function handleTabChange(key: number | string) {
  const tabKey = String(key);
  activeTab.value = tabKey;
  if (tabKey === 'timeline' && !timeline.value) {
    loadTrackerAndTimeline();
  }
}

watch(timeWindow, () => {
  loadAll();
});

watch(
  () => route.params.loopId,
  (newLoopId) => {
    if (newLoopId && newLoopId !== loopId.value) {
      loopId.value = newLoopId as string;
      selectedTime.value = null;
      timeline.value = null;
      trackerItem.value = null;
      activeTab.value = 'evidence';
      loadAll();
    }
  },
);

watch(selectedTime, () => {
  nextTick(() => renderScatterChart());
});

watch(isDark, () => {
  nextTick(() => {
    renderScatterChart();
  });
});

onMounted(() => {
  loadAll();
});
</script>

<template>
  <Page>
    <ClpmPageToolbar
      :title="pageTitle"
      :subtitle="detail?.tagName || '诊断证据与处置'"
    >
      <RadioGroup
        v-model:value="timeWindow"
        :options="timeWindowOptions"
        option-type="button"
        button-style="solid"
        size="small"
      />
      <template #actions>
        <ClpmToolbarButton
          icon="ai"
          label="AI 诊断洞察"
          :disabled="aiGateStatus !== 'active'"
          :disabled-reason="aiGateTooltip"
          :tooltip="aiGateTooltip"
          @click="aiDrawerOpen = true"
        />
        <ClpmToolbarButton
          icon="export"
          label="导出报告"
          :loading="reportGenerating"
          @click="handleGenerateReport"
        />
        <ClpmToolbarButton
          icon="setting"
          label="阈值微调"
          @click="thresholdTuneVisible = true"
        />
        <ClpmToolbarButton icon="back" label="返回" @click="handleBack" />
        <ClpmStandardActions :items="toolbarItems" />
      </template>
    </ClpmPageToolbar>
    <Spin :spinning="loading">
      <!-- P2-18：时间窗切换骨架屏过渡 -->
      <div v-if="loading && !detail" class="space-y-4 p-4">
        <Skeleton active :paragraph="{ rows: 2 }" />
        <Skeleton active :paragraph="{ rows: 6 }" />
      </div>
      <div v-else class="space-y-4">
        <ClpmObjectSummaryBar
          v-if="detail"
          :title="detail.tagName"
          :subtitle="`回路 ID ${detail.loopId} · 算法 ${detail.algorithmVersion}`"
          :items="summaryItems"
          :actions="summaryActions"
          @action="handleSummaryAction"
        />

        <!-- P1a: Tab布局 -->
        <Tabs
          v-model:active-key="activeTab"
          type="card"
          @change="handleTabChange"
        >
          <!-- Tab 1: 诊断证据（Phase C 三区重构：结论/路径/证据） -->
          <Tabs.TabPane key="evidence" tab="诊断证据">
            <div class="space-y-4">
              <!-- ① 结论区（顶部，一眼可见） -->
              <ClpmDataCanvas
                title="诊断结论"
                description="标签徽章 + 诊断小结，一眼掌握核心结论。"
              >
                <div v-if="detail" class="space-y-3">
                  <!-- 标签徽章行 -->
                  <div class="flex flex-wrap items-center gap-2">
                    <Tag
                      v-for="label in detail.diagnosisLabels"
                      :key="label.label"
                      :color="
                        DIAGNOSIS_LABEL_COLOR_MAP[label.label] || 'default'
                      "
                      class="!m-0"
                    >
                      {{
                        label.labelName ||
                        labelNameMap[label.label] ||
                        label.label
                      }}
                      <span class="ml-1 opacity-70">
                        {{ (label.confidence * 100).toFixed(0) }}%
                      </span>
                    </Tag>
                    <span
                      v-if="detail.diagnosisLabels.length === 0"
                      class="text-sm"
                      :style="{ color: themeColors.NEUTRAL }"
                    >
                      未检测到异常标签
                    </span>
                    <span
                      class="ml-2 text-xs"
                      :style="{ color: themeColors.NEUTRAL }"
                    >
                      发现于 {{ formatRelativeTime(detail.diagnosedAt) }}
                    </span>
                  </div>
                  <!-- 诊断小结(模板) -->
                  <div
                    class="rounded border p-3 text-sm"
                    :style="{ background: 'hsl(var(--muted) / 42%)' }"
                  >
                    {{ diagnosisSummary }}
                  </div>
                  <!-- P2-01：结构化诊断报告（原因排序+根因+建议+预估改善） -->
                  <ClpmStructuredDiagnosisReport
                    v-if="detail.diagnosisLabels.length > 0"
                    :labels="detail.diagnosisLabels"
                    :fused-confidence="detail.fusedConfidence"
                    :confidence-level="detail.confidenceLevel"
                    :show-tuning-action="true"
                    @tuning="handleStructuredReportTuning"
                  />
                </div>
              </ClpmDataCanvas>

              <!-- ② 问题定位路径区（横向流程，节点可点展开证据） -->
              <ClpmDataCanvas
                title="问题定位路径"
                description="现象 → 特征 → 根因，点击节点展开对应证据。"
              >
                <div class="problem-path">
                  <template
                    v-for="(step, index) in problemPathSteps"
                    :key="index"
                  >
                    <div
                      class="path-node"
                      :class="{ 'path-node--active': index === currentStep }"
                      role="button"
                      tabindex="0"
                      :aria-current="index === currentStep ? 'step' : undefined"
                      @click="handlePathStepClick(index)"
                      @keydown.enter="handlePathStepClick(index)"
                      @keydown.space.prevent="handlePathStepClick(index)"
                    >
                      <div class="path-node-index">{{ index + 1 }}</div>
                      <div class="path-node-body">
                        <div class="path-node-title">{{ step.title }}</div>
                        <div
                          class="path-node-desc"
                          :style="{ color: themeColors.NEUTRAL }"
                        >
                          {{ step.description }}
                        </div>
                      </div>
                    </div>
                    <IconifyIcon
                      v-if="index < problemPathSteps.length - 1"
                      icon="ant-design:arrow-right-outlined"
                      :size="20"
                      class="path-arrow"
                      :style="{ color: themeColors.NEUTRAL }"
                    />
                  </template>
                </div>
              </ClpmDataCanvas>

              <!-- ③ 证据区（可折叠，技术细节） -->
              <ClpmDataCanvas
                title="诊断证据"
                description="特征值表、时序波形与 PV-OP 散点图，按需展开。"
              >
                <!-- 3a. 特征值表（带工程含义列，默认折叠） -->
                <div class="evidence-section">
                  <div
                    class="evidence-header"
                    role="button"
                    tabindex="0"
                    :aria-expanded="!evidenceCollapsed"
                    @click="evidenceCollapsed = !evidenceCollapsed"
                    @keydown.enter="evidenceCollapsed = !evidenceCollapsed"
                    @keydown.space.prevent="
                      evidenceCollapsed = !evidenceCollapsed
                    "
                  >
                    <IconifyIcon
                      :icon="
                        evidenceCollapsed
                          ? 'ant-design:right-outlined'
                          : 'ant-design:down-outlined'
                      "
                      :size="14"
                    />
                    <span class="font-medium">
                      特征值表（{{ featureTableData.length }} 项）
                    </span>
                    <span
                      class="ml-auto text-xs"
                      :style="{ color: themeColors.NEUTRAL }"
                    >
                      名称 / 值 / 阈值 / 工程含义
                    </span>
                  </div>
                  <div v-show="!evidenceCollapsed" class="mt-3">
                    <Table
                      v-if="featureTableData.length > 0"
                      :columns="featureColumns"
                      :data-source="featureTableData"
                      :pagination="false"
                      size="small"
                      :scroll="{ y: 320 }"
                    />
                    <div
                      v-else
                      class="py-4 text-center"
                      :style="{ color: themeColors.NEUTRAL }"
                    >
                      暂无特征值
                    </div>
                  </div>
                </div>

                <!-- 3b. 时序波形与散点图（默认展开，保证 ECharts 首屏渲染） -->
                <div class="evidence-section mt-4">
                  <div
                    class="evidence-header"
                    role="button"
                    tabindex="0"
                    :aria-expanded="!waveformCollapsed"
                    @click="waveformCollapsed = !waveformCollapsed"
                    @keydown.enter="waveformCollapsed = !waveformCollapsed"
                    @keydown.space.prevent="
                      waveformCollapsed = !waveformCollapsed
                    "
                  >
                    <IconifyIcon
                      :icon="
                        waveformCollapsed
                          ? 'ant-design:right-outlined'
                          : 'ant-design:down-outlined'
                      "
                      :size="14"
                    />
                    <span class="font-medium">时序波形与 PV-OP 散点图</span>
                    <Button
                      v-if="selectedTime && !waveformCollapsed"
                      type="link"
                      size="small"
                      class="ml-auto"
                      @click.stop="clearSelection"
                    >
                      清除联动
                    </Button>
                  </div>
                  <div v-show="!waveformCollapsed" class="mt-3 space-y-4">
                    <div v-if="selectedTime" class="clpm-linkage-bar">
                      <IconifyIcon icon="ant-design:link-outlined" />
                      <span>
                        联动已激活：选中时间
                        {{ formatSelectedTime(selectedTime.timestamp) }}
                      </span>
                    </div>

                    <ClpmDataCanvas title="时序波形" :loading="waveformLoading">
                      <WaveformChart
                        v-if="waveform"
                        :trend="waveform"
                        :enable-time-select="true"
                        :selected-timestamp="selectedTimestamp"
                        height="320px"
                        @time-select="onTrendTimeSelect"
                      />
                      <div
                        v-else
                        class="py-12 text-center"
                        :style="{ color: themeColors.NEUTRAL }"
                      >
                        暂无波形数据
                      </div>
                    </ClpmDataCanvas>

                    <ClpmDataCanvas title="PV-OP 散点图" class="mt-4">
                      <EchartsUI ref="scatterChartRef" height="320px" />
                    </ClpmDataCanvas>

                    <div v-if="detail" class="mt-4 space-y-3">
                      <div v-if="detail.evidenceChain?.reasoning">
                        <div class="mb-2 font-medium">推理过程</div>
                        <div
                          class="rounded border p-3 text-sm"
                          :style="{ background: 'hsl(var(--muted) / 42%)' }"
                        >
                          {{ detail.evidenceChain.reasoning }}
                        </div>
                      </div>
                      <div
                        v-else
                        class="py-4 text-center"
                        :style="{ color: themeColors.NEUTRAL }"
                      >
                        暂无推理过程
                      </div>
                    </div>
                  </div>
                </div>
              </ClpmDataCanvas>

              <!-- 推荐动作 -->
              <Recommendations
                :recommendations="recommendations"
                :loading="recommendationsLoading"
                adoptable
                @adopt="handleAdoptRecommendation"
              />
            </div>
          </Tabs.TabPane>

          <!-- Tab 2: 处置时间线（P1a新增） -->
          <Tabs.TabPane key="timeline" tab="处置时间线">
            <ClpmDataCanvas
              title="异常处置时间线"
              description="从异常发现、认领、诊断、整定、实施到验证的全链路记录"
              :loading="timelineLoading"
            >
              <!-- 当前状态卡片 -->
              <div v-if="trackerItem" class="mb-6">
                <div
                  class="flex items-center justify-between rounded-lg border p-4"
                  :style="{
                    borderColor: `${trackerStatusColor}40`,
                    background: `${trackerStatusColor}08`,
                  }"
                >
                  <div class="flex items-center gap-4">
                    <div
                      class="flex h-12 w-12 items-center justify-center rounded-full"
                      :style="{
                        background: `${trackerStatusColor}20`,
                        color: trackerStatusColor,
                      }"
                    >
                      <IconifyIcon
                        :icon="
                          trackerItem.actionStatus === 'CLOSED'
                            ? 'ant-design:check-circle-filled'
                            : trackerItem.actionStatus === 'VERIFYING' ||
                                trackerItem.actionStatus === 'IMPLEMENTED'
                              ? 'ant-design:clock-circle-filled'
                              : trackerItem.actionStatus === 'IGNORED'
                                ? 'ant-design:minus-circle-filled'
                                : 'ant-design:exclamation-circle-filled'
                        "
                        :size="24"
                      />
                    </div>
                    <div>
                      <div
                        class="text-sm"
                        :style="{ color: themeColors.NEUTRAL }"
                      >
                        当前状态
                      </div>
                      <div
                        class="text-xl font-semibold"
                        :style="{ color: trackerStatusColor }"
                      >
                        {{ trackerStatusTag.label }}
                      </div>
                      <div
                        v-if="trackerItem.updatedAt"
                        class="mt-1 text-xs"
                        :style="{ color: themeColors.NEUTRAL }"
                      >
                        最后更新：{{ formatTime(trackerItem.updatedAt) }}
                      </div>
                    </div>
                  </div>
                  <div class="flex items-center gap-2">
                    <template
                      v-for="action in availableActions"
                      :key="action.key"
                    >
                      <Popconfirm
                        v-if="action.key !== 'implement'"
                        :title="actionConfirmMap[action.key]?.title"
                        :ok-text="actionConfirmMap[action.key]?.okText"
                        :ok-type="
                          actionConfirmMap[action.key]?.danger
                            ? 'danger'
                            : 'primary'
                        "
                        cancel-text="取消"
                        @confirm="handleActionConfirm(action.key)"
                      >
                        <Button
                          :type="
                            action.variant === 'primary' ? 'primary' : 'default'
                          "
                          :danger="action.danger"
                          :loading="statusUpdating"
                          size="small"
                        >
                          {{ action.label }}
                        </Button>
                      </Popconfirm>
                      <Button
                        v-else
                        type="primary"
                        :loading="statusUpdating"
                        size="small"
                        @click="handleImplement"
                      >
                        {{ action.label }}
                      </Button>
                    </template>
                  </div>
                </div>

                <!-- PID参数展示（VERIFYING/IMPLEMENTED/CLOSED状态） -->
                <div
                  v-if="
                    trackerItem.newPidP != null ||
                    trackerItem.newPidI != null ||
                    trackerItem.newPidD != null
                  "
                  class="mt-4"
                >
                  <div class="mb-2 text-sm font-medium">实施PID参数</div>
                  <div class="grid grid-cols-3 gap-3">
                    <div class="rounded border p-3 text-center">
                      <div
                        class="text-xs"
                        :style="{ color: themeColors.NEUTRAL }"
                      >
                        比例增益 P
                      </div>
                      <div class="mt-1 text-lg font-medium clpm-num">
                        {{ trackerItem.newPidP?.toFixed(3) ?? '—' }}
                      </div>
                    </div>
                    <div class="rounded border p-3 text-center">
                      <div
                        class="text-xs"
                        :style="{ color: themeColors.NEUTRAL }"
                      >
                        积分时间 I (s)
                      </div>
                      <div class="mt-1 text-lg font-medium clpm-num">
                        {{ trackerItem.newPidI?.toFixed(1) ?? '—' }}
                      </div>
                    </div>
                    <div class="rounded border p-3 text-center">
                      <div
                        class="text-xs"
                        :style="{ color: themeColors.NEUTRAL }"
                      >
                        微分时间 D (s)
                      </div>
                      <div class="mt-1 text-lg font-medium clpm-num">
                        {{ trackerItem.newPidD?.toFixed(1) ?? '—' }}
                      </div>
                    </div>
                  </div>
                </div>
              </div>

              <!-- 未跟踪提示 -->
              <div
                v-else
                class="mb-6 flex flex-col items-center justify-center rounded-lg border-2 border-dashed p-8"
                :style="{ borderColor: `${themeColors.NEUTRAL}40` }"
              >
                <IconifyIcon
                  icon="ant-design:flag-outlined"
                  :size="48"
                  :style="{ color: `${themeColors.NEUTRAL}60` }"
                />
                <div class="mt-4 text-center">
                  <div class="font-medium">该回路尚未加入异常跟踪</div>
                  <div
                    class="mt-1 text-sm"
                    :style="{ color: themeColors.NEUTRAL }"
                  >
                    点击下方按钮加入跟踪，开始异常处置闭环流程
                  </div>
                </div>
                <Popconfirm
                  :title="actionConfirmMap.claim?.title"
                  :ok-text="actionConfirmMap.claim?.okText"
                  cancel-text="取消"
                  @confirm="handleClaim"
                >
                  <Button type="primary" class="mt-4" :loading="statusUpdating">
                    加入跟踪并认领
                  </Button>
                </Popconfirm>
              </div>

              <!-- 时间线组件 -->
              <ClpmDispositionTimeline
                v-if="timeline"
                :events="timeline.events"
                :current-status="timeline.currentStatus"
                :pending-verification-at="timeline.pendingVerificationAt"
                :loading="timelineLoading"
              />
            </ClpmDataCanvas>
          </Tabs.TabPane>

          <!-- Tab 3: 整定对比（预留） -->
          <Tabs.TabPane key="tuning" tab="整定对比">
            <ClpmDataCanvas
              title="整定参数对比"
              description="对比整定前后PID参数，一键跳转回路整定模块进行详细仿真"
            >
              <div class="flex flex-col items-center justify-center py-12">
                <IconifyIcon
                  icon="ant-design:sliders-outlined"
                  :size="64"
                  :style="{ color: `${themeColors.NEUTRAL}40` }"
                />
                <div class="mt-4 text-center">
                  <div class="font-medium">整定参数对比</div>
                  <div
                    class="mt-1 text-sm"
                    :style="{ color: themeColors.NEUTRAL }"
                  >
                    请先前往回路整定模块完成参数辨识和仿真
                  </div>
                </div>
                <Button
                  type="primary"
                  class="mt-4"
                  @click="
                    () =>
                      router.push({
                        path: '/tuning/workbench',
                        query: buildTuningContextQuery(),
                      })
                  "
                >
                  前往回路整定
                </Button>
              </div>
            </ClpmDataCanvas>
          </Tabs.TabPane>

          <!-- Tab 4: A/B验证（P2-02：验证计划可视化） -->
          <Tabs.TabPane key="ab-verify" tab="A/B验证">
            <ClpmDataCanvas
              title="A/B效果验证"
              description="对比实施前后KPI指标变化，验证整改效果"
            >
              <!-- 验证中：显示验证计划时间线 -->
              <template v-if="abVerifyPlan?.isVerifying">
                <Steps
                  :current="1"
                  :items="abVerifySteps"
                  direction="vertical"
                  size="small"
                />
                <div
                  class="mt-4 flex items-start gap-2 rounded border border-blue-200 bg-blue-50 p-3 text-sm"
                >
                  <IconifyIcon
                    icon="lucide:info"
                    :size="16"
                    class="mt-0.5 shrink-0 text-blue-500"
                  />
                  <div>
                    <div class="font-medium text-blue-700">
                      系统正在采集数据，请耐心等待
                    </div>
                    <div class="mt-1 text-blue-600">
                      数据采集完成后将自动生成 A/B 对比报告，对比实施前后的 KPI
                      指标变化。
                    </div>
                  </div>
                </div>
              </template>

              <!-- 已闭环：显示验证通过 -->
              <template v-else-if="abVerifyPlan?.isClosed">
                <div class="flex flex-col items-center justify-center py-12">
                  <IconifyIcon
                    icon="ant-design:check-circle-outlined"
                    :size="64"
                    :style="{ color: `${themeColors.SUCCESS}60` }"
                  />
                  <div class="mt-4 text-center">
                    <div class="font-medium">验证已通过</div>
                    <div
                      class="mt-1 text-sm"
                      :style="{ color: themeColors.NEUTRAL }"
                    >
                      该异常已闭环，A/B 对比结果已记录
                    </div>
                  </div>
                </div>
              </template>

              <!-- 待实施：显示空状态提示 -->
              <template v-else>
                <div class="flex flex-col items-center justify-center py-12">
                  <IconifyIcon
                    icon="ant-design:line-chart-outlined"
                    :size="64"
                    :style="{ color: `${themeColors.NEUTRAL}40` }"
                  />
                  <div class="mt-4 text-center">
                    <div class="font-medium">A/B对比验证</div>
                    <div
                      class="mt-1 text-sm"
                      :style="{ color: themeColors.NEUTRAL }"
                    >
                      标记参数已实施后，等待24小时数据采集即可查看对比结果
                    </div>
                  </div>
                </div>
              </template>
            </ClpmDataCanvas>
          </Tabs.TabPane>

          <!-- AI 洞察已迁移至工具栏 AI 图标触发的右抽屉（§5.2），原独立 Tab 移除 -->
        </Tabs>
      </div>
    </Spin>

    <!-- P1a: 实施记录弹窗 -->
    <ClpmImplementRecordModal
      v-model:visible="implementModalVisible"
      :loading="statusUpdating"
      :loop-id="loopId"
      @submit="handleImplementSubmit"
    />

    <!-- P3-02: 阈值微调弹窗 -->
    <ClpmThresholdTuneModal
      v-model:visible="thresholdTuneVisible"
      :loop-id="loopId"
      :tag-name="detail?.tagName"
    />

    <!-- AI 诊断洞察右抽屉（工具栏 AI 图标触发，§5.2） -->
    <ClpmAiDrawer
      v-model:open="aiDrawerOpen"
      scene="diagnosis"
      :loop-id="loopId"
    />
  </Page>
</template>

<style scoped>
.clpm-linkage-bar {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 6px 12px;
  margin-bottom: 12px;
  font-size: 13px;
  color: hsl(var(--primary));
  background: hsl(var(--primary) / 8%);
  border: 1px solid hsl(var(--primary) / 20%);
  border-radius: 4px;
}

/* ===== Phase C §4.3.2 ② 问题定位路径横向流程 ===== */
.problem-path {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: stretch;
  padding: 8px 0;
}

.path-node {
  display: flex;
  flex: 1;
  gap: 10px;
  align-items: flex-start;
  min-width: 180px;
  padding: 12px 14px;
  cursor: pointer;
  background: hsl(var(--muted) / 30%);
  border: 1px solid hsl(var(--border) / 60%);
  border-radius: 6px;
  transition:
    border-color 0.2s,
    background 0.2s;
}

.path-node:hover {
  background: hsl(var(--primary) / 6%);
  border-color: hsl(var(--primary) / 40%);
}

.path-node--active {
  background: hsl(var(--primary) / 8%);
  border-color: hsl(var(--primary));
}

.path-node-index {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  font-size: 12px;
  font-weight: 600;
  color: hsl(var(--primary-foreground));
  background: hsl(var(--primary));
  border-radius: 50%;
}

.path-node-body {
  flex: 1;
  min-width: 0;
}

.path-node-title {
  margin-bottom: 2px;
  font-size: 13px;
  font-weight: 600;
}

.path-node-desc {
  font-size: 12px;
  line-height: 1.5;
  word-break: break-all;
}

.path-arrow {
  flex-shrink: 0;
  align-self: center;
}

/* ===== Phase C §4.3.2 ③ 证据区折叠 ===== */
.evidence-section {
  overflow: hidden;
  border: 1px solid hsl(var(--border) / 60%);
  border-radius: 6px;
}

.evidence-header {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 10px 14px;
  cursor: pointer;
  background: hsl(var(--muted) / 30%);
  transition: background 0.2s;
}

.evidence-header:hover {
  background: hsl(var(--muted) / 50%);
}
</style>
