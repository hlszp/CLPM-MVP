<template>
  <Page>
    <div class="clpm-pid-dashboard">
      <div class="clpm-pid-dashboard__header">
        <div class="clpm-pid-dashboard__header-left">
          <h1 class="clpm-pid-dashboard__title">评估看板</h1>
        </div>
        <div class="clpm-pid-dashboard__header-right">
          <Select
            v-model:value="timeWindow"
            style="width: 140px"
            size="small"
            :options="timeWindowOptions"
            @change="handleTimeWindowChange"
          />
        </div>
      </div>

      <div class="clpm-pid-dashboard__body">
        <PlantNodeTree
          card-title="工厂导航"
          :width="200"
          @select="onTreeSelect"
        />

        <div class="clpm-pid-dashboard__main">
          <div class="clpm-pid-dashboard__top-row">
            <div class="clpm-pid-dashboard__gauge-card">
              <div class="clpm-pid-dashboard__gauge-title">实时自控率</div>
              <EchartsUI ref="gauge1Ref" height="126px" />
              <div class="clpm-pid-dashboard__gauge-value">{{ autoRateRt?.rate ?? '--' }}%</div>
            </div>

            <div class="clpm-pid-dashboard__gauge-card">
              <div class="clpm-pid-dashboard__gauge-title">性能评分</div>
              <EchartsUI ref="gauge2Ref" height="126px" />
              <div class="clpm-pid-dashboard__gauge-value" :style="{ color: scoreColor(aggregateData?.avgScore) }">
                {{ aggregateData?.avgScore ?? '--' }}%
              </div>
            </div>

            <div class="clpm-pid-dashboard__gauge-card">
              <div class="clpm-pid-dashboard__gauge-title">自控率</div>
              <EchartsUI ref="gauge3Ref" height="126px" />
              <div class="clpm-pid-dashboard__gauge-value">{{ aggregateData?.autoModeRate ?? '--' }}%</div>
            </div>

            <div class="clpm-pid-dashboard__gauge-card">
              <div class="clpm-pid-dashboard__gauge-title">平稳率</div>
              <EchartsUI ref="gauge4Ref" height="126px" />
              <div class="clpm-pid-dashboard__gauge-value">{{ aggregateData?.stabilityRate ?? '--' }}%</div>
            </div>

            <div class="clpm-pid-dashboard__gauge-card">
              <div class="clpm-pid-dashboard__gauge-title">好值率</div>
              <EchartsUI ref="gauge5Ref" height="126px" />
              <div class="clpm-pid-dashboard__gauge-value">{{ aggregateData?.goodValueRate ?? '--' }}%</div>
            </div>
          </div>

          <div class="clpm-pid-dashboard__middle-row">
            <div class="clpm-pid-dashboard__chart-card clpm-pid-dashboard__chart-card--status-pie">
              <div class="clpm-pid-dashboard__card-header">
                <span>回路状态统计</span>
              </div>
              <EchartsUI ref="statusPieChartRef" height="200px" />
            </div>

            <div class="clpm-pid-dashboard__chart-card clpm-pid-dashboard__chart-card--trend">
              <div class="clpm-pid-dashboard__card-header">
                <span>性能指标趋势图</span>
              </div>
              <EchartsUI ref="trendChartRef" height="240px" />
            </div>

            <div class="clpm-pid-dashboard__chart-card clpm-pid-dashboard__chart-card--pie">
              <div class="clpm-pid-dashboard__card-header">
                <span>回路等级占比</span>
              </div>
              <EchartsUI ref="pieChartRef" height="240px" />
            </div>
          </div>

          <div class="clpm-pid-dashboard__bottom-row">
            <div class="clpm-pid-dashboard__table-card">
              <div class="clpm-pid-dashboard__card-header">
                <span>装置/单元性能明细表</span>
              </div>
              <Table :columns="tableColumns" :data-source="tableData" :pagination="false" :scroll="{ y: 200 }">
                <template #bodyCell="{ column, record }">
                  <template v-if="column.key === 'rating'">
                    <span :class="['clpm-pid-dashboard__rating-tag', `clpm-pid-dashboard__rating-tag--${record.rating}`]">
                      {{ ratingLabels[record.rating] }}
                    </span>
                  </template>
                  <template v-if="column.key === 'autoRate'">
                    <span>{{ record.autoRate }}%</span>
                  </template>
                  <template v-if="column.key === 'smoothRate'">
                    <span>{{ record.smoothRate }}%</span>
                  </template>
                </template>
              </Table>
            </div>

            <div class="clpm-pid-dashboard__top5-card">
              <div class="clpm-pid-dashboard__card-header">
                <span>TOP5回路</span>
                <div class="clpm-pid-dashboard__card-tabs">
                  <span :class="{ active: top5Sort === 'desc' }" @click="top5Sort = 'desc'">评分最高</span>
                  <span :class="{ active: top5Sort === 'asc' }" @click="top5Sort = 'asc'">评分最低</span>
                </div>
              </div>
              <Table :columns="top5Columns" :data-source="top5TableData" :pagination="false" :scroll="{ y: 200 }">
                <template #bodyCell="{ column, record }">
                  <template v-if="column.key === 'score'">
                    <span :style="{ color: scoreColor(record.score) }">{{ record.score }}</span>
                  </template>
                  <template v-if="column.key === 'diagnosis'">
                    <Button type="text" size="small" :loading="diagnosisLoading" @click="handleDiagnosis(record.loopId)">
                      <template #icon>
                        <IconifyIcon icon="ant-design:right-outlined" />
                      </template>
                    </Button>
                  </template>
                </template>
              </Table>
            </div>
          </div>
        </div>
      </div>
    </div>
  </Page>
