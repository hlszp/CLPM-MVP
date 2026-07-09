<script lang="ts" setup>
import type { TableColumnsType, TablePaginationConfig } from 'ant-design-vue';

/**
 * 回路性能列表页
 *
 * 对齐后端 GET /api/v1/performance/loops/snapshots
 * - 顶部工具栏：标题 + 刷新
 * - 筛选区：工厂模型节点 + 控制类型 + 评估状态
 * - 表格：回路编号 / 名称 / 类型 / 控制类型 / 控制方式 / 评估等级 / 综合评分 /
 *   准确率 / 快速率 / 平稳率 / 有效自控率 / 可信度 / 时间窗口 / 评估时间 /
 *   评估状态 / 操作（详情、历史、诊断）
 * - 详情抽屉：回路基本信息 + 8 大 KPI + 4 大诊断指标 + 可信度 + 时间窗 + 评估时间
 * - 历史 Modal：时间维度切换（8/12/24/72/168h） + ECharts 趋势图
 * - 诊断 Modal（90% 宽）：4 个 Tab（频谱分析 / 时域分析 / 诊断概览 / 评估历史）
 */
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { DiagnosisApi } from '#/api/diagnosis';
import type { LoopApi } from '#/api/loop';
import type {
  ConfidenceLevel,
  KpiSnapshotItem,
  KpiStatus,
  MetricApi,
} from '#/api/metric';
import type { PlantNodeApi } from '#/api/plant-node';

import {
  nextTick,
  onMounted,
  reactive,
  ref,
  shallowRef,
  watch,
} from 'vue';

