<script lang="ts" setup>
/**
 * S4-DIAG-012 诊断统计报表页
 *
 * 对齐 IDS v3.2 §2.4 + PRD §4.4
 * - 顶部筛选栏（时间范围/装置/诊断标签/处理状态/粒度 day/week/month）
 * - ECharts 饼图：预诊标签分布（8 类标签）
 * - ECharts 折线图：处理效率趋势（resolvedCount + avgCloseDurationHours 双 Y 轴）
 * - ECharts 柱状图：闭环时长分布（0-24h/24-72h/72h+）
 * - 支持导出 PNG/Excel 按钮（FDS §5.4.5）
 */
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { DiagnosisApi, DiagnosisLabel } from '#/api/diagnosis';
import type { PlantNodeApi } from '#/api/plant-node';

import { onMounted, reactive, ref, watch } from 'vue';

import { Page } from '@vben/common-ui';
import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import { Button, Card, DatePicker, message, Select } from 'ant-design-vue';
import dayjs from 'dayjs';

import {
  exportDiagnosisAnalyticsApi,
  getDiagnosisAnalyticsApi,
} from '#/api/diagnosis';
import { getPlantNodeTreeApi } from '#/api/plant-node';
import {
  DIAGNOSIS_LABEL_COLOR_HEX_MAP,
  DIAGNOSIS_LABEL_OPTIONS,
} from '#/constants/diagnosis';
import { $t } from '#/locales';
import { flattenNodes } from '#/utils/plant-node';

defineOptions({ name: 'DiagnosisStatistics' });

const loading = ref(false);
const exporting = ref(false);
const analyticsData = ref<DiagnosisApi.AnalyticsResult | null>(null);
const plantNodes = ref<PlantNodeApi.PlantNode[]>([]);

const filter = reactive({
  timeRange: [dayjs().subtract(30, 'day'), dayjs()] as [
    dayjs.Dayjs,
    dayjs.Dayjs,
  ],
  plantNodeId: undefined as string | undefined,
  diagnosisLabel: undefined as DiagnosisLabel | undefined,
  actionStatus: undefined as DiagnosisApi.ActionStatus | undefined,
  granularity: 'day' as DiagnosisApi.Granularity,
});

/** 8 类诊断标签选项 */
const labelOptions = DIAGNOSIS_LABEL_OPTIONS;

/** 标签颜色映射（用于饼图） */
const labelColorHexMap = DIAGNOSIS_LABEL_COLOR_HEX_MAP;

/** 处理状态选项 */
const statusOptions: { label: string; value: DiagnosisApi.ActionStatus }[] = [
  { label: '待处理', value: 'PENDING' },
  { label: '处理中', value: 'IN_PROGRESS' },
  { label: '已实施', value: 'IMPLEMENTED' },
  { label: '已忽略', value: 'IGNORED' },
];

/** 粒度选项 */
const granularityOptions: { label: string; value: DiagnosisApi.Granularity }[] =
  [
    { label: '天', value: 'day' },
    { label: '周', value: 'week' },
    { label: '月', value: 'month' },
  ];

// ECharts refs
const pieChartRef = ref<EchartsUIType>();
const trendChartRef = ref<EchartsUIType>();
const barChartRef = ref<EchartsUIType>();

const { renderEcharts: renderPie, getChartInstance: getPieInstance } =
  useEcharts(pieChartRef);
const { renderEcharts: renderTrend, getChartInstance: getTrendInstance } =
  useEcharts(trendChartRef);
const { renderEcharts: renderBar, getChartInstance: getBarInstance } =
  useEcharts(barChartRef);

/** 加载工厂节点 */
async function loadPlantNodes() {
  try {
    const tree = await getPlantNodeTreeApi();
    plantNodes.value = flattenNodes(tree);
  } catch {
    // 错误已由拦截器处理
  }
}

/** 加载报表数据 */
async function loadData() {
  if (!filter.timeRange || filter.timeRange.length !== 2) {
    message.warning('请选择时间范围');
    return;
  }
  const [start, end] = filter.timeRange;
  if (!start || !end) {
    message.warning('请选择时间范围');
    return;
  }
  loading.value = true;
  try {
    const data = await getDiagnosisAnalyticsApi({
      startTime: start.format('YYYY-MM-DD HH:mm:ss'),
      endTime: end.format('YYYY-MM-DD HH:mm:ss'),
      plantNodeId: filter.plantNodeId,
      diagnosisLabel: filter.diagnosisLabel,
      actionStatus: filter.actionStatus,
      granularity: filter.granularity,
    });
    analyticsData.value = data;
    renderAllCharts();
  } catch {
    // 错误已由拦截器处理
  } finally {
    loading.value = false;
  }
}

