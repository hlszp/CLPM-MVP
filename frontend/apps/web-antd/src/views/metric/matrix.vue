<script lang="ts" setup>
/**
 * 指标矩阵 — 全回路 × 全指标集中查看（/metric/matrix，评估模块第 6 页）
 *
 * 设计依据：docs/MVP设计/15-回路指标矩阵页设计方案.md（2026-08-27 v1.0）
 * - 行=回路、列=指标，4 指标组 Segmented 切换（核心/诊断/统计/阀门）
 * - 率类指标分档着色（正向 90/80/60；反向振荡/饱和/仪表故障率阈值取反），
 *   NULL 斜纹中性占位（严禁红色），诊断数值类与统计/阀门组不着色
 * - 单元格点击 → 该回路该指标历史趋势抽屉（复用快照分页接口）
 * - 列头趋势入口 → TOP N 薄弱回路多回路历史折线叠加（metric-series 批量接口）
 * - 列头漏斗 → 仅显示该指标薄弱回路（前端过滤当前页）
 * - 服务端排序仅 SNAPSHOT_SORT_COLUMNS 白名单 7 指标，其余列前端排序当前页
 * - URL query 为真相源（tab/window/plantNodeId/loopId），replace 不重建实例
 */
import type { TableColumnsType } from 'ant-design-vue';

import type { EchartsUIType } from '@vben/plugins/echarts';

import type {
  KpiSnapshotItem,
  KpiSnapshotListResult,
  MetricSeriesKey,
} from '#/api/metric';

