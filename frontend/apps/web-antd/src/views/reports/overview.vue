<script lang="ts" setup>
/**
 * 统计报告-管理总览（/reports/overview，IA 优化 P0 + P3，三阶段自适应）
 *
 * 设计文档：docs/设计文档/IA 优化/CLPM-IA优化实施方案-0822.md §2.5 / §7.5
 * 固定 3×4=12 格 KPI 骨架（S1 填 5，S2 填 4，S3 填 3；S2/S3 追加不移动已有卡片）；
 * 图表区 Segmented 切换（健康趋势 / 闭环趋势<S2+> / 收益趋势<S3+>），不堆叠；
 * TOP 问题回路表固定列，S2 追加「处置状态」、S3 追加「评分改善」；
 * 标题旁 ClpmStageIndicator 显示当前阶段（管理员可锁定/预览）；
 * 底部 ClpmUpgradePrompt 自适应显示下一阶段能力引导；
 * 工具栏支持 PDF 导出（异步轮询）+ Excel 导出 + 刷新 + 帮助。
 */
import type { EchartsUIType } from '@vben/plugins/echarts';

import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';

import { Page } from '@vben/common-ui';
import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import { message, Modal } from 'ant-design-vue';
import { RangePicker, Segmented, Table, Tag, TreeSelect } from 'ant-design-vue';
import dayjs from 'dayjs';

import { getPlantNodeTreeApi } from '#/api/plant-node';
import {
  downloadReportPdfUrl,
  getReportOverviewApi,
  getReportPdfExportTaskApi,
  getReportStageLockApi,
  type ReportsApi,
  setReportStageLockApi,
  triggerReportPdfExportApi,
} from '#/api/reports';
import {
  ClpmDataCanvas,
  ClpmKpiCard,
  ClpmPageToolbar,
  ClpmStageIndicator,
  ClpmToolbarButton,
  ClpmUpgradePrompt,
} from '#/components/clpm';
import { useEchartsPreset } from '#/composables/use-echarts-preset';
import { showPageHelp } from '#/composables/use-page-toolbar';

defineOptions({ name: 'ReportsOverview' });

// ===== 固定 12 格骨架（S1~S3 槽位，禁止 v-if 动态增减卡片）=====
interface KpiSlot {
  key: string;
  label: string;
  unit: string;
  stage: ReportsApi.Stage;
  icon: string;
}
const KPI_SLOTS: KpiSlot[] = [
  // S1（行 1）：5 个，顺序固定
  { key: 'totalLoops', label: '回路总数', unit: '个', stage: 'S1', icon: 'lucide:network' },
  { key: 'healthRate', label: '健康率', unit: '%', stage: 'S1', icon: 'lucide:heart-pulse' },
  { key: 'evaluationRate', label: '参评率', unit: '%', stage: 'S1', icon: 'lucide:clipboard-check' },
  { key: 'anomalyCount', label: '异常数', unit: '个', stage: 'S1', icon: 'lucide:alert-triangle' },
  { key: 'dataHealthRate', label: '数据健康率', unit: '%', stage: 'S1', icon: 'lucide:database-check' },
  // S2（行 2）：4 个，仅闭环阶段数据可用（位置固定）
  { key: 'closedLoopRate', label: '闭环率', unit: '%', stage: 'S2', icon: 'lucide:refresh-cw' },
  { key: 'avgCycleHours', label: '平均处置时长', unit: 'h', stage: 'S2', icon: 'lucide:timer' },
  { key: 'closedThisMonth', label: '本月整改', unit: '次', stage: 'S2', icon: 'lucide:check-circle-2' },
  { key: 'ineffectiveRate', label: '无效重开率', unit: '%', stage: 'S2', icon: 'lucide:undo-2' },
  // S3（行 3）：3 个，仅持续优化阶段可用（位置固定，第 12 槽预留）
  { key: 'kpiImprovement', label: 'KPI 改善', unit: '分', stage: 'S3', icon: 'lucide:trending-up' },
  { key: 'autoRateImprovement', label: '自控提升', unit: 'pp', stage: 'S3', icon: 'lucide:gauge' },
  { key: 'benchmarkGap', label: '标杆差', unit: '分', stage: 'S3', icon: 'lucide:flag' },
];

