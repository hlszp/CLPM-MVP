<script lang="ts" setup>
import type { Dayjs } from 'dayjs';

/**
 * 装置工作台 — 04-系统概览标杆页 v4.4（线框图对齐版）
 *
 * 布局（按线框图比例）：
 *   行1 标题行（页面标题 + 页面级时间窗总开关）
 *   行2 全厂卡片区（88px ≈ 3行字符：综合评分 + 七紧凑仪表盘 + 回路数 + 等级分布）
 *   中排四列（h=340px）：§2 全厂雷达(20%) / §3 装置-单元排名(40%) / §4 重点回路(20%) / §6 运行状态(20%)
 *   趋势排两列（flex-1）：§5 绩效趋势(55%) / §7 装置指标对比(45%)
 *   1920×1080 一屏无滚动，组件内部滚动。
 *
 * 核心交互：排名即导航——点击装置/单元/柱组选中，驱动 §4 重点回路 + §5 趋势联动；
 * 排名区始终展示全厂层级数据；行1 时间窗为整页联动总开关（行2–§5、§7 统一口径，
 * §6 运行状态为实时口径不随时间窗变化）。
 *
 * 设计规范：docs/设计文档/页面标杆设计/04-系统概览/04-装置工作台密度增强设计-2026-08-15.md
 */
import type { DashboardApi, MetricApi } from '#/api';
import type { GradeDistributionResult, TimeWindowParam } from '#/api/metric';
import type { PlantNodeApi } from '#/api/plant-node';

