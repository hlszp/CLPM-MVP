<script lang="ts" setup>
/**
 * 性能看板（FE-06 重构 + B2.4 深化）
 *
 * 对齐 UI/UX v4.1 §6.1.1 + §8.5 + PRD §4.3 + IDS v3.2 §2.3
 * - 左侧：工厂树导航
 * - 顶部：PageToolbar（时间窗 + 刷新 + 导出，带图标）
 * - 摘要条：ObjectSummaryBar（综合评分 primaryItem + actions）
 * - 上行三列：综合健康仪表盘 + 核心 Bullet + 数据质量环形图
 * - 中行：实时自控率仪表盘 + 整点 KpiStrip
 * - 下行：平稳率趋势 + 详细列表
 * - StatusFooter：最近刷新/数据延迟/自动刷新状态/对象
 * - 5 分钟自动刷新
 */
import type { TableColumnsType, TablePaginationConfig } from 'ant-design-vue';

import type { EchartsUIType } from '@vben/plugins/echarts';

import type { KpiStatus, MetricApi, TimeWindow } from '#/api/metric';
import type { PlantNodeApi } from '#/api/plant-node';

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
  Input,
  message,
  Modal,
  Select,
  Table,
  Tag,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import {
  getBoardApi,
  getNodeSnapshotApi,
  getNodesOverviewApi,
  getRankingApi,
  getRealtimeAutoRateApi,
} from '#/api/metric';
import { ClpmDataCanvas, ClpmKpiStrip, ClpmObjectSummaryBar, ClpmPageToolbar, ClpmToolbarButton } from '#/components/clpm';
import type { KpiStripItem, SummaryAction, SummaryItem } from '#/components/clpm';
import PlantNodeTree from '#/components/plant-node/plant-node-tree.vue';
import AutoRateGauge from '#/components/metric/auto-rate-gauge.vue';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import { usePagePreference } from '#/composables/use-clpm-preferences';
import type { FilterPreset } from '#/composables/use-clpm-preferences';

defineOptions({ name: 'MetricDashboard' });

const {
  isDark,
  themeColors,
  chartTextColor,
  chartTrackColor,
  chartMarkLineColor,
} = useClpmTheme();

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
const realtimeAutoRate = ref<MetricApi.RealtimeAutoRateResult | null>(null);
const realtimeAutoRateLoading = ref(false);

// ===== 节点级 KPI 数据（P0 B2 修复：接入节点级 API）=====
// 选中具体节点时：节点最新快照（含 score/loopCount/autoLoopRatio）
// 未选中（全厂）时：全厂总览（含多节点汇总与状态分布）
const nodeSnapshot = ref<MetricApi.NodeSnapshotItem | null>(null);
const nodeOverview = ref<MetricApi.NodeOverviewData | null>(null);
const nodeLoading = ref(false);

/** 节点级回路数（选中节点时为该节点回路数，全厂时为总回路数） */
const nodeLoopCount = computed(() => {
  if (nodeSnapshot.value) return nodeSnapshot.value.loopCount;
  if (nodeOverview.value) {
    return nodeOverview.value.nodes.reduce((sum, n) => sum + n.loopCount, 0);
  }
  return 0;
});

/** 节点级自控率（优先节点快照 realtimeAutoRate，回退 autoLoopRatio） */
const nodeAutoLoopRatio = computed(() => {
  if (nodeSnapshot.value) {
    return (
      nodeSnapshot.value.realtimeAutoRate ??
      nodeSnapshot.value.autoLoopRatio ??
      null
    );
  }
  if (nodeOverview.value && nodeOverview.value.nodesWithSnapshot > 0) {
    const withRate = nodeOverview.value.nodes.filter(
      (n) => n.autoLoopRatio != null,
    );
    if (withRate.length > 0) {
      return (
        withRate.reduce((sum, n) => sum + (n.autoLoopRatio ?? 0), 0) /
        withRate.length
      );
    }
  }
  return null;
});

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

const statusLabelMap: Record<KpiStatus, string> = {
  SUCCESS: '良好',
  INCONCLUSIVE: '不确定',
  PARTIAL: '部分',
};

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
  const readAt = realtimeAutoRate.value?.readAt;
  if (!readAt) return '';
  const diff = dayjs().diff(dayjs(readAt), 'minute');
  if (diff < 1) return '<1m';
  if (diff < 60) return `${diff}m`;
  return `${Math.floor(diff / 60)}h`;
});