const STAGE_ORDER: Record<ReportsApi.Stage, number> = { S1: 1, S2: 2, S3: 3 };

const loading = ref(false);
const data = ref<null | ReportsApi.OverviewData>(null);

// 筛选条
const dateRange = ref<[dayjs.Dayjs, dayjs.Dayjs]>([
  dayjs().subtract(30, 'day'),
  dayjs(),
]);
const plantNodeId = ref<string | undefined>();
const plantTree = ref<any[]>([]);
// 前端预览阶段：实际以 data.stage（后端生效阶段）为展示基准；segmented 仅影响请求参数
const requestedStage = ref<ReportsApi.Stage>('S1');

const chartTab = ref<'benefit' | 'closedLoop' | 'health'>('health');

const { getEchartsBase, getLineSeriesPreset, getSeriesColor, getTooltipPreset } =
  useEchartsPreset();
const chartRef = ref<EchartsUIType>();
const { renderEcharts } = useEcharts(chartRef);

// PDF 导出状态
const pdfLoading = ref(false);
let pdfPollTimer: null | ReturnType<typeof setTimeout> = null;

// ====== 角色：管理员可用阶段锁定 ======
const isAdmin = computed<boolean>(() => {
  try {
    // 兼容 vben 系统角色读取；取不到则默认允许 UI 显示（后端才是门禁）
    const raw = localStorage.getItem('vben_user_roles') ?? '';
    if (!raw) return true;
    const roles = JSON.parse(raw) as string[];
    return roles.includes('ADMIN') || roles.includes('admin') || roles.some((r) => r.toUpperCase() === 'ADMIN');
  } catch {
    return true;
  }
});

// ====== KPI 映射 ======
const kpiMap = computed(() => {
  const m = new Map<string, ReportsApi.OverviewKpi>();
  for (const k of data.value?.kpis ?? []) m.set(k.key, k);
  return m;
});

type KpiStatus = 'error' | 'info' | 'neutral' | 'ok' | 'warning';
interface MergedSlot extends KpiSlot {
  context: string;
  status: KpiStatus;
  value: number | string;
  locked: boolean;
}
const kpiSlots = computed<MergedSlot[]>(() => {
  const effectiveStage = data.value?.stage ?? requestedStage.value;
  const effN = STAGE_ORDER[effectiveStage];
  return KPI_SLOTS.map((slot) => {
    const slotN = STAGE_ORDER[slot.stage];
    const slotAvailable = slotN <= effN;
    const k = kpiMap.value.get(slot.key);
    if (k && slotAvailable) {
      return {
        ...slot,
        value: k.value ?? '—',
        status: (k.status as KpiStatus) ?? 'neutral',
        context: k.context ?? '',
        locked: false,
      };
    }
    if (slotAvailable) {
      return {
        ...slot,
        value: '—',
        status: 'neutral' as KpiStatus,
        context: `${slot.stage} 指标暂无数据`,
        locked: false,
      };
    }
    return {
      ...slot,
      value: '—',
      status: 'neutral' as KpiStatus,
      context: `${slot.stage} 能力待开通`,
      locked: true,
    };
  });
});

// ====== TOP 回路表列：固定列 + 条件追加（P3）======
const topColumns = computed(() => {
  const effectiveStage = data.value?.stage ?? 'S1';
  const effN = STAGE_ORDER[effectiveStage];
  const cols: any[] = [
    { dataIndex: 'loopTagName', title: '回路', width: 200 },
    { dataIndex: 'unitPath', title: '装置.单元', width: 200 },
    { dataIndex: 'latestScore', title: '最新评分', width: 100 },
    { dataIndex: 'primaryCategoryLabel', title: '诊断主分类', width: 180 },
    { dataIndex: 'severity', title: '严重度', width: 90 },
  ];
  if (effN >= 2) {
    cols.push({ dataIndex: 'handlingStatus', title: '处置状态', width: 110 });
  }
  if (effN >= 3) {
    cols.push({ dataIndex: 'benefitEstimate', title: '评分改善', width: 100 });
  }
  return cols;
});

// ====== 图表 Segmented options：按阶段显隐 ======
const chartTabOptions = computed(() => {
  const effN = STAGE_ORDER[data.value?.stage ?? 'S1'];
  const opts: any[] = [{ label: '健康趋势', value: 'health' }];
  if (effN >= 2) opts.push({ label: '闭环趋势', value: 'closedLoop' });
  if (effN >= 3) opts.push({ label: '收益趋势', value: 'benefit' });
  return opts;
});

