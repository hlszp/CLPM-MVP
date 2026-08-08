<script lang="ts" setup>
import type { TableColumnsType, TablePaginationConfig } from 'ant-design-vue';

import type { EchartsUIType } from '@vben/plugins/echarts';

import type { DiagnosisLabel } from '#/api/diagnosis';
/**
 * S2-LOOP-011 回路监控列表页
 *
 * 对齐 D06 §6 + IDS v3.2 §2.2.15 + UI/UX 改造方案 §8.3
 * - 顶部工具栏：装置/单元 + 类型 + 关键字 + 自动刷新开关 + 查询/刷新/导出
 * - 主区：回路列表 Table（全宽，行内提供 趋势/性能/详情 三个操作入口）
 * - 详情展示交由详情页：避免与 /loop/detail/:id 内容重合
 * - 趋势 Modal：复用 WaveformChart 组件（与回路详情页风格统一）
 * - 性能 Modal：ECharts 仪表盘 + 6 大 KPI 卡片（含权重）
 * - 30 秒自动刷新（Switch 开关；WS 在线走实时推送，断连走 usePolling 轮询 fallback）
 * - StatusFooter：最近刷新/数据延迟/自动刷新状态/选中回路
 */
import type { LoopApi } from '#/api/loop';
import type { PlantNodeApi } from '#/api/plant-node';
import type {
  ColumnConfig,
  FilterPreset,
} from '#/composables/use-clpm-preferences';

import {
  computed,
  nextTick,
  onMounted,
  onUnmounted,
  reactive,
  ref,
  watch,
} from 'vue';
import { useRoute, useRouter } from 'vue-router';

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

import { getDiagnosisListApi } from '#/api/diagnosis';
import {
  getLoopDetailApi,
  getLoopMonitorDetailApi,
  getLoopMonitorListApi,
  getLoopTypeStatsApi,
} from '#/api/loop';
import { getPlantNodeTreeApi } from '#/api/plant-node';
import {
  ClpmDataCanvas,
  ClpmBulletChart,
  ClpmDataHealthBadges,
  ClpmInfoTip,
  ClpmLoopLink,
  ClpmModal,
  ClpmNumeric,
  ClpmPageToolbar,
  ClpmStandardActions,
} from '#/components/clpm';
import WaveformChart from '#/components/loop/waveform-chart.vue';
import { usePagePreference } from '#/composables/use-clpm-preferences';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import { useEchartsPreset } from '#/composables/use-echarts-preset';
import {
  LOOP_TYPE_COLOR_MAP,
  LOOP_TYPE_LABEL_MAP,
  MODE_COLOR_MAP,
  MODE_LABEL_MAP,
  useLoopPalettes,
} from '#/composables/use-loop-palettes';
import { showPageHelp, usePageToolbar } from '#/composables/use-page-toolbar';
import { usePolling } from '#/composables/use-polling';
import { DIAGNOSIS_TERM_EXPLANATIONS } from '#/constants/clpm-ui';
import {
  DIAGNOSIS_LABEL_COLOR_MAP,
  getDiagnosisLabelName,
} from '#/constants/diagnosis';
import { formatTime } from '#/utils/format';
import { flattenNodes } from '#/utils/plant-node';
import { mapQualityToLabel } from '#/utils/quality-code';
import { realtimeWs } from '#/utils/realtime-ws';

defineOptions({ name: 'LoopMonitor' });

const { themeColors } = useClpmTheme();
const { modeLabelColor } = useLoopPalettes();

const router = useRouter();
const route = useRoute();

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

