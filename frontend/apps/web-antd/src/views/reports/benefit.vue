<script lang="ts" setup>
/**
 * 统计报告-收益报告（/reports/benefit，IA 优化 P0 新建）
 *
 * 设计文档：docs/设计文档/IA 优化/CLPM-IA优化实施方案-0822.md §2.2
 * - 整定前后 KPI 对比（综合评分/有效自控率/PV 好值率/振荡率）
 * - 自控率提升曲线（按月）
 * - 装置标杆表（均分/自控率/改善幅度）
 * P2 闭环增强（报告模块优化方案 §5.2/§5.3）：
 * - 整定执行区块（算法/状态分布 + 回滚率 + 平均拟合度 + 拟合度四桶 + 批次前后散点）
 * - 逐工单前后对比明细（逐单举证"这一单到底有没有效"）
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
  getReportBenefitOrdersApi,
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

const {
  getBarSeriesPreset,
  getEchartsBase,
  getLineSeriesPreset,
  getSeriesColor,
  getTooltipPreset,
} = useEchartsPreset();
const chartRef = ref<EchartsUIType>();
const { renderEcharts } = useEcharts(chartRef);
// P2-3 整定执行区块图表
const fittingChartRef = ref<EchartsUIType>();
const { renderEcharts: renderFitting } = useEcharts(fittingChartRef);
const algoChartRef = ref<EchartsUIType>();
const { renderEcharts: renderAlgo } = useEcharts(algoChartRef);
const scatterChartRef = ref<EchartsUIType>();
const { renderEcharts: renderScatter } = useEcharts(scatterChartRef);

function fmtPctRaw(v: null | number | undefined): string {
  return typeof v === 'number' ? `${(v * 100).toFixed(1)}%` : '—';
}

interface SummaryCard {
  key: string;
  title: string;
  value: number | string;
  unit: string;
  status: 'info' | 'neutral' | 'ok' | 'warning';
  icon: string;
  infoTip?: string;
}
const summaryCards = computed<SummaryCard[]>(() => {
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
    // P2-3（方案 §5.2）：回滚率 + 平均拟合度（整定执行口径，窗口内记录）
    {
      key: 'rollbackRate',
      title: '整定回滚率',
      value: fmtPctRaw(d?.tuningExecution?.rollbackRate),
      unit: '',
      status: 'warning' as const,
      icon: 'lucide:undo-2',
      infoTip: '回滚率=ROLLED_BACK / 窗口内全部整定记录（created_at 归窗）',
    },
    {
      key: 'avgFittingScore',
      title: '平均拟合度',
      value: fmtNum(d?.tuningExecution?.avgFittingScore),
      unit: '',
      status: 'ok' as const,
      icon: 'lucide:gauge',
      infoTip: '窗口内整定记录 fitting_score 均值（0~100）',
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
    renderTuningCharts();
  }
  await loadOrders();
}

// ===== P2-4：逐工单前后对比明细（服务端分页，verified_at 归窗） =====
const orders = ref<ReportsApi.BenefitOrderItem[]>([]);
const ordersTotal = ref(0);
const ordersPage = ref(1);
const ordersPageSize = ref(10);
const ordersLoading = ref(false);

async function loadOrders() {
  ordersLoading.value = true;
  try {
    const resp = await getReportBenefitOrdersApi({
      ...queryParams(),
      page: ordersPage.value,
      pageSize: ordersPageSize.value,
    });
    orders.value = resp.items ?? [];
    ordersTotal.value = resp.total ?? 0;
  } catch {
    orders.value = [];
    ordersTotal.value = 0;
  } finally {
    ordersLoading.value = false;
  }
}

function handleOrdersPageChange(page: number) {
  ordersPage.value = page;
  loadOrders();
}

const ordersPagination = computed(() => ({
  current: ordersPage.value,
  pageSize: ordersPageSize.value,
  total: ordersTotal.value,
  showSizeChanger: false,
  size: 'small' as const,
  onChange: handleOrdersPageChange,
}));

const orderColumns = [
  { dataIndex: 'orderNo', title: '工单号', width: 150 },
  { dataIndex: 'loopTagName', title: '回路', width: 130 },
  { dataIndex: 'actionTypeLabel', title: '类型', width: 90 },
  { dataIndex: 'score', title: '综合评分', width: 150 },
  { dataIndex: 'effectiveAutoRate', title: '有效自控率', width: 150 },
  { dataIndex: 'goodValueRate', title: 'PV 好值率', width: 150 },
  { dataIndex: 'oscillationRate', title: '振荡率', width: 150 },
  { dataIndex: 'verifyResult', title: '验证结论', width: 90 },
  { dataIndex: 'verifiedAt', title: '验证时间', width: 150 },
];

/** 四指标前后值与差值；reverse=true 为反向指标（振荡率，越低越好） */
function metricPair(
  record: ReportsApi.BenefitOrderItem,
  key: string,
  reverse = false,
): null | { after: number; before: number; delta: number } {
  const b = Number(record.kpiBefore?.[key]);
  const a = Number(record.kpiAfter?.[key]);
  if (Number.isNaN(b) || Number.isNaN(a)) return null;
  const raw = a - b;
  return { before: b, after: a, delta: reverse ? -raw : raw };
}