// ===== 详细列表（低效排行） =====
const rankingLoading = ref(false);
const rankingList = ref<MetricApi.RankingItem[]>([]);
const rankingTotal = ref(0);
const rankingQuery = reactive({
  level: undefined as 1 | 2 | 3 | undefined,
  keyword: '',
  page: 1,
  pageSize: 10,
});

const levelOptions = [
  { label: '全部', value: undefined },
  { label: '1 级', value: 1 },
  { label: '2 级', value: 2 },
  { label: '3 级', value: 3 },
];

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
    title: '装置',
    dataIndex: 'unitName',
    key: 'unitName',
    width: 140,
    ellipsis: true,
  },
  {
    title: '综合评分',
    dataIndex: 'compositeScore',
    key: 'compositeScore',
    width: 100,
    align: 'right',
  },
  {
    title: '自控率',
    dataIndex: 'autoModeRate',
    key: 'autoModeRate',
    width: 90,
    align: 'right',
  },
  {
    title: '平稳率',
    dataIndex: 'steadyRate',
    key: 'steadyRate',
    width: 90,
    align: 'right',
  },
  {
    title: '状态',
    dataIndex: 'status',
    key: 'status',
    width: 90,
    align: 'center',
  },
];

const kpiStripItems = computed<KpiStripItem[]>(() =>
  (boardData.value?.kpiCards || []).map((card) => ({
    key: card.metricKey,
    label: card.metricName,
    status:
      card.status === 'SUCCESS'
        ? 'success'
        : card.status === 'PARTIAL'
          ? 'warning'
          : 'neutral',
    unit: card.unit,
    value: card.value?.toFixed(1) ?? '--',
  })),
);

// ===== ObjectSummaryBar 派生 =====
const compositeScore = computed(
  () => boardData.value?.kpiSummary.composite_score ?? 0,
);

const primaryItem = computed<SummaryItem | null>(() => {
  if (!boardData.value) return null;
  const score = compositeScore.value;
  return {
    key: 'composite',
    label: '综合评分',
    value: score.toFixed(1),
    status: score >= 80 ? 'success' : score >= 60 ? 'warning' : 'danger',
  };
});

const summaryItems = computed<SummaryItem[]>(() => {
  if (!boardData.value) return [];
  const k = boardData.value.kpiSummary;
  const items: SummaryItem[] = [
    {
      key: 'status',
      label: 'KPI 状态',
      value: statusLabelMap[k.status] ?? k.status,
      status:
        k.status === 'SUCCESS'
          ? 'success'
          : k.status === 'PARTIAL'
            ? 'warning'
            : 'neutral',
    },
    {
      key: 'partial',
      label: '不确定回路',
      value: `${boardData.value.partialWarning.inconclusiveCount} 个`,
      status:
        boardData.value.partialWarning.inconclusiveCount > 0
          ? 'warning'
          : 'success',
    },
    {
      key: 'algo',
      label: '算法版本',
      value: k.algorithm_version,
      status: 'neutral',
    },
  ];

  // 节点级 KPI 汇总（P0 B2 修复：展示节点级聚合数据）
  if (nodeLoopCount.value > 0) {
    items.push({
      key: 'nodeLoops',
      label: `${selectedPlantNodeName.value}回路数`,
      value: `${nodeLoopCount.value} 个`,
      status: 'neutral',
    });
  }
  if (nodeAutoLoopRatio.value != null) {
    items.push({
      key: 'nodeAutoRate',
      label: `${selectedPlantNodeName.value}自控率`,
      value: `${(nodeAutoLoopRatio.value * 100).toFixed(1)}%`,
      status:
        nodeAutoLoopRatio.value >= 0.9
          ? 'success'
          : nodeAutoLoopRatio.value >= 0.7
            ? 'warning'
            : 'neutral',
    });
  }
  return items;
});

