<script lang="ts" setup>
/**
 * S6-PORTAL-002 工作台性能总览首页
 *
 * 对齐 UI/UX v4.1 §6.1.1 + PRD §4.1 + IDS v3.2 §2.1
 * - 顶部筛选栏：全厂/装置/单元级联 + 日/周/月粒度 + 刷新按钮
 * - 6 大 KPI 卡片区（自控投用率/平稳率/综合评分/报警次数/操作频次/好值率）
 * - 低效回路列表（位号/评分/预诊标签/关键指标）+ 选中回路小趋势缩略图
 * - 趋势摘要：ECharts 折线图（最近 7 天综合评分趋势）
 * - 待处理异常：待处理诊断数 + 待处理 Tracker 数
 * - 5 分钟自动刷新（页面可见时）
 *
 * 反 AI Slop：高密度表格 + 侧栏详情，非卡片瀑布流。
 */
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { DashboardApi } from '#/api/dashboard';
import type { DiagnosisLabel } from '#/api/diagnosis';
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

import {
  Alert,
  Button,
  Card,
  Cascader,
  RadioGroup,
  Statistic,
  Table,
  Tag,
} from 'ant-design-vue';
import type { TableColumnsType } from 'ant-design-vue';

import { getDashboardOverviewApi } from '#/api/dashboard';
import { getPlantNodeTreeApi } from '#/api/plant-node';
import {
  DIAGNOSIS_LABEL_COLOR_MAP,
  DIAGNOSIS_LABEL_NAME_MAP,
} from '#/constants/diagnosis';

defineOptions({ name: 'DashboardWorkbench' });

const router = useRouter();

const loading = ref(false);
const overviewData = ref<DashboardApi.OverviewResult | null>(null);
const plantNodeTree = ref<PlantNodeApi.PlantNode[]>([]);

const filter = reactive({
  plantId: undefined as string | undefined,
  granularity: 'day' as DashboardApi.Granularity,
});

/** 粒度选项 */
const granularityOptions = [
  { label: '日', value: 'day' as const },
  { label: '周', value: 'week' as const },
  { label: '月', value: 'month' as const },
];

/** KPI 卡片配置（key 对应 KpiCards 字段） */
const kpiConfig: Array<{
  key: keyof DashboardApi.KpiCards;
  title: string;
  /** true=上升为好；false=下降为好（报警/操作次数） */
  goodWhenUp: boolean;
}> = [
  { key: 'auto_mode_rate', title: '自控投用率', goodWhenUp: true },
  { key: 'steady_rate', title: '平稳率', goodWhenUp: true },
  { key: 'composite_score', title: '综合评分', goodWhenUp: true },
  { key: 'alarm_count', title: '报警次数', goodWhenUp: false },
  { key: 'operation_count', title: '操作频次', goodWhenUp: false },
  { key: 'good_value_rate', title: '好值率', goodWhenUp: true },
];

/** 诊断标签颜色映射（对齐 diagnosis/list.vue） */
const labelColorMap = DIAGNOSIS_LABEL_COLOR_MAP;

/** 诊断标签中文名 */
const labelNameMap = DIAGNOSIS_LABEL_NAME_MAP;

/** 低效回路表格列定义 */
const columns: TableColumnsType = [
  {
    title: '回路位号',
    dataIndex: 'loop_tag',
    key: 'loop_tag',
    width: 130,
    ellipsis: true,
  },
  {
    title: '回路名称',
    dataIndex: 'loop_name',
    key: 'loop_name',
    width: 160,
    ellipsis: true,
  },
  {
    title: '装置',
    dataIndex: 'plant_name',
    key: 'plant_name',
    width: 140,
    ellipsis: true,
  },
  {
    title: '综合评分',
    dataIndex: 'composite_score',
    key: 'composite_score',
    width: 100,
    align: 'right',
  },
  {
    title: '预诊标签',
    dataIndex: 'diagnosis_labels',
    key: 'diagnosis_labels',
    width: 160,
  },
  {
    title: '自控率',
    dataIndex: 'key_metric.auto_mode_rate',
    key: 'auto_mode_rate',
    width: 90,
    align: 'right',
  },
  {
    title: '平稳率',
    dataIndex: 'key_metric.steady_rate',
    key: 'steady_rate',
    width: 90,
    align: 'right',
  },
];

