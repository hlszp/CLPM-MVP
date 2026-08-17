<script lang="ts" setup>
/**
 * KPI 报表模块
 *
 * 提供综合报表与回路报表两种视图，支持日/周/月时间维度切换，
 * 按工厂模型层级筛选，并支持 CSV 导出。
 *
 * - 综合报表：使用 getBoardAggregateApi 获取装置级 KPI 聚合数据
 * - 回路报表：使用 getLoopSnapshotsApi 获取回路 KPI 快照数据
 * - 性能等级：使用 getGradingThresholdsApi 获取定级阈值并按评分映射
 *
 * 路由：/metric/kpi-report
 */
import type { TableColumnsType } from 'ant-design-vue';

import type { DashboardApi } from '#/api/dashboard';
import type { ConfidenceLevel, KpiSnapshotItem, MetricApi } from '#/api/metric';
import type { PlantNodeApi } from '#/api/plant-node';
import type { ColumnConfig } from '#/composables/use-clpm-preferences';

import { computed, onMounted, ref, watch } from 'vue';

import { Page } from '@vben/common-ui';

import {
  DatePicker,
  Dropdown,
  Menu,
  message,
  RadioGroup,
  Select,
  Skeleton,
  Table,
  Tag,
} from 'ant-design-vue';
import dayjs from 'dayjs';
// Ant Design Vue 的周选择器（picker="week"）依赖 isoWeek 插件进行周格式化
import isoWeek from 'dayjs/plugin/isoWeek';

import { getBoardAggregateApi } from '#/api/dashboard';
import { getLoopListApi } from '#/api/loop';
import { getGradingThresholdsApi, getLoopSnapshotsApi } from '#/api/metric';
import { getPlantNodeTreeApi } from '#/api/plant-node';
import {
  ClpmDataCanvas,
  ClpmPageToolbar,
  ClpmStandardActions,
  ClpmToolbarButton,
} from '#/components/clpm';
import { usePagePreference } from '#/composables/use-clpm-preferences';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import { showPageHelp, usePageToolbar } from '#/composables/use-page-toolbar';
import { exportData } from '#/utils/export';
import { formatLocalTime } from '#/utils/format';
import { flattenNodes } from '#/utils/plant-node';

defineOptions({ name: 'MetricKpiReport' });

// 注册 isoWeek 插件（周选择器内部调用 dayjs().isoWeek() 进行格式化）
dayjs.extend(isoWeek);

const { themeColors } = useClpmTheme();

// ============ 筛选状态 ============
type TimeDimension = 'day' | 'month' | 'week';
type ReportType = 'comprehensive' | 'loop';

const timeDimension = ref<TimeDimension>('day');
const selectedDate = ref<dayjs.Dayjs>(dayjs());
const selectedWeek = ref<dayjs.Dayjs>(dayjs());
const selectedMonth = ref<dayjs.Dayjs>(dayjs());
const reportType = ref<ReportType>('comprehensive');
const plantNodeId = ref<string | undefined>();

/** 工厂节点列表（扁平化用于 Select） */
const plantNodes = ref<PlantNodeApi.PlantNode[]>([]);
/** 工厂节点原始树（用于递归收集子孙节点 ID） */
const plantNodeTree = ref<PlantNodeApi.PlantNode[]>([]);

/**
 * 递归收集某节点及其所有子孙节点的 ID。
 * 选择非末级节点（工厂/装置）时，需将其下属所有末级节点（单元）的 ID
 * 一并传给后端，否则后端按精确匹配会返回空结果。
 */
function collectDescendantIds(
  nodes: PlantNodeApi.PlantNode[],
  targetId: string,
): string[] {
  const result: string[] = [];
  const findAndCollect = (list: PlantNodeApi.PlantNode[]): boolean => {
    for (const node of list) {
      if (node.id === targetId) {
        result.push(node.id);
        collectAllChildren(node, result);
        return true;
      }
      if (
        node.children &&
        node.children.length > 0 &&
        findAndCollect(node.children)
      )
        return true;
    }
    return false;
  };
  const collectAllChildren = (node: PlantNodeApi.PlantNode, acc: string[]) => {
    for (const child of node.children ?? []) {
      acc.push(child.id);
      collectAllChildren(child, acc);
    }
  };
  findAndCollect(nodes);
  return result;
}

/** 获取当前筛选条件下的有效 plantNodeId 字符串（逗号分隔） */
function getEffectivePlantNodeIds(): string | undefined {
  if (!plantNodeId.value) return undefined;
  const ids = collectDescendantIds(plantNodeTree.value, plantNodeId.value);
  return ids.length > 0 ? ids.join(',') : plantNodeId.value;
}

// ============ 数据状态 ============
const loading = ref(false);
const loadError = ref(false);
const exporting = ref(false);