const summaryActions = computed<SummaryAction[]>(() => [
  {
    key: 'ranking',
    label: '查看排行',
    icon: 'ant-design:bar-chart-outlined',
    type: 'default',
  },
  {
    key: 'statistics',
    label: '统计分析',
    icon: 'ant-design:line-chart-outlined',
    type: 'primary',
  },
]);

function onSummaryAction(key: string) {
  if (key === 'ranking') {
    router.push('/metric/ranking');
  } else if (key === 'statistics') {
    router.push('/metric/statistics');
  }
}

/** 数据质量摘要（基于 good_value_rate 推导） */
const dataQualitySummary = computed(() => {
  const rate = boardData.value?.kpiSummary.good_value_rate ?? 0;
  const good = rate;
  const bad = (100 - rate) / 2;
  const uncertain = 100 - rate - bad;
  return { bad, good, uncertain, validRate: rate };
});

// ECharts 趋势图
const trendChartRef = ref<EchartsUIType>();
const { renderEcharts: renderTrend, getChartInstance: getTrendInstance } =
  useEcharts(trendChartRef);

// ===== D2 多图联动：趋势图 → 排行表 =====
/** 趋势图选中的时间点 */
const selectedTrendTime = ref<string | null>(null);
/** 排行表中高亮的回路 ID */
const selectedLoopId = ref<string | null>(null);

// 综合健康仪表盘
const healthGaugeRef = ref<EchartsUIType>();
const { renderEcharts: renderHealthGaugeEcharts } = useEcharts(healthGaugeRef);

// 核心 Bullet Chart
const bulletRef = ref<EchartsUIType>();
const { renderEcharts: renderBulletEcharts } = useEcharts(bulletRef);

// 数据质量环形图
const qualityDonutRef = ref<EchartsUIType>();
const { renderEcharts: renderQualityDonutEcharts } =
  useEcharts(qualityDonutRef);

// 自动刷新
const REFRESH_INTERVAL = 5 * 60 * 1000;
let refreshTimer: null | ReturnType<typeof setInterval> = null;

/** 加载看板数据 */
async function loadBoard() {
  loading.value = true;
  try {
    const data = await getBoardApi({
      plantNodeId: selectedPlantNodeId.value,
      timeWindow: filter.timeWindow,
    });
    boardData.value = data;
    await nextTick();
    renderTrendChart();
    renderHealthGauge();
    renderBulletChart();
    renderQualityDonut();
  } catch {
    // 错误已由拦截器处理
  } finally {
    loading.value = false;
    lastRefreshAt.value = new Date();
  }
}

/** 加载实时自控率 */
async function loadRealtimeAutoRate() {
  realtimeAutoRateLoading.value = true;
  try {
    const data = await getRealtimeAutoRateApi({
      plantNodeId: selectedPlantNodeId.value,
    });
    realtimeAutoRate.value = data;
  } catch {
    // 错误已由拦截器处理
  } finally {
    realtimeAutoRateLoading.value = false;
  }
}

/**
 * 加载节点级 KPI 数据（P0 B2 修复）
 *
 * 选中具体节点时调用 getNodeSnapshotApi 获取该节点最新快照，
 * 未选中（全厂）时调用 getNodesOverviewApi 获取全厂总览。
 * 两个 API 互斥调用，避免冗余请求。
 */
async function loadNodeSnapshot() {
  nodeLoading.value = true;
  try {
    if (selectedPlantNodeId.value) {
      // 选中具体节点：获取节点最新快照
      nodeSnapshot.value = await getNodeSnapshotApi(selectedPlantNodeId.value);
      nodeOverview.value = null;
    } else {
      // 全厂：获取总览
      nodeSnapshot.value = null;
      nodeOverview.value = await getNodesOverviewApi({
        timeWindow: filter.timeWindow,
      });
    }
  } catch {
    // 节点级 KPI 为增强信息，失败不影响主看板，静默处理
    nodeSnapshot.value = null;
    nodeOverview.value = null;
  } finally {
    nodeLoading.value = false;
  }
}