</template>

<script lang="ts" setup>
import type { EchartsUIType } from '@vben/plugins/echarts';
import type { MetricApi, DashboardApi, TimeWindow } from '#/api';
import type { PlantNodeApi } from '#/api/plant-node';

import { computed, nextTick, onMounted, ref, watch } from 'vue';
import { useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';
import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import { Button, Select, Table, message } from 'ant-design-vue';
import { IconifyIcon } from '@vben/icons';

import PlantNodeTree from '#/components/plant-node/plant-node-tree.vue';
import { useClpmTheme } from '#/composables/use-clpm-theme';

defineOptions({ name: 'PidDashboard' });

const { isDark, themeColors, chartColors } = useClpmTheme();
const router = useRouter();

const timeWindowOptions = [
  { label: '近8小时', value: 'last_8_hours' },
  { label: '24小时', value: 'today' },
  { label: '168小时', value: 'last_7_days' },
  { label: '近1月', value: 'last_30_days' },
];

const timeWindow = ref<TimeWindow>('today');

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

function handleTimeWindowChange() {
  loadAll();
}

const boardAggregate = ref<DashboardApi.BoardAggregateResult | null>(null);
const boardTrend = ref<DashboardApi.BoardTrendResult | null>(null);
const autoRateRt = ref<DashboardApi.AutoRateRt | null>(null);
const rankingList = ref<MetricApi.RankingItem[]>([]);
const diagnosisLoading = ref(false);

const top5Sort = ref<'asc' | 'desc'>('desc');

const aggregateData = computed(() => boardAggregate.value?.aggregate);

const top5List = computed(() => {
  const items = [...rankingList.value];
  if (top5Sort.value === 'asc') {
    items.sort((a, b) => (a.score ?? 0) - (b.score ?? 0));
  } else {
    items.sort((a, b) => (b.score ?? 0) - (a.score ?? 0));
  }
  return items.slice(0, 5);
});

const ratingLabels: Record<string, string> = {
  '1': '一星级',
  '2': '二星级',
  '3': '三星级',
  '4': '四星级',
  '5': '五星级',
};

const tableColumns = [
  { title: '序号', dataIndex: 'index', key: 'index', width: 60, align: 'center' as const },
  { title: '名称', dataIndex: 'name', key: 'name', width: 150, align: 'left' as const },
  { title: '性能评级', dataIndex: 'rating', key: 'rating', width: 80, align: 'center' as const },
  { title: '性能评分', dataIndex: 'score', key: 'score', width: 80, align: 'right' as const },
  { title: '平稳率', dataIndex: 'smoothRate', key: 'smoothRate', width: 80, align: 'right' as const },
  { title: '自控率', dataIndex: 'autoRate', key: 'autoRate', width: 80, align: 'right' as const },
  { title: '自控回路数', dataIndex: 'autoLoopCount', key: 'autoLoopCount', width: 100, align: 'right' as const },
  { title: '参评回路数', dataIndex: 'loopCount', key: 'loopCount', width: 100, align: 'right' as const },
  { title: '回路总数', dataIndex: 'totalLoops', key: 'totalLoops', width: 80, align: 'right' as const },
];

const tableData = computed(() => {
  const items = boardAggregate.value?.items ?? [];
  const sortedItems = [...items].sort((a, b) => (b.avgScore ?? 0) - (a.avgScore ?? 0));
  return sortedItems.map((item, index) => {
    const score = item.avgScore ?? 0;
    let rating = '3';
    if (score >= 90) rating = '5';
    else if (score >= 80) rating = '4';
    else if (score >= 70) rating = '3';
    else if (score >= 60) rating = '2';
    else rating = '1';
    return {
      key: item.nodeId,
      index: index + 1,
      name: item.nodeName ?? '',
      rating,
      score: formatNumber(score),
      totalLoops: item.totalLoops ?? 0,
      loopCount: item.evaluatedLoops ?? 0,
      autoLoopCount: Math.round(((item.autoModeRate ?? 0) / 100) * (item.evaluatedLoops ?? 0)),
      autoRate: formatNumber(item.autoModeRate),
      smoothRate: formatNumber(item.stabilityRate),
    };
  });
});

const top5Columns = [
  { title: '序号', dataIndex: 'index', key: 'index', width: 40, align: 'center' as const },
  { title: '位号', dataIndex: 'tagName', key: 'tagName', width: 90 },
  { title: '名称', dataIndex: 'loopName', key: 'loopName', width: 120 },
  { title: '性能评分', dataIndex: 'score', key: 'score', width: 70, align: 'right' as const },
  { title: '平稳率', dataIndex: 'steadyRate', key: 'steadyRate', width: 65, align: 'right' as const },
  { title: '', dataIndex: 'diagnosis', key: 'diagnosis', width: 40, align: 'center' as const },
];

const top5TableData = computed(() => {
  return top5List.value.map((item, index) => ({
    key: item.loopId,
    index: index + 1,
    loopId: item.loopId,
    tagName: item.tagName,
    loopName: item.loopName || item.tagName || '',
    score: formatNumber(item.score),
    steadyRate: `${formatNumber(item.steadyRate)}%`,
  }));
});

const gauge1Ref = ref<EchartsUIType>();
const gauge2Ref = ref<EchartsUIType>();
const gauge3Ref = ref<EchartsUIType>();
const gauge4Ref = ref<EchartsUIType>();
const gauge5Ref = ref<EchartsUIType>();
const trendChartRef = ref<EchartsUIType>();
const pieChartRef = ref<EchartsUIType>();
const statusPieChartRef = ref<EchartsUIType>();

const { renderEcharts: renderGauge1 } = useEcharts(gauge1Ref);
const { renderEcharts: renderGauge2 } = useEcharts(gauge2Ref);
const { renderEcharts: renderGauge3 } = useEcharts(gauge3Ref);
const { renderEcharts: renderGauge4 } = useEcharts(gauge4Ref);
const { renderEcharts: renderGauge5 } = useEcharts(gauge5Ref);
const { renderEcharts: renderTrend } = useEcharts(trendChartRef);
const { renderEcharts: renderPie } = useEcharts(pieChartRef);
const { renderEcharts: renderStatusPie } = useEcharts(statusPieChartRef);

function renderGaugeOption(value: number, color: string) {
  return {
    series: [
      {
        type: 'gauge' as const,
        startAngle: 220,
        endAngle: -40,
        min: 0,
        max: 100,
        splitNumber: 5,
        radius: '108%',
        center: ['50%', '55%'],
        axisLine: {
          lineStyle: {
            width: 6,
            color: [
              [0.3, themeColors.value.DANGER],
              [0.5, themeColors.value.WARNING],
              [0.75, themeColors.value.INFO],
              [1, themeColors.value.SUCCESS],
            ],
          },
        },
        pointer: {
          length: '50%',
          width: 3,
          itemStyle: { color },
        },
        axisTick: {
          distance: -11,
          length: 5,
          lineStyle: { color: chartColors.value.text, width: 1 },
        },
        splitLine: {
          distance: -14,
          length: 18,
          lineStyle: { color: chartColors.value.text, width: 2 },
        },
        axisLabel: {
          color: chartColors.value.text,
          fontSize: 9,
          distance: 14,
        },
        detail: { show: false },
        data: [{ value, name: '' }],
      },
    ],
  } as any;
}

function renderTrendChart() {
  const trend = boardTrend.value;
  if (!trend || !trend.timestamps?.length) return;

  const timestamps = trend.timestamps.map((ts) => {
    const d = new Date(new Date(ts).getTime() + 8 * 3600 * 1000);
    return `${d.getMonth() + 1}-${d.getDate()} ${d.getHours()}:00`;
  });

  const barDataTotal = trend.evaluatedLoops ?? [];
  const barDataAuto = trend.evaluatedLoops ?? [];

  const showBar = timestamps.length <= 24;

  renderTrend({
    grid: { bottom: 40, left: '2%', right: '2%', top: 20, containLabel: true },
    xAxis: {
      type: 'category',
      data: timestamps,
      axisLabel: { color: chartColors.value.text, fontSize: 10, rotate: timestamps.length > 12 ? 45 : 0 },
      axisLine: { lineStyle: { color: chartColors.value.splitLine } },
      axisTick: { show: false },
    },
    yAxis: [
      {
        type: 'value',
        name: '回路数',
        nameTextStyle: { color: chartColors.value.text, fontSize: 11 },
        axisLabel: { color: chartColors.value.text, fontSize: 10 },
        splitLine: { lineStyle: { color: chartColors.value.splitLine, type: 'dashed' } },
      },
      {
        type: 'value',
        name: '百分比(%)',
        nameTextStyle: { color: chartColors.value.text, fontSize: 11 },
        axisLabel: { color: chartColors.value.text, fontSize: 10, formatter: '{value}%' },
        max: 100,
        splitLine: { show: false },
      },
    ],
    series: [
      {
        name: '总参评回路数',
        type: showBar ? 'bar' as const : 'line' as const,
        data: barDataTotal,
        itemStyle: { color: themeColors.value.INFO },
        areaStyle: showBar ? undefined : {
          color: {
            type: 'linear',
            x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: `${themeColors.value.INFO}40` },
              { offset: 1, color: `${themeColors.value.INFO}05` },
            ],
          },
        },
        lineStyle: showBar ? undefined : { width: 2 },
        smooth: !showBar,
        symbol: 'circle',
        symbolSize: 4,
      },
      {
        name: '自动回路数',
        type: showBar ? 'bar' as const : 'line' as const,
        data: barDataAuto,
        itemStyle: { color: themeColors.value.SUCCESS },
        areaStyle: showBar ? undefined : {
          color: {
            type: 'linear',
            x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: `${themeColors.value.SUCCESS}40` },
              { offset: 1, color: `${themeColors.value.SUCCESS}05` },
            ],
          },
        },
        lineStyle: showBar ? undefined : { width: 2 },
        smooth: !showBar,
        symbol: 'circle',
        symbolSize: 4,
      },
      {
        name: '性能评分',
        type: 'line' as const,
        yAxisIndex: 1,
        data: trend.avgScore ?? [],
        smooth: true,
        itemStyle: { color: themeColors.value.WARNING },
        lineStyle: { width: 2 },
        symbol: 'circle',
        symbolSize: 6,
        areaStyle: {
          color: {
            type: 'linear',
            x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: `${themeColors.value.WARNING}30` },
              { offset: 1, color: `${themeColors.value.WARNING}05` },
            ],
          },
        },
      },
      {
        name: '自控率',
        type: 'line' as const,
        yAxisIndex: 1,
        data: trend.autoModeRate ?? [],
        smooth: true,
        itemStyle: { color: themeColors.value.INFO },
        lineStyle: { width: 2 },
        symbol: 'circle',
        symbolSize: 6,
        areaStyle: {
          color: {
            type: 'linear',
            x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: `${themeColors.value.INFO}30` },
              { offset: 1, color: `${themeColors.value.INFO}05` },
            ],
          },
        },
      },
      {
        name: '平稳率',
        type: 'line' as const,
        yAxisIndex: 1,
        data: trend.stabilityRate ?? [],
        smooth: true,
        itemStyle: { color: themeColors.value.SUCCESS },
        lineStyle: { width: 2 },
        symbol: 'circle',
        symbolSize: 6,
        areaStyle: {
          color: {
            type: 'linear',
            x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: `${themeColors.value.SUCCESS}30` },
              { offset: 1, color: `${themeColors.value.SUCCESS}05` },
            ],
          },
        },
      },
    ],
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
    },
    legend: {
      bottom: 0,
      textStyle: { color: chartColors.value.text, fontSize: 11 },
      data: ['总参评回路数', '自动回路数', '性能评分', '自控率', '平稳率'],
    },
  });
}