const comprehensiveData = ref<DashboardApi.BoardItem[]>([]);
const loopData = ref<KpiSnapshotItem[]>([]);
const thresholds = ref<MetricApi.GradingThresholdItem[]>([]);

/** 回路描述映射（loopId → description），用于回路报表显示回路名称 */
const loopDescMap = ref<Map<string, string>>(new Map());

// ============ 回路报表聚合 ============
/** 回路聚合行（每个回路一条记录，各指标按均值聚合） */
interface LoopAggRow {
  loopId: string;
  loopTagName: string;
  loopName: string;
  score: null | number;
  accuracyRate: null | number;
  fastRate: null | number;
  steadyRate: null | number;
  autoModeRate: null | number;
  effectiveAutoRate: null | number;
  saturationRate: null | number;
  oscillationRate: null | number;
  goodValueRate: null | number;
  stictionIndex: null | number;
  settlingTime: null | number;
  outputTravelIndex: null | number;
  idealSettlingTime: null | number;
  validRate: null | number;
  /** 聚合的评估次数 */
  evalCount: number;
  /** 可信度分布（取众数或最低等级） */
  confidenceLevel: ConfidenceLevel | null;
}

/** 数值聚合字段列表 */
const NUMERIC_AGG_FIELDS = [
  'score',
  'accuracyRate',
  'fastRate',
  'steadyRate',
  'autoModeRate',
  'effectiveAutoRate',
  'saturationRate',
  'oscillationRate',
  'goodValueRate',
  'stictionIndex',
  'settlingTime',
  'outputTravelIndex',
  'idealSettlingTime',
  'validRate',
] as const;

/** 计算非 null 值的均值 */
function meanOf(values: (null | number)[]): null | number {
  const valid = values.filter(
    (v): v is number => v !== null && v !== undefined && !Number.isNaN(v),
  );
  if (valid.length === 0) return null;
  return valid.reduce((s, v) => s + v, 0) / valid.length;
}

/** 取可信度等级的最差值（A→E，取字母序最大） */
function worstConfidence(
  levels: (ConfidenceLevel | null)[],
): ConfidenceLevel | null {
  const valid = levels.filter((v): v is ConfidenceLevel => !!v);
  if (valid.length === 0) return null;
  valid.sort((a, b) => b.localeCompare(a));
  return valid[0] ?? null;
}

/** 将回路快照列表按 loopId 分组并聚合（求均值） */
const aggregatedLoopData = computed<LoopAggRow[]>(() => {
  const groups = new Map<string, KpiSnapshotItem[]>();
  for (const snap of loopData.value) {
    const key = snap.loopId ?? snap.loopTagName ?? 'unknown';
    const group = groups.get(key) ?? [];
    group.push(snap);
    groups.set(key, group);
  }
  const rows: LoopAggRow[] = [];
  for (const [, snaps] of groups) {
    const first = snaps[0];
    if (!first) continue;
    const loopId = first.loopId ?? '';
    const row: LoopAggRow = {
      loopId,
      loopTagName: first.loopTagName ?? '—',
      loopName: loopDescMap.value.get(loopId) || first.loopTagName || '—',
      score: null,
      accuracyRate: null,
      fastRate: null,
      steadyRate: null,
      autoModeRate: null,
      effectiveAutoRate: null,
      saturationRate: null,
      oscillationRate: null,
      goodValueRate: null,
      stictionIndex: null,
      settlingTime: null,
      outputTravelIndex: null,
      idealSettlingTime: null,
      validRate: null,
      evalCount: snaps.length,
      confidenceLevel: worstConfidence(snaps.map((s) => s.confidenceLevel)),
    };
    for (const field of NUMERIC_AGG_FIELDS) {
      (row as any)[field] = meanOf(snaps.map((s) => s[field] as null | number));
    }
    rows.push(row);
  }
  // 按回路编号排序
  rows.sort((a, b) => a.loopTagName.localeCompare(b.loopTagName));
  return rows;
});

// ============ 等级元数据 ============
/** 5 级性能定级中文标签（颜色走 levelColor 单源） */
const LEVEL_META: Record<number, { label: string }> = {
  1: { label: '优秀' },
  2: { label: '良好' },
  3: { label: '合格' },
  4: { label: '警告' },
  5: { label: '不合格' },
};

/**
 * 等级默认展示色：与 use-score-color 的 fallbackByLevel 同口径
 * （SUCCESS/INFO/WARNING/DANGER/DANGER，随明暗主题响应）
 */
function levelColor(level: number): string {
  const fallbackByLevel: Record<number, string> = {
    1: themeColors.value.SUCCESS,
    2: themeColors.value.INFO,
    3: themeColors.value.WARNING,
    4: themeColors.value.DANGER,
    5: themeColors.value.DANGER,
  };
  return fallbackByLevel[level] ?? themeColors.value.NEUTRAL;
}