import { Page } from '@vben/common-ui';
import { IconifyIcon, RotateCw } from '@vben/icons';
import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import {
  Badge,
  Button,
  Card,
  Col,
  Descriptions,
  DescriptionsItem,
  Drawer,
  message,
  Modal,
  RadioGroup,
  Row,
  Select,
  Spin,
  Table,
  Tabs,
  Tag,
  TreeSelect,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import { getDiagnosisVisualizationApi } from '#/api/diagnosis';
import { getLoopListApi } from '#/api/loop';
import {
  getGradingThresholdsApi,
  getLoopSnapshotsApi,
} from '#/api/metric';
import { getPlantNodeTreeApi } from '#/api/plant-node';
import {
  ClpmDataCanvas,
  ClpmPageToolbar,
} from '#/components/clpm';
import ChoudhuryCard from '#/components/diagnosis-visualization/choudhury-card.vue';
import CusumChart from '#/components/diagnosis-visualization/cusum-chart.vue';
import IaeCard from '#/components/diagnosis-visualization/iae-card.vue';
import KanoCard from '#/components/diagnosis-visualization/kano-card.vue';
import QualityTimelineChart from '#/components/diagnosis-visualization/quality-timeline-chart.vue';
import SaturationChart from '#/components/diagnosis-visualization/saturation-chart.vue';
import ScatterChart from '#/components/diagnosis-visualization/scatter-chart.vue';
import SlowResponseCard from '#/components/diagnosis-visualization/slow-response-card.vue';
import SpectrumChart from '#/components/diagnosis-visualization/spectrum-chart.vue';
import StepResponseChart from '#/components/diagnosis-visualization/step-response-chart.vue';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import {
  DIAGNOSIS_LABEL_COLOR_MAP,
  DIAGNOSIS_LABEL_NAME_MAP,
} from '#/constants/diagnosis';

defineOptions({ name: 'MetricLoopPerformance' });

const { isDark, themeColors } = useClpmTheme();

// ===== 常量映射 =====

/** 回路类型映射（label + color） */
const LOOP_TYPE_MAP: Record<string, { color: string; label: string }> = {
  TEMPERATURE: { label: '温度', color: '#FCA5A5' },
  PRESSURE: { label: '压力', color: '#93C5FD' },
  LEVEL: { label: '液位', color: '#86EFAC' },
  FLOW: { label: '流量', color: '#67E8F9' },
  ANALYSIS: { label: '分析', color: '#D8B4FE' },
  SPEED: { label: '速度', color: '#FDBA74' },
  OTHER: { label: '其他', color: '#CBD5E1' },
};

/** 控制类型映射 */
const CONTROL_TYPE_MAP: Record<string, string> = {
  STABLE: '稳定型',
  SLOW: '慢速型',
  FAST: '快速型',
  LOGIC: '逻辑型',
};

/** 控制方式颜色映射 */
function controlModeColor(mode?: string): string {
  if (mode === 'Auto') return '#10B981';
  if (mode === 'Manual') return '#F59E0B';
  if (mode === 'Cascade') return '#3B82F6';
  return '#CBD5E1';
}

/** 评估状态映射 */
const STATUS_COLOR_MAP: Record<string, string> = {
  SUCCESS: 'success',
  PARTIAL: 'warning',
  INCONCLUSIVE: 'default',
};

const STATUS_LABEL_MAP: Record<string, string> = {
  SUCCESS: '成功',
  INCONCLUSIVE: '不确定',
  PARTIAL: '部分',
};

/** 可信度徽章颜色 */
const CONFIDENCE_COLOR_MAP: Record<string, string> = {
  A: 'green',
  B: 'blue',
  C: 'gold',
  D: 'orange',
  E: 'red',
};

const CONFIDENCE_LABEL_MAP: Record<string, string> = {
  A: 'A 优秀',
  B: 'B 良好',
  C: 'C 一般',
  D: 'D 较差',
  E: 'E 不足',
};

/** 评估等级颜色（一级~五级） */
const GRADE_COLOR_MAP: Record<number, string> = {
  1: 'green',
  2: 'blue',
  3: 'gold',
  4: 'orange',
  5: 'red',
};

const GRADE_LABEL_MAP: Record<number, string> = {
  1: '一级',
  2: '二级',
  3: '三级',
  4: '四级',
  5: '五级',
};

/** 控制类型筛选选项 */
const controlTypeOptions = [
  { label: '全部', value: undefined },
  { label: '稳定型', value: 'STABLE' },
  { label: '慢速型', value: 'SLOW' },
  { label: '快速型', value: 'FAST' },
  { label: '逻辑型', value: 'LOGIC' },
];

/** 评估状态筛选选项 */
const statusOptions = [
  { label: '全部', value: undefined },
  { label: '成功', value: 'SUCCESS' },
  { label: '不确定', value: 'INCONCLUSIVE' },
  { label: '部分', value: 'PARTIAL' },
];

/** 历史趋势时间窗选项（小时） */
const historyWindowOptions = [
  { label: '8小时', value: 8 },
  { label: '12小时', value: 12 },
  { label: '24小时', value: 24 },
  { label: '72小时', value: 72 },
  { label: '168小时', value: 168 },
];

// ===== 合并行类型：快照 + 回路元数据 =====

interface LoopPerformanceRow extends KpiSnapshotItem {
  /** 关联的回路元数据（来自 loops 列表） */
  loopMeta?: LoopApi.LoopListItem;
  /** 回路描述（来自 loopMeta.description） */
  description?: string;
  /** 回路类型（来自 loopMeta.loopType） */
  loopType?: string;
  /** 控制类型（来自 loopMeta.controlType） */
  controlType?: string;
  /** 控制方式（来自 loopMeta.controlMode） */
  controlMode?: string;
}

// ===== 列表状态 =====

const loading = ref(false);
const loadError = ref(false);
const rows = ref<LoopPerformanceRow[]>([]);
const total = ref(0);
const query = reactive({
  plantNodeId: undefined as string | undefined,
  controlType: undefined as string | undefined,
  status: undefined as KpiStatus | undefined,
  page: 1,
  pageSize: 20,
});

/** 回路元数据 Map（loopId → LoopListItem） */
const loopMap = shallowRef<Map<string, LoopApi.LoopListItem>>(new Map());

/** 工厂节点树（保留层级结构供 TreeSelect 使用） */
const plantNodeTree = ref<PlantNodeApi.PlantNode[]>([]);

// ===== 定级阈值 =====

const gradingThresholds = ref<MetricApi.GradingThresholdItem[]>([]);

/** 根据综合评分判定评估等级（1~5） */
function getGrade(score: null | number | undefined): null | number {
  if (score === null || score === undefined) return null;
  if (gradingThresholds.value.length === 0) {
    // 默认阈值兜底
    if (score >= 90) return 1;
    if (score >= 80) return 2;
    if (score >= 70) return 3;
    if (score >= 60) return 4;
    return 5;
  }
  for (const t of gradingThresholds.value) {
    if (score >= t.minScore && score <= t.maxScore) {
      return t.level;
    }
  }
  // 兜底：取最低级
  return 5;
}

// ===== 表格列定义 =====

const columns: TableColumnsType = [
  {
    title: '回路编号',
    key: 'loopTagName',
    dataIndex: 'loopTagName',
    width: 160,
    fixed: 'left',
    ellipsis: true,
  },
  {
    title: '回路名称',
    key: 'description',
    dataIndex: 'description',
    width: 160,
    ellipsis: true,
  },
  {
    title: '回路类型',
    key: 'loopType',
    width: 90,
  },
  {
    title: '控制类型',
    key: 'controlType',
    width: 90,
  },
  {
    title: '控制方式',
    key: 'controlMode',
    width: 90,
  },
  {
    title: '评估等级',
    key: 'grade',
    width: 90,
  },
  {
    title: '综合评分',
    key: 'score',
    dataIndex: 'score',
    width: 90,
    sorter: true,
  },
  {
    title: '准确率',
    key: 'accuracyRate',
    dataIndex: 'accuracyRate',
    width: 80,
  },
  {
    title: '快速率',
    key: 'fastRate',
    dataIndex: 'fastRate',
    width: 80,
  },
  {
    title: '平稳率',
    key: 'steadyRate',
    dataIndex: 'steadyRate',
    width: 80,
  },
  {
    title: '有效自控率',
    key: 'effectiveAutoRate',
    dataIndex: 'effectiveAutoRate',
    width: 100,
  },
  {
    title: '可信度',
    key: 'confidenceLevel',
    dataIndex: 'confidenceLevel',
    width: 90,
  },
  {
    title: '时间窗口',
    key: 'tsRange',
    width: 140,
  },
  {
    title: '评估时间',
    key: 'tsEnd',
    dataIndex: 'tsEnd',
    width: 140,
  },
  {
    title: '评估状态',
    key: 'status',
    dataIndex: 'status',
    width: 90,
  },
  {
    title: '操作',
    key: 'action',
    width: 200,
    fixed: 'right' as const,
  },
];

// ===== 工具函数 =====

/** 时间字符串规范化（PostgreSQL timestamp without timezone 假定为 UTC） */
function normalizeTime(ts: null | string | undefined): null | string {
  if (!ts) return null;
  const hasTimezone = /[zZ]$|[+-]\d{2}:?\d{2}$/.test(ts);
  return hasTimezone ? ts : `${ts}Z`;
}

function formatTsRange(start: null | string, end: null | string): string {
  const s = normalizeTime(start);
  const e = normalizeTime(end);
  if (!s && !e) return '—';
  const fmt = 'MM-DD HH:mm';
  if (s && e) {
    return `${dayjs(s).format(fmt)} ~ ${dayjs(e).format(fmt)}`;
  }
  return dayjs(e || s).format(fmt);
}

function formatTime(ts: null | string | undefined): string {
  const n = normalizeTime(ts);
  if (!n) return '—';
  return dayjs(n).format('YYYY-MM-DD HH:mm:ss');
}

function formatShortTime(ts: null | string | undefined): string {
  const n = normalizeTime(ts);
  if (!n) return '—';
  return dayjs(n).format('MM-DD HH:mm');
}

function formatNumber(val: null | number | undefined, suffix = ''): string {
  if (val === null || val === undefined) return '—';
  return `${val.toFixed(2)}${suffix}`;
}

/** 综合评分颜色 */
function scoreColor(val: null | number | undefined): string {
  if (val === null || val === undefined) return '#9CA3AF';
  if (val >= 80) return themeColors.value.SUCCESS;
  if (val >= 60) return themeColors.value.WARNING;
  return themeColors.value.DANGER;
}

// ===== 数据加载 =====

/** 加载工厂节点 */
async function loadPlantNodes() {
  try {
    const tree = await getPlantNodeTreeApi();
    plantNodeTree.value = tree || [];
  } catch {
    plantNodeTree.value = [];
  }
}

/** 加载回路列表（构建 loopId → LoopListItem 映射） */
async function loadLoopMap() {
  try {
    const allLoops: LoopApi.LoopListItem[] = [];
    let page = 1;
    const loopPageSize = 100;
    let totalLoops = 0;
    do {
      const result = await getLoopListApi({
        page,
        pageSize: loopPageSize,
      });
      totalLoops = result.total;
      allLoops.push(...(result.items || []));
      page += 1;
    } while ((page - 1) * loopPageSize < totalLoops);
    const map = new Map<string, LoopApi.LoopListItem>();
    for (const l of allLoops) {
      map.set(l.loopId, l);
    }
    loopMap.value = map;
  } catch {
    loopMap.value = new Map();
  }
}

/** 加载定级阈值 */
async function loadGradingThresholds() {
  try {
    const result = await getGradingThresholdsApi();
    gradingThresholds.value = result.thresholds || [];
  } catch {
    gradingThresholds.value = [];
  }
}

/** 加载列表数据（合并快照 + 回路元数据） */
async function loadList() {
  loading.value = true;
  loadError.value = false;
  try {
    const params: Record<string, unknown> = {
      page: query.page,
      pageSize: query.pageSize,
    };
    if (query.plantNodeId) params.plantNodeId = query.plantNodeId;
    if (query.status) params.status = query.status;

    // 按控制类型筛选：先在 loopMap 中找到匹配的 loopId，再传给快照接口
    if (query.controlType) {
      const matchedIds: string[] = [];
      for (const [id, loop] of loopMap.value.entries()) {
        if (loop.controlType === query.controlType) {
          matchedIds.push(id);
        }
      }
      if (matchedIds.length === 0) {
        rows.value = [];
        total.value = 0;
        return;
      }
      params.loopId = matchedIds.join(',');
    }

    const result = await getLoopSnapshotsApi(params as any);
    rows.value = (result.items || []).map((snap) => {
      const meta = snap.loopId ? loopMap.value.get(snap.loopId) : undefined;
      return {
        ...snap,
        loopMeta: meta,
        description: meta?.description,
        loopType: meta?.loopType,
        controlType: meta?.controlType,
        controlMode: meta?.controlMode,
      };
    });
    total.value = result.total;
  } catch (error: any) {
    loadError.value = true;
    console.error('加载回路性能列表失败:', error);
    message.error(error?.message || '加载失败');
  } finally {
    loading.value = false;
  }
}

function handleSearch() {
  query.page = 1;
  loadList();
}

function handleTableChange(pagination: TablePaginationConfig) {
  query.page = pagination.current || 1;
  query.pageSize = pagination.pageSize || 20;
  loadList();
}

// ===== 详情抽屉 =====

const drawerVisible = ref(false);
const drawerRecord = ref<LoopPerformanceRow | null>(null);

function openDetail(record: LoopPerformanceRow) {
  drawerRecord.value = record;
  drawerVisible.value = true;
}

function closeDetail() {
  drawerVisible.value = false;
  drawerRecord.value = null;
}

// ===== 历史 Modal =====

const historyModalVisible = ref(false);
const historyLoading = ref(false);
const historyRecord = ref<LoopPerformanceRow | null>(null);
const historyWindow = ref<number>(24);
const historySnapshots = ref<KpiSnapshotItem[]>([]);
const historyChartRef = ref<EchartsUIType>();
const { renderEcharts: renderHistoryChart } = useEcharts(historyChartRef);

async function openHistory(record: LoopPerformanceRow) {
  historyRecord.value = record;
  historyModalVisible.value = true;
  historyWindow.value = 24;
  await loadHistoryData();
}

async function loadHistoryData() {
  if (!historyRecord.value?.loopId) {
    message.warning('该记录缺少回路 ID，无法查询历史');
    return;
  }
  historyLoading.value = true;
  try {
    const endTime = dayjs();
    const startTime = endTime.subtract(historyWindow.value, 'hour');
    // 后端 pageSize 上限 100，循环分页拉取时间窗内全部快照
    const allItems: KpiSnapshotItem[] = [];
    let page = 1;
    // eslint-disable-next-line @typescript-eslint/no-magic-numbers
    const pageLimit = 100;
    let total = 0;
    do {
      const result = await getLoopSnapshotsApi({
        loopId: historyRecord.value.loopId,
        startTime: startTime.toISOString(),
        endTime: endTime.toISOString(),
        page,
        pageSize: pageLimit,
      });
      allItems.push(...(result.items || []));
      total = result.total ?? 0;
      page += 1;
    } while ((page - 1) * pageLimit < total);
    historySnapshots.value = allItems.sort((a, b) => {
      const aTs = normalizeTime(a.tsStart) || '';
      const bTs = normalizeTime(b.tsStart) || '';
      return aTs.localeCompare(bTs);
    });
    await nextTick();
    renderHistoryTrend();
  } catch (error: any) {
    console.error('加载历史趋势失败:', error);
    message.error(error?.message || '加载历史趋势失败');
  } finally {
    historyLoading.value = false;
  }
}

/** 渲染历史趋势图：综合评分(柱) + 准确率/快速率/平稳率/有效自控率(线) */
function renderHistoryTrend() {
  const data = historySnapshots.value;
  if (data.length === 0) return;

  const xLabels = data.map((s) => {
    const t = normalizeTime(s.tsStart) || normalizeTime(s.tsEnd);
    return t ? dayjs(t).format('MM-DD HH:mm') : '';
  });

  const scores = data.map((s) => s.score ?? null);
  const accuracy = data.map((s) => s.accuracyRate ?? null);
  const fast = data.map((s) => s.fastRate ?? null);
  const steady = data.map((s) => s.steadyRate ?? null);
  const effectiveAuto = data.map((s) => s.effectiveAutoRate ?? null);

  const textColor = themeColors.value.NEUTRAL;

  renderHistoryChart({
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
    },
    legend: {
      data: ['综合评分', '准确率', '快速率', '平稳率', '有效自控率'],
      top: 0,
      textStyle: { color: textColor, fontSize: 12 },
    },
    grid: { top: 40, right: 24, bottom: 40, left: 48, containLabel: true },
    xAxis: {
      type: 'category',
      data: xLabels,
      axisLabel: { color: textColor, fontSize: 11, hideOverlap: true },
    },
    yAxis: {
      type: 'value',
      min: 0,
      max: 100,
      axisLabel: { color: textColor, fontSize: 11 },
      splitLine: { lineStyle: { color: isDark.value ? 'rgba(255,255,255,0.08)' : '#E5E5E5' } },
    },
    series: [
      {
        name: '综合评分',
        type: 'bar',
        data: scores,
        itemStyle: { color: themeColors.value.INFO },
        barWidth: '40%',
      },
      {
        name: '准确率',
        type: 'line',
        data: accuracy,
        smooth: true,
        symbol: 'circle',
        symbolSize: 4,
        lineStyle: { color: themeColors.value.SUCCESS, width: 2 },
        itemStyle: { color: themeColors.value.SUCCESS },
      },
      {
        name: '快速率',
        type: 'line',
        data: fast,
        smooth: true,
        symbol: 'circle',
        symbolSize: 4,
        lineStyle: { color: themeColors.value.WARNING, width: 2 },
        itemStyle: { color: themeColors.value.WARNING },
      },
      {
        name: '平稳率',
        type: 'line',
        data: steady,
        smooth: true,
        symbol: 'circle',
        symbolSize: 4,
        lineStyle: { color: themeColors.value.ACCENT, width: 2 },
        itemStyle: { color: themeColors.value.ACCENT },
      },
      {
        name: '有效自控率',
        type: 'line',
        data: effectiveAuto,
        smooth: true,
        symbol: 'circle',
        symbolSize: 4,
        lineStyle: { color: themeColors.value.DANGER, width: 2 },
        itemStyle: { color: themeColors.value.DANGER },
      },
    ],
  });
}