function renderStatusPieChart() {
  const data = aggregateData.value;
  if (!data) return;

  const autoCount = Math.round(((data.autoModeRate ?? 0) / 100) * (data.evaluatedLoops ?? 0));
  const manualCount = (data.evaluatedLoops ?? 0) - autoCount;
  const totalLoops = data.totalLoops ?? 0;
  const unevaluatedCount = totalLoops - (data.evaluatedLoops ?? 0);

  const total = autoCount + manualCount + unevaluatedCount;

  renderStatusPie({
    tooltip: {
      trigger: 'item',
      position: 'right',
      formatter: (params: any) => {
        const percent = total > 0 ? ((params.value / total) * 100).toFixed(1) : 0;
        return `${params.name}: ${params.value} (${percent}%)`;
      },
    },
    legend: {
      bottom: 0,
      textStyle: { color: chartColors.value.text, fontSize: 11 },
      data: ['自动模式', '手动模式', '未参评'],
    },
    series: [
      {
        type: 'pie' as const,
        radius: '70%',
        center: ['50%', '45%'],
        avoidLabelOverlap: false,
        itemStyle: {
          borderRadius: 4,
          borderColor: chartColors.value.border,
          borderWidth: 2,
        },
        label: { show: false },
        emphasis: {
          label: { show: true, fontSize: 12, fontWeight: 'bold', color: chartColors.value.textStrong },
        },
        labelLine: { show: false },
        data: [
          { value: autoCount, name: '自动模式', itemStyle: { color: themeColors.value.SUCCESS } },
          { value: manualCount, name: '手动模式', itemStyle: { color: themeColors.value.WARNING } },
          { value: unevaluatedCount, name: '未参评', itemStyle: { color: themeColors.value.INFO } },
        ].filter((d) => (d.value ?? 0) > 0 || total === 0),
      },
    ],
  });
}

