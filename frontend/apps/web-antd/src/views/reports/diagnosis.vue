<script lang="ts" setup>
/**
 * 统计报告-诊断报告（/reports/diagnosis，IA 优化 P0 + P3 补齐）
 *
 * 设计文档：docs/设计文档/IA 优化/CLPM-IA优化实施方案-0822.md §2.2 / §6
 * 上半部分（P3 四卡片 2×2 栅格）：
 *   - 诊断分类占比（饼图，用户偏好：无引线）
 *   - 置信度分布（柱图）
 *   - 分类趋势（近 30 天折线：总数 vs 高严重度）
 *   - TOP 异常回路表
 * 下半部分：诊断记录列表（R1 自持：/reports/diagnosis-runs，支持装置下钻
 * plantNodeId 透传，修复报告页明细装置筛选失效 P-07），支持导出 CSV。
 * 统计数据来自 GET /reports/diagnosis-statistics（基于 DiagnosisRun 表）。
 * 诊断模块停用时本页照常可用（历史归档口径，灰色横幅提示）。
 */
import type { EchartsUIType } from '@vben/plugins/echarts';

import type { DiagnosisApi } from '#/api/diagnosis';

import { nextTick, onMounted, ref, watch } from 'vue';

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

import { getPlantNodeTreeApi } from '#/api/plant-node';
import {
  exportReportDiagnosisRunsApi,
  getReportDiagnosisRunsApi,
  getReportDiagnosisStatisticsApi,
  type ReportsApi,
} from '#/api/reports';
import {
  ClpmDataCanvas,
  ClpmModuleArchivedBanner,
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

const { getEchartsBase, getLineSeriesPreset, getSeriesColor, getTooltipPreset } =
  useEchartsPreset();
const pieRef = ref<EchartsUIType>();
const barRef = ref<EchartsUIType>();
const trendRef = ref<EchartsUIType>();
const { renderEcharts: renderPie } = useEcharts(pieRef);
const { renderEcharts: renderBar } = useEcharts(barRef);
const { renderEcharts: renderTrend } = useEcharts(trendRef);

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
  { dataIndex: 'loopTagName', title: '回路', width: 180 },
  { dataIndex: 'unitPath', title: '装置.单元', width: 180 },
  { dataIndex: 'runCount', title: '诊断次数', width: 90 },
  { dataIndex: 'highCount', title: '高严重度', width: 90 },
  { dataIndex: 'latestCategoryLabel', title: '最近分类', width: 150 },
  { dataIndex: 'latestSeverity', title: '严重度', width: 80 },
  { dataIndex: 'latestConfidence', title: '置信度', width: 80 },
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
    // R1 自持端点（P0-7）：plantNodeId 透传，修复装置筛选对明细不生效（P-07）
    const res = await getReportDiagnosisRunsApi({
      page: page.value,
      pageSize: pageSize.value,
      startDate: start?.format('YYYY-MM-DD'),
      endDate: end?.format('YYYY-MM-DD'),
      plantNodeId: plantNodeId.value,
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
  await nextTick();
  renderPieChart();
  renderBarChart();
  renderTrendChart();
}

async function handleExport() {
  exporting.value = true;
  try {
    const [start, end] = dateRange.value ?? [];
    // R1 自持导出（P0-7）：筛选口径与明细列表一致（含装置下钻）
    const blob = await exportReportDiagnosisRunsApi({
      startDate: start?.format('YYYY-MM-DD'),
      endDate: end?.format('YYYY-MM-DD'),
      plantNodeId: plantNodeId.value,
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

// ---------- 图表：诊断分类占比饼图（无引线）----------
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
      orient: 'horizontal',
      bottom: 4,
      left: 'center',
      type: 'scroll',
      textStyle: { fontSize: 11 },
    },
    grid: { left: 10, right: 10, top: 10, bottom: 50 },
    series: [
      {
        type: 'pie',
        radius: ['42%', '68%'],
        center: ['50%', '42%'],
        avoidLabelOverlap: false,
        label: { show: false },
        labelLine: { show: false }, // 用户偏好：饼图不用引线
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

// ---------- 图表：置信度分布柱图 ----------
function renderBarChart() {
  const dist = stats.value?.confidenceDistribution ?? [];
  if (dist.length === 0) return;
  renderBar({
    ...getEchartsBase(),
    tooltip: {
      ...getTooltipPreset(),
      trigger: 'axis' as const,
      axisPointer: { type: 'shadow' as const },
      formatter: (params: any) => {
        const arr = Array.isArray(params) ? params : [params];
        const lines: string[] = [arr[0]?.name ?? ''];
        for (const p of arr) {
          const origin = p.data as { count: number; ratio: number };
          lines.push(
            `${p.seriesName}: ${origin.count}（${(origin.ratio * 100).toFixed(1)}%）`,
          );
        }
        return lines.join('<br/>');
      },
    },
    grid: { left: 40, right: 20, top: 20, bottom: 40 },
    xAxis: {
      ...getEchartsBase().xAxis,
      data: dist.map((d) => d.label),
      axisLabel: { ...getEchartsBase().xAxis.axisLabel, interval: 0, fontSize: 10 },
    },
    yAxis: { ...getEchartsBase().yAxis, minInterval: 1, max: 100, name: '占比%' },
    series: [
      {
        name: '占比',
        type: 'bar',
        barWidth: '55%',
        itemStyle: { color: '#1d4ed8', borderRadius: [3, 3, 0, 0] },
        data: dist.map((d) => ({
          value: (d.ratio * 100).toFixed(1),
          count: d.count,
          ratio: d.ratio,
        })),
      },
    ],
  });
}

// ---------- P3：图表·诊断趋势折线（近 30 天：总数 + 高严重度）----------
function renderTrendChart() {
  const tr = stats.value?.trend ?? [];
  if (tr.length === 0) return;
  renderTrend({
    ...getEchartsBase(),
    tooltip: { ...getTooltipPreset(), trigger: 'axis' as const },
    legend: { data: ['诊断总数', '高严重度'], top: 4 },
    grid: { left: 40, right: 20, top: 36, bottom: 40 },
    xAxis: {
      ...getEchartsBase().xAxis,
      data: tr.map((p) => p.date),
      axisLabel: { ...getEchartsBase().xAxis.axisLabel, fontSize: 10 },
    },
    yAxis: { ...getEchartsBase().yAxis, minInterval: 1, name: '次' },
    series: [
      {
        name: '诊断总数',
        data: tr.map((p) => p.total),
        ...getLineSeriesPreset(getSeriesColor('info')),
      },
      {
        name: '高严重度',
        data: tr.map((p) => p.high),
        ...getLineSeriesPreset(getSeriesColor('error')),
      },
    ],
  });
}

function sevInfo(sev?: null | string) {
  switch (sev) {
    case 'HIGH': {
      return { color: 'red', text: '高' };
    }
    case 'LOW': {
      return { color: 'default', text: '低' };
    }
    case 'MEDIUM': {
      return { color: 'orange', text: '中' };
    }
    default: {
      return null;
    }
  }
}

function handleHelp() {
  showPageHelp({
    title: '诊断报告 帮助',
    content: `
      <p><b>上半区（2×2）</b>：
        <ul>
          <li>分类占比：饼图（无引线），展示窗口内各诊断分类的数量占比</li>
          <li>置信度分布：柱状图按区间展示占比与计数</li>
          <li>分类趋势：近 30 天每日诊断总数 + 高严重度数折线</li>
          <li>TOP 异常回路：诊断出现次数最多 / 严重度最高的回路</li>
        </ul>
      </p>
      <p><b>下半区</b>：诊断记录明细，时间/装置/分类筛选联动，可导出 CSV（上限 5000 行）。统计基于 DiagnosisRun 表。</p>
      <p><b>模块停用</b>：诊断模块停用时本页照常可用，展示历史数据归档，查询与导出不受影响。</p>
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
      subtitle="诊断分类占比 · 置信度分布 · 分类趋势 · TOP 异常回路 · 记录导出"
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

    <!-- P0-5：诊断模块停用时灰色归档横幅（历史数据可查询导出） -->
    <ClpmModuleArchivedBanner :modules="['diagnosis']" />

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

    <!-- 上半部分：P3 2×2 栅格（占比 / 置信度 / 趋势 / TOP回路） -->
    <div class="reports-top-grid">
      <ClpmDataCanvas
        title="诊断分类占比"
        :loading="loading"
        :empty="!stats?.categoryDistribution?.length"
        empty-text="暂无诊断数据"
      >
        <EchartsUI ref="pieRef" height="260px" />
      </ClpmDataCanvas>

      <ClpmDataCanvas
        title="置信度分布（占比）"
        :loading="loading"
        :empty="!stats?.confidenceDistribution?.length"
        empty-text="暂无置信度数据"
      >
        <EchartsUI ref="barRef" height="260px" />
      </ClpmDataCanvas>

      <ClpmDataCanvas
        title="诊断趋势（近 30 天）"
        :loading="loading"
        :empty="!stats?.trend?.length"
        empty-text="暂无趋势数据"
      >
        <EchartsUI ref="trendRef" height="260px" />
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
          :scroll="{ x: 840 }"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.dataIndex === 'latestSeverity'">
              <Tag v-if="sevInfo(record.latestSeverity)" :color="sevInfo(record.latestSeverity)!.color">
                {{ sevInfo(record.latestSeverity)!.text }}
              </Tag>
              <span v-else class="text-neutral-400">—</span>
            </template>
            <template v-else-if="column.dataIndex === 'latestConfidence'">
              <span v-if="record.latestConfidence != null">
                {{ (record.latestConfidence * 100).toFixed(0) }}%
              </span>
              <span v-else class="text-neutral-400">—</span>
            </template>
            <template v-else-if="column.dataIndex === 'highCount'">
              <span
                :class="Number(record.highCount) > 0 ? 'text-red-600 font-medium' : ''"
              >
                {{ record.highCount ?? 0 }}
              </span>
            </template>
          </template>
        </Table>
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
  flex-wrap: wrap;
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
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 12px;
}

.reports-records-canvas {
  margin-bottom: 12px;
}

@media (max-width: 1200px) {
  .reports-top-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