/** 可信度等级颜色与标签 */
const CONFIDENCE_META: Record<string, { color: string; label: string }> = {
  A: { label: 'A', color: 'green' },
  B: { label: 'B', color: 'blue' },
  C: { label: 'C', color: 'gold' },
  D: { label: 'D', color: 'orange' },
  E: { label: 'E', color: 'red' },
};

/** 按评分计算性能等级 */
function getRating(score: null | number | undefined): {
  color: string;
  label: string;
  level: number;
} {
  if (score === null || score === undefined) {
    return { color: 'default', label: '—', level: 0 };
  }
  const sorted = [...thresholds.value].toSorted((a, b) => a.level - b.level);
  // level 1 为最高分区间，逐级递减
  for (const t of sorted) {
    if (score >= t.minScore && score < t.maxScore) {
      return {
        color: levelColor(t.level),
        label: LEVEL_META[t.level]?.label ?? `L${t.level}`,
        level: t.level,
      };
    }
  }
  // 处理最高分上界（score == maxScore 的场景）
  const top = sorted[0];
  if (top && score >= top.maxScore) {
    return {
      color: levelColor(top.level),
      label: LEVEL_META[top.level]?.label ?? `L${top.level}`,
      level: top.level,
    };
  }
  // 低于最低分下界归为最低级
  const bottom = sorted[sorted.length - 1];
  if (bottom && score < bottom.minScore) {
    return {
      color: levelColor(bottom.level),
      label: LEVEL_META[bottom.level]?.label ?? `L${bottom.level}`,
      level: bottom.level,
    };
  }
  return { color: 'default', label: '—', level: 0 };
}

// ============ 工具函数 ============
function formatNumber(val: null | number | undefined, suffix = ''): string {
  if (val === null || val === undefined) return '—';
  return `${val.toFixed(2)}${suffix}`;
}

/**
 * 根据时间维度计算时间范围（UTC ISO 8601）。
 * 后端快照 ts_start 为 UTC 存储（TIMESTAMP WITHOUT TIME ZONE），
 * 本地选中的日/周/月边界必须先转为 UTC，否则窗口整体偏移 8 小时。
 */
function getTimeRange(): { end: string; start: string } {
  switch (timeDimension.value) {
    case 'month': {
      const m = selectedMonth.value || dayjs();
      return {
        start: m.startOf('month').toISOString(),
        end: m.endOf('month').toISOString(),
      };
    }
    case 'week': {
      const w = selectedWeek.value || dayjs();
      return {
        start: w.startOf('week').toISOString(),
        end: w.endOf('week').toISOString(),
      };
    }
    default: {
      const d = selectedDate.value || dayjs();
      return {
        start: d.startOf('day').toISOString(),
        end: d.endOf('day').toISOString(),
      };
    }
  }
}

/**
 * 快照时间（后端 UTC 存储，无时区标记）转本地显示。
 * 与 history-snapshots 页约定一致：无时区后缀按 UTC 处理。
 */
function formatSnapshotTime(ts: null | string | undefined): string {
  return formatLocalTime(ts, 'MM-DD HH:mm');
}

/** 提取记录的评分（综合报表用 avgScore，回路报表用聚合 score） */
function getScore(record: Record<string, any>): null | number {
  if (reportType.value === 'comprehensive') {
    return (record as DashboardApi.BoardItem).avgScore ?? null;
  }
  return (record as LoopAggRow).score ?? null;
}

/** 判断是否为百分比列 */
function isRateColumn(key: string): boolean {
  return [
    'accuracyRate',
    'autoModeRate',
    'effectiveAutoRate',
    'fastRate',
    'goodValueRate',
    'oscillationRate',
    'saturationRate',
    'steadyRate',
  ].includes(key);
}

/** 判断是否为纯数值列（扩展指标，非百分比） */
function isPlainNumberColumn(key: string): boolean {
  return [
    'idealSettlingTime',
    'outputTravelIndex',
    'settlingTime',
    'stictionIndex',
  ].includes(key);
}

/** 判断是否为整数计数列 */
function isCountColumn(key: string): boolean {
  return ['evalCount', 'evaluatedLoops', 'inconclusiveLoops'].includes(key);
}

/** 评级分布统计（性能等级 → 行数，level 0 为未评级） */
const ratingDistribution = computed<Record<number, number>>(() => {
  const dist: Record<number, number> = { 0: 0, 1: 0, 2: 0, 3: 0, 4: 0, 5: 0 };
  for (const row of currentData.value) {
    const { level } = getRating(getScore(row));
    dist[level] = (dist[level] ?? 0) + 1;
  }
  return dist;
});

