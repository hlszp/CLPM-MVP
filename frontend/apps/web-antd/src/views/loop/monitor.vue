<script lang="ts" setup>
import type { TableColumnsType, TablePaginationConfig } from 'ant-design-vue';

import type { EchartsUIType } from '@vben/plugins/echarts';

/**
 * S2-LOOP-011 回路监控列表页
 *
 * 对齐 D06 §6 + IDS v3.2 §2.2.15 + UI/UX 改造方案 §8.3
 * - 顶部工具栏：装置/单元 + 类型 + 关键字 + 自动刷新开关 + 查询/刷新/导出
 * - 主区：回路列表 Table（全宽，行内提供 趋势/性能/详情 三个操作入口）
 * - 详情展示交由详情页：避免与 /loop/detail/:id 内容重合
 * - 趋势 Modal：复用 WaveformChart 组件（与回路详情页风格统一）
 * - 性能 Modal：ECharts 仪表盘 + 6 大 KPI 卡片（含权重）
 * - 30 秒自动刷新（Switch 开关 + 倒计时）
 * - StatusFooter：最近刷新/数据延迟/自动刷新状态/选中回路
 */
import type { LoopApi } from '#/api/loop';
import type { PlantNodeApi } from '#/api/plant-node';
import type { ColumnConfig, FilterPreset } from '#/composables/use-clpm-preferences';

