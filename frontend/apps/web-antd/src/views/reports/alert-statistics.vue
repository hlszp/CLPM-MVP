<script setup lang="ts">
/**
 * 预警统计报告页（/reports/alert-statistics，报告模块优化 P1-4，2026-08-28）
 *
 * 定位（方案 §4.2）：预警治理效果度量——"预警有没有用？误报多不多？确认
 * 快不快？"。监控是基础模块，此报告在任何模块组合下完整。
 *
 * 区块：KPI 卡（预警总数/活跃数/MTTA/MTTR/误报率/活跃抑制）+ 按天 severity
 * 堆叠柱 + 严重度饼图 + 状态分布 + TOP10 规则/回路 + 导出。
 * 视觉约束：堆叠柱无动画；饼图无引线、仅悬浮框（用户偏好）。
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
  Select,
  Table,
  TreeSelect,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import { getPlantNodeTreeApi } from '#/api/plant-node';
import { getReportAlertStatisticsApi } from '#/api/reports';
import {
  ClpmDataCanvas,
  ClpmKpiCard,
  ClpmPageToolbar,
  ClpmToolbarButton,
} from '#/components/clpm';
import { showPageHelp } from '#/composables/use-page-toolbar';
import { exportData } from '#/utils/export';

defineOptions({ name: 'ReportsAlertStatistics' });

const loading = ref(false);
const data = ref<null | ReportsApi.AlertStatisticsData>(null);

// 统一筛选条（时间 + 装置 + 严重度 + 状态，透传 /reports/alert-statistics）
const dateRange = ref<[Dayjs, Dayjs]>([dayjs().subtract(30, 'day'), dayjs()]);
const plantNodeId = ref<string | undefined>();
const severity = ref<string | undefined>();
const status = ref<string | undefined>();
const plantTree = ref<any[]>([]);

const SEVERITY_OPTIONS = [
  { label: 'INFO', value: 'INFO' },
  { label: 'WARN', value: 'WARN' },
  { label: 'ERROR', value: 'ERROR' },
  { label: 'CRITICAL', value: 'CRITICAL' },
];

const STATUS_OPTIONS = [
  { label: '活跃', value: 'ACTIVE' },
  { label: '已确认', value: 'ACKNOWLEDGED' },
  { label: '已解决', value: 'RESOLVED' },
  { label: '已抑制', value: 'SUPPRESSED' },
  { label: '已归档', value: 'ARCHIVED' },
];

const SEVERITY_LABELS: Record<string, string> = {
  CRITICAL: '严重',
  ERROR: '错误',
  INFO: '提示',
  WARN: '警告',
};

function queryParams(): ReportsApi.ReportQuery & {
  severity?: string;
  status?: string;
} {
  const [start, end] = dateRange.value ?? [];
  return {
    startDate: start?.format('YYYY-MM-DD'),
    endDate: end?.format('YYYY-MM-DD'),
    plantNodeId: plantNodeId.value,
    severity: severity.value,
    status: status.value,
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
    data.value = await getReportAlertStatisticsApi(queryParams());
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

function fmtHours(v: null | number | undefined): string {
  return typeof v === 'number'
    ? (v >= 24
      ? `${(v / 24).toFixed(1)} 天`
      : `${v.toFixed(1)} h`)
    : '—';
}

const summaryCards = computed(() => [
  {
    key: 'total',
    title: '预警总数',
    value: String(data.value?.summary.total ?? '—'),
    status: 'info' as const,
    icon: 'lucide:bell',
  },
  {
    key: 'active',
    title: '活跃预警',
    value: String(data.value?.summary.active ?? '—'),
    status: 'warning' as const,
    icon: 'lucide:bell-ring',
  },
  {
    key: 'mttaHours',
    title: 'MTTA（确认）',
    value: fmtHours(data.value?.summary.mttaHours),
    status: 'neutral' as const,
    icon: 'lucide:timer',
  },
  {
    key: 'mttrHours',
    title: 'MTTR（解决）',
    value: fmtHours(data.value?.summary.mttrHours),
    status: 'neutral' as const,
    icon: 'lucide:wrench',
  },
  {
    key: 'falsePositiveRate',
    title: '误报率',
    value: fmtPct(data.value?.summary.falsePositiveRate),
    status: 'warning' as const,
    icon: 'lucide:thumbs-down',
  },
  {
    key: 'activeSuppressions',
    title: '活跃抑制',
    value: String(data.value?.summary.activeSuppressions ?? '—'),
    status: 'neutral' as const,
    icon: 'lucide:bell-off',
  },
]);

// ===== 按天 severity 堆叠柱（无动画，用户偏好） =====
const trendRef = ref<EchartsUIType>();
const { renderEcharts: renderTrend } = useEcharts(trendRef);

watch(
  () => data.value?.trend,
  async (trend) => {
    if (!trend?.length) return;
    await nextTick();
    renderTrend({
      animation: false,
      color: ['#6b7280', '#d97706', '#dc2626', '#7f1d1d'],
      grid: { bottom: 40, left: 48, right: 16, top: 32 },
      legend: { data: ['INFO', 'WARN', 'ERROR', 'CRITICAL'], top: 0 },
      series: ['INFO', 'WARN', 'ERROR', 'CRITICAL'].map((s) => ({
        data: trend.map((p) => [p.date, p[s as keyof ReportsApi.AlertTrendPoint]]),
        name: s,
        stack: 'total',
        type: 'bar',
      })),
      tooltip: { trigger: 'axis' },
      xAxis: { type: 'category' },
      yAxis: { type: 'value' },
    });
  },
);

// ===== 严重度分布饼图（无引线，仅悬浮框） =====
const pieRef = ref<EchartsUIType>();
const { renderEcharts: renderPie } = useEcharts(pieRef);

watch(
  () => data.value?.severityDistribution,
  async (dist) => {
    if (!dist?.length) return;
    await nextTick();
    renderPie({
      animation: false,
      color: ['#6b7280', '#d97706', '#dc2626', '#7f1d1d'],
      series: [
        {
          data: dist.map((d) => ({
            name: SEVERITY_LABELS[d.key] ?? d.key,
            value: d.count,
          })),
          label: { show: false },
          labelLine: { show: false },
          name: '严重度分布',
          type: 'pie',
        },
      ],
      tooltip: { trigger: 'item' },
    });
  },
);

// ===== TOP10 规则 / 回路 =====
const ruleColumns = [
  { dataIndex: 'ruleCode', title: '规则代码', width: 140 },
  { dataIndex: 'ruleName', title: '规则名称', width: 160 },
  { dataIndex: 'count', title: '触发次数', width: 90 },
  { dataIndex: 'falsePositives', title: '误报数', width: 80 },
];

const loopColumns = [
  { dataIndex: 'loopTagName', title: '回路', width: 160 },
  { dataIndex: 'count', title: '触发次数', width: 90 },
  { dataIndex: 'falsePositives', title: '误报数', width: 80 },
];

// ===== 导出（TOP 规则/回路聚合 CSV/Excel） =====
function handleExport(format: 'csv' | 'excel' = 'csv') {
  const rules = data.value?.topRules ?? [];
  const loops = data.value?.topLoops ?? [];
  if (rules.length === 0 && loops.length === 0) {
    message.warning('当前无数据可导出');
    return;
  }
  exportData({
    filename: `alert_statistics_${dayjs().format('YYYYMMDD')}`,
    format,
    sheetName: '预警统计',
    headers: ['类别', '名称', '触发次数', '误报数'],
    rows: [
      ...rules.map((r) => [
        '规则',
        r.ruleName ? `${r.ruleCode}（${r.ruleName}）` : r.ruleCode,
        r.count,
        r.falsePositives,
      ]),
      ...loops.map((l) => ['回路', l.loopTagName, l.count, l.falsePositives]),
    ],
  });
}

function handleHelp() {
  showPageHelp({
    title: '预警统计报告 帮助',
    content: `
      <p><b>定位</b>：预警治理效果度量——预警有没有用、误报多不多、确认快不快。</p>
      <p><b>口径</b>：MTTA=已确认事件从触发到确认的平均时长；MTTR=已解决事件从触发到解决的平均时长；误报率=标记误报/已标记集（未标记不参与）。</p>
      <p><b>筛选</b>：时间/装置/严重度/状态影响全部区块（默认近 30 天，按触发时间归窗）；活跃抑制为当前全量口径。</p>
      <p><b>模块停用</b>：监控为基础模块，本页在任何模块组合下均完整可用。</p>
    `,
  });
}

watch([dateRange, plantNodeId, severity, status], () => load());

onMounted(() => {
  loadPlants();
  load();
});
</script>

<template>
  <Page>
    <ClpmPageToolbar
      :loading="loading"
      subtitle="预警治理效果度量 · MTTA / MTTR / 误报率 / TOP 榜"
      title="预警统计报告"
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
            tooltip="导出 TOP 规则/回路数据"
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

    <!-- 统一筛选条（时间 + 装置 + 严重度 + 状态） -->
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
        style="width: 220px"
        tree-default-expand-all
      />
      <span class="reports-filter-bar__label">严重度</span>
      <Select
        v-model:value="severity"
        :options="SEVERITY_OPTIONS"
        allow-clear
        placeholder="全部"
        style="width: 120px"
      />
      <span class="reports-filter-bar__label">状态</span>
      <Select
        v-model:value="status"
        :options="STATUS_OPTIONS"
        allow-clear
        placeholder="全部"
        style="width: 120px"
      />
    </div>

    <!-- KPI 卡（6 卡） -->
    <div class="mb-3 mt-2 grid grid-cols-2 gap-3 md:grid-cols-6">
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
        title="按天预警量（严重度堆叠）"
        :empty="!data?.trend?.length"
        empty-text="暂无预警数据"
      >
        <EchartsUI ref="trendRef" height="220px" />
      </ClpmDataCanvas>

      <ClpmDataCanvas
        title="严重度分布"
        :empty="!data?.severityDistribution?.length"
        empty-text="暂无分布数据"
      >
        <EchartsUI ref="pieRef" height="220px" />
      </ClpmDataCanvas>
    </div>

    <div class="grid grid-cols-2 gap-3">
      <ClpmDataCanvas
        title="TOP10 规则"
        :empty="!data?.topRules?.length"
        empty-text="暂无规则数据"
      >
        <Table
          :columns="ruleColumns"
          :data-source="data?.topRules ?? []"
          :pagination="false"
          row-key="ruleCode"
          size="small"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.dataIndex === 'ruleName'">
              {{ record.ruleName ?? '—' }}
            </template>
          </template>
        </Table>
      </ClpmDataCanvas>

      <ClpmDataCanvas
        title="TOP10 回路"
        :empty="!data?.topLoops?.length"
        empty-text="暂无回路数据"
      >
        <Table
          :columns="loopColumns"
          :data-source="data?.topLoops ?? []"
          :pagination="false"
          row-key="loopId"
          size="small"
        />
      </ClpmDataCanvas>
    </div>
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