/** 选中回路 */
const selectedLoop = ref<DashboardApi.InefficientLoop | null>(null);

/** ECharts 趋势图 */
const trendChartRef = ref<EchartsUIType>();
const { renderEcharts: renderTrend } = useEcharts(trendChartRef);

/** ECharts 选中回路小趋势缩略图 */
const miniChartRef = ref<EchartsUIType>();
const { renderEcharts: renderMini } = useEcharts(miniChartRef);

/** 自动刷新定时器 */
const REFRESH_INTERVAL = 5 * 60 * 1000; // 5 分钟
let refreshTimer: null | ReturnType<typeof setInterval> = null;

/** 待处理异常 */
const pendingAlerts = computed(
  () => overviewData.value?.pending_alerts ?? null,
);

/** 低效回路列表 */
const inefficientLoops = computed(
  () => overviewData.value?.inefficient_loops ?? [],
);

/** Cascader 选项递归类型 */
interface CascaderNodeOption {
  value: string;
  label: string;
  children?: CascaderNodeOption[];
}

/** 工厂节点树转 Cascader 选项 */
function toCascaderOptions(
  nodes: PlantNodeApi.PlantNode[],
): CascaderNodeOption[] {
  return nodes.map((node) => {
    const option: CascaderNodeOption = {
      value: node.id,
      label: node.name,
    };
    if (node.children && node.children.length > 0) {
      option.children = toCascaderOptions(node.children);
    }
    return option;
  });
}

const cascaderOptions = computed(() => toCascaderOptions(plantNodeTree.value));

/** 级联选择值（v-model 绑定） */
const cascaderValue = ref<string[]>([]);

/** 监听级联选择变更 */
watch(cascaderValue, (val) => {
  if (val && val.length > 0) {
    filter.plantId = val[val.length - 1];
  } else {
    filter.plantId = undefined;
  }
  loadOverview();
});

/** 粒度变更 */
function handleGranularityChange() {
  loadOverview();
}

/** 判断趋势是否为"好" */
function isTrendGood(trend: DashboardApi.Trend, goodWhenUp: boolean): boolean {
  if (trend === 'stable') return true;
  if (goodWhenUp) return trend === 'up';
  return trend === 'down';
}

/** 趋势箭头 */
function trendArrow(trend: DashboardApi.Trend): string {
  if (trend === 'up') return '↑';
  if (trend === 'down') return '↓';
  return '→';
}

/** 趋势颜色 */
function trendColor(trend: DashboardApi.Trend, goodWhenUp: boolean): string {
  if (trend === 'stable') return '#6c757d';
  return isTrendGood(trend, goodWhenUp) ? '#198754' : '#dc3545';
}

/** 格式化 delta */
function formatDelta(delta: number, unit: string): string {
  const sign = delta > 0 ? '+' : '';
  return `${sign}${delta}${unit}`;
}

/** 评分颜色（对齐 UI/UX v4.1 §3.1.4） */
function scoreColor(score: number): string {
  if (score >= 80) return '#198754';
  if (score >= 60) return '#ffc107';
  return '#dc3545';
}

/** 格式化百分比 */
function formatPercent(val: number | undefined): string {
  if (val === null || val === undefined || Number.isNaN(val)) return '—';
  return `${Number(val).toFixed(1)}%`;
}

/** 加载工厂节点树 */
async function loadPlantNodes() {
  try {
    const tree = await getPlantNodeTreeApi();
    plantNodeTree.value = tree || [];
  } catch {
    // 错误已由拦截器处理
  }
}

/** 加载工作台概览数据 */
async function loadOverview() {
  loading.value = true;
  try {
    const data = await getDashboardOverviewApi({
      plant_id: filter.plantId,
      granularity: filter.granularity,
    });
    overviewData.value = data;
    // 默认选中第一个低效回路
    if (data.inefficient_loops && data.inefficient_loops.length > 0) {
      if (!selectedLoop.value) {
        selectedLoop.value = data.inefficient_loops[0] ?? null;
      }
    } else {
      selectedLoop.value = null;
    }
    renderTrendChart();
    renderMiniChart();
  } catch {
    // 错误已由拦截器处理
  } finally {
    loading.value = false;
  }
}