/** 明细行视图模型：预计算四指标前后对（模板免重复调用 + 类型安全） */
const orderRows = computed(() =>
  orders.value.map((o) => ({
    ...o,
    m: {
      score: metricPair(o, 'score'),
      effectiveAutoRate: metricPair(o, 'effectiveAutoRate'),
      goodValueRate: metricPair(o, 'goodValueRate'),
      oscillationRate: metricPair(o, 'oscillationRate', true),
    } as Record<
      string,
      null | { after: number; before: number; delta: number }
    >,
  })),
);

/** 模板取数：按列 dataIndex 取预计算的指标对（列 key 即指标 key） */
function metricOf(record: any, key: unknown) {
  return (record?.m?.[String(key)] ?? null) as null | {
    after: number;
    before: number;
    delta: number;
  };
}

// ===== P2-3：整定执行区块（状态分布 chips + 拟合度/算法柱图 + 批次散点） =====
const TUNING_STATUS_LABELS: Record<string, string> = {
  DRAFT: '草稿',
  RUNNING: '进行中',
  IDENTIFIED: '已辨识',
  SIMULATED: '已仿真',
  COMPLETED: '已完成',
  INCONCLUSIVE: '无法判定',
  ROLLED_BACK: '已回滚',
  PENDING: '待处理',
  APPLIED: '已实施',
  VERIFIED: '已验证',
};

const tuningExec = computed(() => data.value?.tuningExecution ?? null);
const fittingEmpty = computed(
  () => (data.value?.fittingDistribution ?? []).every((b) => b.count === 0),
);
const batchScatter = computed(() => data.value?.latestBatchScatter ?? null);