function handleHistoryWindowChange() {
  loadHistoryData();
}

// ===== 诊断 Modal =====

const diagModalVisible = ref(false);
const diagLoading = ref(false);
const diagRecord = ref<LoopPerformanceRow | null>(null);
const diagData = ref<DiagnosisApi.DiagnosisVisualizationData | null>(null);
const diagActiveTab = ref<'history' | 'overview' | 'spectrum' | 'time'>(
  'overview',
);

async function openDiagnosis(record: LoopPerformanceRow) {
  diagRecord.value = record;
  diagModalVisible.value = true;
  diagActiveTab.value = 'overview';
  diagData.value = null;
  if (!record.loopId) {
    message.warning('该记录缺少回路 ID，无法获取诊断数据');
    return;
  }
  diagLoading.value = true;
  try {
    diagData.value = await getDiagnosisVisualizationApi(record.loopId);
  } catch (error: any) {
    console.error('加载诊断可视化数据失败:', error);
    message.error(error?.message || '加载诊断可视化数据失败');
  } finally {
    diagLoading.value = false;
  }
}

// ===== 诊断 Modal - 评估历史 Tab（简化版 snapshots 表格） =====

const diagHistoryLoading = ref(false);
const diagHistorySnapshots = ref<KpiSnapshotItem[]>([]);
const diagHistoryTotal = ref(0);
const diagHistoryPage = ref(1);
const diagHistoryPageSize = ref(10);
const diagHistoryStatus = ref<KpiStatus | undefined>();
const diagHistoryConfidence = ref<ConfidenceLevel | undefined>();