/** 渲染趋势摘要图（7 天综合评分趋势） */
function renderTrendChart() {
  const trend = overviewData.value?.trend_summary;
  if (!trend || !trend.dates || trend.dates.length === 0) return;

  renderTrend({
    grid: { bottom: 30, containLabel: true, left: '2%', right: '2%', top: 40 },
    legend: { data: ['综合评分'], top: 5 },
    series: [
      {
        areaStyle: { opacity: 0.1 },
        data: trend.composite_scores,
        itemStyle: { color: '#0D6EFD' },
        lineStyle: { width: 2 },
        name: '综合评分',
        smooth: true,
        symbol: 'circle',
        symbolSize: 6,
        type: 'line',
      },
    ],
    tooltip: {
      axisPointer: { type: 'cross' },
      trigger: 'axis',
      valueFormatter: (val: unknown) => {
        if (val === null || val === undefined) return '—';
        return Number(val).toFixed(1);
      },
    },
    xAxis: {
      boundaryGap: false,
      data: trend.dates,
      type: 'category',
    },
    yAxis: {
      max: 100,
      min: 0,
      splitLine: { lineStyle: { color: '#E5E5E5', type: 'dashed' } },
      type: 'value',
    },
  });
}

/** 渲染选中回路小趋势缩略图（关键指标柱状） */
function renderMiniChart() {
  const loop = selectedLoop.value;
  if (!loop) return;

  renderMini({
    grid: { bottom: 24, containLabel: true, left: '2%', right: '2%', top: 24 },
    series: [
      {
        barWidth: '40%',
        data: [
          {
            itemStyle: { color: scoreColor(loop.composite_score) },
            value: Number(loop.composite_score.toFixed(1)),
          },
          {
            itemStyle: { color: '#0D6EFD' },
            value: Number(loop.key_metric.auto_mode_rate.toFixed(1)),
          },
          {
            itemStyle: { color: '#198754' },
            value: Number(loop.key_metric.steady_rate.toFixed(1)),
          },
        ],
        itemStyle: { borderRadius: [2, 2, 0, 0] },
        type: 'bar',
      },
    ],
    tooltip: {
      trigger: 'axis',
      valueFormatter: (val: unknown) => {
        if (val === null || val === undefined) return '—';
        return `${Number(val).toFixed(1)}`;
      },
    },
    xAxis: {
      axisLabel: { fontSize: 11, color: '#6C757D' },
      data: ['综合评分', '自控率', '平稳率'],
      type: 'category',
    },
    yAxis: {
      axisLabel: { fontSize: 11, color: '#6C757D' },
      max: 100,
      min: 0,
      splitLine: { lineStyle: { color: '#E5E5E5', type: 'dashed' } },
      type: 'value',
    },
  });
}

/** 点击低效回路行 */
function handleRowClick(record: DashboardApi.InefficientLoop) {
  selectedLoop.value = record;
  nextTick(() => renderMiniChart());
}

/** 跳转诊断详情 */
function handleGoDiagnosis(loopId: string) {
  router.push(`/diagnosis/detail/${loopId}`);
}

/** 跳转异常跟踪 */
function handleGoTracker() {
  router.push('/diagnosis/tracker');
}

/** 启动自动刷新 */
function startAutoRefresh() {
  stopAutoRefresh();
  refreshTimer = setInterval(() => {
    loadOverview();
  }, REFRESH_INTERVAL);
}

/** 停止自动刷新 */
function stopAutoRefresh() {
  if (refreshTimer) {
    clearInterval(refreshTimer);
    refreshTimer = null;
  }
}

/** 页面可见性变化 — 切回可见时立即刷新 */
function handleVisibilityChange() {
  if (document.visibilityState === 'visible') {
    loadOverview();
  }
}

watch(
  () => overviewData.value?.trend_summary,
  () => renderTrendChart(),
  { deep: true },
);

watch(
  () => selectedLoop.value,
  () => renderMiniChart(),
);

