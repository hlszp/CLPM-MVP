<script lang="ts" setup>
/**
 * S3-METRIC-009 性能看板页
 *
 * 对齐 IDS v3.2 §2.3 + PRD §4.3
 * - 顶部筛选栏（装置选择/时间窗选择 today/yesterday/last_7_days/last_30_days）
 * - 7 张 KPI 卡片（6 大 KPI + 综合评分），含状态色标和算法版本号
 * - ECharts 趋势图（平稳率趋势折线图）
 * - partialWarning 黄色警告横幅
 * - 5 分钟自动刷新
 */
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { KpiStatus, MetricApi, TimeWindow } from '#/api/metric';
import type { PlantNodeApi } from '#/api/plant-node';

import { onMounted, onUnmounted, reactive, ref, watch } from 'vue';

import { Page } from '@vben/common-ui';
import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import { Alert, Button, Card, Select } from 'ant-design-vue';

import { getBoardApi } from '#/api/metric';
import { getPlantNodeTreeApi } from '#/api/plant-node';

defineOptions({ name: 'MetricDashboard' });

const loading = ref(false);
const boardData = ref<MetricApi.BoardResult | null>(null);
const plantNodes = ref<PlantNodeApi.PlantNode[]>([]);

const filter = reactive({
  plantNodeId: undefined as string | undefined,
  timeWindow: 'today' as TimeWindow,
});

const timeWindowOptions = [
  { label: '今天', value: 'today' },
  { label: '昨天', value: 'yesterday' },
  { label: '近 7 天', value: 'last_7_days' },
  { label: '近 30 天', value: 'last_30_days' },
];

// 状态色映射
const statusColorMap: Record<KpiStatus, string> = {
  GOOD: '#52c41a',
  INCONCLUSIVE: '#d9d9d9',
  POOR: '#ff4d4f',
  WARNING: '#faad14',
};

const statusLabelMap: Record<KpiStatus, string> = {
  GOOD: '良好',
  INCONCLUSIVE: '不确定',
  POOR: '差',
  WARNING: '警告',
};

// ECharts 趋势图
const trendChartRef = ref<EchartsUIType>();
const { renderEcharts: renderTrend } = useEcharts(trendChartRef);

// 自动刷新
const refreshInterval = 5 * 60; // 5 分钟
let refreshTimer: null | ReturnType<typeof setInterval> = null;

/** 扁平化工厂节点树 */
function flattenNodes(
  nodes: PlantNodeApi.PlantNode[],
  result: PlantNodeApi.PlantNode[] = [],
): PlantNodeApi.PlantNode[] {
  for (const node of nodes) {
    result.push(node);
    if (node.children) {
      flattenNodes(node.children, result);
    }
  }
  return result;
}

/** 加载工厂节点 */
async function loadPlantNodes() {
  try {
    const tree = await getPlantNodeTreeApi();
    plantNodes.value = flattenNodes(tree);
  } catch {
    // 错误已由拦截器处理
  }
}

/** 加载看板数据 */
async function loadBoard() {
  loading.value = true;
  try {
    const data = await getBoardApi({
      plantNodeId: filter.plantNodeId,
      timeWindow: filter.timeWindow,
    });
    boardData.value = data;
    renderTrendChart();
  } catch {
    // 错误已由拦截器处理
  } finally {
    loading.value = false;
  }
}

/** 渲染趋势图 */
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
      valueFormatter: (val: any) =>
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

/** 启动自动刷新 */
function startAutoRefresh() {
  stopAutoRefresh();
  refreshTimer = setInterval(() => {
    loadBoard();
  }, refreshInterval * 1000);
}

/** 停止自动刷新 */
function stopAutoRefresh() {
  if (refreshTimer) {
    clearInterval(refreshTimer);
    refreshTimer = null;
  }
}

function handleSearch() {
  loadBoard();
}

watch(
  () => boardData.value?.steadyRateTrend,
  () => renderTrendChart(),
  { deep: true },
);

onMounted(() => {
  loadPlantNodes();
  loadBoard();
  startAutoRefresh();
});

onUnmounted(() => {
  stopAutoRefresh();
});
</script>

<template>
  <Page title="性能看板">
    <!-- Partial 警告横幅 -->
    <Alert
      v-if="boardData?.partialWarning?.active"
      class="mb-4"
      type="warning"
      show-icon
      :message="boardData.partialWarning.message || '存在部分回路数据不完整'"
      :description="`不确定回路 ${boardData.partialWarning.inconclusiveCount} 个，部分关联 ${boardData.partialWarning.partialCount} 个`"
    />

    <!-- 筛选栏 -->
    <Card class="mb-4">
      <div class="flex flex-wrap items-center gap-3">
        <Select
          v-model:value="filter.plantNodeId"
          placeholder="装置/单元筛选"
          style="width: 240px"
          allow-clear
          :options="plantNodes.map((n) => ({ label: n.name, value: n.id }))"
          @change="handleSearch"
        />
        <Select
          v-model:value="filter.timeWindow"
          style="width: 160px"
          :options="timeWindowOptions"
          @change="handleSearch"
        />
        <Button type="primary" :loading="loading" @click="handleSearch">
          查询
        </Button>
        <span class="ml-auto text-xs text-gray-400"> 每 5 分钟自动刷新 </span>
      </div>
    </Card>

    <!-- KPI 卡片区 -->
    <div
      class="mb-4 grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-7"
    >
      <Card
        v-for="card in boardData?.kpiCards || []"
        :key="card.metricKey"
        size="small"
        :loading="loading"
        :body-style="{ padding: '16px' }"
      >
        <div class="flex flex-col">
          <div class="mb-1 flex items-center justify-between">
            <span class="text-xs text-gray-500">{{ card.metricName }}</span>
            <span
              class="inline-block h-2 w-2 rounded-full"
              :style="{ backgroundColor: statusColorMap[card.status] }"
            ></span>
          </div>
          <div class="flex items-baseline gap-1">
            <span
              class="text-2xl font-bold"
              :style="{ color: statusColorMap[card.status] }"
            >
              {{ card.value.toFixed(1) }}
            </span>
            <span class="text-xs text-gray-400">{{ card.unit }}</span>
          </div>
          <div class="mt-1 flex items-center justify-between">
            <span
              class="text-xs"
              :style="{ color: statusColorMap[card.status] }"
            >
              {{ statusLabelMap[card.status] }}
            </span>
            <span class="text-xs text-gray-400">{{
              card.algorithmVersion
            }}</span>
          </div>
        </div>
      </Card>
    </div>

    <!-- 趋势图 -->
    <Card title="平稳率趋势" :loading="loading">
      <EchartsUI ref="trendChartRef" height="360px" />
    </Card>
  </Page>
</template>