// ============ 列设置（IA 整改 P2-10：综合默认显10列，回路默认显9列，可自定义）============

/** 综合报表：14 列默认保留 10 列可见（隐藏饱和率/振荡率/数据不足） */
const COMPREHENSIVE_COLS: ColumnConfig[] = [
  { key: 'nodeName', label: '节点', visible: true },
  { key: 'snapshotTime', label: '数据时间', visible: true },
  { key: 'rating', label: '性能等级', visible: true },
  { key: 'score', label: '性能评分', visible: true },
  { key: 'accuracyRate', label: '准确率', visible: true },
  { key: 'fastRate', label: '快速率', visible: true },
  { key: 'steadyRate', label: '平稳率', visible: true },
  { key: 'autoModeRate', label: '自控率', visible: true },
  { key: 'effectiveAutoRate', label: '有效自控率', visible: true },
  { key: 'goodValueRate', label: '好值率', visible: true },
  { key: 'saturationRate', label: '饱和率', visible: false },
  { key: 'oscillationRate', label: '振荡率', visible: false },
  { key: 'evaluatedLoops', label: '参评回路数', visible: true },
  { key: 'inconclusiveLoops', label: '数据不足回路数', visible: false },
];

/** 回路报表：18 列默认保留 9 列可见（其余默认隐藏） */
const LOOP_COLS: ColumnConfig[] = [
  { key: 'index', label: '序号', visible: true },
  { key: 'loopTagName', label: '回路编号', visible: true },
  { key: 'loopName', label: '回路名称', visible: true },
  { key: 'rating', label: '性能等级', visible: true },
  { key: 'score', label: '性能评分', visible: true },
  { key: 'accuracyRate', label: '准确率', visible: true },
  { key: 'fastRate', label: '快速率', visible: false },
  { key: 'steadyRate', label: '平稳率', visible: true },
  { key: 'autoModeRate', label: '自控率', visible: false },
  { key: 'effectiveAutoRate', label: '有效自控率', visible: false },
  { key: 'saturationRate', label: '饱和率', visible: false },
  { key: 'oscillationRate', label: '振荡率', visible: false },
  { key: 'goodValueRate', label: '好值率', visible: false },
  { key: 'idealSettlingTime', label: '理想稳定时间', visible: false },
  { key: 'settlingTime', label: '实际稳定时间', visible: false },
  { key: 'outputTravelIndex', label: '输出跳变率', visible: false },
  { key: 'stictionIndex', label: '阀门粘滞', visible: false },
  { key: 'confidenceLevel', label: '可信度', visible: true },
  { key: 'evalCount', label: '评估次数', visible: false },
];

const compPrefs = usePagePreference('kpi-report-comprehensive');
const loopPrefs = usePagePreference('kpi-report-loop');

const comprehensiveColumnConfigs = ref<ColumnConfig[]>(
  compPrefs.preferences.value.columns &&
    compPrefs.preferences.value.columns.length > 0
    ? compPrefs.preferences.value.columns
    : COMPREHENSIVE_COLS,
);
const loopColumnConfigs = ref<ColumnConfig[]>(
  loopPrefs.preferences.value.columns &&
    loopPrefs.preferences.value.columns.length > 0
    ? loopPrefs.preferences.value.columns
    : LOOP_COLS,
);

const currentColumnConfigs = computed(() =>
  reportType.value === 'comprehensive'
    ? comprehensiveColumnConfigs.value
    : loopColumnConfigs.value,
);

function handleUpdateColumns(cols: ColumnConfig[]) {
  if (reportType.value === 'comprehensive') {
    comprehensiveColumnConfigs.value = cols;
    compPrefs.updateColumns(cols);
  } else {
    loopColumnConfigs.value = cols;
    loopPrefs.updateColumns(cols);
  }
}

function handleResetColumns() {
  if (reportType.value === 'comprehensive') {
    comprehensiveColumnConfigs.value = [...COMPREHENSIVE_COLS];
    compPrefs.updateColumns([...COMPREHENSIVE_COLS]);
  } else {
    loopColumnConfigs.value = [...LOOP_COLS];
    loopPrefs.updateColumns([...LOOP_COLS]);
  }
}

function getColumnKey(col: TableColumnsType[number]): string {
  const c = col as Record<string, unknown>;
  return (c.dataIndex as string) || (c.key as string) || '';
}

/** 根据列设置过滤并排序后的可见列 */
function applyColumnVisibility(
  allColumns: TableColumnsType,
  configs: ColumnConfig[],
): TableColumnsType {
  const configMap = new Map(
    configs.map((c, i) => [c.key, { visible: c.visible, order: i }]),
  );
  const filtered = allColumns.filter((c) => {
    const cfg = configMap.get(getColumnKey(c));
    return cfg ? cfg.visible : true;
  });
  // eslint-disable-next-line unicorn/no-array-sort
  return [...filtered].sort((a, b) => {
    const aOrder = configMap.get(getColumnKey(a))?.order ?? 99;
    const bOrder = configMap.get(getColumnKey(b))?.order ?? 99;
    return aOrder - bOrder;
  });
}

