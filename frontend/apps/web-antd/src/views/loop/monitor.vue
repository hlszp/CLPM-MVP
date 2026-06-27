<script lang="ts" setup>
import type { TableColumnsType, TablePaginationConfig } from 'ant-design-vue';

import type { EchartsUIType } from '@vben/plugins/echarts';

/**
 * S2-LOOP-011 回路监控列表页
 *
 * 对齐 D06 §6 + IDS v3.2 §2.2.15 + UI/UX 改造方案 §8.3
 * - 左侧筛选区：装置/单元 + 类型 + 关键字 + 自动刷新开关
 * - 中部：回路列表 Table（点击行联动右侧摘要）
 * - 右侧选中回路区：摘要条 + 趋势预览小图 + KPI 摘要 + 风险标签 + 下一步动作
 * - 趋势 Modal：复用 WaveformChart 组件（与回路详情页风格统一）
 * - 性能 Modal：ECharts 仪表盘 + 6 大 KPI 卡片（含权重）
 * - 30 秒自动刷新（Switch 开关 + 倒计时）
 * - StatusFooter：最近刷新/数据延迟/自动刷新状态/选中回路
 */
import type { LoopApi } from '#/api/loop';
import type { PlantNodeApi } from '#/api/plant-node';

import { computed, nextTick, onMounted, onUnmounted, reactive, ref } from 'vue';
import { useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';
import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import {
  Alert,
  Button,
  Input,
  message,
  Modal,
  RadioGroup,
  Select,
  Spin,
  Switch,
  Table,
  Tag,
  Tooltip,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import {
  getLoopDetailApi,
  getLoopMonitorDetailApi,
  getLoopMonitorListApi,
} from '#/api/loop';
import {
  ClpmDataCanvas,
  ClpmKpiStrip,
  ClpmObjectSummaryBar,
  ClpmPageToolbar,
  ClpmToolbarButton,
  type KpiStripItem,
  type SummaryAction,
  type SummaryItem,
} from '#/components/clpm';
import WaveformChart from '#/components/loop/waveform-chart.vue';
import { getPlantNodeTreeApi } from '#/api/plant-node';
import { flattenNodes } from '#/utils/plant-node';

defineOptions({ name: 'LoopMonitor' });

const router = useRouter();

// ===== 常量 =====

/** 回路类型映射（label + color） */
const LOOP_TYPE_MAP: Record<string, { color: string; label: string }> = {
  TEMPERATURE: { label: '温度', color: 'red' },
  PRESSURE: { label: '压力', color: 'blue' },
  LEVEL: { label: '液位', color: 'green' },
  FLOW: { label: '流量', color: 'cyan' },
  ANALYSIS: { label: '分析', color: 'purple' },
  SPEED: { label: '速度', color: 'orange' },
  OTHER: { label: '其他', color: 'default' },
};

const loopTypeOptions = [
  { label: '全部', value: undefined },
  ...Object.entries(LOOP_TYPE_MAP).map(([value, { label }]) => ({
    label,
    value,
  })),
];

/** 趋势时间窗选项 */
const trendWindowOptions: { label: string; value: LoopApi.TrendWindow }[] = [
  { label: '1h', value: 'last_1_hour' },
  { label: '2h', value: 'last_2_hours' },
  { label: '4h', value: 'last_4_hours' },
  { label: '8h', value: 'last_8_hours' },
  { label: '24h', value: 'last_24_hours' },
  { label: '72h', value: 'last_72_hours' },
];

/** KPI 状态映射 */
const kpiStatusMap: Record<string, { color: string; label: string }> = {
  SUCCESS: { color: 'green', label: '良好' },
  INCONCLUSIVE: { color: 'default', label: '未确定' },
  PARTIAL: { color: 'orange', label: '部分' },
};

/** 性能 Modal 中 KPI 结果是否为 INCONCLUSIVE */
const isPerfInconclusive = computed(
  () => perfDetail.value?.kpiSummary.status === 'INCONCLUSIVE',
);

/** 6 大 KPI 配置（含权重 key） */
const kpiItems: {
  desc: string;
  key: keyof LoopApi.KpiSummary;
  label: string;
  unit: string;
  weightKey?: keyof LoopApi.ScoreWeights;
}[] = [
  {
    desc: '自动模式率',
    key: 'auto_mode_rate',
    label: '自控率',
    unit: '%',
    weightKey: 'auto_mode_rate',
  },
  {
    desc: '有效自控率',
    key: 'effective_auto_rate',
    label: '有效自控率',
    unit: '%',
  },
  {
    desc: '稳定率',
    key: 'steady_rate',
    label: '平稳率',
    unit: '%',
    weightKey: 'steady_rate',
  },
  {
    desc: '准确度',
    key: 'accuracy_rate',
    label: '准确率',
    unit: '%',
    weightKey: 'accuracy_rate',
  },
  {
    desc: '快速率',
    key: 'fast_response_rate',
    label: '快速率',
    unit: '%',
    weightKey: 'fast_response_rate',
  },
  {
    desc: '振荡率',
    key: 'oscillation_rate',
    label: '振荡率',
    unit: '%',
    weightKey: 'oscillation_rate',
  },
  {
    desc: '饱和率',
    key: 'saturation_rate',
    label: '饱和率',
    unit: '%',
    weightKey: 'saturation_rate',
  },
  {
    desc: '优良值率',
    key: 'good_value_rate',
    label: '好值率',
    unit: '%',
  },
];

// ===== 列表状态 =====

const loading = ref(false);
const monitorList = ref<LoopApi.MonitorListItem[]>([]);
const total = ref(0);
const query = reactive({
  plantNodeId: undefined as string | undefined,
  loopType: undefined as string | undefined,
  keyword: '',
  page: 1,
  pageSize: 100,
});

const plantNodes = ref<PlantNodeApi.PlantNode[]>([]);

/** 工厂节点层级选项（显示完整路径：工厂A / 装置B / 单元C） */
const plantNodeOptions = computed(() => {
  const nodeMap = new Map<string, PlantNodeApi.PlantNode>();
  for (const node of plantNodes.value) {
    nodeMap.set(node.id, node);
  }
  return plantNodes.value.map((node) => {
    const path: string[] = [];
    let current: PlantNodeApi.PlantNode | undefined = node;
    while (current) {
      path.unshift(current.name);
      current = current.parentId ? nodeMap.get(current.parentId) : undefined;
    }
    return {
      label: path.join(' / '),
      value: node.id,
    };
  });
});

const columns: TableColumnsType = [
  { title: '回路编号', dataIndex: 'tagName', key: 'tagName', width: 160 },
  {
    title: '名称',
    dataIndex: 'description',
    key: 'description',
    ellipsis: true,
  },
  { title: '类型', dataIndex: 'loopType', key: 'loopType', width: 100 },
  { title: '设定值 SP', key: 'sp', width: 120 },
  { title: '测量值 PV', key: 'pv', width: 120 },
  { title: '输出值 OP', key: 'op', width: 120 },
  { title: '控制方式', key: 'mode', width: 110 },
  { title: '性能指数', dataIndex: 'score', key: 'score', width: 100 },
  { title: '操作', key: 'action', width: 200, fixed: 'right' },
];

// ===== 自动刷新 =====

const autoRefresh = ref(true);
const refreshInterval = 30; // seconds
const countdown = ref(refreshInterval);
let refreshTimer: null | ReturnType<typeof setInterval> = null;
let countdownTimer: null | ReturnType<typeof setInterval> = null;

// ===== 趋势 Modal =====

const trendModalVisible = ref(false);
const trendLoading = ref(false);
const trendDetail = ref<LoopApi.MonitorDetail | null>(null);
const trendWindow = ref<LoopApi.TrendWindow>('last_4_hours');
const waveformChartRef = ref<InstanceType<typeof WaveformChart>>();
const trendFullscreen = ref(false);

const trendModalWidth = computed(() =>
  trendFullscreen.value ? '100vw' : '1100px',
);
const trendChartHeight = computed(() =>
  trendFullscreen.value ? 'calc(100vh - 220px)' : '400px',
);
const trendBodyStyle = computed(() =>
  trendFullscreen.value
    ? { height: 'calc(100vh - 55px)', overflow: 'auto', padding: '16px' }
    : { maxHeight: 'calc(100vh - 120px)', overflow: 'auto' },
);

function toggleTrendFullscreen() {
  trendFullscreen.value = !trendFullscreen.value;
  nextTick(() => {
    setTimeout(() => waveformChartRef.value?.resize(), 100);
  });
}

// ===== 性能 Modal =====

const perfModalVisible = ref(false);
const perfLoading = ref(false);
const perfDetail = ref<LoopApi.MonitorDetail | null>(null);
const perfWindow = ref<LoopApi.TrendWindow>('last_24_hours');
const loopDetailForWeights = ref<LoopApi.LoopDetail | null>(null);
const gaugeChartRef = ref<EchartsUIType>();
const { renderEcharts: renderGaugeChart } = useEcharts(gaugeChartRef);

// ===== 当前操作的回路 =====

const currentRecord = ref<LoopApi.MonitorListItem | null>(null);
const selectedLoop = ref<LoopApi.MonitorListItem | null>(null);

// ===== 选中回路趋势预览（右侧小图） =====
const previewTrend = ref<LoopApi.MonitorDetail | null>(null);
const previewLoading = ref(false);
const previewWaveformRef = ref<InstanceType<typeof WaveformChart>>();

// ===== 状态反馈：最近刷新 + 数据延迟 =====
const lastRefreshAt = ref<Date | null>(null);
const lastRefreshText = computed(() => {
  if (!lastRefreshAt.value) return '';
  const diff = dayjs().diff(lastRefreshAt.value, 'second');
  if (diff < 60) return `${diff} 秒前`;
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  return dayjs(lastRefreshAt.value).format('HH:mm:ss');
});

const dataDelayText = computed(() => {
  const readAt = selectedLoop.value?.readAt;
  if (!readAt) return '';
  const diff = dayjs().diff(dayjs(readAt), 'minute');
  if (diff < 1) return '<1m';
  if (diff < 60) return `${diff}m`;
  return `${Math.floor(diff / 60)}h`;
});

const summaryItems = computed<SummaryItem[]>(() => {
  if (!selectedLoop.value) return [];
  return [
    {
      key: 'mode',
      label: '控制方式',
      value: modeText(selectedLoop.value),
      status: modeText(selectedLoop.value) === 'Auto' ? 'success' : 'warning',
    },
    {
      key: 'readAt',
      label: '最近读取',
      value: formatTime(selectedLoop.value.readAt),
      status: 'neutral',
    },
  ];
});

/** 主指标：性能指数 */
const primaryItem = computed<SummaryItem | null>(() => {
  if (!selectedLoop.value) return null;
  return {
    key: 'score',
    label: '性能指数',
    value: selectedLoop.value.score?.toFixed(1) ?? '—',
    status:
      selectedLoop.value.score >= 80
        ? 'success'
        : selectedLoop.value.score >= 60
          ? 'warning'
          : 'danger',
  };
});

/** 风险标签：基于 KPI 状态与有效自控率推导 */
const riskTags = computed<{ color: string; key: string; label: string }[]>(() => {
  if (!selectedLoop.value?.kpiSummary) return [];
  const kpi = selectedLoop.value.kpiSummary;
  const tags: { color: string; key: string; label: string }[] = [];
  // KPI 状态标签
  if (kpi.status === 'INCONCLUSIVE') {
    tags.push({ key: 'inconclusive', label: '数据不足', color: 'default' });
  } else if (kpi.status === 'PARTIAL') {
    tags.push({ key: 'partial', label: '部分评估', color: 'orange' });
  }
  // 振荡风险
  if (kpi.oscillation_rate >= 30) {
    tags.push({ key: 'oscillation', label: `振荡风险 ${kpi.oscillation_rate.toFixed(0)}%`, color: 'red' });
  }
  // 饱和风险
  if (kpi.saturation_rate >= 30) {
    tags.push({ key: 'saturation', label: `OP 饱和 ${kpi.saturation_rate.toFixed(0)}%`, color: 'volcano' });
  }
  // 未投自动
  if ((kpi.auto_mode_rate ?? 0) < 50) {
    tags.push({ key: 'manual', label: '自动率低', color: 'gold' });
  }
  // 低效
  if ((selectedLoop.value.score ?? 100) < 60) {
    tags.push({ key: 'loweff', label: '低效回路', color: 'magenta' });
  }
  return tags;
});

/** 摘要条 actions（下一步动作，带图标） */
const summaryActions = computed<SummaryAction[]>(() => {
  if (!selectedLoop.value) return [];
  return [
    {
      key: 'detail',
      label: '查看详情',
      icon: 'ant-design:profile-outlined',
      type: 'default',
    },
    {
      key: 'diagnosis',
      label: '进入诊断',
      icon: 'ant-design:medicine-box-outlined',
      type: 'primary',
    },
    {
      key: 'tuning',
      label: '整定建议',
      icon: 'ant-design:tool-outlined',
      type: 'default',
    },
  ];
});

/** 摘要条动作分发 */
function onSummaryAction(key: string) {
  const loopId = selectedLoop.value?.loopId;
  if (!loopId) return;
  if (key === 'detail') {
    router.push(`/loop/detail/${loopId}`);
  } else if (key === 'diagnosis') {
    router.push(`/diagnosis/detail/${loopId}`);
  } else if (key === 'tuning') {
    router.push(`/tuning/workbench?loopId=${loopId}`);
  }
}

const monitorKpiItems = computed<KpiStripItem[]>(() => {
  if (!selectedLoop.value?.kpiSummary) return [];
  const summary = selectedLoop.value.kpiSummary;
  return [
    { key: 'good', label: '好值率', value: summary.good_value_rate?.toFixed(1) ?? '—', unit: '%', status: summary.good_value_rate >= 80 ? 'success' : summary.good_value_rate >= 60 ? 'warning' : 'danger' },
    { key: 'auto', label: '自控率', value: summary.auto_mode_rate?.toFixed(1) ?? '—', unit: '%', status: summary.auto_mode_rate >= 80 ? 'success' : summary.auto_mode_rate >= 60 ? 'warning' : 'danger' },
    { key: 'effective', label: '有效自控率', value: summary.effective_auto_rate?.toFixed(1) ?? '—', unit: '%', status: summary.effective_auto_rate >= 80 ? 'success' : summary.effective_auto_rate >= 60 ? 'warning' : 'danger' },
    { key: 'steady', label: '平稳率', value: summary.steady_rate?.toFixed(1) ?? '—', unit: '%', status: summary.steady_rate >= 80 ? 'success' : summary.steady_rate >= 60 ? 'warning' : 'danger' },
  ];
});

/** 选中回路：联动加载趋势预览 */
function handleSelectLoop(record: LoopApi.MonitorListItem) {
  selectedLoop.value = record;
  loadPreviewTrend(record.loopId);
}

/** 加载右侧趋势预览（小图，固定 1h 窗口） */
async function loadPreviewTrend(loopId: string) {
  previewLoading.value = true;
  try {
    previewTrend.value = await getLoopMonitorDetailApi(loopId, 'last_1_hour');
    await nextTick();
    previewWaveformRef.value?.resize();
  } catch {
    previewTrend.value = null;
  } finally {
    previewLoading.value = false;
  }
}

// ===== 工具函数 =====

/** MODE 颜色映射：Auto=绿 / Manual=橙 / Cascade=蓝 */
function modeColor(modeLabel: string): string {
  if (modeLabel === 'Auto') return 'green';
  if (modeLabel === 'Manual') return 'orange';
  if (modeLabel === 'Cascade') return 'blue';
  return 'default';
}

/** MODE 中文标签映射：0=Manual, 1=Auto, 2=Cascade */
function modeText(record: LoopApi.MonitorListItem): string {
  const label = record.currentValues?.modeLabel;
  if (label) return label;
  const mode = record.currentValues?.mode;
  if (mode === 0) return 'Manual';
  if (mode === 1) return 'Auto';
  if (mode === 2) return 'Cascade';
  return '—';
}

/** OP 值格式化，带 % 后缀 */
function formatOp(val: null | number | undefined): string {
  if (val === null || val === undefined || Number.isNaN(val)) return '—';
  return `${val.toFixed(2)}%`;
}

/** 数值 + 单位格式化 */
function formatValueWithUnit(
  val: null | number | undefined,
  unit?: string,
  digits = 2,
): string {
  if (val === null || val === undefined || Number.isNaN(val)) return '—';
  const formatted = val.toFixed(digits);
  return unit ? `${formatted} ${unit}` : formatted;
}

function formatTime(t: null | string | undefined): string {
  if (!t) return '—';
  try {
    return new Date(t).toLocaleString('zh-CN');
  } catch {
    return t;
  }
}

// ===== 数据加载 =====

/** 加载工厂节点 */
async function loadPlantNodes() {
  try {
    const tree = await getPlantNodeTreeApi();
    plantNodes.value = flattenNodes(tree);
  } catch {
    // 错误已由拦截器处理
  }
}

/** 加载监控列表 */
async function loadList() {
  loading.value = true;
  try {
    const data = await getLoopMonitorListApi({
      plantNodeId: query.plantNodeId,
      loopType: query.loopType as LoopApi.LoopType | undefined,
      keyword: query.keyword || undefined,
      page: query.page,
      pageSize: query.pageSize,
    });
    monitorList.value = data.items;
    total.value = data.total;
  } catch {
    // 错误已由拦截器处理
  } finally {
    loading.value = false;
    lastRefreshAt.value = new Date();
  }
}

/** 导出（占位，待后端接口） */
function handleExport() {
  message.info(`导出 ${total.value} 条回路监控数据，待后端接口支持`);
}

function handleSearch() {
  query.page = 1;
  loadList();
}

function handleTableChange(pagination: TablePaginationConfig) {
  query.page = pagination.current || 1;
  query.pageSize = pagination.pageSize || 100;
  loadList();
}

// ===== 趋势 Modal =====

/** 打开趋势 Modal */
async function openTrend(record: LoopApi.MonitorListItem) {
  currentRecord.value = record;
  trendModalVisible.value = true;
  trendWindow.value = 'last_4_hours';
  trendDetail.value = null;
  await loadTrendDetail();
}

/** 加载趋势详情 */
async function loadTrendDetail() {
  if (!currentRecord.value) return;
  trendLoading.value = true;
  try {
    trendDetail.value = await getLoopMonitorDetailApi(
      currentRecord.value.loopId,
      trendWindow.value,
    );
    // WaveformChart 组件内置 watch(trend) 自动渲染，只需在 DOM 更新后触发 resize 修正尺寸
    await nextTick();
    waveformChartRef.value?.resize();
  } catch {
    // 错误已由拦截器处理
  } finally {
    trendLoading.value = false;
  }
}

function handleTrendWindowChange() {
  loadTrendDetail();
}

// ===== 性能 Modal =====

/** 打开性能 Modal */
async function openPerformance(record: LoopApi.MonitorListItem) {
  currentRecord.value = record;
  perfModalVisible.value = true;
  perfWindow.value = 'last_24_hours';
  perfDetail.value = null;
  loopDetailForWeights.value = null;
  await loadPerfDetail();
}

/** 加载性能详情 */
async function loadPerfDetail() {
  if (!currentRecord.value) return;
  perfLoading.value = true;
  try {
    const [detail, loopDetail] = await Promise.all([
      getLoopMonitorDetailApi(currentRecord.value.loopId, perfWindow.value),
      getLoopDetailApi(currentRecord.value.loopId),
    ]);
    perfDetail.value = detail;
    loopDetailForWeights.value = loopDetail;
    await nextTick();
    renderGauge();
  } catch {
    // 错误已由拦截器处理
  } finally {
    perfLoading.value = false;
  }
}

/** 渲染仪表盘 */
function renderGauge() {
  const score = perfDetail.value?.kpiSummary.composite_score;
  if (score === null || score === undefined) return;

  renderGaugeChart({
    series: [
      {
        axisLine: {
          lineStyle: {
            color: [
              [0.6, '#ff4d4f'],
              [0.8, '#faad14'],
              [1, '#52c41a'],
            ],
            width: 18,
          },
        },
        axisTick: { show: false },
        data: [{ name: '综合性能指数', value: score }],
        detail: {
          fontSize: 28,
          formatter: '{value}',
          offsetCenter: [0, '50%'],
        },
        max: 100,
        min: 0,
        pointer: { itemStyle: { color: 'auto' } },
        progress: { show: true, width: 18 },
        splitLine: { length: 18 },
        title: { fontSize: 14, offsetCenter: [0, '80%'] },
        type: 'gauge',
      },
    ],
  });
}

function handlePerfWindowChange() {
  loadPerfDetail();
}

// ===== 详情跳转 =====

function viewDetail(record: LoopApi.MonitorListItem) {
  router.push(`/loop/detail/${record.loopId}`);
}

// ===== 自动刷新 =====

function startAutoRefresh() {
  stopAutoRefresh();
  if (autoRefresh.value) {
    countdown.value = refreshInterval;
    refreshTimer = setInterval(() => {
      loadList();
      countdown.value = refreshInterval;
    }, refreshInterval * 1000);
    countdownTimer = setInterval(() => {
      if (countdown.value > 0) countdown.value -= 1;
    }, 1000);
  }
}

function stopAutoRefresh() {
  if (refreshTimer) {
    clearInterval(refreshTimer);
    refreshTimer = null;
  }
  if (countdownTimer) {
    clearInterval(countdownTimer);
    countdownTimer = null;
  }
}

function handleToggleAutoRefresh(val: any) {
  autoRefresh.value = !!val;
  if (autoRefresh.value) {
    startAutoRefresh();
  } else {
    stopAutoRefresh();
  }
}

// ===== 生命周期 =====

onMounted(() => {
  loadPlantNodes();
  loadList();
  startAutoRefresh();
});

onUnmounted(() => {
  stopAutoRefresh();
});
</script>

<template>
  <Page title="回路监控">
    <div class="flex min-h-[calc(100vh-160px)] gap-3">
      <ClpmDataCanvas class="w-[320px] flex-shrink-0" title="筛选与刷新">
        <ClpmPageToolbar title="回路监控" subtitle="列表 + 摘要 + 趋势主画布" compact>
          <Select
            v-model:value="query.plantNodeId"
            placeholder="按装置/单元筛选"
            style="width: 260px"
            allow-clear
            show-search
            :options="plantNodeOptions"
            :filter-option="(input: string, option: any) => option.label.includes(input)"
            @change="handleSearch"
          />
          <Select
            v-model:value="query.loopType"
            placeholder="按回路类型筛选"
            style="width: 160px"
            allow-clear
            :options="loopTypeOptions"
            @change="handleSearch"
          />
          <Input
            v-model:value="query.keyword"
            placeholder="搜索位号/描述"
            allow-clear
            style="width: 220px"
            @press-enter="handleSearch"
          />
          <template #actions>
            <ClpmToolbarButton icon="search" label="查询" @click="handleSearch" />
            <ClpmToolbarButton icon="refresh" label="刷新" :loading="loading" @click="loadList" />
            <ClpmToolbarButton icon="export" label="导出" @click="handleExport" />
          </template>
        </ClpmPageToolbar>
        <div class="mt-3 flex items-center gap-2 text-sm text-gray-500">
          <span>自动刷新（{{ refreshInterval }}s）</span>
          <Switch :checked="autoRefresh" @change="handleToggleAutoRefresh" />
          <span v-if="autoRefresh" class="text-xs text-gray-400">{{ countdown }}s 后刷新</span>
        </div>
      </ClpmDataCanvas>

      <div class="flex min-w-0 flex-1 flex-col gap-3">
        <div class="flex min-h-0 flex-1 gap-3">
          <ClpmDataCanvas class="min-w-0 flex-1" title="回路列表" :loading="loading">
            <Table
              :columns="columns"
              :data-source="monitorList"
              :loading="loading"
              :pagination="{
                current: query.page,
                pageSize: query.pageSize,
                total,
                showSizeChanger: true,
                pageSizeOptions: ['20', '50', '100'],
                showTotal: (t: number) => `共 ${t} 条`,
              }"
              :row-key="(record: LoopApi.MonitorListItem) => record.loopId"
              :scroll="{ x: 1200 }"
              size="middle"
              :row-class-name="(record) => selectedLoop?.loopId === record.loopId ? 'ant-table-row-selected cursor-pointer' : 'cursor-pointer'"
              :custom-row="(record) => ({ onClick: () => handleSelectLoop(record as LoopApi.MonitorListItem) })"
              @change="handleTableChange"
            >
              <template #bodyCell="{ column, record }">
                <template v-if="column.key === 'loopType'">
                  <Tag
                    :color="
                      LOOP_TYPE_MAP[
                        (record as LoopApi.MonitorListItem).loopType ?? 'OTHER'
                      ]?.color ?? 'default'
                    "
                    class="m-0"
                  >
                    {{
                      LOOP_TYPE_MAP[
                        (record as LoopApi.MonitorListItem).loopType ?? 'OTHER'
                      ]?.label ?? '其他'
                    }}
                  </Tag>
                </template>
                <template v-else-if="column.key === 'sp'">
                  {{
                    formatValueWithUnit(
                      (record as LoopApi.MonitorListItem).currentValues?.sp,
                      (record as LoopApi.MonitorListItem).currentValues?.unit,
                    )
                  }}
                </template>
                <template v-else-if="column.key === 'pv'">
                  <span class="font-medium text-blue-600">
                    {{
                      formatValueWithUnit(
                        (record as LoopApi.MonitorListItem).currentValues?.pv,
                        (record as LoopApi.MonitorListItem).currentValues?.unit,
                      )
                    }}
                  </span>
                </template>
                <template v-else-if="column.key === 'op'">
                  {{ formatOp((record as LoopApi.MonitorListItem).currentValues?.op) }}
                </template>
                <template v-else-if="column.key === 'mode'">
                  <Tag
                    v-if="
                      (record as LoopApi.MonitorListItem).currentValues?.modeLabel ||
                      (record as LoopApi.MonitorListItem).currentValues?.mode != null
                    "
                    :color="modeColor((record as LoopApi.MonitorListItem).currentValues?.modeLabel)"
                  >
                    {{ modeText(record as LoopApi.MonitorListItem) }}
                  </Tag>
                  <span v-else class="text-gray-400">—</span>
                </template>
                <template v-else-if="column.key === 'score'">
                  <span
                    v-if="(record as LoopApi.MonitorListItem).score != null"
                    class="font-medium"
                  >
                    {{ (record as LoopApi.MonitorListItem).score?.toFixed(1) ?? '—' }}
                  </span>
                  <span v-else class="text-gray-400">—</span>
                </template>
                <template v-else-if="column.key === 'action'">
                  <div class="flex gap-1">
                    <Button
                      type="link"
                      size="small"
                      @click="openTrend(record as LoopApi.MonitorListItem)"
                    >
                      趋势
                    </Button>
                    <Button
                      type="link"
                      size="small"
                      @click="openPerformance(record as LoopApi.MonitorListItem)"
                    >
                      性能
                    </Button>
                    <Button
                      type="link"
                      size="small"
                      @click="viewDetail(record as LoopApi.MonitorListItem)"
                    >
                      详情
                    </Button>
                  </div>
                </template>
              </template>
            </Table>
          </ClpmDataCanvas>

          <ClpmDataCanvas
            class="w-[440px] min-w-0"
            title="选中回路摘要"
            :empty="!selectedLoop"
            empty-text="点击左侧回路查看摘要"
          >
            <template v-if="selectedLoop">
              <ClpmObjectSummaryBar
                :title="selectedLoop.tagName"
                :subtitle="`${selectedLoop.description} · ${selectedLoop.unitName}`"
                :items="summaryItems"
                :primary-item="primaryItem"
                :actions="summaryActions"
                @action="onSummaryAction"
              />

              <!-- 风险标签区 -->
              <div v-if="riskTags.length" class="mt-3 flex flex-wrap gap-1">
                <span class="text-xs text-gray-500">风险标签：</span>
                <Tooltip
                  v-for="tag in riskTags"
                  :key="tag.key"
                  :title="`基于 KPI 实时推导：${tag.label}`"
                >
                  <Tag :color="tag.color" class="m-0">{{ tag.label }}</Tag>
                </Tooltip>
              </div>
              <div v-else class="mt-3 flex items-center gap-1">
                <span class="text-xs text-gray-500">风险标签：</span>
                <Tag color="green" class="m-0">运行正常</Tag>
              </div>

              <!-- 趋势预览小图（1h 窗口） -->
              <div class="mt-3">
                <div class="mb-1 flex items-center justify-between">
                  <span class="text-xs text-gray-500">趋势预览（近 1h）</span>
                  <Button
                    type="link"
                    size="small"
                    class="!px-0"
                    @click="openTrend(selectedLoop!)"
                  >
                    展开大图
                  </Button>
                </div>
                <Spin :spinning="previewLoading" size="small">
                  <WaveformChart
                    v-if="previewTrend"
                    ref="previewWaveformRef"
                    :trend="previewTrend.trend"
                    height="160px"
                  />
                  <div
                    v-else
                    class="flex h-[160px] items-center justify-center text-xs text-gray-400"
                  >
                    暂无趋势数据
                  </div>
                </Spin>
              </div>

              <div v-if="monitorKpiItems.length" class="mt-3">
                <ClpmKpiStrip :items="monitorKpiItems" />
              </div>
            </template>
          </ClpmDataCanvas>
        </div>

        <!-- StatusFooter：最近刷新/数据延迟/自动刷新状态/选中回路 -->
        <div class="clpm-status-footer">
          <span>最近刷新：{{ lastRefreshText || '尚未刷新' }}</span>
          <span class="clpm-status-footer__divider">·</span>
          <span>数据延迟：{{ dataDelayText || '—' }}</span>
          <span class="clpm-status-footer__divider">·</span>
          <span>
            自动刷新：
            <strong :class="autoRefresh ? 'is-active' : 'is-muted'">
              {{ autoRefresh ? `开启（${countdown}s）` : '关闭' }}
            </strong>
          </span>
          <span class="clpm-status-footer__divider">·</span>
          <span>选中回路：{{ selectedLoop?.tagName ?? '—' }}</span>
        </div>
      </div>
    </div>

    <!-- 趋势 Modal -->
    <Modal
      v-model:open="trendModalVisible"
      :width="trendModalWidth"
      :body-style="trendBodyStyle"
      :footer="null"
      destroy-on-close
      :style="trendFullscreen ? { top: 0, paddingBottom: 0 } : {}"
      @cancel="trendFullscreen = false"
    >
      <template #title>
        <div class="flex items-center justify-between pr-8">
          <span>趋势 - {{ currentRecord?.tagName ?? '' }}</span>
          <Button type="text" size="small" @click="toggleTrendFullscreen">
            {{ trendFullscreen ? '退出全屏' : '全屏' }}
          </Button>
        </div>
      </template>
      <Spin :spinning="trendLoading">
        <div v-if="currentRecord" class="space-y-3">
          <!-- 时间范围 + 当前 MODE -->
          <div class="flex flex-wrap items-center justify-between gap-3">
            <div class="flex items-center gap-2">
              <span class="text-sm text-gray-500">时间范围：</span>
              <RadioGroup
                v-model:value="trendWindow"
                :options="trendWindowOptions"
                option-type="button"
                button-style="solid"
                size="small"
                @change="handleTrendWindowChange"
              />
            </div>
            <div class="flex items-center gap-2">
              <span class="text-sm text-gray-500">当前控制方式：</span>
              <Tag
                v-if="trendDetail?.currentValues?.modeLabel"
                :color="modeColor(trendDetail.currentValues.modeLabel)"
              >
                {{ trendDetail.currentValues.modeLabel }}
              </Tag>
              <span v-else class="text-gray-400">—</span>
            </div>
          </div>

          <!-- 当前值快照 -->
          <div
            v-if="trendDetail"
            class="flex flex-wrap items-center gap-4 rounded border p-3"
          >
            <div>
              <span class="text-xs text-gray-400">PV</span>
              <span class="ml-2 font-medium text-blue-600">
                {{
                  formatValueWithUnit(
                    trendDetail.currentValues.pv,
                    trendDetail.currentValues.unit,
                  )
                }}
              </span>
            </div>
            <div>
              <span class="text-xs text-gray-400">SP</span>
              <span class="ml-2 font-medium">
                {{
                  formatValueWithUnit(
                    trendDetail.currentValues.sp,
                    trendDetail.currentValues.unit,
                  )
                }}
              </span>
            </div>
            <div>
              <span class="text-xs text-gray-400">OP</span>
              <span class="ml-2 font-medium">
                {{ formatOp(trendDetail.currentValues.op) }}
              </span>
            </div>
            <div>
              <span class="text-xs text-gray-400">读取时间</span>
              <span class="ml-2 text-sm">
                {{ formatTime(trendDetail.currentValues.readAt) }}
              </span>
            </div>
          </div>

          <!-- 趋势图（复用 WaveformChart 组件，与回路详情页风格统一） -->
          <div v-if="trendDetail">
            <WaveformChart
              ref="waveformChartRef"
              :trend="trendDetail.trend"
              :height="trendChartHeight"
            />
          </div>
          <div v-else class="py-12 text-center text-gray-400">暂无趋势数据</div>
        </div>
      </Spin>
    </Modal>

    <!-- 性能 Modal -->
    <Modal
      v-model:open="perfModalVisible"
      :title="`性能 - ${currentRecord?.tagName ?? ''}`"
      width="900px"
      :footer="null"
      destroy-on-close
    >
      <Spin :spinning="perfLoading">
        <div v-if="perfDetail" class="space-y-4">
          <!-- 时间范围 -->
          <div class="flex items-center gap-2">
            <span class="text-sm text-gray-500">时间范围：</span>
            <RadioGroup
              v-model:value="perfWindow"
              :options="trendWindowOptions"
              option-type="button"
              button-style="solid"
              size="small"
              @change="handlePerfWindowChange"
            />
          </div>

          <!-- INCONCLUSIVE 警告 -->
          <Alert
            v-if="isPerfInconclusive"
            class="mb-4"
            type="warning"
            show-icon
            message="该回路本期评估数据不足，结果不确定"
            description="有效数据率低于 20%，KPI 数值仅供参考，不参与评级与排行。"
          />

          <!-- 综合评分 + 仪表盘 -->
          <div
            class="flex items-center gap-6 rounded border p-4"
            :class="{ 'opacity-60': isPerfInconclusive }"
          >
            <div style="width: 240px; height: 240px">
              <EchartsUI
                v-if="perfDetail.kpiSummary.composite_score != null"
                ref="gaugeChartRef"
                height="240px"
              />
              <div
                v-else
                class="flex h-full items-center justify-center text-gray-400"
              >
                暂无评分
              </div>
            </div>
            <div class="flex-1">
              <div class="text-sm text-gray-500">
                综合性能指数（composite_score）
              </div>
              <div
                class="mt-1 text-3xl font-bold"
                :class="
                  isPerfInconclusive
                    ? 'text-gray-400'
                    : {
                        'text-green-600':
                          (perfDetail.kpiSummary.composite_score ?? 0) >= 80,
                        'text-orange-500':
                          (perfDetail.kpiSummary.composite_score ?? 0) >= 60 &&
                          (perfDetail.kpiSummary.composite_score ?? 0) < 80,
                        'text-red-500':
                          (perfDetail.kpiSummary.composite_score ?? 0) < 60,
                      }
                "
              >
                {{ perfDetail.kpiSummary.composite_score?.toFixed(1) ?? '—' }}
              </div>
              <div class="mt-2 flex items-center gap-2">
                <span class="text-xs text-gray-400">KPI 状态：</span>
                <Tag :color="kpiStatusMap[perfDetail.kpiSummary.status]?.color">
                  {{
                    kpiStatusMap[perfDetail.kpiSummary.status]?.label ||
                    perfDetail.kpiSummary.status
                  }}
                </Tag>
              </div>
              <div class="mt-1 text-xs text-gray-400">
                算法版本：{{ perfDetail.kpiSummary.algorithm_version }}
              </div>
              <div class="text-xs text-gray-400">
                计算时间：{{ formatTime(perfDetail.kpiSummary.calculatedAt) }}
              </div>
            </div>
          </div>

          <!-- 6 大 KPI 卡片（含权重） -->
          <div
            class="grid grid-cols-2 gap-3 md:grid-cols-3"
            :class="{ 'opacity-60': isPerfInconclusive }"
          >
            <div
              v-for="item in kpiItems"
              :key="item.key"
              class="rounded border p-3"
            >
              <div class="flex items-center justify-between">
                <span class="text-sm font-medium">{{ item.label }}</span>
                <span class="text-xs text-gray-400">
                  权重：{{
                    item.weightKey
                      ? (loopDetailForWeights?.basicInfo.scoreWeights?.[
                          item.weightKey
                        ] ?? '—')
                      : '—'
                  }}%
                </span>
              </div>
              <div class="mt-1 text-xl font-medium">
                {{
                  (perfDetail.kpiSummary[item.key] as null | number)?.toFixed(
                    1,
                  ) ?? '—'
                }}{{ item.unit }}
              </div>
              <div class="mt-1 text-xs text-gray-400">{{ item.desc }}</div>
            </div>
          </div>
        </div>
        <div v-else class="py-12 text-center text-gray-400">暂无性能数据</div>
      </Spin>
    </Modal>
  </Page>
</template>

<style scoped>
.clpm-status-footer {
  align-items: center;
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border));
  border-radius: calc(var(--radius) * 1px);
  color: hsl(var(--muted-foreground));
  display: flex;
  flex-wrap: wrap;
  font-size: 12px;
  gap: 8px;
  padding: 8px 12px;
}

.clpm-status-footer__divider {
  color: hsl(var(--border));
}

.clpm-status-footer strong {
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace);
  font-feature-settings: 'tnum';
  font-variant-numeric: tabular-nums;
  font-weight: 700;
}

.clpm-status-footer strong.is-active {
  color: hsl(var(--primary));
}

.clpm-status-footer strong.is-muted {
  color: hsl(var(--muted-foreground));
}
</style>
