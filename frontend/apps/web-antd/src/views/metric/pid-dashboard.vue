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
                <Tooltip :title="top5Sort === 'desc' ? '当前：评分最高，点击切换为最低' : '当前：评分最低，点击切换为最高'">
                  <Button type="text" size="small" class="clpm-pid-dashboard__sort-btn" @click="top5Sort = top5Sort === 'desc' ? 'asc' : 'desc'">
                    <IconifyIcon :icon="top5Sort === 'desc' ? 'ant-design:sort-descending-outlined' : 'ant-design:sort-ascending-outlined'" />
                  </Button>
                </Tooltip>
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

import { Button, Select, Table, Tooltip, message } from 'ant-design-vue';
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
const gradingThresholds = ref<MetricApi.GradingThresholdItem[]>([]);

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

// 定级阈值等级名称（level 1=优秀, 2=良好, 3=合格, 4=警告, 5=不合格）
const ratingLabels: Record<string, string> = {
  '1': '优秀',
  '2': '良好',
  '3': '合格',
  '4': '警告',
  '5': '不合格',
};

// 默认定级阈值（国标 GB/T 44693.2-2024 §6.3）
const DEFAULT_THRESHOLDS: MetricApi.GradingThresholdItem[] = [
  { level: 1, name: 'EXCELLENT', minScore: 90, maxScore: 100, color: '#52c41a' },
  { level: 2, name: 'GOOD', minScore: 80, maxScore: 90, color: '#1890ff' },
  { level: 3, name: 'FAIR', minScore: 60, maxScore: 80, color: '#faad14' },
  { level: 4, name: 'WARNING', minScore: 40, maxScore: 60, color: '#fa8c16' },
  { level: 5, name: 'POOR', minScore: 0, maxScore: 40, color: '#f5222d' },
];

function getRatingLevel(score: number): string {
  const thresholds = gradingThresholds.value.length > 0
    ? gradingThresholds.value
    : DEFAULT_THRESHOLDS;
  // 按 minScore 降序匹配（level 1 = 最高分区间）
  for (const t of [...thresholds].sort((a: MetricApi.GradingThresholdItem, b: MetricApi.GradingThresholdItem) => b.minScore - a.minScore)) {
    if (score >= t.minScore) return String(t.level);
  }
  return '5'; // 最低等级
}

const tableColumns = [
  { title: '序号', dataIndex: 'index', key: 'index', width: 60, align: 'center' as const },
  { title: '名称', dataIndex: 'name', key: 'name', width: 150, align: 'left' as const },
  { title: '性能评级', dataIndex: 'rating', key: 'rating', width: 80, align: 'center' as const },
  { title: '性能评分', dataIndex: 'score', key: 'score', width: 80, align: 'right' as const },
  { title: '平稳率', dataIndex: 'smoothRate', key: 'smoothRate', width: 80, align: 'right' as const },
  { title: '自控率', dataIndex: 'autoRate', key: 'autoRate', width: 80, align: 'right' as const },
  { title: '回路总数', dataIndex: 'totalLoops', key: 'totalLoops', width: 80, align: 'right' as const },
];

const tableData = computed(() => {
  const items = boardAggregate.value?.items ?? [];
  const sortedItems = [...items].sort((a, b) => (b.avgScore ?? 0) - (a.avgScore ?? 0));
  return sortedItems.map((item, index) => {
    const score = item.avgScore ?? 0;
    return {
      key: item.nodeId,
      index: index + 1,
      name: item.nodeName ?? '',
      rating: getRatingLevel(score),
      score: formatNumber(score),
      totalLoops: item.totalLoops ?? 0,
      autoRate: formatNumber(item.autoModeRate),
      smoothRate: formatNumber(item.stabilityRate),
    };
  });
});

const top5Columns = [
  { title: '序号', dataIndex: 'index', key: 'index', width: 40, align: 'center' as const },
  { title: '位号', dataIndex: 'tagName', key: 'tagName', width: 90 },
  { title: '名称', dataIndex: 'loopName', key: 'loopName', ellipsis: true },
  { title: '性能评分', dataIndex: 'score', key: 'score', width: 70, align: 'right' as const },
  { title: '平稳率', dataIndex: 'steadyRate', key: 'steadyRate', width: 65, align: 'right' as const },
  { title: '', dataIndex: 'diagnosis', key: 'diagnosis', width: 40, align: 'center' as const },
];

const top5TableData = computed(() => {
  return top5List.value.map((item, index) => {
    const fullName = item.loopName || item.tagName || '—';
    // 最多显示 16 个字符（按字符长度计算，中文算 2），超出截断加省略号
    let truncated = fullName;
    let len = 0;
    for (const ch of fullName) {
      len += ch.charCodeAt(0) > 0x7f ? 2 : 1;
      if (len > 32) { // 16 汉字 = 32
        truncated = fullName.slice(0, fullName.indexOf(ch)) + '…';
        break;
      }
    }
    return {
      key: item.loopId,
      index: index + 1,
      loopId: item.loopId,
      tagName: item.tagName,
      loopName: truncated,
      score: formatNumber(item.score),
      steadyRate: `${formatNumber(item.steadyRate)}%`,
    };
  });
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

  const barDataTotal = (trend.totalLoops ?? 0) > 0
    ? timestamps.map(() => trend.totalLoops)
    : [];
  const barDataEvaluated = trend.evaluatedLoops ?? [];

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
        name: '总回路数',
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
        name: '参评回路数',
        type: showBar ? 'bar' as const : 'line' as const,
        data: barDataEvaluated,
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
      data: ['总回路数', '参评回路数', '性能评分', '自控率', '平稳率'],
    },
  });
}

