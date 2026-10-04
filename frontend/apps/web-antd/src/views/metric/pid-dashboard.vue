<script lang="ts" setup>
import type { EchartsUIType } from '@vben/plugins/echarts';

import type {
  DashboardApi,
  GradeDistributionResult,
  MetricApi,
} from '#/api';
import type { PlantNodeApi } from '#/api/plant-node';

import { computed, nextTick, onMounted, ref, watch } from 'vue';
import { useRouter } from 'vue-router';

import { IconifyIcon } from '@vben/icons';
import { EchartsUI, useEcharts } from '@vben/plugins/echarts';

import {
  Button,
  DatePicker,
  Drawer,
  RangePicker,
  Select,
  Table,
  TabPane,
  Tabs,
  Tag,
  Tooltip,
} from 'ant-design-vue';
import dayjs from 'dayjs';

import {
  ClpmBulletChart,
  ClpmEmptyState,
  ClpmLoopLink,
  ClpmPageToolbar,
  ClpmStandardActions,
} from '#/components/clpm';
import PlantNodeTree from '#/components/plant-node/plant-node-tree.vue';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import { useConfigAccess } from '#/composables/use-config-access';
import { useEchartsPreset } from '#/composables/use-echarts-preset';
import { showPageHelp, usePageToolbar } from '#/composables/use-page-toolbar';
import { useScoreColor } from '#/composables/use-score-color';
import { GRADE_THRESHOLDS } from '#/constants/clpm-ui';
import { normalizeUtcTimestamp } from '#/utils/format';

import GateHealthPanel from './components/gate-health-panel.vue';

defineOptions({ name: 'PidDashboard' });

const router = useRouter();
const { isDark, themeColors, chartColors } = useClpmTheme();
const { canReadConfig } = useConfigAccess();
const { axisBase, getTooltipPreset } = useEchartsPreset();

// ===== 时间选择（2026-10-03 裁决）：今日 / 按日 / 按周 / 按月 / 自定义 =====
// 语义统一由前端 resolveWindow 计算起止（今日=北京日历日 00:00 → 当前时刻；
// 周=周一起的 7 个日历日；自定义精度小时），全部以 timeWindow=custom +
// startTime/endTime 下发后端，根治本页三处窗口口径不一致问题。
type WindowMode = 'custom' | 'day' | 'month' | 'today' | 'week';

const windowMode = ref<WindowMode>('today');
const dayValue = ref(dayjs());
const weekValue = ref(dayjs());
const monthValue = ref(dayjs());
const customRange = ref<[dayjs.Dayjs, dayjs.Dayjs]>([dayjs().startOf('day'), dayjs()]);

const windowModeOptions = [
  { label: '今日', value: 'today' },
  { label: '按日', value: 'day' },
  { label: '按周', value: 'week' },
  { label: '按月', value: 'month' },
  { label: '自定义', value: 'custom' },
];

/** 与 locale 无关的周一锚点（中国惯例周一为首日） */
function startOfWeekMonday(d: dayjs.Dayjs): dayjs.Dayjs {
  const dayNum = (d.day() + 6) % 7; // 周一=0 … 周日=6
  return d.subtract(dayNum, 'day').startOf('day');
}

/** ISO 周数（无 weekOfYear 插件的手动算法：周四决定所属年份） */
function isoWeekNumber(d: dayjs.Dayjs): number {
  const monday = startOfWeekMonday(d);
  const thursday = monday.add(3, 'day');
  const yearStart = thursday.startOf('year');
  const yearStartMondayOffset = (yearStart.day() + 6) % 7;
  return (
    Math.floor((thursday.diff(yearStart, 'day') + yearStartMondayOffset) / 7) + 1
  );
}

/** 统一解析窗口 [start, end]（本地时间；end 钳制到当前时刻，未来不取数） */
function resolveWindow(): { end: dayjs.Dayjs; start: dayjs.Dayjs } {
  const now = dayjs();
  let start: dayjs.Dayjs;
  let end: dayjs.Dayjs;
  switch (windowMode.value) {
    case 'custom': {
      [start, end] = customRange.value;
      break;
    }
    case 'day': {
      start = dayValue.value.startOf('day');
      end = start.add(1, 'day');
      break;
    }
    case 'month': {
      start = monthValue.value.startOf('month');
      end = start.endOf('month').add(1, 'millisecond');
      break;
    }
    case 'week': {
      start = startOfWeekMonday(weekValue.value);
      end = start.add(7, 'day');
      break;
    }
    default: {
      start = now.startOf('day');
      end = now;
    }
  }
  if (end.isAfter(now)) end = now;
  return { start, end };
}

/** 指标分析页深链窗口映射：其窗口枚举为滚动窗预设，按当前窗口跨度取最近档 */
function nearestIndicatorWindow(): string {
  const { start, end } = resolveWindow();
  const hours = end.diff(start, 'hour', true);
  if (hours <= 8) return 'last_8_hours';
  if (hours <= 24) return 'today';
  if (hours <= 24 * 7) return 'last_7_days';
  return 'last_30_days';
}

/** 统一 API 窗口参数（custom + UTC ISO），所有数据接口共用同一窗口 */
const windowParams = computed<{
  endTime: string;
  startTime: string;
  timeWindow: 'custom';
}>(() => {
  const { start, end } = resolveWindow();
  return {
    timeWindow: 'custom',
    startTime: start.toISOString(),
    endTime: end.toISOString(),
  };
});

/** 禁止选择未来日期 */
const disableFutureDate = (current: dayjs.Dayjs) =>
  current.isAfter(dayjs().endOf('day'));

/** 当前时间窗中文标签（gauges 卡片统计窗口标注） */
const timeWindowLabel = computed(() => {
  switch (windowMode.value) {
    case 'custom': {
      const { start, end } = resolveWindow();
      return `${start.format('M-D HH:mm')}~${end.format('M-D HH:mm')}`;
    }
    case 'day': {
      return dayValue.value.format('M月D日');
    }
    case 'month': {
      return monthValue.value.format('YYYY年M月');
    }
    case 'week': {
      const monday = startOfWeekMonday(weekValue.value);
      return `${monday.add(3, 'day').year()}年第${isoWeekNumber(weekValue.value)}周`;
    }
    default: {
      return '今日';
    }
  }
});

/** P2 IA优化：fitness tag 中文映射（与其他模块共用） */
const PID_NA_TAG_CN: Record<string, string> = {
  // H1 修复（2026-10-01）：后端 loop_fitness.py 实际产出以下 7 标签
  // （T_* 系为历史标签，保留兼容旧快照；文案与后端 TAG_HUMAN_REASON 一致）
  DATA_INSUFFICIENT: '数据严重不足',
  MANUAL_DOMINANT: '手动模式占比过高',
  LOW_AUTO_RATE: '自控率极低',
  OP_SATURATED: 'OP 长期处于饱和限位附近',
  SP_PV_DEVIATION: 'SP-PV 长期偏离设定',
  NO_EXCITATION: 'OP 无有效激励',
  WEAK_RESPONSE: 'PV 对 OP 响应极弱',
  T_UNKNOWN: '未知',
  T_LOCAL_DATA_MISSING: '本地无历史数据',
  T_LOW_COVERAGE_7D: '近 7 日覆盖不足 50%',
  T_LOW_COVERAGE_30D: '近 30 日覆盖不足 50%',
  T_BAD_QUALITY: '数据质量差（PV 坏值/不确定）',
  T_MODE_NOT_AUTO: '当前处于手动控制模式',
  T_SETPOINT_MISSING: 'OPC 未绑定 SP 位号',
  T_OUTPUT_MISSING: 'OPC 未绑定 OP 位号',
  T_PID_PARAMS_INCOMPLETE: 'OPC 未绑定 P/I/D 位号',
  T_CONSTANT_SETPOINT: 'SP 长时间未变（如 30 天全恒定）',
  T_OOS_PV: 'PV 量程外点比例过高',
  T_BAD_OP_RANGE: 'OP 长期顶边或贴底（<5% / >95%）',
  T_DAMPED_OSC: '存在阻尼振荡趋势',
  T_SUSTAINED_OSC: '存在持续振荡趋势',
  T_VALVE_STICTION: '阀门疑似粘滞',
  T_DEADTIME_HIGH: '纯滞后/惯性比偏高',
  T_DRIFT: 'SP-PV 长期偏移（均值偏差）',
  T_HIGH_PV_NOISE: 'PV 高频噪声过大',
};
const pidNATagToCn = (t: string) => PID_NA_TAG_CN[t] ?? t;
/** 不适用（L0/L1）时的 Tooltip */
function fitnessNATip(
  level: null | string | undefined,
  tags: null | string[] | undefined,
): string {
  const lv = level ?? '';
  const tagText =
    tags && tags.length > 0
      ? tags.map((t) => pidNATagToCn(t)).join('、')
      : '适用性不足';
  return `不适用（${lv || 'NA'}）：${tagText}`;
}
/** 不适用时统一中性灰 slate（与其他模块一致，不红不警告） */
const FITNESS_NA_COLOR = 'var(--color-slate-500)';