function renderPieChart() {
  const items = boardAggregate.value?.items ?? [];
  const counts: number[] = [0, 0, 0, 0, 0];

  items.forEach((item) => {
    const score = item.avgScore ?? 0;
    const idx = score >= 90 ? 4 : score >= 80 ? 3 : score >= 70 ? 2 : score >= 60 ? 1 : 0;
    counts[idx] = (counts[idx] ?? 0) + 1;
  });

  const total = counts.reduce((a, b) => a + b, 0);

  renderPie({
    tooltip: {
      trigger: 'item',
      formatter: (params: any) => {
        const percent = total > 0 ? ((params.value / total) * 100).toFixed(1) : 0;
        return `${params.name}: ${params.value}个 (${percent}%)`;
      },
    },
    legend: {
      bottom: 0,
      textStyle: { color: chartColors.value.text, fontSize: 11 },
      data: ['一级', '二级', '三级', '四级', '五级'],
    },
    series: [
      {
        type: 'pie' as const,
        radius: ['45%', '70%'],
        center: ['50%', '45%'],
        avoidLabelOverlap: false,
        itemStyle: {
          borderRadius: 4,
          borderColor: chartColors.value.border,
          borderWidth: 2,
        },
        label: {
          show: true,
          position: 'outside',
          fontSize: 11,
          color: chartColors.value.text,
          formatter: (params: any) => {
            const percent = total > 0 ? ((params.value / total) * 100).toFixed(0) : 0;
            return `${params.name}\n${params.value}个 ${percent}%`;
          },
        },
        emphasis: {
          label: { show: true, fontSize: 12, fontWeight: 'bold', color: chartColors.value.textStrong },
        },
        labelLine: { show: true, length: 10, length2: 10 },
        data: [
          { value: counts[0], name: '一级', itemStyle: { color: themeColors.value.DANGER } },
          { value: counts[1], name: '二级', itemStyle: { color: '#f97316' } },
          { value: counts[2], name: '三级', itemStyle: { color: themeColors.value.WARNING } },
          { value: counts[3], name: '四级', itemStyle: { color: themeColors.value.INFO } },
          { value: counts[4], name: '五级', itemStyle: { color: themeColors.value.SUCCESS } },
        ].filter((d) => (d.value ?? 0) > 0 || total === 0),
      },
    ],
  });
}