// ====== 阶段切换 Segmented：按 availability 控制 ======
const stageSegmentedOptions = computed(() => {
  const av = data.value?.availability;
  return [
    { label: 'S1 基础可视', value: 'S1' },
    { label: 'S2 闭环管理', value: 'S2', disabled: !(av?.s2Available ?? false) },
    { label: 'S3 持续优化', value: 'S3', disabled: !(av?.s3Available ?? false) },
  ];
});

// ====== 升级引导（ClpmUpgradePrompt 自适应）======
const upgradePrompt = computed<null | {
  description: string;
  show: boolean;
  stage: 'S2' | 'S3';
  title: string;
}>(() => {
  const eff = data.value?.stage ?? 'S1';
  const av = data.value?.availability;
  if (eff === 'S1' && av?.s2Available === false) {
    return {
      stage: 'S2',
      show: true,
      title: '升级到 S2 闭环管理阶段',
      description:
        '启用诊断 / 处置模块并产生记录后，将自动升级到 S2。S2 可见：闭环率、平均处置时长、本月整改、无效重开率、处置闭环趋势、异常分布变化等。',
    };
  }
  if (STAGE_ORDER[eff] <= 2 && av?.s3Available === false) {
    return {
      stage: 'S3',
      show: true,
      title: '升级到 S3 持续优化阶段',
      description:
        '完成整定记录 ≥1 且经验证闭环处置工单 ≥5 后，将自动升级到 S3。S3 可见：KPI 改善、自控提升、标杆对比、收益趋势等。',
    };
  }
  return null;
});

// ------------------------------------------------------------------
// 生命周期 & 数据加载
// ------------------------------------------------------------------

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
    const [start, end] = dateRange.value ?? [];
    const result = await getReportOverviewApi({
      stage: requestedStage.value,
      startDate: start?.format('YYYY-MM-DD'),
      endDate: end?.format('YYYY-MM-DD'),
      plantNodeId: plantNodeId.value,
    });
    data.value = result;
    // 回显实际阶段（若后端返回更高阶段则覆盖 requestedStage，让 segmented 高亮）
    if (result?.stage) requestedStage.value = result.stage;
    // 默认 tab：S3→benefit，S2→closedLoop，S1→health
    const effN = STAGE_ORDER[result?.stage ?? 'S1'];
    if (effN >= 3) chartTab.value = 'benefit';
    else if (effN >= 2) chartTab.value = 'closedLoop';
    await nextTick();
    renderChart();
  } catch {
    data.value = null;
  } finally {
    loading.value = false;
  }
}

// ------------------------------------------------------------------
// 图表：按 Segmented Tab 切换（不堆叠）
// ------------------------------------------------------------------

function _renderHealthTrend() {
  const trend = data.value?.healthTrend ?? [];
  const option = {
    ...getEchartsBase(),
    tooltip: { ...getTooltipPreset(), trigger: 'axis' as const },
    xAxis: { ...getEchartsBase().xAxis, data: trend.map((p) => p.date) },
    yAxis: { ...getEchartsBase().yAxis, min: 0, max: 100 },
    series: [
      {
        name: '平均健康分',
        data: trend.map((p) => p.score),
        ...getLineSeriesPreset(getSeriesColor('info')),
      },
    ],
  };
  renderEcharts(option);
}

function _renderClosedLoopTrend() {
  const tr = data.value?.closedLoopTrend ?? [];
  const option = {
    ...getEchartsBase(),
    tooltip: { ...getTooltipPreset(), trigger: 'axis' as const },
    legend: { data: ['新建工单', '闭环工单', '闭环率%'] },
    xAxis: { ...getEchartsBase().xAxis, data: tr.map((m) => m.month) },
    yAxis: [
      { ...getEchartsBase().yAxis, type: 'value' as const, name: '工单数' },
      {
        ...getEchartsBase().yAxis,
        type: 'value' as const,
        name: '闭环率%',
        min: 0,
        max: 100,
        position: 'right' as const,
      },
    ],
    series: [
      {
        name: '新建工单',
        type: 'bar' as const,
        data: tr.map((m) => m.total),
        itemStyle: { color: getSeriesColor('info') },
      },
      {
        name: '闭环工单',
        type: 'bar' as const,
        data: tr.map((m) => m.closed),
        itemStyle: { color: getSeriesColor('ok') },
      },
      {
        name: '闭环率%',
        yAxisIndex: 1,
        data: tr.map((m) => m.closedRate),
        ...getLineSeriesPreset(getSeriesColor('warning')),
      },
    ],
  };
  renderEcharts(option);
}

