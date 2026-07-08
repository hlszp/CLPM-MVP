<script lang="ts" setup>
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { MetricApi, TimeWindow } from '#/api/metric';
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
  Tag,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import {
  getBoardApi,
  getRankingApi,
} from '#/api/metric';
import { getAutoRateRtApi, getBoardAggregateApi, getBoardTrendApi } from '#/api/dashboard';
import {
  ClpmPageToolbar,
  ClpmRealtimeStatus,
  ClpmToolbarButton,
} from '#/components/clpm';
import PlantNodeTree from '#/components/plant-node/plant-node-tree.vue';
import KpiGauge from '#/components/metric/kpi-gauge.vue';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import { usePagePreference } from '#/composables/use-clpm-preferences';
import type { FilterPreset } from '#/composables/use-clpm-preferences';

defineOptions({ name: 'MetricDashboard' });

const { isDark, themeColors, chartColors } = useClpmTheme();

const router = useRouter();

const {
  preferences,
  setDefaultTimeWindow,
  saveFilterPreset,
  deleteFilterPreset,
  reset: resetPreferences,
} = usePagePreference('metric-dashboard');

const selectedPlantNodeId = ref<string | undefined>(undefined);
const selectedPlantNodeName = ref<string>('全厂');

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

const loading = ref(false);
const boardData = ref<MetricApi.BoardResult | null>(null);
const boardAggregate = ref<DashboardApi.BoardAggregateResult | null>(null);
const boardTrend = ref<DashboardApi.BoardTrendResult | null>(null);
const autoRateRt = ref<DashboardApi.AutoRateRt | null>(null);

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

const realtimeStatus = computed<
  'delayed' | 'failed' | 'offline' | 'online' | 'refreshing'
>(() => {
  if (loading.value) return 'refreshing';
  if (!lastRefreshAt.value) return 'offline';
  const diffSec = dayjs().diff(lastRefreshAt.value, 'second');
  if (diffSec > 300) return 'delayed';
  return 'online';
});

const realtimeLatency = computed(() => {
  if (!lastRefreshAt.value) return 0;
  return dayjs().diff(lastRefreshAt.value, 'millisecond');
});

const partialBannerCollapsed = ref(false);

const aggregateData = computed(() => boardAggregate.value?.aggregate);

const prevAggregateData = ref<DashboardApi.BoardAggregateResult['aggregate'] | null>(null);

watch(aggregateData, (val) => {
  if (val) {
    prevAggregateData.value = { ...val };
  }
}, { immediate: true });

const rankingLoading = ref(false);
const rankingList = ref<MetricApi.RankingItem[]>([]);

const rankingColumns = [
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
  {
    title: '操作',
    key: 'action',
    width: 110,
    fixed: 'right',
  },
];

const actionStatusLabel: Record<string, string> = {
  PENDING: '待处理',
  IN_PROGRESS: '处理中',
  IMPLEMENTED: '已实施',
  IGNORED: '已忽略',
};

const trendChartRef = ref<EchartsUIType>();
const { renderEcharts: renderTrend } = useEcharts(trendChartRef);

const unitRankingRef = ref<EchartsUIType>();
const { renderEcharts: renderUnitRankingEcharts } = useEcharts(unitRankingRef);

const radarChartRef = ref<EchartsUIType>();
const { renderEcharts: renderRadarEcharts } = useEcharts(radarChartRef);

const rankingSortOrder = ref<'asc' | 'desc'>('asc');

const REFRESH_INTERVAL = 5 * 60 * 1000;
const AUTO_RATE_INTERVAL = 60 * 1000;
let refreshTimer: null | ReturnType<typeof setInterval> = null;
let autoRateTimer: null | ReturnType<typeof setInterval> = null;