const selectedPlantNodeId = ref<string | undefined>(undefined);
const selectedPlantNodeName = ref<string>('全厂');

/** 整改 A-13：工厂导航树抽屉化（默认收起，释放主区 15% 宽度） */
const treeDrawerOpen = ref(false);

function onTreeSelect(node: null | PlantNodeApi.PlantNode) {
  if (node) {
    selectedPlantNodeId.value = node.id;
    selectedPlantNodeName.value = node.name;
  } else {
    selectedPlantNodeId.value = undefined;
    selectedPlantNodeName.value = '全厂';
  }
  treeDrawerOpen.value = false;
  loadAll();
}

function handleWindowChange() {
  loadAll();
}

/** M3 联动（指标分析页）：携指标与当前时间窗深链跳转，回答"该指标弱在哪套装置" */
function goIndicatorAnalysis(metric: string) {
  router.push({
    path: '/metric/indicator-analysis',
    query: { metric, window: nearestIndicatorWindow() },
  });
}

const boardAggregate = ref<DashboardApi.BoardAggregateResult | null>(null);
const boardTrend = ref<DashboardApi.BoardTrendResult | null>(null);
const autoRateRt = ref<DashboardApi.AutoRateRt | null>(null);

/**
 * 实时数据过期阈值（分钟），超过则标灰/警示
 * P3-17：改为从环境变量读取，可在 .env 中配置 VITE_RT_STALE_MINUTES
 */
const RT_STALE_MINUTES =
  Number(import.meta.env.VITE_RT_STALE_MINUTES ?? 10) || 10;

/** 实时数据新鲜度：readAt 为空（DB 回退）或超过阈值视为过期 */
const rtStale = computed(() => {
  const readAt = autoRateRt.value?.readAt;
  if (!readAt) return true;
  return dayjs().diff(dayjs(readAt), 'minute') > RT_STALE_MINUTES;
});

/** 实时数据更新时间小字（状态饼图/实时自控率卡片角标） */
const rtReadAtText = computed(() => {
  const readAt = autoRateRt.value?.readAt;
  if (!readAt) return '实时数据中断';
  return `数据更新于 ${dayjs(readAt).format('HH:mm')}`;
});

const rankingList = ref<MetricApi.RankingItem[]>([]);
const gradingThresholds = ref<MetricApi.GradingThresholdItem[]>([]);
/** 各性能等级回路数分布（服务端 SQL 聚合，喂"回路等级占比"饼图） */
const gradeDistribution = ref<GradeDistributionResult | null>(null);

// ===== 整改 A-13：分布行列表数据（donut/饼图退役） =====

/** 回路状态统计行（MODE 分布；手动>0 时红色强调） */
const modeRows = computed(() => {
  const rt = autoRateRt.value;
  const total = rt?.totalCount ?? 0;
  const counts = rt?.modeCounts ?? {};
  const order: { key: string; label: string }[] = [
    { key: '1', label: '自动' },
    { key: '2', label: '串级' },
    { key: '3', label: '远程' },
    { key: '4', label: '先控' },
    { key: '0', label: '手动' },
  ];
  return order
    .map((o) => {
      const count = counts[o.key] ?? 0;
      return {
        label: o.label,
        count,
        pct: total > 0 ? Math.round((count / total) * 100) : 0,
        color: o.key === '0' ? 'var(--status-error)' : 'var(--color-slate-400)',
        emphasis: o.key === '0' && count > 0,
      };
    })
    .filter((r) => r.count > 0 || total === 0);
});

/** 等级分布行（按定级阈值顺序 + 数据不足；等级语义色） */
const gradeRows = computed(() => {
  const dist = gradeDistribution.value;
  if (!dist) return [];
  const distMap = dist as unknown as Record<string, number>;
  const total = dist.total ?? 0;
  const rows = [...effectiveThresholds.value]
    .toSorted((a, b) => a.level - b.level)
    .map((t) => {
      const count = distMap[t.name] ?? 0;
      return {
        label: ratingLabels.value[String(t.level)] ?? t.name,
        count,
        pct: total > 0 ? Math.round((count / total) * 100) : 0,
        color: gradeColor(t.level),
      };
    });
  rows.push({
    label: '数据不足',
    count: dist.INCONCLUSIVE ?? 0,
    pct: total > 0 ? Math.round(((dist.INCONCLUSIVE ?? 0) / total) * 100) : 0,
    color: 'var(--status-neutral)',
  });
  return rows;
});

// ===== 2026-10-03 改版：适用性分层 L0~L4（评估/诊断/整定共用口径；三性分离见改版方案 §5） =====
// 分层语义与 loop_fitness.py 一致：L0 数据不足 / L1 仅可监视 / L2 条件异常 / L3 待激励 / L4 可优化
const FITNESS_LEVEL_META: {
  color: string;
  key: string;
  label: string;
}[] = [
  { key: 'L0', label: 'L0 数据不足', color: 'var(--status-error)' },
  { key: 'L1', label: 'L1 仅可监视', color: 'var(--status-error)' },
  { key: 'L2', label: 'L2 条件异常', color: 'var(--status-warning)' },
  { key: 'L3', label: 'L3 待激励', color: 'var(--status-info)' },
  { key: 'L4', label: 'L4 可优化', color: 'var(--status-success)' },
];

// ===== 三性分离（R5，2026-10-03）：可评估/可诊断/可整定分布 =====
// 数据源 grade-distribution 的 assessDistribution/diagnoseDistribution/tuneDistribution
// （每回路最新快照口径，旧快照三列 NULL 已由后端 COALESCE 回退 fitness_level）
const DIMENSION_LABELS: Record<string, string> = {
  assess: '可评估性',
  diagnose: '可诊断性',
  tune: '可整定性',
};

const fitnessRows = computed(() => {
  const dist = gradeDistribution.value;
  if (!dist) return [];
  const withSnap = Number(dist.fitnessDistribution?.total ?? dist.total ?? 0);
  const plantTotal = aggregateData.value?.totalLoops ?? 0;
  const rows = FITNESS_LEVEL_META.map((m) => {
    const count = Number(dist.fitnessDistribution?.[m.key] ?? 0);
    return {
      label: m.label,
      count,
      pct: withSnap > 0 ? Math.round((count / withSnap) * 100) : 0,
      color: m.color,
    };
  });
  // 对账行：窗口内无快照回路（全量活跃 − 有快照）
  const noSnap = Math.max(plantTotal - withSnap, 0);
  if (plantTotal > 0) {
    rows.push({
      label: '无快照',
      count: noSnap,
      pct: plantTotal > 0 ? Math.round((noSnap / plantTotal) * 100) : 0,
      color: 'var(--status-neutral)',
    });
  }
  return rows;
});