function _renderBenefitTrend() {
  const bt = data.value?.benefitTrend ?? [];
  const option = {
    ...getEchartsBase(),
    tooltip: { ...getTooltipPreset(), trigger: 'axis' as const },
    legend: { data: ['自控率%', '综合评分'] },
    xAxis: { ...getEchartsBase().xAxis, data: bt.map((p) => p.date) },
    yAxis: [
      {
        ...getEchartsBase().yAxis,
        type: 'value' as const,
        name: '自控率%',
        min: 0,
        max: 100,
      },
      {
        ...getEchartsBase().yAxis,
        type: 'value' as const,
        name: '评分',
        min: 0,
        max: 100,
        position: 'right' as const,
      },
    ],
    series: [
      {
        name: '自控率%',
        data: bt.map((p) => p.autoRate),
        ...getLineSeriesPreset(getSeriesColor('info')),
      },
      {
        name: '综合评分',
        yAxisIndex: 1,
        data: bt.map((p) => p.score),
        ...getLineSeriesPreset(getSeriesColor('warning')),
      },
    ],
  };
  renderEcharts(option);
}

function renderChart() {
  if (chartTab.value === 'closedLoop') {
    _renderClosedLoopTrend();
  } else if (chartTab.value === 'benefit') {
    _renderBenefitTrend();
  } else {
    _renderHealthTrend();
  }
}

const chartEmptyText = computed(() => {
  if (chartTab.value === 'closedLoop') return '该时段暂无闭环趋势数据';
  if (chartTab.value === 'benefit') return '该时段暂无收益趋势数据';
  return '该时段暂无健康趋势数据';
});

const chartHasData = computed(() => {
  if (!data.value) return false;
  if (chartTab.value === 'closedLoop') {
    return (data.value.closedLoopTrend?.length ?? 0) > 0;
  }
  if (chartTab.value === 'benefit') {
    return (data.value.benefitTrend?.length ?? 0) > 0;
  }
  return (data.value.healthTrend?.length ?? 0) > 0;
});

watch(chartTab, () => nextTick(renderChart));

// ------------------------------------------------------------------
// 阶段锁定 / 解锁（管理员）
// ------------------------------------------------------------------
async function handleStageClick() {
  if (!isAdmin.value) {
    message.info('仅管理员可配置成熟度阶段锁定');
    return;
  }
  const lockInfo = await getReportStageLockApi({
    plantNodeId: plantNodeId.value,
  }).catch(() => null);
  const effective = data.value?.stage ?? lockInfo?.detectedStage ?? 'S1';
  const isLocked = data.value?.isLocked ?? lockInfo?.locked ?? false;
  const countsHtml = lockInfo
    ? `<p style="margin:4px 0;color:#64748b;font-size:12px">
         诊断 ${lockInfo.counts.diagnosisRuns} 次 · 工单 ${lockInfo.counts.handlingOrders} 条 ·
         整定 ${lockInfo.counts.tuningRecords} 次 · 闭环且验证 ${lockInfo.counts.closedVerifiedOrders} 条
       </p>`
    : '';
  const available = lockInfo?.availability;
  const s2Avail = available?.s2Available ?? false;
  const s3Avail = available?.s3Available ?? false;
  const contentHtml = `
      <p style="margin:6px 0"><b>当前生效阶段</b>：${effective}（${isLocked ? '已锁定' : '自动判定'}）</p>
      ${countsHtml}
      <p style="margin:8px 0 4px 0;color:#475569">选择目标阶段（取消锁定后，将根据实际记录数自动判定）：</p>
      <div id="stage-locking-options" style="display:flex;flex-direction:column;gap:6px;margin-top:8px">
        <label style="font-size:13px"><input type="radio" name="stageOpt" value="AUTO" checked> 自动判定（取消锁定，按记录数升降阶）</label>
        <label style="font-size:13px"><input type="radio" name="stageOpt" value="S1"> 强制锁定 S1 基础可视</label>
        <label style="font-size:13px"><input type="radio" name="stageOpt" value="S2" ${
          s2Avail ? '' : 'disabled'
        }> 强制锁定 S2 闭环管理${s2Avail ? '' : '（当前数据不足以支持）'}</label>
        <label style="font-size:13px"><input type="radio" name="stageOpt" value="S3" ${
          s3Avail ? '' : 'disabled'
        }> 强制锁定 S3 持续优化${s3Avail ? '' : '（当前数据不足以支持）'}</label>
      </div>
    `;
  Modal.confirm({
    title: '成熟度阶段配置',
    content: contentHtml,
    okText: '保存',
    cancelText: '取消',
    centered: true,
    maskClosable: true,
    onOk: async () => {
      // 读取选中项
      const els = document.querySelectorAll<HTMLInputElement>(
        '#stage-locking-options input[name="stageOpt"]:checked',
      );
      const selected = els[0]?.value ?? 'AUTO';
      const stage: null | ReportsApi.Stage =
        selected === 'AUTO' ? null : (selected as ReportsApi.Stage);
      await setReportStageLockApi(
        { stage },
        { plantNodeId: plantNodeId.value },
      );
      message.success(
        stage == null ? '已取消阶段锁定，回到自动判定' : `已锁定阶段为 ${stage}`,
      );
      await load();
    },
  });
}