/** 回路类型筛选选项（label 取自共享色板常量 LOOP_TYPE_LABEL_MAP） */
const loopTypeOptions = [
  { label: '全部', value: '' },
  ...Object.entries(LOOP_TYPE_LABEL_MAP).map(([value, label]) => ({
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

/**
 * KPI 状态映射 - ZL 语义色（响应式 themeColors）
 * INCONCLUSIVE 文案统一为「数据不足」（对齐 confidence-badge、规范 §7.9）
 */
const kpiStatusMap = computed<Record<string, { color: string; label: string }>>(
  () => ({
    SUCCESS: { color: themeColors.value.SUCCESS, label: '良好' },
    INCONCLUSIVE: { color: themeColors.value.NEUTRAL, label: '数据不足' },
    PARTIAL: { color: themeColors.value.WARNING, label: '部分' },
  }),
);

/** 性能 Modal 中 KPI 结果是否为 INCONCLUSIVE */
const isPerfInconclusive = computed(
  () => perfDetail.value?.kpiSummary.status === 'INCONCLUSIVE',
);

/** P3 #53: 回路状态（READY/PARTIAL/INACTIVE）—— 用于区分 KPI 缺失原因
 * WS-D 阶段5：消费 backend loopStatus（前端不再硬编码） */
const loopStatus = computed(() => perfDetail.value?.loopStatus);

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
/** Phase 10 UX 包：错误态/空态分离——loadList 抛错时记录错误信息，
 * 区分"接口异常"（errorState）与"接口正常但无数据"（空态）。
 * 错误已由全局拦截器 toast，这里仅用于渲染内联错误占位，避免误报"暂无数据"。 */
const errorMessage = ref<null | string>(null);

/**
 * D6 入口整合：回路 → 最新诊断标签展示信息 map。
 *
 * 监控列表每页 20 条回路加载后，并行调用诊断列表 API（loopIds 批量过滤）
 * 建立该 map。表格"诊断标签"列据此渲染彩色 Tag，点击跳转诊断详情页。
 * 失败时降级为空 map，诊断列显示"—"，不阻塞主列表。
 */
const diagLabelMap = ref<
  Record<string, { color: string; label: string; labelCode: string }>
>({});

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

/** 按控制方式统计数量（MODE 数值: 0=手动,1=自动,2=串级,3=远程,4=先控） */
const controlModeStats = ref<Record<string, number>>({});

/** 控制方式柱状图 */
const { axisBase, getTooltipPreset } = useEchartsPreset();
const modeChartRef = ref<EchartsUIType>();
const { renderEcharts: renderModeChart } = useEcharts(modeChartRef);

/** 更新控制方式柱状图（固定 5 个类别） */
function updateModeChart() {
  const stats = controlModeStats.value;
  const modeKeys = ['0', '1', '2', '3', '4'];
  const labels = modeKeys.map((k) => MODE_LABEL_MAP[k] || k);
  const data = modeKeys.map((k) => stats[k] || 0);
  const colors = modeKeys.map((k) => MODE_COLOR_MAP[k]);

  // 整改 A-15：轴/工具提示走 ECharts 工业 preset（统一字号/等宽/中性色/无阴影）
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
        data: data.map((v, i) => ({
          value: v,
          itemStyle: { color: colors[i] },
        })),
        type: 'bar',
      },
    ],
    tooltip: { ...getTooltipPreset(), trigger: 'axis' },
    xAxis: {
      ...axisBase.value,
      splitLine: undefined,
      data: labels,
      type: 'category',
    },
    yAxis: {
      ...axisBase.value,
      type: 'value',
      splitLine: { show: false },
    },
  });
}

/** 自动回路数（MODE ≠ 0） */
const autoModeCount = computed(() => {
  const stats = controlModeStats.value;
  return (
    (stats['1'] || 0) +
    (stats['2'] || 0) +
    (stats['3'] || 0) +
    (stats['4'] || 0)
  );
});

/** 手动回路数（MODE = 0） */
const manualModeCount = computed(() => {
  return controlModeStats.value['0'] || 0;
});

/** 自控率（自动控制回路数 / 总回路数） */
const realtimeControlRate = computed(() => {
  const total = autoModeCount.value + manualModeCount.value;
  return total > 0 ? Math.round((autoModeCount.value / total) * 100) : 0;
});

/** 加载回路类型统计 */
async function loadLoopTypeStats() {
  try {
    const data = await getLoopTypeStatsApi(query.plantNodeId);
    loopTypeStats.value =
      (data as any).loopTypeStats || (data as Record<string, number>);
    controlModeStats.value = (data as any).controlModeStats || {};
    updateModeChart();
  } catch {
    // 错误已由拦截器处理
  }
}