/** 加载低效排行 */
async function loadRanking() {
  rankingLoading.value = true;
  try {
    const data = await getRankingApi({
      plantNodeId: selectedPlantNodeId.value,
      timeWindow: filter.timeWindow,
      sortBy: 'compositeScore',
      sortOrder: 'asc',
      limit: rankingQuery.pageSize * rankingQuery.page,
    });
    let items = data || [];
    // 关键字过滤
    if (rankingQuery.keyword) {
      const kw = rankingQuery.keyword.toLowerCase();
      items = items.filter(
        (it) =>
          it.tagName.toLowerCase().includes(kw) ||
          it.unitName?.toLowerCase().includes(kw),
      );
    }
    rankingTotal.value = items.length;
    const start = (rankingQuery.page - 1) * rankingQuery.pageSize;
    rankingList.value = items.slice(start, start + rankingQuery.pageSize);
  } catch {
    // 错误已由拦截器处理
  } finally {
    rankingLoading.value = false;
  }
}

function loadAll() {
  loadBoard();
  loadRealtimeAutoRate();
  loadRanking();
  loadNodeSnapshot();
}

function handleRankingSearch() {
  rankingQuery.page = 1;
  loadRanking();
}

function handleRankingTableChange(pagination: TablePaginationConfig) {
  rankingQuery.page = pagination.current || 1;
  rankingQuery.pageSize = pagination.pageSize || 10;
  loadRanking();
}

