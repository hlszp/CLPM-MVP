<script lang="ts" setup>
/**
 * 统计报告-诊断报告（/reports/diagnosis，IA 优化 P0 新建）
 *
 * 设计文档：docs/设计文档/IA 优化/CLPM-IA优化实施方案-0822.md §2.2
 * 上半部分：分类占比（饼图）/ 置信度分布（柱图）/ TOP 异常回路；
 * 下半部分：诊断记录列表（复用 /diagnosis/runs），支持导出 CSV。
 * 统计数据来自 GET /reports/diagnosis-statistics（基于 DiagnosisRun 表）。
 */
import type { EchartsUIType } from '@vben/plugins/echarts';

import { onMounted, ref, watch } from 'vue';

import { Page } from '@vben/common-ui';
import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import {
  message,
  RangePicker,
  Table,
  Tag,
  TreeSelect,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import {
  type DiagnosisApi,
  exportDiagnosisRunsApi,
  getDiagnosisRunsApi,
} from '#/api/diagnosis';
import { getPlantNodeTreeApi } from '#/api/plant-node';
import {
  getReportDiagnosisStatisticsApi,
  type ReportsApi,
} from '#/api/reports';
import {
  ClpmDataCanvas,
  ClpmPageToolbar,
  ClpmToolbarButton,
} from '#/components/clpm';
import { useEchartsPreset } from '#/composables/use-echarts-preset';
import { showPageHelp } from '#/composables/use-page-toolbar';

defineOptions({ name: 'ReportsDiagnosis' });

const loading = ref(false);
const exporting = ref(false);
const stats = ref<null | ReportsApi.DiagnosisStatisticsData>(null);

const dateRange = ref<[dayjs.Dayjs, dayjs.Dayjs]>([
  dayjs().subtract(30, 'day'),
  dayjs(),
]);
const plantNodeId = ref<string | undefined>();
const plantTree = ref<any[]>([]);

// 诊断记录列表
const records = ref<DiagnosisApi.RunListItem[]>([]);
const total = ref(0);
const page = ref(1);
const pageSize = ref(10);

const { getEchartsBase, getTooltipPreset } = useEchartsPreset();
const pieRef = ref<EchartsUIType>();
const barRef = ref<EchartsUIType>();
const { renderEcharts: renderPie } = useEcharts(pieRef);
const { renderEcharts: renderBar } = useEcharts(barRef);

const CATEGORY_COLORS = [
  '#1d4ed8',
  '#b45309',
  '#7c3aed',
  '#0f766e',
  '#be123c',
  '#a16207',
  '#475569',
  '#15803d',
];

const topColumns = [
  { dataIndex: 'loopTagName', title: '回路' },
  { dataIndex: 'unitPath', title: '装置.单元', width: 180 },
  { dataIndex: 'runCount', title: '诊断次数', width: 90 },
  { dataIndex: 'highCount', title: '高严重度', width: 90 },
  { dataIndex: 'latestCategoryLabel', title: '最近分类', width: 180 },
];

const recordColumns = [
  { dataIndex: 'createdAt', title: '时间', width: 160 },
  { dataIndex: 'loopTagName', title: '回路', width: 160 },
  { dataIndex: 'primaryCategoryLabel', title: '主分类', width: 180 },
  { dataIndex: 'severity', title: '严重度', width: 90 },
  { dataIndex: 'primaryConfidence', title: '置信度', width: 90 },
  { dataIndex: 'triggerTypeLabel', title: '触发', width: 100 },
  { dataIndex: 'status', title: '状态', width: 90 },
];

function queryParams() {
  const [start, end] = dateRange.value ?? [];
  return {
    startDate: start?.format('YYYY-MM-DD'),
    endDate: end?.format('YYYY-MM-DD'),
    plantNodeId: plantNodeId.value,
  };
}

async function loadPlants() {
  try {
    plantTree.value = await getPlantNodeTreeApi();
  } catch {
    plantTree.value = [];
  }
}

async function loadStats() {
  try {
    stats.value = await getReportDiagnosisStatisticsApi(queryParams());
  } catch {
    stats.value = null;
  }
}

async function loadRecords() {
  const [start, end] = dateRange.value ?? [];
  try {
    const res = await getDiagnosisRunsApi({
      page: page.value,
      pageSize: pageSize.value,
      startTime: start?.startOf('day').toISOString(),
      endTime: end?.endOf('day').toISOString(),
    });
    records.value = res.items;
    total.value = res.total;
  } catch {
    records.value = [];
    total.value = 0;
  }
}

async function load() {
  loading.value = true;
  await Promise.all([loadStats(), loadRecords()]);
  loading.value = false;
  renderPieChart();
  renderBarChart();
}

async function handleExport() {
  exporting.value = true;
  try {
    const [start, end] = dateRange.value ?? [];
    const blob = await exportDiagnosisRunsApi({
      startTime: start?.startOf('day').toISOString(),
      endTime: end?.endOf('day').toISOString(),
    });
    const url = URL.createObjectURL(
      new Blob([blob as unknown as BlobPart], {
        type: 'text/csv;charset=utf-8',
      }),
    );
    const a = document.createElement('a');
    a.href = url;
    a.download = `diagnosis_runs_${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  } catch {
    message.error('导出失败');
  } finally {
    exporting.value = false;
  }
}

function renderPieChart() {
  const dist = stats.value?.categoryDistribution ?? [];
  if (dist.length === 0) return;
  renderPie({
    ...getEchartsBase(),
    tooltip: {
      ...getTooltipPreset(),
      trigger: 'item' as const,
      formatter: '{b}: {c} ({d}%)',
    },
    legend: {
      ...getEchartsBase().legend,
      orient: 'vertical',
      right: 8,
      top: 'center',
    },
    series: [
      {
        type: 'pie',
        radius: ['40%', '68%'],
        center: ['38%', '50%'],
        avoidLabelOverlap: false,
        label: { show: false },
        labelLine: { show: false },
        itemStyle: { borderColor: 'hsl(var(--card))', borderWidth: 2 },
        data: dist.map((d, i) => ({
          name: d.label,
          value: d.count,
          itemStyle: { color: CATEGORY_COLORS[i % CATEGORY_COLORS.length] },
        })),
      },
    ],
  });
}

function renderBarChart() {
  const dist = stats.value?.confidenceDistribution ?? [];
  if (dist.length === 0) return;
  renderBar({
    ...getEchartsBase(),
    tooltip: { ...getTooltipPreset(), trigger: 'axis' as const },
    xAxis: {
      ...getEchartsBase().xAxis,
      data: dist.map((d) => d.label),
      axisLabel: { ...getEchartsBase().xAxis.axisLabel, interval: 0, fontSize: 10 },
    },
    yAxis: { ...getEchartsBase().yAxis, minInterval: 1 },
    series: [
      {
        type: 'bar',
        barWidth: '50%',
        itemStyle: { color: '#1d4ed8', borderRadius: [3, 3, 0, 0] },
        data: dist.map((d) => d.count),
      },
    ],
  });
}

function handleHelp() {
  showPageHelp({
    title: '诊断报告 帮助',
    content: `
      <p><b>上半区</b>：诊断结论的分类占比、置信度分布与 TOP 异常回路。</p>
      <p><b>下半区</b>：诊断记录明细，可按时间窗导出 CSV。统计基于 DiagnosisRun 表。</p>
    `,
  });
}

function onTableChange(pag: { current?: number; pageSize?: number }) {
  page.value = pag.current ?? 1;
  pageSize.value = pag.pageSize ?? 10;
  loadRecords();
}

watch([dateRange, plantNodeId], () => {
  page.value = 1;
  load();
});

onMounted(() => {
  loadPlants();
  load();
});
</script>

<template>
  <Page>
    <ClpmPageToolbar
      :loading="loading"
      subtitle="诊断分类占比 · 置信度分布 · 异常回路 · 记录导出"
      title="诊断报告"
    >
      <template #actions>
        <ClpmToolbarButton
          icon="ant-design:question-circle-outlined"
          label="帮助"
          @click="handleHelp"
        />
        <ClpmToolbarButton
          :loading="exporting"
          icon="ant-design:download-outlined"
          label="导出CSV"
          @click="handleExport"
        />
        <ClpmToolbarButton
          icon="ant-design:sync-outlined"
          label="刷新"
          @click="load"
        />
      </template>
    </ClpmPageToolbar>

    <!-- 统一筛选条 -->
    <div class="reports-filter-bar">
      <span class="reports-filter-bar__label">时间范围</span>
      <RangePicker v-model:value="dateRange" allow-clear />
      <span class="reports-filter-bar__label">装置</span>
      <TreeSelect
        v-model:value="plantNodeId"
        :tree-data="plantTree"
        :field-names="{ label: 'name', value: 'id', children: 'children' }"
        allow-clear
        placeholder="全部装置"
        style="width: 240px"
        tree-default-expand-all
      />
    </div>

    <!-- 上半部分：分类占比 / 置信度分布 / TOP 异常回路 -->
    <div class="reports-top-grid">
      <ClpmDataCanvas
        title="分类占比"
        :loading="loading"
        :empty="!stats?.categoryDistribution?.length"
        empty-text="暂无诊断数据"
      >
        <EchartsUI ref="pieRef" height="240px" />
      </ClpmDataCanvas>
      <ClpmDataCanvas
        title="置信度分布"
        :loading="loading"
        :empty="!stats?.confidenceDistribution?.length"
        empty-text="暂无置信度数据"
      >
        <EchartsUI ref="barRef" height="240px" />
      </ClpmDataCanvas>
      <ClpmDataCanvas
        title="TOP 异常回路"
        :empty="!stats?.topAbnormalLoops?.length"
        empty-text="暂无异常回路"
      >
        <Table
          :columns="topColumns"
          :data-source="stats?.topAbnormalLoops ?? []"
          :pagination="false"
          row-key="loopId"
          size="small"
        />
      </ClpmDataCanvas>
    </div>

    <!-- 下半部分：诊断记录列表 -->
    <ClpmDataCanvas
      class="reports-records-canvas"
      title="诊断记录"
      :loading="loading"
    >
      <Table
        :columns="recordColumns"
        :data-source="records"
        :pagination="{
          current: page,
          pageSize,
          total,
          showSizeChanger: true,
          showTotal: (t: number) => `共 ${t} 条`,
        }"
        row-key="id"
        size="small"
        @change="onTableChange"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.dataIndex === 'severity'">
            <Tag
              v-if="record.severity"
              :color="
                record.severity === 'HIGH'
                  ? 'red'
                  : record.severity === 'MEDIUM'
                    ? 'orange'
                    : 'default'
              "
            >
              {{ record.severity === 'HIGH' ? '高' : record.severity === 'MEDIUM' ? '中' : '低' }}
            </Tag>
            <span v-else class="text-neutral-400">—</span>
          </template>
          <template v-else-if="column.dataIndex === 'primaryConfidence'">
            {{ record.primaryConfidence != null ? `${(record.primaryConfidence * 100).toFixed(0)}%` : '—' }}
          </template>
          <template v-else-if="column.dataIndex === 'createdAt'">
            {{ dayjs(record.createdAt).format('YYYY-MM-DD HH:mm') }}
          </template>
        </template>
      </Table>
    </ClpmDataCanvas>
  </Page>
</template>

<style scoped>
.reports-filter-bar {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 8px 12px;
  margin: 8px 0;
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border));
  border-radius: calc(var(--radius) * 1px);
}

.reports-filter-bar__label {
  font-size: 12px;
  color: hsl(var(--muted-foreground));
}

.reports-top-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 12px;
}

.reports-records-canvas {
  margin-bottom: 12px;
}
</style>