/** 三性各维度分布行（堆叠条 + 可用/阻断摘要） */
const dimensionRows = computed(() => {
  const dist = gradeDistribution.value as unknown as null | Record<string, Record<string, number>>;
  if (!dist) return [];
  const dims: { key: string; label: string }[] = [
    { key: 'assessDistribution', label: DIMENSION_LABELS.assess! },
    { key: 'diagnoseDistribution', label: DIMENSION_LABELS.diagnose! },
    { key: 'tuneDistribution', label: DIMENSION_LABELS.tune! },
  ];
  const out: {
    blocked: number;
    colorScale: string[];
    key: string;
    label: string;
    open: number;
    segs: { count: number; label: string; pct: number }[];
    total: number;
  }[] = [];
  for (const d of dims) {
    const raw = dist[d.key];
    if (!raw || typeof raw !== 'object') continue;
    const total = Number(raw.total ?? 0);
    const segs = FITNESS_LEVEL_META.map((m) => {
      const count = Number(raw[m.key] ?? 0);
      return {
        label: m.label,
        count,
        pct: total > 0 ? (count / total) * 100 : 0,
      };
    });
    const blocked = (Number(raw.L0) || 0) + (Number(raw.L1) || 0);
    const open = (Number(raw.L2) || 0) + (Number(raw.L3) || 0) + (Number(raw.L4) || 0);
    out.push({
      key: d.key,
      label: d.label,
      segs,
      total,
      blocked,
      open,
      colorScale: FITNESS_LEVEL_META.map((m) => m.color),
    });
  }
  return out;
});

/** 等级/适用性卡当前 Tab */
const distTab = ref<'fitness' | 'grade'>('grade');

// ===== 整改 F4：阀门运行区间异常（OP 行程越限 5%~95%） =====
// 2026-10-03 改版：改走服务端聚合 /performance/valve-alerts（TOP N + 总数），
// 替代前端全量翻页拉快照再客户端过滤（生产 961 回路 = 10 页串行请求）且全量渲染无上限
const VALVE_TOP_N = 10;
interface ValveAlertRow {
  loopId: string;
  tagName: string;
  loopName: null | string;
  range: string;
}

const valveAlerts = ref<ValveAlertRow[]>([]);
const valveAlertsTotal = ref(0);

async function loadValveAlerts() {
  try {
    const { getValveAlertsApi } = await import('#/api/metric');
    const res = await getValveAlertsApi({
      plantNodeId: selectedPlantNodeId.value,
      ...windowParams.value,
      limit: VALVE_TOP_N,
    });
    valveAlertsTotal.value = res.total ?? 0;
    valveAlerts.value = (res.items ?? []).map((it) => ({
      loopId: it.loopId,
      tagName: it.tagName,
      loopName: it.loopName,
      range: `OP ${it.valveOpMin?.toFixed(1) ?? '—'}% ~ ${
        it.valveOpMax?.toFixed(1) ?? '—'
      }%`,
    }));
  } catch {
    valveAlerts.value = [];
    valveAlertsTotal.value = 0;
  }
}

// 整改 C2-3：默认评分升序（最差优先），管理者注意力直达 Bad Actor
// 2026-10-03 改版：TOP5 治理台账 → 待治理 TOP10（服务端 fitnessFilter 剔除 L0/L1）
const GOVERNANCE_TOP_N = 10;
const topNSort = ref<'asc' | 'desc'>('asc');

const aggregateData = computed(() => boardAggregate.value?.aggregate);

const topNList = computed(() => {
  const items = [...rankingList.value];
  if (topNSort.value === 'asc') {
    items.sort((a, b) => (a.score ?? 0) - (b.score ?? 0));
  } else {
    items.sort((a, b) => (b.score ?? 0) - (a.score ?? 0));
  }
  return items.slice(0, GOVERNANCE_TOP_N);
});

// 默认定级阈值（国标 GB/T 44693.2-2024 §6.3，与 use-score-color 内部默认值同口径；
// 不配置 color 字段：配色统一走 gradeColor 的阈值配置色 > ZL 语义色降级链）
// 0929 口径收敛：档位定义唯一源在 constants/clpm-ui（GRADE_THRESHOLDS）
const DEFAULT_THRESHOLDS: MetricApi.GradingThresholdItem[] = GRADE_THRESHOLDS;

/** 生效阈值集：动态配置优先，为空时降级默认阈值 */
const effectiveThresholds = computed<MetricApi.GradingThresholdItem[]>(() =>
  gradingThresholds.value.length > 0
    ? gradingThresholds.value
    : DEFAULT_THRESHOLDS,
);

// 定级阈值等级中文显示名（从配置读取，降级用默认值）
const ratingLabels = computed<Record<string, string>>(() => {
  const labels: Record<string, string> = {};
  for (const t of effectiveThresholds.value) {
    labels[String(t.level)] = t.label ?? t.name;
  }
  return labels;
});

/**
 * 等级配色：阈值项自带 color 优先，未配置时按档位降级到 ZL 语义色
 * （降级链与 use-score-color 一致；无评分场景不调用本函数）
 */
function gradeColor(level: number): string {
  const t = effectiveThresholds.value.find((item) => item.level === level);
  if (t?.color) return t.color;
  const fallbackByLevel: Record<number, string> = {
    1: themeColors.value.SUCCESS,
    2: themeColors.value.INFO,
    3: themeColors.value.WARNING,
    4: themeColors.value.DANGER,
    5: themeColors.value.DANGER,
  };
  return fallbackByLevel[level] ?? themeColors.value.NEUTRAL;
}

/**
 * 按评分判定等级（level 字符串，'1' 最优；无评分返回 null）
 * 匹配逻辑与 useScoreColor 一致：按 minScore 降序首个 score >= minScore，都不命中取最低档
 */
function getRatingLevel(score: null | number | undefined): null | string {
  if (score === null || score === undefined || Number.isNaN(score)) return null;
  for (const t of [...effectiveThresholds.value].toSorted(
    (a, b) => b.minScore - a.minScore,
  )) {
    if (score >= t.minScore) return String(t.level);
  }
  return String(
    effectiveThresholds.value[effectiveThresholds.value.length - 1]?.level ?? 5,
  );
}

const tableColumns = [
  {
    title: '名称',
    dataIndex: 'name',
    key: 'name',
    width: 200,
    align: 'left' as const,
  },
  {
    title: '性能评级',
    dataIndex: 'rating',
    key: 'rating',
    width: 80,
    align: 'center' as const,
  },
  {
    title: '性能评分',
    dataIndex: 'score',
    key: 'score',
    width: 80,
    align: 'right' as const,
  },
  {
    title: '平稳率',
    dataIndex: 'smoothRate',
    key: 'smoothRate',
    width: 80,
    align: 'right' as const,
  },
  {
    title: '自控率',
    dataIndex: 'autoRate',
    key: 'autoRate',
    width: 80,
    align: 'right' as const,
  },
  {
    title: '回路总数',
    dataIndex: 'totalLoops',
    key: 'totalLoops',
    width: 80,
    align: 'right' as const,
  },
];

/**
 * 明细表树数据（2026-10-03 改版：board/tree 一次取整棵子树，可折叠）
 * 树形下行序=层级序（树结构下按评分重排会破坏层级）；
 * 无快照节点指标显示 —（诚实化：不把「无数据」渲染成 0）
 */
interface TableTreeNode {
  children?: TableTreeNode[];
  key: string;
  name: string;
  rating: null | string;
  ratingColor: string;
  score: string;
  totalLoops: number;
  autoRate: string;
  smoothRate: string;
}

