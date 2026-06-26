<script lang="ts" setup>
import type { TableColumnsType, TablePaginationConfig } from 'ant-design-vue';

/**
 * 性能总览页（FE-06 重构）
 *
 * 对齐 UI/UX v4.1 §6.1.1 + PRD §4.3 + IDS v3.2 §2.3
 * - 左侧：工厂树导航
 * - 右上：实时自控率仪表盘（ECharts 环形图）
 * - 右中：整点 KPI 卡片（评分/自控率/平稳率/实时自控率）
 * - 右下：详细列表（等级筛选 + 参数搜索）
 * - 5 分钟自动刷新
 */
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { KpiStatus, MetricApi, TimeWindow } from '#/api/metric';
import type { PlantNodeApi } from '#/api/plant-node';

import { onMounted, onUnmounted, reactive, ref, watch } from 'vue';

import { Page } from '@vben/common-ui';
import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import {
  Alert,
  Button,
  Card,
  Input,
  Select,
  Table,
  Tag,
} from 'ant-design-vue';

import {
  getBoardApi,
  getRankingApi,
  getRealtimeAutoRateApi,
} from '#/api/metric';
import PlantNodeTree from '#/components/plant-node/plant-node-tree.vue';
import AutoRateGauge from '#/components/metric/auto-rate-gauge.vue';

defineOptions({ name: 'MetricDashboard' });

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

const statusColorMap: Record<KpiStatus, string> = {
  SUCCESS: '#52c41a',
  INCONCLUSIVE: '#d9d9d9',
  PARTIAL: '#faad14',
};

const statusLabelMap: Record<KpiStatus, string> = {
  SUCCESS: '良好',
  INCONCLUSIVE: '不确定',
  PARTIAL: '部分',
};

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

// ECharts 趋势图
const trendChartRef = ref<EchartsUIType>();
const { renderEcharts: renderTrend } = useEcharts(trendChartRef);

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
    renderTrendChart();
  } catch {
    // 错误已由拦截器处理
  } finally {
    loading.value = false;
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
    <!-- Partial 警告横幅 -->
    <Alert
      v-if="boardData?.partialWarning?.active"
      class="mb-3"
      type="warning"
      show-icon
      :message="boardData.partialWarning.message || '存在部分回路数据不完整'"
      :description="`不确定回路 ${boardData.partialWarning.inconclusiveCount} 个，部分关联 ${boardData.partialWarning.partialCount} 个`"
    />

    <div class="flex gap-3" style="min-height: calc(100vh - 160px)">
      <!-- 左侧工厂树（统一组件） -->
      <PlantNodeTree
        card-title="工厂导航"
        :width="260"
        @select="onTreeSelect"
      />

      <!-- 右侧主区域 -->
      <div class="flex flex-1 flex-col gap-3">
        <!-- 顶部：时间窗 + 节点信息 -->
        <Card size="small" :body-style="{ padding: '8px 12px' }">
          <div class="flex flex-wrap items-center gap-3">
            <span class="text-sm font-medium">{{ selectedPlantNodeName }}</span>
            <Select
              v-model:value="filter.timeWindow"
              style="width: 140px"
              size="small"
              :options="timeWindowOptions"
              @change="handleTimeWindowChange"
            />
            <Button
              type="primary"
              size="small"
              :loading="loading"
              @click="loadAll"
            >
              刷新
            </Button>
            <span class="ml-auto text-xs text-gray-400">每 5 分钟自动刷新</span>
          </div>
        </Card>

        <!-- 右上：实时自控率仪表盘 + KPI 卡片 -->
        <div class="grid grid-cols-1 gap-3 lg:grid-cols-3">
          <!-- 仪表盘 -->
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

          <!-- 整点 KPI 卡片 -->
          <Card
            class="lg:col-span-2"
            size="small"
            :body-style="{ padding: '12px' }"
          >
            <div class="mb-2 text-sm font-medium">整点 KPI</div>
            <div class="grid grid-cols-2 gap-2 md:grid-cols-4">
              <div
                v-for="card in boardData?.kpiCards || []"
                :key="card.metricKey"
                class="rounded border p-2"
                :body-style="{ padding: '8px' }"
              >
                <div class="mb-1 flex items-center justify-between">
                  <span class="text-xs text-gray-500">{{
                    card.metricName
                  }}</span>
                  <span
                    class="inline-block h-2 w-2 rounded-full"
                    :style="{ backgroundColor: statusColorMap[card.status] }"
                  ></span>
                </div>
                <div class="flex items-baseline gap-1">
                  <span
                    class="text-xl font-bold"
                    :style="{ color: statusColorMap[card.status] }"
                  >
                    {{ card.value?.toFixed(1) ?? '--' }}
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
              <!-- 实时自控率卡片 -->
              <div class="rounded border border-blue-100 bg-blue-50 p-2">
                <div class="mb-1 flex items-center justify-between">
                  <span class="text-xs text-gray-500">实时自控率</span>
                  <span
                    class="inline-block h-2 w-2 rounded-full"
                    style="background-color: #52c41a"
                  ></span>
                </div>
                <div class="flex items-baseline gap-1">
                  <span
                    class="text-xl font-bold"
                    :style="{
                      color: scoreColor(realtimeAutoRate?.autoRate ?? 0),
                    }"
                  >
                    {{ realtimeAutoRate?.autoRate?.toFixed(1) ?? '--' }}
                  </span>
                  <span class="text-xs text-gray-400">%</span>
                </div>
                <div class="mt-1 text-xs text-gray-500">
                  自动 {{ realtimeAutoRate?.autoCount ?? 0 }} / 总
                  {{ realtimeAutoRate?.totalCount ?? 0 }}
                </div>
              </div>
            </div>
          </Card>
        </div>

        <!-- 右中：平稳率趋势 -->
        <Card title="平稳率趋势" size="small" :loading="loading">
          <EchartsUI ref="trendChartRef" height="240px" />
        </Card>

        <!-- 右下：详细列表 -->
        <Card size="small" :body-style="{ padding: '12px' }">
          <template #title>
            <span class="text-sm">详细列表</span>
          </template>
          <div class="mb-3 flex flex-wrap items-center gap-2">
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
            <Button type="primary" size="small" @click="handleRankingSearch">
              查询
            </Button>
          </div>
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
        </Card>
      </div>
    </div>
  </Page>
</template>

<style scoped>
/* 树组件样式由 PlantNodeTree 组件内部管理 */
</style>
