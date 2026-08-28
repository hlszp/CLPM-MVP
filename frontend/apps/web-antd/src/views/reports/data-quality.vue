<script setup lang="ts">
/**
 * 数据质量报告页（/reports/data-quality，报告模块优化 P1-2，2026-08-28）
 *
 * 定位（方案 §4.1）：回答 S1 阶段最关键问题——"数据可信吗？哪些回路该补
 * 数据/修 tag？"。只依赖基础模块数据（kpi_snapshot_hourly /
 * loop_integrity_snapshot / loop_confidence_latest），可插拔模块全拔时仍完整。
 *
 * 区块：KPI 卡（参评率/数据健康率/INCONCLUSIVE 率）+ 按天双折线（健康率/
 * INCONCLUSIVE 率）+ 可信度 A~E 分布 + 回路明细表（含未参评原因归因）+ 导出。
 */
import type { Dayjs } from 'dayjs';

import type { EchartsUIType } from '@vben/plugins/echarts';

import type { ReportsApi } from '#/api/reports';

import { computed, nextTick, onMounted, ref, watch } from 'vue';

import { Page } from '@vben/common-ui';
import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import {
  Dropdown,
  Menu,
  message,
  RangePicker,
  Table,
  TreeSelect,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import { getPlantNodeTreeApi } from '#/api/plant-node';
import { getReportDataQualityApi } from '#/api/reports';
import {
  ClpmDataCanvas,
  ClpmKpiCard,
  ClpmPageToolbar,
  ClpmToolbarButton,
} from '#/components/clpm';
import { showPageHelp } from '#/composables/use-page-toolbar';
import { exportData } from '#/utils/export';

defineOptions({ name: 'ReportsDataQuality' });

const loading = ref(false);
const data = ref<null | ReportsApi.DataQualityData>(null);

// 统一筛选条（时间 + 装置，透传 /reports/data-quality）
const dateRange = ref<[Dayjs, Dayjs]>([dayjs().subtract(30, 'day'), dayjs()]);
const plantNodeId = ref<string | undefined>();
const plantTree = ref<any[]>([]);

function queryParams(): ReportsApi.ReportQuery {
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
    data.value = await getReportDataQualityApi(queryParams());
  } catch {
    data.value = null;
  } finally {
    loading.value = false;
  }
}

// ===== KPI 卡（null → '—'） =====
function fmtPct(v: null | number | undefined): string {
  return typeof v === 'number' ? `${v.toFixed(1)}%` : '—';
}

const summaryCards = computed(() => [
  {
    key: 'evaluateRate',
    title: '参评率',
    value: fmtPct(data.value?.summary.evaluateRate),
    status: 'info' as const,
    icon: 'lucide:check-circle-2',
  },
  {
    key: 'dataHealthRate',
    title: '数据健康率',
    value: fmtPct(data.value?.summary.dataHealthRate),
    status: 'ok' as const,
    icon: 'lucide:heart-pulse',
  },
  {
    key: 'inconclusiveRate',
    title: 'INCONCLUSIVE 率',
    value: fmtPct(data.value?.summary.inconclusiveRate),
    status: 'warning' as const,
    icon: 'lucide:help-circle',
  },
  {
    key: 'totalLoops',
    title: '回路总数',
    value: String(data.value?.summary.totalLoops ?? '—'),
    status: 'neutral' as const,
    icon: 'lucide:database',
  },
]);

// ===== 按天双折线（数据健康率 / INCONCLUSIVE 率） =====
const trendRef = ref<EchartsUIType>();
const { renderEcharts: renderTrend } = useEcharts(trendRef);

watch(
  () => data.value?.trend,
  async (trend) => {
    if (!trend?.length) return;
    await nextTick();
    renderTrend({
      animation: false,
      color: ['#1d4ed8', '#b45309'],
      grid: { bottom: 40, left: 48, right: 16, top: 32 },
      legend: { data: ['数据健康率', 'INCONCLUSIVE 率'], top: 0 },
      series: [
        {
          data: trend.map((p) => [p.date, p.healthRate]),
          name: '数据健康率',
          showSymbol: false,
          type: 'line',
          yAxisIndex: 0,
        },
        {
          data: trend.map((p) => [p.date, p.inconclusiveRate]),
          name: 'INCONCLUSIVE 率',
          showSymbol: false,
          type: 'line',
          yAxisIndex: 1,
        },
      ],
      tooltip: {
        trigger: 'axis',
        valueFormatter: (v: any) => (typeof v === 'number' ? `${v}%` : '—'),
      },
      xAxis: { type: 'category' },
      yAxis: [
        { max: 100, min: 0, type: 'value' },
        { max: 100, min: 0, type: 'value' },
      ],
    });
  },
);

// ===== 可信度分布（A~E + 未评估） =====
const CONF_LABELS: Record<string, string> = {
  A: 'A（高可信）',
  B: 'B',
  C: 'C',
  D: 'D',
  E: 'E（低可信）',
  UNKNOWN: '未评估',
};

