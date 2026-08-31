<script setup lang="ts">
/**
 * 处置报告页（/reports/handling，IA 优化 P0 由 /handling/statistics 迁入）
 *
 * 设计文档：docs/MVP设计/08-处置模块设计方案.md §8.4（v1.1）
 * 汇总卡（周期闭环数/闭环率/平均处置时长/无效率/平均 KPI 改善/驳回率/平均排程周期
 * + P2-1 新增：SLA 按时闭环率 / 整改有效率）
 * + 月度趋势 / 建议漏斗（P2-1 置换类型/装置分布）/ 人员工作量（P2-1）+ Top 问题回路表。
 * 数据源：GET /reports/handling-statistics（R1 自持，直读 handling_order/
 * loop_action_item，处置模块禁用时不受影响）。
 *
 * 视觉规范（IA 优化 §6）：ClpmKpiCard 状态色驱动（禁硬编码 hex）、
 * 面板用 ClpmDataCanvas（禁 AntD Card）。
 */
import type { Dayjs } from 'dayjs';

import type { EchartsUIType } from '@vben/plugins/echarts';

import type { HandlingApi } from '#/api/handling';
import type { ReportsApi } from '#/api/reports';

import { computed, onMounted, ref, watch } from 'vue';
import { useRouter } from 'vue-router';

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
import { getReportHandlingStatisticsApi } from '#/api/reports';
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

defineOptions({ name: 'ReportsHandling' });

const router = useRouter();

const loading = ref(false);
const data = ref<HandlingApi.StatisticsData | null>(null);

const { getBarSeriesPreset, getEchartsBase, getSeriesColor, getTooltipPreset } =
  useEchartsPreset();
const funnelChartRef = ref<EchartsUIType>();
const { renderEcharts: renderFunnel } = useEcharts(funnelChartRef);

// 统一筛选条（P0-6：时间 + 装置，透传 reports 自持端点）
const dateRange = ref<[Dayjs, Dayjs]>([dayjs().subtract(180, 'day'), dayjs()]);
const plantNodeId = ref<string | undefined>();
const plantTree = ref<any[]>([]);

function queryParams(): ReportsApi.ReportQuery & { months: number } {
  const [start, end] = dateRange.value ?? [];
  return {
    months: 6,
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
    data.value = await getReportHandlingStatisticsApi(queryParams());
  } catch {
    data.value = null;
  } finally {
    loading.value = false;
    renderFunnelChart();
  }
}

// ===== 建议漏斗（P2-1：五态流转量横向柱图，置换原类型/装置分布文本列表） =====
const funnelEmpty = computed(
  () => (data.value?.suggestionFunnel ?? []).every((f) => f.count === 0),
);

function renderFunnelChart() {
  const funnel = data.value?.suggestionFunnel ?? [];
  if (funnel.length === 0 || funnelEmpty.value) return;
  // 倒序填入：待审核在最上方，形成漏斗视觉
  const stages = funnel.toReversed();
  renderFunnel({
    ...getEchartsBase(),
    tooltip: { ...getTooltipPreset(), trigger: 'axis' },
    grid: { ...getEchartsBase().grid, left: 8 },
    xAxis: { ...getEchartsBase().yAxis },
    yAxis: {
      ...getEchartsBase().xAxis,
      type: 'category' as const,
      data: stages.map((f) => f.label),
    },
    series: [
      {
        name: '建议数',
        data: stages.map((f) => f.count),
        ...getBarSeriesPreset(getSeriesColor('info')),
        barWidth: '55%',
        label: {
          show: true,
          position: 'right' as const,
          fontSize: 11,
        },
      },
    ],
  });
}