const boardTree = ref<DashboardApi.BoardTreeItem[]>([]);
/** 默认展开首层（根节点行） */
const tableExpandedRowKeys = ref<string[]>([]);

function mapTreeItem(node: DashboardApi.BoardTreeItem): TableTreeNode {
  const rating = node.hasSnapshot ? getRatingLevel(node.avgScore) : null;
  const children = node.children?.map((c) => mapTreeItem(c)) ?? [];
  return {
    key: node.nodeId,
    name: node.nodeName ?? '',
    rating,
    ratingColor: rating ? gradeColor(Number(rating)) : '',
    score: formatNumber(node.avgScore),
    totalLoops: node.totalLoops ?? 0,
    autoRate: formatNumber(node.autoModeRate),
    smoothRate: formatNumber(node.stabilityRate),
    ...(children.length > 0 ? { children } : {}),
  };
}

const tableData = computed<TableTreeNode[]>(() =>
  boardTree.value.map((root) => mapTreeItem(root)),
);

async function loadBoardTree() {
  try {
    const { getBoardTreeApi } = await import('#/api/dashboard');
    const res = await getBoardTreeApi({
      ...(selectedPlantNodeId.value && { plantId: selectedPlantNodeId.value }),
      ...windowParams.value,
    });
    boardTree.value = res.items ?? [];
    tableExpandedRowKeys.value = boardTree.value.map((n) => n.nodeId);
  } catch {
    boardTree.value = [];
    tableExpandedRowKeys.value = [];
  }
}

const topNColumns = [
  {
    title: '序号',
    dataIndex: 'index',
    key: 'index',
    width: 40,
    align: 'center' as const,
  },
  {
    title: '位号',
    dataIndex: 'tagName',
    key: 'tagName',
    width: 140,
    ellipsis: true,
  },
  { title: '名称', dataIndex: 'loopName', key: 'loopName', ellipsis: true },
  {
    title: '性能评级',
    dataIndex: 'rating',
    key: 'rating',
    width: 70,
    align: 'center' as const,
  },
  {
    title: '性能评分',
    dataIndex: 'score',
    key: 'score',
    width: 70,
    align: 'right' as const,
  },
  {
    title: '平稳率',
    dataIndex: 'steadyRate',
    key: 'steadyRate',
    width: 65,
    align: 'right' as const,
  },
];

const topNTableData = computed(() => {
  return topNList.value.map((item, index) => {
    const fitnessLevel = item.fitnessLevel ?? null;
    const fitnessTags = Array.isArray(item.fitnessTags)
      ? item.fitnessTags
      : null;
    const isFitnessNA = fitnessLevel === 'L0' || fitnessLevel === 'L1';
    const ratingLevel = getRatingLevel(item.score);
    const ratingLabel = ratingLevel
      ? (ratingLabels.value[ratingLevel] ?? `L${ratingLevel}`)
      : '—';
    return {
      key: item.loopId,
      index: index + 1,
      loopId: item.loopId,
      tagName: item.tagName,
      loopName: item.loopName || item.tagName || '—',
      // P2 IA优化：L0/L1 显示"不适用"中性灰
      ratingText: isFitnessNA ? '不适用' : ratingLabel,
      ratingColor: isFitnessNA
        ? FITNESS_NA_COLOR
        : (ratingLevel
          ? gradeColor(Number(ratingLevel))
          : ''),
      isFitnessNA,
      fitnessNATipText: isFitnessNA
        ? fitnessNATip(fitnessLevel, fitnessTags)
        : '',
      score: isFitnessNA ? '—' : formatNumber(item.score),
      scoreColor: isFitnessNA ? FITNESS_NA_COLOR : scoreColor(item.score),
      steadyRate: `${formatNumber(item.steadyRate)}%`,
    };
  });
});

const trendChartRef = ref<EchartsUIType>();

const { renderEcharts: renderTrend } = useEcharts(trendChartRef);

function renderTrendChart() {
  const trend = boardTrend.value;
  if (!trend || !trend.timestamps?.length) return;

  // 2026-10-03 改版：恒柱状（不再随窗口切换柱/线漂移）；
  // day 粒度 timestamps 为北京日字符串（YYYY-MM-DD）直接格式化，
  // hour 粒度沿用"补 Z 转本地"约定（后端为无时区后缀的 UTC ISO8601）
  const isDay = trend.granularity === 'day';
  const timestamps = trend.timestamps.map((ts) =>
    isDay
      ? dayjs(ts).format('M-D')
      : dayjs(normalizeUtcTimestamp(ts)).format('M-D H:00'),
  );

  const barDataTotal =
    (trend.totalLoops ?? 0) > 0 ? timestamps.map(() => trend.totalLoops) : [];
  const barDataEvaluated = trend.evaluatedLoops ?? [];

  renderTrend({
    grid: { bottom: 40, left: '2%', right: '2%', top: 20, containLabel: true },
    xAxis: {
      ...axisBase.value,
      type: 'category',
      data: timestamps,
      axisTick: { show: false },
    },
    yAxis: [
      {
        ...axisBase.value,
        type: 'value',
        name: '回路数',
        nameTextStyle: { color: chartColors.value.text, fontSize: 11 },
      },
      {
        ...axisBase.value,
        type: 'value',
        name: '百分比(%)',
        nameTextStyle: { color: chartColors.value.text, fontSize: 11 },
        axisLabel: {
          color: chartColors.value.text,
          fontSize: 10,
          formatter: '{value}%',
          hideOverlap: true,
        },
        max: 100,
        splitLine: { show: false },
      },
    ],
    series: [
      {
        name: '总回路数',
        type: 'bar' as const,
        data: barDataTotal,
        itemStyle: { color: themeColors.value.INFO },
        barMaxWidth: 26,
      },
      {
        name: '参评回路数',
        type: 'bar' as const,
        data: barDataEvaluated,
        itemStyle: { color: themeColors.value.SUCCESS },
        barMaxWidth: 26,
      },
      {
        name: '性能评分',
        type: 'line' as const,
        yAxisIndex: 1,
        data: trend.avgScore ?? [],
        smooth: true,
        itemStyle: { color: themeColors.value.WARNING },
        lineStyle: { width: 2 },
        symbol: 'circle',
        symbolSize: 6,
        areaStyle: {
          color: {
            type: 'linear',
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [
              { offset: 0, color: `${themeColors.value.WARNING}30` },
              { offset: 1, color: `${themeColors.value.WARNING}05` },
            ],
          },
        },
      },
      {
        name: '自控率',
        type: 'line' as const,
        yAxisIndex: 1,
        data: trend.autoModeRate ?? [],
        smooth: true,
        itemStyle: { color: themeColors.value.INFO },
        lineStyle: { width: 2 },
        symbol: 'circle',
        symbolSize: 6,
        areaStyle: {
          color: {
            type: 'linear',
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [
              { offset: 0, color: `${themeColors.value.INFO}30` },
              { offset: 1, color: `${themeColors.value.INFO}05` },
            ],
          },
        },
      },
      {
        name: '平稳率',
        type: 'line' as const,
        yAxisIndex: 1,
        data: trend.stabilityRate ?? [],
        smooth: true,
        itemStyle: { color: themeColors.value.SUCCESS },
        lineStyle: { width: 2 },
        symbol: 'circle',
        symbolSize: 6,
        areaStyle: {
          color: {
            type: 'linear',
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [
              { offset: 0, color: `${themeColors.value.SUCCESS}30` },
              { offset: 1, color: `${themeColors.value.SUCCESS}05` },
            ],
          },
        },
      },
    ],
    tooltip: {
      ...getTooltipPreset(),
      trigger: 'axis',
      axisPointer: { type: 'cross' },
    },
    legend: {
      bottom: 0,
      textStyle: { color: chartColors.value.text, fontSize: 11 },
      data: ['总回路数', '参评回路数', '性能评分', '自控率', '平稳率'],
    },
  });
}