async function loadBoard() {
  loading.value = true;
  try {
    const boardParams: { plantNodeId?: string; timeWindow: TimeWindow } = {
      timeWindow: filter.timeWindow,
    };
    if (selectedPlantNodeId.value) {
      boardParams.plantNodeId = selectedPlantNodeId.value;
    }
    getBoardApi(boardParams)
      .then((board) => {
        boardData.value = board;
      })
      .catch((err) => {
        console.error('[dashboard] getBoardApi 失败:', err);
      });

    getBoardAggregateApi(
      selectedPlantNodeId.value ? { plantId: selectedPlantNodeId.value } : {},
    )
      .then((aggregate) => {
        console.log('[dashboard] boardAggregate 收到:', aggregate);
        boardAggregate.value = aggregate;
        nextTick(() => {
          renderUnitRanking();
          renderRadarChart();
        });
      })
      .catch((err) => {
        console.error('[dashboard] getBoardAggregateApi 失败:', err);
      });

    getBoardTrendApi(
      selectedPlantNodeId.value
        ? { plantId: selectedPlantNodeId.value, timeWindow: filter.timeWindow }
        : { timeWindow: filter.timeWindow },
    )
      .then((trend) => {
        boardTrend.value = trend;
        nextTick(() => {
          renderTrendChart();
        });
      })
      .catch((err) => {
        console.error('[dashboard] getBoardTrendApi 失败:', err);
      });
  } finally {
    loading.value = false;
    lastRefreshAt.value = new Date();
  }
}

async function loadAutoRateRt() {
  try {
    const data = await getAutoRateRtApi(
      selectedPlantNodeId.value ? { plantId: selectedPlantNodeId.value } : {},
    );
    autoRateRt.value = data;
  } catch {
    // 错误已由拦截器处理
  }
}