function renderTrendChart() {
  const trend = boardData.value?.steadyRateTrend;
  if (!trend || !trend.timestamps || trend.timestamps.length === 0) return;

  // D2 联动：选中时间点 markLine
  const selTs = selectedTrendTime.value;

  renderTrend({
    grid: { bottom: 30, containLabel: true, left: '2%', right: '2%', top: 40 },
    legend: { data: ['平稳率'], top: 5 },
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
        name: '平稳率',
        smooth: true,
        type: 'line',
      },
    ],
    tooltip: {
      axisPointer: { type: 'cross' },
      trigger: 'axis',
      valueFormatter: (val) =>
        val === null || val === undefined ? '—' : `${Number(val).toFixed(1)}%`,
    },
    xAxis: {
      axisLabel: {
        formatter: (val: string) => {
          try {
            // 强制北京时间（UTC+8）：+8h 后用 getUTC* 方法
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
    yAxis: {
      axisLabel: { formatter: '{value}%' },
      max: 100,
      min: 0,
      type: 'value',
    },
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
  // 同一 zr 实例已绑定，避免重复
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

/** D2 联动：趋势图选中时间点 → 排行表高亮评分最低回路 */
function onTrendTimeSelect(timestamp: string) {
  selectedTrendTime.value = timestamp;
  // 排行表已按 compositeScore 升序排列，第一项为评分最低回路
  if (rankingList.value.length > 0) {
    const lowest = rankingList.value[0]!;
    selectedLoopId.value = lowest.loopId;
    // 滚动到选中行
    nextTick(() => scrollToSelectedRow());
  }
}

/** D2 联动：清除选中 */
function clearTrendSelection() {
  selectedTrendTime.value = null;
  selectedLoopId.value = null;
}

/** 滚动到排行表选中行 */
function scrollToSelectedRow() {
  if (!selectedLoopId.value) return;
  const row = document.querySelector(
    `tr[data-loop-id="${selectedLoopId.value}"]`,
  );
  if (row) {
    row.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
}

/** 排行表行样式：高亮选中回路 */
function rankingRowClassName(record: MetricApi.RankingItem): string {
  return record.loopId === selectedLoopId.value ? 'clpm-row-selected' : '';
}

/** 排行表 customRow：附加 data-loop-id 便于 DOM 查询 */
function rankingCustomRow(record: MetricApi.RankingItem): any {
  return {
    'data-loop-id': record.loopId,
  };
}

/** 格式化选中时间戳为可读字符串 */
function formatSelectedTime(ts: string): string {
  try {
    // 强制北京时间（UTC+8）：+8h 后用 getUTC* 方法
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

/** 综合健康仪表盘（半圆 Gauge） */
function renderHealthGauge() {
  const score = compositeScore.value;
  renderHealthGaugeEcharts({
    series: [
      {
        axisLine: {
          lineStyle: {
            color: [
              [0.6, themeColors.value.DANGER],
              [0.8, themeColors.value.WARNING],
              [1, themeColors.value.SUCCESS],
            ],
            width: 18,
          },
        },
        axisTick: { show: false },
        data: [{ name: '综合健康', value: score }],
        detail: {
          fontSize: 28,
          fontWeight: 700,
          formatter: '{value}',
          offsetCenter: [0, '50%'],
        },
        endAngle: 0,
        max: 100,
        min: 0,
        pointer: { itemStyle: { color: 'auto' } },
        progress: { show: true, width: 18 },
        radius: '95%',
        splitLine: { length: 18 },
        startAngle: 180,
        title: { fontSize: 14, offsetCenter: [0, '80%'] },
        type: 'gauge',
      },
    ],
  });
}

/** 核心 Bullet Chart（稳定率/好值率/快速率） */
function renderBulletChart() {
  const k = boardData.value?.kpiSummary;
  if (!k) return;
  const metrics = [
    { name: '稳定率', value: k.steady_rate ?? 0, target: 80 },
    { name: '好值率', value: k.good_value_rate ?? 0, target: 95 },
    { name: '快速率', value: k.fast_response_rate ?? 0, target: 80 },
  ];
  renderBulletEcharts({
    grid: { bottom: 24, containLabel: true, left: 48, right: 24, top: 16 },
    series: [
      {
        data: metrics.map((m) => ({
          name: m.name,
          symbol: 'roundRect',
          symbolKeepAspect: true,
          symbolSize: [18, 14],
          symbolOffset: [0, 0],
          type: 'custom',
          value: m.value,
          itemStyle: {
            color:
              m.value >= m.target
                ? themeColors.value.SUCCESS
                : m.value >= m.target - 20
                  ? themeColors.value.WARNING
                  : themeColors.value.DANGER,
          },
        })),
        type: 'custom',
        encode: { x: -1, y: -1 },
        renderItem: (_params: any, api: any) => {
          const idx = api.value(0);
          const val = api.value(1);
          const target = api.value(2);
          const yStart = api.coord([0, idx]);
          const yEnd = api.coord([0, idx + 1]);
          const valEnd = api.coord([val, idx]);
          const targetPos = api.coord([target, idx]);
          const barHeight = (yStart[1] - yEnd[1]) * 0.55;
          const barY = yEnd[1] + (yStart[1] - yEnd[1] - barHeight) / 2;
          return {
            type: 'group',
            children: [
              // 背景 track
              {
                shape: {
                  height: barHeight,
                  width: api.coord([100, 0])[0] - api.coord([0, 0])[0],
                  x: api.coord([0, 0])[0],
                  y: barY,
                },
                style: { fill: chartTrackColor.value },
                type: 'rect',
              },
              // 实际值
              {
                shape: {
                  height: barHeight,
                  width: valEnd[0] - api.coord([0, 0])[0],
                  x: api.coord([0, 0])[0],
                  y: barY,
                },
                style: {
                  fill:
                    val >= target
                      ? themeColors.value.SUCCESS
                      : val >= target - 20
                        ? themeColors.value.WARNING
                        : themeColors.value.DANGER,
                },
                type: 'rect',
              },
              // 目标线
              {
                shape: {
                  height: barHeight + 6,
                  width: 2,
                  x: targetPos[0] - 1,
                  y: barY - 3,
                },
                style: { fill: chartMarkLineColor.value },
                type: 'rect',
              },
            ],
          };
        },
      },
    ],
    tooltip: {
      formatter: (p: any) => {
        const m = metrics[p.dataIndex];
        if (!m) return '';
        return `${m.name}: ${m.value.toFixed(1)}% (目标 ${m.target}%)`;
      },
      trigger: 'item',
    },
    xAxis: { max: 100, min: 0, show: false, type: 'value' },
    yAxis: {
      axisLabel: { fontSize: 12 },
      data: metrics.map((m) => m.name),
      type: 'category',
    },
  });
}

/** 数据质量环形图 */
function renderQualityDonut() {
  const q = dataQualitySummary.value;
  renderQualityDonutEcharts({
    color: [
      themeColors.value.SUCCESS,
      themeColors.value.DANGER,
      themeColors.value.NEUTRAL,
    ],
    legend: {
      bottom: 0,
      data: ['Good', 'Bad', 'Uncertain'],
      icon: 'circle',
      itemHeight: 8,
      itemWidth: 8,
      textStyle: { fontSize: 11 },
    },
    series: [
      {
        avoidLabelOverlap: false,
        center: ['50%', '45%'],
        data: [
          { value: q.good, name: 'Good' },
          { value: q.bad, name: 'Bad' },
          { value: q.uncertain, name: 'Uncertain' },
        ],
        label: {
          position: 'center',
          formatter: `{a|${q.validRate.toFixed(1)}%}\n{b|好值率}`,
          rich: {
            a: {
              color: themeColors.value.SUCCESS,
              fontSize: 22,
              fontWeight: 700,
              lineHeight: 28,
            },
            b: { color: chartTextColor.value, fontSize: 12, lineHeight: 18 },
          },
          show: true,
        },
        labelLine: { show: false },
        name: '数据质量',
        radius: ['55%', '78%'],
        type: 'pie',
      },
    ],
    tooltip: {
      formatter: '{b}: {c} ({d}%)',
      trigger: 'item',
    },
  });
}

function startAutoRefresh() {
  stopAutoRefresh();
  refreshTimer = setInterval(() => {
    loadAll();
  }, REFRESH_INTERVAL);
}

function stopAutoRefresh() {
  if (refreshTimer) {
    clearInterval(refreshTimer);
    refreshTimer = null;
  }
}

function handleTimeWindowChange() {
  loadAll();
}

/** 导出（占位） */
function handleExport() {
  message.info('KPI 看板导出功能待后端接口支持');
}

function formatPercent(val: number | undefined): string {
  if (val === null || val === undefined || Number.isNaN(val)) return '—';
  return `${Number(val).toFixed(1)}%`;
}

function scoreColor(score: number): string {
  if (score >= 80) return themeColors.value.SUCCESS;
  if (score >= 60) return themeColors.value.WARNING;
  return themeColors.value.DANGER;
}

watch(
  () => boardData.value?.steadyRateTrend,
  () => renderTrendChart(),
  { deep: true },
);

// D2 联动：选中时间变化时重渲趋势图（更新 markLine）
watch(selectedTrendTime, () => {
  nextTick(() => renderTrendChart());
});

// ===== 主题切换重渲图表 =====
watch(isDark, () => {
  nextTick(() => {
    renderTrendChart();
    renderHealthGauge();
    renderBulletChart();
    renderQualityDonut();
  });
});

// ===== 偏好持久化 =====

/** 保存默认时间窗 */
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
    level: rankingQuery.level,
    keyword: rankingQuery.keyword,
  });
  presetModalVisible.value = false;
  message.success('预设已保存');
}

function handleApplyPreset(preset: FilterPreset) {
  const f = preset.filters;
  if (f.timeWindow) {
    filter.timeWindow = f.timeWindow as TimeWindow;
  }
  rankingQuery.level = f.level;
  rankingQuery.keyword = f.keyword ?? '';
  rankingQuery.page = 1;
  loadAll();
  message.success(`已应用预设：${preset.name}`);
}

function handleDeletePreset(id: string) {
  deleteFilterPreset(id);
  message.success('预设已删除');
}

/** 重置页面偏好 */
function handleResetPreferences() {
  resetPreferences();
  filter.timeWindow = 'today' as TimeWindow;
  message.success('页面偏好已重置');
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
    <Alert
      v-if="boardData?.partialWarning?.active"
      class="mb-3"
      type="warning"
      show-icon
      :message="boardData.partialWarning.message || '存在部分回路数据不完整'"
      :description="`不确定回路 ${boardData.partialWarning.inconclusiveCount} 个，部分关联 ${boardData.partialWarning.partialCount} 个`"
    />

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
            <ClpmToolbarButton
              icon="refresh"
              label="刷新"
              :loading="loading"
              @click="loadAll"
            />
            <ClpmToolbarButton
              icon="export"
              label="导出"
              @click="handleExport"
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

        <!-- ObjectSummaryBar：综合评分 primaryItem + actions -->
        <ClpmObjectSummaryBar
          v-if="boardData"
          :title="selectedPlantNodeName"
          :subtitle="`${timeWindowOptions.find((o) => o.value === filter.timeWindow)?.label ?? '今天'} · 性能总览`"
          :items="summaryItems"
          :primary-item="primaryItem"
          :actions="summaryActions"
          @action="onSummaryAction"
        />

        <!-- 上行三列：综合健康仪表盘 + 核心 Bullet + 数据质量环形图 -->
        <div class="clpm-top-grid">
          <Card size="small" title="综合健康仪表盘" class="clpm-chart-card">
            <EchartsUI ref="healthGaugeRef" height="200px" />
          </Card>
          <Card size="small" title="核心指标 Bullet" class="clpm-chart-card">
            <EchartsUI ref="bulletRef" height="200px" />
          </Card>
          <Card size="small" title="数据质量摘要" class="clpm-chart-card">
            <EchartsUI ref="qualityDonutRef" height="200px" />
          </Card>
        </div>

        <!-- 中行：实时自控率仪表盘 + 整点 KPI -->
        <div class="grid grid-cols-1 gap-3 lg:grid-cols-3">
          <div class="lg:col-span-1">
            <AutoRateGauge
              :auto-count="realtimeAutoRate?.autoCount ?? 0"
              :manual-count="realtimeAutoRate?.manualCount ?? 0"
              :loading="realtimeAutoRateLoading"
              :subtitle="
                realtimeAutoRate?.readAt
                  ? // 强制北京时间（UTC+8）
                    `统计于 ${new Date(realtimeAutoRate.readAt).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })}`
                  : ''
              "
              height="220px"
            />
          </div>

          <ClpmDataCanvas
            class="lg:col-span-2"
            title="整点 KPI"
            description="核心指标按当前时间窗聚合展示，支持部分有效和不确定状态。"
            :loading="loading"
          >
            <ClpmKpiStrip :items="kpiStripItems" :loading="loading" />
          </ClpmDataCanvas>
        </div>

        <ClpmDataCanvas title="平稳率趋势" :loading="loading">
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
        </ClpmDataCanvas>

        <ClpmDataCanvas title="详细列表" :loading="rankingLoading">
          <template #extra>
            <Select
              v-model:value="rankingQuery.level"
              placeholder="等级筛选"
              style="width: 120px"
              size="small"
              allow-clear
              :options="levelOptions"
              @change="handleRankingSearch"
            />
            <Input
              v-model:value="rankingQuery.keyword"
              placeholder="搜索位号/装置"
              allow-clear
              size="small"
              style="width: 220px"
              @press-enter="handleRankingSearch"
            />
            <ClpmToolbarButton
              icon="search"
              label="查询"
              @click="handleRankingSearch"
            />
          </template>
          <Table
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
                    ['red', 'orange', 'gold'][record.rank - 1] ?? 'default'
                  "
                  class="m-0"
                >
                  {{ record.rank }}
                </Tag>
                <span v-else>{{ record.rank }}</span>
              </template>
              <template v-else-if="column.key === 'compositeScore'">
                <span
                  class="font-mono font-bold"
                  :style="{ color: scoreColor(record.compositeScore) }"
                >
                  {{ Number(record.compositeScore).toFixed(1) }}
                </span>
              </template>
              <template v-else-if="column.key === 'autoModeRate'">
                <span class="font-mono text-xs">
                  {{ formatPercent(record.autoModeRate) }}
                </span>
              </template>
              <template v-else-if="column.key === 'steadyRate'">
                <span class="font-mono text-xs">
                  {{ formatPercent(record.steadyRate) }}
                </span>
              </template>
              <template v-else-if="column.key === 'status'">
                <Tag
                  :color="
                    record.status === 'SUCCESS'
                      ? 'green'
                      : record.status === 'PARTIAL'
                        ? 'orange'
                        : 'default'
                  "
                  class="m-0"
                >
                  {{
                    statusLabelMap[record.status as KpiStatus] || record.status
                  }}
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
          <span>自动刷新：每 5 分钟</span>
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
.clpm-top-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
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

.clpm-chart-card {
  display: flex;
  flex-direction: column;
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

/* D2 多图联动状态指示条 */
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

/* D2 多图联动：排行表选中行高亮 */
:deep(.clpm-row-selected) td {
  background-color: hsl(var(--primary) / 8%) !important;
}

@media (max-width: 1024px) {
  .clpm-top-grid {
    grid-template-columns: 1fr;
  }
}
</style>