function renderTuningCharts() {
  // 拟合度四桶分布
  const dist = data.value?.fittingDistribution ?? [];
  if (dist.length > 0 && !fittingEmpty.value) {
    renderFitting({
      ...getEchartsBase(),
      tooltip: { ...getTooltipPreset(), trigger: 'axis' },
      xAxis: {
        ...getEchartsBase().xAxis,
        boundaryGap: true,
        data: dist.map((b) => b.label),
      },
      series: [
        {
          name: '记录数',
          data: dist.map((b) => b.count),
          ...getBarSeriesPreset(getSeriesColor('ok')),
        },
      ],
    });
  }
  // 算法分布
  const algos = tuningExec.value?.byAlgorithm ?? [];
  if (algos.length > 0) {
    renderAlgo({
      ...getEchartsBase(),
      tooltip: { ...getTooltipPreset(), trigger: 'axis' },
      xAxis: {
        ...getEchartsBase().xAxis,
        boundaryGap: true,
        data: algos.map((a) => a.algorithm),
      },
      series: [
        {
          name: '记录数',
          data: algos.map((a) => a.count),
          ...getBarSeriesPreset(getSeriesColor('neutral')),
        },
      ],
    });
  }
  // 批次前后散点（按 loopId 配对：x=整改前评分，y=整改后评分，对角线为无变化参考）
  const bs = batchScatter.value;
  if (bs) {
    const afterMap = new Map(bs.after.map((p) => [p.loopId, p]));
    const points: Array<[number, number, string]> = [];
    for (const p of bs.before) {
      const q = afterMap.get(p.loopId);
      if (p.score == null || q?.score == null) continue;
      points.push([p.score, q.score, p.loopTagName ?? p.loopId]);
    }
    if (points.length > 0) {
      const xs = points.map((p) => p[0]);
      const ys = points.map((p) => p[1]);
      const lo = Math.floor(Math.min(...xs, ...ys, 0));
      const hi = Math.ceil(Math.max(...xs, ...ys, 100));
      renderScatter({
        ...getEchartsBase(),
        tooltip: {
          ...getTooltipPreset(),
          trigger: 'item',
          formatter: (param: any) =>
            `${param.data[2]}<br/>整改前：${param.data[0]} 分<br/>整改后：${param.data[1]} 分`,
        },
        xAxis: {
          ...getEchartsBase().yAxis,
          type: 'value' as const,
          name: '整改前',
          min: lo,
          max: hi,
        },
        yAxis: {
          ...getEchartsBase().yAxis,
          type: 'value' as const,
          name: '整改后',
          min: lo,
          max: hi,
        },
        series: [
          {
            name: '回路',
            type: 'scatter' as const,
            symbolSize: 8,
            data: points,
            itemStyle: { color: getSeriesColor('ok') },
            markLine: {
              silent: true,
              symbol: 'none',
              label: { show: false },
              lineStyle: {
                type: 'dashed' as const,
                color: getSeriesColor('neutral'),
                width: 1,
              },
              data: [
                [
                  { coord: [lo, lo] },
                  { coord: [hi, hi] },
                ],
              ],
            },
          },
        ],
      });
    }
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
      <p><b>整定执行</b>：算法/状态分布、回滚率（ROLLED_BACK/窗口内全部记录）、平均拟合度均按整定记录创建时间归窗；拟合度分布为 &lt;60 / 60~75 / 75~90 / ≥90 四桶；批次散点取最近已完成批次的前后评分快照（全局口径）。</p>
      <p><b>逐单明细</b>：仅已闭环且前后 KPI 快照非空的工单（验证闭环时间归窗）；差值绿色=改善、红色=恶化，振荡率为反向指标（下降为改善）。</p>
      <p><b>范围</b>：本期仅技术指标，经济收益作为可选配置项后续提供。</p>
      <p><b>模块停用</b>：处置/整定模块停用时本页照常可用，展示历史数据归档，查询与导出不受影响。</p>
    `,
  });
}

watch([dateRange, plantNodeId], () => {
  ordersPage.value = 1;
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

    <!-- 汇总卡（P2-3：+ 回滚率 / 平均拟合度） -->
    <div class="mb-3 mt-2 grid grid-cols-4 gap-3">
      <ClpmKpiCard
        v-for="c in summaryCards"
        :key="c.key"
        :icon="c.icon"
        :status="c.status"
        :title="c.title"
        :unit="c.unit"
        :value="c.value"
        :info-tip="c.infoTip"
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

    <!-- P2-4：逐工单前后对比明细（§5.3，KPI 对比表下方；振荡率反向指标） -->
    <ClpmDataCanvas
      class="mb-3"
      title="逐工单前后对比明细"
      description="仅已闭环且前后 KPI 快照非空的工单，按验证闭环时间归窗——逐单举证整改是否有效"
      :loading="ordersLoading"
      loading-variant="opacity"
      :empty="orders.length === 0"
      empty-text="暂无已闭环且含前后快照的工单"
    >
      <Table
        :columns="orderColumns"
        :data-source="orderRows"
        :pagination="ordersPagination"
        row-key="orderNo"
        size="small"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="['score', 'effectiveAutoRate', 'goodValueRate', 'oscillationRate'].includes(column.dataIndex as string)">
            <span v-if="metricOf(record, column.dataIndex)">
              <span class="text-neutral-500">
                {{ fmtNum(metricOf(record, column.dataIndex)?.before) }}
              </span>
              →
              {{ fmtNum(metricOf(record, column.dataIndex)?.after) }}
              <span :class="deltaClass(metricOf(record, column.dataIndex)?.delta)">
                （{{ (metricOf(record, column.dataIndex)?.delta ?? 0) > 0 ? '+' : '' }}{{ fmtNum(metricOf(record, column.dataIndex)?.delta) }}）
              </span>
            </span>
            <span v-else class="text-neutral-400">—</span>
          </template>
          <template v-else-if="column.dataIndex === 'verifyResult'">
            <Tag v-if="record.verifyResult === 'EFFECTIVE'" color="green">有效</Tag>
            <Tag v-else-if="record.verifyResult === 'INEFFECTIVE'" color="red">无效</Tag>
            <span v-else class="text-neutral-400">—</span>
          </template>
          <template v-else-if="column.dataIndex === 'verifiedAt'">
            {{ record.verifiedAt ? dayjs(record.verifiedAt).format('YYYY-MM-DD HH:mm') : '—' }}
          </template>
        </template>
      </Table>
    </ClpmDataCanvas>

    <!-- P2-3：整定执行区块（§5.2，KPI 对比表与趋势图之间） -->
    <div class="mb-3 grid grid-cols-3 gap-3">
      <ClpmDataCanvas
        title="整定执行统计"
        description="窗口内整定记录（按创建时间归窗）"
        :empty="!tuningExec || tuningExec.totalRecords === 0"
        empty-text="窗口内暂无整定记录"
      >
        <div class="flex flex-col gap-2 text-xs">
          <div class="text-neutral-600">
            记录数
            <span class="font-semibold">{{ tuningExec?.totalRecords ?? 0 }}</span>
            · 回滚率
            <span class="font-semibold">{{ fmtPctRaw(tuningExec?.rollbackRate) }}</span>
            · 平均拟合度
            <span class="font-semibold">{{ fmtNum(tuningExec?.avgFittingScore) }}</span>
          </div>
          <div class="flex flex-wrap gap-1">
            <Tag v-for="s in tuningExec?.byStatus ?? []" :key="s.status">
              {{ TUNING_STATUS_LABELS[s.status] ?? s.status }}：{{ s.count }}
            </Tag>
          </div>
        </div>
      </ClpmDataCanvas>

      <ClpmDataCanvas
        title="拟合度分布"
        :empty="fittingEmpty"
        empty-text="暂无拟合度数据"
      >
        <EchartsUI ref="fittingChartRef" height="220px" />
      </ClpmDataCanvas>

      <ClpmDataCanvas
        title="算法分布"
        :empty="!tuningExec?.byAlgorithm?.length"
        empty-text="窗口内暂无整定记录"
      >
        <EchartsUI ref="algoChartRef" height="220px" />
      </ClpmDataCanvas>
    </div>

    <!-- P2-3：批次前后散点对比（有批次数据时展示，全局口径） -->
    <ClpmDataCanvas
      v-if="batchScatter"
      class="mb-3"
      :title="`批次前后散点对比（${batchScatter.batchNo} ${batchScatter.title}）`"
      description="每点一个回路：x=整改前评分，y=整改后评分；对角线以上为改善"
    >
      <EchartsUI ref="scatterChartRef" height="300px" />
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