import { computed, onMounted, ref, watch } from 'vue';
import { useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';

import { RangePicker, Spin } from 'ant-design-vue';
import dayjs from 'dayjs';
import utcPlugin from 'dayjs/plugin/utc';

import {
  getAutoRateRtApi,
  getBoardAggregateApi,
  getBoardTrendApi,
  getGradingThresholdsApi,
} from '#/api';
import {
  getGradeDistributionApi,
  getLoopSnapshotsApi,
  getNodeRankingApi,
  getNodeTrendApi,
  getRankingApi,
} from '#/api/metric';
import { getPlantNodeTreeApi } from '#/api/plant-node';
import { ClpmBulletChart } from '#/components/clpm';

// dayjs utc 插件：自定义时间窗 → UTC naive ISO（后端统一 UTC 存储口径）
dayjs.extend(utcPlugin);

// ================ 常量 ================
/** 页面级时间窗类型（近 N 小时滚动窗口 + 自定义起止） */
type WorkbenchWindow =
  | 'custom'
  | 'last_8_hours'
  | 'last_24_hours'
  | 'last_72_hours'
  | 'last_168_hours';

/** 页面级时间窗总开关（§1 卡片 / §2–§4 排名与回路 / §5 趋势统一口径） */
const pageTimeWindow = ref<WorkbenchWindow>('last_24_hours');

const TIME_WINDOW_OPTIONS: {
  label: string;
  short: string;
  value: WorkbenchWindow;
}[] = [
  { label: '近 8 小时', short: '近 8h', value: 'last_8_hours' },
  { label: '近 24 小时', short: '近 24h', value: 'last_24_hours' },
  { label: '近 72 小时', short: '近 72h', value: 'last_72_hours' },
  { label: '近 168 小时', short: '近 168h', value: 'last_168_hours' },
  { label: '自定义', short: '自定义', value: 'custom' },
];

/** 各滚动窗口的小时数（自定义窗口按 customRange 计算） */
const WINDOW_HOURS: Record<Exclude<WorkbenchWindow, 'custom'>, number> = {
  last_8_hours: 8,
  last_24_hours: 24,
  last_72_hours: 72,
  last_168_hours: 168,
};

/** 自定义窗口（本地时间，分钟/秒归零 → 小时颗粒度） */
const customRange = ref<[Dayjs, Dayjs] | null>(null);
/** 自定义起止时间选择面板开关 */
const showCustomPicker = ref(false);

/** 等级五档默认配置（/configs/grading-thresholds 加载失败时兜底） */
const DEFAULT_GRADES = [
  { label: '优秀', color: '#1a7f4b', min: 95 },
  { label: '良好', color: '#2563eb', min: 85 },
  { label: '合格', color: '#b45309', min: 70 },
  { label: '警告', color: '#c23434', min: 60 },
  { label: '不合格', color: '#a12222', min: 0 },
];

const LINE_COLORS = {
  acc: '#1a7f4b',
  auto: '#0284c7',
  fast: '#7c3aed',
  score: '#1d4ed8',
  steady: '#2563eb',
} as const;

function fmt(v: null | number | undefined, digits = 1): string {
  return v === null || v === undefined ? '--' : v.toFixed(digits);
}

// ================ 定级阈值（配置化，禁硬编码） ================
const gradeCfgs = ref([...DEFAULT_GRADES]);

async function loadGradeCfgs() {
  try {
    const res = await getGradingThresholdsApi();
    const items = (res.thresholds ?? [])
      .filter((t) => Number.isFinite(t.minScore))
      .toSorted((a, b) => b.minScore - a.minScore)
      .map((t) => ({
        label: t.label || t.name,
        color:
          t.color ||
          DEFAULT_GRADES.find((g) => g.label === (t.label || t.name))?.color ||
          '#94a3b8',
        min: t.minScore,
      }));
    if (items.length > 0) gradeCfgs.value = items;
  } catch {
    /* 配置加载失败回落默认五档 */
  }
}

function getGrade(score: null | number | undefined): {
  color: string;
  label: string;
  letter: string;
} {
  if (score === null || score === undefined) {
    return { label: '—', color: '#94a3b8', letter: '—' };
  }
  for (let i = 0; i < gradeCfgs.value.length; i++) {
    const g = gradeCfgs.value[i]!;
    if (score >= g.min) {
      return { ...g, letter: String.fromCodePoint(65 + i) }; // A=0,B=1...
    }
  }
  const last = gradeCfgs.value[gradeCfgs.value.length - 1]!;
  return {
    ...last,
    letter: String.fromCodePoint(65 + gradeCfgs.value.length - 1),
  };
}

/** 告警线阈值：警告等级（倒数第二档）的 minScore；默认60 */
const warningThreshold = computed(() => {
  // 倒数第二档即"警告"等级；若配置不足两档则回落60
  const warn = gradeCfgs.value[gradeCfgs.value.length - 2];
  return warn?.min ?? 60;
});

/** 告警线颜色：取警告等级配置色，默认 #c23434 */
const warningColor = computed(() => {
  const warn = gradeCfgs.value[gradeCfgs.value.length - 2];
  return warn?.color ?? '#c23434';
});

// ================ 状态 ================
const router = useRouter();

/** 选中节点（排名即导航；null = 全厂） */
const selected = ref<null | {
  id: string;
  name: string;
  type: 'AREA' | 'UNIT';
}>(null);

/** §4 重点回路排序：asc = 评分最低 10 / desc = 评分最高 10 */
const topMode = ref<'asc' | 'desc'>('asc');

// §1 KPI 卡片数据（随 pageTimeWindow 刷新；实时自控率为实时值与时间窗无关）
const agg = ref<DashboardApi.BoardAggregateResult | null>(null);
const autoRate = ref<DashboardApi.AutoRateRt | null>(null);
const gradeDist = ref<GradeDistributionResult | null>(null);
/** 上一窗口聚合（环比基线；加载失败为 null → 不显示环比） */
const prevAgg = ref<null | typeof agg.value>(null);

// §6 运行状态：阀门 OP 行程越限回路（实时快照，与时间窗无关）
interface ValveAlertItem {
  loopId: string;
  tagName: string;
  range: string;
}
const valveAlerts = ref<ValveAlertItem[]>([]);

// §2/§3 排名数据（始终全厂层级）
const areaRanking = ref<MetricApi.NodeRankingItem[]>([]);
const unitRanking = ref<MetricApi.NodeRankingItem[]>([]);

// §4 重点回路（随 selected + topMode）
const topLoops = ref<MetricApi.RankingItem[]>([]);

// §5 趋势（随 selected + pageTimeWindow；evaluated 仅全厂口径有值）
interface TrendLines {
  timestamps: string[];
  score: (null | number)[];
  steady: (null | number)[];
  fast: (null | number)[];
  acc: (null | number)[];
  auto: (null | number)[];
  /** 参评回路数柱（右轴）；节点口径无此数据为 null */
  evaluated: null | number[];
}
const trend = ref<null | TrendLines>(null);
const lineVisible = ref({ acc: true, auto: true, fast: true, steady: true });
/** §5 图例扩展：评分主线可切换 */
const scoreVisible = ref(true);

const pageLoading = ref(false);

// ================ 时间窗参数（统一口径） ================
/** 自定义范围 → UTC naive ISO（与后端存储口径一致） */
function toUtcIso(d: Dayjs): string {
  return d.utc().format('YYYY-MM-DDTHH:00:00');
}

/** 统一时间窗请求参数（custom 且已选范围时附起止时间；custom 无范围时回退近 24 小时） */
const windowParams = computed<{
  endTime?: string;
  startTime?: string;
  timeWindow: TimeWindowParam;
}>(() => {
  if (pageTimeWindow.value === 'custom') {
    if (customRange.value) {
      return {
        timeWindow: 'custom',
        startTime: toUtcIso(customRange.value[0]),
        endTime: toUtcIso(customRange.value[1]),
      };
    }
    return { timeWindow: 'last_24_hours' };
  }
  return { timeWindow: pageTimeWindow.value };
});

/** 当前窗口小时数（自定义按已选范围计算；无范围回退 24） */
const trendHours = computed(() => {
  if (pageTimeWindow.value === 'custom') {
    if (customRange.value) {
      return Math.max(
        1,
        customRange.value[1].diff(customRange.value[0], 'hour', true),
      );
    }
    return 24;
  }
  return WINDOW_HOURS[pageTimeWindow.value];
});

/** 上一窗口请求参数（环比基线）：当前窗口向前平移一个窗口长度；custom 未选范围为 null */
const prevWindowParams = computed<null | {
  endTime: string;
  startTime: string;
  timeWindow: 'custom';
}>(() => {
  let startMs: number;
  let endMs: number;
  if (pageTimeWindow.value === 'custom') {
    if (!customRange.value) return null;
    startMs = customRange.value[0].valueOf();
    endMs = customRange.value[1].valueOf();
  } else {
    endMs = Date.now();
    startMs = endMs - WINDOW_HOURS[pageTimeWindow.value] * 3_600_000;
  }
  const lenMs = endMs - startMs;
  return {
    timeWindow: 'custom',
    startTime: toUtcIso(dayjs(startMs - lenMs)),
    endTime: toUtcIso(dayjs(startMs)),
  };
});

/** 标题旁实际统计时间范围（本地时间显示） */
const rangeLabel = computed(() => {
  if (pageTimeWindow.value === 'custom') {
    if (!customRange.value) return '请选择起止时间';
    return `${customRange.value[0].format('MM-DD HH:00')} ~ ${customRange.value[1].format('MM-DD HH:00')}`;
  }
  const now = new Date();
  const start = new Date(now.getTime() - trendHours.value * 3_600_000);
  const fmt = (d: Date) =>
    `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:00`;
  return `${fmt(start)} ~ ${fmt(now)}`;
});

/** 自定义范围选择完成（小时颗粒度：分钟/秒归零；兼容字符串值） */
function onCustomRangeChange(vals: [Dayjs, Dayjs] | [string, string]) {
  const toDay = (v: Dayjs | string) => (typeof v === 'string' ? dayjs(v) : v);
  const s = toDay(vals[0]);
  const e = toDay(vals[1]);
  if (s.isValid() && e.isValid()) {
    customRange.value = [
      s.minute(0).second(0).millisecond(0),
      e.minute(0).second(0).millisecond(0),
    ];
    showCustomPicker.value = false;
  }
}

// ================ 数据加载 ================
async function loadCards() {
  const [a, r, g, p] = await Promise.allSettled([
    getBoardAggregateApi({ ...windowParams.value }),
    getAutoRateRtApi(),
    getGradeDistributionApi({}),
    prevWindowParams.value
      ? getBoardAggregateApi({ ...prevWindowParams.value })
      : Promise.resolve(null),
  ]);
  if (a.status === 'fulfilled') agg.value = a.value;
  if (r.status === 'fulfilled') autoRate.value = r.value;
  if (g.status === 'fulfilled') gradeDist.value = g.value;
  prevAgg.value = p.status === 'fulfilled' && p.value ? p.value : null;
}

async function loadRankings() {
  const [a, u] = await Promise.allSettled([
    getNodeRankingApi({
      nodeType: 'AREA',
      ...windowParams.value,
      sortBy: 'score',
      sortOrder: 'desc',
      limit: 50,
    }),
    getNodeRankingApi({
      nodeType: 'UNIT',
      ...windowParams.value,
      sortBy: 'score',
      sortOrder: 'desc',
      limit: 200,
    }),
  ]);
  areaRanking.value = a.status === 'fulfilled' ? a.value : [];
  unitRanking.value = u.status === 'fulfilled' ? u.value : [];
  // 首次加载默认展开全部装置行（含"未挂载装置"兜底组；此后保留用户折叠状态）
  if (expandedAreas.value.size === 0) {
    expandedAreas.value = new Set([
      ...areaRanking.value.map((x) => x.plantNodeId),
      '__ungrouped__',
    ]);
  }
}

async function loadTopLoops() {
  try {
    topLoops.value = await getRankingApi({
      plantNodeId: selected.value?.id,
      ...windowParams.value,
      limit: 10,
      sortBy: 'score',
      sortOrder: topMode.value,
    });
  } catch {
    topLoops.value = [];
  }
}

/** 当前时间窗 → 节点趋势接口所需的 UTC 起止区间 */
function twRange(): { endTime: string; startTime: string } {
  if (pageTimeWindow.value === 'custom' && customRange.value) {
    return {
      startTime: toUtcIso(customRange.value[0]),
      endTime: toUtcIso(customRange.value[1]),
    };
  }
  const now = Date.now();
  const startTime = now - trendHours.value * 3_600_000;
  return {
    startTime: new Date(startTime).toISOString().slice(0, 19),
    endTime: new Date(now).toISOString().slice(0, 19),
  };
}

async function loadTrend() {
  try {
    if (selected.value) {
      const range = twRange();
      const res = await getNodeTrendApi(selected.value.id, range);
      const m: Record<string, (null | number)[]> = {};
      for (const s of res.series) m[s.metricKey] = s.values;
      trend.value = {
        timestamps: res.timestamps,
        score: m.score ?? [],
        steady: m.steady_rate ?? [],
        fast: m.fast_rate ?? [],
        acc: m.accuracy_rate ?? [],
        auto: m.auto_mode_rate ?? [],
        evaluated: null,
      };
    } else {
      const res = await getBoardTrendApi({ ...windowParams.value });
      trend.value = {
        timestamps: res.timestamps,
        score: res.avgScore,
        steady: res.stabilityRate,
        fast: res.fastRate ?? [],
        acc: res.accuracyRate ?? [],
        auto: res.autoModeRate ?? [],
        evaluated: res.evaluatedLoops ?? null,
      };
    }
  } catch {
    trend.value = null;
  }
}

// §6 运行状态：阀门 OP 行程越限（OP min ≤5% 或 max ≥95%，实时快照最新一条）
async function loadValveAlerts() {
  try {
    const res = await getLoopSnapshotsApi({
      page: 1,
      pageSize: 50,
      latestOnly: true,
    });
    valveAlerts.value = (res.items ?? [])
      .filter((it) => {
        const lo = it.valveOpMin;
        const hi = it.valveOpMax;
        return (
          (lo !== null && lo <= 5) ||
          (hi !== null && hi !== undefined && hi >= 95)
        );
      })
      .map((it) => ({
        loopId: it.loopId ?? '',
        tagName: it.loopTagName ?? '—',
        range: `${fmt(it.valveOpMin, 0)}~${fmt(it.valveOpMax, 0)}%`,
      }));
  } catch {
    valveAlerts.value = [];
  }
}

// ================ 选中联动 ================
function toggleArea(item: MetricApi.NodeRankingItem) {
  selected.value =
    selected.value?.id === item.plantNodeId
      ? null
      : { id: item.plantNodeId, name: item.plantNodeName ?? '—', type: 'AREA' };
}

function toggleUnit(item: MetricApi.NodeRankingItem) {
  selected.value =
    selected.value?.id === item.plantNodeId
      ? null
      : { id: item.plantNodeId, name: item.plantNodeName ?? '—', type: 'UNIT' };
}

function clearSelection() {
  selected.value = null;
}

function goToLoop(loopId: string) {
  router.push({
    path: '/monitor/loop-workbench',
    query: {
      from: 'overview',
      loopId,
      ...(selected.value?.id ? { plantNodeId: selected.value.id } : {}),
    },
  });
}

/** 跳转到关注队列（可选传 plantNodeId 筛选上下文） */
function goToAttention(plantNodeId?: string) {
  router.push({
    path: '/monitor/attention',
    query: {
      from: 'overview',
      ...(plantNodeId ? { plantNodeId } : {}),
    },
  });
}

// ================ watch / 生命周期 ================
watch(selected, () => {
  loadTopLoops();
  loadTrend();
});
watch(topMode, () => loadTopLoops());
/** 页面级时间窗总开关：切换到自定义时弹出起止选择面板（选完才刷新）；其余直接刷新 */
watch(pageTimeWindow, (v) => {
  if (v === 'custom') {
    showCustomPicker.value = true;
    if (customRange.value) {
      loadCards();
      loadRankings();
      loadTopLoops();
      loadTrend();
    }
    return;
  }
  showCustomPicker.value = false;
  loadCards();
  loadRankings();
  loadTopLoops();
  loadTrend();
});
/** 自定义范围选定后刷新（小时颗粒度归零即触发） */
watch(customRange, () => {
  if (pageTimeWindow.value === 'custom' && customRange.value) {
    loadCards();
    loadRankings();
    loadTopLoops();
    loadTrend();
  }
});

onMounted(async () => {
  pageLoading.value = true;
  loadGradeCfgs();
  await Promise.allSettled([
    loadCards(),
    loadRankings(),
    loadNodeTree(),
    loadTopLoops(),
    loadTrend(),
    loadValveAlerts(),
  ]);
  pageLoading.value = false;
});

// ================ 计算属性 ================
const scopeLabel = computed(() => selected.value?.name ?? '全厂');

const twLabel = computed(
  () =>
    TIME_WINDOW_OPTIONS.find((o) => o.value === pageTimeWindow.value)?.short ??
    '近 24h',
);

// §1 KPI 卡片
const r1 = computed(() => {
  const a = agg.value?.aggregate;
  return {
    accuracyRate: a?.accuracyRate ?? null,
    autoCount: autoRate.value?.autoCount ?? 0,
    autoModeRate: a?.autoModeRate ?? null,
    avgScore: a?.avgScore ?? null,
    effectiveAutoRate: a?.effectiveAutoRate ?? null,
    evaluatedLoops: a?.evaluatedLoops ?? 0,
    fastRate: a?.fastRate ?? null,
    goodValueRate: a?.goodValueRate ?? null,
    manualCount: autoRate.value?.manualCount ?? 0,
    rtAutoRate: autoRate.value?.rate ?? null,
    stabilityRate: a?.stabilityRate ?? null,
    totalLoops: a?.totalLoops ?? 0,
  };
});

/** 时间窗指标环比差值（当前窗口 − 上一窗口；缺基线为 null 不显示） */
const r1Delta = computed(() => {
  const a = agg.value?.aggregate;
  const p = prevAgg.value?.aggregate;
  if (!a || !p) {
    return {
      accuracyRate: null,
      autoModeRate: null,
      avgScore: null,
      effectiveAutoRate: null,
      fastRate: null,
      goodValueRate: null,
      stabilityRate: null,
    };
  }
  const diff = (
    cur: null | number | undefined,
    prev: null | number | undefined,
  ): null | number =>
    typeof cur === 'number' && typeof prev === 'number' ? cur - prev : null;
  return {
    accuracyRate: diff(a.accuracyRate, p.accuracyRate),
    autoModeRate: diff(a.autoModeRate, p.autoModeRate),
    avgScore: diff(a.avgScore, p.avgScore),
    effectiveAutoRate: diff(a.effectiveAutoRate, p.effectiveAutoRate),
    fastRate: diff(a.fastRate, p.fastRate),
    goodValueRate: diff(a.goodValueRate, p.goodValueRate),
    stabilityRate: diff(a.stabilityRate, p.stabilityRate),
  };
});

// ================ 行2 仪表盘带（ClpmBulletChart × 7，性能总览同款语义） ================
/** 实时数据过期阈值（分钟），超过则仪表/角标标灰警示 */
const RT_STALE_MINUTES =
  Number(import.meta.env.VITE_RT_STALE_MINUTES ?? 10) || 10;

const rtStale = computed(() => {
  const readAt = autoRate.value?.readAt;
  if (!readAt) return true;
  return Date.now() - new Date(readAt).getTime() > RT_STALE_MINUTES * 60_000;
});

/** 实时自控率仪表 meta 角标（中断时提示） */
const rtMeta = computed(() => {
  const readAt = autoRate.value?.readAt;
  if (!readAt) return '实时数据中断';
  return `实时 · ${new Date(readAt).toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })}`;
});

// ================ §6 运行状态：MODE 分布行（1自动/2串级/3远程/4先控/0手动） ================
const modeRows = computed(() => {
  const rt = autoRate.value;
  const total = rt?.totalCount ?? 0;
  const counts = rt?.modeCounts ?? {};
  const order: { key: string; label: string }[] = [
    { key: '1', label: '自动' },
    { key: '2', label: '串级' },
    { key: '3', label: '远程' },
    { key: '4', label: '先控' },
    { key: '0', label: '手动' },
  ];
  return order.map((o) => {
    const count = counts[o.key] ?? 0;
    return {
      label: o.label,
      count,
      pct: total > 0 ? Math.round((count / total) * 100) : 0,
      /** 手动模式 >0 红色强调（运行监控核心语义） */
      emphasis: o.key === '0' && count > 0,
    };
  });
});

// ================ §1 评分等级分布 Pie ================
const pieSegments = computed(() => {
  const d = gradeDist.value;
  if (!d || !d.total) return [];
  const defs: {
    key: Exclude<keyof GradeDistributionResult, 'total'>;
    label: string;
  }[] = [
    { key: 'EXCELLENT', label: '优秀' },
    { key: 'GOOD', label: '良好' },
    { key: 'FAIR', label: '合格' },
    { key: 'WARNING', label: '警告' },
    { key: 'POOR', label: '不合格' },
    { key: 'INCONCLUSIVE', label: '待评估' },
  ];
  const segs: { color: string; count: number; label: string; pct: number }[] =
    [];
  for (const def of defs) {
    const count = d[def.key] ?? 0;
    if (count <= 0) continue;
    const color =
      def.key === 'INCONCLUSIVE'
        ? '#94a3b8'
        : (gradeCfgs.value.find((g) => g.label === def.label)?.color ??
          '#94a3b8');
    segs.push({
      label: def.label,
      color,
      count,
      pct: (count / d.total) * 100,
    });
  }
  return segs;
});

function arcPath(
  cx: number,
  cy: number,
  r: number,
  a0: number,
  a1: number,
): string {
  const x0 = cx + r * Math.cos(a0);
  const y0 = cy + r * Math.sin(a0);
  const x1 = cx + r * Math.cos(a1);
  const y1 = cy + r * Math.sin(a1);
  const large = a1 - a0 > Math.PI ? 1 : 0;
  return `M${cx},${cy} L${x0.toFixed(2)},${y0.toFixed(2)} A${r},${r} 0 ${large} 1 ${x1.toFixed(2)},${y1.toFixed(2)} Z`;
}

const pieSvg = computed(() => {
  const segs = pieSegments.value;
  if (segs.length === 0) return '';
  const cx = 24;
  const cy = 24;
  const r = 21;
  let angle = -Math.PI / 2;
  let paths = '';
  for (const s of segs) {
    const next = angle + (s.pct / 100) * Math.PI * 2;
    paths += `<path d="${arcPath(cx, cy, r, angle, next)}" fill="${s.color}" stroke="#fff" stroke-width="1"><title>${s.label} ${s.count} 个（${s.pct.toFixed(0)}%）</title></path>`;
    angle = next;
  }
  return `<svg width="48" height="48" viewBox="0 0 48 48">${paths}</svg>`;
});

// ================ §3 单元排名多维表格（表头可按维度重排） ================
type UnitSortKey = 'acc' | 'auto' | 'fast' | 'loops' | 'score' | 'steady';
const UNIT_SORT_DEFS: {
  key: UnitSortKey;
  label: string;
  metric: keyof MetricApi.NodeRankingItem | null;
}[] = [
  { key: 'score', label: '评分', metric: 'score' },
  { key: 'steady', label: '平稳率', metric: 'steadyRate' },
  { key: 'fast', label: '快速率', metric: 'fastRate' },
  { key: 'acc', label: '准确率', metric: 'accuracyRate' },
  { key: 'auto', label: '自控率', metric: 'autoModeRate' },
  { key: 'loops', label: '回路数', metric: 'loopCount' },
];

const unitSortKey = ref<UnitSortKey>('score');

/** 树形行指标列文本（动态索引类型收窄：仅有限数字可格式化，其余显示 —） */
function metricText(
  item: MetricApi.NodeRankingItem | undefined,
  metric: keyof MetricApi.NodeRankingItem | null,
  digits = 1,
): string {
  if (!item || !metric) return '—';
  const v = item[metric];
  return typeof v === 'number' && Number.isFinite(v) ? fmt(v, digits) : '—';
}

// ================ §3 装置-单元树形排名（装置行折叠/展开单元行，工厂树 join 层级） ================
interface TreeRow {
  kind: 'area' | 'unit';
  id: string;
  name: string;
  /** 当前排序维度下的序号（装置行 = 装置排名，单元行 = 装置内序号） */
  rank: number;
  item?: MetricApi.NodeRankingItem;
}

/** 单元 → 所属装置 映射（树缺失/未挂载的单元归"未挂载装置"组兜底） */
const unitParentMap = ref<Map<string, string>>(new Map());

async function loadNodeTree() {
  try {
    const tree = await getPlantNodeTreeApi();
    const map = new Map<string, string>();
    const walk = (nodes: PlantNodeApi.PlantNode[], areaId?: string) => {
      for (const n of nodes) {
        if (n.type === 'AREA') {
          walk(n.children ?? [], n.id);
        } else if (n.type === 'UNIT' && areaId) {
          map.set(n.id, areaId);
        } else {
          walk(n.children ?? [], areaId);
        }
      }
    };
    walk(tree, undefined);
    unitParentMap.value = map;
  } catch {
    unitParentMap.value = new Map();
  }
}

/** 展开的装置行（默认全展开，时间窗刷新后保留用户折叠状态） */
const expandedAreas = ref<Set<string>>(new Set());

function toggleAreaExpand(id: string) {
  const s = new Set(expandedAreas.value);
  if (s.has(id)) s.delete(id);
  else s.add(id);
  expandedAreas.value = s;
}

/** 树形行：装置行 + 展开的单元行（装置/单元均按当前表头维度降序） */
const treeRows = computed<TreeRow[]>(() => {
  const num = (v: unknown): number =>
    typeof v === 'number' && Number.isFinite(v) ? v : -1;
  const def = UNIT_SORT_DEFS.find((d) => d.key === unitSortKey.value);
  const by = (a: MetricApi.NodeRankingItem, b: MetricApi.NodeRankingItem) =>
    def?.metric ? num(b[def.metric]) - num(a[def.metric]) : a.rank - b.rank;

  const areas = areaRanking.value.toSorted(by);
  const rows: TreeRow[] = [];
  /** 已归属某装置的单元（与展开状态无关：折叠装置的单元不算"未挂载"） */
  const grouped = new Set<string>();
  for (const [ai, area] of areas.entries()) {
    rows.push({
      kind: 'area',
      id: area.plantNodeId,
      name: area.plantNodeName ?? '—',
      rank: ai + 1,
      item: area,
    });
    const units = unitRanking.value
      .filter(
        (u) => unitParentMap.value.get(u.plantNodeId) === area.plantNodeId,
      )
      .toSorted(by);
    for (const u of units) grouped.add(u.plantNodeId);
    if (!expandedAreas.value.has(area.plantNodeId)) continue;
    rows.push(
      ...units.map((u, ui) => ({
        kind: 'unit' as const,
        id: u.plantNodeId,
        name: u.plantNodeName ?? '—',
        rank: ui + 1,
        item: u,
      })),
    );
  }
  // 兜底：未挂载到任何装置的单元 → "未挂载装置"组
  const orphans = unitRanking.value.filter((u) => !grouped.has(u.plantNodeId));
  if (orphans.length > 0) {
    rows.push({
      kind: 'area',
      id: '__ungrouped__',
      name: '未挂载装置',
      rank: areas.length + 1,
    });
    if (expandedAreas.value.has('__ungrouped__')) {
      const sorted = orphans.toSorted(by);
      rows.push(
        ...sorted.map((u, ui) => ({
          kind: 'unit' as const,
          id: u.plantNodeId,
          name: u.plantNodeName ?? '—',
          rank: ui + 1,
          item: u,
        })),
      );
    }
  }
  return rows;
});

/** 行数 ≤ 10 时，拉伸行高等间距填满列表区；> 10 行时自然高度+滚动 */
const stretchRows = computed(
  () => treeRows.value.length > 0 && treeRows.value.length <= 10,
);

// ================ §5 趋势双轴柱线图（左轴五线 + 右轴参评回路数柱） ================
/** 趋势图几何（viewBox 坐标）：SVG 生成与悬浮十字线映射共用，避免两处漂移 */
const trendGeo = computed(() => {
  const t = trend.value;
  if (!t || t.timestamps.length === 0) return null;
  const all = [...t.score, ...t.steady, ...t.fast, ...t.acc, ...t.auto].filter(
    (v): v is number => v !== null && v !== undefined,
  );
  if (all.length === 0) return null;
  /** §5 卡片 55% 宽（≈1000px 内宽 / 图区 ≈320px 高，3.1:1），viewBox 比例匹配容器避免文字单向拉伸 */
  const W = 960;
  const H = 310;
  const L = 46;
  const R = 16;
  const T = 14;
  const B = 30;
  return {
    n: t.timestamps.length,
    W,
    H,
    L,
    R,
    T,
    B,
    iw: W - L - R,
    ih: H - T - B,
    yMin: Math.min(
      warningThreshold.value - 10,
      Math.floor((Math.min(...(all.length > 0 ? all : [100])) - 6) / 10) * 10,
    ),
    yMax: 100,
  };
});

const trendSvg = computed(() => {
  const t = trend.value;
  const geo = trendGeo.value;
  if (!t || !geo) return '';

  const { n, W, H, L, R, T, iw, ih, yMin, yMax } = geo;
  const x = (i: number) => L + (iw * i) / Math.max(1, n - 1);
  const y = (v: number) =>
    T + ih * (1 - (Math.max(yMin, Math.min(yMax, v)) - yMin) / (yMax - yMin));

  const path = (arr: (null | number)[]) => {
    let d = '';
    let started = false;
    for (let i = 0; i < n; i++) {
      const v = arr[i];
      if (v === null || v === undefined) {
        started = false;
        continue;
      }
      const px = x(i).toFixed(1);
      const py = y(v).toFixed(1);
      d += started ? ` L${px},${py}` : `M${px},${py}`;
      started = true;
    }
    return d;
  };

  // 网格 + 告警线（取自定级阈值配置，禁硬编码）
  const wt = warningThreshold.value;
  const wc = warningColor.value;
  let grid = '';
  for (let v = yMin; v <= yMax; v += 10) {
    grid += `<line x1="${L}" y1="${y(v).toFixed(1)}" x2="${W - R}" y2="${y(v).toFixed(1)}" stroke="#eef2f7"/>`;
    grid += `<text x="${L - 5}" y="${(y(v) + 3).toFixed(1)}" font-size="9" fill="#94a3b8" text-anchor="end">${v}</text>`;
  }
  // 告警线：单独画虚线（不依赖网格刻度）
  const wy = y(Math.max(yMin, Math.min(yMax, wt))).toFixed(1);
  grid += `<line x1="${L}" y1="${wy}" x2="${W - R}" y2="${wy}" stroke="${wc}" stroke-dasharray="5,4" stroke-width="1.2"/>`;
  grid += `<text x="${L + 4}" y="${(Number(wy) - 4).toFixed(1)}" font-size="9" fill="${wc}" text-anchor="start">告警线 ${wt}</text>`;

  // X 轴标签（≥120 小时 → M/D；其余 → HH:00）
  let xl = '';
  const step = Math.ceil(n / 8);
  const byDay = trendHours.value >= 120;
  for (let i = 0; i < n; i += step) {
    const ts = t.timestamps[i]!;
    const d = new Date(ts);
    const lab = byDay
      ? `${d.getMonth() + 1}/${d.getDate()}`
      : `${d.getHours().toString().padStart(2, '0')}:00`;
    const anchor = i === 0 ? 'start' : (i + step >= n ? 'end' : 'middle');
    xl += `<text x="${x(i).toFixed(1)}" y="${H - 8}" font-size="9" fill="#94a3b8" text-anchor="${anchor}">${lab}</text>`;
  }

  // 评分主系列数据点（稀疏）
  let dots = '';
  const interval = n <= 8 ? 1 : Math.ceil(n / 14);
  if (scoreVisible.value) {
    for (let i = 0; i < n; i++) {
      const v = t.score[i];
      if (v === null || v === undefined) continue;
      if (i % interval !== 0 && i !== n - 1) continue;
      dots += `<circle cx="${x(i).toFixed(1)}" cy="${y(v).toFixed(1)}" r="2.4" fill="${LINE_COLORS.score}"/>`;
    }
  }

  const aux = (arr: (null | number)[], color: string) =>
    `<path d="${path(arr)}" fill="none" stroke="${color}" stroke-width="1.2" stroke-dasharray="5,3" opacity=".85"/>`;

  return `<svg viewBox="0 0 ${W} ${H}" width="100%" height="100%" preserveAspectRatio="none" style="display:block">
    ${grid}
    ${lineVisible.value.steady ? aux(t.steady, LINE_COLORS.steady) : ''}
    ${lineVisible.value.fast ? aux(t.fast, LINE_COLORS.fast) : ''}
    ${lineVisible.value.acc ? aux(t.acc, LINE_COLORS.acc) : ''}
    ${lineVisible.value.auto ? aux(t.auto, LINE_COLORS.auto) : ''}
    ${scoreVisible.value ? `<path d="${path(t.score)}" fill="none" stroke="${LINE_COLORS.score}" stroke-width="2.2"/>` : ''}
    ${dots}${xl}
  </svg>`;
});

// ================ §5 悬浮十字线 + 统一悬浮框 ================
/** 悬停桶索引（null = 不显示）；viewBox X 坐标用于十字线/悬浮框定位 */
const trendHoverIdx = ref<null | number>(null);
const trendHoverX = ref(0);

function onTrendMove(e: MouseEvent) {
  const geo = trendGeo.value;
  const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
  if (!geo || rect.width <= 0) return;
  const vx = ((e.clientX - rect.left) / rect.width) * geo.W;
  if (vx < geo.L - 6 || vx > geo.W - geo.R + 6) {
    trendHoverIdx.value = null;
    return;
  }
  const i = Math.round(((vx - geo.L) / geo.iw) * (geo.n - 1));
  if (Number.isNaN(i) || i < 0 || i >= geo.n) {
    trendHoverIdx.value = null;
    return;
  }
  trendHoverIdx.value = i;
  trendHoverX.value = geo.L + (geo.iw * i) / Math.max(1, geo.n - 1);
}

function onTrendLeave() {
  trendHoverIdx.value = null;
}

/** 悬浮框内容：时间 + 当前可见序列在该桶的取值 */
const trendTip = computed(() => {
  const i = trendHoverIdx.value;
  const t = trend.value;
  if (i === null || !t) return null;
  const d = t.timestamps[i] ? new Date(t.timestamps[i]!) : null;
  const time = d
    ? (trendHours.value >= 120 ? `${d.getMonth() + 1}/${d.getDate()} ` : '') +
      `${d.getHours().toString().padStart(2, '0')}:00`
    : '—';
  const rows: { color: string; label: string; text: string }[] = [];
  const line = (label: string, v: null | number | undefined, color: string) =>
    rows.push({
      color,
      label,
      text: typeof v === 'number' ? `${v.toFixed(1)}%` : '—',
    });
  if (scoreVisible.value) line('综合评分', t.score[i], LINE_COLORS.score);
  if (lineVisible.value.steady) line('平稳率', t.steady[i], LINE_COLORS.steady);
  if (lineVisible.value.fast) line('快速率', t.fast[i], LINE_COLORS.fast);
  if (lineVisible.value.acc) line('准确率', t.acc[i], LINE_COLORS.acc);
  if (lineVisible.value.auto) line('自控率', t.auto[i], LINE_COLORS.auto);
  return { rows, time };
});

// ================ §7 装置指标对比（分组柱状图，点击柱组 = 选中装置） ================
const AREA_BAR_COLORS = {
  acc: '#1a7f4b',
  auto: '#0284c7',
  fast: '#7c3aed',
  steady: '#2563eb',
} as const;

const areaBarsSvg = computed(() => {
  const items = areaRanking.value;
  if (items.length === 0) return '';

  // §7 卡片 45% 宽（≈800px 内宽 / 图区 ≈320px 高，2.5:1），viewBox 比例匹配容器避免文字单向拉伸
  const W = 780;
  const H = 310;
  const L = 44;
  const R = 10;
  const T = 16;
  const B = 30;
  const iw = W - L - R;
  const ih = H - T - B;
  const yv = (v: number) => T + ih * (1 - Math.max(0, Math.min(100, v)) / 100);

  let grid = '';
  for (const v of [0, 25, 50, 75, 100]) {
    grid += `<line x1="${L}" y1="${yv(v).toFixed(1)}" x2="${W - R}" y2="${yv(v).toFixed(1)}" stroke="#eef2f7"/>`;
    grid += `<text x="${L - 4}" y="${(yv(v) + 3).toFixed(1)}" font-size="8" fill="#94a3b8" text-anchor="end">${v}</text>`;
  }

  const n = items.length;
  const groupW = iw / n;
  const barCount = 4;
  const gap = groupW * 0.16;
  const barW = (groupW - gap * 2) / barCount;
  const metricDefs: {
    field: keyof MetricApi.NodeRankingItem;
    key: 'acc' | 'auto' | 'fast' | 'steady';
    label: string;
  }[] = [
    { key: 'steady', field: 'steadyRate', label: '平稳' },
    { key: 'fast', field: 'fastRate', label: '快速' },
    { key: 'acc', field: 'accuracyRate', label: '准确' },
    { key: 'auto', field: 'autoModeRate', label: '自控' },
  ];

  let groups = '';
  items.forEach((item, idx) => {
    const gx = L + idx * groupW;
    const isSel = selected.value?.id === item.plantNodeId;
    const name = (item.plantNodeName ?? '—').slice(0, 5);
    const num = (v: unknown): null | number =>
      typeof v === 'number' && Number.isFinite(v) ? v : null;

    let g = `<g data-id="${item.plantNodeId}" style="cursor:pointer">`;
    g += `<rect x="${(gx + 1).toFixed(1)}" y="${T}" width="${(groupW - 2).toFixed(1)}" height="${ih}" fill="transparent"/>`;
    metricDefs.forEach((md, mi) => {
      const v = num(item[md.field]);
      if (v === null) return;
      const bx = gx + gap + mi * barW;
      g += `<rect x="${bx.toFixed(1)}" y="${yv(v).toFixed(1)}" width="${Math.max(2, barW - 1.5).toFixed(1)}" height="${(T + ih - yv(v)).toFixed(1)}" fill="${AREA_BAR_COLORS[md.key]}"><title>${item.plantNodeName ?? ''} · ${md.label}率 ${fmt(v)}%</title></rect>`;
    });
    // 柱组上方评分标注（等级色）
    g += `<text x="${(gx + groupW / 2).toFixed(1)}" y="${T - 4}" font-size="9" font-weight="700" text-anchor="middle" fill="${getGrade(item.score).color}">${fmt(item.score, 0)}</text>`;
    g += `<text x="${(gx + groupW / 2).toFixed(1)}" y="${H - 8}" font-size="8" fill="#64748b" text-anchor="middle">${name}</text>`;
    if (isSel) {
      g += `<rect x="${(gx + 0.5).toFixed(1)}" y="${T}" width="${(groupW - 1).toFixed(1)}" height="${(H - B - T).toFixed(1)}" fill="none" stroke="#2563eb" stroke-width="1.2" stroke-dasharray="4,3"/>`;
    }
    g += '</g>';
    groups += g;
  });

  return `<svg viewBox="0 0 ${W} ${H}" width="100%" height="100%" preserveAspectRatio="none" style="display:block">${grid}${groups}</svg>`;
});

/** §7 事件委托：点击柱组 = 选中/取消该装置（与 §2 卡片同语义） */
function onAreaBarsClick(e: MouseEvent) {
  const target = e.target as null | SVGElement;
  const g = target?.closest?.('g[data-id]') as null | SVGGElement;
  const id = g?.dataset.id;
  if (!id) return;
  const item = areaRanking.value.find((a) => a.plantNodeId === id);
  if (item) toggleArea(item);
}

// ================ §8 全厂雷达（六维平均，三层网格环；迁入行2 右端紧凑版） ================
const radarSvg = computed(() => {
  const dims: { label: string; value: null | number }[] = [
    { label: '评分', value: r1.value.avgScore },
    { label: '平稳率', value: r1.value.stabilityRate },
    { label: '快速率', value: r1.value.fastRate },
    { label: '准确率', value: r1.value.accuracyRate },
    { label: '自控率', value: r1.value.autoModeRate },
    { label: '好值率', value: r1.value.goodValueRate },
  ];
  if (dims.every((d) => d.value === null)) return '';

  const W = 340;
  const H = 300;
  const cx = W / 2;
  const cy = 152;
  const r = 100;
  const n = dims.length;
  const angle = (i: number) => -Math.PI / 2 + (i * 2 * Math.PI) / n;
  const px = (i: number, rad: number) => cx + rad * Math.cos(angle(i));
  const py = (i: number, rad: number) => cy + rad * Math.sin(angle(i));
  const poly = (rad: number) =>
    dims
      .map((_, i) => `${px(i, rad).toFixed(1)},${py(i, rad).toFixed(1)}`)
      .join(' ');

  let svg = '';
  // 三层网格环（33 / 67 / 100）+ 轴线
  for (const frac of [1, 0.67, 0.33]) {
    svg += `<polygon points="${poly(r * frac)}" fill="none" stroke="#e2e8f0" stroke-width="1"/>`;
  }
  for (let i = 0; i < n; i++) {
    svg += `<line x1="${cx}" y1="${cy}" x2="${px(i, r).toFixed(1)}" y2="${py(i, r).toFixed(1)}" stroke="#e2e8f0"/>`;
  }
  // 数据多边形（null 维度按 0 收敛到中心，轴端标 "—"）
  const pts = dims
    .map((d, i) => {
      const rad =
        d.value === null ? 0 : (r * Math.max(0, Math.min(100, d.value))) / 100;
      return `${px(i, rad).toFixed(1)},${py(i, rad).toFixed(1)}`;
    })
    .join(' ');
  svg += `<polygon points="${pts}" fill="rgba(37,99,235,.16)" stroke="#2563eb" stroke-width="1.5"/>`;
  dims.forEach((d, i) => {
    const rad =
      d.value === null ? 0 : (r * Math.max(0, Math.min(100, d.value))) / 100;
    svg += `<circle cx="${px(i, rad).toFixed(1)}" cy="${py(i, rad).toFixed(1)}" r="2" fill="#2563eb"><title>${d.label} ${d.value === null ? '—' : `${fmt(d.value)}%`}</title></circle>`;
  });
  // 轴端标注：指标名 + 数值两行式（按角度对齐）
  dims.forEach((d, i) => {
    const lx = px(i, r + 16);
    const ly = py(i, r + 16);
    const cosA = Math.cos(angle(i));
    const anchor = Math.abs(cosA) < 0.3 ? 'middle' : (cosA > 0 ? 'start' : 'end');
    const sinA = Math.sin(angle(i));
    // 顶部轴整体上移一行、底部轴下移一行，水平轴居中对齐
    const base = sinA < -0.3 ? -14 : (sinA > 0.3 ? -2 : 1);
    svg += `<text x="${lx.toFixed(1)}" y="${(ly + base).toFixed(1)}" font-size="9" fill="#64748b" text-anchor="${anchor}">${d.label}</text>`;
    svg += `<text x="${lx.toFixed(1)}" y="${(ly + base + 11).toFixed(1)}" font-size="10" font-weight="700" fill="#1e293b" text-anchor="${anchor}">${d.value === null ? '—' : fmt(d.value, 0)}</text>`;
  });

  return `<svg viewBox="0 0 ${W} ${H}" width="100%" height="100%" preserveAspectRatio="xMidYMid meet" style="display:block">${svg}</svg>`;
});
</script>

<template>
  <Page auto-content-height>
    <Spin :spinning="pageLoading" class="h-full" wrapper-class-name="h-full">
      <div class="flex h-full flex-col gap-1 overflow-hidden">
        <!-- ══════ 行1 标题行（页面标题深蓝加粗 + 实际时间范围 + 页面级时间窗总开关） ══════ -->
        <div class="flex h-8 flex-none items-center">
          <span class="text-[16px] font-bold tracking-wide text-[#1e40af]"
            >装置工作台</span
          >
          <!-- 实际统计时间范围（标题右侧；custom 已选范围时可点击重新调整） -->
          <span
            class="ml-3 text-[11px] whitespace-nowrap"
            :class="[
              pageTimeWindow === 'custom' && !customRange
                ? 'text-amber-600'
                : 'text-gray-500',
              pageTimeWindow === 'custom' && customRange
                ? 'cursor-pointer underline decoration-dotted underline-offset-2 hover:text-blue-700'
                : '',
            ]"
            :title="
              pageTimeWindow === 'custom' && customRange
                ? '点击调整起止时间'
                : undefined
            "
            @click="
              pageTimeWindow === 'custom' &&
              customRange &&
              (showCustomPicker = !showCustomPicker)
            "
            >{{ rangeLabel }}</span
          >
          <div class="relative ml-auto flex items-center">
            <!-- 自定义起止时间选择面板（小时颗粒度） -->
            <div
              v-if="pageTimeWindow === 'custom' && showCustomPicker"
              class="absolute right-0 top-full z-50 mt-1.5 rounded border border-gray-200 bg-white p-3 shadow-lg"
            >
              <RangePicker
                :allow-clear="false"
                format="YYYY-MM-DD HH:00"
                :show-time="{ format: 'HH' }"
                :value="customRange ?? undefined"
                @change="onCustomRangeChange"
              />
              <div class="mt-1.5 text-[10px] text-gray-400">
                时间颗粒度：小时（选定后自动应用）
              </div>
            </div>
            <!-- 时间窗按钮组（与"评分最高/最低 10"风格一致：选中蓝底白字，未选中白底灰字+左分隔线） -->
            <div
              class="flex overflow-hidden rounded border border-gray-200 text-xs"
            >
              <button
                v-for="(o, idx) in TIME_WINDOW_OPTIONS"
                :key="o.value"
                class="border-0 px-2.5 py-0.5"
                :class="[
                  idx > 0 ? 'border-l border-gray-200' : '',
                  pageTimeWindow === o.value
                    ? 'bg-blue-700 text-white'
                    : 'bg-white text-gray-600',
                ]"
                @click="pageTimeWindow = o.value"
              >
                {{ o.label }}
              </button>
            </div>
          </div>
        </div>

        <!-- ══════ 行2 全厂综合指标行（评分 + 七仪表盘带 + 回路数 + 等级分布，十区块等宽均分） ══════
             高度 88px ≈ 3 行字符（紧凑仪表：标签数值行/轨道行/角标行） -->
        <div
          class="flex flex-none items-stretch rounded border border-gray-200 bg-white"
          style="height: 88px"
        >
          <!-- 综合评分 + 等级（含环比角标） -->
          <div
            class="flex min-w-0 flex-1 items-center justify-center gap-2.5 px-2"
          >
            <div>
              <div class="flex items-center gap-1.5">
                <span class="text-[10px] text-gray-400">全厂综合评分</span>
                <span
                  v-if="r1Delta.avgScore !== null"
                  class="rounded px-1 font-mono text-[10px] font-bold"
                  :class="
                    (r1Delta.avgScore ?? 0) > 0.005
                      ? 'bg-green-50 text-green-700'
                      : (r1Delta.avgScore ?? 0) < -0.005
                        ? 'bg-red-50 text-red-700'
                        : 'bg-gray-100 text-gray-500'
                  "
                  >{{
                    (r1Delta.avgScore ?? 0) > 0.005
                      ? '↑'
                      : (r1Delta.avgScore ?? 0) < -0.005
                        ? '↓'
                        : '→'
                  }}
                  {{ fmt(Math.abs(r1Delta.avgScore ?? 0), 2) }}</span
                >
              </div>
              <div
                class="font-mono text-2xl leading-tight"
                :style="{ color: getGrade(r1.avgScore).color }"
              >
                {{ fmt(r1.avgScore, 2) }}
              </div>
              <span
                class="mt-1 inline-block rounded border px-2 py-0.5 text-[11px] font-bold"
                :style="{
                  color: getGrade(r1.avgScore).color,
                  borderColor: `${getGrade(r1.avgScore).color}33`,
                  background: `${getGrade(r1.avgScore).color}11`,
                }"
              >
                {{ getGrade(r1.avgScore).label }}
              </span>
            </div>
          </div>

          <!-- 七仪表盘带（实时自控率 + 六时间窗指标含环比；区块等宽 + 适度内距） -->
          <div
            class="flex min-w-0 flex-1 items-center border-l border-gray-100 px-2.5"
            :class="rtStale ? 'opacity-55' : ''"
          >
            <ClpmBulletChart
              class="mx-auto w-[94%]"
              compact
              label="实时自控率"
              :value="r1.rtAutoRate"
              :color="getGrade(r1.rtAutoRate).color"
            />
          </div>
          <div
            class="flex min-w-0 flex-1 items-center border-l border-gray-100 px-2.5"
          >
            <ClpmBulletChart
              class="mx-auto w-[94%]"
              compact
              label="有效自控率"
              :value="r1.effectiveAutoRate"
              :delta="r1Delta.effectiveAutoRate"
              :color="getGrade(r1.effectiveAutoRate).color"
            />
          </div>
          <div
            class="flex min-w-0 flex-1 items-center border-l border-gray-100 px-2.5"
          >
            <ClpmBulletChart
              class="mx-auto w-[94%]"
              compact
              label="平稳率"
              :value="r1.stabilityRate"
              :delta="r1Delta.stabilityRate"
              :color="getGrade(r1.stabilityRate).color"
            />
          </div>
          <div
            class="flex min-w-0 flex-1 items-center border-l border-gray-100 px-2.5"
          >
            <ClpmBulletChart
              class="mx-auto w-[94%]"
              compact
              label="快速率"
              :value="r1.fastRate"
              :delta="r1Delta.fastRate"
              :color="getGrade(r1.fastRate).color"
            />
          </div>
          <div
            class="flex min-w-0 flex-1 items-center border-l border-gray-100 px-2.5"
          >
            <ClpmBulletChart
              class="mx-auto w-[94%]"
              compact
              label="准确率"
              :value="r1.accuracyRate"
              :delta="r1Delta.accuracyRate"
              :color="getGrade(r1.accuracyRate).color"
            />
          </div>
          <div
            class="flex min-w-0 flex-1 items-center border-l border-gray-100 px-2.5"
          >
            <ClpmBulletChart
              class="mx-auto w-[94%]"
              compact
              label="平均自控率"
              :value="r1.autoModeRate"
              :delta="r1Delta.autoModeRate"
              :color="getGrade(r1.autoModeRate).color"
            />
          </div>
          <div
            class="flex min-w-0 flex-1 items-center border-l border-gray-100 px-2.5"
          >
            <ClpmBulletChart
              class="mx-auto w-[94%]"
              compact
              label="好值率"
              :value="r1.goodValueRate"
              :delta="r1Delta.goodValueRate"
              :color="getGrade(r1.goodValueRate).color"
            />
          </div>

          <!-- 回路统计：2×2 网格（总数/自控/手动/参评） -->
          <div
            class="grid min-w-0 flex-1 content-center gap-x-2 gap-y-0.5 border-l border-gray-100 px-2 text-[10px] text-gray-400"
            style="grid-template-columns: 1fr 1fr"
          >
            <div class="flex items-center gap-1 whitespace-nowrap">
              <span>总数</span>
              <span class="font-mono text-sm font-bold text-gray-700">{{
                r1.totalLoops
              }}</span>
            </div>
            <div class="flex items-center gap-1 whitespace-nowrap">
              <span>参评</span>
              <span
                class="font-mono text-sm font-bold"
                :class="
                  r1.evaluatedLoops > 0 ? 'text-blue-700' : 'text-gray-700'
                "
                >{{ r1.evaluatedLoops }}</span
              >
            </div>
            <div class="flex items-center gap-1 whitespace-nowrap">
              <span>自控</span>
              <span class="font-mono text-sm font-bold text-emerald-700">{{
                r1.autoCount
              }}</span>
            </div>
            <div class="flex items-center gap-1 whitespace-nowrap">
              <span>手动</span>
              <span class="font-mono text-sm font-bold text-red-600">{{
                r1.manualCount
              }}</span>
            </div>
          </div>

          <!-- 等级分布：饼图 + 右侧图例 -->
          <div
            class="flex min-w-0 flex-1 items-center justify-center gap-1.5 border-l border-gray-100 px-1.5"
          >
            <div class="flex flex-col items-center gap-0.5">
              <span class="text-[9px] text-gray-400">等级分布</span>
              <div
                v-if="pieSvg"
                class="flex-none cursor-pointer"
                v-html="pieSvg"
              ></div>
              <div
                v-else
                class="flex h-[48px] w-[48px] flex-none items-center justify-center rounded-full bg-gray-50 text-[8px] text-gray-300"
              >
                暂无
              </div>
            </div>
            <!-- 图例（色块 + 标签 + 数量） -->
            <div class="flex flex-col gap-0.5 text-[9px]">
              <div
                v-for="seg in pieSegments"
                :key="seg.label"
                class="flex items-center gap-1"
              >
                <span
                  class="inline-block h-2 w-2 flex-none rounded-sm"
                  :style="{ background: seg.color }"
                ></span>
                <span class="text-gray-500">{{ seg.label }}</span>
                <span class="font-mono font-bold text-gray-700">{{
                  seg.count
                }}</span>
              </div>
            </div>
          </div>
        </div>

        <!-- ══════ 中排四列：全厂雷达 / 装置-单元树形排名 / 重点回路 / 运行状态 ══════ -->
        <div class="flex h-[398px] flex-none gap-1">
          <!-- §2 全厂雷达（六维平均，自行2右端迁入中排左侧，替代原装置排名区） -->
          <div
            class="flex w-[calc(20%_-_3px)] min-w-0 flex-col rounded border border-gray-200 bg-white"
          >
            <div
              class="flex h-8 flex-none items-center border-b border-gray-100 px-2.5 text-[11px] font-bold text-gray-700"
            >
              全厂雷达
              <span class="ml-auto text-[9px] font-normal text-gray-400">{{
                twLabel
              }}</span>
            </div>
            <div class="min-h-0 flex-1 p-1">
              <div
                v-if="radarSvg"
                v-html="radarSvg"
                class="h-full w-full"
              ></div>
              <div
                v-else
                class="flex h-full items-center justify-center text-xs text-gray-300"
              >
                暂无全厂指标
              </div>
            </div>
            <div
              class="flex h-7 flex-none items-center border-t border-gray-100 px-2.5 text-[10px] text-gray-400"
            >
              六维平均 · 单系列
            </div>
          </div>

          <!-- §3 装置-单元树形排名（合并原装置/单元排名：装置行折叠/展开单元行，表头点击切换排序维度） -->
          <div
            class="flex w-[calc(40%_-_3px)] min-w-0 flex-col rounded border border-gray-200 bg-white dark:border-slate-700 dark:bg-slate-800"
          >
            <div
              class="flex h-8 flex-none items-center border-b border-gray-100 px-2.5 text-[11px] font-bold text-gray-700 dark:border-slate-700 dark:text-slate-100"
            >
              装置-单元排名
              <span
                class="ml-auto text-[9px] font-normal text-gray-400 dark:text-slate-500"
                >全厂 · 点击表头排序 · {{ twLabel }}</span
              >
            </div>
            <!-- 表头（按字符数百分比分配：3+3+12+4+4+5×5=51字符，按用户要求每列 = N/39*100%） -->
            <div
              class="grid h-8 flex-none items-center border-b border-gray-100 bg-gray-50/60 px-2.5 text-[11px] text-gray-500 dark:border-slate-700 dark:bg-slate-700/40 dark:text-slate-400"
              style="
                grid-template-columns:
                  calc(3 / 39 * 100%) calc(3 / 39 * 100%)
                  calc(12 / 39 * 100%) calc(4 / 39 * 100%) calc(4 / 39 * 100%)
                  repeat(5, calc(5 / 39 * 100%));
              "
            >
              <span class="text-center"></span>
              <span class="text-center">排名</span>
              <span class="truncate px-1.5">装置 / 单元</span>
              <button
                class="cursor-pointer border-0 bg-transparent text-right text-[11px]"
                :class="
                  unitSortKey === 'loops'
                    ? 'font-bold text-blue-700 dark:text-blue-400'
                    : 'text-gray-500 hover:text-blue-600 dark:text-slate-400 dark:hover:text-blue-400'
                "
                title="按回路数降序"
                @click="unitSortKey = 'loops'"
              >
                回路数{{ unitSortKey === 'loops' ? ' ▾' : '' }}
              </button>
              <span class="text-center">等级</span>
              <button
                v-for="k in ['score', 'steady', 'fast', 'acc', 'auto'] as const"
                :key="k"
                class="cursor-pointer border-0 bg-transparent text-right text-[11px]"
                :class="
                  unitSortKey === k
                    ? 'font-bold text-blue-700 dark:text-blue-400'
                    : 'text-gray-500 hover:text-blue-600 dark:text-slate-400 dark:hover:text-blue-400'
                "
                :title="`按${{ score: '评分', steady: '平稳率', fast: '快速率', acc: '准确率', auto: '自控率' }[k]}降序`"
                @click="unitSortKey = k"
              >
                {{
                  {
                    score: '评分',
                    steady: '平稳率',
                    fast: '快速率',
                    acc: '准确率',
                    auto: '自控率',
                  }[k]
                }}{{ unitSortKey === k ? ' ▾' : '' }}
              </button>
            </div>
            <!-- 树形数据行：≤10 行时 flex-col 均分高度填满；>10 行时自然高度+滚动 -->
            <div
              class="min-h-0 flex-1"
              :class="
                stretchRows
                  ? 'rank-rows-stretch flex flex-col overflow-hidden'
                  : 'overflow-y-auto'
              "
            >
              <div
                v-for="row in treeRows"
                :key="row.id"
                class="data-row grid cursor-pointer items-center border-b border-gray-50 px-2.5 text-[12px] leading-snug dark:border-slate-700/60"
                :class="[
                  stretchRows ? '' : 'py-1.5',
                  row.kind === 'area'
                    ? 'bg-gray-50/70 font-bold dark:bg-slate-700/30'
                    : 'hover:bg-blue-50/60 dark:hover:bg-slate-700/40',
                  selected?.id === row.id
                    ? 'bg-blue-50 dark:bg-blue-900/30'
                    : '',
                ]"
                :style="{
                  gridTemplateColumns:
                    'calc(3 / 39 * 100%) calc(3 / 39 * 100%) calc(12 / 39 * 100%) calc(4 / 39 * 100%) calc(4 / 39 * 100%) repeat(5, calc(5 / 39 * 100%))',
                  borderLeft:
                    selected?.id === row.id
                      ? '3px solid #2563eb'
                      : '3px solid transparent',
                }"
                @click="
                  row.kind === 'area'
                    ? row.item
                      ? toggleArea(row.item)
                      : toggleAreaExpand(row.id)
                    : row.item && toggleUnit(row.item)
                "
              >
                <!-- 单选框（选中状态指示） -->
                <span class="text-center">
                  <span
                    class="inline-block h-2.5 w-2.5 rounded-full border"
                    :class="
                      selected?.id === row.id
                        ? 'border-blue-600 bg-blue-600'
                        : 'border-gray-300 bg-white dark:border-slate-500 dark:bg-slate-700'
                    "
                  ></span>
                </span>
                <!-- 排名序号（装置行显示红色排名，单元行留空） -->
                <span
                  class="text-center font-mono text-[11px]"
                  :class="
                    row.kind === 'area' && row.rank <= 3
                      ? 'font-bold text-red-500'
                      : row.kind === 'area'
                        ? 'font-bold text-gray-400 dark:text-slate-500'
                        : 'text-transparent'
                  "
                  >{{ row.kind === 'area' ? row.rank : '·' }}</span
                >
                <!-- 名称（折叠箭头在名称前；单元行缩进） -->
                <span
                  class="flex items-center gap-1 truncate px-1.5"
                  :class="
                    row.kind === 'area'
                      ? 'text-gray-800 dark:text-white'
                      : 'text-gray-600 dark:text-slate-300'
                  "
                  :title="row.name"
                >
                  <span
                    v-if="row.kind === 'area'"
                    class="flex-none w-3 text-center text-[10px] text-gray-400 hover:text-blue-600 dark:text-slate-500 dark:hover:text-blue-400"
                    @click.stop="toggleAreaExpand(row.id)"
                    >{{ expandedAreas.has(row.id) ? '▼' : '►' }}</span
                  >
                  <span
                    class="min-w-0 truncate"
                    :class="row.kind === 'unit' ? 'pl-4' : ''"
                    >{{ row.name }}</span
                  >
                </span>
                <!-- 回路数 -->
                <span
                  class="text-right font-mono"
                  :class="
                    row.kind === 'area'
                      ? 'font-bold text-gray-700 dark:text-slate-200'
                      : 'text-gray-500 dark:text-slate-400'
                  "
                  >{{ metricText(row.item, 'loopCount', 0) }}</span
                >
                <!-- 等级（A/B/C/D/E 色块） -->
                <span class="text-center">
                  <span
                    v-if="row.item"
                    class="inline-flex h-4 w-4 items-center justify-center rounded text-[10px] font-bold text-white"
                    :style="{ background: getGrade(row.item.score).color }"
                    :title="getGrade(row.item.score).label"
                    >{{ getGrade(row.item.score).letter }}</span
                  >
                  <span v-else class="text-gray-300 dark:text-slate-600"
                    >—</span
                  >
                </span>
                <!-- 5 个指标列（评分带等级色） -->
                <span
                  v-for="(m, idx) in [
                    'score',
                    'steadyRate',
                    'fastRate',
                    'accuracyRate',
                    'autoModeRate',
                  ] as const"
                  :key="m"
                  class="text-right font-mono"
                  :class="
                    row.kind === 'area'
                      ? idx === 0
                        ? 'font-bold'
                        : 'font-semibold dark:text-slate-200'
                      : 'text-gray-600 dark:text-slate-400'
                  "
                  :style="
                    idx === 0 && row.item
                      ? { color: getGrade(row.item.score).color }
                      : {}
                  "
                  >{{ metricText(row.item, m, 1) }}</span
                >
              </div>
              <div
                v-if="treeRows.length === 0"
                class="flex h-full items-center justify-center text-sm text-gray-300 dark:text-slate-600"
              >
                暂无装置/单元评分数据
              </div>
            </div>
            <div
              class="flex h-7 flex-none items-center border-t border-gray-100 px-2.5 text-[11px] dark:border-slate-700"
            >
              <span class="text-gray-400 dark:text-slate-500"
                >范围:
                <span class="font-bold text-gray-600 dark:text-slate-200">{{
                  scopeLabel
                }}</span
                >，装置 {{ areaRanking.length }} / 单元
                {{ unitRanking.length }}</span
              >
              <button
                v-if="selected"
                class="ml-auto cursor-pointer rounded border border-gray-200 px-1.5 py-0.5 text-[9px] text-gray-500 hover:border-blue-300 hover:text-blue-600 dark:border-slate-600 dark:text-slate-400 dark:hover:border-blue-500 dark:hover:text-blue-400"
                @click="clearSelection"
              >
                清除选择
              </button>
              <span v-else class="ml-auto text-gray-300 dark:text-slate-600"
                >点击行联动趋势/回路 · 箭头折叠</span
              >
            </div>
          </div>

          <!-- §4 重点回路 -->
          <div
            class="flex w-[calc(20%_-_3px)] min-w-0 flex-col rounded border border-gray-200 bg-white"
          >
            <div
              class="flex h-8 flex-none items-center gap-2 border-b border-gray-100 px-2.5 text-[11px] font-bold text-gray-700"
            >
              重点回路
              <div
                class="ml-auto flex overflow-hidden rounded border border-gray-200 text-[10px]"
              >
                <button
                  class="border-0 px-2 py-0.5"
                  :class="
                    topMode === 'asc'
                      ? 'bg-blue-700 text-white'
                      : 'bg-white text-gray-600'
                  "
                  @click="topMode = 'asc'"
                >
                  评分最低 10
                </button>
                <button
                  class="border-0 border-l border-gray-200 px-2 py-0.5"
                  :class="
                    topMode === 'desc'
                      ? 'bg-blue-700 text-white'
                      : 'bg-white text-gray-600'
                  "
                  @click="topMode = 'desc'"
                >
                  评分最高 10
                </button>
              </div>
            </div>
            <div class="min-h-0 flex-1 overflow-y-auto px-2 py-1.5">
              <div
                v-for="(item, idx) in topLoops"
                :key="item.loopId"
                class="mb-1 flex cursor-pointer items-center gap-2 rounded border border-gray-200 bg-white px-2 py-1.5 hover:border-blue-300 hover:shadow-sm"
                @click="goToLoop(item.loopId)"
              >
                <span
                  class="w-5 flex-none font-mono text-[10px] font-bold"
                  :class="
                    topMode === 'asc' && idx < 3
                      ? 'text-red-500'
                      : 'text-gray-400'
                  "
                  >{{ idx + 1 }}</span
                >
                <div class="min-w-0 flex-1">
                  <div
                    class="truncate font-mono text-[11px] font-bold text-gray-800"
                  >
                    {{ item.tagName }}
                  </div>
                  <div class="truncate text-[9px] text-gray-400">
                    {{ item.unitName || '—' }}
                  </div>
                </div>
                <span
                  v-if="item.score !== null"
                  class="flex-none font-mono text-[12px] font-bold"
                  :style="{ color: getGrade(item.score).color }"
                  >{{ item.score.toFixed(1) }}</span
                >
                <span
                  v-else
                  class="flex-none rounded border border-gray-200 px-1 text-[8px] text-gray-400"
                  >待评估</span
                >
                <span
                  v-if="item.score !== null"
                  class="flex-none rounded border px-1 text-[9px] font-bold"
                  :style="{
                    color: getGrade(item.score).color,
                    borderColor: `${getGrade(item.score).color}33`,
                    background: `${getGrade(item.score).color}11`,
                  }"
                  >{{ getGrade(item.score).label }}</span
                >
                <span class="flex-none text-[11px] text-blue-600">→</span>
              </div>
              <div
                v-if="topLoops.length === 0"
                class="flex h-full items-center justify-center text-xs text-gray-300"
              >
                暂无回路评分数据
              </div>
            </div>
            <div
              class="flex h-7 flex-none items-center border-t border-gray-100 px-2.5 text-[10px]"
            >
              <span class="text-gray-400"
                >范围:
                <span class="font-bold text-gray-600">{{ scopeLabel }}</span>
                ·
                {{ topMode === 'asc' ? '最低' : '最高' }} 10</span
              >
              <button
                v-if="selected"
                class="ml-auto cursor-pointer rounded border border-gray-200 px-1.5 py-0.5 text-[9px] text-gray-500 hover:border-blue-300 hover:text-blue-600"
                @click="clearSelection"
              >
                清除选择
              </button>
              <span v-else class="ml-auto text-gray-300"
                >点击行进入回路工作台</span
              >
            </div>
          </div>

          <!-- §6 运行状态（实时口径：MODE 分布 + 阀门 OP 行程越限，不随时间窗变化） -->
          <div
            class="flex w-[calc(20%_-_3px)] min-w-0 flex-col rounded border border-gray-200 bg-white"
          >
            <div
              class="flex h-8 flex-none items-center border-b border-gray-100 px-2.5 text-[11px] font-bold text-gray-700"
            >
              运行状态
              <span
                class="ml-auto truncate text-[9px] font-normal"
                :class="rtStale ? 'text-gray-400' : 'text-emerald-600'"
                >{{ rtMeta }}</span
              >
            </div>
            <!-- MODE 分布行列表（自动/串级/远程/先控/手动，手动红显） -->
            <div class="flex-none space-y-1 px-2.5 pt-1.5">
              <div
                v-for="row in modeRows"
                :key="row.label"
                class="flex items-center gap-1.5 text-[10px]"
              >
                <span
                  class="w-6 flex-none"
                  :class="
                    row.emphasis ? 'font-bold text-red-500' : 'text-gray-500'
                  "
                  >{{ row.label }}</span
                >
                <div class="h-1.5 min-w-0 flex-1 rounded bg-gray-100">
                  <div
                    class="h-1.5 rounded"
                    :style="{
                      width: `${row.pct}%`,
                      background: row.emphasis ? '#c23434' : '#94a3b8',
                    }"
                  ></div>
                </div>
                <span
                  class="w-7 flex-none text-right font-mono"
                  :class="row.emphasis ? 'text-red-500' : 'text-gray-600'"
                  >{{ row.count }}</span
                >
                <span class="w-8 flex-none text-right text-[9px] text-gray-400"
                  >{{ row.pct }}%</span
                >
              </div>
            </div>
            <div class="mx-2.5 mt-1.5 border-t border-gray-100"></div>
            <!-- 阀门 OP 行程越限列表 -->
            <div class="flex min-h-0 flex-1 flex-col px-2.5 py-1">
              <div class="flex-none text-[10px] text-gray-500">
                阀门运行区间异常
                <span
                  class="font-mono font-bold cursor-pointer hover:underline"
                  :class="
                    valveAlerts.length > 0 ? 'text-red-500' : 'text-gray-400'
                  "
                  @click="valveAlerts.length > 0 && goToAttention(selected?.id)"
                  >{{ valveAlerts.length }}</span
                >
                <span class="text-[9px] text-gray-400"
                  >回路（OP≤5% 或 ≥95%）</span
                >
              </div>
              <div class="min-h-0 flex-1 overflow-y-auto">
                <div
                  v-for="v in valveAlerts"
                  :key="v.loopId"
                  class="flex cursor-pointer items-center gap-1.5 rounded px-1 py-0.5 text-[10px] hover:bg-blue-50/60"
                  @click="goToLoop(v.loopId)"
                >
                  <span
                    class="min-w-0 flex-1 truncate font-mono text-gray-700"
                    >{{ v.tagName }}</span
                  >
                  <span class="flex-none font-mono text-[9px] text-red-500">{{
                    v.range
                  }}</span>
                </div>
                <div
                  v-if="valveAlerts.length === 0"
                  class="py-3 text-center text-[10px] text-gray-300"
                >
                  无越限回路
                </div>
              </div>
            </div>
            <div
              class="flex h-7 flex-none items-center border-t border-gray-100 px-2.5 text-[10px] text-gray-400"
            >
              实时口径 · 不随时间窗变化
            </div>
          </div>
        </div>

        <!-- ══════ 趋势排两列：§5 绩效趋势(60%) / §7 装置指标对比(40%)，填满宽度 ══════ -->
        <div class="flex h-[398px] flex-none gap-1">
          <!-- §5 绩效趋势（双 Y 轴柱线组合；60% 宽，线框图标尺主力图） -->
          <div
            class="flex w-[calc(60%_-_2px)] min-w-0 flex-none flex-col rounded border border-gray-200 bg-white"
          >
            <div
              class="flex h-9 flex-none items-center gap-2 border-b border-gray-100 px-2.5"
            >
              <span class="text-[11px] font-bold text-gray-700">绩效趋势</span>
              <!-- 图例（六项全部可点击 toggle；副标题省略——底部状态行已含范围信息） -->
              <div
                class="ml-auto flex items-center gap-1.5 text-[9px]"
                data-testid="trend-legend"
              >
                <button
                  class="flex cursor-pointer items-center gap-1 border-0 bg-white"
                  :class="scoreVisible ? 'text-gray-600' : 'text-gray-300'"
                  @click="scoreVisible = !scoreVisible"
                >
                  <span
                    class="inline-block h-0.5 w-2.5 rounded"
                    :style="{
                      background: scoreVisible ? LINE_COLORS.score : '#cbd5e1',
                    }"
                  ></span>
                  综合评分
                </button>
                <button
                  v-for="lg in [
                    { key: 'steady', label: '平稳率' },
                    { key: 'fast', label: '快速率' },
                    { key: 'acc', label: '准确率' },
                    { key: 'auto', label: '自控率' },
                  ] as {
                    key: 'acc' | 'auto' | 'fast' | 'steady';
                    label: string;
                  }[]"
                  :key="lg.key"
                  class="flex cursor-pointer items-center gap-1 border-0 bg-white"
                  :class="
                    lineVisible[lg.key] ? 'text-gray-600' : 'text-gray-300'
                  "
                  @click="lineVisible[lg.key] = !lineVisible[lg.key]"
                >
                  <span
                    class="inline-block h-0.5 w-2.5 rounded"
                    :style="{
                      background: lineVisible[lg.key]
                        ? LINE_COLORS[lg.key]
                        : '#cbd5e1',
                    }"
                  ></span>
                  {{ lg.label }}
                </button>
              </div>
            </div>
            <div class="min-h-0 flex-1 px-2 py-0.5">
              <div
                v-if="trendSvg"
                class="relative h-full w-full"
                data-testid="trend-chart"
                @mousemove="onTrendMove"
                @mouseleave="onTrendLeave"
              >
                <div v-html="trendSvg" class="h-full w-full"></div>
                <!-- 悬浮十字线 + 统一悬浮框 -->
                <template v-if="trendHoverIdx !== null && trendGeo && trendTip">
                  <div
                    class="pointer-events-none absolute z-10 w-px bg-slate-400/60"
                    :style="{
                      left: `${(trendHoverX / trendGeo.W) * 100}%`,
                      top: `${(trendGeo.T / trendGeo.H) * 100}%`,
                      height: `${(trendGeo.ih / trendGeo.H) * 100}%`,
                    }"
                  ></div>
                  <div
                    class="pointer-events-none absolute top-1.5 z-20 w-max min-w-28 rounded border border-gray-200 bg-white/95 px-2 py-1.5 text-[10px] shadow-lg"
                    :style="
                      trendHoverX > trendGeo.W * 0.62
                        ? {
                            right: `${((trendGeo.W - trendHoverX + 8) / trendGeo.W) * 100}%`,
                          }
                        : {
                            left: `${((trendHoverX + 8) / trendGeo.W) * 100}%`,
                          }
                    "
                  >
                    <div
                      class="mb-1 border-b border-gray-100 pb-0.5 font-mono text-[9px] text-gray-400"
                    >
                      {{ trendTip.time }}
                    </div>
                    <div
                      v-for="row in trendTip.rows"
                      :key="row.label"
                      class="flex items-center gap-1.5 leading-4"
                    >
                      <span
                        class="inline-block h-1.5 w-1.5 flex-none rounded-sm"
                        :style="{ background: row.color }"
                      ></span>
                      <span class="text-gray-500">{{ row.label }}</span>
                      <span
                        class="ml-auto font-mono font-semibold text-gray-700"
                        >{{ row.text }}</span
                      >
                    </div>
                  </div>
                </template>
              </div>
              <div
                v-else
                class="flex h-full items-center justify-center text-xs text-gray-300"
              >
                暂无趋势数据
              </div>
            </div>
            <div
              class="flex h-6 flex-none items-center border-t border-gray-100 px-3 text-[10px] text-gray-400"
            >
              选中:
              <span class="font-bold text-gray-600">{{ scopeLabel }}</span> ·
              {{ twLabel }} · 告警线 {{ warningThreshold }}
            </div>
          </div>

          <!-- §7 装置指标对比（分组柱状图，点击柱组 = 选中装置；40% 宽） -->
          <div
            class="flex w-[calc(40%_-_2px)] min-w-0 flex-none flex-col rounded border border-gray-200 bg-white"
          >
            <div
              class="flex h-9 flex-none items-center gap-2 border-b border-gray-100 px-2.5"
            >
              <span class="text-[11px] font-bold text-gray-700"
                >装置指标对比</span
              >
              <span class="ml-auto text-[9px] font-normal text-gray-400"
                >{{ twLabel }} · 点击柱组选中</span
              >
            </div>
            <div class="min-h-0 flex-1 p-0.5" @click="onAreaBarsClick($event)">
              <div
                v-if="areaBarsSvg"
                v-html="areaBarsSvg"
                class="h-full w-full"
              ></div>
              <div
                v-else
                class="flex h-full items-center justify-center text-xs text-gray-300"
              >
                暂无装置数据
              </div>
            </div>
            <div
              class="flex h-6 flex-none items-center justify-center gap-2.5 text-[9px] text-gray-500"
            >
              <span class="flex items-center gap-1"
                ><span
                  class="inline-block h-2 w-2 rounded-sm"
                  :style="{ background: AREA_BAR_COLORS.steady }"
                ></span
                >平稳</span
              >
              <span class="flex items-center gap-1"
                ><span
                  class="inline-block h-2 w-2 rounded-sm"
                  :style="{ background: AREA_BAR_COLORS.fast }"
                ></span>
                快速
              </span>
              <span class="flex items-center gap-1"
                ><span
                  class="inline-block h-2 w-2 rounded-sm"
                  :style="{ background: AREA_BAR_COLORS.acc }"
                ></span
                >准确</span
              >
              <span class="flex items-center gap-1"
                ><span
                  class="inline-block h-2 w-2 rounded-sm"
                  :style="{ background: AREA_BAR_COLORS.auto }"
                ></span
                >自控</span
              >
            </div>
          </div>
        </div>
      </div>
    </Spin>
  </Page>
</template>

<style scoped>
/* ≤10 行时，data-row 均分列表区高度，等间距填满 */
.rank-rows-stretch > .data-row {
  flex: 1 1 0;
  min-height: 0;
}
</style>