/**
 * 评分 → 颜色（表单元格等按值取色场景）。
 * 统一走 useScoreColor：动态 gradingThresholds 定档，null/NaN → ZL 中性灰
 * （"数据不足"不是"不合格"，严禁渲染为故障红）。
 */
function scoreColor(score: null | number | undefined): string {
  return useScoreColor(score, gradingThresholds).color.value;
}

function formatNumber(val: null | number | undefined, digits = 1): string {
  if (val === null || val === undefined || Number.isNaN(val)) return '--';
  return Number(val).toFixed(digits);
}

async function loadBoard() {
  try {
    const { getBoardAggregateApi, getBoardTrendApi } =
      await import('#/api/dashboard');
    const [aggregate, trend] = await Promise.all([
      getBoardAggregateApi({
        ...(selectedPlantNodeId.value && {
          plantId: selectedPlantNodeId.value,
        }),
        ...windowParams.value,
      }),
      getBoardTrendApi({
        ...(selectedPlantNodeId.value && {
          plantId: selectedPlantNodeId.value,
        }),
        ...windowParams.value,
        granularity: 'auto',
      }),
    ]);
    boardAggregate.value = aggregate;
    boardTrend.value = trend;
    await nextTick();
    renderTrendChart();
  } catch (error) {
    console.error('[CLPM] 加载看板数据失败:', error);
  }
}

async function loadAutoRateRt() {
  try {
    const { getAutoRateRtApi } = await import('#/api/dashboard');
    const data = await getAutoRateRtApi(
      selectedPlantNodeId.value ? { plantId: selectedPlantNodeId.value } : {},
    );
    autoRateRt.value = data;
    await nextTick();
  } catch {
    // ignore
  }
}

/**
 * 待治理 TOP N 排行：服务端排序 + limit 单次请求。
 * 2026-10-03 改版：TOP10 + fitnessFilter=true（服务端先剔除最新快照为
 * L0/L1 的回路再截断——「待治理」语义=可采取治理动作的回路，
 * 客户端过滤在 L0/L1 ≥ limit 时会把榜单滤空）
 */
async function loadRanking() {
  try {
    const { getRankingApi } = await import('#/api/metric');
    const items = await getRankingApi({
      plantNodeId: selectedPlantNodeId.value,
      ...windowParams.value,
      sortBy: 'score',
      sortOrder: topNSort.value,
      limit: GOVERNANCE_TOP_N,
      fitnessFilter: true,
    });
    rankingList.value = items.filter((it) => it.includeInEvaluation !== false);
  } catch {
    // 错误 toast 由拦截器统一处理；保留旧数据
  }
}

/** 加载等级分布 + 三性分布（服务端 GROUP BY 聚合，窗口与其他接口同源） */
async function loadGradeDistribution() {
  try {
    const { getGradeDistributionApi } = await import('#/api/metric');
    const { start, end } = resolveWindow();
    gradeDistribution.value = await getGradeDistributionApi({
      ...(selectedPlantNodeId.value && {
        plantNodeId: selectedPlantNodeId.value,
      }),
      startTime: start.toISOString(),
      endTime: end.toISOString(),
    });
    await nextTick();
  } catch {
    // 错误 toast 由拦截器统一处理
  }
}

async function loadGradingThresholds() {
  // 整改 C2-1：SPONSOR/EXPERT 无 /configs/* 读取权限，前置跳过避免 403 toast
  if (!canReadConfig.value) return;
  try {
    const { getGradingThresholdsApi } = await import('#/api/metric');
    const data = await getGradingThresholdsApi();
    gradingThresholds.value = data.thresholds ?? [];
  } catch {
    // 加载失败时使用默认阈值
  }
}

function loadAll() {
  loadBoard();
  loadBoardTree();
  loadAutoRateRt();
  loadRanking();
  loadGradeDistribution();
  loadValveAlerts();
}

watch(topNSort, () => loadRanking());

watch(isDark, () => {
  nextTick(() => {
    renderTrendChart();
  });
});

/** 工具栏刷新态（刷新时短暂保持供工具栏反馈） */
const loading = ref(false);

/** 工具栏刷新：重新加载看板全部数据 */
function handleRefresh() {
  loading.value = true;
  loadAll();
  // loadAll 为非阻塞（内部各子任务各自 await），加保护性复位
  setTimeout(() => {
    loading.value = false;
  }, 600);
}

/** 工具栏帮助 */
function handleHelp() {
  showPageHelp({
    title: '评估看板 帮助',
    content:
      '工厂级 KPI 评估看板：实时自控率、性能评分、自控率/平稳率/好值率/仪表故障率 6 仪表盘 + 性能指标趋势图（恒柱状，长窗口自动按日聚合）+ 等级/适用性 L0~L4 分布 + 装置/单元可折叠明细表 + 待治理 TOP10 + 阀门越限 TOP10。支持按工厂节点树筛选与时间窗口切换（近 8h / 24h / 168h / 近 1 月）。',
  });
}

// ===== 统一工具栏（标准 2 工具：刷新 / 帮助） =====
const { toolbarItems } = usePageToolbar(() => ({
  refresh: { onClick: handleRefresh, loading: loading.value },
  help: { onClick: handleHelp },
}));

onMounted(() => {
  loadGradingThresholds();
  loadAll();
});
</script>