async function loadDiagHistory() {
  if (!diagRecord.value?.loopId) return;
  diagHistoryLoading.value = true;
  try {
    const params: Record<string, unknown> = {
      loopId: diagRecord.value.loopId,
      page: diagHistoryPage.value,
      pageSize: diagHistoryPageSize.value,
    };
    if (diagHistoryStatus.value) params.status = diagHistoryStatus.value;
    if (diagHistoryConfidence.value)
      params.confidenceLevel = diagHistoryConfidence.value;
    const result = await getLoopSnapshotsApi(params as any);
    diagHistorySnapshots.value = result.items || [];
    diagHistoryTotal.value = result.total;
  } catch {
    diagHistorySnapshots.value = [];
    diagHistoryTotal.value = 0;
  } finally {
    diagHistoryLoading.value = false;
  }
}

const diagHistoryColumns: TableColumnsType = [
  {
    title: '时间窗',
    key: 'tsRange',
    width: 140,
  },
  {
    title: '综合评分',
    key: 'score',
    dataIndex: 'score',
    width: 90,
  },
  {
    title: '准确率',
    key: 'accuracyRate',
    dataIndex: 'accuracyRate',
    width: 80,
  },
  {
    title: '快速率',
    key: 'fastRate',
    dataIndex: 'fastRate',
    width: 80,
  },
  {
    title: '平稳率',
    key: 'steadyRate',
    dataIndex: 'steadyRate',
    width: 80,
  },
  {
    title: '有效自控率',
    key: 'effectiveAutoRate',
    dataIndex: 'effectiveAutoRate',
    width: 100,
  },
  {
    title: '可信度',
    key: 'confidenceLevel',
    dataIndex: 'confidenceLevel',
    width: 80,
  },
  {
    title: '状态',
    key: 'status',
    dataIndex: 'status',
    width: 90,
  },
];

function handleDiagHistoryTabChange() {
  if (diagActiveTab.value === 'history' && diagHistorySnapshots.value.length === 0) {
    loadDiagHistory();
  }
}