// ===== 明细表 =====
const itemColumns = [
  { dataIndex: 'loopTagName', title: '回路', width: 140 },
  { dataIndex: 'unitPath', title: '装置.单元', width: 150 },
  { key: 'completeness', title: '数据完整性', width: 150 },
  { dataIndex: 'goodValueRate', title: 'PV 好值率', width: 100 },
  { dataIndex: 'confidenceLevel', title: '可信度', width: 80 },
  { dataIndex: 'fitnessLevel', title: '适用性', width: 80 },
  { dataIndex: 'nonEvalReason', title: '未参评原因', width: 130 },
];

function fmtCompleteness(record: ReportsApi.DataQualityItem): string {
  if (record.pvCompleteness == null && record.overallCompleteness == null) {
    return '—';
  }
  return `${record.pvCompleteness?.toFixed(1) ?? '—'}% / ${
    record.overallCompleteness?.toFixed(1) ?? '—'
  }%`;
}

// ===== 导出（明细表 CSV/Excel，对齐报告模块交互） =====
function handleExport(format: 'csv' | 'excel' = 'csv') {
  const items = data.value?.items ?? [];
  if (items.length === 0) {
    message.warning('当前无数据可导出');
    return;
  }
  exportData({
    filename: `data_quality_${dayjs().format('YYYYMMDD')}`,
    format,
    sheetName: '数据质量明细',
    headers: [
      '回路',
      '装置.单元',
      'PV 完整度',
      '整体完整度',
      '巡检状态',
      'PV 好值率',
      '可信度',
      '适用性',
      '未参评原因',
    ],
    rows: items.map((i) => [
      i.loopTagName,
      i.unitPath,
      i.pvCompleteness == null ? '' : `${i.pvCompleteness.toFixed(1)}%`,
      i.overallCompleteness == null ? '' : `${i.overallCompleteness.toFixed(1)}%`,
      i.integrityStatus ?? '',
      i.goodValueRate == null ? '' : `${i.goodValueRate.toFixed(1)}%`,
      i.confidenceLevel ?? '',
      i.fitnessLevel ?? '',
      i.nonEvalReason ?? '',
    ]),
  });
}

function handleHelp() {
  showPageHelp({
    title: '数据质量报告 帮助',
    content: `
      <p><b>定位</b>：数据可信度回顾——数据健康吗、哪些回路该补数据/修 tag。可信数据是平台技术护城河的基础。</p>
      <p><b>口径</b>：参评率=纳入评估回路/回路总数；数据健康率=窗口内各回路 PV 好值率的均值；INCONCLUSIVE 率=评估不确结论快照占比。</p>
      <p><b>未参评原因</b>：按优先级归因——未纳入参评 → L0 数据不足 → 评估 INCONCLUSIVE。</p>
      <p><b>筛选</b>：时间范围影响 KPI 与趋势（默认近 30 天）；明细表的完整度/可信度/适用性为各回路最新状态。</p>
      <p><b>模块停用</b>：本页只依赖基础模块数据，任何模块组合下均完整可用。</p>
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
      subtitle="数据可信度回顾 · 参评率 / 数据健康率 / 未参评归因"
      title="数据质量报告"
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
            tooltip="导出回路明细数据"
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
          @click="load()"
        />
      </template>
    </ClpmPageToolbar>

    <!-- 统一筛选条（时间 + 装置） -->
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

    <!-- KPI 卡 -->
    <div class="mb-3 mt-2 grid grid-cols-2 gap-3 md:grid-cols-4">
      <ClpmKpiCard
        v-for="c in summaryCards"
        :key="c.key"
        :icon="c.icon"
        :status="c.status"
        :title="c.title"
        :value="c.value"
      />
    </div>

    <div class="mb-3 grid grid-cols-2 gap-3">
      <ClpmDataCanvas
        title="按天趋势（数据健康率 / INCONCLUSIVE 率）"
        :empty="!data?.trend?.length"
        empty-text="暂无趋势数据"
      >
        <EchartsUI ref="trendRef" height="220px" />
      </ClpmDataCanvas>

      <ClpmDataCanvas
        title="可信度分布（最新一次评估）"
        :empty="!data?.summary?.confidenceDistribution?.length"
        empty-text="暂无评估数据"
      >
        <div class="flex flex-wrap gap-2 text-xs">
          <span
            v-for="c in data?.summary?.confidenceDistribution ?? []"
            :key="c.level"
            class="text-neutral-600"
          >
            {{ CONF_LABELS[c.level] ?? c.level }}：{{ c.count }}
          </span>
        </div>
      </ClpmDataCanvas>
    </div>

    <ClpmDataCanvas
      title="回路明细"
      :empty="!data?.items?.length"
      empty-text="暂无回路数据"
    >
      <Table
        :columns="itemColumns"
        :data-source="data?.items ?? []"
        :loading="loading"
        :pagination="{ pageSize: 10, showSizeChanger: false }"
        row-key="loopId"
        size="small"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'completeness'">
            {{ fmtCompleteness(record as ReportsApi.DataQualityItem) }}
          </template>
          <template v-else-if="column.dataIndex === 'nonEvalReason'">
            <span
              :class="
                record.nonEvalReason
                  ? 'text-amber-600'
                  : 'text-emerald-600'
              "
            >
              {{ record.nonEvalReason ?? '正常参评' }}
            </span>
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
</style>