import { computed, nextTick, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';
import { IconifyIcon } from '@vben/icons';
import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import {
  Button,
  Drawer,
  message,
  Modal,
  Segmented,
  Select,
  Table,
  Tooltip,
  TreeSelect,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import { getLoopListApi } from '#/api/loop';
import {
  getLoopMetricSeriesApi,
  getLoopSnapshotsApi,
} from '#/api/metric';
import { getPlantNodeTreeApi } from '#/api/plant-node';
import {
  ClpmDataCanvas,
  ClpmPageToolbar,
  ClpmStandardActions,
} from '#/components/clpm';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import { useEchartsPreset } from '#/composables/use-echarts-preset';
import { MULTI_SERIES_PALETTE } from '#/composables/use-loop-palettes';
import { showPageHelp, usePageToolbar } from '#/composables/use-page-toolbar';

defineOptions({ name: 'MetricMatrix' });

const { isDark, themeColors } = useClpmTheme();
const { axisBase, getTooltipPreset } = useEchartsPreset();
const route = useRoute();
const router = useRouter();

// ===========================================================================
// 指标定义（4 组，field 对齐 KpiSnapshotItem camelCase 返回体）
// ===========================================================================

type MetricGroupId = 'core' | 'diagnosis' | 'stats' | 'valve';

/** 着色规则：grade=正向定级；reverse=反向指标（低=好，自定义三档阈值） */
type ColorRule =
  | { good: number; kind: 'reverse'; ok: number; warn: number }
  | { kind: 'grade' };

interface MetricDef {
  /** 着色规则；null=不着色（诊断数值类 / 统计 / 阀门 DISPLAY_ONLY） */
  color: ColorRule | null;
  /** 返回体字段（camelCase） */
  field: string;
  /** 所属指标组 */
  group: MetricGroupId;
  /** 数值精度 */
  precision: number;
  /** metric-series 批量序列键；null=无批量序列（统计/阀门组） */
  seriesKey: MetricSeriesKey | null;
  /** 服务端排序键（SNAPSHOT_SORT_COLUMNS 白名单）；null=仅前端排序 */
  sortKey: null | string;
  /** 中文标题 */
  title: string;
  /** 单位 */
  unit: '%' | '' | 's';
}

const METRIC_DEFS: MetricDef[] = [
  // --- 核心组（9）：评分 + 六率 + 反向二率 ---
  {
    field: 'score',
    seriesKey: 'score',
    sortKey: 'score',
    title: '综合评分',
    unit: '',
    group: 'core',
    precision: 1,
    color: { kind: 'grade' },
  },
  {
    field: 'goodValueRate',
    seriesKey: 'good_value_rate',
    sortKey: 'good_value_rate',
    title: '好值率',
    unit: '%',
    group: 'core',
    precision: 1,
    color: { kind: 'grade' },
  },
  {
    field: 'autoModeRate',
    seriesKey: 'auto_mode_rate',
    sortKey: 'auto_mode_rate',
    title: '自控率',
    unit: '%',
    group: 'core',
    precision: 1,
    color: { kind: 'grade' },
  },
  {
    field: 'effectiveAutoRate',
    seriesKey: 'effective_auto_rate',
    sortKey: 'effective_auto_rate',
    title: '有效自控率',
    unit: '%',
    group: 'core',
    precision: 1,
    color: { kind: 'grade' },
  },
  {
    field: 'steadyRate',
    seriesKey: 'steady_rate',
    sortKey: 'steady_rate',
    title: '平稳率',
    unit: '%',
    group: 'core',
    precision: 1,
    color: { kind: 'grade' },
  },
  {
    field: 'accuracyRate',
    seriesKey: 'accuracy_rate',
    sortKey: 'accuracy_rate',
    title: '准确率',
    unit: '%',
    group: 'core',
    precision: 1,
    color: { kind: 'grade' },
  },
  {
    field: 'fastRate',
    seriesKey: 'fast_rate',
    sortKey: 'fast_rate',
    title: '快速率',
    unit: '%',
    group: 'core',
    precision: 1,
    color: { kind: 'grade' },
  },
  {
    field: 'oscillationRate',
    seriesKey: 'oscillation_rate',
    sortKey: null,
    title: '振荡率',
    unit: '%',
    group: 'core',
    precision: 1,
    // 反向指标（与工作台热力矩阵口径一致：<5 优 / <15 良 / <30 中 / ≥30 差）
    color: { good: 5, kind: 'reverse', ok: 15, warn: 30 },
  },
  {
    field: 'saturationRate',
    seriesKey: 'saturation_rate',
    sortKey: null,
    title: '饱和率',
    unit: '%',
    group: 'core',
    precision: 1,
    color: { good: 5, kind: 'reverse', ok: 15, warn: 30 },
  },
  // --- 诊断组（6）---
  {
    field: 'instrumentFaultRate',
    seriesKey: 'instrument_fault_rate',
    sortKey: null,
    title: '仪表故障率',
    unit: '%',
    group: 'diagnosis',
    precision: 2,
    color: { good: 1.5, kind: 'reverse', ok: 3, warn: 5 },
  },
  {
    field: 'stictionIndex',
    seriesKey: 'stiction_index',
    sortKey: null,
    title: '粘滞指数',
    unit: '',
    group: 'diagnosis',
    precision: 2,
    color: null,
  },
  {
    field: 'settlingTime',
    seriesKey: 'settling_time',
    sortKey: null,
    title: '稳态时间',
    unit: 's',
    group: 'diagnosis',
    precision: 1,
    color: null,
  },
  {
    field: 'idealSettlingTime',
    seriesKey: null,
    sortKey: null,
    title: '理想稳态时间',
    unit: 's',
    group: 'diagnosis',
    precision: 1,
    color: null,
  },
  {
    field: 'outputTravelIndex',
    seriesKey: 'output_trip_index',
    sortKey: null,
    title: '行程指数',
    unit: '',
    group: 'diagnosis',
    precision: 1,
    color: null,
  },
  {
    field: 'timeConstant',
    seriesKey: null,
    sortKey: null,
    title: '时间常数',
    unit: 's',
    group: 'diagnosis',
    precision: 1,
    color: null,
  },
  // --- 统计组（6）：DISPLAY_ONLY，不着色 ---
  {
    field: 'pvMean',
    seriesKey: null,
    sortKey: null,
    title: 'PV 均值',
    unit: '',
    group: 'stats',
    precision: 2,
    color: null,
  },
  {
    field: 'pvStd',
    seriesKey: null,
    sortKey: null,
    title: 'PV 标准差',
    unit: '',
    group: 'stats',
    precision: 2,
    color: null,
  },
  {
    field: 'spMean',
    seriesKey: null,
    sortKey: null,
    title: 'SP 均值',
    unit: '',
    group: 'stats',
    precision: 2,
    color: null,
  },
  {
    field: 'spStd',
    seriesKey: null,
    sortKey: null,
    title: 'SP 标准差',
    unit: '',
    group: 'stats',
    precision: 2,
    color: null,
  },
  {
    field: 'opMean',
    seriesKey: null,
    sortKey: null,
    title: 'OP 均值',
    unit: '',
    group: 'stats',
    precision: 2,
    color: null,
  },
  {
    field: 'opStd',
    seriesKey: null,
    sortKey: null,
    title: 'OP 标准差',
    unit: '',
    group: 'stats',
    precision: 2,
    color: null,
  },
  // --- 阀门组（6）：DISPLAY_ONLY，不着色 ---
  {
    field: 'valveLinearity',
    seriesKey: null,
    sortKey: null,
    title: '阀门线性度',
    unit: '',
    group: 'valve',
    precision: 3,
    color: null,
  },
  {
    field: 'valveNonlinearity',
    seriesKey: null,
    sortKey: null,
    title: '阀门非线性度',
    unit: '',
    group: 'valve',
    precision: 3,
    color: null,
  },
  {
    field: 'valveOpMin',
    seriesKey: null,
    sortKey: null,
    title: 'OP 下限',
    unit: '',
    group: 'valve',
    precision: 2,
    color: null,
  },
  {
    field: 'valveOpMax',
    seriesKey: null,
    sortKey: null,
    title: 'OP 上限',
    unit: '',
    group: 'valve',
    precision: 2,
    color: null,
  },
  {
    field: 'oscillationAmplitude',
    seriesKey: null,
    sortKey: null,
    title: '振荡幅值',
    unit: '',
    group: 'valve',
    precision: 2,
    color: null,
  },
  {
    field: 'setpointCrossingCount',
    seriesKey: null,
    sortKey: null,
    title: '设定值穿越',
    unit: '',
    group: 'valve',
    precision: 0,
    color: null,
  },
];

const GROUP_OPTIONS: { label: string; value: MetricGroupId }[] = [
  { label: '核心', value: 'core' },
  { label: '诊断', value: 'diagnosis' },
  { label: '统计', value: 'stats' },
  { label: '阀门', value: 'valve' },
];

const WINDOW_OPTIONS: { label: string; value: string }[] = [
  { label: '最新', value: 'latest' },
  { label: '8h', value: '8' },
  { label: '24h', value: '24' },
  { label: '72h', value: '72' },
  { label: '168h', value: '168' },
];

const defByField = new Map(METRIC_DEFS.map((d) => [d.field, d]));
const groupDefs = (g: MetricGroupId) => METRIC_DEFS.filter((d) => d.group === g);

// ===========================================================================
// 状态
// ===========================================================================

const loading = ref(false);
const loadError = ref(false);
const rows = ref<KpiSnapshotItem[]>([]);
const totalCount = ref(0);
const currentPage = ref(1);
const pageSize = ref(50);

const activeGroup = ref<MetricGroupId>('core');
const windowValue = ref('latest');
const filterPlantNodeId = ref<string | undefined>();
const filterLoopId = ref<string | undefined>();

/** 列筛选：非空时仅显示该指标薄弱回路（当前页，前端过滤） */
const filterColumn = ref<null | string>(null);

/** 服务端排序（白名单 7 指标） */
const sortBy = ref<null | string>(null);
const sortOrder = ref<'asc' | 'desc' | null>(null);

const plantNodeTree = ref<any[]>([]);
const loopOptions = ref<{ label: string; value: string }[]>([]);

// ===========================================================================
// 着色（Calm UI：弱色底 + 深色字，透明度 ≤ 0.35）
// ===========================================================================

/** hex → rgba 弱色底 */
function withAlpha(hex: string, alpha: number): string {
  const v = Math.round(Math.max(0, Math.min(1, alpha)) * 255)
    .toString(16)
    .padStart(2, '0');
  return `${hex}${v}`;
}

/** 命中档位语义色；值无效/规则缺失 → null（不着色） */
function metricColor(rule: ColorRule | null, value: null | number): null | string {
  if (!rule || value === null || value === undefined || Number.isNaN(value)) {
    return null;
  }
  const c = themeColors.value;
  if (rule.kind === 'grade') {
    if (value >= 90) return c.SUCCESS;
    if (value >= 80) return c.INFO;
    if (value >= 60) return c.WARNING;
    return c.DANGER;
  }
  if (value < rule.good) return c.SUCCESS;
  if (value < rule.ok) return c.INFO;
  if (value < rule.warn) return c.WARNING;
  return c.DANGER;
}

/** 单元格样式（着色指标弱色底 + 深色字） */
function cellStyle(field: string, value: null | number) {
  const def = defByField.get(field);
  const color = def ? metricColor(def.color, value) : null;
  if (!color) return {};
  return {
    backgroundColor: withAlpha(color, isDark.value ? 0.22 : 0.14),
    color,
  };
}

/** 列筛选阈值：正向=低于"中"档下界（60）；反向=达到差档（warn 及以上） */
function isWeakLoop(def: MetricDef, value: null | number): boolean {
  if (value === null || value === undefined || Number.isNaN(value)) return false;
  if (!def.color) return false;
  if (def.color.kind === 'grade') return value < 60;
  return value >= def.color.warn;
}

function formatCell(def: MetricDef, value: null | number | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  return `${value.toFixed(def.precision)}${def.unit}`;
}

// ===========================================================================
// 列定义
// ===========================================================================

const columns = computed<TableColumnsType>(() => {
  const metricCols = groupDefs(activeGroup.value).map((def) => ({
    title: def.unit ? `${def.title}(${def.unit})` : def.title,
    key: def.field,
    dataIndex: def.field,
    width: def.group === 'core' ? 88 : 96,
    ellipsis: true,
    // 服务端白名单列：受控排序（不激活时 antd 不做本地排序，由 handleTableChange 接管）
    ...(def.sortKey
      ? {
          sorter: true,
          sortOrder: (() => {
            if (sortBy.value !== def.sortKey || !sortOrder.value) return null;
            return sortOrder.value === 'asc' ? 'ascend' : 'descend';
          })(),
        }
      : { sorter: true }),
  }));
  return [
    {
      title: '回路',
      key: 'loopTagName',
      dataIndex: 'loopTagName',
      width: 160,
      fixed: 'left',
      ellipsis: true,
    },
    ...metricCols,
    {
      title: '操作',
      key: 'action',
      width: 70,
      fixed: 'right',
    },
  ];
});

/** 展示行：列筛选开启时前端过滤当前页薄弱回路 */
const displayRows = computed(() => {
  if (!filterColumn.value) return rows.value;
  const def = defByField.get(filterColumn.value);
  if (!def) return rows.value;
  return rows.value.filter((row) => {
    const v = (row as any)[def.field] as null | number;
    return isWeakLoop(def, v);
  });
});

// ===========================================================================
// 数据加载
// ===========================================================================

/** 当前时间窗范围（趋势图用：latest 时趋势默认 24h） */
function currentWindowRange(): { endTime: string; startTime: string } {
  const hours = windowValue.value === 'latest' ? 24 : Number(windowValue.value);
  const end = dayjs();
  const start = end.subtract(hours, 'hour');
  return { startTime: start.toISOString(), endTime: end.toISOString() };
}

async function loadList() {
  loading.value = true;
  loadError.value = false;
  try {
    const params: any = {
      page: currentPage.value,
      pageSize: pageSize.value,
      latestOnly: true,
    };
    if (filterLoopId.value) params.loopId = filterLoopId.value;
    if (filterPlantNodeId.value) params.plantNodeId = filterPlantNodeId.value;
    if (windowValue.value !== 'latest') {
      const { startTime, endTime } = currentWindowRange();
      params.startTime = startTime;
      params.endTime = endTime;
    }
    if (sortBy.value && sortOrder.value) {
      params.sortBy = sortBy.value;
      params.sortOrder = sortOrder.value;
    }
    const result: KpiSnapshotListResult = await getLoopSnapshotsApi(params);
    rows.value = result.items;
    totalCount.value = result.total;
  } catch (error: any) {
    loadError.value = true;
    console.error('加载指标矩阵失败:', error);
    message.error(error?.message || '加载失败');
  } finally {
    loading.value = false;
  }
}

async function loadPlantNodeTree() {
  try {
    const data = await getPlantNodeTreeApi();
    plantNodeTree.value = data || [];
  } catch {
    plantNodeTree.value = [];
  }
}

/** 后端 loops API pageSize 上限 100，循环分页加载全部回路 */
async function loadLoops(plantNodeId?: string) {
  try {
    const allLoops: any[] = [];
    let page = 1;
    const loopPageSize = 100;
    let total = 0;
    do {
      const params: any = { page, pageSize: loopPageSize };
      if (plantNodeId) params.plantNodeId = plantNodeId;
      const result = await getLoopListApi(params);
      total = result.total;
      allLoops.push(...(result.items || []));
      page += 1;
    } while ((page - 1) * loopPageSize < total);
    loopOptions.value = allLoops.map((l: any) => ({
      label: l.tagName,
      value: l.loopId,
    }));
  } catch {
    loopOptions.value = [];
  }
}

function handlePlantNodeChange(value: string | undefined) {
  filterLoopId.value = undefined;
  loadLoops(value);
  currentPage.value = 1;
  loadList();
  syncRouteQuery();
}

function handleTableChange(p: any, _filters: any, sorter: any) {
  currentPage.value = p.current;
  pageSize.value = p.pageSize;
  const s = Array.isArray(sorter) ? sorter[0] : sorter;
  const field = s?.columnKey ?? s?.field;
  const def = field ? defByField.get(String(field)) : null;
  if (def?.sortKey && s?.order) {
    // 服务端排序（白名单列）
    sortBy.value = def.sortKey;
    sortOrder.value = s.order === 'ascend' ? 'asc' : 'desc';
  } else {
    sortBy.value = null;
    sortOrder.value = null;
  }
  loadList();
}

// ===========================================================================
// 单元格交互：Tooltip + 抽屉趋势（单回路 × 单指标）
// ===========================================================================

const cellDrawerVisible = ref(false);
const cellDrawerLoading = ref(false);
const cellDrawerDef = ref<MetricDef | null>(null);
const cellDrawerRow = ref<KpiSnapshotItem | null>(null);
const cellChartRef = ref<EchartsUIType>();
const { renderEcharts: renderCellChart } = useEcharts(cellChartRef);

function cellTooltip(def: MetricDef, row: any): string {
  const v = row[def.field] as null | number;
  const value = formatCell(def, v);
  const ts = row.tsEnd || row.tsStart || '';
  return `${def.title}: ${value} · 可信度 ${row.confidenceLevel ?? '—'} · 窗口 ${ts ? dayjs(ts).format('MM-DD HH:mm') : '—'}`;
}

async function openCellDrawer(field: string, row: any) {
  const def = defByField.get(field);
  if (!def || !row.loopId) return;
  cellDrawerDef.value = def;
  cellDrawerRow.value = row;
  cellDrawerVisible.value = true;
  cellDrawerLoading.value = true;
  try {
    const { startTime, endTime } = currentWindowRange();
    // 后端 pageSize 上限 100，循环分页拉取时间窗内全部快照
    const allItems: KpiSnapshotItem[] = [];
    let page = 1;
    const pageLimit = 100;
    let total = 0;
    do {
      const result = await getLoopSnapshotsApi({
        loopId: row.loopId,
        startTime,
        endTime,
        latestOnly: false,
        page,
        pageSize: pageLimit,
      });
      allItems.push(...(result.items || []));
      total = result.total ?? 0;
      page += 1;
    } while ((page - 1) * pageLimit < total);
    const sorted = allItems.toSorted((a, b) => {
      const aTs = a.tsStart || '';
      const bTs = b.tsStart || '';
      return aTs.localeCompare(bTs);
    });
    await nextTick();
    renderCellTrend(def, sorted);
  } catch (error) {
    console.error('加载单元格趋势失败:', error);
  } finally {
    cellDrawerLoading.value = false;
  }
}

function renderCellTrend(def: MetricDef, snapshots: KpiSnapshotItem[]) {
  const xLabels = snapshots.map((s) =>
    s.tsStart ? dayjs(s.tsStart).format('MM-DD HH:mm') : '',
  );
  const values = snapshots.map((s) => {
    const v = (s as any)[def.field] as null | number;
    return typeof v === 'number' ? v : null;
  });
  const textColor = themeColors.value.NEUTRAL;
  renderCellChart({
    tooltip: { ...getTooltipPreset(), trigger: 'axis' },
    grid: { top: 24, right: 24, bottom: 40, left: 48, containLabel: true },
    xAxis: { ...axisBase.value, type: 'category', data: xLabels },
    yAxis: { ...axisBase.value, type: 'value', scale: true },
    series: [
      {
        name: def.title,
        type: 'line',
        data: values,
        smooth: true,
        symbol: 'circle',
        symbolSize: 4,
        connectNulls: false,
        lineStyle: { color: themeColors.value.INFO, width: 2 },
        itemStyle: { color: themeColors.value.INFO },
      },
    ],
    textStyle: { color: textColor },
  });
}

// ===========================================================================
// 列头交互：列筛选 + 趋势弹层（单指标 × TOP N 多回路）
// ===========================================================================

function toggleColumnFilter(field: string) {
  filterColumn.value = filterColumn.value === field ? null : field;
}

/** 趋势对比弹层状态 */
const trendModalVisible = ref(false);
const trendModalLoading = ref(false);
const trendModalDef = ref<MetricDef | null>(null);
const trendChartRef = ref<EchartsUIType>();
const { renderEcharts: renderTrendChart } = useEcharts(trendChartRef);

/** TOP N 趋势折线调色板（共享分类色板，见 use-loop-palettes） */
const TREND_PALETTE = MULTI_SERIES_PALETTE;

/** 取当前页该指标最差的 N 个回路（正向=最低，反向=最高；NULL 不参与） */
function topWeakLoops(def: MetricDef, n: number): KpiSnapshotItem[] {
  const valid = rows.value.filter((row) => {
    const v = (row as any)[def.field] as null | number;
    return typeof v === 'number';
  });
  const reverse = def.color?.kind === 'reverse';
  return valid
    .toSorted((a, b) => {
      const av = (a as any)[def.field] as number;
      const bv = (b as any)[def.field] as number;
      return reverse ? bv - av : av - bv;
    })
    .slice(0, n);
}

async function openColumnTrend(field: string) {
  const def = defByField.get(field);
  if (!def || !def.seriesKey) return;
  const targets = topWeakLoops(def, 10);
  if (targets.length === 0) {
    message.warning('当前数据无有效值，无法对比趋势');
    return;
  }
  trendModalDef.value = def;
  trendModalVisible.value = true;
  trendModalLoading.value = true;
  try {
    const { startTime, endTime } = currentWindowRange();
    const result = await getLoopMetricSeriesApi({
      loopIds: targets.map((t) => t.loopId!).join(','),
      metricKey: def.seriesKey,
      startTime,
      endTime,
    });
    await nextTick();
    renderTrend(result.series);
  } catch (error) {
    console.error('加载多回路趋势失败:', error);
  } finally {
    trendModalLoading.value = false;
  }
}

function renderTrend(
  series: { loopTagName: null | string; points: { ts: null | string; value: null | number }[] }[],
) {
  const textColor = themeColors.value.NEUTRAL;
  renderTrendChart({
    tooltip: {
      ...getTooltipPreset(),
      trigger: 'axis',
      axisPointer: { type: 'cross' },
    },
    legend: {
      data: series.map((s) => s.loopTagName ?? '?'),
      top: 0,
      type: 'scroll',
      textStyle: { color: textColor, fontSize: 11 },
    },
    grid: { top: 40, right: 24, bottom: 40, left: 48, containLabel: true },
    xAxis: {
      ...axisBase.value,
      type: 'category',
      data: (series[0]?.points ?? []).map((p) =>
        p.ts ? dayjs(p.ts).format('MM-DD HH:mm') : '',
      ),
    },
    yAxis: { ...axisBase.value, type: 'value', scale: true },
    series: series.map((s, i) => ({
      name: s.loopTagName ?? '?',
      type: 'line',
      data: s.points.map((p) => (typeof p.value === 'number' ? p.value : null)),
      smooth: true,
      symbol: 'none',
      lineStyle: { color: TREND_PALETTE[i % TREND_PALETTE.length], width: 1.6 },
      itemStyle: { color: TREND_PALETTE[i % TREND_PALETTE.length] },
    })),
    textStyle: { color: textColor },
  });
}

/** 行尾"详情"：跳转回路性能页（深链 ?loopId= 预过滤） */
function goLoopDetail(row: any) {
  if (!row.loopId) return;
  router.push({ path: '/metric/loop-performance', query: { loopId: row.loopId } });
}

// ===========================================================================
// URL query 同步（tab/window/plantNodeId/loopId，replace 不重建实例）
// ===========================================================================

const GROUP_VALUES = new Set(GROUP_OPTIONS.map((g) => g.value as string));
const WINDOW_VALUES = new Set(WINDOW_OPTIONS.map((w) => w.value));

function applyRouteQuery() {
  const q = route.query;
  if (typeof q.tab === 'string' && GROUP_VALUES.has(q.tab)) {
    activeGroup.value = q.tab as MetricGroupId;
  }
  if (typeof q.window === 'string' && WINDOW_VALUES.has(q.window)) {
    windowValue.value = q.window;
  }
  if (typeof q.plantNodeId === 'string' && q.plantNodeId) {
    filterPlantNodeId.value = q.plantNodeId;
  }
  if (typeof q.loopId === 'string' && q.loopId) {
    filterLoopId.value = q.loopId;
  }
}

function syncRouteQuery() {
  router.replace({
    query: {
      ...(activeGroup.value === 'core' ? {} : { tab: activeGroup.value }),
      ...(windowValue.value === 'latest' ? {} : { window: windowValue.value }),
      ...(filterPlantNodeId.value ? { plantNodeId: filterPlantNodeId.value } : {}),
      ...(filterLoopId.value ? { loopId: filterLoopId.value } : {}),
    },
  });
}

watch([activeGroup, windowValue], () => {
  // 组/窗切换重置分页与列筛选后重查
  currentPage.value = 1;
  filterColumn.value = null;
  loadList();
  syncRouteQuery();
});

// ===========================================================================
// 工具栏与生命周期
// ===========================================================================

async function refresh() {
  await Promise.all([loadPlantNodeTree(), loadLoops(filterPlantNodeId.value)]);
  await loadList();
}

const { toolbarItems } = usePageToolbar(() => ({
  refresh: { onClick: refresh, loading: loading.value },
  help: {
    onClick: () =>
      showPageHelp({
        title: '指标矩阵 帮助',
        content:
          '本页以矩阵形式集中展示全部回路的各项计算指标（核心/诊断/统计/阀门 4 组）。' +
          '率类指标按分档着色（绿≥90/蓝≥80/黄≥60/红<60，振荡/饱和/仪表故障率反向），' +
          '斜纹为该窗口无有效值。点击单元格查看该回路该指标历史趋势；' +
          '点击列头折线图标对比 TOP10 薄弱回路趋势；漏斗图标仅显示薄弱回路（当前页）。',
      }),
  },
}));

// 主题切换重渲图表
watch(isDark, () => {
  if (trendModalVisible.value) {
    const def = trendModalDef.value;
    if (def) openColumnTrend(def.field);
  }
  if (cellDrawerVisible.value && cellDrawerDef.value && cellDrawerRow.value) {
    openCellDrawer(cellDrawerDef.value.field, cellDrawerRow.value);
  }
});

onMounted(() => {
  applyRouteQuery();
  loadPlantNodeTree();
  loadLoops(filterPlantNodeId.value);
  loadList();
});
</script>

<template>
  <Page>
    <!-- 顶部工具栏 -->
    <ClpmPageToolbar
      title="指标矩阵"
      subtitle="全回路 × 全指标集中查看：行=回路，列=指标（核心/诊断/统计/阀门），薄弱项一眼定位。"
      :loading="loading"
    >
      <template #actions>
        <ClpmStandardActions :items="toolbarItems" />
      </template>
    </ClpmPageToolbar>

    <!-- 筛选区 + 指标组切换 -->
    <div class="mb-4 mt-4 flex flex-wrap items-center gap-3">
      <Segmented
        v-model:value="activeGroup"
        :options="GROUP_OPTIONS"
        size="small"
      />
      <TreeSelect
        v-model:value="filterPlantNodeId"
        :field-names="{ label: 'name', value: 'id', children: 'children' }"
        :tree-data="plantNodeTree"
        allow-clear
        placeholder="装置筛选"
        style="width: 200px"
        tree-default-expand-all
        @change="handlePlantNodeChange"
      />
      <Select
        v-model:value="filterLoopId"
        :filter-option="
          (input: string, option: any) =>
            option.label.toLowerCase().includes(input.toLowerCase())
        "
        :options="loopOptions"
        allow-clear
        placeholder="回路筛选"
        show-search
        style="width: 220px"
        @change="
          () => {
            currentPage = 1;
            loadList();
            syncRouteQuery();
          }
        "
      />
      <Segmented
        v-model:value="windowValue"
        :options="WINDOW_OPTIONS"
        size="small"
      />
      <Button
        type="primary"
        @click="
          () => {
            currentPage = 1;
            loadList();
          }
        "
      >
        查询
      </Button>
    </div>

    <!-- 矩阵表 -->
    <ClpmDataCanvas
      :empty="!loading && !loadError && displayRows.length === 0"
      :error="loadError"
      :loading="loading"
      :empty-reason="
        filterColumn
          ? '当前页无该指标薄弱回路（列筛选生效中，可点击列头漏斗关闭）'
          : '暂无 KPI 快照数据。快照由评估任务自动生成，也可在评估任务页手动重算产生'
      "
      @retry="loadList"
    >
      <Table
        :columns="columns"
        :data-source="displayRows"
        :pagination="{
          current: currentPage,
          pageSize,
          total: filterColumn ? displayRows.length : totalCount,
          showSizeChanger: true,
          showTotal: (t: number) => `共 ${t} 条`,
        }"
        :row-key="(record: KpiSnapshotItem) => `${record.loopId}_${record.tsStart}`"
        :scroll="{ x: 160 + groupDefs(activeGroup).length * 96 + 70 }"
        size="small"
        @change="handleTableChange"
      >
        <!-- 列头：指标列附漏斗（列筛选）+ 折线（趋势对比）图标 -->
        <template #headerCell="{ column, title }">
          <template v-if="defByField.has(String(column.key))">
            <div class="matrix-header">
              <span>{{ title }}</span>
              <span class="matrix-header-actions">
                <Tooltip
                  title="列筛选：仅显示该指标薄弱回路（当前页）"
                >
                  <IconifyIcon
                    :class="{ active: filterColumn === column.key }"
                    class="matrix-header-icon"
                    icon="lucide:filter"
                    @click.stop="toggleColumnFilter(String(column.key))"
                  />
                </Tooltip>
                <Tooltip
                  v-if="defByField.get(String(column.key))?.seriesKey"
                  title="趋势对比：TOP10 薄弱回路该指标历史折线叠加"
                >
                  <IconifyIcon
                    class="matrix-header-icon"
                    icon="lucide:line-chart"
                    @click.stop="openColumnTrend(String(column.key))"
                  />
                </Tooltip>
              </span>
            </div>
          </template>
        </template>

        <!-- 单元格：着色 + Tooltip + 点击下钻 -->
        <template #bodyCell="{ column, record }">
          <template v-if="defByField.has(String(column.key))">
            <Tooltip
              :title="cellTooltip(defByField.get(String(column.key))!, record)"
            >
              <div
                :style="cellStyle(String(column.key), (record as any)[column.dataIndex as string])"
                class="matrix-cell"
                @click="openCellDrawer(String(column.key), record)"
              >
                {{
                  formatCell(
                    defByField.get(String(column.key))!,
                    (record as any)[column.dataIndex as string],
                  )
                }}
              </div>
            </Tooltip>
          </template>
          <template v-else-if="column.key === 'action'">
            <a @click="goLoopDetail(record)">详情</a>
          </template>
        </template>
      </Table>

      <!-- 色阶图例 -->
      <div v-if="activeGroup === 'core' || activeGroup === 'diagnosis'" class="matrix-legend">
        <template v-if="activeGroup === 'core'">
          <span class="matrix-legend-item">
            <i :style="{ background: withAlpha(themeColors.SUCCESS, 0.35) }"></i>优（≥90）
          </span>
          <span class="matrix-legend-item">
            <i :style="{ background: withAlpha(themeColors.INFO, 0.35) }"></i>良（80~90）
          </span>
          <span class="matrix-legend-item">
            <i :style="{ background: withAlpha(themeColors.WARNING, 0.35) }"></i>中（60~80）
          </span>
          <span class="matrix-legend-item">
            <i :style="{ background: withAlpha(themeColors.DANGER, 0.35) }"></i>差（&lt;60）
          </span>
          <span class="matrix-legend-note">
            振荡率/饱和率反向着色（&lt;5 优 / &lt;15 良 / &lt;30 中 / ≥30 差）
          </span>
        </template>
        <template v-else>
          <span class="matrix-legend-item">
            <i :style="{ background: withAlpha(themeColors.SUCCESS, 0.35) }"></i>优
          </span>
          <span class="matrix-legend-item">
            <i :style="{ background: withAlpha(themeColors.INFO, 0.35) }"></i>良
          </span>
          <span class="matrix-legend-item">
            <i :style="{ background: withAlpha(themeColors.WARNING, 0.35) }"></i>中
          </span>
          <span class="matrix-legend-item">
            <i :style="{ background: withAlpha(themeColors.DANGER, 0.35) }"></i>差
          </span>
          <span class="matrix-legend-note">
            仅仪表故障率着色（&lt;1.5 优 / &lt;3 良 / &lt;5 中 / ≥5 差），其余诊断指标为数值型不着色
          </span>
        </template>
        <span class="matrix-legend-item matrix-na-demo">
          <i class="matrix-na"></i>无有效值
        </span>
      </div>
    </ClpmDataCanvas>

    <!-- 单元格下钻抽屉：单回路 × 单指标历史趋势 -->
    <Drawer
      v-model:open="cellDrawerVisible"
      :title="`历史趋势 - ${cellDrawerRow?.loopTagName ?? ''} · ${cellDrawerDef?.title ?? ''}`"
      :width="480"
      destroy-on-close
      :footer="null"
    >
      <div v-if="cellDrawerLoading" class="flex h-64 items-center justify-center">
        <span class="text-sm opacity-60">加载中...</span>
      </div>
      <EchartsUI v-else ref="cellChartRef" height="360px" />
    </Drawer>

    <!-- 列头趋势弹层：TOP N 薄弱回路折线叠加 -->
    <Modal
      v-model:open="trendModalVisible"
      :title="`趋势对比 - ${trendModalDef?.title ?? ''}（TOP10 薄弱回路）`"
      :width="900"
      destroy-on-close
      :footer="null"
    >
      <div v-if="trendModalLoading" class="flex h-64 items-center justify-center">
        <span class="text-sm opacity-60">加载中...</span>
      </div>
      <EchartsUI v-else ref="trendChartRef" height="420px" />
    </Modal>
  </Page>
</template>

<style scoped>
.matrix-header {
  display: flex;
  gap: 4px;
  align-items: center;
  justify-content: space-between;
}

.matrix-header-actions {
  display: inline-flex;
  gap: 4px;
  align-items: center;
}

.matrix-header-icon {
  font-size: 13px;
  cursor: pointer;
  opacity: 0.45;
  transition: opacity 0.15s;
}

.matrix-header-icon:hover {
  opacity: 1;
}

.matrix-header-icon.active {
  opacity: 1;
}

.matrix-cell {
  padding: 0 6px;
  font-variant-numeric: tabular-nums;
  line-height: 22px;
  text-align: right;
  cursor: pointer;
  border-radius: 3px;
}

.matrix-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: center;
  padding: 8px 4px 0;
  font-size: 12px;
  color: var(--ant-color-text-tertiary);
}

.matrix-legend-item {
  display: inline-flex;
  gap: 4px;
  align-items: center;
}

.matrix-legend-item i {
  display: inline-block;
  width: 12px;
  height: 12px;
  border-radius: 2px;
}

.matrix-legend-note {
  opacity: 0.75;
}

/* NULL 斜纹占位（对齐工作台 EvalHeatMatrix 的 N/A 样式语义） */
.matrix-na {
  background-image: repeating-linear-gradient(
    135deg,
    transparent,
    transparent 3px,
    rgb(108 117 125 / 30%) 3px,
    rgb(108 117 125 / 30%) 4px
  );
  border: 1px solid rgb(108 117 125 / 25%);
}
</style>