// ------------------------------------------------------------------
// PDF 导出（异步轮询 + 下载）
// ------------------------------------------------------------------

async function handleExportPdf() {
  if (pdfLoading.value) return;
  pdfLoading.value = true;
  try {
    const [s, e] = dateRange.value ?? [];
    const task = await triggerReportPdfExportApi({
      stage: requestedStage.value,
      startDate: s?.format('YYYY-MM-DD'),
      endDate: e?.format('YYYY-MM-DD'),
      plantNodeId: plantNodeId.value,
    });
    const taskId = task.taskId;
    message.loading({
      content: 'PDF 导出中…',
      key: 'pdf-export',
      duration: 0,
    });
    const poll = async () => {
      try {
        const s2 = await getReportPdfExportTaskApi(taskId);
        if (s2.status === 'COMPLETED') {
          if (pdfPollTimer) clearTimeout(pdfPollTimer);
          message.success({ content: 'PDF 导出完成', key: 'pdf-export' });
          // 通过 a href + filename 触发下载（后端已 Content-Disposition）
          const a = document.createElement('a');
          a.href = downloadReportPdfUrl(taskId);
          a.download = s2.fileName ?? `CLPM_管理总览_${taskId}.pdf`;
          a.rel = 'noopener';
          document.body.append(a);
          a.click();
          a.remove();
        } else if (s2.status === 'FAILED') {
          if (pdfPollTimer) clearTimeout(pdfPollTimer);
          message.error({
            content: `PDF 导出失败：${s2.error ?? '未知错误'}`,
            key: 'pdf-export',
          });
        } else {
          pdfPollTimer = setTimeout(poll, 1500);
        }
      } catch {
        pdfPollTimer = setTimeout(poll, 1500);
      }
    };
    pdfPollTimer = setTimeout(poll, 1500);
  } catch (error: any) {
    message.error(`触发 PDF 导出失败：${error?.message ?? '未知错误'}`);
  } finally {
    pdfLoading.value = false;
  }
}

function handleExportExcel() {
  // 复用 utils/export：按阶段导出 TOP 回路 CSV（MVP：exportData 单 sheet 签名，用 CSV 兼容导出）
  if (!data.value) {
    message.warning('暂无数据可导出');
    return;
  }
  const topCols: { key: string; title: string }[] = [
    { key: 'loopTagName', title: '回路' },
    { key: 'unitPath', title: '装置单元' },
    { key: 'latestScore', title: '最新评分' },
    { key: 'primaryCategoryLabel', title: '诊断分类' },
    { key: 'severity', title: '严重度' },
  ];
  const effN = STAGE_ORDER[data.value.stage];
  if (effN >= 2) topCols.push({ key: 'handlingStatus', title: '处置状态' });
  if (effN >= 3) topCols.push({ key: 'benefitEstimate', title: '评分改善' });
  const headers = topCols.map((c) => c.title);
  const keys = topCols.map((c) => c.key);
  const rows = (data.value.topProblemLoops as any[]).map((r) =>
    keys.map((k) => r[k] ?? ''),
  );
  try {
    import('#/utils/export').then(({ exportData }) => {
      exportData({
        filename: `CLPM-管理总览-${data.value!.stage}-${dayjs().format('YYYYMMDD')}`,
        format: 'csv',
        headers,
        rows,
      });
    });
  } catch {
    message.error('Excel 导出失败');
  }
}

