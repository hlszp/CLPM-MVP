<script lang="ts" setup>
/**
 * 统计报告-收益报告（/reports/benefit，IA 优化 P0 新建）
 *
 * 设计文档：docs/设计文档/IA 优化/CLPM-IA优化实施方案-0822.md §2.2
 * - 整定前后 KPI 对比（综合评分/有效自控率/PV 好值率/振荡率）
 * - 自控率提升曲线（按月）
 * - 装置标杆表（均分/自控率/改善幅度）
 * 仅技术指标，不做经济收益（避免口径争议）。
 * 处置/整定模块停用时本页照常可用（历史归档口径，灰色横幅提示，P0-5）。
 */
import type { EchartsUIType } from '@vben/plugins/echarts';

import { computed, onMounted, ref, watch } from 'vue';

import { Page } from '@vben/common-ui';
import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import {
  Dropdown,
  Menu,
  message,
  RangePicker,
  Table,
  Tag,
  TreeSelect,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import { getPlantNodeTreeApi } from '#/api/plant-node';
import {
  getReportBenefitApi,
  type ReportsApi,
} from '#/api/reports';
import {
  ClpmDataCanvas,
  ClpmKpiCard,
  ClpmModuleArchivedBanner,
  ClpmPageToolbar,
  ClpmToolbarButton,
} from '#/components/clpm';
import { useEchartsPreset } from '#/composables/use-echarts-preset';
import { showPageHelp } from '#/composables/use-page-toolbar';
import { exportData } from '#/utils/export';

defineOptions({ name: 'ReportsBenefit' });

const loading = ref(false);
const data = ref<null | ReportsApi.BenefitData>(null);

const dateRange = ref<[dayjs.Dayjs, dayjs.Dayjs]>([
  dayjs().subtract(180, 'day'),
  dayjs(),
]);
const plantNodeId = ref<string | undefined>();
const plantTree = ref<any[]>([]);

const { getEchartsBase, getLineSeriesPreset, getSeriesColor, getTooltipPreset } =
  useEchartsPreset();
const chartRef = ref<EchartsUIType>();
const { renderEcharts } = useEcharts(chartRef);

const summaryCards = computed(() => {
  const d = data.value;
  return [
    {
      key: 'closedOrderCount',
      title: '已闭环处置',
      value: d?.closedOrderCount ?? 0,
      unit: '单',
      status: 'info' as const,
      icon: 'lucide:clipboard-check',
    },
    {
      key: 'tuningCount',
      title: '整定记录',
      value: d?.tuningCount ?? 0,
      unit: '次',
      status: 'neutral' as const,
      icon: 'lucide:sliders-horizontal',
    },
  ];
});

const benchmarkColumns = [
  { dataIndex: 'unitName', title: '装置' },
  { dataIndex: 'loopCount', title: '回路数', width: 100 },
  { dataIndex: 'avgScore', title: '平均评分', width: 120 },
  { dataIndex: 'avgAutoRate', title: '平均自控率', width: 130 },
  { dataIndex: 'avgDelta', title: 'KPI 改善', width: 120 },
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

async function load() {
  loading.value = true;
  try {
    data.value = await getReportBenefitApi(queryParams());
  } catch {
    data.value = null;
  } finally {
    loading.value = false;
    renderChart();
  }
}

function renderChart() {
  const curve = data.value?.autoRateCurve ?? [];
  if (curve.length === 0) return;
  renderEcharts({
    ...getEchartsBase(),
    tooltip: { ...getTooltipPreset(), trigger: 'axis' },
    legend: { ...getEchartsBase().legend, data: ['有效自控率', '平均评分'] },
    xAxis: {
      ...getEchartsBase().xAxis,
      data: curve.map((p) => p.date),
    },
    yAxis: [
      { ...getEchartsBase().yAxis, name: '自控率(%)', min: 0, max: 100 },
      {
        ...getEchartsBase().yAxis,
        name: '评分',
        min: 0,
        max: 100,
        position: 'right',
      },
    ],
    series: [
      {
        name: '有效自控率',
        data: curve.map((p) => p.autoRate),
        ...getLineSeriesPreset(getSeriesColor('ok')),
      },
      {
        name: '平均评分',
        data: curve.map((p) => p.score),
        yAxisIndex: 1,
        ...getLineSeriesPreset(getSeriesColor('info')),
        lineStyle: { width: 1.5, type: 'dashed' as const },
      },
    ],
  });
}

function fmtNum(v: null | number | undefined, digits = 1): string {
  return v == null ? '—' : v.toFixed(digits);
}

function deltaClass(d: null | number | undefined): string {
  if (d == null) return '';
  return d >= 0 ? 'text-emerald-600' : 'text-rose-600';
}

// ===== 导出（P0-8：CSV/Excel 双格式，对齐绩效报告交互） =====
function handleExport(format: 'csv' | 'excel' = 'csv') {
  const rows0 = data.value?.benchmark ?? [];
  if (rows0.length === 0) {
    message.warning('当前无数据可导出');
    return;
  }
  exportData({
    filename: `benefit_benchmark_${dayjs().format('YYYYMMDD')}`,
    format,
    sheetName: '装置标杆',
    headers: ['装置', '回路数', '平均评分', '平均自控率(%)', 'KPI 改善'],
    rows: rows0.map((b) => [
      b.unitName,
      b.loopCount,
      b.avgScore == null ? '' : b.avgScore.toFixed(1),
      b.avgAutoRate == null ? '' : b.avgAutoRate.toFixed(1),
      b.avgDelta == null
        ? ''
        : `${b.avgDelta > 0 ? '+' : ''}${b.avgDelta.toFixed(1)}`,
    ]),
  });
}

function handleHelp() {
  showPageHelp({
    title: '收益报告 帮助',
    content: `
      <p><b>定位</b>：量化整定与处置的技术收益——KPI 前后对比、自控率提升趋势、装置标杆。</p>
      <p><b>口径</b>：前后对比取已闭环处置工单固化的 kpi_before/kpi_after；自控率曲线取月度 KPI 快照均值。</p>
      <p><b>范围</b>：本期仅技术指标，经济收益作为可选配置项后续提供。</p>
      <p><b>模块停用</b>：处置/整定模块停用时本页照常可用，展示历史数据归档，查询与导出不受影响。</p>
    `,
  });
}

watch([dateRange, plantNodeId], () => load());

onMounted(() => {
  loadPlants();
  load();
});
</script>

<template>
  <Page>
    <ClpmPageToolbar
      :loading="loading"
      subtitle="整定前后 KPI 对比 · 自控率提升 · 装置标杆（技术指标）"
      title="收益报告"
    >
      <template #actions>
        <ClpmToolbarButton
          icon="ant-design:question-circle-outlined"
          label="帮助"
          @click="handleHelp"
        />
        <Dropdown>
          <ClpmToolbarButton
            icon="ant-design:download-outlined"
            label="导出"
            tooltip="导出装置标杆数据"
          />
          <template #overlay>
            <Menu @click="(e: any) => handleExport(e.key as 'csv' | 'excel')">
              <Menu.Item key="csv">导出 CSV</Menu.Item>
              <Menu.Item key="excel">导出 Excel</Menu.Item>
            </Menu>
          </template>
        </Dropdown>
        <ClpmToolbarButton
          icon="ant-design:sync-outlined"
          label="刷新"
          @click="load"
        />
      </template>
    </ClpmPageToolbar>

    <!-- P0-5：处置/整定模块停用时灰色归档横幅（历史数据可查询导出） -->
    <ClpmModuleArchivedBanner :modules="['handling', 'tuning']" />

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

    <!-- 汇总卡 -->
    <div class="mb-3 mt-2 grid grid-cols-4 gap-3">
      <ClpmKpiCard
        v-for="c in summaryCards"
        :key="c.key"
        :icon="c.icon"
        :status="c.status"
        :title="c.title"
        :unit="c.unit"
        :value="c.value"
      />
    </div>

    <!-- 整定前后 KPI 对比 -->
    <ClpmDataCanvas
      class="mb-3"
      title="整定前后 KPI 对比"
      :loading="loading"
      :empty="!data?.kpiComparison?.length"
      empty-text="暂无已闭环处置记录"
    >
      <Table
        :columns="[
          { dataIndex: 'label', title: '指标' },
          { dataIndex: 'before', title: '处置前', width: 120, align: 'right' },
          { dataIndex: 'after', title: '处置后', width: 120, align: 'right' },
          { dataIndex: 'delta', title: '变化', width: 120, align: 'right' },
        ]"
        :data-source="data?.kpiComparison ?? []"
        :pagination="false"
        row-key="metric"
        size="small"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.dataIndex === 'before'">
            {{ fmtNum(record.before) }} {{ record.unit ?? '' }}
          </template>
          <template v-else-if="column.dataIndex === 'after'">
            {{ fmtNum(record.after) }} {{ record.unit ?? '' }}
          </template>
          <template v-else-if="column.dataIndex === 'delta'">
            <span :class="deltaClass(record.delta)">
              {{ record.delta == null ? '—' : `${record.delta > 0 ? '+' : ''}${record.delta.toFixed(1)} ${record.unit ?? ''}` }}
            </span>
          </template>
        </template>
      </Table>
    </ClpmDataCanvas>

    <div class="grid grid-cols-2 gap-3">
      <!-- 自控率提升曲线 -->
      <ClpmDataCanvas
        title="自控率 / 评分趋势（按月）"
        :loading="loading"
        :empty="!data?.autoRateCurve?.length"
        empty-text="暂无趋势数据"
      >
        <EchartsUI ref="chartRef" height="300px" />
      </ClpmDataCanvas>

      <!-- 装置标杆表 -->
      <ClpmDataCanvas
        title="装置标杆"
        :empty="!data?.benchmark?.length"
        empty-text="暂无装置数据"
      >
        <Table
          :columns="benchmarkColumns"
          :data-source="data?.benchmark ?? []"
          :pagination="false"
          row-key="unitId"
          size="small"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.dataIndex === 'avgScore'">
              {{ fmtNum(record.avgScore) }}
            </template>
            <template v-else-if="column.dataIndex === 'avgAutoRate'">
              {{ fmtNum(record.avgAutoRate) }}%
            </template>
            <template v-else-if="column.dataIndex === 'avgDelta'">
              <span :class="deltaClass(record.avgDelta)">
                <Tag v-if="record.avgDelta != null" :color="record.avgDelta >= 0 ? 'green' : 'red'">
                  {{ record.avgDelta > 0 ? '+' : '' }}{{ fmtNum(record.avgDelta) }}
                </Tag>
                <span v-else class="text-neutral-400">—</span>
              </span>
            </template>
          </template>
        </Table>
      </ClpmDataCanvas>
    </div>
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
</style>