// ============ 表格列定义 ============
// 注：使用 `as` 类型断言而非注解，规避旧版 vue-tsc 对大型数组字面量的 TS1005 误报
const _compCols = [
  {
    title: '节点',
    dataIndex: 'nodeName',
    key: 'nodeName',
    width: 140,
    ellipsis: true,
  },
  { title: '数据时间', key: 'snapshotTime', width: 110 },
  { title: '性能等级', key: 'rating', width: 90 },
  {
    title: '性能评分',
    dataIndex: 'avgScore',
    key: 'score',
    width: 90,
    sorter: (a: Record<string, any>, b: Record<string, any>) =>
      (a.avgScore ?? 0) - (b.avgScore ?? 0),
  },
  {
    title: '准确率',
    dataIndex: 'accuracyRate',
    key: 'accuracyRate',
    width: 90,
  },
  { title: '快速率', dataIndex: 'fastRate', key: 'fastRate', width: 90 },
  { title: '平稳率', dataIndex: 'stabilityRate', key: 'steadyRate', width: 90 },
  {
    title: '自控率',
    dataIndex: 'autoModeRate',
    key: 'autoModeRate',
    width: 90,
  },
  {
    title: '有效自控率',
    dataIndex: 'effectiveAutoRate',
    key: 'effectiveAutoRate',
    width: 110,
  },
  {
    title: '好值率',
    dataIndex: 'goodValueRate',
    key: 'goodValueRate',
    width: 90,
  },
  {
    title: '饱和率',
    dataIndex: 'saturationRate',
    key: 'saturationRate',
    width: 90,
  },
  {
    title: '振荡率',
    dataIndex: 'oscillationRate',
    key: 'oscillationRate',
    width: 90,
  },
  {
    title: '参评回路数',
    dataIndex: 'evaluatedLoops',
    key: 'evaluatedLoops',
    width: 100,
    align: 'center',
  },
  {
    title: '数据不足回路数',
    dataIndex: 'inconclusiveLoops',
    key: 'inconclusiveLoops',
    width: 120,
    align: 'center',
  },
];
const allComprehensiveColumns = _compCols as TableColumnsType;

const _loopCols = [
  { title: '序号', key: 'index', width: 60, fixed: 'left' },
  {
    title: '回路编号',
    dataIndex: 'loopTagName',
    key: 'loopTagName',
    width: 160,
    ellipsis: true,
    fixed: 'left',
  },
  { title: '回路名称', key: 'loopName', width: 160, ellipsis: true },
  { title: '性能等级', key: 'rating', width: 100 },
  {
    title: '性能评分',
    dataIndex: 'score',
    key: 'score',
    width: 100,
    sorter: (a: Record<string, any>, b: Record<string, any>) =>
      (a.score ?? 0) - (b.score ?? 0),
  },
  {
    title: '准确率',
    dataIndex: 'accuracyRate',
    key: 'accuracyRate',
    width: 90,
  },
  { title: '快速率', dataIndex: 'fastRate', key: 'fastRate', width: 90 },
  { title: '平稳率', dataIndex: 'steadyRate', key: 'steadyRate', width: 90 },
  {
    title: '自控率',
    dataIndex: 'autoModeRate',
    key: 'autoModeRate',
    width: 90,
  },
  {
    title: '有效自控率',
    dataIndex: 'effectiveAutoRate',
    key: 'effectiveAutoRate',
    width: 110,
  },
  {
    title: '饱和率',
    dataIndex: 'saturationRate',
    key: 'saturationRate',
    width: 90,
  },
  {
    title: '振荡率',
    dataIndex: 'oscillationRate',
    key: 'oscillationRate',
    width: 90,
  },
  {
    title: '好值率',
    dataIndex: 'goodValueRate',
    key: 'goodValueRate',
    width: 90,
  },
  {
    title: '理想稳定时间',
    dataIndex: 'idealSettlingTime',
    key: 'idealSettlingTime',
    width: 110,
  },
  {
    title: '实际稳定时间',
    dataIndex: 'settlingTime',
    key: 'settlingTime',
    width: 110,
  },
  {
    title: '输出跳变率',
    dataIndex: 'outputTravelIndex',
    key: 'outputTravelIndex',
    width: 100,
  },
  {
    title: '阀门粘滞',
    dataIndex: 'stictionIndex',
    key: 'stictionIndex',
    width: 90,
  },
  {
    title: '可信度',
    dataIndex: 'confidenceLevel',
    key: 'confidenceLevel',
    width: 80,
  },
  {
    title: '评估次数',
    dataIndex: 'evalCount',
    key: 'evalCount',
    width: 90,
    align: 'center',
  },
];
const allLoopColumns = _loopCols as TableColumnsType;