// ===== 汇总卡（null → '—'，状态色走 ClpmKpiCard status token） =====
interface SummaryCard {
  key: string;
  title: string;
  value: string;
  status: 'info' | 'neutral' | 'ok' | 'warning';
  icon: string;
  contextText?: string;
  infoTip?: string;
}
const summaryCards = computed<SummaryCard[]>(() => {
  const s = data.value?.summary;
  return [
    {
      key: 'closedThisMonth',
      // 传时间窗时闭环数按 verified_at 归窗，标题随之诚实化
      title: dateRange.value ? '窗口闭环' : '本月闭环',
      value: fmtInt(s?.closedThisMonth),
      status: 'ok' as const,
      icon: 'lucide:check-circle-2',
    },
    {
      key: 'closeRate',
      title: '闭环率',
      value: fmtPct(s?.closeRate),
      status: 'info' as const,
      icon: 'lucide:percent',
    },
    {
      key: 'avgCycleHours',
      title: '平均处置时长',
      value: fmtHours(s?.avgCycleHours),
      status: 'neutral' as const,
      icon: 'lucide:timer',
    },
    {
      key: 'ineffectiveRate',
      title: '无效重开率',
      value: fmtPct(s?.ineffectiveRate),
      status: 'warning' as const,
      icon: 'lucide:alert-triangle',
    },
    {
      key: 'avgKpiDelta',
      title: '平均 KPI 改善',
      value: fmtDelta(s?.avgKpiDelta),
      status: 'neutral' as const,
      icon: 'lucide:trending-up',
    },
    {
      key: 'rejectRate',
      title: '驳回率',
      value: fmtPct(s?.rejectRate),
      status: 'warning' as const,
      icon: 'lucide:x-circle',
    },
    {
      key: 'avgScheduleHours',
      title: '平均排程周期',
      value: fmtHours(s?.avgScheduleHours),
      status: 'neutral' as const,
      icon: 'lucide:calendar-clock',
    },
    // P2-1（方案 §5.1）：SLA 达成率卡（按时闭环率 + WARN/BREACH 计数）
    {
      key: 'slaOnTimeRate',
      title: 'SLA 按时闭环率',
      value: fmtPct(data.value?.sla?.onTimeRate),
      status: 'ok' as const,
      icon: 'lucide:alarm-clock-check',
      contextText: `预警 ${data.value?.sla?.warnCount ?? 0} · 违约 ${
        data.value?.sla?.breachCount ?? 0
      }`,
      infoTip:
        '按时闭环率=在 SLA 期限内闭环的工单 / 有 SLA 期限的闭环工单；预警/违约计数为当前 sla_stage=WARN/BREACH 的工单数（与处置模块工单页口径一致）',
    },
    // P2-1（方案 §5.1）：整改有效率卡（verify_result 维度）
    {
      key: 'effectiveRate',
      title: '整改有效率',
      value: fmtPct(data.value?.verifyResult?.effectiveRate),
      status: 'info' as const,
      icon: 'lucide:badge-check',
      contextText: `有效 ${data.value?.verifyResult?.effective ?? 0} · 无效 ${
        data.value?.verifyResult?.ineffective ?? 0
      }`,
      infoTip:
        '整改有效率=验证结论 EFFECTIVE / 已验证工单（verify_result 维度；与"无效重开率"互为补数，无验证记录时显示 —）',
    },
  ];
});

function fmtInt(v: null | number | undefined): string {
  return typeof v === 'number' ? String(v) : '—';
}
function fmtPct(v: null | number | undefined): string {
  return typeof v === 'number' ? `${(v * 100).toFixed(1)}%` : '—';
}
function fmtHours(v: null | number | undefined): string {
  return typeof v === 'number'
    ? (v >= 24
      ? `${(v / 24).toFixed(1)} 天`
      : `${v.toFixed(1)} h`)
    : '—';
}
function fmtDelta(v: null | number | undefined): string {
  return typeof v === 'number' ? `${v > 0 ? '+' : ''}${v.toFixed(1)}` : '—';
}

// ===== Top 问题回路（工单口径：orderTotal=工单总数） =====
const topColumns = [
  { dataIndex: 'loopTagName', title: '回路', width: 150 },
  { dataIndex: 'unitPath', title: '装置.单元', width: 170 },
  { dataIndex: 'orderTotal', title: '工单总数', width: 90 },
  { dataIndex: 'reopened', title: '重开', width: 70 },
  { dataIndex: 'lastClosedKpiDelta', title: '最近 KPI 改善', width: 120 },
];

// ===== 人员工作量（P2-1：直读 mv_staff_workload，全量口径） =====
const staffColumns = [
  { dataIndex: 'userName', title: '人员' },
  { dataIndex: 'activeCount', title: '进行中', width: 80, align: 'right' as const },
  { dataIndex: 'closedCount', title: '已闭环', width: 80, align: 'right' as const },
  {
    dataIndex: 'slaWarnedCount',
    title: 'SLA 预警',
    width: 90,
    align: 'right' as const,
  },
];

function gotoArchive(loopId: string) {
  router.push({ path: '/handling/archive', query: { loopId } });
}

// ===== 导出（P0-8：CSV/Excel 双格式，对齐绩效报告交互） =====
const exporting = ref(false);

function handleExport(format: 'csv' | 'excel' = 'csv') {
  const loops = data.value?.topLoops ?? [];
  if (loops.length === 0) {
    message.warning('当前无数据可导出');
    return;
  }
  exportData({
    filename: `handling_top_loops_${dayjs().format('YYYYMMDD')}`,
    format,
    sheetName: 'Top 问题回路',
    headers: ['回路', '装置.单元', '工单总数', '重开', '最近 KPI 改善'],
    rows: loops.map((l) => [
      l.loopTagName,
      l.unitPath ?? '',
      l.orderTotal,
      l.reopened,
      l.lastClosedKpiDelta == null
        ? ''
        : `${l.lastClosedKpiDelta > 0 ? '+' : ''}${l.lastClosedKpiDelta.toFixed(1)}`,
    ]),
  });
}