/** 渲染所有图表 */
function renderAllCharts() {
  renderPieChart();
  renderTrendChart();
  renderBarChart();
}

/** 渲染标签分布饼图 */
function renderPieChart() {
  const dist = analyticsData.value?.labelDistribution || [];
  if (dist.length === 0) {
    renderPie({
      title: { left: 'center', text: '暂无数据' },
    });
    return;
  }

  renderPie({
    legend: { bottom: 0, orient: 'horizontal' },
    series: [
      {
        avoidLabelOverlap: false,
        data: dist.map((d) => ({
          itemStyle: { color: labelColorHexMap[d.label] },
          name: d.labelName,
          value: d.count,
        })),
        emphasis: {
          itemStyle: {
            shadowBlur: 10,
            shadowColor: 'rgba(0, 0, 0, 0.5)',
            shadowOffsetX: 0,
          },
        },
        label: { formatter: '{b}: {c} ({d}%)', show: true },
        radius: ['40%', '70%'],
        type: 'pie',
      },
    ],
    tooltip: { trigger: 'item' },
  });
}

/** 渲染处理效率趋势折线图（双 Y 轴） */
function renderTrendChart() {
  const trend = analyticsData.value?.efficiencyTrend;
  if (!trend || !trend.timestamps || trend.timestamps.length === 0) {
    renderTrend({
      title: { left: 'center', text: '暂无数据' },
    });
    return;
  }

  renderTrend({
    backgroundColor: 'transparent',
    grid: {
      bottom: 30,
      containLabel: true,
      left: '2%',
      right: '2%',
      top: 50,
    },
    legend: {
      data: ['已解决数', '平均闭环时长'],
      top: 5,
    },
    series: [
      {
        data: trend.resolvedCount,
        itemStyle: { color: '#52c41a' },
        name: '已解决数',
        smooth: true,
        type: 'line',
        yAxisIndex: 0,
      },
      {
        data: trend.avgCloseDurationHours,
        itemStyle: { color: '#fa8c16' },
        name: '平均闭环时长',
        smooth: true,
        type: 'line',
        yAxisIndex: 1,
      },
    ],
    tooltip: {
      axisPointer: { type: 'cross' },
      trigger: 'axis',
    },
    xAxis: {
      axisLabel: {
        formatter: (val: string) => {
          try {
            const d = new Date(val);
            const mm = String(d.getMonth() + 1).padStart(2, '0');
            const dd = String(d.getDate()).padStart(2, '0');
            return `${mm}-${dd}`;
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
        axisLabel: { formatter: '{value}' },
        name: '已解决数',
        nameTextStyle: { color: '#52c41a' },
        type: 'value',
      },
      {
        axisLabel: { formatter: '{value}h' },
        name: '平均闭环时长',
        nameTextStyle: { color: '#fa8c16' },
        splitLine: { show: false },
        type: 'value',
      },
    ],
  });
}

/** 闭环时长区间颜色 */
function getRangeColor(range: string): string {
  if (range === '0-24h') return '#52c41a';
  if (range === '24-72h') return '#faad14';
  return '#ff4d4f';
}

/** 渲染闭环时长分布柱状图 */
function renderBarChart() {
  const dist = analyticsData.value?.closeDurationDistribution || [];
  if (dist.length === 0) {
    renderBar({
      title: { left: 'center', text: '暂无数据' },
    });
    return;
  }

  renderBar({
    backgroundColor: 'transparent',
    grid: {
      bottom: 40,
      containLabel: true,
      left: '2%',
      right: '2%',
      top: 30,
    },
    series: [
      {
        barWidth: '50%',
        data: dist.map((d) => ({
          itemStyle: { color: getRangeColor(d.range) },
          value: d.count,
        })),
        name: '回路数',
        type: 'bar',
      },
    ],
    tooltip: {
      axisPointer: { type: 'shadow' },
      trigger: 'axis',
    },
    xAxis: {
      data: dist.map((d) => d.range),
      type: 'category',
    },
    yAxis: { type: 'value' },
  });
}

/** 生成导出文件名（FDS §5.4.5 规范：CLPM-诊断统计报表-[装置]-[日期范围]） */
function buildExportName(ext: string): string {
  const [start, end] = filter.timeRange;
  const plantName =
    plantNodes.value.find((n) => n.id === filter.plantNodeId)?.name ?? '全部装置';
  const startStr = start?.format('YYYYMMDD') ?? '';
  const endStr = end?.format('YYYYMMDD') ?? '';
  return `CLPM-诊断统计报表-${plantName}-${startStr}_${endStr}.${ext}`;
}

/** 导出 PNG（FDS §5.4.5：使用 ECharts getDataURL） */
function handleExportPng() {
  const instances = [
    { inst: getPieInstance(), name: '标签分布' },
    { inst: getTrendInstance(), name: '处理效率趋势' },
    { inst: getBarInstance(), name: '闭环时长分布' },
  ];
  for (const { inst, name } of instances) {
    if (!inst) continue;
    const url = inst.getDataURL({
      type: 'png',
      pixelRatio: 2,
      backgroundColor: '#fff',
    });
    const link = document.createElement('a');
    link.href = url;
    link.download = `${buildExportName('png').replace('.png', '')}-${name}.png`;
    link.click();
  }
  message.success('PNG 导出完成');
}

/** 导出 Excel（FDS §5.4.5） */
async function handleExportExcel() {
  if (!filter.timeRange || filter.timeRange.length !== 2) {
    message.warning('请选择时间范围');
    return;
  }
  const [start, end] = filter.timeRange;
  if (!start || !end) {
    message.warning('请选择时间范围');
    return;
  }
  exporting.value = true;
  try {
    await exportDiagnosisAnalyticsApi({
      startTime: start.format('YYYY-MM-DD HH:mm:ss'),
      endTime: end.format('YYYY-MM-DD HH:mm:ss'),
      plantNodeId: filter.plantNodeId,
      diagnosisLabel: filter.diagnosisLabel,
      actionStatus: filter.actionStatus,
      granularity: filter.granularity,
    });
    message.success(`Excel 导出任务已提交，文件名：${buildExportName('xlsx')}`);
  } catch {
    // 错误已由拦截器处理
  } finally {
    exporting.value = false;
  }
}

function handleSearch() {
  loadData();
}

// 粒度切换时自动刷新
watch(
  () => filter.granularity,
  () => {
    loadData();
  },
);

onMounted(() => {
  loadPlantNodes();
  loadData();
});
</script>

<template>
  <Page :title="$t('diagnosis.statistics.title')">
    <!-- 筛选栏 -->
    <Card class="mb-4">
      <div class="flex flex-wrap items-center gap-3">
        <DatePicker.RangePicker
          v-model:value="filter.timeRange"
          :show-time="{ format: 'HH:mm' }"
          format="YYYY-MM-DD HH:mm"
          :placeholder="['开始时间', '结束时间']"
        />
        <Select
          v-model:value="filter.plantNodeId"
          :placeholder="$t('diagnosis.list.plantNodePlaceholder')"
          style="width: 220px"
          allow-clear
          :options="plantNodes.map((n) => ({ label: n.name, value: n.id }))"
          @change="handleSearch"
        />
        <Select
          v-model:value="filter.diagnosisLabel"
          :placeholder="$t('diagnosis.list.labelPlaceholder')"
          style="width: 160px"
          allow-clear
          :options="labelOptions"
          @change="handleSearch"
        />
        <Select
          v-model:value="filter.actionStatus"
          placeholder="处理状态"
          style="width: 140px"
          allow-clear
          :options="statusOptions"
          @change="handleSearch"
        />
        <Select
          v-model:value="filter.granularity"
          style="width: 120px"
          :options="granularityOptions"
        />
        <Button type="primary" :loading="loading" @click="handleSearch">
          查询
        </Button>
        <Button @click="handleExportPng"> 导出 PNG </Button>
        <Button :loading="exporting" @click="handleExportExcel">
          导出 Excel
        </Button>
      </div>
    </Card>

    <!-- 标签分布饼图 -->
    <Card
      :title="$t('diagnosis.statistics.labelDistribution')"
      class="mb-4"
      :loading="loading"
    >
      <EchartsUI ref="pieChartRef" height="360px" />
    </Card>

    <div class="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <!-- 处理效率趋势折线图 -->
      <Card
        :title="$t('diagnosis.statistics.efficiencyTrend')"
        :loading="loading"
      >
        <EchartsUI ref="trendChartRef" height="320px" />
      </Card>

      <!-- 闭环时长分布柱状图 -->
      <Card
        :title="$t('diagnosis.statistics.closeDuration')"
        :loading="loading"
      >
        <EchartsUI ref="barChartRef" height="320px" />
      </Card>
    </div>
  </Page>
</template>
