<script lang="ts" setup>
/**
 * 性能看板（v5.3 重构）
 *
 * 对齐 UI/UX v5.3 §6.3.1 + FDS §5.3.4 / §5.3.6 / §5.3.7
 * - 左侧：工厂树导航
 * - 顶部：PageToolbar（时间窗 + 刷新 + 导出）
 * - Partial 警告横幅（条件触发，可折叠不可关闭）
 * - 装置级三大 KPI 卡片区（综合性能/平均自控率/稳定率）+ 实时自控率仪表盘（4 卡片横排）
 * - 装置评分排名柱状图（左 60%）+ 全厂稳定率趋势双轴折线图（右 40%）
 * - 低效回路 Top 10 预览
 * - StatusFooter：最近刷新/数据延迟/自动刷新状态
 * - 5 分钟自动刷新；实时自控率 60 秒轮询
 */
import type { TableColumnsType, TablePaginationConfig } from 'ant-design-vue';

import type { EchartsUIType } from '@vben/plugins/echarts';

import type { ConfidenceLevel, MetricApi, TimeWindow } from '#/api/metric';
import type { PlantNodeApi } from '#/api/plant-node';
import type { DashboardApi } from '#/api/dashboard';

import {
  computed,
  nextTick,
  onMounted,
  onUnmounted,
  reactive,
  ref,
  watch,
} from 'vue';
import { useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';
import { EchartsUI, useEcharts } from '@vben/plugins/echarts';
import { IconifyIcon } from '@vben/icons';

import {
  Alert,
  Button,
  Card,
  Empty,
  Input,
  message,
  Modal,
  Select,
  Table,
  Tag,
  Tooltip,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import {
  getBoardApi,
  getRankingApi,
} from '#/api/metric';
import { getAutoRateRtApi, getBoardKpiApi } from '#/api/dashboard';
import {
  ClpmDataCanvas,
  ClpmPageToolbar,
  ClpmRealtimeStatus,
  ClpmToolbarButton,
} from '#/components/clpm';
import PlantNodeTree from '#/components/plant-node/plant-node-tree.vue';
import AutoRateGauge from '#/components/metric/auto-rate-gauge.vue';
import ConfidenceBadge from '#/components/metric/confidence-badge.vue';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import { useIndustrialStatus } from '#/composables/use-industrial-status';
import { usePagePreference } from '#/composables/use-clpm-preferences';
import type { FilterPreset } from '#/composables/use-clpm-preferences';

defineOptions({ name: 'MetricDashboard' });

const { isDark, themeColors } = useClpmTheme();
const { getStatusMeta } = useIndustrialStatus();

const router = useRouter();

// ===== 用户偏好 =====
const {
  preferences,
  setDefaultTimeWindow,
  saveFilterPreset,
  deleteFilterPreset,
  reset: resetPreferences,
} = usePagePreference('metric-dashboard');

// ===== 树（使用统一组件 PlantNodeTree）=====
const selectedPlantNodeId = ref<string | undefined>(undefined);
const selectedPlantNodeName = ref<string>('全厂');

/** 选中树节点（由 PlantNodeTree emit 触发） */
function onTreeSelect(node: PlantNodeApi.PlantNode | null) {
  if (node) {
    selectedPlantNodeId.value = node.id;
    selectedPlantNodeName.value = node.name;
  } else {
    selectedPlantNodeId.value = undefined;
    selectedPlantNodeName.value = '全厂';
  }
  loadAll();
}

// ===== 看板数据 =====
const loading = ref(false);
const boardData = ref<MetricApi.BoardResult | null>(null);
const boardKpi = ref<DashboardApi.BoardResult | null>(null);
const autoRateRt = ref<DashboardApi.AutoRateRt | null>(null);
const autoRateLoading = ref(false);

const timeWindowOptions = [
  { label: '今天', value: 'today' },
  { label: '昨天', value: 'yesterday' },
  { label: '近 7 天', value: 'last_7_days' },
  { label: '近 30 天', value: 'last_30_days' },
];

const filter = reactive({
  timeWindow:
    (preferences.value.defaultTimeWindow as TimeWindow) ||
    ('today' as TimeWindow),
});

// ===== 状态反馈 =====
const lastRefreshAt = ref<Date | null>(null);
const lastRefreshText = computed(() => {
  if (!lastRefreshAt.value) return '';
  const diff = dayjs().diff(lastRefreshAt.value, 'second');
  if (diff < 60) return `${diff} 秒前`;
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  return dayjs(lastRefreshAt.value).format('HH:mm:ss');
});

const dataDelayText = computed(() => {
  const readAt = autoRateRt.value?.readAt;
  if (!readAt) return '';
  const diff = dayjs().diff(dayjs(readAt), 'minute');
  if (diff < 1) return '<1m';
  if (diff < 60) return `${diff}m`;
  return `${Math.floor(diff / 60)}h`;
});

/** 实时状态：用于 ClpmRealtimeStatus 组件（v6.1 §15.3 P1-1） */
const realtimeStatus = computed<
  'delayed' | 'failed' | 'offline' | 'online' | 'refreshing'
>(() => {
  if (loading.value) return 'refreshing';
  if (!lastRefreshAt.value) return 'offline';
  const diffSec = dayjs().diff(lastRefreshAt.value, 'second');
  // 看板刷新周期 5 分钟，超过 5 分钟视为延迟
  if (diffSec > 300) return 'delayed';
  return 'online';
});

/** 数据延迟（毫秒），用于 ClpmRealtimeStatus 显示 */
const realtimeLatency = computed(() => {
  if (!lastRefreshAt.value) return 0;
  return dayjs().diff(lastRefreshAt.value, 'millisecond');
});

// ===== Partial 警告横幅（可折叠不可关闭）=====
const partialBannerCollapsed = ref(false);

/** 选中装置的 BoardItem（用于三大 KPI 卡片） */
const selectedBoardItem = computed<DashboardApi.BoardItem | null>(() => {
  if (!boardKpi.value || boardKpi.value.items.length === 0) return null;
  if (selectedPlantNodeId.value) {
    return (
      boardKpi.value.items.find((it) => it.nodeId === selectedPlantNodeId.value) ||
      null
    );
  }
  // 未选中节点：取第一个作为全厂代表
  return boardKpi.value.items[0] || null;
});

/** 装置级三大 KPI 卡片数据 */
interface KpiCardData {
  title: string;
  value: number | null;
  unit: string;
  evaluatedLoops: number;
  inconclusiveLoops: number;
  excludedLoops: number;
  confidenceLevel: ConfidenceLevel | null;
  trendDelta: number | null;
  sparkline: number[];
}

const kpiCards = computed<KpiCardData[]>(() => {
  const item = selectedBoardItem.value;
  const spark = boardData.value?.steadyRateTrend?.values ?? [];
  if (!item) {
    return [
      {
        title: '综合性能',
        value: null,
        unit: '',
        evaluatedLoops: 0,
        inconclusiveLoops: 0,
        excludedLoops: 0,
        confidenceLevel: null,
        trendDelta: null,
        sparkline: [],
      },
      {
        title: '平均自控率',
        value: null,
        unit: '%',
        evaluatedLoops: 0,
        inconclusiveLoops: 0,
        excludedLoops: 0,
        confidenceLevel: null,
        trendDelta: null,
        sparkline: [],
      },
      {
        title: '稳定率',
        value: null,
        unit: '%',
        evaluatedLoops: 0,
        inconclusiveLoops: 0,
        excludedLoops: 0,
        confidenceLevel: null,
        trendDelta: null,
        sparkline: [],
      },
    ];
  }
  return [
    {
      title: '综合性能',
      value: item.avgScore,
      unit: '',
      evaluatedLoops: item.evaluatedLoops,
      inconclusiveLoops: item.inconclusiveLoops,
      excludedLoops: item.excludedLoops,
      confidenceLevel: null,
      trendDelta: null,
      sparkline: spark,
    },
    {
      title: '平均自控率',
      value: item.autoModeRate,
      unit: '%',
      evaluatedLoops: item.evaluatedLoops,
      inconclusiveLoops: item.inconclusiveLoops,
      excludedLoops: item.excludedLoops,
      confidenceLevel: null,
      trendDelta: null,
      sparkline: spark,
    },
    {
      title: '稳定率',
      value: item.stabilityRate,
      unit: '%',
      evaluatedLoops: item.evaluatedLoops,
      inconclusiveLoops: item.inconclusiveLoops,
      excludedLoops: item.excludedLoops,
      confidenceLevel: null,
      trendDelta: null,
      sparkline: spark,
    },
  ];
});

/** 实时自控率历史（用于 sparkline，最近 60 分钟） */
const autoRateHistory = ref<number[]>([]);

// ===== 详细列表（低效回路 Top 10） =====
const rankingLoading = ref(false);
const rankingList = ref<MetricApi.RankingItem[]>([]);
const rankingTotal = ref(0);
const rankingQuery = reactive({
  page: 1,
  pageSize: 10,
});

const rankingColumns: TableColumnsType = [
  { title: '排名', dataIndex: 'rank', key: 'rank', width: 70, align: 'center' },
  {
    title: '回路位号',
    dataIndex: 'tagName',
    key: 'tagName',
    width: 140,
    ellipsis: true,
  },
  {
    title: '综合评分',
    dataIndex: 'score',
    key: 'score',
    width: 100,
    align: 'right',
  },
  {
    title: '稳定率',
    dataIndex: 'steadyRate',
    key: 'steadyRate',
    width: 90,
    align: 'right',
  },
  {
    title: '可信度',
    dataIndex: 'confidenceLevel',
    key: 'confidenceLevel',
    width: 110,
    align: 'center',
  },
  {
    title: '预诊',
    dataIndex: 'preDiagnosis',
    key: 'preDiagnosis',
    width: 140,
    ellipsis: true,
  },
  {
    title: '处理状态',
    dataIndex: 'actionStatus',
    key: 'actionStatus',
    width: 100,
    align: 'center',
  },
];

const actionStatusLabel: Record<string, string> = {
  PENDING: '待处理',
  IN_PROGRESS: '处理中',
  IMPLEMENTED: '已实施',
  IGNORED: '已忽略',
};

// ECharts 趋势图
const trendChartRef = ref<EchartsUIType>();
const { renderEcharts: renderTrend, getChartInstance: getTrendInstance } =
  useEcharts(trendChartRef);

// 装置评分排名柱状图
const unitRankingRef = ref<EchartsUIType>();
const { renderEcharts: renderUnitRankingEcharts } = useEcharts(unitRankingRef);

// 自动刷新
const REFRESH_INTERVAL = 5 * 60 * 1000;
/** 实时自控率轮询间隔（60 秒） */
const AUTO_RATE_INTERVAL = 60 * 1000;
let refreshTimer: null | ReturnType<typeof setInterval> = null;
let autoRateTimer: null | ReturnType<typeof setInterval> = null;

/** 加载看板数据（含装置级三大 KPI） */
async function loadBoard() {
  loading.value = true;
  try {
    // 并行：原 board（含稳定率趋势/Partial 警告）+ 装置级 BoardKpi
    const [board, kpi] = await Promise.all([
      getBoardApi({
        plantNodeId: selectedPlantNodeId.value,
        timeWindow: filter.timeWindow,
      }),
      getBoardKpiApi({
        plantId: selectedPlantNodeId.value,
      }),
    ]);
    boardData.value = board;
    boardKpi.value = kpi;
    await nextTick();
    renderTrendChart();
    renderUnitRanking();
  } catch {
    // 错误已由拦截器处理
  } finally {
    loading.value = false;
    lastRefreshAt.value = new Date();
  }
}

/** 加载实时自控率 */
async function loadAutoRateRt() {
  autoRateLoading.value = true;
  try {
    const data = await getAutoRateRtApi({
      plantId: selectedPlantNodeId.value,
    });
    autoRateRt.value = data;
    // 维护最近 60 分钟历史
    if (data.rate !== null && data.rate !== undefined) {
      autoRateHistory.value = [...autoRateHistory.value, data.rate].slice(-60);
    }
  } catch {
    // 错误已由拦截器处理
  } finally {
    autoRateLoading.value = false;
  }
}

/** 加载低效排行 Top 10 */
async function loadRanking() {
  rankingLoading.value = true;
  try {
    const data = await getRankingApi({
      plantNodeId: selectedPlantNodeId.value,
      timeWindow: filter.timeWindow,
      sortBy: 'score',
      sortOrder: 'asc',
      limit: 10,
    });
    // 默认仅展示 include_in_evaluation=true 的回路
    const items = (data || []).filter(
      (it) => it.includeInEvaluation !== false,
    );
    rankingList.value = items;
    rankingTotal.value = items.length;
  } catch {
    // 错误已由拦截器处理
  } finally {
    rankingLoading.value = false;
  }
}

function loadAll() {
  loadBoard();
  loadAutoRateRt();
  loadRanking();
}

function handleRankingTableChange(pagination: TablePaginationConfig) {
  rankingQuery.page = pagination.current || 1;
  rankingQuery.pageSize = pagination.pageSize || 10;
}

/** 渲染稳定率趋势双轴折线图（左轴稳定率，右轴参评回路数） */
function renderTrendChart() {
  const trend = boardData.value?.steadyRateTrend;
  if (!trend || !trend.timestamps || trend.timestamps.length === 0) return;

  // D2 联动：选中时间点 markLine
  const selTs = selectedTrendTime.value;
  // 右轴：参评回路数（用 selectedBoardItem.evaluatedLoops 派生近似序列）
  const evaluatedLoops = selectedBoardItem.value?.evaluatedLoops ?? 0;
  const loopCounts = trend.values.map(() => evaluatedLoops);

  renderTrend({
    grid: { bottom: 30, containLabel: true, left: '2%', right: '2%', top: 40 },
    legend: { data: ['稳定率', '参评回路数'], top: 5 },
    series: [
      {
        areaStyle: { opacity: 0.15 },
        data: trend.values,
        itemStyle: { color: themeColors.value.INFO },
        lineStyle: { width: 2 },
        markLine: selTs
          ? {
              data: [{ xAxis: selTs }],
              label: {
                color: themeColors.value.DANGER,
                formatter: '选中',
                position: 'end',
                show: true,
              },
              lineStyle: {
                color: themeColors.value.DANGER,
                type: 'solid',
                width: 2,
              },
              silent: true,
              symbol: 'none',
            }
          : undefined,
        name: '稳定率',
        smooth: true,
        type: 'line',
        yAxisIndex: 0,
      },
      {
        data: loopCounts,
        itemStyle: { color: themeColors.value.SUCCESS },
        lineStyle: { width: 1.5, type: 'dashed' },
        name: '参评回路数',
        symbolSize: 4,
        type: 'line',
        yAxisIndex: 1,
      },
    ],
    tooltip: {
      axisPointer: { type: 'cross' },
      trigger: 'axis',
      formatter: (params) => {
        const arr = Array.isArray(params) ? params : [params];
        let html = '';
        arr.forEach((p) => {
          const val = (p as { value: number | null }).value;
          if (val === null || val === undefined) return;
          const sp = p as { seriesName?: string; marker?: string };
          const formatted =
            sp.seriesName === '参评回路数'
              ? `${Math.round(Number(val))} 个`
              : `${Number(val).toFixed(1)}%`;
          html += `${sp.marker ?? ''} ${sp.seriesName ?? ''}: ${formatted}<br/>`;
        });
        return html;
      },
    },
    xAxis: {
      axisLabel: {
        formatter: (val: string) => {
          try {
            const d = new Date(new Date(val).getTime() + 8 * 3600 * 1000);
            const mm = String(d.getUTCMonth() + 1).padStart(2, '0');
            const dd = String(d.getUTCDate()).padStart(2, '0');
            const hh = String(d.getUTCHours()).padStart(2, '0');
            const mi = String(d.getUTCMinutes()).padStart(2, '0');
            return `${mm}-${dd} ${hh}:${mi}`;
          } catch {
            return val;
          }
        },
      },
      boundaryGap: false,
      data: trend.timestamps,
      type: 'category',
    },
    yAxis: [
      {
        axisLabel: { formatter: '{value}%' },
        max: 100,
        min: 0,
        name: '稳定率',
        type: 'value',
      },
      {
        axisLabel: { formatter: '{value}' },
        name: '回路数',
        splitLine: { show: false },
        type: 'value',
      },
    ],
  }).then(() => {
    bindTrendClickEvent();
  });
}

// ===== D2 多图联动：趋势图点击事件 =====
let trendBoundZr: any = null;
let trendClickHandler: ((params: any) => void) | null = null;

function bindTrendClickEvent() {
  const chart = getTrendInstance();
  if (!chart) return;
  const zr = chart.getZr();
  if (!zr) return;
  if (trendBoundZr === zr && trendClickHandler) return;
  if (trendBoundZr && trendClickHandler) {
    trendBoundZr.off('click', trendClickHandler);
  }
  trendClickHandler = (params: any) => {
    const trend = boardData.value?.steadyRateTrend;
    if (!trend || !trend.timestamps || trend.timestamps.length === 0) return;
    const point = [params.offsetX, params.offsetY];
    const xVal = chart.convertFromPixel({ xAxisIndex: 0 }, point[0]);
    if (xVal === null || xVal === undefined || Number.isNaN(xVal)) return;
    const idx = Math.round(xVal);
    if (idx < 0 || idx >= trend.timestamps.length) return;
    onTrendTimeSelect(trend.timestamps[idx]!);
  };
  zr.on('click', trendClickHandler);
  trendBoundZr = zr;
}

function onTrendTimeSelect(timestamp: string) {
  selectedTrendTime.value = timestamp;
  if (rankingList.value.length > 0) {
    const lowest = rankingList.value[0]!;
    selectedLoopId.value = lowest.loopId;
    nextTick(() => scrollToSelectedRow());
  }
}

function clearTrendSelection() {
  selectedTrendTime.value = null;
  selectedLoopId.value = null;
}

function scrollToSelectedRow() {
  if (!selectedLoopId.value) return;
  const row = document.querySelector(
    `tr[data-loop-id="${selectedLoopId.value}"]`,
  );
  if (row) {
    row.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
}

function rankingRowClassName(record: MetricApi.RankingItem): string {
  return record.loopId === selectedLoopId.value ? 'clpm-row-selected' : '';
}

function rankingCustomRow(record: MetricApi.RankingItem): any {
  return {
    'data-loop-id': record.loopId,
  };
}

function formatSelectedTime(ts: string): string {
  try {
    const d = new Date(new Date(ts).getTime() + 8 * 3600 * 1000);
    const mm = String(d.getUTCMonth() + 1).padStart(2, '0');
    const dd = String(d.getUTCDate()).padStart(2, '0');
    const hh = String(d.getUTCHours()).padStart(2, '0');
    const mi = String(d.getUTCMinutes()).padStart(2, '0');
    return `${mm}-${dd} ${hh}:${mi}`;
  } catch {
    return ts;
  }
}

/** 装置评分排名柱状图（横向，按评分降序） */
function renderUnitRanking() {
  const items = boardKpi.value?.items ?? [];
  if (items.length === 0) return;
  const sorted = [...items]
    .filter((it) => it.avgScore !== null && it.avgScore !== undefined)
    .sort((a, b) => (b.avgScore ?? 0) - (a.avgScore ?? 0));

  renderUnitRankingEcharts({
    grid: { bottom: 20, containLabel: true, left: '2%', right: '4%', top: 16 },
    series: [
      {
        type: 'bar',
        data: sorted.map((it) => ({
          value: it.avgScore ?? 0,
          itemStyle: { color: scoreToColor(it.avgScore ?? 0) },
        })),
        label: {
          show: true,
          position: 'right',
          formatter: (p: any) => `${Number(p.value).toFixed(1)}`,
        },
        barWidth: 16,
      },
    ],
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (p: any) => {
        const idx = p?.[0]?.dataIndex ?? 0;
        const it = sorted[idx];
        if (!it) return '';
        return `${it.nodeName ?? '—'}<br/>综合评分: ${it.avgScore?.toFixed(1) ?? '—'}<br/>参评回路: ${it.evaluatedLoops}`;
      },
    },
    xAxis: {
      type: 'value',
      max: 100,
      min: 0,
      axisLabel: { formatter: '{value}' },
    },
    yAxis: {
      type: 'category',
      data: sorted.map((it) => it.nodeName ?? '—'),
      inverse: false,
    },
  });
}

/**
 * 5 级定级颜色（对齐 ZL 工业色板，响应式跟随主题）
 * EXCELLENT(>=90)=emerald/GOOD(>=80)=teal/FAIR(>=70)=blue/WARNING(>=60)=amber/POOR(<60)=rose
 */
function scoreToColor(score: number): string {
  if (score >= 90) return themeColors.value.SUCCESS;
  if (score >= 80) return themeColors.value.ACCENT;
  if (score >= 70) return themeColors.value.INFO;
  if (score >= 60) return themeColors.value.WARNING;
  return themeColors.value.DANGER;
}

function startAutoRefresh() {
  stopAutoRefresh();
  refreshTimer = setInterval(() => {
    loadBoard();
    loadRanking();
  }, REFRESH_INTERVAL);
  autoRateTimer = setInterval(() => {
    loadAutoRateRt();
  }, AUTO_RATE_INTERVAL);
}

function stopAutoRefresh() {
  if (refreshTimer) {
    clearInterval(refreshTimer);
    refreshTimer = null;
  }
  if (autoRateTimer) {
    clearInterval(autoRateTimer);
    autoRateTimer = null;
  }
}

function handleTimeWindowChange() {
  loadAll();
}

// D2 多图联动
const selectedTrendTime = ref<string | null>(null);
const selectedLoopId = ref<string | null>(null);

watch(
  () => boardData.value?.steadyRateTrend,
  () => renderTrendChart(),
  { deep: true },
);

watch(selectedTrendTime, () => {
  nextTick(() => renderTrendChart());
});

// 装置级 KPI 变化时重渲柱状图
watch(
  () => boardKpi.value?.items,
  () => renderUnitRanking(),
  { deep: true },
);

// 主题切换重渲图表
watch(isDark, () => {
  nextTick(() => {
    renderTrendChart();
    renderUnitRanking();
  });
});

// ===== 偏好持久化 =====
watch(
  () => filter.timeWindow,
  (val) => setDefaultTimeWindow(val),
);

// ===== 筛选预设 =====
const presetModalVisible = ref(false);
const presetName = ref('');

function handleSavePreset() {
  presetName.value = `预设 ${(preferences.value.savedFilters?.length ?? 0) + 1}`;
  presetModalVisible.value = true;
}

function confirmSavePreset() {
  if (!presetName.value.trim()) {
    message.warning('请输入预设名称');
    return;
  }
  saveFilterPreset(presetName.value.trim(), {
    timeWindow: filter.timeWindow,
    level: undefined,
    keyword: '',
  });
  presetModalVisible.value = false;
  message.success('预设已保存');
}

function handleApplyPreset(preset: FilterPreset) {
  const f = preset.filters;
  if (f.timeWindow) {
    filter.timeWindow = f.timeWindow as TimeWindow;
  }
  loadAll();
  message.success(`已应用预设：${preset.name}`);
}

function handleDeletePreset(id: string) {
  deleteFilterPreset(id);
  message.success('预设已删除');
}

function handleResetPreferences() {
  resetPreferences();
  filter.timeWindow = 'today' as TimeWindow;
  message.success('页面偏好已重置');
}

function formatPercent(val: number | undefined | null): string {
  if (val === null || val === undefined || Number.isNaN(val)) return '—';
  return `${Number(val).toFixed(1)}%`;
}

function formatNumber(val: number | null | undefined, digits = 1): string {
  if (val === null || val === undefined || Number.isNaN(val)) return '--';
  return Number(val).toFixed(digits);
}

function scoreColor(score: number): string {
  if (score >= 80) return themeColors.value.SUCCESS;
  if (score >= 60) return themeColors.value.WARNING;
  return themeColors.value.DANGER;
}

function handleViewFullRanking() {
  router.push('/metric/ranking');
}

onMounted(() => {
  loadAll();
  startAutoRefresh();
});

onUnmounted(() => {
  stopAutoRefresh();
});
</script>

<template>
  <Page>
    <!-- Partial 警告横幅（可折叠不可关闭） -->
    <Alert
      v-if="boardData?.partialWarning?.active"
      class="mb-3"
      type="warning"
      show-icon
      :message="boardData.partialWarning.message || '存在部分回路数据不完整'"
      :description="
        partialBannerCollapsed
          ? ''
          : `不确定回路 ${boardData.partialWarning.inconclusiveCount} 个，部分关联 ${boardData.partialWarning.partialCount} 个`
      "
    >
      <template #action>
        <Button
          type="link"
          size="small"
          @click="partialBannerCollapsed = !partialBannerCollapsed"
        >
          {{ partialBannerCollapsed ? '展开查看' : '收起' }}
        </Button>
      </template>
    </Alert>

    <div class="flex gap-3" style="min-height: calc(100vh - 160px)">
      <PlantNodeTree
        card-title="工厂导航"
        :width="260"
        @select="onTreeSelect"
      />

      <div class="flex flex-1 flex-col gap-3">
        <ClpmPageToolbar
          title="性能看板"
          :subtitle="`${selectedPlantNodeName} · ${timeWindowOptions.find((o) => o.value === filter.timeWindow)?.label ?? '今天'}`"
          :loading="loading"
          :last-refresh="lastRefreshText"
          :data-delay="dataDelayText"
          status-type="info"
        >
          <Select
            v-model:value="filter.timeWindow"
            style="width: 140px"
            size="small"
            :options="timeWindowOptions"
            @change="handleTimeWindowChange"
          />
          <template #actions>
            <ClpmRealtimeStatus
              :status="realtimeStatus"
              :latency="realtimeLatency"
              :last-refresh="lastRefreshAt?.getTime() ?? ''"
              :auto-refresh="true"
              :refresh-interval="300"
            />
            <ClpmToolbarButton
              icon="refresh"
              label="刷新"
              :loading="loading"
              @click="loadAll"
            />
            <ClpmToolbarButton
              icon="export"
              label="导出"
              disabled
              disabled-reason="KPI 看板导出功能待后端接口支持"
            />
            <Button type="link" size="small" @click="handleSavePreset">
              保存预设
            </Button>
            <Button type="link" size="small" @click="handleResetPreferences">
              重置偏好
            </Button>
          </template>
        </ClpmPageToolbar>

        <!-- 筛选预设区 -->
        <div v-if="preferences.savedFilters?.length" class="clpm-preset-bar">
          <span class="text-xs text-gray-500">筛选预设：</span>
          <Tag
            v-for="preset in preferences.savedFilters"
            :key="preset.id"
            class="m-0 cursor-pointer"
            @click="handleApplyPreset(preset)"
          >
            {{ preset.name }}
            <span
              class="ml-1 text-gray-400 hover:text-red-500"
              @click.stop="handleDeletePreset(preset.id)"
            >
              ×
            </span>
          </Tag>
        </div>

        <!-- 装置级三大 KPI 卡片区 + 实时自控率仪表盘（4 卡片横排） -->
        <div class="clpm-kpi-grid">
          <Tooltip
            v-for="card in kpiCards"
            :key="card.title"
            placement="bottom"
          >
            <template #title>
              <div class="text-xs">
                <div>参评回路：{{ card.evaluatedLoops }}</div>
                <div>不确定回路：{{ card.inconclusiveLoops }}</div>
                <div>不参评回路：{{ card.excludedLoops }}</div>
              </div>
            </template>
            <Card size="small" class="clpm-kpi-card">
              <div class="text-xs text-gray-500">{{ card.title }}</div>
              <div class="clpm-kpi-value">
                <span v-if="card.value === null || card.value === undefined">--</span>
                <span
                  v-else
                  :style="{ color: scoreColor(card.value) }"
                  class="clpm-kpi-number clpm-num"
                >
                  {{ formatNumber(card.value, card.title === '综合性能' ? 1 : 1) }}
                </span>
                <span v-if="card.unit && card.value !== null && card.value !== undefined" class="clpm-kpi-unit">
                  {{ card.unit }}
                </span>
              </div>
              <div class="clpm-kpi-meta">
                <span class="text-xs text-gray-400">
                  参评 {{ card.evaluatedLoops }} 回路
                </span>
                <ConfidenceBadge
                  v-if="card.confidenceLevel"
                  :level="card.confidenceLevel"
                  size="small"
                />
              </div>
            </Card>
          </Tooltip>

          <!-- 实时自控率仪表盘卡片（第 4 张） -->
          <AutoRateGauge
            :rate="autoRateRt?.rate ?? null"
            :auto-count="autoRateRt?.autoCount ?? 0"
            :total-count="autoRateRt?.totalCount ?? 0"
            :history="autoRateHistory"
            :loading="autoRateLoading"
            :subtitle="
              autoRateRt?.readAt
                ? `统计于 ${new Date(autoRateRt.readAt).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })}`
                : ''
            "
          />
        </div>

        <!-- 装置评分排名柱状图（左 60%）+ 全厂稳定率趋势双轴折线图（右 40%） -->
        <div class="clpm-chart-grid">
          <Card size="small" title="装置评分排名" class="clpm-chart-card">
            <EchartsUI ref="unitRankingRef" height="280px" />
          </Card>
          <Card size="small" title="全厂稳定率趋势" class="clpm-chart-card">
            <!-- D2 多图联动状态指示条 -->
            <div v-if="selectedTrendTime" class="clpm-linkage-bar">
              <IconifyIcon icon="ant-design:link-outlined" />
              <span>
                联动已激活：选中时间 {{ formatSelectedTime(selectedTrendTime) }}
              </span>
              <Button type="link" size="small" @click="clearTrendSelection">
                清除
              </Button>
            </div>
            <EchartsUI ref="trendChartRef" height="240px" />
          </Card>
        </div>

        <!-- 低效回路 Top 10 预览 -->
        <ClpmDataCanvas title="低效回路 Top 10 预览" :loading="rankingLoading">
          <template #extra>
            <Button type="link" size="small" @click="handleViewFullRanking">
              查看完整排行 →
            </Button>
          </template>
          <Empty
            v-if="!rankingLoading && rankingList.length === 0"
            description="当前筛选条件下无低效回路数据"
          />
          <Table
            v-else
            :columns="rankingColumns"
            :data-source="rankingList"
            :loading="rankingLoading"
            :pagination="{
              current: rankingQuery.page,
              pageSize: rankingQuery.pageSize,
              total: rankingTotal,
              showSizeChanger: true,
              showTotal: (t: number) => `共 ${t} 条`,
            }"
            :row-class-name="rankingRowClassName"
            :custom-row="rankingCustomRow"
            :row-key="(record: MetricApi.RankingItem) => record.loopId"
            :scroll="{ x: 720 }"
            size="small"
            @change="handleRankingTableChange"
          >
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'rank'">
                <Tag
                  v-if="record.rank <= 3"
                  :color="
                    ['error', 'warning', 'default'][record.rank - 1] ?? 'default'
                  "
                  class="m-0"
                >
                  {{ record.rank }}
                </Tag>
                <span v-else class="clpm-num">{{ record.rank }}</span>
              </template>
              <template v-else-if="column.key === 'tagName'">
                <span class="clpm-num font-mono">{{ record.tagName }}</span>
              </template>
              <template v-else-if="column.key === 'score'">
                <span
                  v-if="record.status === 'INCONCLUSIVE'"
                  class="text-gray-400"
                >
                  —
                </span>
                <span
                  v-else
                  class="clpm-num font-mono font-bold"
                  :style="{ color: scoreColor(record.score) }"
                >
                  {{ Number(record.score).toFixed(1) }}
                </span>
              </template>
              <template v-else-if="column.key === 'steadyRate'">
                <span class="clpm-num font-mono text-xs">
                  {{ formatPercent(record.steadyRate) }}
                </span>
              </template>
              <template v-else-if="column.key === 'confidenceLevel'">
                <ConfidenceBadge
                  :level="record.confidenceLevel"
                  :valid-rate="record.validRate"
                  size="small"
                />
              </template>
              <template v-else-if="column.key === 'preDiagnosis'">
                <Tag v-if="record.preDiagnosis" color="warning" class="m-0">
                  {{ record.preDiagnosis }}
                </Tag>
                <span v-else class="text-gray-400">—</span>
              </template>
              <template v-else-if="column.key === 'actionStatus'">
                <Tag
                  :color="getStatusMeta(record.actionStatus).color"
                  :style="{
                    background: getStatusMeta(record.actionStatus).bgColor,
                    borderColor: getStatusMeta(record.actionStatus).borderColor,
                  }"
                  class="m-0"
                >
                  {{ actionStatusLabel[record.actionStatus] || record.actionStatus }}
                </Tag>
              </template>
            </template>
          </Table>
        </ClpmDataCanvas>

        <!-- StatusFooter -->
        <div class="clpm-status-footer">
          <span>最近刷新：{{ lastRefreshText || '尚未刷新' }}</span>
          <span class="clpm-status-footer__divider">·</span>
          <span>数据延迟：{{ dataDelayText || '—' }}</span>
          <span class="clpm-status-footer__divider">·</span>
          <span>看板自动刷新：每 5 分钟</span>
          <span class="clpm-status-footer__divider">·</span>
          <span>实时自控率：每 60 秒</span>
          <span class="clpm-status-footer__divider">·</span>
          <span>对象：{{ selectedPlantNodeName }}</span>
        </div>
      </div>
    </div>

    <!-- 保存筛选预设 Modal -->
    <Modal
      v-model:open="presetModalVisible"
      title="保存筛选预设"
      :footer="null"
      destroy-on-close
      width="400px"
    >
      <div class="flex flex-col gap-3">
        <Input
          v-model:value="presetName"
          placeholder="请输入预设名称"
          @press-enter="confirmSavePreset"
        />
        <div class="flex justify-end gap-2">
          <Button @click="presetModalVisible = false">取消</Button>
          <Button type="primary" @click="confirmSavePreset">确定</Button>
        </div>
      </div>
    </Modal>
  </Page>
