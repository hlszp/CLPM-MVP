<script setup lang="ts">
/**
 * 数据质量报告页（/reports/data-quality，报告模块优化 P1-2，2026-08-28）
 *
 * 定位（方案 §4.1）：回答 S1 阶段最关键问题——"数据可信吗？哪些回路该补
 * 数据/修 tag？"。只依赖基础模块数据（kpi_snapshot_hourly /
 * loop_confidence_latest），可插拔模块全拔时仍完整。
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
  InputNumber,
  Menu,
  message,
  Pagination,
  RangePicker,
  Select,
  Switch,
  Table,
  Tag,
  TreeSelect,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import { getPlantNodeTreeApi } from '#/api/plant-node';
import {
  getReportDataQualityApi,
  getReportDataQualityAuditApi,
} from '#/api/reports';
import {
  ClpmDataCanvas,
  ClpmKpiCard,
  ClpmLoadErrorAlert,
  ClpmPageToolbar,
  ClpmToolbarButton,
} from '#/components/clpm';
import { showPageHelp } from '#/composables/use-page-toolbar';
import {
  DQ_AUDIT_ISSUE_COLOR,
  DQ_AUDIT_ISSUE_LABEL,
} from '#/constants/clpm-ui';
import { exportData } from '#/utils/export';

defineOptions({ name: 'ReportsDataQuality' });

const loading = ref(false);
/** 加载失败态（常驻错误提示 + 重试，替代原空 catch 的静默空图表） */
const loadError = ref(false);
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
  } catch (error) {
    data.value = null;
    loadError.value = true;
    console.error('[数据质量报告] 加载失败:', error);
  } finally {
    loading.value = false;
  }
}

// ===== 位号级体检（2026-09-26 新增，点表口径，只读） =====
//
// 与上方回路级区块互补：回路级看"评估口径的健康率"（kpi_snapshot_hourly），
// 位号级看"点表里到底有没有数、质量码好不好"，回答"哪些位号该修"。
const auditLoading = ref(false);
const auditError = ref(false);
const auditData = ref<null | ReportsApi.DataQualityAuditData>(null);
const auditIssueType = ref<string | undefined>();
const auditPage = ref(1);
const auditPageSize = ref(20);

/**
 * 本分区**独立**的时间窗（刻意不跟随页面 RangePicker），默认近 24 小时。
 *
 * 为什么解耦：窗口越长坏值累计越多，30 天窗口下会出现"断流 0 / 坏值 190"这类
 * **语义正常但观感吓人**的数字；运维更关心"近期哪些位号该修"。
 * 因此这里显式标注窗口（见 auditWindowLabel），避免用户误以为与页面其它分区同窗口。
 */
const auditWindowHours = ref(24);
const AUDIT_WINDOW_OPTIONS = [
  { label: '近 24 小时', value: 24 },
  { label: '近 3 天', value: 72 },
  { label: '近 7 天', value: 168 },
  { label: '近 30 天', value: 720 },
];
/** 当前窗口文案（在分区标题旁显式标注） */
const auditWindowLabel = computed(
  () =>
    AUDIT_WINDOW_OPTIONS.find((o) => o.value === auditWindowHours.value)?.label ??
    `近 ${auditWindowHours.value} 小时`,
);
/** 事件密度比阈值：低于此值的位号判为"密度不足"（基线=等长前一窗口） */
const auditMinDensityRatio = ref(0.5);
/** 是否统计"含 HELD 填平"（缺口被保持值填充）的位号 */
const auditIncludeHeld = ref(true);

/** 问题类型选项（标签集中在 constants/clpm-ui.ts） */
const AUDIT_ISSUE_OPTIONS = [
  { label: '全部问题', value: undefined },
  { label: DQ_AUDIT_ISSUE_LABEL.no_data, value: 'no_data' },
  { label: DQ_AUDIT_ISSUE_LABEL.bad_quality, value: 'bad_quality' },
  { label: DQ_AUDIT_ISSUE_LABEL.low_density, value: 'low_density' },
  { label: DQ_AUDIT_ISSUE_LABEL.held, value: 'held' },
];

async function loadAudit() {
  auditLoading.value = true;
  try {
    auditData.value = await getReportDataQualityAuditApi({
      // 用 lastHours（而非 startDate/endDate）：与后端参数语义一致，
      // 且**不受页面 RangePicker 影响**——本分区刻意使用独立时间窗（见上方注释）
      lastHours: auditWindowHours.value,
      minDensityRatio: auditMinDensityRatio.value,
      includeHeld: auditIncludeHeld.value,
      issueType: auditIssueType.value,
      page: auditPage.value,
      pageSize: auditPageSize.value,
    });
    auditError.value = false;
  } catch (error) {
    auditData.value = null;
    auditError.value = true;
    console.error('[数据质量报告] 位号级体检加载失败:', error);
  } finally {
    auditLoading.value = false;
  }
}

function handleAuditFilterChange() {
  // InputNumber 允许被清空：清空视为"回到默认阈值"，避免空值语义含混
  // （axios 会丢弃 null 参数 → 后端按默认 0.5 处理，与控件显示不符）
  if (auditMinDensityRatio.value === null || auditMinDensityRatio.value === undefined) {
    auditMinDensityRatio.value = 0.5;
  }
  auditPage.value = 1;
  loadAudit();
}

function handleAuditPageChange(page: number, pageSize: number) {
  auditPage.value = page;
  auditPageSize.value = pageSize;
  loadAudit();
}

/** 位号问题标签（含 held：heldTooLong > 0） */
function issueTags(record: Partial<ReportsApi.DataQualityAuditItem>): string[] {
  const list = [...(record.issues ?? [])];
  if ((record.heldTooLong ?? 0) > 0) list.push('held');
  return list;
}