function scoreColor(score: number | null | undefined): string {
  const val = score ?? 0;
  if (val >= 90) return themeColors.value.SUCCESS;
  if (val >= 80) return themeColors.value.INFO;
  if (val >= 70) return themeColors.value.WARNING;
  if (val >= 60) return '#f97316';
  return themeColors.value.DANGER;
}

function formatNumber(val: number | null | undefined, digits = 1): string {
  if (val === null || val === undefined || Number.isNaN(val)) return '--';
  return Number(val).toFixed(digits);
}

async function handleDiagnosis(loopId: string) {
  if (diagnosisLoading.value) return;
  diagnosisLoading.value = true;
  try {
    const { triggerDiagnosisApi } = await import('#/api/diagnosis');
    await triggerDiagnosisApi({ loopIds: [loopId] });
    message.success('诊断任务已创建');
    router.push('/diagnosis/tasks');
  } catch {
    message.error('创建诊断任务失败');
    console.error('[CLPM] 创建诊断任务失败');
  } finally {
    diagnosisLoading.value = false;
  }
}

async function loadBoard() {
  try {
    const { getBoardAggregateApi, getBoardTrendApi } = await import('#/api/dashboard');
    const [aggregate, trend] = await Promise.all([
      getBoardAggregateApi(selectedPlantNodeId.value ? { plantId: selectedPlantNodeId.value } : {}),
      getBoardTrendApi({
        ...(selectedPlantNodeId.value && { plantId: selectedPlantNodeId.value }),
        timeWindow: timeWindow.value,
      }),
    ]);
    boardAggregate.value = aggregate;
    boardTrend.value = trend;
    await nextTick();
    updateGauges();
    renderTrendChart();
    renderPieChart();
    renderStatusPieChart();
  } catch (error) {
    console.error('[CLPM] 加载看板数据失败:', error);
  }
}