function handleHelp() {
  showPageHelp({
    title: '处置报告 帮助',
    content: `
      <p><b>定位</b>：处置活动管理回顾——闭环了多少、效率如何、哪些回路反复出问题。</p>
      <p><b>口径</b>：闭环率=已闭环/已验证；平均处置时长=工单创建到验证闭环的均值；无效重开率=验证无效/已验证；驳回率=建议 REJECTED/已审核；平均排程周期=工单创建到开工的均值。</p>
      <p><b>SLA</b>：按时闭环率=期限内闭环/有期限闭环单；预警/违约为 sla_stage=WARN/BREACH 当前计数。整改有效率=EFFECTIVE/已验证（与无效重开率互为补数）。</p>
      <p><b>建议漏斗</b>：五态流转量（待审核→已接受→已转工单/已驳回/已忽略），按建议提出时间归窗，与驳回率同口径。</p>
      <p><b>人员工作量</b>：直读物化视图 mv_staff_workload（5 分钟刷新），为全量口径，不随时间/装置筛选变化。</p>
      <p><b>月界</b>：北京时间自然月。数据不足（无闭环记录）时显示 —，不出误导性 0。</p>
      <p><b>筛选</b>：时间范围与装置筛选影响除人员工作量外的全部区块；未选时间窗时闭环数为本月口径，选择后按验证闭环时间归窗。</p>
      <p><b>模块停用</b>：处置模块停用时本页照常可用，展示历史数据归档，查询与导出不受影响。</p>
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
      subtitle="处置活动管理回顾 · 本月闭环 / 处置效率 / 问题回路 Top"
      title="处置报告"
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
            :loading="exporting"
            tooltip="导出 Top 问题回路数据"
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

    <!-- P0-5：处置模块停用时灰色归档横幅（历史数据可查询导出） -->
    <ClpmModuleArchivedBanner :modules="['handling']" />

    <!-- P0-6：统一筛选条（时间 + 装置，透传 reports 自持端点） -->
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

    <!-- 汇总指标卡（§8.4 + P2-1 扩展为 9 卡，状态色走 token） -->
    <div class="mb-3 mt-2 grid grid-cols-2 gap-3 md:grid-cols-3">
      <ClpmKpiCard
        v-for="c in summaryCards"
        :key="c.key"
        :icon="c.icon"
        :status="c.status"
        :title="c.title"
        :value="c.value"
        :context-text="c.contextText"
        :info-tip="c.infoTip"
      />
    </div>

    <div class="grid grid-cols-2 gap-3">
      <ClpmDataCanvas
        title="月度趋势（近 6 月）"
        :empty="!data?.monthly?.length"
        empty-text="暂无月度数据"
      >
        <table class="w-full text-xs">
          <thead>
            <tr class="text-neutral-500">
              <th class="py-0.5 text-left font-normal">月份</th>
              <th class="py-0.5 text-right font-normal">闭环数</th>
              <th class="py-0.5 text-right font-normal">闭环率</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="m in data?.monthly ?? []"
              :key="m.month"
              class="border-t border-neutral-200 dark:border-neutral-700"
            >
              <td class="py-0.5">{{ m.month }}</td>
              <td class="py-0.5 text-right">{{ m.closed }}</td>
              <td class="py-0.5 text-right">{{ fmtPct(m.closeRate) }}</td>
            </tr>
          </tbody>
        </table>
      </ClpmDataCanvas>

      <!-- P2-1：建议漏斗图（置换原"处置类型分布"文本列表，信息密度更高） -->
      <ClpmDataCanvas
        title="建议漏斗（五态流转）"
        :empty="funnelEmpty"
        empty-text="暂无建议数据"
      >
        <EchartsUI ref="funnelChartRef" height="260px" />
      </ClpmDataCanvas>

      <!-- P2-1：人员工作量卡（置换原"装置分布"文本列表；MV 全量口径） -->
      <ClpmDataCanvas
        title="人员工作量"
        description="物化视图全量口径（5 分钟刷新），不随时间/装置筛选变化"
        :empty="!data?.staffWorkload?.length"
        empty-text="暂无人员工作量数据"
      >
        <Table
          :columns="staffColumns"
          :data-source="data?.staffWorkload ?? []"
          :pagination="false"
          row-key="userName"
          size="small"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.dataIndex === 'slaWarnedCount'">
              <span
                :class="record.slaWarnedCount > 0 ? 'text-amber-600' : ''"
              >
                {{ record.slaWarnedCount }}
              </span>
            </template>
          </template>
        </Table>
      </ClpmDataCanvas>

      <ClpmDataCanvas
        title="Top 问题回路（重开最多）"
        :empty="!data?.topLoops?.length"
        empty-text="暂无问题回路"
      >
        <Table
          :columns="topColumns"
          :custom-row="
            (record: HandlingApi.TopLoopItem) => ({
              onClick: () => gotoArchive(record.loopId),
              style: { cursor: 'pointer' },
            })
          "
          :data-source="data?.topLoops ?? []"
          :pagination="false"
          row-key="loopId"
          size="small"
        >
          <template #bodyCell="{ column, record }">
            <template v-if="column.dataIndex === 'lastClosedKpiDelta'">
              <span
                :class="
                  record.lastClosedKpiDelta > 0
                    ? 'text-emerald-600'
                    : record.lastClosedKpiDelta < 0
                      ? 'text-rose-600'
                      : ''
                "
              >
                {{ fmtDelta(record.lastClosedKpiDelta) }}
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