function handleDiagHistoryTableChange(p: TablePaginationConfig) {
  diagHistoryPage.value = p.current || 1;
  diagHistoryPageSize.value = p.pageSize || 10;
  loadDiagHistory();
}

// ===== 主题切换重渲图表 =====

watch(isDark, () => {
  nextTick(() => {
    if (historyModalVisible.value) renderHistoryTrend();
  });
});

// ===== 生命周期 =====

onMounted(async () => {
  await Promise.all([loadPlantNodes(), loadLoopMap(), loadGradingThresholds()]);
  loadList();
});
</script>

<template>
  <Page>
    <!-- 顶部工具栏 -->
    <ClpmPageToolbar
      title="回路性能"
      subtitle="按回路展示 KPI 评估结果，支持详情查看、历史趋势与诊断可视化。"
      :loading="loading"
    >
      <template #actions>
        <Button @click="loadList">
          <template #icon><RotateCw /></template>
          刷新
        </Button>
      </template>
    </ClpmPageToolbar>

    <!-- 筛选区 -->
    <div class="mb-4 mt-3 flex flex-wrap items-center gap-3">
      <TreeSelect
        v-model:value="query.plantNodeId"
        :tree-data="plantNodeTree"
        :field-names="{ label: 'name', value: 'id', children: 'children' }"
        placeholder="工厂模型节点"
        allow-clear
        tree-default-expand-all
        style="width: 220px"
        @change="handleSearch"
      />
      <Select
        v-model:value="query.controlType"
        :options="controlTypeOptions"
        placeholder="控制类型"
        allow-clear
        style="width: 140px"
        @change="handleSearch"
      />
      <Select
        v-model:value="query.status"
        :options="statusOptions"
        placeholder="评估状态"
        allow-clear
        style="width: 140px"
        @change="handleSearch"
      />
      <Button type="primary" @click="handleSearch">查询</Button>
    </div>

    <!-- 回路性能列表 -->
    <ClpmDataCanvas
      :loading="loading"
      :error="loadError"
      :empty="!loading && !loadError && rows.length === 0"
      @retry="loadList"
    >
      <Table
        :columns="columns"
        :data-source="rows"
        :loading="loading"
        :pagination="{
          current: query.page,
          pageSize: query.pageSize,
          total,
          showSizeChanger: true,
          pageSizeOptions: ['20', '50', '100'],
          showTotal: (t: number) => `共 ${t} 条`,
        }"
        :row-key="(record: LoopPerformanceRow) => `${record.loopId}-${record.tsStart}`"
        :scroll="{ x: 1800 }"
        size="small"
        @change="handleTableChange"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'loopType'">
            <Tag
              v-if="(record as LoopPerformanceRow).loopType"
              :color="
                LOOP_TYPE_MAP[(record as LoopPerformanceRow).loopType ?? 'OTHER']
                  ?.color ?? 'default'
              "
              class="m-0"
            >
              {{
                LOOP_TYPE_MAP[(record as LoopPerformanceRow).loopType ?? 'OTHER']
                  ?.label ?? '其他'
              }}
            </Tag>
            <span v-else class="text-gray-400">—</span>
          </template>
          <template v-else-if="column.key === 'controlType'">
            <span v-if="(record as LoopPerformanceRow).controlType">
              {{
                CONTROL_TYPE_MAP[(record as LoopPerformanceRow).controlType!] ??
                (record as LoopPerformanceRow).controlType
              }}
            </span>
            <span v-else class="text-gray-400">—</span>
          </template>
          <template v-else-if="column.key === 'controlMode'">
            <Tag
              v-if="(record as LoopPerformanceRow).controlMode"
              :color="controlModeColor((record as LoopPerformanceRow).controlMode)"
              class="m-0"
            >
              {{ (record as LoopPerformanceRow).controlMode }}
            </Tag>
            <span v-else class="text-gray-400">—</span>
          </template>
          <template v-else-if="column.key === 'grade'">
            <Tag
              v-if="getGrade((record as LoopPerformanceRow).score)"
              :color="
                GRADE_COLOR_MAP[getGrade((record as LoopPerformanceRow).score)!]
              "
              class="m-0"
            >
              {{
                GRADE_LABEL_MAP[getGrade((record as LoopPerformanceRow).score)!]
              }}
            </Tag>
            <span v-else class="text-gray-400">—</span>
          </template>
          <template v-else-if="column.key === 'score'">
            <span
              class="font-semibold"
              :style="{ color: scoreColor((record as LoopPerformanceRow).score) }"
            >
              {{ formatNumber((record as LoopPerformanceRow).score) }}
            </span>
          </template>
          <template
            v-else-if="
              ([
                'accuracyRate',
                'fastRate',
                'steadyRate',
                'effectiveAutoRate',
              ] as string[]).includes(column.key as string)
            "
          >
            <span class="font-mono text-xs">
              {{
                formatNumber(
                  (record as Record<string, unknown>)[
                    column.dataIndex as string
                  ] as number | null | undefined,
                  '%',
                )
              }}
            </span>
          </template>
          <template v-else-if="column.key === 'confidenceLevel'">
            <Badge
              v-if="(record as LoopPerformanceRow).confidenceLevel"
              :color="
                CONFIDENCE_COLOR_MAP[(record as LoopPerformanceRow).confidenceLevel!]
              "
              :text="
                CONFIDENCE_LABEL_MAP[(record as LoopPerformanceRow).confidenceLevel!]
              "
            />
            <span v-else class="text-gray-400">—</span>
          </template>
          <template v-else-if="column.key === 'tsRange'">
            <span class="font-mono text-xs">
              {{
                formatTsRange(
                  (record as LoopPerformanceRow).tsStart,
                  (record as LoopPerformanceRow).tsEnd,
                )
              }}
            </span>
          </template>
          <template v-else-if="column.key === 'tsEnd'">
            <span class="font-mono text-xs">
              {{ formatShortTime((record as LoopPerformanceRow).tsEnd) }}
            </span>
          </template>
          <template v-else-if="column.key === 'status'">
            <Tag
              :color="
                STATUS_COLOR_MAP[(record as LoopPerformanceRow).status] || 'default'
              "
              class="m-0"
            >
              {{
                STATUS_LABEL_MAP[(record as LoopPerformanceRow).status] ||
                (record as LoopPerformanceRow).status
              }}
            </Tag>
          </template>
          <template v-else-if="column.key === 'action'">
            <div class="flex items-center gap-1 whitespace-nowrap">
              <Button
                type="link"
                size="small"
                @click="openDetail(record as LoopPerformanceRow)"
              >
                <template #icon>
                  <IconifyIcon icon="ant-design:info-circle-outlined" />
                </template>
                详情
              </Button>
              <Button
                type="link"
                size="small"
                @click="openHistory(record as LoopPerformanceRow)"
              >
                <template #icon>
                  <IconifyIcon icon="ant-design:history-outlined" />
                </template>
                历史
              </Button>
              <Button
                type="link"
                size="small"
                @click="openDiagnosis(record as LoopPerformanceRow)"
              >
                <template #icon>
                  <IconifyIcon icon="ant-design:stethoscope-outlined" />
                </template>
                诊断
              </Button>
            </div>
          </template>
        </template>
      </Table>
    </ClpmDataCanvas>

    <!-- 详情抽屉 -->
    <Drawer
      :open="drawerVisible"
      title="回路性能详情"
      placement="right"
      :width="720"
      :mask-closable="true"
      @close="closeDetail"
    >
      <template v-if="drawerRecord">
        <!-- 回路基本信息 -->
        <div class="mb-2 text-sm font-medium">回路基本信息</div>
        <Descriptions
          :column="2"
          size="small"
          bordered
          :label-style="{ width: '120px' }"
        >
          <DescriptionsItem label="回路编号">
            {{ drawerRecord.loopTagName || '—' }}
          </DescriptionsItem>
          <DescriptionsItem label="回路名称">
            {{ drawerRecord.description || '—' }}
          </DescriptionsItem>
          <DescriptionsItem label="回路类型">
            {{
              LOOP_TYPE_MAP[drawerRecord.loopType ?? 'OTHER']?.label ?? '—'
            }}
          </DescriptionsItem>
          <DescriptionsItem label="控制类型">
            {{
              drawerRecord.controlType
                ? (CONTROL_TYPE_MAP[drawerRecord.controlType] ??
                  drawerRecord.controlType)
                : '—'
            }}
          </DescriptionsItem>
          <DescriptionsItem label="控制方式">
            <Tag
              v-if="drawerRecord.controlMode"
              :color="controlModeColor(drawerRecord.controlMode)"
            >
              {{ drawerRecord.controlMode }}
            </Tag>
            <span v-else>—</span>
          </DescriptionsItem>
          <DescriptionsItem label="评估等级">
            <Tag
              v-if="getGrade(drawerRecord.score)"
              :color="GRADE_COLOR_MAP[getGrade(drawerRecord.score)!]"
            >
              {{ GRADE_LABEL_MAP[getGrade(drawerRecord.score)!] }}
            </Tag>
            <span v-else>—</span>
          </DescriptionsItem>
          <DescriptionsItem label="PV 量程">
            {{
              drawerRecord.loopMeta?.pvRange
                ? `${drawerRecord.loopMeta.pvRange.min ?? '—'} ~ ${
                    drawerRecord.loopMeta.pvRange.max ?? '—'
                  }${drawerRecord.loopMeta.pvUnit ? ` ${ drawerRecord.loopMeta.pvUnit}` : ''}`
                : '—'
            }}
          </DescriptionsItem>
          <DescriptionsItem label="OP 量程">
            {{
              drawerRecord.loopMeta?.opRange
                ? `${drawerRecord.loopMeta.opRange.min ?? '—'} ~ ${
                    drawerRecord.loopMeta.opRange.max ?? '—'
                  }${drawerRecord.loopMeta.opUnit ? ` ${ drawerRecord.loopMeta.opUnit}` : ''}`
                : '—'
            }}
          </DescriptionsItem>
        </Descriptions>

        <!-- 8 大性能评估 KPI -->
        <div class="mb-2 mt-4 text-sm font-medium">8 大性能评估 KPI 指标</div>
        <Descriptions :column="2" size="small" bordered :label-style="{ width: '120px' }">
          <DescriptionsItem label="综合评分">
            <span
              class="font-semibold"
              :style="{ color: scoreColor(drawerRecord.score) }"
            >
              {{ formatNumber(drawerRecord.score) }}
            </span>
          </DescriptionsItem>
          <DescriptionsItem label="准确率">
            {{ formatNumber(drawerRecord.accuracyRate, '%') }}
          </DescriptionsItem>
          <DescriptionsItem label="快速率">
            {{ formatNumber(drawerRecord.fastRate, '%') }}
          </DescriptionsItem>
          <DescriptionsItem label="平稳率">
            {{ formatNumber(drawerRecord.steadyRate, '%') }}
          </DescriptionsItem>
          <DescriptionsItem label="有效自控率">
            {{ formatNumber(drawerRecord.effectiveAutoRate, '%') }}
          </DescriptionsItem>
          <DescriptionsItem label="自控率">
            {{ formatNumber(drawerRecord.autoModeRate, '%') }}
          </DescriptionsItem>
          <DescriptionsItem label="好值率">
            {{ formatNumber(drawerRecord.goodValueRate, '%') }}
          </DescriptionsItem>
          <DescriptionsItem label="振荡率">
            {{ formatNumber(drawerRecord.oscillationRate, '%') }}
          </DescriptionsItem>
        </Descriptions>

        <!-- 4 大诊断指标 -->
        <div class="mb-2 mt-4 text-sm font-medium">4 大诊断指标</div>
        <Descriptions :column="2" size="small" bordered :label-style="{ width: '120px' }">
          <DescriptionsItem label="饱和率">
            {{ formatNumber(drawerRecord.saturationRate, '%') }}
          </DescriptionsItem>
          <DescriptionsItem label="振荡率">
            {{ formatNumber(drawerRecord.oscillationRate, '%') }}
          </DescriptionsItem>
          <DescriptionsItem label="阀门粘滞指数">
            {{ formatNumber(drawerRecord.stictionIndex) }}
          </DescriptionsItem>
          <DescriptionsItem label="稳定时间">
            {{ formatNumber(drawerRecord.settlingTime, 's') }}
          </DescriptionsItem>
        </Descriptions>

        <!-- 可信度 + 时间窗口 + 评估时间 -->
        <div class="mb-2 mt-4 text-sm font-medium">评估信息</div>
        <Descriptions :column="2" size="small" bordered :label-style="{ width: '120px' }">
          <DescriptionsItem label="可信度">
            <Badge
              v-if="drawerRecord.confidenceLevel"
              :color="CONFIDENCE_COLOR_MAP[drawerRecord.confidenceLevel]"
              :text="CONFIDENCE_LABEL_MAP[drawerRecord.confidenceLevel]"
            />
            <span v-else>—</span>
          </DescriptionsItem>
          <DescriptionsItem label="评估状态">
            <Tag :color="STATUS_COLOR_MAP[drawerRecord.status] || 'default'">
              {{ STATUS_LABEL_MAP[drawerRecord.status] || drawerRecord.status }}
            </Tag>
          </DescriptionsItem>
          <DescriptionsItem label="时间窗口">
            <span class="font-mono text-xs">
              {{
                formatTsRange(drawerRecord.tsStart, drawerRecord.tsEnd)
              }}
            </span>
          </DescriptionsItem>
          <DescriptionsItem label="评估时间">
            <span class="font-mono text-xs">
              {{ formatTime(drawerRecord.tsEnd) }}
            </span>
          </DescriptionsItem>
          <DescriptionsItem label="有效数据率">
            {{ formatNumber(drawerRecord.validRate) }}
          </DescriptionsItem>
          <DescriptionsItem label="算法版本">
            {{ drawerRecord.algorithmVersion || '—' }}
          </DescriptionsItem>
        </Descriptions>
      </template>
    </Drawer>

    <!-- 历史 Modal -->
    <Modal
      v-model:open="historyModalVisible"
      :title="`历史趋势 - ${historyRecord?.loopTagName ?? ''}`"
      width="900px"
      :footer="null"
      destroy-on-close
    >
      <Spin :spinning="historyLoading">
        <div class="space-y-3">
          <!-- 时间维度切换 -->
          <div class="flex items-center gap-2">
            <span class="text-sm text-gray-500">时间范围：</span>
            <RadioGroup
              v-model:value="historyWindow"
              :options="historyWindowOptions"
              option-type="button"
              button-style="solid"
              size="small"
              @change="handleHistoryWindowChange"
            />
          </div>

          <!-- 趋势图 -->
          <div v-if="historySnapshots.length > 0">
            <EchartsUI ref="historyChartRef" height="380px" />
          </div>
          <div v-else class="py-12 text-center text-gray-400">
            暂无历史趋势数据
          </div>
        </div>
      </Spin>
    </Modal>

    <!-- 诊断 Modal -->
    <Modal
      v-model:open="diagModalVisible"
      :title="`诊断可视化 - ${diagRecord?.loopTagName ?? ''}`"
      width="90%"
      :footer="null"
      destroy-on-close
    >
      <Spin :spinning="diagLoading">
        <div v-if="diagData" class="space-y-3">
          <!-- 诊断摘要 -->
          <Card :bordered="false" size="small">
            <Row :gutter="[16, 16]">
              <Col :span="6">
                <div class="summary-card">
                  <div class="summary-label">综合评分</div>
                  <div
                    class="summary-value"
                    :style="{ color: scoreColor(diagData.compositeScore) }"
                  >
                    {{ diagData.compositeScore?.toFixed(1) ?? '—' }}
                  </div>
                </div>
              </Col>
              <Col :span="6">
                <div class="summary-card">
                  <div class="summary-label">融合置信度</div>
                  <div class="summary-value">
                    {{ ((diagData.fusedConfidence ?? 0) * 100).toFixed(1) }}%
                  </div>
                </div>
              </Col>
              <Col :span="12">
                <div class="summary-card">
                  <div class="summary-label">诊断标签</div>
                  <div class="summary-tags">
                    <Tag
                      v-for="label in diagData.diagnosisLabels"
                      :key="label.label"
                      :color="
                        (label.label &&
                          DIAGNOSIS_LABEL_COLOR_MAP[label.label as keyof typeof DIAGNOSIS_LABEL_COLOR_MAP]) ||
                        'default'
                      "
                    >
                      {{
                        label.label &&
                        DIAGNOSIS_LABEL_NAME_MAP[label.label as keyof typeof DIAGNOSIS_LABEL_NAME_MAP]
                      }}
                      ({{ (label.confidence * 100).toFixed(0) }}%)
                    </Tag>
                  </div>
                </div>
              </Col>
            </Row>
          </Card>

          <!-- 4 个 Tab 页 -->
          <Tabs v-model:active-key="diagActiveTab" @change="handleDiagHistoryTabChange">
            <!-- Tab 1: 诊断概览 -->
            <Tabs.TabPane key="overview" tab="诊断概览">
              <Row :gutter="[12, 12]">
                <Col :span="8">
                  <Card :bordered="false" class="mini-card">
                    <QualityTimelineChart :data="diagData.qualityTimeline" />
                  </Card>
                </Col>
                <Col :span="8">
                  <Card :bordered="false" class="mini-card">
                    <SaturationChart :data="diagData.saturationAnalysis" />
                  </Card>
                </Col>
                <Col :span="8">
                  <Card :bordered="false" class="mini-card">
                    <SlowResponseCard :data="diagData.slowResponse" />
                  </Card>
                </Col>
                <Col :span="8">
                  <Card :bordered="false" class="mini-card">
                    <ChoudhuryCard :data="diagData.choudhury" />
                  </Card>
                </Col>
                <Col :span="8">
                  <Card :bordered="false" class="mini-card">
                    <IaeCard :data="diagData.iaeAnalysis" />
                  </Card>
                </Col>
                <Col :span="8">
                  <Card :bordered="false" class="mini-card">
                    <KanoCard :data="diagData.kano" />
                  </Card>
                </Col>
              </Row>
            </Tabs.TabPane>

            <!-- Tab 2: 频谱分析 -->
            <Tabs.TabPane key="spectrum" tab="频谱分析">
              <Card :bordered="false">
                <SpectrumChart :data="diagData.spectrum" />
              </Card>
            </Tabs.TabPane>

            <!-- Tab 3: 时域分析 -->
            <Tabs.TabPane key="time" tab="时域分析">
              <Row :gutter="[16, 16]">
                <Col :span="12">
                  <Card :bordered="false" class="chart-card">
                    <StepResponseChart :data="diagData.stepResponse" />
                  </Card>
                </Col>
                <Col :span="12">
                  <Card :bordered="false" class="chart-card">
                    <CusumChart :data="diagData.cusumAnalysis" />
                  </Card>
                </Col>
                <Col :span="12">
                  <Card :bordered="false" class="chart-card">
                    <ScatterChart :data="diagData.scatterPlot" />
                  </Card>
                </Col>
              </Row>
            </Tabs.TabPane>

            <!-- Tab 4: 评估历史 -->
            <Tabs.TabPane key="history" tab="评估历史">
              <!-- 筛选区 -->
              <div class="mb-3 flex flex-wrap items-center gap-2">
                <Select
                  v-model:value="diagHistoryStatus"
                  :options="statusOptions"
                  placeholder="状态"
                  allow-clear
                  style="width: 130px"
                  @change="loadDiagHistory"
                />
                <Select
                  v-model:value="diagHistoryConfidence"
                  placeholder="可信度"
                  allow-clear
                  style="width: 130px"
                  @change="loadDiagHistory"
                >
                  <Select.Option value="A">A 优秀</Select.Option>
                  <Select.Option value="B">B 良好</Select.Option>
                  <Select.Option value="C">C 一般</Select.Option>
                  <Select.Option value="D">D 较差</Select.Option>
                  <Select.Option value="E">E 不足</Select.Option>
                </Select>
                <Button type="primary" size="small" @click="loadDiagHistory">
                  刷新
                </Button>
              </div>

              <Table
                :columns="diagHistoryColumns"
                :data-source="diagHistorySnapshots"
                :loading="diagHistoryLoading"
                :pagination="{
                  current: diagHistoryPage,
                  pageSize: diagHistoryPageSize,
                  total: diagHistoryTotal,
                  showSizeChanger: true,
                  showTotal: (t: number) => `共 ${t} 条`,
                }"
                row-key="tsStart"
                size="small"
                :scroll="{ x: 800 }"
                @change="handleDiagHistoryTableChange"
              >
                <template #bodyCell="{ column, record }">
                  <template v-if="column.key === 'tsRange'">
                    <span class="font-mono text-xs">
                      {{
                        formatTsRange(
                          (record as KpiSnapshotItem).tsStart,
                          (record as KpiSnapshotItem).tsEnd,
                        )
                      }}
                    </span>
                  </template>
                  <template v-else-if="column.key === 'score'">
                    <span
                      class="font-semibold"
                      :style="{
                        color: scoreColor((record as KpiSnapshotItem).score),
                      }"
                    >
                      {{ formatNumber((record as KpiSnapshotItem).score) }}
                    </span>
                  </template>
                  <template
                    v-else-if="
                      ([
                        'accuracyRate',
                        'fastRate',
                        'steadyRate',
                        'effectiveAutoRate',
                      ] as string[]).includes(column.key as string)
                    "
                  >
                    <span class="font-mono text-xs">
                      {{
                        formatNumber(
                          (record as Record<string, unknown>)[
                            column.dataIndex as string
                          ] as number | null | undefined,
                          '%',
                        )
                      }}
                    </span>
                  </template>
                  <template v-else-if="column.key === 'confidenceLevel'">
                    <Badge
                      v-if="(record as KpiSnapshotItem).confidenceLevel"
                      :color="
                        CONFIDENCE_COLOR_MAP[
                          (record as KpiSnapshotItem).confidenceLevel!
                        ]
                      "
                      :text="
                        CONFIDENCE_LABEL_MAP[
                          (record as KpiSnapshotItem).confidenceLevel!
                        ]
                      "
                    />
                    <span v-else class="text-gray-400">—</span>
                  </template>
                  <template v-else-if="column.key === 'status'">
                    <Tag
                      :color="
                        STATUS_COLOR_MAP[(record as KpiSnapshotItem).status] ||
                        'default'
                      "
                      class="m-0"
                    >
                      {{
                        STATUS_LABEL_MAP[(record as KpiSnapshotItem).status] ||
                        (record as KpiSnapshotItem).status
                      }}
                    </Tag>
                  </template>
                </template>
              </Table>
            </Tabs.TabPane>
          </Tabs>
        </div>
        <div v-else class="py-12 text-center text-gray-400">
          暂无诊断可视化数据
        </div>
      </Spin>
    </Modal>
  </Page>
</template>

<style scoped>
.summary-card {
  padding: 12px;
  background: var(--ant-color-fill-quaternary, rgb(0 0 0 / 4%));
  border-radius: 8px;
}

.summary-label {
  margin-bottom: 4px;
  font-size: 12px;
  color: #6b7280;
}

.summary-value {
  font-size: 24px;
  font-weight: 700;
  color: #1f2937;
}

.summary-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.mini-card {
  height: 200px;
}

.chart-card {
  height: 300px;
}

:deep(.ant-table-cell) {
  white-space: nowrap;
}

:deep(.ant-table-cell-wrap-all) {
  white-space: normal;
}
</style>