</template>

<style scoped>
.clpm-kpi-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

.clpm-kpi-card {
  display: flex;
  flex-direction: column;
  min-height: 140px;
}

.clpm-kpi-value {
  display: flex;
  align-items: baseline;
  gap: 4px;
  margin-top: 6px;
}

.clpm-kpi-number {
  font-size: 32px;
  font-weight: 700;
  line-height: 1.1;
  font-variant-numeric: tabular-nums;
  transition: color 300ms ease-out;
}

.clpm-kpi-unit {
  font-size: 14px;
  color: hsl(var(--muted-foreground));
}

.clpm-kpi-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 8px;
}

.clpm-chart-grid {
  display: grid;
  grid-template-columns: 3fr 2fr;
  gap: 12px;
}

.clpm-chart-card {
  display: flex;
  flex-direction: column;
}

.clpm-preset-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
  padding: 6px 12px;
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border));
  border-radius: calc(var(--radius) * 1px);
}

.clpm-status-footer {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  padding: 8px 12px;
  font-size: 12px;
  color: hsl(var(--muted-foreground));
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border));
  border-radius: calc(var(--radius) * 1px);
}

.clpm-status-footer__divider {
  color: hsl(var(--border));
}

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

:deep(.clpm-row-selected) td {
  background-color: hsl(var(--primary) / 8%) !important;
}

@media (max-width: 1280px) {
  .clpm-kpi-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 1024px) {
  .clpm-kpi-grid {
    grid-template-columns: 1fr;
  }

  .clpm-chart-grid {
    grid-template-columns: 1fr;
  }
}
</style>