async function loadAutoRateRt() {
  try {
    const { getAutoRateRtApi } = await import('#/api/dashboard');
    const data = await getAutoRateRtApi(selectedPlantNodeId.value ? { plantId: selectedPlantNodeId.value } : {});
    autoRateRt.value = data;
    await nextTick();
    updateGauges();
  } catch {
    // ignore
  }
}

async function loadRanking() {
  try {
    const { getRankingApi } = await import('#/api/metric');
    const data = await getRankingApi({
      plantNodeId: selectedPlantNodeId.value,
      timeWindow: timeWindow.value,
      sortBy: 'score',
      sortOrder: top5Sort.value,
      limit: 10,
    });
    rankingList.value = data.filter((it) => it.includeInEvaluation !== false);
  } catch {
    // ignore
  }
}

function loadAll() {
  loadBoard();
  loadAutoRateRt();
  loadRanking();
}

function updateGauges() {
  renderGauge1(renderGaugeOption(autoRateRt.value?.rate ?? 0, themeColors.value.INFO));
  renderGauge2(renderGaugeOption(aggregateData.value?.avgScore ?? 0, scoreColor(aggregateData.value?.avgScore)));
  renderGauge3(renderGaugeOption(aggregateData.value?.autoModeRate ?? 0, themeColors.value.SUCCESS));
  renderGauge4(renderGaugeOption(aggregateData.value?.stabilityRate ?? 0, themeColors.value.WARNING));
  renderGauge5(renderGaugeOption(aggregateData.value?.goodValueRate ?? 0, themeColors.value.SUCCESS));
}

watch(top5Sort, () => loadRanking());