<template>
  <!-- 2026-10-03 改版（R6）：不走 vben Page 组件（自带 padding 破坏固定视口），
       方法对齐回路工作台 workbench360——整页零滚动，1080 设计基准 -->
  <div class="clpm-pid-dashboard">
      <ClpmPageToolbar
        title="评估看板"
        subtitle="工厂级 KPI 仪表盘 · 趋势 · 等级分布 · 待治理 TOP10"
        :loading="loading"
      >
        <Button size="small" @click="treeDrawerOpen = true">
          <template #icon>
            <IconifyIcon icon="lucide:git-fork" />
          </template>
          {{ selectedPlantNodeName }}
        </Button>
        <!-- 时间选择（2026-10-03 裁决）：今日 / 按日 / 按周 / 按月 / 自定义（精度小时） -->
        <Select
          v-model:value="windowMode"
          style="width: 92px"
          size="small"
          :options="windowModeOptions"
          @change="handleWindowChange"
        />
        <DatePicker
          v-if="windowMode === 'day'"
          v-model:value="dayValue"
          size="small"
          :allow-clear="false"
          :disabled-date="disableFutureDate"
          @change="handleWindowChange"
        />
        <DatePicker
          v-else-if="windowMode === 'week'"
          v-model:value="weekValue"
          picker="week"
          size="small"
          :allow-clear="false"
          :disabled-date="disableFutureDate"
          @change="handleWindowChange"
        />
        <DatePicker
          v-else-if="windowMode === 'month'"
          v-model:value="monthValue"
          picker="month"
          size="small"
          :allow-clear="false"
          :disabled-date="disableFutureDate"
          @change="handleWindowChange"
        />
        <RangePicker
          v-else-if="windowMode === 'custom'"
          v-model:value="customRange"
          show-time
          format="MM-DD HH:00"
          size="small"
          :allow-clear="false"
          :disabled-date="disableFutureDate"
          @change="handleWindowChange"
        />
        <template #actions>
          <ClpmStandardActions :items="toolbarItems" />
        </template>
      </ClpmPageToolbar>

      <!-- 评估门禁健康（0921 监控面板）：断点比例 vs 门禁门槛 -->
      <GateHealthPanel class="mb-3" />

      <div class="clpm-pid-dashboard__body">
        <div class="clpm-pid-dashboard__main">
          <div class="clpm-pid-dashboard__top-row">
            <div class="clpm-pid-dashboard__gauge-card">
              <ClpmBulletChart
                label="实时自控率"
                :value="autoRateRt?.rate ?? null"
                :meta="rtReadAtText"
              />
            </div>

            <div class="clpm-pid-dashboard__gauge-card">
              <ClpmBulletChart
                label="性能评分"
                :value="aggregateData?.avgScore ?? null"
                :meta="`统计窗口：${timeWindowLabel}`"
              />
            </div>

            <div class="clpm-pid-dashboard__gauge-card">
              <ClpmBulletChart
                label="自控率"
                :value="aggregateData?.autoModeRate ?? null"
                :meta="`统计窗口：${timeWindowLabel}`"
              />
              <a
                class="clpm-pid-dashboard__gauge-analysis"
                @click="goIndicatorAnalysis('auto_mode_rate')"
              >
                单指标分析 →
              </a>
            </div>

            <div class="clpm-pid-dashboard__gauge-card">
              <ClpmBulletChart
                label="平稳率"
                :value="aggregateData?.stabilityRate ?? null"
                :meta="`统计窗口：${timeWindowLabel}`"
              />
              <a
                class="clpm-pid-dashboard__gauge-analysis"
                @click="goIndicatorAnalysis('steady_rate')"
              >
                单指标分析 →
              </a>
            </div>

            <div class="clpm-pid-dashboard__gauge-card">
              <ClpmBulletChart
                label="好值率"
                :value="aggregateData?.goodValueRate ?? null"
                :meta="`统计窗口：${timeWindowLabel}`"
              />
              <a
                class="clpm-pid-dashboard__gauge-analysis"
                @click="goIndicatorAnalysis('good_value_rate')"
              >
                单指标分析 →
              </a>
            </div>

            <div class="clpm-pid-dashboard__gauge-card">
              <ClpmBulletChart
                label="仪表故障率"
                :value="aggregateData?.instrumentFaultRate ?? null"
                :max="30"
                :fair="5"
                :good="10"
                invert
                :meta="`统计窗口：${timeWindowLabel}`"
              />
            </div>
          </div>

          <div class="clpm-pid-dashboard__middle-row">
            <div
              class="clpm-pid-dashboard__chart-card clpm-pid-dashboard__chart-card--status-pie"
            >
              <div class="clpm-pid-dashboard__card-header">
                <span>回路状态统计</span>
                <span
                  class="clpm-pid-dashboard__card-meta"
                  :class="{
                    'clpm-pid-dashboard__card-meta--stale': rtStale,
                  }"
                >
                  {{ rtReadAtText }}
                </span>
              </div>
              <div class="clpm-pid-dashboard__mode-list">
                <div
                  v-for="row in modeRows"
                  :key="row.label"
                  class="clpm-pid-dashboard__dist-row"
                >
                  <span class="clpm-pid-dashboard__dist-label">{{
                    row.label
                  }}</span>
                  <span class="clpm-pid-dashboard__dist-track">
                    <i
                      :style="{
                        width: `${row.pct}%`,
                        background: row.color,
                      }"
                    ></i>
                  </span>
                  <span
                    class="clpm-pid-dashboard__dist-count"
                    :style="
                      row.emphasis ? { color: 'var(--status-error)' } : {}
                    "
                    >{{ row.count }}</span
                  >
                </div>
                <div
                  v-if="modeRows.length === 0"
                  class="py-6 text-center text-xs text-gray-400"
                >
                  暂无实时数据
                </div>
              </div>
            </div>

            <div
              class="clpm-pid-dashboard__chart-card clpm-pid-dashboard__chart-card--trend"
            >
              <div class="clpm-pid-dashboard__card-header">
                <span>性能指标趋势图</span>
              </div>
              <EchartsUI ref="trendChartRef" height="240px" />
            </div>

            <div
              class="clpm-pid-dashboard__chart-card clpm-pid-dashboard__chart-card--pie"
            >
              <Tabs v-model:active-key="distTab" size="small">
                <TabPane key="grade" tab="等级占比" />
                <TabPane key="fitness" tab="适用性 L0~L4" />
              </Tabs>
              <div class="clpm-pid-dashboard__grade-list">
                <template v-if="distTab === 'grade'">
                  <div
                    v-for="row in gradeRows"
                    :key="row.label"
                    class="clpm-pid-dashboard__dist-row"
                  >
                    <span class="clpm-pid-dashboard__dist-label">{{
                      row.label
                    }}</span>
                    <span class="clpm-pid-dashboard__dist-track">
                      <i
                        :style="{
                          width: `${row.pct}%`,
                          background: row.color,
                        }"
                      ></i>
                    </span>
                    <span class="clpm-pid-dashboard__dist-count">{{
                      row.count
                    }}</span>
                  </div>
                </template>
                <template v-else>
                  <div
                    v-if="dimensionRows.length === 0 && fitnessRows.length === 0"
                    class="py-6 text-center text-xs text-gray-400"
                  >
                    暂无适用性数据
                  </div>
                  <!-- 三性分离（R5）：三维度堆叠条 + 可用/阻断摘要 -->
                  <div
                    v-for="dim in dimensionRows"
                    :key="dim.key"
                    class="clpm-pid-dashboard__dim-block"
                  >
                    <div class="clpm-pid-dashboard__dim-head">
                      <span class="clpm-pid-dashboard__dim-label">{{
                        dim.label
                      }}</span>
                      <span class="clpm-pid-dashboard__dim-summary">
                        可用 {{ dim.open }} / 阻断 {{ dim.blocked }} / 共
                        {{ dim.total }}
                      </span>
                    </div>
                    <div class="clpm-pid-dashboard__dim-stack">
                      <Tooltip
                        v-for="(seg, i) in dim.segs"
                        :key="seg.label"
                        :title="`${seg.label}：${seg.count}（${seg.pct.toFixed(0)}%）`"
                      >
                        <i
                          :style="{
                            width: `${seg.pct}%`,
                            background: dim.colorScale[i],
                          }"
                        ></i>
                      </Tooltip>
                    </div>
                  </div>
                  <!-- 兼容降级：三性分布缺失时显示综合 L0~L4 行列表 -->
                  <template v-if="dimensionRows.length === 0">
                    <div
                      v-for="row in fitnessRows"
                      :key="row.label"
                      class="clpm-pid-dashboard__dist-row"
                    >
                      <span class="clpm-pid-dashboard__dist-label">{{
                        row.label
                      }}</span>
                      <span class="clpm-pid-dashboard__dist-track">
                        <i
                          :style="{
                            width: `${row.pct}%`,
                            background: row.color,
                          }"
                        ></i>
                      </span>
                      <span class="clpm-pid-dashboard__dist-count">{{
                        row.count
                      }}</span>
                    </div>
                  </template>
                  <div class="clpm-pid-dashboard__dist-note">
                    维度口径可在 配置 → 指标配置 → 适用性 Tab 调整
                  </div>
                </template>
              </div>
            </div>
          </div>

          <div class="clpm-pid-dashboard__bottom-row">
            <div class="clpm-pid-dashboard__table-card">
              <div class="clpm-pid-dashboard__card-header">
                <span>装置/单元明细（可折叠）</span>
              </div>
              <Table
                :columns="tableColumns"
                :data-source="tableData"
                :pagination="false"
                :scroll="{ y: 340 }"
                :expanded-row-keys="tableExpandedRowKeys"
                size="small"
                @expanded-rows-change="
                  (keys: (number | string)[]) =>
                    (tableExpandedRowKeys = keys.map(String))
                "
              >
                <template #headerCell="{ column }">
                  <!-- M3 联动：列头“分析”深链指标分析页（按该指标找最差装置/回路） -->
                  <template v-if="column.key === 'smoothRate'">
                    平稳率
                    <a
                      class="clpm-pid-dashboard__gauge-analysis"
                      @click="goIndicatorAnalysis('steady_rate')"
                    >
                      分析
                    </a>
                  </template>
                  <template v-else-if="column.key === 'autoRate'">
                    自控率
                    <a
                      class="clpm-pid-dashboard__gauge-analysis"
                      @click="goIndicatorAnalysis('auto_mode_rate')"
                    >
                      分析
                    </a>
                  </template>
                </template>
                <template #bodyCell="{ column, record }">
                  <template v-if="column.key === 'rating'">
                    <span
                      v-if="record.rating"
                      class="clpm-pid-dashboard__rating-tag"
                      :style="{
                        color: record.ratingColor,
                        backgroundColor: `${record.ratingColor}1A`,
                      }"
                    >
                      {{ ratingLabels[record.rating] }}
                    </span>
                    <span v-else>—</span>
                  </template>
                  <template v-if="column.key === 'autoRate'">
                    <span>{{ record.autoRate }}%</span>
                  </template>
                  <template v-if="column.key === 'smoothRate'">
                    <span>{{ record.smoothRate }}%</span>
                  </template>
                </template>
                <template #emptyText>
                  <ClpmEmptyState
                    scene="data"
                    description="当前装置节点与时间窗内无聚合明细；可切换时间窗或选择其他节点。"
                  />
                </template>
              </Table>
            </div>

            <div class="clpm-pid-dashboard__top5-card">
              <div class="clpm-pid-dashboard__card-header">
                <span>待治理 TOP{{ GOVERNANCE_TOP_N }}</span>
                <Tooltip
                  :title="
                    topNSort === 'desc'
                      ? '当前：评分最高，点击切换为最低'
                      : '当前：评分最低，点击切换为最高'
                  "
                >
                  <Button
                    type="text"
                    size="small"
                    class="clpm-pid-dashboard__sort-btn"
                    :aria-label="
                      topNSort === 'desc'
                        ? '当前按评分最低排序，切换为最高'
                        : '当前按评分最高排序，切换为最低'
                    "
                    @click="topNSort = topNSort === 'desc' ? 'asc' : 'desc'"
                  >
                    <IconifyIcon
                      :icon="
                        topNSort === 'desc'
                          ? 'ant-design:sort-descending-outlined'
                          : 'ant-design:sort-ascending-outlined'
                      "
                    />
                  </Button>
                </Tooltip>
              </div>
              <Table
                :columns="topNColumns"
                :data-source="topNTableData"
                :pagination="false"
                :scroll="{ y: 340 }"
                size="small"
              >
                <template #bodyCell="{ column, record }">
                  <template v-if="column.key === 'tagName'">
                    <!-- F-PID-002：位号接 LoopLink，默认跳诊断（待治理用户任务=找最差回路去处置），
                         下拉菜单可跳工作台/整定/评估 -->
                    <ClpmLoopLink
                      :loop-id="record.loopId"
                      :tag-name="record.tagName"
                      default-target="diagnosis"
                    />
                  </template>
                  <template v-else-if="column.key === 'rating'">
                    <Tooltip
                      v-if="record.isFitnessNA"
                      :title="record.fitnessNATipText"
                      placement="top"
                    >
                      <Tag
                        :color="record.ratingColor || 'default'"
                        class="mr-0"
                      >
                        {{ record.ratingText }}
                      </Tag>
                    </Tooltip>
                    <Tag
                      v-else-if="record.ratingColor"
                      :color="record.ratingColor"
                      class="mr-0"
                    >
                      {{ record.ratingText }}
                    </Tag>
                    <span v-else class="text-neutral-400">—</span>
                  </template>
                  <template v-else-if="column.key === 'score'">
                    <Tooltip
                      v-if="record.isFitnessNA"
                      :title="record.fitnessNATipText"
                      placement="top"
                    >
                      <span :style="{ color: record.scoreColor }">{{
                        record.score
                      }}</span>
                    </Tooltip>
                    <span v-else :style="{ color: record.scoreColor }">{{
                      record.score
                    }}</span>
                  </template>
                </template>
                <template #emptyText>
                  <ClpmEmptyState
                    scene="data"
                    description="当前时间窗内暂无参评回路快照；可先发起性能评估。"
                  />
                </template>
              </Table>
            </div>

            <!-- 整改 F4：阀门运行区间异常卡（2026-10-03：服务端聚合 TOP N） -->
            <div class="clpm-pid-dashboard__valve-card">
              <div class="clpm-pid-dashboard__card-header">
                <span>阀门运行区间异常</span>
                <span class="clpm-pid-dashboard__card-meta">
                  {{ valveAlertsTotal }} 回路越限
                </span>
              </div>
              <div
                v-if="valveAlerts.length === 0"
                class="py-4 text-center text-xs text-gray-400"
              >
                无越限回路（OP 行程超出 5%~95% 为越限）
              </div>
              <div
                v-for="item in valveAlerts"
                :key="item.loopId"
                class="clpm-pid-dashboard__valve-row"
              >
                <ClpmLoopLink
                  :loop-id="item.loopId"
                  :tag-name="item.tagName"
                  default-target="tuning"
                />
                <span
                  class="text-xs"
                  :style="{ color: 'var(--status-warning)' }"
                  >{{ item.range }}</span
                >
              </div>
              <div
                v-if="valveAlertsTotal > valveAlerts.length"
                class="clpm-pid-dashboard__valve-more"
              >
                共 {{ valveAlertsTotal }} 回路越限，仅列严重度前
                {{ valveAlerts.length }}
              </div>
            </div>
          </div>
        </div>
      </div>

    <!-- 整改 A-13：工厂导航抽屉 -->
    <Drawer
      v-model:open="treeDrawerOpen"
      title="工厂导航"
      placement="left"
      :width="300"
    >
      <PlantNodeTree card-title="" :width="260" @select="onTreeSelect" />
    </Drawer>
  </div>