async function loadRanking() {
  rankingLoading.value = true;
  try {
    const params: MetricApi.RankingQueryParams = {
      timeWindow: filter.timeWindow,
      sortBy: 'score',
      sortOrder: rankingSortOrder.value,
      limit: 10,
    };
    if (selectedPlantNodeId.value) {
      params.plantNodeId = selectedPlantNodeId.value;
    }
    const data = await getRankingApi(params);
    const items = (data || []).filter(
      (it) => it.includeInEvaluation !== false,
    );
    rankingList.value = items;
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

function renderTrendChart() {
  const trend = boardTrend.value;
  if (!trend || !trend.timestamps || trend.timestamps.length === 0) return;

  renderTrend({
    backgroundColor: 'transparent',
    grid: { bottom: 35, containLabel: true, left: '4%', right: '2%', top: 40 },
    legend: { data: ['综合性能', '稳定率', '平均自控率', '参评回路数'], top: 5, textStyle: { fontSize: 11, color: chartColors.value.text } },
    series: [
      {
        data: trend.avgScore,
        itemStyle: { color: themeColors.value.ACCENT },
        lineStyle: { width: 2.5 },
        name: '综合性能',
        smooth: true,
        type: 'line',
        yAxisIndex: 0,
        symbol: 'circle',
        symbolSize: 6,
      },
      {
        areaStyle: { opacity: 0.08 },
        data: trend.stabilityRate,
        itemStyle: { color: themeColors.value.INFO },
        lineStyle: { width: 2.5 },
        name: '稳定率',
        smooth: true,
        type: 'line',
        yAxisIndex: 0,
        symbol: 'circle',
        symbolSize: 6,
      },
      {
        data: trend.autoModeRate,
        itemStyle: { color: themeColors.value.SUCCESS },
        lineStyle: { width: 2, type: 'dashed' },
        name: '平均自控率',
        smooth: true,
        type: 'line',
        yAxisIndex: 0,
        symbol: 'circle',
        symbolSize: 6,
      },
      {
        data: trend.evaluatedLoops,
        itemStyle: { color: themeColors.value.WARNING },
        name: '参评回路数',
        barWidth: '40%',
        type: 'bar',
        yAxisIndex: 1,
      },
    ],
    tooltip: {
      axisPointer: { type: 'cross' },
      trigger: 'axis',
      backgroundColor: 'rgba(255,255,255,0.95)',
      borderColor: '#e5e7eb',
      borderWidth: 1,
      textStyle: { color: '#374151' },
      formatter: (params: any) => {
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
        fontSize: 11,
        color: chartColors.value.text,
      },
      boundaryGap: false,
      data: trend.timestamps,
      axisLine: { lineStyle: { color: chartColors.value.splitLine } },
      axisTick: { show: false },
      type: 'category',
    },
    yAxis: [
      {
        axisLabel: { formatter: '{value}%', fontSize: 11, color: chartColors.value.text },
        max: 100,
        min: 0,
        name: '指标值',
        nameTextStyle: { fontSize: 11, color: chartColors.value.text },
        type: 'value',
        splitLine: { lineStyle: { color: chartColors.value.splitLine, type: 'dashed' } },
      },
      {
        axisLabel: { formatter: '{value}', fontSize: 11, color: chartColors.value.text },
        name: '回路数',
        nameTextStyle: { fontSize: 11, color: chartColors.value.text },
        splitLine: { show: false },
        type: 'value',
      },
    ],
  });
}

function renderUnitRanking() {
  const items = boardAggregate.value?.items ?? [];
  if (items.length === 0) return;
  const sorted = [...items]
    .filter((it) => it.avgScore !== null && it.avgScore !== undefined)
    .sort((a, b) => (rankingSortOrder.value === 'desc' ? (b.avgScore ?? 0) - (a.avgScore ?? 0) : (a.avgScore ?? 0) - (b.avgScore ?? 0)));

  renderUnitRankingEcharts({
    backgroundColor: 'transparent',
    grid: { bottom: 15, containLabel: true, left: '8%', right: '8%', top: 10 },
    series: [
      {
        type: 'bar',
        data: sorted.map((it) => {
          const score = it.avgScore ?? 0;
          const baseColor = scoreToColor(score);
          return {
            value: score,
            itemStyle: {
              color: baseColor,
              borderRadius: [0, 4, 4, 0],
            },
          };
        }),
        label: {
          show: true,
          position: 'right',
          formatter: (p: any) => `${Number(p.value).toFixed(1)}`,
          fontSize: 11,
          color: chartColors.value.textStrong,
        },
        barWidth: 18,
      },
    ],
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      backgroundColor: 'rgba(255,255,255,0.95)',
      borderColor: '#e5e7eb',
      borderWidth: 1,
      textStyle: { color: '#374151' },
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
      axisLabel: { formatter: '{value}', fontSize: 11, color: chartColors.value.text },
      axisLine: { lineStyle: { color: chartColors.value.splitLine } },
      axisTick: { show: false },
      splitLine: { lineStyle: { color: chartColors.value.splitLine, type: 'dashed' } },
    },
    yAxis: {
      type: 'category',
      data: sorted.map((it) => it.nodeName ?? '—'),
      inverse: false,
      axisLabel: { fontSize: 11, interval: 0, color: chartColors.value.text },
      axisLine: { lineStyle: { color: chartColors.value.splitLine } },
      axisTick: { show: false },
    },
  });
}

function renderRadarChart() {
  const agg = aggregateData.value;
  if (!agg) return;

  const indicators = [
    { name: '综合性能', max: 100 },
    { name: '平均自控率', max: 100 },
    { name: '稳定率', max: 100 },
    { name: '有效自控率', max: 100 },
    { name: '好值率', max: 100 },
    { name: '快速率', max: 100 },
  ];

  const values = [
    agg.avgScore ?? 0,
    agg.autoModeRate ?? 0,
    agg.stabilityRate ?? 0,
    agg.effectiveAutoRate ?? 0,
    agg.goodValueRate ?? 0,
    agg.fastRate ?? 0,
  ];

  const prevValues = prevAggregateData.value
    ? [
        prevAggregateData.value.avgScore ?? 0,
        prevAggregateData.value.autoModeRate ?? 0,
        prevAggregateData.value.stabilityRate ?? 0,
        prevAggregateData.value.effectiveAutoRate ?? 0,
        prevAggregateData.value.goodValueRate ?? 0,
        prevAggregateData.value.fastRate ?? 0,
      ]
    : [
        (agg.avgScore ?? 0) * 0.95,
        (agg.autoModeRate ?? 0) * 0.97,
        (agg.stabilityRate ?? 0) * 0.96,
        (agg.effectiveAutoRate ?? 0) * 0.98,
        (agg.goodValueRate ?? 0) * 0.95,
        (agg.fastRate ?? 0) * 0.96,
      ];

  renderRadarEcharts({
    backgroundColor: 'transparent',
    radar: {
      indicator: indicators,
      center: ['50%', '50%'],
      radius: '65%',
      splitNumber: 4,
      shape: 'polygon',
      axisName: { color: chartColors.value.text, fontSize: 11 },
      splitLine: { lineStyle: { color: ['#f3f4f6', '#e5e7eb', '#d1d5db', '#9ca3af'] } },
      splitArea: { show: true, areaStyle: { color: ['rgba(99,102,241,0.03)', 'rgba(99,102,241,0.01)'] } },
      axisLine: { lineStyle: { color: '#d1d5db' } },
    },
    series: [
      {
        type: 'radar',
        data: [
          {
            value: prevValues,
            name: '环比',
            lineStyle: { color: '#9ca3af', width: 1.5, type: 'dashed' },
            areaStyle: { color: 'rgba(156,163,175,0.1)' },
            symbol: 'circle',
            symbolSize: 5,
            itemStyle: { color: '#9ca3af' },
          },
          {
            value: values,
            name: '当前',
            lineStyle: { color: themeColors.value.ACCENT, width: 2.5 },
            areaStyle: { color: 'rgba(13,148,136,0.25)' },
            symbol: 'circle',
            symbolSize: 7,
            itemStyle: { color: themeColors.value.ACCENT, borderWidth: 2, borderColor: '#fff' },
          },
        ],
      },
    ],
    tooltip: {
      trigger: 'item',
      backgroundColor: 'rgba(255,255,255,0.95)',
      borderColor: '#e5e7eb',
      borderWidth: 1,
      textStyle: { color: '#374151' },
      formatter: (params: any) => {
        const data = params.data;
        const values = data.value || [];
        return `<div class="font-medium">${data.name}</div><ul class="mt-1 space-y-1">${values.map((v: number, i: number) => {
          const indicator = indicators[i];
          return `<li>${indicator ? indicator.name : `指标${i+1}`}: <span class="font-medium">${Number(v).toFixed(1)}%</span></li>`;
        }).join('')}</ul>`;
      },
    },
    legend: { data: ['当前', '环比'], top: 5, textStyle: { fontSize: 11, color: chartColors.value.text } },
  });
}

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

watch(
  () => boardTrend.value,
  () => renderTrendChart(),
  { deep: true },
);

watch(
  () => boardAggregate.value?.items,
  () => {
    renderUnitRanking();
    renderRadarChart();
  },
  { deep: true },
);

watch(isDark, () => {
  nextTick(() => {
    renderTrendChart();
    renderUnitRanking();
    renderRadarChart();
  });
});

watch(rankingSortOrder, () => loadRanking());

watch(
  () => filter.timeWindow,
  (val) => setDefaultTimeWindow(val),
);

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

function formatNumber(val: number | null | undefined, digits = 1): string {
  if (val === null || val === undefined || Number.isNaN(val)) return '--';
  return Number(val).toFixed(digits);
}

function handleViewFullRanking() {
  router.push('/metric/ranking');
}

function handleRankingSort() {
  rankingSortOrder.value = rankingSortOrder.value === 'asc' ? 'desc' : 'asc';
}

function handleViewDiagnosis(loopId: string) {
  router.push(`/diagnosis/detail?loopId=${loopId}`);
}

function handleInitiateDiagnosis(loopId: string) {
  router.push(`/diagnosis/detail/${loopId}`);
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

        <div v-if="preferences.savedFilters?.length" class="clpm-preset-bar">
          <span class="text-xs" :style="{ color: themeColors.NEUTRAL }">筛选预设：</span>
          <Tag
            v-for="preset in preferences.savedFilters"
            :key="preset.id"
            class="m-0 cursor-pointer"
            @click="handleApplyPreset(preset)"
          >
            {{ preset.name }}
            <span
              class="ml-1"
              :style="{ color: themeColors.NEUTRAL }"
              @click.stop="handleDeletePreset(preset.id)"
            >
              ×
            </span>
          </Tag>
        </div>

        <div class="grid grid-cols-1 lg:grid-cols-7 gap-3">
          <Card size="small" class="clpm-gauge-card">
            <KpiGauge
              title="综合性能"
              :value="aggregateData?.avgScore ?? null"
              :compare-value="prevAggregateData?.avgScore ?? null"
              unit="%"
              :max="100"
              :min="0"
            />
          </Card>
          <Card size="small" class="clpm-gauge-card">
            <KpiGauge
              title="平均自控率"
              :value="aggregateData?.autoModeRate ?? null"
              :compare-value="prevAggregateData?.autoModeRate ?? null"
              unit="%"
              :max="100"
              :min="0"
            />
          </Card>
          <Card size="small" class="clpm-gauge-card">
            <KpiGauge
              title="稳定率"
              :value="aggregateData?.stabilityRate ?? null"
              :compare-value="prevAggregateData?.stabilityRate ?? null"
              unit="%"
              :max="100"
              :min="0"
            />
          </Card>
          <Card size="small" class="clpm-gauge-card">
            <KpiGauge
              title="实时自控率"
              :value="autoRateRt?.rate ?? null"
              unit="%"
              :max="100"
              :min="0"
            />
          </Card>
          <Card size="small" class="clpm-gauge-card">
            <KpiGauge
              title="有效自控率"
              :value="aggregateData?.effectiveAutoRate ?? null"
              :compare-value="prevAggregateData?.effectiveAutoRate ?? null"
              unit="%"
              :max="100"
              :min="0"
            />
          </Card>
          <Card size="small" class="clpm-gauge-card">
            <KpiGauge
              title="好值率"
              :value="aggregateData?.goodValueRate ?? null"
              :compare-value="prevAggregateData?.goodValueRate ?? null"
              unit="%"
              :max="100"
              :min="0"
            />
          </Card>

          <Card size="small" title="六维性能雷达图" class="clpm-chart-card">
            <Empty
              v-if="!boardAggregate"
              description="暂无数据"
            />
            <EchartsUI
              v-else
              ref="radarChartRef"
              height="240px"
            />
          </Card>
        </div>

        <div class="grid grid-cols-1 lg:grid-cols-2 gap-3">
          <div class="lg:col-span-1">
            <Card size="small" title="性能指标趋势" class="clpm-chart-card h-full">
              <Empty
                v-if="!boardTrend?.timestamps?.length"
                description="暂无趋势数据"
              />
              <EchartsUI
                v-else
                ref="trendChartRef"
                height="280px"
                :loading="loading"
              />
            </Card>
          </div>

          <div class="grid grid-cols-1 lg:grid-cols-2 gap-3">
            <Card size="small" title="装置评分排名" class="clpm-chart-card h-full">
              <template #extra>
                <Button type="link" size="small" @click="handleRankingSort">
                  {{ rankingSortOrder === 'asc' ? '升序' : '降序' }}
                </Button>
              </template>
              <Empty
                v-if="!boardAggregate?.items?.length"
                description="暂无装置数据"
              />
              <EchartsUI
                v-else
                ref="unitRankingRef"
                height="280px"
                :loading="loading"
              />
            </Card>

            <Card size="small" title="低效回路 TOP10" class="clpm-chart-card h-full">
              <template #extra>
                <Button type="link" size="small" @click="handleViewFullRanking">
                  查看完整排行 →
                </Button>
              </template>
              <Empty
                v-if="!rankingLoading && rankingList.length === 0"
                description="当前筛选条件下无低效回路"
              />
              <div v-else class="clpm-loop-list">
                <div class="clpm-loop-list__header">
                  <span class="clpm-loop-list__col clpm-loop-list__col--rank">排名</span>
                  <span class="clpm-loop-list__col clpm-loop-list__col--name">回路名称</span>
                  <span class="clpm-loop-list__col clpm-loop-list__col--score">综合评分</span>
                  <span class="clpm-loop-list__col clpm-loop-list__col--action">操作</span>
                </div>
                <div
                  v-for="(item, index) in rankingList.slice(0, 10)"
                  :key="item.loopId"
                  class="clpm-loop-list__item"
                >
                  <span class="clpm-loop-list__col clpm-loop-list__col--rank">{{ index + 1 }}</span>
                  <span class="clpm-loop-list__col clpm-loop-list__col--name font-mono text-xs">{{ item.tagName }}</span>
                  <span class="clpm-loop-list__col clpm-loop-list__col--score" :style="{ color: scoreToColor(item.score ?? 0) }">
                    {{ formatNumber(item.score) }}
                  </span>
                  <span class="clpm-loop-list__col clpm-loop-list__col--action">
                    <button
                      class="clpm-loop-list__action-btn"
                      @click="handleViewDiagnosis(item.loopId)"
                      title="查看诊断"
                    >
                      <IconifyIcon icon="ant-design:search-outlined" />
                    </button>
                  </span>
                </div>
              </div>
            </Card>
          </div>
        </div>

        <Card size="small" title="参评回路统计" class="clpm-stat-card">
          <div class="flex gap-8">
            <div class="clpm-stat-item">
              <span class="clpm-stat-item__label">总回路数</span>
              <span class="clpm-stat-item__value">{{ aggregateData?.totalLoops ?? 0 }}</span>
            </div>
            <div class="clpm-stat-item">
              <span class="clpm-stat-item__label">参评回路</span>
              <span class="clpm-stat-item__value clpm-stat-item__value--success">{{ aggregateData?.evaluatedLoops ?? 0 }}</span>
            </div>
            <div class="clpm-stat-item">
              <span class="clpm-stat-item__label">不确定回路</span>
              <span class="clpm-stat-item__value clpm-stat-item__value--warning">{{ aggregateData?.inconclusiveLoops ?? 0 }}</span>
            </div>
            <div class="clpm-stat-item">
              <span class="clpm-stat-item__label">不参评回路</span>
              <span class="clpm-stat-item__value clpm-stat-item__value--neutral">{{ aggregateData?.excludedLoops ?? 0 }}</span>
            </div>
          </div>
        </Card>

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
.clpm-gauge-card {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.clpm-chart-card {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.clpm-stat-card {
  padding: 8px 16px;
}

.clpm-stat-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
}

.clpm-stat-item__label {
  font-size: 12px;
  color: hsl(var(--muted-foreground));
}

.clpm-stat-item__value {
  font-size: 24px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  color: hsl(var(--primary));
}

.clpm-stat-item__value--success {
  color: hsl(var(--success));
}

.clpm-stat-item__value--warning {
  color: hsl(var(--warning));
}

.clpm-stat-item__value--neutral {
  color: hsl(var(--muted-foreground));
}

.clpm-loop-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.clpm-loop-list__header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  font-size: 11px;
  font-weight: 600;
  color: hsl(var(--muted-foreground));
  border-bottom: 1px solid hsl(var(--border));
  margin-bottom: 4px;
}

.clpm-loop-list__col {
  flex: 1;
  text-align: left;
}

.clpm-loop-list__col--rank {
  width: 32px;
  flex: none;
  text-align: center;
}

.clpm-loop-list__col--score {
  width: 60px;
  flex: none;
  text-align: right;
}

.clpm-loop-list__col--action {
  width: 32px;
  flex: none;
  text-align: center;
}

.clpm-loop-list__item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border-radius: 4px;
  background: hsl(var(--muted));
  transition: background 0.15s;
}

.clpm-loop-list__item:hover {
  background: hsl(var(--accent) / 10%);
}

.clpm-loop-list__action-btn {
  width: 24px;
  height: 24px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: none;
  background: transparent;
  color: hsl(var(--primary));
  cursor: pointer;
  border-radius: 4px;
  transition: background 0.15s;
}

.clpm-loop-list__action-btn:hover {
  background: hsl(var(--primary) / 10%);
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
</style>