const currentColumns = computed(() =>
  reportType.value === 'comprehensive'
    ? applyColumnVisibility(
        allComprehensiveColumns,
        comprehensiveColumnConfigs.value,
      )
    : applyColumnVisibility(allLoopColumns, loopColumnConfigs.value),
);

const currentData = computed(() =>
  reportType.value === 'comprehensive'
    ? comprehensiveData.value
    : aggregatedLoopData.value,
);

const isEmpty = computed(
  () => !loading.value && !loadError.value && currentData.value.length === 0,
);

const rowKeyField = computed(() =>
  reportType.value === 'comprehensive' ? 'nodeId' : 'loopId',
);

const tableScrollX = computed(() =>
  reportType.value === 'comprehensive' ? 1380 : 2110,
);

// ============ 数据加载 ============
async function loadPlantNodes() {
  try {
    const tree = await getPlantNodeTreeApi();
    plantNodeTree.value = tree;
    plantNodes.value = flattenNodes(tree);
  } catch {
    plantNodeTree.value = [];
    plantNodes.value = [];
  }
}

async function loadThresholds() {
  try {
    const data = await getGradingThresholdsApi();
    thresholds.value = data.thresholds ?? [];
    // 补全 5 级默认值（后端可能未返回全部）
    const defaults: Record<number, { max: number; min: number }> = {
      1: { min: 90, max: 100 },
      2: { min: 80, max: 90 },
      3: { min: 70, max: 80 },
      4: { min: 60, max: 70 },
      5: { min: 0, max: 60 },
    };
    const names = ['EXCELLENT', 'GOOD', 'FAIR', 'WARNING', 'POOR'];
    for (const lv of [1, 2, 3, 4, 5]) {
      if (!thresholds.value.some((t) => t.level === lv)) {
        thresholds.value.push({
          level: lv,
          name: names[lv - 1] ?? `L${lv}`,
          minScore: defaults[lv]?.min ?? 0,
          maxScore: defaults[lv]?.max ?? 100,
          color: levelColor(lv),
        });
      }
    }
    thresholds.value.sort((a, b) => a.level - b.level);
  } catch {
    thresholds.value = [];
  }
}

async function loadComprehensive() {
  const result = await getBoardAggregateApi(
    plantNodeId.value ? { plantId: plantNodeId.value } : undefined,
  );
  comprehensiveData.value = result.items ?? [];
}

async function loadLoop() {
  const { start, end } = getTimeRange();
  // 先加载回路描述映射（用于回路报表显示回路名称）
  await loadLoopDescMap();
  // 递归收集所选节点及其所有子孙节点 ID（支持选择工厂/装置等非末级节点）
  const effectivePlantNodeId = getEffectivePlantNodeIds();
  // 后端 pageSize 上限 100，循环分页拉取时间窗内全部快照
  // latestOnly=false：返回窗口内全部快照（默认 true 只取每回路最新一条，
  // 会导致日/周/月聚合失效、"评估次数"恒为 1）
  const allItems: any[] = [];
  let page = 1;
  const pageLimit = 100;
  let total: number;
  do {
    const result = await getLoopSnapshotsApi({
      startTime: start,
      endTime: end,
      plantNodeId: effectivePlantNodeId,
      latestOnly: false,
      page,
      pageSize: pageLimit,
    });
    allItems.push(...(result.items ?? []));
    total = result.total ?? 0;
    page += 1;
  } while ((page - 1) * pageLimit < total);
  loopData.value = allItems;
}

/** 加载回路列表，建立 loopId → description 映射（回路报表显示回路名称） */
async function loadLoopDescMap() {
  try {
    const map = new Map<string, string>();
    // getLoopListApi 后端已支持递归子孙节点，只需传单个 plantNodeId
    let page = 1;
    const pageLimit = 100;
    let total = 0;
    do {
      const params: any = { page, pageSize: pageLimit };
      if (plantNodeId.value) params.plantNodeId = plantNodeId.value;
      const result = await getLoopListApi(params);
      for (const item of result.items ?? []) {
        if (item.description) {
          map.set(item.loopId, item.description);
        }
      }
      total = result.total;
      page += 1;
    } while ((page - 1) * pageLimit < total);
    loopDescMap.value = map;
  } catch {
    loopDescMap.value = new Map();
  }
}