</template>

<style lang="scss" scoped>
/*
 * 配色统一走 vben 设计令牌 CSS 变量（--background/--card/--foreground/
 * --muted-foreground/--border/--primary/--muted），明暗主题自动响应，
 * 不再需要 .dark 覆写块。
 *
 * 2026-10-03 改版（R6）：1920×1080 固定视口——整页零滚动，仅卡片内滚动
 * （方法对齐回路工作台 workbench360：calc(100vh - 110px) + overflow:hidden
 * + flex 分区 min-height:0）。底部三卡 flex:1，表格走 :scroll.y 内滚。
 */
.clpm-pid-dashboard {
  display: flex;
  flex-direction: column;
  height: calc(100vh - 110px);
  overflow: hidden;
  color: hsl(var(--foreground));
  background: linear-gradient(
    180deg,
    hsl(var(--background)) 0%,
    hsl(var(--background-deep)) 100%
  );
}

.clpm-pid-dashboard__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 56px;
  padding: 10px 24px;
  background: linear-gradient(
    90deg,
    hsl(var(--card)) 0%,
    hsl(var(--primary) / 8%) 50%,
    hsl(var(--card)) 100%
  );
  border-bottom: 1px solid hsl(var(--border));
}

.clpm-pid-dashboard__header-left {
  display: flex;
  align-items: center;
}

.clpm-pid-dashboard__header-right {
  display: flex;
  align-items: center;
}

.clpm-pid-dashboard__title {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  color: hsl(var(--foreground));
}

.clpm-pid-dashboard__body {
  display: flex;
  flex: 1;
  gap: 12px;
  min-height: 0;
  padding: 12px;
}

.clpm-pid-dashboard__main {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 8px;
  min-height: 0;
  overflow: hidden;
}

.clpm-pid-dashboard__top-row {
  display: flex;
  flex: none;
  gap: 12px;

  & > * {
    flex: 1;
  }
}