watch(isDark, () => {
  nextTick(() => {
    updateGauges();
    renderTrendChart();
    renderPieChart();
    renderStatusPieChart();
  });
});

onMounted(() => {
  loadAll();
});
</script>

<style lang="scss" scoped>
.clpm-pid-dashboard {
  min-height: 100vh;
  background: linear-gradient(180deg, #f8fafc 0%, #f1f5f9 100%);
  color: #334155;
}

.dark .clpm-pid-dashboard {
  background: linear-gradient(180deg, #0f172a 0%, #1e293b 100%);
  color: #e2e8f0;
}

.clpm-pid-dashboard__header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 24px;
  background: linear-gradient(90deg, #ffffff 0%, #eff6ff 50%, #ffffff 100%);
  border-bottom: 1px solid #e2e8f0;
  height: 56px;
}

.dark .clpm-pid-dashboard__header {
  background: linear-gradient(90deg, #0f172a 0%, #1e3a5f 50%, #0f172a 100%);
  border-bottom: 1px solid #334155;
}

.clpm-pid-dashboard__header-left {
  display: flex;
  align-items: center;
}

.clpm-pid-dashboard__header-right {
  display: flex;
  align-items: center;
}

.clpm-pid-dashboard__title {
  font-size: 18px;
  font-weight: 600;
  color: #1e293b;
  margin: 0;
}

.dark .clpm-pid-dashboard__title {
  color: #f1f5f9;
}

.clpm-pid-dashboard__body {
  display: flex;
  padding: 16px;
  gap: 16px;
  height: calc(100vh - 56px);
}

.clpm-pid-dashboard__main {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 16px;
  overflow-y: auto;
}

.clpm-pid-dashboard__top-row {
  display: flex;
  gap: 12px;

  & > * {
    flex: 1;
  }
}

.clpm-pid-dashboard__gauge-card {
  background: rgba(255, 255, 255, 0.8);
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 12px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;

  &-title {
    font-size: 12px;
    color: #64748b;
  }

  &-value {
    font-size: 16px;
    font-weight: 600;
    color: #1e293b;
  }
}

.dark .clpm-pid-dashboard__gauge-card {
  background: rgba(15, 23, 42, 0.8);
  border: 1px solid #334155;

  &-title {
    color: #94a3b8;
  }

  &-value {
    color: #f1f5f9;
  }
}

.clpm-pid-dashboard__middle-row {
  display: flex;
  gap: 12px;
}

.clpm-pid-dashboard__chart-card {
  background: rgba(255, 255, 255, 0.8);
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 16px;

  &--status-pie {
    width: 20%;
  }

  &--trend {
    width: 60%;
  }

  &--pie {
    width: 20%;
  }
}

.dark .clpm-pid-dashboard__chart-card {
  background: rgba(15, 23, 42, 0.8);
  border: 1px solid #334155;
}

.clpm-pid-dashboard__card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
  font-size: 14px;
  font-weight: 500;
  color: #334155;

  &-tabs {
    display: flex;
    gap: 4px;

    span {
      padding: 4px 12px;
      font-size: 12px;
      color: #64748b;
      cursor: pointer;
      border-radius: 4px;
      transition: all 0.2s;

      &.active {
        background: rgba(59, 130, 246, 0.1);
        color: #3b82f6;
      }
    }
  }
}

.dark .clpm-pid-dashboard__card-header {
  color: #e2e8f0;

  &-tabs span {
    color: #94a3b8;

    &.active {
      background: rgba(59, 130, 246, 0.2);
      color: #3b82f6;
    }
  }
}

.clpm-pid-dashboard__bottom-row {
  display: flex;
  gap: 12px;
  flex: 1;
}

.clpm-pid-dashboard__table-card {
  width: 60%;
  background: rgba(255, 255, 255, 0.8);
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 16px;
  display: flex;
  flex-direction: column;
}

.dark .clpm-pid-dashboard__table-card {
  background: rgba(15, 23, 42, 0.8);
  border: 1px solid #334155;
}

.dark .clpm-pid-dashboard__table-card :deep(.ant-table-content) {
  color: #f1f5f9;
}

.dark .clpm-pid-dashboard__table-card :deep(.ant-table-thead > tr > th) {
  color: #94a3b8;
}

.dark .clpm-pid-dashboard__table-card :deep(.ant-table-tbody > tr > td) {
  color: #f1f5f9;
}

.clpm-pid-dashboard__top5-card {
  width: 40%;
  background: rgba(255, 255, 255, 0.8);
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  padding: 16px;
  display: flex;
  flex-direction: column;
}

.dark .clpm-pid-dashboard__top5-card {
  background: rgba(15, 23, 42, 0.8);
  border: 1px solid #334155;
}

.dark .clpm-pid-dashboard__top5-card :deep(.ant-table-content) {
  color: #f1f5f9;
}

.dark .clpm-pid-dashboard__top5-card :deep(.ant-table-thead > tr > th) {
  color: #94a3b8;
}

.dark .clpm-pid-dashboard__top5-card :deep(.ant-table-tbody > tr > td) {
  color: #f1f5f9;
}

.clpm-pid-dashboard__rating-tag {
  padding: 2px 8px;
  font-size: 12px;
  border-radius: 4px;

  &--1 {
    background: rgba(239, 68, 68, 0.1);
    color: #ef4444;
  }
  &--2 {
    background: rgba(249, 115, 22, 0.1);
    color: #f97316;
  }
  &--3 {
    background: rgba(245, 158, 11, 0.1);
    color: #f59e0b;
  }
  &--4 {
    background: rgba(59, 130, 246, 0.1);
    color: #3b82f6;
  }
  &--5 {
    background: rgba(34, 197, 94, 0.1);
    color: #22c55e;
  }
}

.dark .clpm-pid-dashboard__rating-tag {
  &--1 {
    background: rgba(239, 68, 68, 0.2);
  }
  &--2 {
    background: rgba(249, 115, 22, 0.2);
  }
  &--3 {
    background: rgba(245, 158, 11, 0.2);
  }
  &--4 {
    background: rgba(59, 130, 246, 0.2);
  }
  &--5 {
    background: rgba(34, 197, 94, 0.2);
  }
}

:deep(.ant-table) {
  background: transparent;

  .ant-table-header {
    background: rgba(241, 245, 249, 0.5);
  }

  .ant-table-body {
    background: transparent;
  }

  .ant-table-cell {
    color: #475569;
    border-bottom: 1px solid #e2e8f0;
    padding: 6px 8px;
    font-size: 12px;
    line-height: 1.4;
  }

  .ant-table-thead > tr > th {
    color: #64748b;
    background: rgba(241, 245, 249, 0.5);
    border-bottom: 1px solid #e2e8f0;
    padding: 8px 8px;
    font-size: 12px;
    font-weight: 500;
  }

  .ant-table-tbody > tr:hover > td {
    background: rgba(59, 130, 246, 0.05);
  }

  .ant-table-tbody > tr {
    height: 32px;
  }
}

.dark :deep(.ant-table) {
  background: transparent;

  .ant-table-header {
    background: rgba(30, 41, 59, 0.5);
  }

  .ant-table-body {
    background: transparent;
  }

  .ant-table-cell {
    color: #cbd5e1;
    border-bottom: 1px solid #334155;
    padding: 6px 8px;
    font-size: 12px;
    line-height: 1.4;
  }

  .ant-table-thead > tr > th {
    color: #94a3b8;
    background: rgba(30, 41, 59, 0.5);
    border-bottom: 1px solid #334155;
    padding: 8px 8px;
    font-size: 12px;
    font-weight: 500;
  }

  .ant-table-tbody > tr:hover > td {
    background: rgba(59, 130, 246, 0.1);
  }

  .ant-table-tbody > tr {
    height: 32px;
  }
}

:deep(.ant-select-selector) {
  background: rgba(241, 245, 249, 0.5) !important;
  border: 1px solid #e2e8f0 !important;
  color: #334155 !important;
}

.dark :deep(.ant-select-selector) {
  background: rgba(30, 41, 59, 0.5) !important;
  border: 1px solid #334155 !important;
  color: #e2e8f0 !important;
}

:deep(.ant-btn) {
  background: rgba(59, 130, 246, 0.1);
  border: 1px solid #3b82f6;
  color: #3b82f6;
}

.dark :deep(.ant-btn) {
  background: rgba(59, 130, 246, 0.2);
}
</style>