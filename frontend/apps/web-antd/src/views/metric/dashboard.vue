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

import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue';
import { useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';
import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import {
  Alert,
  Card,
  Input,
  message,
  Select,
  Table,
  Tag,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import {
  getBoardApi,
  getRankingApi,
  getRealtimeAutoRateApi,
} from '#/api/metric';
import {
  ClpmDataCanvas,
  ClpmKpiStrip,
  ClpmObjectSummaryBar,
  ClpmPageToolbar,
  ClpmToolbarButton,
  type KpiStripItem,
  type SummaryAction,
  type SummaryItem,
} from '#/components/clpm';
import PlantNodeTree from '#/components/plant-node/plant-node-tree.vue';
import AutoRateGauge from '#/components/metric/auto-rate-gauge.vue';
import { THEME_COLORS } from '#/preferences';

defineOptions({ name: 'MetricDashboard' });

const router = useRouter();

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

const timeWindowOptions = [
  { label: '今天', value: 'today' },
  { label: '昨天', value: 'yesterday' },
  { label: '近 7 天', value: 'last_7_days' },
  { label: '近 30 天', value: 'last_30_days' },
];

const filter = reactive({
  timeWindow: 'today' as TimeWindow,
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
    status:
      score >= 80 ? 'success' : score >= 60 ? 'warning' : 'danger',
  };
});

const summaryItems = computed<SummaryItem[]>(() => {
  if (!boardData.value) return [];
  const k = boardData.value.kpiSummary;
  return [
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
const { renderEcharts: renderTrend } = useEcharts(trendChartRef);

// 综合健康仪表盘
const healthGaugeRef = ref<EchartsUIType>();
const { renderEcharts: renderHealthGaugeEcharts } = useEcharts(healthGaugeRef);

// 核心 Bullet Chart
const bulletRef = ref<EchartsUIType>();
const { renderEcharts: renderBulletEcharts } = useEcharts(bulletRef);

// 数据质量环形图
const qualityDonutRef = ref<EchartsUIType>();
const { renderEcharts: renderQualityDonutEcharts } = useEcharts(qualityDonutRef);

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
  renderTrend({
    grid: { bottom: 30, containLabel: true, left: '2%', right: '2%', top: 40 },
    legend: { data: ['平稳率'], top: 5 },
    series: [
      {
        areaStyle: { opacity: 0.15 },
        data: trend.values,
        itemStyle: { color: '#0D6EFD' },
        lineStyle: { width: 2 },
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
            const d = new Date(val);
            const mm = String(d.getMonth() + 1).padStart(2, '0');
            const dd = String(d.getDate()).padStart(2, '0');
            const hh = String(d.getHours()).padStart(2, '0');
            const mi = String(d.getMinutes()).padStart(2, '0');
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
  });
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
              [0.6, THEME_COLORS.DANGER],
              [0.8, THEME_COLORS.WARNING],
              [1, THEME_COLORS.SUCCESS],
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
                ? THEME_COLORS.SUCCESS
                : m.value >= m.target - 20
                  ? THEME_COLORS.WARNING
                  : THEME_COLORS.DANGER,
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
          const barY = yEnd[1] + ((yStart[1] - yEnd[1]) - barHeight) / 2;
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
                style: { fill: '#f0f0f0' },
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
                      ? THEME_COLORS.SUCCESS
                      : val >= target - 20
                        ? THEME_COLORS.WARNING
                        : THEME_COLORS.DANGER,
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
                style: { fill: '#475569' },
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
    color: [THEME_COLORS.SUCCESS, THEME_COLORS.DANGER, THEME_COLORS.NEUTRAL],
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
              color: THEME_COLORS.SUCCESS,
              fontSize: 22,
              fontWeight: 700,
              lineHeight: 28,
            },
            b: { color: '#8c8c8c', fontSize: 12, lineHeight: 18 },
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
  if (score >= 80) return '#198754';
  if (score >= 60) return '#ffc107';
  return '#dc3545';
}

watch(
  () => boardData.value?.steadyRateTrend,
  () => renderTrendChart(),
  { deep: true },
);

onMounted(() => {
  loadAll();
  startAutoRefresh();
});

onUnmounted(() => {
  stopAutoRefresh();
});
</script>

<template>
  <Page title="性能看板">
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
          title="性能驾驶舱"
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
          </template>
        </ClpmPageToolbar>

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
                  ? `统计于 ${new Date(realtimeAutoRate.readAt).toLocaleString('zh-CN')}`
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
            :row-key="(record: MetricApi.RankingItem) => record.loopId"
            :scroll="{ x: 720 }"
            size="small"
            @change="handleRankingTableChange"
          >
            <template #bodyCell="{ column, record }">
              <template v-if="column.key === 'rank'">
                <Tag
                  v-if="record.rank <= 3"
                  :color="['red', 'orange', 'gold'][record.rank - 1] ?? 'default'"
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
                  {{ statusLabelMap[record.status as KpiStatus] || record.status }}
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
  </Page>
</template>

<style scoped>
.clpm-top-grid {
  display: grid;
  gap: 12px;
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.clpm-chart-card {
  display: flex;
  flex-direction: column;
}

.clpm-status-footer {
  align-items: center;
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border));
  border-radius: calc(var(--radius) * 1px);
  color: hsl(var(--muted-foreground));
  display: flex;
  flex-wrap: wrap;
  font-size: 12px;
  gap: 8px;
  padding: 8px 12px;
}

.clpm-status-footer__divider {
  color: hsl(var(--border));
}

@media (max-width: 1024px) {
  .clpm-top-grid {
    grid-template-columns: 1fr;
  }
}
</style>