.clpm-pid-dashboard__gauge-card {
  display: flex;
  flex-direction: column;
  gap: 2px;
  align-items: center;
  padding: 8px;
  background: hsl(var(--card) / 80%);
  border: 1px solid hsl(var(--border));
  border-radius: 8px;

  &-title {
    font-size: 12px;
    color: hsl(var(--muted-foreground));
  }

  &-value {
    font-size: 16px;
    font-weight: 600;
    color: hsl(var(--foreground));
  }
}

/* M3 联动：仪表盘卡/装置表列头的“单指标分析”深链入口（低噪小字，hover 主色） */
.clpm-pid-dashboard__gauge-analysis {
  font-size: 11px;
  line-height: 1.4;
  color: hsl(var(--muted-foreground));
  cursor: pointer;

  &:hover {
    color: hsl(var(--primary));
  }
}

.clpm-pid-dashboard__gauge-meta {
  font-size: 10px;
  line-height: 1.2;
  color: hsl(var(--muted-foreground));

  &--stale {
    color: hsl(var(--muted-foreground) / 60%);
  }
}

.clpm-pid-dashboard__card-meta {
  font-size: 11px;
  font-weight: 400;
  color: hsl(var(--muted-foreground));

  &--stale {
    color: hsl(var(--muted-foreground) / 60%);
  }
}

.clpm-pid-dashboard__middle-row {
  display: flex;
  flex: none;
  gap: 8px;
  /* 固定行高：趋势图 240 + 卡头/内边距（1080 预算） */
  height: 302px;
}

.clpm-pid-dashboard__chart-card {
  display: flex;
  flex-direction: column;
  padding: 8px 12px;
  overflow: hidden;
  background: hsl(var(--card) / 80%);
  border: 1px solid hsl(var(--border));
  border-radius: 8px;

  &--status-pie {
    width: 20%;
  }

  &--trend {
    width: 60%;
  }

  &--pie {
    width: 20%;

    &:deep(.ant-tabs) {
      margin-bottom: 2px;

      .ant-tabs-nav {
        margin-bottom: 0;

        &::before {
          display: none;
        }
      }

      .ant-tabs-tab {
        padding: 4px 0;
        margin-right: 14px;
        font-size: 13px;
      }
    }
  }
}

.clpm-pid-dashboard__card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
  font-size: 14px;
  font-weight: 500;
  color: hsl(var(--foreground));
}

.clpm-pid-dashboard__sort-btn {
  padding: 2px 6px;
  color: hsl(var(--muted-foreground));
  cursor: pointer;

  &:hover {
    color: hsl(var(--primary));
  }
}

.clpm-pid-dashboard__bottom-row {
  display: flex;
  flex: 1;
  gap: 8px;
  min-height: 0;
}

.clpm-pid-dashboard__table-card {
  display: flex;
  flex-direction: column;
  width: 40%;
  padding: 8px 12px;
  overflow: hidden;
  background: hsl(var(--card) / 80%);
  border: 1px solid hsl(var(--border));
  border-radius: 8px;
}

/* D1/D4：诊断聚合卡 + 整改有效率卡行（两列并列，窄屏堆叠） */
.clpm-pid-dashboard__diag-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
  margin-top: 8px;

  @media (width <= 1200px) {
    grid-template-columns: 1fr;
  }
}

.clpm-pid-dashboard__top5-card {
  display: flex;
  flex-direction: column;
  width: 32%;
  padding: 8px 12px;
  overflow: hidden;
  background: hsl(var(--card) / 80%);
  border: 1px solid hsl(var(--border));
  border-radius: 8px;
}

.clpm-pid-dashboard__top5-card :deep(.ant-table-tbody > tr > td) {
  white-space: nowrap;
}

/* 整改 A-13：分布行列表（donut/饼图替代） */
.clpm-pid-dashboard__dist-row {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 3px 0;
  font-size: 12px;
}

.clpm-pid-dashboard__dist-label {
  flex-shrink: 0;
  width: 56px;
  color: hsl(var(--muted-foreground));
}

.clpm-pid-dashboard__dist-track {
  flex: 1;
  height: 8px;
  overflow: hidden;
  background: var(--color-slate-100);
  border-radius: 2px;
}

.clpm-pid-dashboard__dist-track i {
  display: block;
  height: 100%;
  border-radius: 2px;
}

.clpm-pid-dashboard__dist-count {
  flex-shrink: 0;
  width: 32px;
  font-family: var(--font-mono);
  font-variant-numeric: tabular-nums;
  text-align: right;
}

/* 整改 F4：阀门运行区间异常卡（2026-10-03：TOP N + 溢出内滚） */
.clpm-pid-dashboard__valve-card {
  display: flex;
  flex-direction: column;
  width: 28%;
  padding: 8px 12px;
  overflow-y: auto;
  background: hsl(var(--card) / 80%);
  border: 1px solid hsl(var(--border));
  border-radius: 8px;
}

.clpm-pid-dashboard__valve-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 0;
  border-bottom: 1px dashed hsl(var(--border));
}

.clpm-pid-dashboard__valve-row:last-child {
  border-bottom: none;
}

.clpm-pid-dashboard__valve-more {
  padding-top: 6px;
  font-size: 11px;
  color: hsl(var(--muted-foreground));
  text-align: center;
}

/* 适用性 Tab 底注 */
.clpm-pid-dashboard__dist-note {
  padding-top: 6px;
  font-size: 10px;
  line-height: 1.3;
  color: hsl(var(--muted-foreground) / 70%);
}

/* 三性分离（R5）：维度堆叠条块 */
.clpm-pid-dashboard__dim-block {
  padding: 4px 0 6px;
}

.clpm-pid-dashboard__dim-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 3px;
  font-size: 12px;
}

.clpm-pid-dashboard__dim-label {
  font-weight: 500;
  color: hsl(var(--foreground));
}

.clpm-pid-dashboard__dim-summary {
  font-size: 10px;
  color: hsl(var(--muted-foreground));
  font-variant-numeric: tabular-nums;
}

.clpm-pid-dashboard__dim-stack {
  display: flex;
  gap: 2px;
  height: 12px;
  overflow: hidden;
  border-radius: 2px;

  i {
    display: block;
    height: 100%;
    min-width: 0;
    transition: width 0.3s;
  }
}

/* 评级标签底色为行内 style（等级色 + 10% 透明背景，色值随阈值配置），
   此处仅保留布局属性 */
.clpm-pid-dashboard__rating-tag {
  padding: 2px 8px;
  font-size: 12px;
  border-radius: 4px;
}

:deep(.ant-table) {
  background: transparent;

  .ant-table-header {
    background: hsl(var(--muted) / 50%);
  }

  .ant-table-body {
    background: transparent;
  }

  .ant-table-cell {
    padding: 6px 8px;
    font-size: 12px;
    line-height: 1.4;
    color: hsl(var(--foreground));
    border-bottom: 1px solid hsl(var(--border));
  }

  .ant-table-thead > tr > th {
    padding: 8px;
    font-size: 12px;
    font-weight: 500;
    color: hsl(var(--muted-foreground));
    background: hsl(var(--muted) / 50%);
    border-bottom: 1px solid hsl(var(--border));
  }

  .ant-table-tbody > tr:hover > td {
    background: hsl(var(--primary) / 5%);
  }

  .ant-table-tbody > tr {
    height: 32px;
  }
}

:deep(.ant-select-selector) {
  color: hsl(var(--foreground)) !important;
  background: hsl(var(--muted) / 50%) !important;
  border: 1px solid hsl(var(--border)) !important;
}

:deep(.ant-btn) {
  color: hsl(var(--primary));
  background: hsl(var(--primary) / 10%);
  border: 1px solid hsl(var(--primary));
}
</style>