function renderStatusPieChart() {
  const rt = autoRateRt.value;
  const total = rt?.totalCount ?? 0;

  // 5 种标准 MODE 值的回路数与中文标签 / 配色（对齐 app.constants.mode）
  // 0=手动, 1=自动, 2=串级, 3=远程, 4=先控
  const MODE_LABELS: Record<number, string> = {
    0: '手动',
    1: '自动',
    2: '串级',
    3: '远程',
    4: '先控',
  };
  const MODE_COLORS: Record<number, string> = {
    0: '#d4380d', // 红橙 - 手动（警示）
    1: '#52c41a', // 绿 - 自动（正常）
    2: '#1890ff', // 蓝 - 串级
    3: '#722ed1', // 紫 - 远程
    4: '#13c2c2', // 青 - 先控
  };

  const modeCounts = rt?.modeCounts ?? {};
  const pieData = Object.keys(MODE_LABELS).map((modeKey) => {
    const mode = Number.parseInt(modeKey, 10);
    const count = modeCounts[modeKey] ?? modeCounts[mode] ?? 0;
    return {
      value: count,
      name: MODE_LABELS[mode],
      itemStyle: { color: MODE_COLORS[mode] },
    };
  });

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
      data: Object.values(MODE_LABELS),
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
        // 仅展示有数据的 MODE 值（或全部为 0 时显示全部以便占位）
        data: total === 0 ? pieData : pieData.filter((d) => (d.value ?? 0) > 0),
      },
    ],
  });
}

function renderPieChart() {
  const items = boardAggregate.value?.items ?? [];
  const thresholds = gradingThresholds.value.length > 0
    ? gradingThresholds.value
    : DEFAULT_THRESHOLDS;

  // 按等级统计数量（level 1=优秀 ~ level 5=不合格）
  const levelCounts: Record<number, number> = { 1: 0, 2: 0, 3: 0, 4: 0, 5: 0 };
  items.forEach((item) => {
    const score = item.avgScore ?? 0;
    const level = parseInt(getRatingLevel(score), 10);
    levelCounts[level] = (levelCounts[level] ?? 0) + 1;
  });

  const total = items.length;

  // 按等级顺序（1→5）生成饼图数据
  const pieData = thresholds
    .slice()
    .sort((a: MetricApi.GradingThresholdItem, b: MetricApi.GradingThresholdItem) => a.level - b.level)
    .map((t: MetricApi.GradingThresholdItem) => ({
      value: levelCounts[t.level] ?? 0,
      name: ratingLabels[String(t.level)] ?? t.name,
      itemStyle: { color: t.color ?? themeColors.value.SUCCESS },
    }));

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
      data: pieData.map((d: { value: number; name: string; itemStyle: { color: string } }) => d.name),
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
        data: pieData.filter((d: { value: number; name: string; itemStyle: { color: string } }) => (d.value ?? 0) > 0 || total === 0),
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
    renderStatusPieChart();
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

async function loadGradingThresholds() {
  try {
    const { getGradingThresholdsApi } = await import('#/api/metric');
    const data = await getGradingThresholdsApi();
    gradingThresholds.value = data.thresholds ?? [];
  } catch {
    // 加载失败时使用默认阈值
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
  loadGradingThresholds();
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
}

.dark .clpm-pid-dashboard__card-header {
  color: #e2e8f0;
}

.clpm-pid-dashboard__sort-btn {
  padding: 2px 6px;
  color: #64748b;
  cursor: pointer;

  &:hover {
    color: #3b82f6;
  }
}

.dark .clpm-pid-dashboard__sort-btn {
  color: #94a3b8;

  &:hover {
    color: #3b82f6;
  }
}

.clpm-pid-dashboard__bottom-row {
  display: flex;
  gap: 12px;
  flex: 1;
}

.clpm-pid-dashboard__table-card {
  width: 50%;
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
  width: 50%;
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

.clpm-pid-dashboard__top5-card :deep(.ant-table-tbody > tr > td) {
  white-space: nowrap;
}

.clpm-pid-dashboard__rating-tag {
  padding: 2px 8px;
  font-size: 12px;
  border-radius: 4px;

  &--1 {
    background: rgba(34, 197, 94, 0.1);
    color: #22c55e;
  }
  &--2 {
    background: rgba(59, 130, 246, 0.1);
    color: #3b82f6;
  }
  &--3 {
    background: rgba(245, 158, 11, 0.1);
    color: #f59e0b;
  }
  &--4 {
    background: rgba(249, 115, 22, 0.1);
    color: #f97316;
  }
  &--5 {
    background: rgba(239, 68, 68, 0.1);
    color: #ef4444;
  }
}

.dark .clpm-pid-dashboard__rating-tag {
  &--1 {
    background: rgba(34, 197, 94, 0.2);
  }
  &--2 {
    background: rgba(59, 130, 246, 0.2);
  }
  &--3 {
    background: rgba(245, 158, 11, 0.2);
  }
  &--4 {
    background: rgba(249, 115, 22, 0.2);
  }
  &--5 {
    background: rgba(239, 68, 68, 0.2);
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