function fmtRatio(v: null | number | undefined): string {
  return typeof v === 'number' ? v.toFixed(2) : '—';
}

const auditColumns = [
  { dataIndex: 'tagName', title: '位号', width: 220 },
  { dataIndex: 'loopName', title: '回路', width: 160 },
  { dataIndex: 'role', title: '角色', width: 90 },
  { dataIndex: 'rows', title: '行数', width: 90, align: 'right' as const },
  { dataIndex: 'badRows', title: '坏值数', width: 90, align: 'right' as const },
  { dataIndex: 'densityRatio', title: '密度比', width: 90, align: 'right' as const },
  { dataIndex: 'heldTooLong', title: 'HELD 槽', width: 100, align: 'right' as const },
  { dataIndex: 'issues', title: '问题类型', width: 220 },
];

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
  { dataIndex: 'goodValueRate', title: 'PV 好值率', width: 100 },
  { dataIndex: 'confidenceLevel', title: '可信度', width: 80 },
  { dataIndex: 'fitnessLevel', title: '适用性', width: 80 },
  { dataIndex: 'nonEvalReason', title: '未参评原因', width: 130 },
];

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
      'PV 好值率',
      '可信度',
      '适用性',
      '未参评原因',
    ],
    rows: items.map((i) => [
      i.loopTagName,
      i.unitPath,
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
      <p><b>筛选</b>：时间范围影响 KPI 与趋势（默认近 30 天）；明细表的可信度/适用性为各回路最新状态。</p>
      <p><b>模块停用</b>：本页只依赖基础模块数据，任何模块组合下均完整可用。</p>
    `,
  });
}

watch([dateRange, plantNodeId], () => {
  auditPage.value = 1;
  load();
  loadAudit();
});

onMounted(() => {
  loadPlants();
  load();
  loadAudit();
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

    <!-- 2026-09-24：加载失败常驻提示（原空 catch 只留空图表，无法区分"没数据"与"服务异常"） -->
    <ClpmLoadErrorAlert :error="loadError" @retry="load" />

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
          <template v-if="column.dataIndex === 'nonEvalReason'">
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
    <ClpmDataCanvas
      title="位号级数据质量体检（点表口径，只读）"
      :empty="!auditLoading && !auditError && !auditData?.items?.length"
      empty-text="窗口内未发现位号级数据质量问题"
    >
      <template #extra>
        <div class="flex flex-wrap items-center gap-x-3 gap-y-1">
          <!-- 本分区时间窗独立于页面 RangePicker：显式标注，避免口径混淆 -->
          <span class="text-xs text-neutral-500">
            窗口：{{ auditWindowLabel }}（独立于页面时间范围）
          </span>
          <Select
            v-model:value="auditWindowHours"
            :options="AUDIT_WINDOW_OPTIONS"
            class="w-32"
            size="small"
            title="本分区时间窗（不跟随页面时间范围）"
            @change="handleAuditFilterChange"
          />
          <Select
            v-model:value="auditIssueType"
            :options="AUDIT_ISSUE_OPTIONS"
            allow-clear
            class="w-36"
            placeholder="问题类型"
            size="small"
            @change="handleAuditFilterChange"
          />
          <span
            class="flex items-center gap-1"
            title="事件密度比低于此值的位号判为「密度不足」；基线为等长前一窗口"
          >
            <span class="text-xs text-neutral-500">密度比 ≥</span>
            <InputNumber
              v-model:value="auditMinDensityRatio"
              :max="1"
              :min="0"
              :precision="2"
              :step="0.05"
              class="w-20"
              size="small"
              @change="handleAuditFilterChange"
            />
          </span>
          <span
            class="flex items-center gap-1"
            title="是否统计「含 HELD 填平」（缺口被保持值填充）的位号"
          >
            <span class="text-xs text-neutral-500">含 HELD</span>
            <Switch
              v-model:checked="auditIncludeHeld"
              size="small"
              @change="handleAuditFilterChange"
            />
          </span>
          <span
            v-if="auditData"
            class="text-xs text-neutral-500"
          >
            {{ auditData.summary.points }} 个位号：断流
            {{ auditData.summary.noData }} · 质量码坏
            {{ auditData.summary.badQuality }} · 密度不足
            {{ auditData.summary.lowDensity }} · 含 HELD
            {{ auditData.summary.heldFilled }}
          </span>
        </div>
      </template>

      <ClpmLoadErrorAlert :error="auditError" @retry="loadAudit" />

      <Table
        v-if="!auditError"
        :columns="auditColumns"
        :data-source="auditData?.items ?? []"
        :loading="auditLoading"
        :pagination="false"
        :scroll="{ x: 1100 }"
        row-key="pointId"
        size="small"
      >
        <template #bodyCell="{ column, record }">
          <template v-if="column.dataIndex === 'issues'">
            <span v-if="issueTags(record).length === 0" class="text-emerald-600">
              正常
            </span>
            <Tag
              v-for="issue in issueTags(record)"
              :key="issue"
              :color="DQ_AUDIT_ISSUE_COLOR[issue] ?? 'default'"
              class="mr-1"
            >
              {{ DQ_AUDIT_ISSUE_LABEL[issue] ?? issue }}
            </Tag>
          </template>
          <template v-else-if="column.dataIndex === 'densityRatio'">
            {{ fmtRatio(record.densityRatio) }}
          </template>
        </template>
      </Table>

      <div v-if="(auditData?.total ?? 0) > 0" class="mt-3 flex justify-end">
        <Pagination
          :current="auditPage"
          :page-size="auditPageSize"
          :page-size-options="['20', '50', '100']"
          :total="auditData?.total ?? 0"
          show-size-changer
          size="small"
          @change="handleAuditPageChange"
        />
      </div>
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