/** 节流刷新统计卡片：1 秒内多次 MODE 推送只触发一次 stats 重载，避免打爆 API */
let statsRefreshTimer: null | ReturnType<typeof setTimeout> = null;
function scheduleStatsRefresh() {
  if (statsRefreshTimer) return;
  statsRefreshTimer = setTimeout(() => {
    statsRefreshTimer = null;
    loadLoopTypeStats();
  }, 1000);
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
  {
    title: '回路位号',
    dataIndex: 'tagName',
    key: 'tagName',
    width: 150,
    align: 'left',
  },
  {
    title: '名称',
    dataIndex: 'description',
    key: 'description',
    width: 180,
    ellipsis: true,
    align: 'left',
  },
  {
    title: '所属单元',
    dataIndex: 'unitName',
    key: 'unitName',
    width: 120,
    align: 'center',
  },
  // v6.1 新增：测量量程 / 单位
  { title: '测量量程', key: 'pvRange', width: 100, align: 'center' },
  { title: '单位', key: 'pvUnit', width: 55, align: 'center' },
  {
    title: '类型',
    dataIndex: 'loopType',
    key: 'loopType',
    width: 100,
    align: 'center',
  },
  {
    title: '设定值 SP',
    key: 'sp',
    width: 90,
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
    width: 90,
    align: 'right',
    customRender: ({ record }) => {
      const val = (record as LoopApi.MonitorListItem).currentValues?.pv;
      return val !== null && val !== undefined ? val.toFixed(2) : '-';
    },
    customCell: () => ({ style: { 'text-align': 'right' } }),
  },
  {
    title: '输出值 OP(%)',
    key: 'op',
    width: 90,
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
    width: 85,
    align: 'right',
    customRender: ({ text }) => {
      return text !== null && text !== undefined
        ? Number(text).toFixed(2)
        : '-';
    },
    customCell: () => ({ style: { 'text-align': 'right' } }),
  },
  // D6 入口整合：诊断标签列——展示最新诊断标签，点击跳转诊断详情页
  { title: '诊断标签', key: 'diagLabel', width: 110, align: 'center' },
  // 数据健康度（方案 A §5）：可信度 + 预处理有效率 + PV 完整度
  { title: '数据健康度', key: 'dataHealth', width: 130, align: 'center' },
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
let wsUnsubscribe: (() => void) | null = null;
let wsConnectionUnsubscribe: (() => void) | null = null;

/**
 * WS 断连 fallback 轮询：统一走 usePolling
 * （递归 setTimeout 防堆积、页面隐藏自动暂停、可见恢复立即补跑、卸载自动清理）
 */
const {
  isPolling: isFallbackPolling,
  start: startPolling,
  stop: stopPolling,
} = usePolling(
  async () => {
    await Promise.all([loadList(), loadLoopTypeStats()]);
  },
  { interval: refreshInterval * 1000 },
);

/** Phase 10 UX 包：WS 连接状态响应式镜像（实时驱动状态栏在线/离线/重连中徽标）
 * 通过 onConnectionChange 回调同步 realtimeWs.status 三态。 */
const wsConnectionStatus = ref<'offline' | 'online' | 'reconnecting'>(
  realtimeWs.status,
);

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
      // 本地重算 modeLabel/controlMode（与后端 _mode_value_to_label 默认映射一致），
      // 使监控列表"控制方式"列实时反映 MODE 变化；若有 loop_mode_mapping 自定义配置，
      // 下次列表刷新会对齐。同时节流触发统计卡片刷新。
      cv.mode = numValue;
      if (numValue === 0) {
        cv.modeLabel = 'Manual';
        if (item.controlMode !== undefined) item.controlMode = 'Manual';
      } else if (numValue >= 1) {
        cv.modeLabel = 'Auto';
        if (item.controlMode !== undefined) item.controlMode = 'Auto';
      }
      // 未知 MODE 值不覆盖 modeLabel（保持后端权威值）
      scheduleStatsRefresh();
      break;
    }
    case 'OP': {
      cv.op = numValue;
      break;
    }
    case 'PV': {
      cv.pv = numValue;
      // Phase 10 UX 包：WS 质量码统一映射（与后端 _GOOD_CODES={1,2,3,192} 对齐），
      // 不再直接透传数字；原 `as any` 会把 2 当成 UNCERTAIN 与 REST 路径冲突
      cv.pvQuality = mapQualityToLabel(msg.quality) as any;
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
// #3: ClpmModal 内置最大化/复位/拖动；这里仅追踪最大化态以调整图表高度
const trendMaximized = ref(false);

const trendChartHeight = computed(() =>
  trendMaximized.value ? 'calc(100vh - 220px)' : '400px',
);

/** #3: ClpmModal 最大化/还原时重置趋势图尺寸 */
function handleTrendMaximizeChange(maximized: boolean) {
  trendMaximized.value = maximized;
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

// ===== 统一工具栏支撑 =====
// 筛选区折叠态（工具栏「筛选」工具切换）
const filterVisible = ref(true);
function toggleFilter() {
  filterVisible.value = !filterVisible.value;
}

// 趋势时间窗循环切换（工具栏「时间窗」工具）
const trendWindowIdx = ref(
  Math.max(
    0,
    trendWindowOptions.findIndex((o) => o.value === trendWindow.value),
  ),
);
const trendWindowLabel = computed(
  () => trendWindowOptions[trendWindowIdx.value]?.label ?? '4h',
);
function cycleTrendWindow() {
  const next = (trendWindowIdx.value + 1) % trendWindowOptions.length;
  trendWindowIdx.value = next;
  trendWindow.value = trendWindowOptions[next]!.value;
}

/** 导出当前监控列表为 CSV（客户端生成） */
function exportMonitorCsv() {
  if (monitorList.value.length === 0) {
    message.warning('当前无可导出的数据');
    return;
  }
  const header = [
    '回路位号',
    '名称',
    '所属单元',
    '类型',
    'SP',
    'PV',
    'OP',
    '控制方式',
    '性能指数',
  ];
  const rows = monitorList.value.map((m) => [
    m.tagName ?? '',
    m.description ?? '',
    m.unitName ?? '',
    m.loopType ?? '',
    m.currentValues?.sp == null ? '' : m.currentValues.sp.toFixed(2),
    m.currentValues?.pv == null ? '' : m.currentValues.pv.toFixed(2),
    m.currentValues?.op == null ? '' : m.currentValues.op.toFixed(2),
    m.currentValues?.mode == null
      ? ''
      : (MODE_LABEL_MAP[String(m.currentValues.mode)] ??
        String(m.currentValues.mode)),
    m.score == null ? '' : Number(m.score).toFixed(2),
  ]);
  const csv = [header, ...rows]
    .map((r) => r.map((c) => `"${String(c).replaceAll('"', '""')}"`).join(','))
    .join('\n');
  const blob = new Blob([`\uFEFF${csv}`], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `loop-monitor-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
  message.success(`已导出 ${monitorList.value.length} 条回路`);
}

function handleHelp() {
  showPageHelp({
    title: '回路监控 帮助',
    content:
      '按装置/单元、类型、关键字筛选回路；统计卡片可点击快速筛选。列表展示实时 SP/PV/OP、控制方式、性能指数与诊断标签。工具栏「时间窗」切换趋势弹窗默认范围，「列设置」自定义显示列。WS 在线时实时推送，断连自动降级为轮询。',
  });
}

// ===== 统一工具栏（refresh / time-window / filter / export / setting / help） =====
const { toolbarItems } = usePageToolbar(() => ({
  refresh: { onClick: loadList, loading: loading.value },
  'time-window': {
    onClick: cycleTrendWindow,
    active: true,
    tooltip: `趋势时间窗：${trendWindowLabel.value}（点击切换）`,
    label: trendWindowLabel.value,
  },
  filter: { onClick: toggleFilter, active: filterVisible.value },
  export: {
    onClick: exportMonitorCsv,
    permission: ['ADMIN', 'IC_ENGINEER'],
    disabledReason: '仅工程师/管理员可导出',
  },
  setting: {},
  help: { onClick: handleHelp },
}));

/** 选中回路：仅记录选中状态（用于表格行高亮 + StatusFooter 显示） */
function handleSelectLoop(record: LoopApi.MonitorListItem) {
  selectedLoop.value = record;
}

// ===== 工具函数 =====

/** MODE 徽标颜色：委托共享色板 useLoopPalettes（ZL 语义色，随主题切换） */
function modeColor(modeLabel: null | string | undefined): string {
  return modeLabelColor(modeLabel);
}

/** MODE 中文标签映射：优先使用后端 loop_mode_mapping 配置生成的 modeLabel
 * WS-D 阶段5：不再前端硬编码 0/1/2 → Manual/Auto/Cascade，统一由后端权威输出。 */
function modeText(record: LoopApi.MonitorListItem): string {
  return record.currentValues?.modeLabel || '—';
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
  errorMessage.value = null;
  try {
    const data = await getLoopMonitorListApi({
      plantNodeId: query.plantNodeId,
      loopType: (query.loopType as LoopApi.LoopType) || undefined,
      keyword: query.keyword || undefined,
      page: query.page,
      pageSize: query.pageSize,
    });
    monitorList.value = data.items;
    total.value = data.total;
    // D6 入口整合：并行加载当前页回路的最新诊断标签（不阻塞主列表，
    // 失败时诊断列降级显示"—"；WS 实时刷新触发的局部更新不重查诊断标签）
    loadDiagLabels(data.items.map((it) => it.loopId));
  } catch (error: any) {
    // 错误已由拦截器 toast；此处仅记录用于内联错误占位渲染
    errorMessage.value = error?.message ?? '加载失败';
    // 出错时清空旧列表，避免显示过期数据混淆
    monitorList.value = [];
    total.value = 0;
    diagLabelMap.value = {};
  } finally {
    loading.value = false;
    lastRefreshAt.value = new Date();
  }
}

/**
 * D6 入口整合：批量加载回路的最新诊断标签，建立 loopId → {color, label} map。
 *
 * 后端 list_diagnosis 接口新增 loop_ids 批量过滤参数，一次请求拿回当前页
 * 全部回路的最新诊断结果（pageSize=100 覆盖单页 20 条 + 重复回路）。
 * 前端按 loopId 建立映射，表格"诊断标签"列据此渲染。
 */
async function loadDiagLabels(loopIds: string[]) {
  if (loopIds.length === 0) {
    diagLabelMap.value = {};
    return;
  }
  try {
    const data = await getDiagnosisListApi({
      loopIds,
      page: 1,
      pageSize: 100,
    });
    const map: Record<
      string,
      { color: string; label: string; labelCode: string }
    > = {};
    for (const item of data.items ?? []) {
      const labelName =
        item.labelName ||
        getDiagnosisLabelName(item.diagnosisLabel as DiagnosisLabel);
      const color =
        DIAGNOSIS_LABEL_COLOR_MAP[item.diagnosisLabel as DiagnosisLabel] ??
        'default';
      map[item.loopId] = {
        color,
        label: labelName,
        labelCode: item.diagnosisLabel,
      };
    }
    diagLabelMap.value = map;
  } catch {
    // 错误已由拦截器处理；诊断列降级显示"—"
    diagLabelMap.value = {};
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
  // Phase 10 UX 包：pageSize 兜底——用 ?? 保留已有 query.pageSize，
  // 避免 antd 异常时静默退到默认 20 导致已展示数据被截断
  query.pageSize = pagination.pageSize ?? query.pageSize;
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
  } catch {
    // 错误已由拦截器处理
  } finally {
    perfLoading.value = false;
  }
}


function handlePerfWindowChange() {
  loadPerfDetail();
}

// ===== 详情跳转 =====

function viewDetail(record: LoopApi.MonitorListItem) {
  router.push(`/loop/detail/${record.loopId}`);
}

/** D6 入口整合：跳转诊断详情页（诊断标签列点击 + 无诊断回路也可跳转触发新诊断） */
function goDiagnosisDetail(loopId: string) {
  router.push(`/diagnosis/detail/${loopId}`);
}

/** P3 #53: 跳转到回路管理（WS-D 阶段5：修复死链 /loop/tag-mapping → /loop/manage，
 * tag-mapping 路由不存在，统一进入回路管理整合页维护 Tag 关联） */
function goToTagMapping() {
  if (!currentRecord.value) return;
  router.push({
    path: '/loop/manage',
    query: { loopId: currentRecord.value.loopId },
  });
}

// ===== 自动刷新 =====

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
    // Phase 10 UX 包：同时同步状态栏徽标（online/offline/reconnecting）
    wsConnectionUnsubscribe = realtimeWs.onConnectionChange(() => {
      wsConnectionStatus.value = realtimeWs.status;
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

// 整改 C1-2：筛选/分页状态入 URL（刷新/分享/回退不丢巡检上下文）
function applyQueryFromUrl() {
  const q = route.query;
  if (typeof q.plantNodeId === 'string') query.plantNodeId = q.plantNodeId;
  if (typeof q.loopType === 'string') query.loopType = q.loopType;
  if (typeof q.keyword === 'string') query.keyword = q.keyword;
  if (typeof q.page === 'string' && Number(q.page) > 1)
    query.page = Number(q.page);
}

/** 将当前筛选/分页写入 URL query（保留 loopId 深链参数） */
function syncQueryToUrl() {
  const q: Record<string, string> = {};
  if (route.query.loopId) q.loopId = String(route.query.loopId);
  if (query.plantNodeId) q.plantNodeId = query.plantNodeId;
  if (query.loopType) q.loopType = query.loopType;
  if (query.keyword) q.keyword = query.keyword;
  if (query.page > 1) q.page = String(query.page);
  router.replace({ query: q });
}

onMounted(() => {
  applyQueryFromUrl();
  loadPlantNodes();
  loadList();
  loadLoopTypeStats();
  startAutoRefresh();
});

watch(
  () => [query.plantNodeId, query.loopType, query.keyword, query.page],
  syncQueryToUrl,
);

watch(
  () => query.plantNodeId,
  () => {
    query.page = 1;
    loadList();
    loadLoopTypeStats();
  },
);

onUnmounted(() => {
  stopAutoRefresh();
  // P2-05：WebSocket 由全局布局管理（basic.vue onUnmounted 断开），页面只停止轮询
});
</script>

<template>
  <Page>
    <!-- 顶部工具栏：筛选条件 + 操作按钮 + 自动刷新 -->
    <ClpmPageToolbar
      title="回路监控"
      subtitle="列表 + 摘要 + 趋势主画布"
      :loading="loading"
      :last-refresh="lastRefreshText"
    >
      <template #actions>
        <ClpmStandardActions
          :items="toolbarItems"
          :column-configs="columnConfigs"
          @update:columns="handleUpdateColumns"
          @reset-columns="handleResetColumns"
        />
      </template>
    </ClpmPageToolbar>

    <!-- 筛选区（工具栏「筛选」工具可折叠） -->
    <div
      class="clpm-filter-bar"
      :class="{ 'clpm-filter-bar--collapsed': !filterVisible }"
    >
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
      <Button type="primary" @click="handleSearch">查询</Button>
      <div class="!ml-auto flex items-center gap-2 text-sm text-gray-500">
        <span>自动刷新（{{ refreshInterval }}s）</span>
        <Switch :checked="autoRefresh" @change="handleToggleAutoRefresh" />
        <span v-if="autoRefresh" class="text-xs text-gray-400">
          {{ isFallbackPolling ? 'WS 断连，轮询刷新中' : 'WS 实时推送' }}
        </span>
      </div>
    </div>

    <!-- v6.1 新增：统计卡片区域 -->
    <div class="mt-3">
      <Card :body-style="{ padding: '8px 16px' }" class="h-auto">
        <div class="flex items-center justify-between">
          <div class="flex items-center gap-4">
            <div
              class="flex items-center gap-2 px-4 py-1.5 rounded-lg cursor-pointer hover:opacity-80 transition-opacity"
              :style="{
                backgroundColor:
                  query.loopType === ''
                    ? `${themeColors.NEUTRAL}15`
                    : `${themeColors.NEUTRAL}08`,
                borderLeft: `3px solid ${themeColors.NEUTRAL}`,
                borderBottom:
                  query.loopType === ''
                    ? `2px solid ${themeColors.NEUTRAL}`
                    : 'none',
              }"
              @click="handleTypeCardClick('ALL')"
              role="button"
              tabindex="0"
              :aria-pressed="query.loopType === ''"
              @keydown.enter="handleTypeCardClick('ALL')"
              @keydown.space.prevent="handleTypeCardClick('ALL')"
            >
              <span
                class="w-2 h-2 rounded-full"
                :style="{ backgroundColor: themeColors.NEUTRAL }"
              ></span>
              <span class="text-sm text-gray-600 font-medium">全部</span>
              <span
                class="text-sm font-bold"
                :style="{ color: themeColors.NEUTRAL }"
              >
                {{
                  Object.values(loopTypeStats).reduce(
                    (sum, count) => sum + count,
                    0,
                  )
                }}
              </span>
            </div>
            <div
              v-for="(count, key) in loopTypeStats"
              v-show="count > 0"
              :key="key"
              class="flex items-center gap-2 px-3 py-1 rounded cursor-pointer hover:opacity-80 transition-opacity"
              :style="{
                backgroundColor:
                  query.loopType === key
                    ? `${LOOP_TYPE_COLOR_MAP[key]}30`
                    : `${LOOP_TYPE_COLOR_MAP[key]}15`,
                borderLeft: `3px solid ${LOOP_TYPE_COLOR_MAP[key]}`,
                borderBottom:
                  query.loopType === key
                    ? `2px solid ${LOOP_TYPE_COLOR_MAP[key]}`
                    : 'none',
              }"
              @click="handleTypeCardClick(key)"
              role="button"
              tabindex="0"
              :aria-pressed="query.loopType === key"
              @keydown.enter="handleTypeCardClick(key)"
              @keydown.space.prevent="handleTypeCardClick(key)"
            >
              <span
                class="w-2 h-2 rounded-full"
                :style="{ backgroundColor: LOOP_TYPE_COLOR_MAP[key] }"
              ></span>
              <span class="text-sm text-gray-600">
                {{ LOOP_TYPE_LABEL_MAP[key] }}
              </span>
              <span
                class="text-sm font-semibold"
                :style="{ color: LOOP_TYPE_COLOR_MAP[key] }"
              >
                {{ count }}
              </span>
            </div>
          </div>
          <div class="flex items-center gap-4">
            <!-- 自动卡片（MODE ≠ 0） -->
            <div
              class="flex items-center gap-2 px-3 py-1 rounded"
              :style="{
                backgroundColor: `${themeColors.SUCCESS}15`,
                borderLeft: `3px solid ${themeColors.SUCCESS}`,
              }"
            >
              <span
                class="w-2 h-2 rounded-full"
                :style="{ backgroundColor: themeColors.SUCCESS }"
              ></span>
              <span class="text-sm text-gray-600">自动</span>
              <span
                class="text-sm font-semibold"
                :style="{ color: themeColors.SUCCESS }"
                >{{ autoModeCount }}</span
              >
            </div>
            <!-- 手动卡片（MODE = 0） -->
            <div
              class="flex items-center gap-2 px-3 py-1 rounded"
              :style="{
                backgroundColor:
                  manualModeCount > 0
                    ? `${MODE_COLOR_MAP['0']}15`
                    : `${themeColors.NEUTRAL}08`,
                borderLeft: `3px solid ${
                  manualModeCount > 0
                    ? MODE_COLOR_MAP['0']
                    : themeColors.NEUTRAL
                }`,
              }"
            >
              <span
                class="w-2 h-2 rounded-full"
                :style="{
                  backgroundColor:
                    manualModeCount > 0
                      ? MODE_COLOR_MAP['0']
                      : themeColors.NEUTRAL,
                }"
              ></span>
              <span class="text-sm text-gray-600">手动</span>
              <span
                class="text-sm font-semibold"
                :style="{
                  color:
                    manualModeCount > 0
                      ? MODE_COLOR_MAP['0']
                      : themeColors.NEUTRAL,
                }"
                >{{ manualModeCount }}</span
              >
            </div>
            <!-- 自控率卡片 -->
            <div
              class="flex items-center gap-2 px-4 py-1.5 rounded-lg"
              :style="{
                backgroundColor: `${themeColors.ACCENT}15`,
                borderLeft: `3px solid ${themeColors.ACCENT}`,
              }"
            >
              <span
                class="w-2 h-2 rounded-full"
                :style="{ backgroundColor: themeColors.ACCENT }"
              ></span>
              <span class="text-sm text-gray-600 font-medium">自控率</span>
              <span
                class="text-sm font-bold"
                :style="{ color: themeColors.ACCENT }"
                >{{ realtimeControlRate }}%</span
              >
            </div>
            <EchartsUI ref="modeChartRef" style="width: 300px; height: 60px" />
          </div>
        </div>
      </Card>
    </div>

    <!-- 主区：回路列表（全宽，详情/趋势/性能通过 Modal 与跳转访问） -->
    <div class="mt-3 min-h-[calc(100vh-220px)]">
      <ClpmDataCanvas
        title="回路列表"
        :loading="loading"
        :empty="!loading && !errorMessage && monitorList.length === 0"
        empty-text="暂无监控数据"
        empty-reason="可能原因：当前筛选无匹配回路；或回路已创建但本地 TDengine 暂无历史数据、尚未参与评估，可先到数据管理导入历史数据。"
        empty-action-text="去导入数据"
        @empty-action="router.push('/loop/data')"
      >
        <template #extra>
          <!-- 筛选预设与重置偏好（折叠区）；列设置已上移至页面工具栏 -->
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
                      role="button"
                      tabindex="0"
                      :aria-label="`删除预设 ${preset.name}`"
                      @click.stop="handleDeletePreset(preset.id)"
                      @keydown.enter.stop="handleDeletePreset(preset.id)"
                      @keydown.space.stop.prevent="handleDeletePreset(preset.id)"
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
        <!-- Phase 10 UX 包：错误态分离——接口异常时显示内联错误占位，
             避免与"接口正常但无数据"的空态混淆 -->
        <Alert
          v-if="errorMessage"
          type="error"
          show-icon
          :message="`回路监控数据加载失败：${errorMessage}`"
          description="请检查后端服务或稍后重试。错误详情已在页面右上角提示。"
          class="mb-3"
        >
          <template #action>
            <Button size="small" type="link" @click="loadList">重试</Button>
          </template>
        </Alert>
        <Table
          v-if="!errorMessage"
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
          :scroll="{ x: 1570 }"
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
            <template v-if="column.key === 'tagName'">
              <ClpmLoopLink
                :loop-id="(record as LoopApi.MonitorListItem).loopId"
                :tag-name="(record as LoopApi.MonitorListItem).tagName"
                :unit-name="(record as LoopApi.MonitorListItem).unitName"
                :description="(record as LoopApi.MonitorListItem).description"
                default-target="detail"
              />
            </template>
            <!-- v6.1 新增：测量量程 -->
            <template v-else-if="column.key === 'pvRange'">
              <span
                v-if="
                  (record as LoopApi.MonitorListItem).pvRange?.min != null ||
                  (record as LoopApi.MonitorListItem).pvRange?.max != null
                "
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
              <span
                v-if="(record as LoopApi.MonitorListItem).pvUnit"
                class="text-xs text-slate-600"
              >
                {{ (record as LoopApi.MonitorListItem).pvUnit }}
              </span>
              <span v-else class="text-slate-300">—</span>
            </template>
            <template v-else-if="column.key === 'loopType'">
              <Tag class="clpm-tag-neutral m-0">
                {{
                  LOOP_TYPE_LABEL_MAP[
                    (record as LoopApi.MonitorListItem).loopType ?? 'OTHER'
                  ] ?? '其他'
                }}
              </Tag>
            </template>
            <template v-else-if="column.key === 'sp'">
              <span
                v-if="
                  (record as LoopApi.MonitorListItem).currentValues?.sp != null
                "
                class="flex items-baseline justify-end gap-1"
              >
                <ClpmNumeric
                  :value="(record as LoopApi.MonitorListItem).currentValues?.sp"
                  :precision="2"
                  mono
                  size="sm"
                />
              </span>
              <span v-else class="text-gray-400">—</span>
            </template>
            <template v-else-if="column.key === 'pv'">
              <span
                v-if="
                  (record as LoopApi.MonitorListItem).currentValues?.pv != null
                "
                class="flex items-baseline justify-end gap-1"
              >
                <ClpmNumeric
                  :value="(record as LoopApi.MonitorListItem).currentValues?.pv"
                  :precision="2"
                  mono
                  size="sm"
                  :weight="600"
                />
              </span>
              <span v-else class="text-gray-400">—</span>
            </template>
            <template v-else-if="column.key === 'op'">
              <span
                v-if="
                  (record as LoopApi.MonitorListItem).currentValues?.op != null
                "
                class="flex items-baseline justify-end gap-0.5"
              >
                <ClpmNumeric
                  :value="(record as LoopApi.MonitorListItem).currentValues?.op"
                  :precision="2"
                  mono
                  size="sm"
                />
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
            <!-- D6 入口整合：诊断标签列——展示最新诊断标签彩色 Tag，点击跳转诊断详情 -->
            <template v-else-if="column.key === 'diagLabel'">
              <span
                v-if="diagLabelMap[(record as LoopApi.MonitorListItem).loopId]"
                class="inline-flex items-center gap-1"
              >
                <Tag
                  :color="
                    diagLabelMap[(record as LoopApi.MonitorListItem).loopId]!
                      .color
                  "
                  class="m-0 cursor-pointer hover:opacity-80"
                  @click="
                    goDiagnosisDetail(
                      (record as LoopApi.MonitorListItem).loopId,
                    )
                  "
                >
                  {{
                    diagLabelMap[(record as LoopApi.MonitorListItem).loopId]!
                      .label
                  }}
                </Tag>
                <ClpmInfoTip
                  v-if="
                    (
                      DIAGNOSIS_TERM_EXPLANATIONS as Record<
                        string,
                        { term: string; short: string; detail?: string }
                      >
                    )[
                      diagLabelMap[(record as LoopApi.MonitorListItem).loopId]!
                        .labelCode
                    ]
                  "
                  :tip="
                    (
                      DIAGNOSIS_TERM_EXPLANATIONS as Record<
                        string,
                        { term: string; short: string; detail?: string }
                      >
                    )[
                      diagLabelMap[(record as LoopApi.MonitorListItem).loopId]!
                        .labelCode
                    ]!.short
                  "
                />
              </span>
              <span
                v-else
                class="text-gray-400 cursor-pointer hover:text-blue-500"
                title="暂无诊断记录，点击进入诊断详情触发新诊断"
                role="button"
                tabindex="0"
                aria-label="进入诊断详情"
                @click="
                  goDiagnosisDetail((record as LoopApi.MonitorListItem).loopId)
                "
                @keydown.enter="
                  goDiagnosisDetail((record as LoopApi.MonitorListItem).loopId)
                "
                @keydown.space.prevent="
                  goDiagnosisDetail((record as LoopApi.MonitorListItem).loopId)
                "
              >
                —
              </span>
            </template>
            <!-- 数据健康度（方案 A §5）：可信度 + 预处理有效率（PV 完整度已与 PV 数值列重复，隐藏） -->
            <template v-else-if="column.key === 'dataHealth'">
              <ClpmDataHealthBadges
                :health="(record as LoopApi.MonitorListItem).dataHealth"
                :show-pv-completeness="false"
              />
            </template>
            <template v-else-if="column.key === 'action'">
              <!-- 整改 A-14：彩色 Tag 按钮墙 → 安静文字链接 -->
              <div class="flex items-center gap-1">
                <Button
                  type="link"
                  size="small"
                  @click="viewDetail(record as LoopApi.MonitorListItem)"
                  >详情</Button
                >
                <Button
                  type="link"
                  size="small"
                  @click="openTrend(record as LoopApi.MonitorListItem)"
                  >趋势</Button
                >
                <Button
                  type="link"
                  size="small"
                  @click="openPerformance(record as LoopApi.MonitorListItem)"
                  >性能</Button
                >
              </div>
            </template>
          </template>
        </Table>
      </ClpmDataCanvas>
    </div>

    <!-- StatusFooter：最近刷新/数据延迟/自动刷新状态/WS在线状态/选中回路 -->
    <div class="clpm-status-footer">
      <span>最近刷新：{{ lastRefreshText || '尚未刷新' }}</span>
      <span class="clpm-status-footer__divider">·</span>
      <span>数据延迟：{{ dataDelayText || '—' }}</span>
      <span class="clpm-status-footer__divider">·</span>
      <span>
        自动刷新：
        <strong :class="autoRefresh ? 'is-active' : 'is-muted'">
          {{
            autoRefresh
              ? isFallbackPolling
                ? `开启（轮询 ${refreshInterval}s）`
                : '开启（WS 实时）'
              : '关闭'
          }}
        </strong>
      </span>
      <span class="clpm-status-footer__divider">·</span>
      <!-- Phase 10 UX 包：WS 在线状态徽标（online/offline/reconnecting） -->
      <span class="flex items-center gap-1">
        <span
          class="inline-block w-2 h-2 rounded-full"
          :style="{
            backgroundColor:
              wsConnectionStatus === 'online'
                ? themeColors.SUCCESS
                : wsConnectionStatus === 'reconnecting'
                  ? themeColors.WARNING
                  : themeColors.NEUTRAL,
          }"
        ></span>
        <span class="text-xs">
          {{
            wsConnectionStatus === 'online'
              ? 'WS 在线'
              : wsConnectionStatus === 'reconnecting'
                ? 'WS 重连中'
                : 'WS 离线'
          }}
        </span>
      </span>
      <span class="clpm-status-footer__divider">·</span>
      <span>选中回路：{{ selectedLoop?.tagName ?? '—' }}</span>
    </div>

    <!-- 趋势 Modal（#3: ClpmModal 深色标题栏 + 拖动 + 最大化/复位） -->
    <ClpmModal
      v-model:open="trendModalVisible"
      :title="`趋势 - ${currentRecord?.tagName ?? ''}`"
      width="1100px"
      :footer="null"
      destroy-on-close
      @maximize-change="handleTrendMaximizeChange"
    >
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
              <span
                v-if="trendDetail.currentValues.pv != null"
                class="ml-2 flex items-baseline gap-1"
              >
                <ClpmNumeric
                  :value="trendDetail.currentValues.pv"
                  :precision="2"
                  mono
                  size="sm"
                  :weight="600"
                />
                <span class="text-xs text-gray-500">{{
                  trendDetail.currentValues.unit
                }}</span>
              </span>
              <span v-else class="ml-2 text-gray-400">—</span>
            </div>
            <div>
              <span class="text-xs text-gray-400">SP</span>
              <span
                v-if="trendDetail.currentValues.sp != null"
                class="ml-2 flex items-baseline gap-1"
              >
                <ClpmNumeric
                  :value="trendDetail.currentValues.sp"
                  :precision="2"
                  mono
                  size="sm"
                  :weight="600"
                />
                <span class="text-xs text-gray-500">{{
                  trendDetail.currentValues.unit
                }}</span>
              </span>
              <span v-else class="ml-2 text-gray-400">—</span>
            </div>
            <div>
              <span class="text-xs text-gray-400">OP</span>
              <span
                v-if="trendDetail.currentValues.op != null"
                class="ml-2 flex items-baseline gap-0.5"
              >
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
    </ClpmModal>

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
            <!-- 整改 A-06：gauge 退役，子弹图（值条+分档区间带） -->
            <div style="width: 280px">
              <ClpmBulletChart
                label="综合性能指数"
                :value="perfDetail.kpiSummary.composite_score ?? null"
                unit="分"
              />
            </div>
            <div class="flex-1">
              <div class="text-sm text-gray-500">
                综合性能指数（composite_score）
              </div>
              <div
                class="mt-1 text-3xl font-bold"
                :style="{
                  color: isPerfInconclusive
                    ? themeColors.NEUTRAL
                    : (perfDetail.kpiSummary.composite_score ?? 0) >= 80
                      ? themeColors.SUCCESS
                      : (perfDetail.kpiSummary.composite_score ?? 0) >= 60
                        ? themeColors.WARNING
                        : themeColors.DANGER,
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
                      ? `${themeColors.SUCCESS}0F`
                      : (perfDetail.kpiSummary[item.key] as number) >= 60
                        ? `${themeColors.WARNING}0F`
                        : `${themeColors.DANGER}0F`,
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
                        ? themeColors.NEUTRAL
                        : (perfDetail.kpiSummary[item.key] as number) >= 80
                          ? themeColors.SUCCESS
                          : (perfDetail.kpiSummary[item.key] as number) >= 60
                            ? themeColors.WARNING
                            : themeColors.DANGER,
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
                  v-if="
                    (perfDetail.kpiSummary[item.key] as null | number) != null
                  "
                  class="h-full rounded-full transition-all"
                  :style="{
                    width: `${perfDetail.kpiSummary[item.key]}%`,
                    backgroundColor:
                      (perfDetail.kpiSummary[item.key] as number) >= 80
                        ? themeColors.SUCCESS
                        : (perfDetail.kpiSummary[item.key] as number) >= 60
                          ? themeColors.WARNING
                          : themeColors.DANGER,
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
  transition:
    visibility 0.2s ease,
    opacity 0.2s ease;
}

:deep(.ant-table-row):hover .loop-row-actions {
  visibility: visible;
  opacity: 1;
}
</style>