async function loadData() {
  loading.value = true;
  loadError.value = false;
  try {
    await (reportType.value === 'comprehensive'
      ? loadComprehensive()
      : loadLoop());
  } catch (error) {
    loadError.value = true;
    // 错误 toast 由 api/request.ts 拦截器统一弹出，视图层只更新本地 error 态
    console.error('加载 KPI 报表失败:', error);
  } finally {
    loading.value = false;
  }
}

// ============ 导出（P3-23：支持 CSV/Excel 双格式） ============
async function handleExport(format: 'csv' | 'excel' = 'csv') {
  if (currentData.value.length === 0) {
    message.warning('当前无数据可导出');
    return;
  }
  exporting.value = true;
  try {
    const cols = currentColumns.value;
    const headers = cols.map((c) => String(c.title ?? ''));
    const rows = currentData.value.map((row, idx) =>
      cols.map((c) => {
        const key = c.key as string;
        if (key === 'index') return String(idx + 1);
        if (key === 'rating') {
          return getRating(getScore(row)).label;
        }
        if (key === 'snapshotTime') {
          return formatSnapshotTime((row as Record<string, any>).snapshotTime);
        }
        if (key === 'loopName') {
          const val = (row as Record<string, any>)[key];
          return val ?? '';
        }
        const dataIndex = (c as any).dataIndex as string;
        if (!dataIndex) return '';
        const val = (row as Record<string, any>)[dataIndex];
        if (val === null || val === undefined) {
          return isCountColumn(key) ? '0' : '';
        }
        if (isCountColumn(key)) return String(val);
        if (typeof val === 'number') return val.toFixed(2);
        return String(val);
      }),
    );
    const typeLabel =
      reportType.value === 'comprehensive' ? '综合报表' : '回路报表';
    const dateStr = dayjs().format('YYYYMMDD');
    exportData({
      filename: `KPI报表_${typeLabel}_${dateStr}`,
      format,
      headers,
      rows,
      sheetName: typeLabel,
    });
    message.success(`已导出 ${rows.length} 条记录`);
  } catch (error: any) {
    console.error('导出失败:', error);
    message.error(error?.message || '导出失败');
  } finally {
    exporting.value = false;
  }
}

// ============ 自动重载 ============
// 时间维度 / 报表类型 / 工厂节点变化时自动重新加载
watch([timeDimension, reportType, plantNodeId], () => {
  loadData();
});

// 日期 / 周 / 月变化时仅在对应维度下重新加载
watch(selectedDate, () => {
  if (timeDimension.value === 'day') loadData();
});
watch(selectedWeek, () => {
  if (timeDimension.value === 'week') loadData();
});
watch(selectedMonth, () => {
  if (timeDimension.value === 'month') loadData();
});

/** 工具栏帮助 */
function handleHelp() {
  showPageHelp({
    title: 'KPI 报表 帮助',
    content:
      '按日/周/月维度查看综合报表（装置级聚合）与回路报表（回路级明细），支持按工厂模型层级筛选与 CSV 导出。综合报表展示参评回路数与数据不足回路数；回路报表聚合窗口内多次评估结果（均值）并展示可信度等级。切换时间维度/报表类型/工厂节点会自动重新加载。',
  });
}

// ===== 统一工具栏（标准 3 工具：刷新 / 列设置 / 帮助；导出用 Dropdown） =====
const { toolbarItems } = usePageToolbar(() => ({
  refresh: { onClick: loadData, loading: loading.value },
  setting: {},
  help: { onClick: handleHelp },
}));

onMounted(() => {
  loadPlantNodes();
  loadThresholds();
  loadData();
});
</script>