onMounted(() => {
  loadPlantNodes();
  loadOverview();
  startAutoRefresh();
  document.addEventListener('visibilitychange', handleVisibilityChange);
});

onUnmounted(() => {
  stopAutoRefresh();
  document.removeEventListener('visibilitychange', handleVisibilityChange);
});
</script>

<template>
  <Page title="性能总览首页">
    <!-- 待处理异常横幅 -->
    <Alert
      v-if="pendingAlerts"
      class="mb-3"
      type="warning"
      show-icon
      :message="`待处理诊断 ${pendingAlerts.open_diagnoses} 项 · 待处理 Tracker ${pendingAlerts.open_trackers} 项`"
    >
      <template #action>
        <Button type="link" size="small" @click="handleGoTracker">
          前往异常跟踪
        </Button>
      </template>
    </Alert>

    <!-- 顶部筛选栏 -->
    <Card class="mb-3" size="small" :body-style="{ padding: '12px 16px' }">
      <div class="flex flex-wrap items-center gap-3">
        <Cascader
          v-model:value="cascaderValue"
          :options="cascaderOptions"
          placeholder="全厂 / 装置 / 单元"
          style="width: 280px"
          allow-clear
          change-on-select
        />
        <RadioGroup
          v-model:value="filter.granularity"
          :options="granularityOptions"
          option-type="button"
          button-style="solid"
          @change="handleGranularityChange"
        />
        <Button type="primary" :loading="loading" @click="loadOverview">
          刷新
        </Button>
        <span class="ml-auto text-xs text-gray-400">每 5 分钟自动刷新</span>
      </div>
    </Card>

    <!-- 6 大 KPI 卡片区 -->
    <div class="mb-3 grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
      <Card
        v-for="cfg in kpiConfig"
        :key="cfg.key"
        size="small"
        :loading="loading"
        :body-style="{ padding: '16px' }"
      >
        <div class="flex flex-col">
          <span class="mb-1 text-xs text-gray-500">{{ cfg.title }}</span>
          <div class="flex items-baseline gap-1">
            <span
              v-if="overviewData"
              class="font-mono text-2xl font-bold"
              :style="{
                color: scoreColor(overviewData.kpi_cards[cfg.key].value),
              }"
            >
              {{ overviewData.kpi_cards[cfg.key].value.toFixed(1) }}
            </span>
            <span v-if="overviewData" class="text-xs text-gray-400">
              {{ overviewData.kpi_cards[cfg.key].unit }}
            </span>
          </div>
          <div v-if="overviewData" class="mt-1 flex items-center gap-1 text-xs">
            <span
              :style="{
                color: trendColor(
                  overviewData.kpi_cards[cfg.key].trend,
                  cfg.goodWhenUp,
                ),
              }"
            >
              {{ trendArrow(overviewData.kpi_cards[cfg.key].trend) }}
              {{
                formatDelta(
                  overviewData.kpi_cards[cfg.key].delta,
                  overviewData.kpi_cards[cfg.key].unit,
                )
              }}
            </span>
          </div>
        </div>
      </Card>
    </div>

    <!-- 中行：低效回路列表 + 选中回路摘要 -->
    <div class="mb-3 grid grid-cols-1 gap-3 lg:grid-cols-5">
      <!-- 低效回路列表（占 3/5） -->
      <Card class="lg:col-span-3" title="低效回路列表" size="small">
        <Table
          :columns="columns"
          :data-source="inefficientLoops"
          :loading="loading"
          :pagination="false"
          :row-key="(record: DashboardApi.InefficientLoop) => record.loop_id"
          :scroll="{ x: 870 }"
          size="small"
          :row-class-name="
            (record: DashboardApi.InefficientLoop) =>
              record.composite_score < 60 ? 'row-low-score' : ''
          "
          :custom-row="
            (record: DashboardApi.InefficientLoop) => ({
              onClick: () => handleRowClick(record),
              style: { cursor: 'pointer' },
            })
          "
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'loop_tag'">
              <span class="font-mono text-xs font-medium">
                {{ record.loop_tag }}
              </span>
            </template>
            <template v-else-if="column.key === 'composite_score'">
              <span
                class="font-mono font-bold"
                :style="{
                  color: scoreColor(record.composite_score),
                }"
              >
                {{ Number(record.composite_score).toFixed(1) }}
              </span>
            </template>
            <template v-else-if="column.key === 'diagnosis_labels'">
              <template
                v-if="
                  record.diagnosis_labels && record.diagnosis_labels.length > 0
                "
              >
                <Tag
                  v-for="label in record.diagnosis_labels"
                  :key="label"
                  :color="labelColorMap[label as DiagnosisLabel]"
                  class="mr-1"
                >
                  {{ labelNameMap[label as DiagnosisLabel] || label }}
                </Tag>
              </template>
              <span v-else class="text-gray-400">—</span>
            </template>
            <template v-else-if="column.key === 'auto_mode_rate'">
              <span class="font-mono text-xs">
                {{ formatPercent(record.key_metric.auto_mode_rate) }}
              </span>
            </template>
            <template v-else-if="column.key === 'steady_rate'">
              <span class="font-mono text-xs">
                {{ formatPercent(record.key_metric.steady_rate) }}
              </span>
            </template>
          </template>
        </Table>
      </Card>

      <!-- 选中回路摘要（占 2/5） -->
      <Card class="lg:col-span-2" title="选中回路摘要" size="small">
        <template v-if="selectedLoop">
          <div class="mb-3">
            <div class="flex items-center justify-between">
              <div>
                <span class="font-mono text-base font-semibold">
                  {{ selectedLoop.loop_tag }}
                </span>
                <span class="ml-2 text-sm text-gray-500">
                  {{ selectedLoop.loop_name }}
                </span>
              </div>
              <span
                class="font-mono text-xl font-bold"
                :style="{ color: scoreColor(selectedLoop.composite_score) }"
              >
                {{ Number(selectedLoop.composite_score).toFixed(1) }}
              </span>
            </div>
            <div class="mt-1 text-xs text-gray-400">
              {{ selectedLoop.plant_name }}
            </div>
          </div>

          <!-- 小趋势缩略图 -->
          <EchartsUI ref="miniChartRef" height="180px" />

          <!-- 关键指标 -->
          <div class="mt-3 grid grid-cols-2 gap-2">
            <Statistic
              title="自控率"
              :value="selectedLoop.key_metric.auto_mode_rate"
              :precision="1"
              suffix="%"
              :value-style="{ fontSize: '18px' }"
            />
            <Statistic
              title="平稳率"
              :value="selectedLoop.key_metric.steady_rate"
              :precision="1"
              suffix="%"
              :value-style="{ fontSize: '18px' }"
            />
          </div>

          <!-- 预诊标签 -->
          <div
            v-if="
              selectedLoop.diagnosis_labels &&
              selectedLoop.diagnosis_labels.length > 0
            "
            class="mt-3"
          >
            <div class="mb-1 text-xs text-gray-500">预诊标签</div>
            <Tag
              v-for="label in selectedLoop.diagnosis_labels"
              :key="label"
              :color="labelColorMap[label as DiagnosisLabel]"
              class="mr-1"
            >
              {{ labelNameMap[label as DiagnosisLabel] || label }}
            </Tag>
          </div>

          <!-- 快捷动作 -->
          <div class="mt-4 flex gap-2">
            <Button
              type="primary"
              size="small"
              @click="handleGoDiagnosis(selectedLoop.loop_id)"
            >
              进入诊断
            </Button>
            <Button size="small" @click="handleGoTracker">
              进入异常跟踪
            </Button>
          </div>
        </template>
        <div v-else class="flex h-64 items-center justify-center text-gray-400">
          请从左侧列表选择回路
        </div>
      </Card>
    </div>

    <!-- 下行：趋势摘要 -->
    <Card title="综合评分趋势" size="small" :loading="loading">
      <EchartsUI ref="trendChartRef" height="280px" />
    </Card>
  </Page>
</template>

<style scoped>
:deep(.row-low-score) {
  background-color: #fff1f0;
}

:deep(.row-low-score:hover) {
  background-color: #ffe7e5;
}
</style>