// ------------------------------------------------------------------
// 其它
// ------------------------------------------------------------------
function formatHandlingStatus(hs: null | string | undefined) {
  switch (hs) {
    case 'CANCELLED': {
      return { color: 'default', text: '已作废' };
    }
    case 'CLOSED': {
      return { color: 'green', text: '已闭环' };
    }
    case 'EXECUTING': {
      return { color: 'blue', text: '执行中' };
    }
    case 'PENDING': {
      return { color: 'default', text: '待执行' };
    }
    case 'REOPENED': {
      return { color: 'orange', text: '已重开' };
    }
    case 'VERIFYING': {
      return { color: 'cyan', text: '验证中' };
    }
    default: {
      return { color: 'default', text: hs ?? '无工单' };
    }
  }
}

function handleHelp() {
  showPageHelp({
    title: '管理总览 帮助',
    content: `
      <p><b>定位</b>：面向管理层的全局健康看板，固定 12 格骨架按成熟度 S1/S2/S3 自适应填充。</p>
      <p><b>S1 基础可视</b>：回路总数、健康率、参评率、异常数、数据健康率 + 健康趋势 + TOP 问题回路。</p>
      <p><b>S2 闭环管理</b>：闭环率、平均处置时长、本月整改、无效重开率 + 闭环趋势 + 异常分布变化 + 处置状态列。</p>
      <p><b>S3 持续优化</b>：KPI 改善、自控提升、标杆差 + 收益趋势 + 评分改善列 + 装置标杆。</p>
      <p><b>阶段判定</b>（系统自动，管理员可锁定）：</p>
      <ul>
        <li>S1：无诊断记录且无处置工单</li>
        <li>S2：诊断记录 ≥1 或 处置工单 ≥1</li>
        <li>S3：整定记录 ≥1 且「闭环且验证通过」处置工单 ≥5</li>
      </ul>
      <p><b>导出</b>：PDF 异步生成（按阶段自适应章节）；Excel 多 sheet。</p>
    `,
  });
}