import {
  computed,
  nextTick,
  onMounted,
  onUnmounted,
  reactive,
  ref,
  watch,
} from 'vue';
import { useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';
import { EchartsUI, useEcharts } from '@vben/plugins/echarts';
import { useAccessStore } from '@vben/stores';

import {
  Alert,
  Button,
  Card,
  Input,
  message,
  Modal,
  Popover,
  RadioGroup,
  Select,
  Spin,
  Switch,
  Table,
  Tag,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import {
  getLoopDetailApi,
  getLoopMonitorDetailApi,
  getLoopMonitorListApi,
  getLoopTypeStatsApi,
} from '#/api/loop';
import { getPlantNodeTreeApi } from '#/api/plant-node';
import {
  ClpmColumnSettings,
  ClpmDataCanvas,
  ClpmNumeric,
  ClpmPageToolbar,
  ClpmToolbarButton,
} from '#/components/clpm';
import WaveformChart from '#/components/loop/waveform-chart.vue';
import { usePagePreference } from '#/composables/use-clpm-preferences';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import { flattenNodes } from '#/utils/plant-node';
import { realtimeWs } from '#/utils/realtime-ws';

defineOptions({ name: 'LoopMonitor' });

const { isDark, themeColors } = useClpmTheme();

const router = useRouter();

// ===== 用户偏好 =====
const {
  preferences,
  updateColumns,
  setDefaultTimeWindow,
  saveFilterPreset,
  deleteFilterPreset,
  reset: resetPreferences,
} = usePagePreference('loop-monitor');

// ===== 常量 =====

/** 回路类型映射（label + color）- 使用中性色调，避免混淆状态语义色 */
const LOOP_TYPE_MAP: Record<string, { color: string; label: string }> = {
  TEMPERATURE: { label: '温度', color: '#FCA5A5' },
  PRESSURE: { label: '压力', color: '#93C5FD' },
  LEVEL: { label: '液位', color: '#86EFAC' },
  FLOW: { label: '流量', color: '#67E8F9' },
  ANALYSIS: { label: '分析', color: '#D8B4FE' },
  SPEED: { label: '速度', color: '#FDBA74' },
  OTHER: { label: '其他', color: '#CBD5E1' },
};

const loopTypeOptions = [
  { label: '全部', value: '' },
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

/** KPI 状态映射 - 使用 ZL 语义色 */
const kpiStatusMap: Record<string, { color: string; label: string }> = {
  SUCCESS: { color: '#10B981', label: '良好' },
  INCONCLUSIVE: { color: '#CBD5E1', label: '未确定' },
  PARTIAL: { color: '#F59E0B', label: '部分' },
};

/** 性能 Modal 中 KPI 结果是否为 INCONCLUSIVE */
const isPerfInconclusive = computed(
  () => perfDetail.value?.kpiSummary.status === 'INCONCLUSIVE',
);

/** P3 #53: 回路状态（READY/PARTIAL/INACTIVE）—— 用于区分 KPI 缺失原因 */
const loopStatus = computed(() => perfDetail.value?.status);

/** P3 #53: 回路状态非 READY（PARTIAL/INACTIVE），未参与 KPI 计算 */
const isLoopNotReady = computed(() => {
  const s = loopStatus.value;
  return s === 'PARTIAL' || s === 'INACTIVE';
});

/** P3 #53: 仅在回路 READY 且 KPI 状态 INCONCLUSIVE 时显示「数据不足」警告 */
const showDataInsufficientAlert = computed(
  () => !isLoopNotReady.value && isPerfInconclusive.value,
);

/** 8 大 KPI 配置（含权重 key） */
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
    key: 'fast_rate',
    label: '快速率',
    unit: '%',
    weightKey: 'fast_rate',
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

/** 按回路类型统计数量（后端 API 获取，支持递归子节点） */
const loopTypeStats = ref<Record<string, number>>({
  TEMPERATURE: 0,
  PRESSURE: 0,
  LEVEL: 0,
  FLOW: 0,
  ANALYSIS: 0,
  SPEED: 0,
  OTHER: 0,
});

/** 按控制方式统计数量（从监控列表实时数据中统计） */
const controlModeStats = ref<Record<string, number>>({});

/** 控制方式颜色映射 */
const CONTROL_MODE_COLOR_MAP: Record<string, string> = {
  Auto: '#10b981',
  Manual: '#ef4444',
  Cascade: '#3b82f6',
  Unknown: '#6b7280',
};

/** 回路类型颜色映射 */
const LOOP_TYPE_COLOR_MAP: Record<string, string> = {
  TEMPERATURE: '#ef4444',
  PRESSURE: '#3b82f6',
  LEVEL: '#10b981',
  FLOW: '#06b6d4',
  ANALYSIS: '#8b5cf6',
  SPEED: '#f59e0b',
  OTHER: '#6b7280',
};

/** 控制方式柱状图 */
const modeChartRef = ref<EchartsUIType>();
const { renderEcharts: renderModeChart } = useEcharts(modeChartRef);

/** 更新控制方式柱状图 */
function updateModeChart() {
  const stats = controlModeStats.value;
  const keys = Object.keys(stats);
  const labels = keys;
  const data = keys.map((k) => stats[k]);
  const colors = keys.map((k) => CONTROL_MODE_COLOR_MAP[k] || '#6b7280');

  renderModeChart({
    animation: false,
    grid: {
      bottom: 0,
      containLabel: true,
      left: '1%',
      right: '1%',
      top: '5%',
    },
    series: [
      {
        barMaxWidth: 40,
        data: data.map((v, i) => ({ value: v, itemStyle: { color: colors[i] } })),
        type: 'bar',
      },
    ],
    tooltip: {
      axisPointer: { lineStyle: { width: 1 } },
      trigger: 'axis',
    },
    xAxis: {
      data: labels,
      type: 'category',
      axisLabel: { fontSize: 11 },
    },
    yAxis: {
      type: 'value',
      axisLabel: { fontSize: 11 },
      splitLine: { show: false },
    },
  });
}

/** 自控率（自动控制回路数 / 总回路数） */
const realtimeControlRate = computed(() => {
  const autoCount = controlModeStats.value['Auto'] || 0;
  const total = Object.values(controlModeStats.value).reduce((sum, count) => sum + count, 0);
  return total > 0 ? Math.round((autoCount / total) * 100) : 0;
});

/** 加载回路类型统计 */
async function loadLoopTypeStats() {
  try {
    const data = await getLoopTypeStatsApi(query.plantNodeId);
    loopTypeStats.value = (data as any).loopTypeStats || (data as Record<string, number>);
    controlModeStats.value = (data as any).controlModeStats || {};
    updateModeChart();
  } catch {
    // 错误已由拦截器处理
  }
}

/** 点击统计卡片自动筛选 */
function handleTypeCardClick(type: string) {
  if (type === 'ALL') {
    query.loopType = '';
  } else {
    query.loopType = query.loopType === type ? '' : type;
  }
  query.page = 1;
  loadList();
}

const query = reactive({
  plantNodeId: undefined as string | undefined,
  loopType: '' as string | undefined,
  keyword: '',
  page: 1,
  pageSize: 20,
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
  { title: '回路编号', dataIndex: 'tagName', key: 'tagName', width: 160, align: 'center' },
  {
    title: '名称',
    dataIndex: 'description',
    key: 'description',
    ellipsis: true,
    align: 'left',
  },
  { title: '所属单元', dataIndex: 'unitName', key: 'unitName', width: 120, align: 'center' },
  // v6.1 新增：测量量程 / 单位
  { title: '测量量程', key: 'pvRange', width: 130, align: 'center' },
  { title: '单位', key: 'pvUnit', width: 70, align: 'center' },
  { title: '类型', dataIndex: 'loopType', key: 'loopType', width: 100, align: 'center' },
  {
    title: '设定值 SP',
    key: 'sp',
    width: 120,
    align: 'right',
    customRender: ({ record }) => {
      const val = (record as LoopApi.MonitorListItem).currentValues?.sp;
      return val !== null && val !== undefined ? val.toFixed(2) : '-';
    },
    customCell: () => ({ style: { 'text-align': 'right' } }),
  },
  {
    title: '测量值 PV',
    key: 'pv',
    width: 120,
    align: 'right',
    customRender: ({ record }) => {
      const val = (record as LoopApi.MonitorListItem).currentValues?.pv;
      return val !== null && val !== undefined ? val.toFixed(2) : '-';
    },
    customCell: () => ({ style: { 'text-align': 'right' } }),
  },
  {
    title: '输出值 OP',
    key: 'op',
    width: 120,
    align: 'right',
    customRender: ({ record }) => {
      const val = (record as LoopApi.MonitorListItem).currentValues?.op;
      return val !== null && val !== undefined ? val.toFixed(2) : '-';
    },
    customCell: () => ({ style: { 'text-align': 'right' } }),
  },
  { title: '控制方式', key: 'mode', width: 110, align: 'center' },
  {
    title: '性能指数',
    dataIndex: 'score',
    key: 'score',
    width: 100,
    align: 'right',
    customRender: ({ text }) => {
      return text !== null && text !== undefined ? Number(text).toFixed(2) : '-';
    },
    customCell: () => ({ style: { 'text-align': 'right' } }),
  },
  { title: '操作', key: 'action', width: 160, fixed: 'right', align: 'center' },
];

/** 提取列 key 为字符串 */
function getColumnKey(col: any): string {
  if (col.key) return String(col.key);
  if (col.dataIndex) {
    return Array.isArray(col.dataIndex)
      ? String(col.dataIndex[0])
      : String(col.dataIndex);
  }
  return '';
}

/** 默认列配置 */
function buildDefaultColumnConfigs(): ColumnConfig[] {
  return columns.map((c: any, i: number) => ({
    key: getColumnKey(c),
    label: String(c.title ?? ''),
    visible: true,
    order: i,
  }));
}

/** 表格列配置（从偏好恢复或使用默认） */
const columnConfigs = ref<ColumnConfig[]>(
  preferences.value.columns && preferences.value.columns.length > 0
    ? preferences.value.columns
    : buildDefaultColumnConfigs(),
);

/** 根据列配置计算实际显示的表格列（过滤 + 排序） */
const visibleColumns = computed<TableColumnsType>(() => {
  const configMap = new Map(
    columnConfigs.value.map((c, i) => [
      c.key,
      { visible: c.visible, order: i },
    ]),
  );
  return columns
    .filter((c: any) => {
      const cfg = configMap.get(getColumnKey(c));
      return cfg ? cfg.visible : true;
    })
    .toSorted((a: any, b: any) => {
      const aOrder = configMap.get(getColumnKey(a))?.order ?? 99;
      const bOrder = configMap.get(getColumnKey(b))?.order ?? 99;
      return aOrder - bOrder;
    });
});

/** 列设置变更 */
function handleUpdateColumns(cols: ColumnConfig[]) {
  columnConfigs.value = cols;
  updateColumns(cols);
}

/** 恢复默认列配置 */
function handleResetColumns() {
  columnConfigs.value = buildDefaultColumnConfigs();
  updateColumns(columnConfigs.value);
}

// ===== 自动刷新（WebSocket 实时推送 + 低频轮询 fallback）=====

const autoRefresh = ref(true);
const refreshInterval = 30; // fallback 轮询间隔（秒），仅在 WS 断连时生效
const countdown = ref(refreshInterval);
let refreshTimer: null | ReturnType<typeof setInterval> = null;
let countdownTimer: null | ReturnType<typeof setInterval> = null;
let wsUnsubscribe: (() => void) | null = null;
let wsConnectionUnsubscribe: (() => void) | null = null;

/** WebSocket 实时数据：局部更新单条回路的 currentValues */
function handleRealtimeMessage(msg: {
  collectTime: string;
  quality: number;
  tagCode: string;
  value: string;
}) {
  // 解析 tagCode: "80FIC11906_PIDA.PV" → tagName="80FIC11906_PIDA", role="PV"
  const dotIdx = msg.tagCode.lastIndexOf('.');
  if (dotIdx === -1) return;
  const tagName = msg.tagCode.slice(0, Math.max(0, dotIdx));
  const role = msg.tagCode.slice(Math.max(0, dotIdx + 1)).toUpperCase();

  // 在当前列表中找到对应回路并局部更新
  const item = monitorList.value.find((l) => l.tagName === tagName);
  if (!item) return;

  const cv = item.currentValues;
  const numValue = Number.parseFloat(msg.value);
  if (Number.isNaN(numValue)) return;

  switch (role) {
    case 'MODE': {
      cv.mode = numValue;
      // 复用后端已有映射逻辑
      let modeLabel = 'Unknown';
      switch (numValue) {
        case 0: { modeLabel = 'Manual'; break; }
        case 1: { modeLabel = 'Auto'; break; }
        case 2: { modeLabel = 'Cascade'; break; }
        default: { break; }
      }
      cv.modeLabel = modeLabel;
      break;
    }
    case 'OP': {
      cv.op = numValue;
      break;
    }
    case 'PV': {
      cv.pv = numValue;
      cv.pvQuality = msg.quality as any;
      break;
    }
    case 'SP': {
      cv.sp = numValue;
      break;
    }
    default: {
      return; // PID_P/PID_I/PID_D 不在监控列表展示
    }
  }
  cv.readAt = msg.collectTime;
  lastRefreshAt.value = new Date();
}

// ===== 趋势 Modal =====

const trendModalVisible = ref(false);
const trendLoading = ref(false);
const trendDetail = ref<LoopApi.MonitorDetail | null>(null);
const trendWindow = ref<LoopApi.TrendWindow>(
  (preferences.value.defaultTimeWindow as LoopApi.TrendWindow) ||
    'last_4_hours',
);
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

/** 选中回路：仅记录选中状态（用于表格行高亮 + StatusFooter 显示） */
function handleSelectLoop(record: LoopApi.MonitorListItem) {
  selectedLoop.value = record;
}

// ===== 工具函数 =====

/** MODE 颜色映射：Auto=绿 / Manual=橙 / Cascade=蓝 - 使用 ZL 语义色 */
function modeColor(modeLabel: string): string {
  if (modeLabel === 'Auto') return '#10B981';
  if (modeLabel === 'Manual') return '#F59E0B';
  if (modeLabel === 'Cascade') return '#3B82F6';
  return '#CBD5E1';
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

function formatTime(t: null | string | undefined): string {
  if (!t) return '—';
  try {
    // 强制北京时间（UTC+8）
    return new Date(t).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' });
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
      loopType: query.loopType ? (query.loopType as LoopApi.LoopType) : undefined,
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

// P2 #37 UX13: 导出功能开发中，按钮改为 disabled + tooltip

function handleSearch() {
  query.page = 1;
  loadList();
  loadLoopTypeStats();
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
              [0.6, themeColors.value.DANGER],
              [0.8, themeColors.value.WARNING],
              [1, themeColors.value.SUCCESS],
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

/** P3 #53: 跳转到 Tag 关联管理（带当前回路 ID） */
function goToTagMapping() {
  if (!currentRecord.value) return;
  router.push(`/loop/tag-mapping?loopId=${currentRecord.value.loopId}`);
}

// ===== 自动刷新 =====

/**
 * 启动低频轮询 fallback
 *
 * 仅在 WS 断连时使用，避免与 WS 实时推送重复请求。
 */
function startPolling() {
  if (refreshTimer) return;
  countdown.value = refreshInterval;
  refreshTimer = setInterval(() => {
    loadList();
    countdown.value = refreshInterval;
  }, refreshInterval * 1000);
  countdownTimer = setInterval(() => {
    if (countdown.value > 0) countdown.value -= 1;
  }, 1000);
}

/**
 * 停止低频轮询
 */
function stopPolling() {
  if (refreshTimer) {
    clearInterval(refreshTimer);
    refreshTimer = null;
  }
  if (countdownTimer) {
    clearInterval(countdownTimer);
    countdownTimer = null;
  }
}

function startAutoRefresh() {
  stopAutoRefresh();
  if (!autoRefresh.value) return;

  // 启动 WebSocket 实时推送
  const accessStore = useAccessStore();
  const token = accessStore.accessToken;
  if (token) {
    if (!realtimeWs.isConnected) {
      realtimeWs.connect(token);
    }
    wsUnsubscribe = realtimeWs.onMessage(handleRealtimeMessage);
    // P2 #38 UX14: WS 连接状态变化时切换轮询策略
    // - WS 在线 → 停止轮询（实时推送已覆盖）
    // - WS 断连 → 启动轮询 fallback
    wsConnectionUnsubscribe = realtimeWs.onConnectionChange(() => {
      if (realtimeWs.isConnected) {
        stopPolling();
      } else {
        startPolling();
      }
    });
  }

  // 初始策略：WS 已连接则不启动轮询，等 WS 推送；WS 未连接则启动轮询 fallback
  if (!realtimeWs.isConnected) {
    startPolling();
  }
}

function stopAutoRefresh() {
  stopPolling();
  if (wsUnsubscribe) {
    wsUnsubscribe();
    wsUnsubscribe = null;
  }
  if (wsConnectionUnsubscribe) {
    wsConnectionUnsubscribe();
    wsConnectionUnsubscribe = null;
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

// ===== 主题切换重渲图表 =====
watch(isDark, () => {
  nextTick(() => {
    renderGauge();
  });
});

// ===== 偏好持久化 =====

/** 保存默认时间窗 */
watch(trendWindow, (val) => setDefaultTimeWindow(val));

// ===== 筛选预设 =====

const presetModalVisible = ref(false);
const presetName = ref('');

function handleSavePreset() {
  presetName.value = `预设 ${(preferences.value.savedFilters?.length ?? 0) + 1}`;
  presetModalVisible.value = true;
}

function confirmSavePreset() {
  if (!presetName.value.trim()) {
    message.warning('请输入预设名称');
    return;
  }
  saveFilterPreset(presetName.value.trim(), { ...query });
  presetModalVisible.value = false;
  message.success('预设已保存');
}

function handleApplyPreset(preset: FilterPreset) {
  const f = preset.filters;
  query.plantNodeId = f.plantNodeId;
  query.loopType = f.loopType;
  query.keyword = f.keyword ?? '';
  query.page = 1;
  loadList();
  message.success(`已应用预设：${preset.name}`);
}

function handleDeletePreset(id: string) {
  deleteFilterPreset(id);
  message.success('预设已删除');
}

/** 重置页面偏好 */
function handleResetPreferences() {
  resetPreferences();
  columnConfigs.value = buildDefaultColumnConfigs();
  trendWindow.value = 'last_4_hours';
  message.success('页面偏好已重置');
}

// ===== 生命周期 =====

onMounted(() => {
  loadPlantNodes();
  loadList();
  loadLoopTypeStats();
  startAutoRefresh();
});

watch(
  () => query.plantNodeId,
  () => {
    query.page = 1;
    loadList();
    loadLoopTypeStats();
  }
);

onUnmounted(() => {
  stopAutoRefresh();
  // 页面离开时断开 WebSocket（其他页面可能不需要实时数据）
  realtimeWs.disconnect();
});
</script>

<template>
  <Page>
    <!-- 顶部工具栏：筛选条件 + 操作按钮 + 自动刷新 -->
    <ClpmPageToolbar title="回路监控" subtitle="列表 + 摘要 + 趋势主画布">
      <Select
        v-model:value="query.plantNodeId"
        placeholder="按装置/单元筛选"
        style="width: 220px"
        allow-clear
        show-search
        :options="plantNodeOptions"
        :filter-option="
          (input: string, option: any) => option.label.includes(input)
        "
        @change="handleSearch"
      />
      <Select
        v-model:value="query.loopType"
        placeholder="按回路类型筛选"
        style="width: 140px"
        allow-clear
        :options="loopTypeOptions"
        @change="handleSearch"
      />
      <Input
        v-model:value="query.keyword"
        placeholder="搜索位号/描述"
        allow-clear
        style="width: 200px"
        @press-enter="handleSearch"
      />
      <div class="flex items-center gap-2 text-sm text-gray-500">
        <span>自动刷新（{{ refreshInterval }}s）</span>
        <Switch :checked="autoRefresh" @change="handleToggleAutoRefresh" />
        <span v-if="autoRefresh" class="text-xs text-gray-400">{{ countdown }}s 后刷新</span>
      </div>
      <template #actions>
        <ClpmToolbarButton icon="search" label="查询" @click="handleSearch" />
        <ClpmToolbarButton
          icon="refresh"
          label="刷新"
          :loading="loading"
          @click="loadList"
        />
        <ClpmToolbarButton
          icon="export"
          label="导出"
          disabled
          disabled-reason="回路监控数据导出功能待后端接口支持"
        />
      </template>
    </ClpmPageToolbar>

    <!-- v6.1 新增：统计卡片区域 -->
    <div class="mt-3">
      <Card :body-style="{ padding: '8px 16px' }" class="h-auto">
        <div class="flex items-center justify-between">
          <div class="flex items-center gap-4">
            <div
              class="flex items-center gap-2 px-4 py-1.5 rounded-lg cursor-pointer hover:opacity-80 transition-opacity"
              :style="{
                backgroundColor: query.loopType === '' ? '#4B556315' : '#4B556308',
                borderLeft: `3px solid #4B5563`,
                borderBottom: query.loopType === '' ? `2px solid #4B5563` : 'none',
              }"
              @click="handleTypeCardClick('ALL')"
            >
              <span
                class="w-2 h-2 rounded-full"
                style="background-color: #4B5563"
              ></span>
              <span class="text-sm text-gray-600 font-medium">全部</span>
              <span class="text-sm font-bold" style="color: #4B5563">
                {{ Object.values(loopTypeStats).reduce((sum, count) => sum + count, 0) }}
              </span>
            </div>
            <div
              v-for="(count, key) in loopTypeStats"
              :key="key"
              class="flex items-center gap-2 px-3 py-1 rounded cursor-pointer hover:opacity-80 transition-opacity"
              :style="{
                backgroundColor: query.loopType === key ? `${LOOP_TYPE_COLOR_MAP[key]}30` : `${LOOP_TYPE_COLOR_MAP[key]}15`,
                borderLeft: `3px solid ${LOOP_TYPE_COLOR_MAP[key]}`,
                borderBottom: query.loopType === key ? `2px solid ${LOOP_TYPE_COLOR_MAP[key]}` : 'none',
              }"
              @click="handleTypeCardClick(key)"
            >
              <span
                class="w-2 h-2 rounded-full"
                :style="{ backgroundColor: LOOP_TYPE_COLOR_MAP[key] }"
              ></span>
              <span class="text-sm text-gray-600">
                {{ { TEMPERATURE: '温度', PRESSURE: '压力', LEVEL: '液位', FLOW: '流量', ANALYSIS: '分析', SPEED: '速度', OTHER: '其他' }[key] }}
              </span>
              <span class="text-sm font-semibold" :style="{ color: LOOP_TYPE_COLOR_MAP[key] }">
                {{ count }}
              </span>
            </div>
          </div>
          <div class="flex items-center gap-4">
            <div
              v-for="(count, key) in controlModeStats"
              :key="key"
              class="flex items-center gap-2 px-3 py-1 rounded"
              :style="{
                backgroundColor: `${CONTROL_MODE_COLOR_MAP[key] || '#6b7280'}15`,
                borderLeft: `3px solid ${CONTROL_MODE_COLOR_MAP[key] || '#6b7280'}`,
              }"
            >
              <span
                class="w-2 h-2 rounded-full"
                :style="{ backgroundColor: CONTROL_MODE_COLOR_MAP[key] || '#6b7280' }"
              ></span>
              <span class="text-sm text-gray-600">{{ key }}</span>
              <span class="text-sm font-semibold" :style="{ color: CONTROL_MODE_COLOR_MAP[key] || '#6b7280' }">
                {{ count }}
              </span>
            </div>
            <div
              class="flex items-center gap-2 px-4 py-1.5 rounded-lg"
              :style="{
                backgroundColor: '#8b5cf615',
                borderLeft: '3px solid #8b5cf6',
              }"
            >
              <span
                class="w-2 h-2 rounded-full"
                style="background-color: #8b5cf6"
              ></span>
              <span class="text-sm text-gray-600 font-medium">自控率</span>
              <span class="text-sm font-bold" style="color: #8b5cf6">
                {{ realtimeControlRate }}%
              </span>
            </div>
            <EchartsUI ref="modeChartRef" style="width: 300px; height: 60px" />
          </div>
        </div>
      </Card>
    </div>

    <!-- 主区：回路列表（全宽，详情/趋势/性能通过 Modal 与跳转访问） -->
    <div class="mt-3 min-h-[calc(100vh-220px)]">
      <ClpmDataCanvas title="回路列表" :loading="loading">
        <template #extra>
          <ClpmColumnSettings
            :columns="columnConfigs"
            @update:columns="handleUpdateColumns"
            @reset="handleResetColumns"
          />
          <!-- 筛选预设与重置偏好（折叠区） -->
          <Popover placement="bottomRight" trigger="click">
            <template #content>
              <div class="w-64">
                <div class="mb-2 flex items-center justify-between">
                  <span class="text-xs text-gray-500">筛选预设</span>
                  <Button
                    type="link"
                    size="small"
                    class="!px-0"
                    @click="handleSavePreset"
                  >
                    保存当前筛选
                  </Button>
                </div>
                <div
                  v-if="preferences.savedFilters?.length"
                  class="flex flex-wrap gap-1"
                >
                  <Tag
                    v-for="preset in preferences.savedFilters"
                    :key="preset.id"
                    class="m-0 cursor-pointer"
                    @click="handleApplyPreset(preset)"
                  >
                    {{ preset.name }}
                    <span
                      class="ml-1 text-gray-400 hover:text-red-500"
                      @click.stop="handleDeletePreset(preset.id)"
                    >
                      ×
                    </span>
                  </Tag>
                </div>
                <div v-else class="text-xs text-gray-400">暂无保存的预设</div>
                <div class="mt-3 border-t border-gray-100 pt-2">
                  <Button
                    type="link"
                    size="small"
                    class="!px-0"
                    @click="handleResetPreferences"
                  >
                    重置页面偏好
                  </Button>
                </div>
              </div>
            </template>
            <Button type="text" size="small" class="!px-2">
              <template #icon><span class="text-sm">⚙</span></template>
              偏好
            </Button>
          </Popover>
        </template>
        <Table
          :columns="visibleColumns"
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
          size="small"
          :row-class-name="
            (record) =>
              selectedLoop?.loopId === record.loopId
                ? 'ant-table-row-selected cursor-pointer'
                : 'cursor-pointer'
          "
          :custom-row="
            (record) => ({
              onClick: () =>
                handleSelectLoop(record as LoopApi.MonitorListItem),
            })
          "
          @change="handleTableChange"
        >
          <template #bodyCell="{ column, record }">
            <!-- v6.1 新增：测量量程 -->
            <template v-if="column.key === 'pvRange'">
              <span
                v-if="(record as LoopApi.MonitorListItem).pvRange?.min != null || (record as LoopApi.MonitorListItem).pvRange?.max != null"
                class="font-mono text-xs text-slate-600"
              >
                {{ (record as LoopApi.MonitorListItem).pvRange?.min ?? '—' }}
                ~
                {{ (record as LoopApi.MonitorListItem).pvRange?.max ?? '—' }}
              </span>
              <span v-else class="text-slate-300">—</span>
            </template>
            <!-- v6.1 新增：单位 -->
            <template v-else-if="column.key === 'pvUnit'">
              <span v-if="(record as LoopApi.MonitorListItem).pvUnit" class="text-xs text-slate-600">
                {{ (record as LoopApi.MonitorListItem).pvUnit }}
              </span>
              <span v-else class="text-slate-300">—</span>
            </template>
            <template v-else-if="column.key === 'loopType'">
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
              <span v-if="(record as LoopApi.MonitorListItem).currentValues?.sp != null" class="flex items-baseline justify-end gap-1">
                <ClpmNumeric
                  :value="(record as LoopApi.MonitorListItem).currentValues?.sp"
                  :precision="2"
                  mono
                  size="sm"
                />
                <span class="text-xs text-gray-500">{{ (record as LoopApi.MonitorListItem).currentValues?.unit }}</span>
              </span>
              <span v-else class="text-gray-400">—</span>
            </template>
            <template v-else-if="column.key === 'pv'">
              <span v-if="(record as LoopApi.MonitorListItem).currentValues?.pv != null" class="flex items-baseline justify-end gap-1">
                <ClpmNumeric
                  :value="(record as LoopApi.MonitorListItem).currentValues?.pv"
                  :precision="2"
                  mono
                  size="sm"
                  :weight="600"
                />
                <span class="text-xs text-gray-500">{{ (record as LoopApi.MonitorListItem).currentValues?.unit }}</span>
              </span>
              <span v-else class="text-gray-400">—</span>
            </template>
            <template v-else-if="column.key === 'op'">
              <span v-if="(record as LoopApi.MonitorListItem).currentValues?.op != null" class="flex items-baseline justify-end gap-0.5">
                <ClpmNumeric
                  :value="(record as LoopApi.MonitorListItem).currentValues?.op"
                  :precision="2"
                  mono
                  size="sm"
                />
                <span class="text-xs text-gray-500">%</span>
              </span>
              <span v-else class="text-gray-400">—</span>
            </template>
            <template v-else-if="column.key === 'mode'">
              <Tag
                v-if="
                  (record as LoopApi.MonitorListItem).currentValues
                    ?.modeLabel ||
                  (record as LoopApi.MonitorListItem).currentValues?.mode !=
                    null
                "
                :color="
                  modeColor(
                    (record as LoopApi.MonitorListItem).currentValues
                      ?.modeLabel,
                  )
                "
              >
                {{ modeText(record as LoopApi.MonitorListItem) }}
              </Tag>
              <span v-else class="text-gray-400">—</span>
            </template>
            <template v-else-if="column.key === 'score'">
              <span
                v-if="(record as LoopApi.MonitorListItem).score != null"
                class="inline-flex items-center gap-1"
              >
                <span
                  class="w-2 h-2 rounded-full"
                  :class="
                    (record as LoopApi.MonitorListItem).score >= 80
                      ? 'bg-emerald-500'
                      : (record as LoopApi.MonitorListItem).score >= 60
                        ? 'bg-amber-500'
                        : 'bg-rose-500'
                  "
                ></span>
                <ClpmNumeric
                  :value="(record as LoopApi.MonitorListItem).score"
                  :precision="1"
                  mono
                  size="sm"
                  :weight="600"
                />
              </span>
              <span v-else class="text-gray-400">—</span>
            </template>
            <template v-else-if="column.key === 'action'">
              <div class="flex items-center gap-1">
                <Tag color="blue" class="cursor-pointer hover:opacity-80">
                  <span @click="viewDetail(record as LoopApi.MonitorListItem)">详情</span>
                </Tag>
                <Tag color="green" class="cursor-pointer hover:opacity-80">
                  <span @click="openTrend(record as LoopApi.MonitorListItem)">趋势</span>
                </Tag>
                <Tag color="orange" class="cursor-pointer hover:opacity-80">
                  <span @click="openPerformance(record as LoopApi.MonitorListItem)">性能</span>
                </Tag>
              </div>
            </template>
          </template>
        </Table>
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
              <span v-if="trendDetail.currentValues.pv != null" class="ml-2 flex items-baseline gap-1">
                <ClpmNumeric
                  :value="trendDetail.currentValues.pv"
                  :precision="2"
                  mono
                  size="sm"
                  :weight="600"
                />
                <span class="text-xs text-gray-500">{{ trendDetail.currentValues.unit }}</span>
              </span>
              <span v-else class="ml-2 text-gray-400">—</span>
            </div>
            <div>
              <span class="text-xs text-gray-400">SP</span>
              <span v-if="trendDetail.currentValues.sp != null" class="ml-2 flex items-baseline gap-1">
                <ClpmNumeric
                  :value="trendDetail.currentValues.sp"
                  :precision="2"
                  mono
                  size="sm"
                  :weight="600"
                />
                <span class="text-xs text-gray-500">{{ trendDetail.currentValues.unit }}</span>
              </span>
              <span v-else class="ml-2 text-gray-400">—</span>
            </div>
            <div>
              <span class="text-xs text-gray-400">OP</span>
              <span v-if="trendDetail.currentValues.op != null" class="ml-2 flex items-baseline gap-0.5">
                <ClpmNumeric
                  :value="trendDetail.currentValues.op"
                  :precision="2"
                  mono
                  size="sm"
                  :weight="600"
                />
                <span class="text-xs text-gray-500">%</span>
              </span>
              <span v-else class="ml-2 text-gray-400">—</span>
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

          <!-- P3 #53: 回路状态非 READY 警告（Tag 关联不完整或未激活） -->
          <Alert
            v-if="isLoopNotReady && loopStatus === 'PARTIAL'"
            class="mb-4"
            type="warning"
            show-icon
            message="回路 Tag 关联不完整（PARTIAL），未参与 KPI 计算"
          >
            <template #description>
              <div>
                该回路缺少 PV/SP/OP/MODE 4 个必填 Tag 中的一个或多个，
                系统不会为其生成 KPI 快照。请到
                <a @click="goToTagMapping">Tag 关联管理</a>
                补全必填 Tag 后再次评估。
              </div>
            </template>
          </Alert>
          <Alert
            v-else-if="isLoopNotReady && loopStatus === 'INACTIVE'"
            class="mb-4"
            type="info"
            show-icon
            message="回路未激活（INACTIVE），不参与 KPI 计算"
            description="请到回路管理页面激活该回路，并确保 4 个必填 Tag 关联完整。"
          />

          <!-- P3 #53: 仅当回路 READY 但 KPI INCONCLUSIVE 时显示「数据不足」警告 -->
          <Alert
            v-if="showDataInsufficientAlert"
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
              :style="{
                color: isPerfInconclusive
                  ? '#9CA3AF'
                  : (perfDetail.kpiSummary.composite_score ?? 0) >= 80
                    ? '#10B981'
                    : (perfDetail.kpiSummary.composite_score ?? 0) >= 60
                      ? '#F59E0B'
                      : '#F43F5E',
              }"
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
            class="rounded border p-3 transition-all hover:shadow-sm"
            :style="{
              backgroundColor:
                (perfDetail.kpiSummary[item.key] as null | number) == null
                  ? 'transparent'
                  : (perfDetail.kpiSummary[item.key] as number) >= 80
                    ? 'rgba(16, 185, 129, 0.06)'
                    : (perfDetail.kpiSummary[item.key] as number) >= 60
                      ? 'rgba(245, 158, 11, 0.06)'
                      : 'rgba(244, 63, 94, 0.06)',
            }"
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
            <div class="mt-1 flex items-center gap-2">
              <span
                class="text-xl font-medium"
                :style="{
                  color:
                    (perfDetail.kpiSummary[item.key] as null | number) == null
                      ? '#9CA3AF'
                      : (perfDetail.kpiSummary[item.key] as number) >= 80
                        ? '#10B981'
                        : (perfDetail.kpiSummary[item.key] as number) >= 60
                          ? '#F59E0B'
                          : '#F43F5E',
                }"
              >
                {{
                  (perfDetail.kpiSummary[item.key] as null | number)?.toFixed(
                    1,
                  ) ?? '—'
                }}
              </span>
              <span class="text-sm text-gray-500">{{ item.unit }}</span>
            </div>
            <div class="mt-2 h-1 bg-gray-100 rounded-full overflow-hidden">
              <div
                v-if="(perfDetail.kpiSummary[item.key] as null | number) != null"
                class="h-full rounded-full transition-all"
                :style="{
                  width: `${perfDetail.kpiSummary[item.key]}%`,
                  backgroundColor:
                    (perfDetail.kpiSummary[item.key] as number) >= 80
                      ? '#10B981'
                      : (perfDetail.kpiSummary[item.key] as number) >= 60
                        ? '#F59E0B'
                        : '#F43F5E',
                }"
              ></div>
            </div>
            <div class="mt-1 text-xs text-gray-400">{{ item.desc }}</div>
          </div>
          </div>
        </div>
        <div v-else class="py-12 text-center text-gray-400">暂无性能数据</div>
      </Spin>
    </Modal>

    <!-- 保存筛选预设 Modal -->
    <Modal
      v-model:open="presetModalVisible"
      title="保存筛选预设"
      :footer="null"
      destroy-on-close
      width="400px"
    >
      <div class="flex flex-col gap-3">
        <Input
          v-model:value="presetName"
          placeholder="请输入预设名称"
          @press-enter="confirmSavePreset"
        />
        <div class="flex justify-end gap-2">
          <Button @click="presetModalVisible = false">取消</Button>
          <Button type="primary" @click="confirmSavePreset">确定</Button>
        </div>
      </div>
    </Modal>
  </Page>
</template>

<style scoped>
:deep(.ant-table-thead > tr > th) {
  text-align: center !important;
}

.clpm-status-footer {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  padding: 8px 12px;
  font-size: 12px;
  color: hsl(var(--muted-foreground));
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border));
  border-radius: calc(var(--radius) * 1px);
}

.clpm-status-footer__divider {
  color: hsl(var(--border));
}

.clpm-status-footer strong {
  font-family: var(
    --font-mono,
    ui-monospace,
    SFMono-Regular,
    Menlo,
    Monaco,
    Consolas,
    monospace
  );
  font-weight: 700;
  font-feature-settings: 'tnum';
  font-variant-numeric: tabular-nums;
}

.clpm-status-footer strong.is-active {
  color: hsl(var(--primary));
}

.clpm-status-footer strong.is-muted {
  color: hsl(var(--muted-foreground));
}

.loop-row-actions {
  display: flex;
  visibility: hidden;
  gap: 1px;
  opacity: 0;
  transition: all 0.2s ease;
}

:deep(.ant-table-row):hover .loop-row-actions {
  visibility: visible;
  opacity: 1;
}
</style>