<template>
  <Page>
    <ClpmPageToolbar
      title="KPI 报表"
      subtitle="按日/周/月维度查看综合与回路 KPI 报表，支持 CSV 导出。"
      :loading="loading"
    >
      <RadioGroup
        v-model:value="timeDimension"
        size="small"
        :options="[
          { label: '日', value: 'day' },
          { label: '周', value: 'week' },
          { label: '月', value: 'month' },
        ]"
        option-type="button"
        button-style="solid"
      />
      <DatePicker
        v-if="timeDimension === 'day'"
        v-model:value="selectedDate"
        size="small"
        format="YYYY-MM-DD"
      />
      <DatePicker
        v-else-if="timeDimension === 'week'"
        v-model:value="selectedWeek"
        picker="week"
        size="small"
        format="YYYY-[W]WW"
      />
      <DatePicker
        v-else
        v-model:value="selectedMonth"
        picker="month"
        size="small"
        format="YYYY-MM"
      />
      <RadioGroup
        v-model:value="reportType"
        size="small"
        :options="[
          { label: '综合报表', value: 'comprehensive' },
          { label: '回路报表', value: 'loop' },
        ]"
        option-type="button"
        button-style="solid"
      />
      <Select
        v-model:value="plantNodeId"
        placeholder="工厂/装置/单元"
        size="small"
        allow-clear
        style="width: 200px"
        :options="plantNodes.map((n) => ({ label: n.name, value: n.id }))"
        :filter-option="
          (input: string, option: any) =>
            option.label.toLowerCase().includes(input.toLowerCase())
        "
        show-search
      />
      <template #actions>
        <ClpmStandardActions
          :items="toolbarItems"
          :column-configs="currentColumnConfigs"
          @update:columns="handleUpdateColumns"
          @reset-columns="handleResetColumns"
        />
        <!-- P3-23：导出 CSV/Excel 双格式 -->
        <Dropdown>
          <ClpmToolbarButton
            icon="ant-design:download-outlined"
            label="导出"
            :loading="exporting"
            tooltip="导出当前报表数据"
          />
          <template #overlay>
            <Menu @click="(e: any) => handleExport(e.key as 'csv' | 'excel')">
              <Menu.Item key="csv">导出 CSV</Menu.Item>
              <Menu.Item key="excel">导出 Excel</Menu.Item>
            </Menu>
          </template>
        </Dropdown>
      </template>
    </ClpmPageToolbar>

    <ClpmDataCanvas
      class="mt-3"
      :loading="loading"
      :error="loadError"
      :empty="isEmpty"
      empty-text="暂无报表数据"
      empty-reason="当前维度与日期没有报表数据；可切换日/周/月维度或选择其他日期。"
      @retry="loadData"
    >
      <div
        v-if="currentData.length > 0"
        class="mb-2 flex flex-wrap items-center gap-1 text-xs"
      >
        <span :style="{ color: themeColors.NEUTRAL }">评级分布</span>
        <Tag v-for="lv in 5" :key="lv" :color="levelColor(lv)" class="mr-0">
          {{ LEVEL_META[lv]?.label }} × {{ ratingDistribution[lv] ?? 0 }}
        </Tag>
        <Tag v-if="(ratingDistribution[0] ?? 0) > 0" class="mr-0">
          未评级 × {{ ratingDistribution[0] }}
        </Tag>
      </div>
      <!-- P2-13/P2-15：报表切换骨架屏过渡 -->
      <Skeleton
        v-if="loading && currentData.length === 0"
        active
        :paragraph="{ rows: 8 }"
        class="mb-4"
      />
      <Table
        v-show="!(loading && currentData.length === 0)"
        :columns="currentColumns"
        :data-source="currentData"
        :pagination="{
          pageSize: 20,
          showSizeChanger: true,
          showTotal: (t: number) => `共 ${t} 条`,
        }"
        :scroll="{ x: tableScrollX }"
        :row-key="rowKeyField"
        size="small"
      >
        <template #bodyCell="{ column, record, index }">
          <template v-if="column.key === 'index'">
            {{ index + 1 }}
          </template>
          <template v-else-if="column.key === 'snapshotTime'">
            <span class="font-mono text-xs">{{
              formatSnapshotTime(record.snapshotTime)
            }}</span>
          </template>
          <template v-else-if="column.key === 'rating'">
            <Tag
              v-if="getRating(getScore(record)).level > 0"
              :color="getRating(getScore(record)).color"
            >
              {{ getRating(getScore(record)).label }}
            </Tag>
            <span v-else :style="{ color: themeColors.NEUTRAL }">—</span>
          </template>
          <template v-else-if="column.key === 'score'">
            <span class="clpm-num font-semibold">
              {{ formatNumber(getScore(record)) }}
            </span>
          </template>
          <template v-else-if="column.key === 'loopName'">
            <span>{{ record.loopName || record.loopTagName || '—' }}</span>
          </template>
          <template v-else-if="column.key === 'confidenceLevel'">
            <Tag
              v-if="
                record.confidenceLevel &&
                CONFIDENCE_META[String(record.confidenceLevel)]
              "
              :color="CONFIDENCE_META[String(record.confidenceLevel)]!.color"
            >
              {{ CONFIDENCE_META[String(record.confidenceLevel)]!.label }}
            </Tag>
            <span v-else :style="{ color: themeColors.NEUTRAL }">—</span>
          </template>
          <template v-else-if="isCountColumn(column.key as string)">
            <span class="clpm-num font-mono">{{
              record[column.dataIndex as string] ?? 0
            }}</span>
          </template>
          <template v-else-if="isPlainNumberColumn(column.key as string)">
            <span class="clpm-num">
              {{ formatNumber(record[column.dataIndex as string]) }}
            </span>
          </template>
          <template v-else-if="isRateColumn(column.key as string)">
            <span class="clpm-num">
              {{ formatNumber(record[column.dataIndex as string], '%') }}
            </span>
          </template>
        </template>
      </Table>
    </ClpmDataCanvas>
  </Page>
</template>