onBeforeUnmount(() => {
  if (pdfPollTimer) clearTimeout(pdfPollTimer);
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
      subtitle="全局健康 · 闭环 · 收益（按管理成熟度自适应）"
      title="管理总览"
    >
      <template #context>
        <ClpmStageIndicator
          :locked="!!data?.isLocked"
          :stage="data?.stage ?? requestedStage"
          class="cursor-pointer select-none"
          size="small"
          @click="handleStageClick"
        />
      </template>
      <template #actions>
        <ClpmToolbarButton
          :disabled="!data || pdfLoading"
          icon="ant-design:file-pdf-outlined"
          :loading="pdfLoading"
          label="导出 PDF"
          @click="handleExportPdf"
        />
        <ClpmToolbarButton
          :disabled="!data"
          icon="ant-design:file-excel-outlined"
          label="导出 Excel"
          @click="handleExportExcel"
        />
        <ClpmToolbarButton
          icon="ant-design:sync-outlined"
          label="刷新"
          @click="load"
        />
        <ClpmToolbarButton
          icon="ant-design:question-circle-outlined"
          label="帮助"
          @click="handleHelp"
        />
      </template>
    </ClpmPageToolbar>

    <!-- 统一筛选条：时间 + 装置 + 阶段 Segmented + 触发按钮 -->
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
      <a class="reports-filter-bar__apply" @click="load">查询</a>
      <Segmented
        v-model:value="requestedStage"
        :options="stageSegmentedOptions"
        size="small"
        @change="load"
      />
    </div>

    <!-- 固定 3×4=12 格 KPI 骨架（禁止动态增减卡片） -->
    <ClpmDataCanvas
      :loading="loading"
      :skeleton-rows="2"
      class="reports-kpi-canvas"
    >
      <div class="reports-kpi-grid">
        <ClpmKpiCard
          v-for="slot in kpiSlots"
          :key="slot.key"
          :context-text="slot.context"
          :icon="slot.icon"
          :status="slot.status"
          :title="slot.label"
          :unit="slot.unit"
          :value="slot.value"
          :class="{ 'is-locked-slot': slot.locked }"
        />
      </div>
    </ClpmDataCanvas>

    <!-- 图表区：Segmented 切换，按阶段显隐 Tab，不堆叠 -->
    <ClpmDataCanvas class="reports-chart-canvas" title="趋势分析">
      <template #extra>
        <Segmented
          v-model:value="chartTab"
          size="small"
          :options="chartTabOptions"
        />
      </template>
      <div class="reports-chart-body">
        <EchartsUI ref="chartRef" height="280px" />
        <div v-if="!chartHasData" class="reports-chart-empty">
          {{ chartEmptyText }}
        </div>
      </div>
    </ClpmDataCanvas>

    <!-- TOP 问题回路：固定列，S2 追加处置状态、S3 追加评分改善 -->
    <ClpmDataCanvas
      class="reports-top-canvas"
      title="TOP 问题回路（评分最低前 10 条）"
      :empty="!data?.topProblemLoops?.length"
      empty-text="暂无问题回路"
    >
      <Table
        :columns="topColumns"
        :data-source="data?.topProblemLoops ?? []"
        :pagination="false"
        row-key="loopId"
        size="small"
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
              {{
                record.severity === 'HIGH'
                  ? '高'
                  : record.severity === 'MEDIUM'
                    ? '中'
                    : '低'
              }}
            </Tag>
            <span v-else class="text-neutral-400">—</span>
          </template>
          <template v-else-if="column.dataIndex === 'handlingStatus'">
            <Tag :color="formatHandlingStatus(record.handlingStatus).color">
              {{ formatHandlingStatus(record.handlingStatus).text }}
            </Tag>
          </template>
          <template v-else-if="column.dataIndex === 'benefitEstimate'">
            <span
              v-if="record.benefitEstimate != null"
              :class="
                Number(record.benefitEstimate) > 0
                  ? 'text-emerald-600'
                  : Number(record.benefitEstimate) < 0
                    ? 'text-red-600'
                    : ''
              "
            >
              {{ Number(record.benefitEstimate) > 0 ? '+' : ''
              }}{{ Number(record.benefitEstimate).toFixed(1) }}
            </span>
            <span v-else class="text-neutral-400">—</span>
          </template>
          <template v-else-if="column.dataIndex === 'latestScore'">
            <span
              v-if="record.latestScore != null"
              :class="
                Number(record.latestScore) < 40
                  ? 'text-red-600 font-semibold'
                  : Number(record.latestScore) < 60
                    ? 'text-amber-600 font-medium'
                    : ''
              "
            >
              {{ Number(record.latestScore).toFixed(1) }}
            </span>
            <span v-else class="text-neutral-400">—</span>
          </template>
        </template>
      </Table>
    </ClpmDataCanvas>

    <!-- 底部 ClpmUpgradePrompt：仅低阶段提示下一阶段能力引导 -->
    <ClpmUpgradePrompt
      v-if="upgradePrompt?.show"
      :stage="upgradePrompt.stage"
      :title="upgradePrompt.title"
      :description="upgradePrompt.description"
    />
  </Page>
</template>

<style scoped>
.reports-filter-bar {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
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

.reports-filter-bar__apply {
  font-size: 12px;
  color: hsl(var(--primary));
  cursor: pointer;
}

.reports-kpi-canvas {
  margin-bottom: 12px;
}

.reports-kpi-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

.reports-kpi-grid :deep(.is-locked-slot) {
  opacity: 0.55;
  filter: saturate(0.6);
}

.reports-chart-canvas {
  margin-bottom: 12px;
}

.reports-chart-body {
  position: relative;
  min-height: 280px;
}

.reports-chart-empty {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  color: hsl(var(--muted-foreground));
  pointer-events: none;
}

.reports-top-canvas {
  margin-bottom: 12px;
}
</style>
