<script lang="ts">
/**
 * P6-02b（O02-CONTEXT/PL）：指标分析页深链 query 解析/构造纯函数。
 * 独立 <script> 块导出供 vitest 直接覆盖（不依赖 vue）：
 * - parseIndicatorQuery：消费全部携带参数（metric/window/tsStart/tsEnd/tz/
 *   fitness/plantNodeId/plantNodeName/from）；无效参数 notices 显式列出，不
 *   静默丢弃；tsStart/tsEnd 精确窗优先于 window 滚动窗枚举（禁止近似替换）。
 * - buildIndicatorQuery：状态 → query（syncQuery 真相源；customWindow 激活时
 *   写精确窗键不写 window；from 原样保留）。
 * - equalLengthPrevRange：等长环比窗口 [start-Δ, start]（PL-02 等长比较）。
 */
import { localTimezone } from '#/views/cockpit/wb-comps/use-drill';

export interface IndicatorCustomWindow {
  end: string;
  start: string;
  timezone: string;
}

export interface IndicatorQueryState {
  customWindow: IndicatorCustomWindow | null;
  fitness: string;
  from: null | string;
  metric: string;
  notices: string[];
  plantNodeId: string | undefined;
  plantNodeName: string;
  window: string;
}

function queryStr(v: unknown): string {
  if (Array.isArray(v)) return typeof v[0] === 'string' ? v[0] : '';
  return typeof v === 'string' ? v : '';
}

export function parseIndicatorQuery(
  q: Record<string, unknown>,
  opts: {
    defaultMetric: string;
    defaultWindow: string;
    validMetrics: string[];
    validWindows: string[];
  },
): IndicatorQueryState {
  const notices: string[] = [];

  let metric = opts.defaultMetric;
  const rawMetric = queryStr(q.metric);
  if (rawMetric) {
    if (opts.validMetrics.includes(rawMetric)) {
      metric = rawMetric;
    } else {
      notices.push(`无法识别的指标参数“${rawMetric}”，已按默认指标展示`);
    }
  }

  let window = opts.defaultWindow;
  let customWindow: IndicatorCustomWindow | null = null;
  const tsStart = queryStr(q.tsStart);
  const tsEnd = queryStr(q.tsEnd);
  const rawWin = queryStr(q.window);
  if (tsStart || tsEnd) {
    const a = Date.parse(tsStart);
    const b = Date.parse(tsEnd);
    if (tsStart && tsEnd && Number.isFinite(a) && Number.isFinite(b) && b > a) {
      customWindow = {
        end: new Date(b).toISOString(),
        start: new Date(a).toISOString(),
        timezone: queryStr(q.tz) || localTimezone(),
      };
    } else {
      notices.push('时间窗参数（tsStart/tsEnd）缺失或无效，已忽略并按默认窗口展示');
    }
  }
  if (!customWindow && rawWin) {
    if (opts.validWindows.includes(rawWin)) {
      window = rawWin;
    } else {
      notices.push(`无法识别的时间窗参数“${rawWin}”，已按默认窗口展示`);
    }
  }

  const fitness = queryStr(q.fitness);
  const plantNodeId = queryStr(q.plantNodeId) || undefined;
  const plantNodeName = plantNodeId
    ? queryStr(q.plantNodeName) || '指定节点'
    : '全厂';
  const from = queryStr(q.from) || null;

  return {
    customWindow,
    fitness,
    from,
    metric,
    notices,
    plantNodeId,
    plantNodeName,
    window,
  };
}

export function buildIndicatorQuery(s: {
  customWindow: IndicatorCustomWindow | null;
  fitness: string;
  fitnessFilterOn: boolean;
  from: null | string;
  metric: string;
  plantNodeId: string | undefined;
  plantNodeName: string;
  window: string;
}): Record<string, string> {
  const query: Record<string, string> = { metric: s.metric };
  if (s.customWindow) {
    query.tsStart = s.customWindow.start;
    query.tsEnd = s.customWindow.end;
    query.tz = s.customWindow.timezone;
  } else {
    query.window = s.window;
  }
  if (s.plantNodeId) {
    query.plantNodeId = s.plantNodeId;
    // 占位符不写入 URL；仅携带真实节点名（展示用途，随深链可还原）
    if (s.plantNodeName && s.plantNodeName !== '指定节点') {
      query.plantNodeName = s.plantNodeName;
    }
  }
  // fitness 仅在过滤仍关闭时保留（用户重新打开过滤即失效，不残留）
  if (s.fitness && !s.fitnessFilterOn) query.fitness = s.fitness;
  if (s.from) query.from = s.from;
  return query;
}

/** 等长环比窗口 [start-Δ, start]（PL-02：等长比较；自定义窗无滚动窗枚举可比） */
export function equalLengthPrevRange(
  start: string,
  end: string,
): { endTime: string; startTime: string } {
  const a = Date.parse(start);
  const b = Date.parse(end);
  const dur = b - a;
  return {
    endTime: new Date(a).toISOString(),
    startTime: new Date(a - dur).toISOString(),
  };
}
</script>

<script setup lang="ts">
/**
 * 指标分析页（评估模块第 5 页，docs/MVP设计/12-指标分析页设计方案.md）
 *
 * 指标维度横切分析："这个指标全厂怎么样？弱在哪套装置？"
 * 一套骨架服务 6 指标（默认自控率），五区块版式：
 * - A 指标概览卡：均值/中位数/满分回路/参评数（带环比 Δ）+ 环比恶化最多 + 最差装置
 * - B TOP10 排行：最差优先横向条形，点击条形 → 回路工作台
 * - C 装置对比：节点级横向条形（全厂视角，选中节点高亮），点击装置 → 以该节点过滤全页
 * - E 指标分布：十档直方图（分档语义色 + 均值线），回答"全厂分布形态"
 * - D 行动清单：TOP20 回路明细（指标值 + 环比 + 综合评分 + 可信度 + fitness）
 *
 * 环比口径：上一等长滚动窗口（如 today=近 24h → 环比=[now-48h, now-24h]），
 * 与后端滚动窗口语义对齐，经 custom startTime/endTime 拉取。
 *
 * 数据源：
 * - 回路排行 GET /performance/ranking（M1 已扩展 accuracy/auto_mode/effective_auto 排序白名单）
 * - 装置排行 GET /performance/nodes/ranking（sortBy=score 拉全量，前端按所选指标重排）
 *
 * fitness 过滤默认开：L0/L1 不适用回路不进入图表与清单（与 TOP5 口径一致）。
 * 深链：?metric=&plantNodeId=&window= 为真相源，控件切换时 replace 同步。
 * P6-02b：新增 tsStart/tsEnd/tz 精确窗（优先于 window 滚动窗枚举，禁近似
 * 替换）与 from 来源消费——回显于窗口下拉首项（allow-clear 单项清除回退
 * 原预设），无效参数逐条提示不静默丢弃；回路下钻携精确窗口/节点 scope/
 * from 至回路工作台，返回恢复本页筛选现场。
 */
import type { TableColumnsType } from 'ant-design-vue';

import type { RouteLocationNormalizedLoaded } from 'vue-router';

import type { EchartsUIType } from '@vben/plugins/echarts';

import type { MetricApi, TimeWindowParam } from '#/api/metric';
import type { PlantNodeApi } from '#/api/plant-node';

import { computed, nextTick, onActivated, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';
import { IconifyIcon } from '@vben/icons';
import { EchartsUI, useEcharts } from '@vben/plugins/echarts';
import { useUserStore } from '@vben/stores';

import {
  Button,
  Card,
  Drawer,
  Select,
  Spin,
  Switch,
  Table,
  Tooltip,
} from 'ant-design-vue';

import { getNodeRankingApi, getRankingApi } from '#/api/metric';
import {
  ClpmDataCanvas,
  ClpmPageToolbar,
  ClpmStandardActions,
} from '#/components/clpm';
import ConfidenceBadge from '#/components/clpm/confidence-badge.vue';
import PlantNodeTree from '#/components/plant-node/plant-node-tree.vue';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import { useEchartsPreset } from '#/composables/use-echarts-preset';
import { showPageHelp, usePageToolbar } from '#/composables/use-page-toolbar';
import {
  canOpenWorkbench360,
  wb360DrillQuery,
} from '#/views/cockpit/wb-comps/use-drill';

defineOptions({ name: 'IndicatorAnalysis' });

const route = useRoute();
const router = useRouter();
const userStore = useUserStore();
const { isDark, themeColors } = useClpmTheme();
const { axisBase } = useEchartsPreset();

// ===== 指标元数据（单一定义点，方案 §5）=====

interface MetricMeta {
  /** 后端 ranking sortBy（snake_case） */
  sortKey: string;
  /** 回路排行返回体字段（camelCase） */
  field: keyof MetricApi.RankingItem;
  /** 节点排行返回体字段（注意快速率为 fastResponseRate） */
  nodeField: string;
  label: string;
  help: string;
  /** 值域上限（百分比类 100，综合评分 100） */
  max: number;
}

const METRIC_OPTIONS: MetricMeta[] = [
  {
    sortKey: 'auto_mode_rate',
    field: 'autoModeRate',
    nodeField: 'autoModeRate',
    label: '自控率',
    help: '自动模式时间占比。合规第一指标，低自控率的根因多为管理问题（手操纪律/仪表故障）。',
    max: 100,
  },
  {
    sortKey: 'accuracy_rate',
    field: 'accuracyRate',
    nodeField: 'accuracyRate',
    label: '准确率',
    help: 'PV 对 SP 的跟踪能力。按准确率找最差回路即整定资源投向清单。',
    max: 100,
  },
  {
    sortKey: 'fast_rate',
    field: 'fastRate',
    nodeField: 'fastResponseRate',
    label: '快速率',
    help: '扰动恢复速度指标，越低越差。',
    max: 100,
  },
  {
    sortKey: 'steady_rate',
    field: 'steadyRate',
    nodeField: 'steadyRate',
    label: '平稳率',
    help: '运行平稳程度，越低越差。',
    max: 100,
  },
  {
    sortKey: 'good_value_rate',
    field: 'goodValueRate',
    nodeField: 'goodValueRate',
    label: '好值率',
    help: 'PV 处于好值带的时间占比，越低越差。',
    max: 100,
  },
  {
    sortKey: 'score',
    field: 'score',
    nodeField: 'score',
    label: '综合评分',
    help: '3 核心 + 1 综合加权评分，越低越差（与总览 TOP5 同口径）。',
    max: 100,
  },
];

// ===== fitness tag 中文映射（与 loop-performance/pid-dashboard 共用口径）=====

const NA_TAG_CN: Record<string, string> = {
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
  T_CONSTANT_SETPOINT: 'SP 长时间未变',
  T_OOS_PV: 'PV 量程外点比例过高',
  T_BAD_OP_RANGE: 'OP 长期顶边或贴底',
};

function fitnessNATip(level: null | string, tags: null | string[]): string {
  const tagText =
    tags && tags.length > 0
      ? tags.map((t) => NA_TAG_CN[t] ?? t).join('、')
      : '适用性不足';
  return `不适用（${level || 'NA'}）：${tagText}`;
}

/** 数值格式化（与 loop-performance.vue 同口径，保留 2 位小数） */
function formatNumber(val: null | number | undefined, suffix = ''): string {
  if (val === null || val === undefined) return '—';
  return `${val.toFixed(2)}${suffix}`;
}

// ===== 控制条状态（深链真相源）=====

const timeWindowOptions = [
  { label: '近8小时', value: 'last_8_hours' },
  { label: '24小时', value: 'today' },
  { label: '168小时', value: 'last_7_days' },
  { label: '近1月', value: 'last_30_days' },
];

/** 各时间窗时长（毫秒），与后端滚动窗口语义对齐（today=now-24h，非自然日） */
const WINDOW_DURATIONS_MS: Record<string, number> = {
  last_30_days: 30 * 24 * 3_600_000,
  last_7_days: 7 * 24 * 3_600_000,
  last_8_hours: 8 * 3_600_000,
  today: 24 * 3_600_000,
};

/** 环比窗口 = 上一等长滚动窗口 [now-2Δ, now-Δ]；未知窗口无环比 */
function prevWindowRange(
  win: string,
): undefined | { endTime: string; startTime: string } {
  const dur = WINDOW_DURATIONS_MS[win];
  if (!dur) return undefined;
  const now = Date.now();
  return {
    startTime: new Date(now - 2 * dur).toISOString(),
    endTime: new Date(now - dur).toISOString(),
  };
}

const selectedMetricKey = ref('auto_mode_rate');
const timeWindow = ref('today');
/** 02b：深链精确窗（tsStart/tsEnd/tz 消费）；激活时优先于 timeWindow 预设 */
const customWindow = ref<IndicatorCustomWindow | null>(null);
/** 02b：深链来源（from 消费；返回按钮恢复来源页筛选现场） */
const fromRef = ref<null | string>(null);
/** 02b：深链无效参数提示（不静默丢弃，逐条回显） */
const queryNotices = ref<string[]>([]);
/** 深链 fitness 原值（L0/L1 → 关闭适用性过滤；syncQuery 仅过滤仍关时回写） */
const fitnessRaw = ref('');
const selectedPlantNodeId = ref<string | undefined>(undefined);
const selectedPlantNodeName = ref('全厂');
/** 2026-10-22 用户裁决：评估默认不做适用性过滤（L0/L1 不默认剔除） */
const fitnessFilter = ref(false);
const treeDrawerOpen = ref(false);

const metricMeta = computed(
  () =>
    METRIC_OPTIONS.find((m) => m.sortKey === selectedMetricKey.value) ??
    METRIC_OPTIONS[0]!,
);

// ===== 数据加载 =====

const rankingItems = ref<MetricApi.RankingItem[]>([]);
const prevRankingItems = ref<MetricApi.RankingItem[]>([]);
/** E5（2026-10-01）：上窗拉取失败显式标记（区别于"真无上窗数据"） */
const prevLoadFailed = ref(false);
const nodeItems = ref<MetricApi.NodeRankingItem[]>([]);
const loading = ref(false);
const loadError = ref(false);

/** 02b：生效窗口 API 参数（深链精确窗优先；预设滚动窗兜底） */
const windowApiParams = computed<
  | { endTime: string; startTime: string; timeWindow: 'custom' }
  | { timeWindow: TimeWindowParam }
>(() =>
  customWindow.value
    ? {
        timeWindow: 'custom',
        startTime: customWindow.value.start,
        endTime: customWindow.value.end,
      }
    : { timeWindow: timeWindow.value as TimeWindowParam },
);

async function loadAll() {
  loading.value = true;
  loadError.value = false;
  prevLoadFailed.value = false;
  const params: MetricApi.RankingQueryParams = {
    ...windowApiParams.value,
    sortBy: metricMeta.value.sortKey,
    sortOrder: 'asc',
    limit: 100,
    // 适用性过滤下推服务端：先剔除 L0/L1 再排序取 top-N。客户端过滤在
    // L0/L1 回路 ≥limit 时会把榜单整个滤空（如全厂 961 回路中 898 个不参评，
    // 最差 100 条全为 L1），页面恒空态
    fitnessFilter: fitnessFilter.value,
  };
  if (selectedPlantNodeId.value) params.plantNodeId = selectedPlantNodeId.value;
  // 环比：上一等长窗口（PL-02 等长比较——预设=滚动窗 [now-2Δ, now-Δ]，
  // 深链精确窗=[start-Δ, start]）；拉取失败不阻塞主数据（catch 内静默降级）；
  // 过滤口径与当前窗口一致，保证环比双方回路集合可比
  const prevRange = customWindow.value
    ? equalLengthPrevRange(customWindow.value.start, customWindow.value.end)
    : prevWindowRange(timeWindow.value);
  const prevParams: MetricApi.RankingQueryParams | undefined = prevRange
    ? {
        timeWindow: 'custom',
        startTime: prevRange.startTime,
        endTime: prevRange.endTime,
        sortBy: metricMeta.value.sortKey,
        sortOrder: 'asc',
        limit: 100,
        fitnessFilter: fitnessFilter.value,
        ...(selectedPlantNodeId.value
          ? { plantNodeId: selectedPlantNodeId.value }
          : {}),
      }
    : undefined;
  try {
    const [ranking, nodes, prev] = await Promise.all([
      getRankingApi(params),
      // C 区：score 排序拉全量节点（返回体含全部指标字段），前端按所选指标重排。
      // 不指定 nodeType——回路挂载层级随环境而异（zpdev 生产挂 AREA、种子环境
      // 挂 UNIT），由 sortedNodes 按「有数据的最深层级」自适应取用
      getNodeRankingApi({
        ...windowApiParams.value,
        sortBy: 'score',
        sortOrder: 'desc',
        limit: 200,
      }),
      prevParams
        ? getRankingApi(prevParams).catch(() => {
            // E5：失败显式标记（原静默按空=与"真无上窗数据"不可区分）
            prevLoadFailed.value = true;
            return [] as MetricApi.RankingItem[];
          })
        : Promise.resolve([] as MetricApi.RankingItem[]),
    ]);
    rankingItems.value = ranking;
    nodeItems.value = nodes;
    prevRankingItems.value = prev;
  } catch {
    loadError.value = true;
    // 错误 toast 由拦截器统一处理；保留旧数据
  } finally {
    loading.value = false;
  }
}

/** fitness + 指标值过滤：排除不参评回路；适用性过滤开启时排除 L0/L1（方案 §4）；
 *  当前指标无值回路始终排除（无值在“最差排行”占位会误导，与 C 区 sortedNodes 口径对齐） */
const filteredItems = computed(() =>
  rankingItems.value.filter((it) => {
    if (it.includeInEvaluation === false) return false;
    if (
      fitnessFilter.value &&
      (it.fitnessLevel === 'L0' || it.fitnessLevel === 'L1')
    ) {
      return false;
    }
    return it[metricMeta.value.field] != null;
  }),
);

const top10 = computed(() => filteredItems.value.slice(0, 10));
const actionRows = computed(() => filteredItems.value.slice(0, 20));

/** B 区提示：TOP10 全部同值时排行无信息量（如自控率全 100%），
 *  显式提示避免满格等长条形被误读为“无数据/图未渲染” */
const topValuesUniform = computed(() => {
  const vals = top10.value
    .map((it) => it[metricMeta.value.field] as null | number)
    .filter((v): v is number => v != null);
  if (vals.length === 0 || vals.length < top10.value.length) return false;
  return Math.min(...vals) === Math.max(...vals);
});

/** A 区概览：均值（当前规模 <100 时为全量均值；截断偏差见方案 §10 风险表） */
const curValues = computed(() =>
  filteredItems.value
    .map((it) => it[metricMeta.value.field] as null | number)
    .filter((v): v is number => v != null),
);

/**
 * R03-4（P3-D6b）：统计基数对账——"共 M 条"= 参评口径（includeInEvaluation
 * + 适用性过滤）下的回路数；"有效 N 条"= 其中该指标有值的回路。null 不作 0
 * 参与统计（均值/中位数/分布桶均只算有效值），未计算条数显式展示。
 */
const participationBaseItems = computed(() =>
  rankingItems.value.filter((it) => {
    if (it.includeInEvaluation === false) return false;
    if (
      fitnessFilter.value &&
      (it.fitnessLevel === 'L0' || it.fitnessLevel === 'L1')
    ) {
      return false;
    }
    return true;
  }),
);
const uncomputedCount = computed(
  () => participationBaseItems.value.length - curValues.value.length,
);

const avgValue = computed(() => {
  const vals = curValues.value;
  if (vals.length === 0) return null;
  return vals.reduce((a, b) => a + b, 0) / vals.length;
});

/** 中位数（比均值更抗极端值，治理场景常看；入参已剔除 null） */
function medianOf(vals: number[]): null | number {
  if (vals.length === 0) return null;
  const sorted = [...vals].toSorted((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 1
    ? sorted[mid]!
    : (sorted[mid - 1]! + sorted[mid]!) / 2;
}

const medianValue = computed(() => medianOf(curValues.value));

// ===== 环比（上一等长滚动窗口，口径与当前一致的同套过滤）=====

const prevFilteredItems = computed(() =>
  prevRankingItems.value.filter((it) => {
    if (it.includeInEvaluation === false) return false;
    if (
      fitnessFilter.value &&
      (it.fitnessLevel === 'L0' || it.fitnessLevel === 'L1')
    ) {
      return false;
    }
    return it[metricMeta.value.field] != null;
  }),
);

const prevValues = computed(() =>
  prevFilteredItems.value
    .map((it) => it[metricMeta.value.field] as null | number)
    .filter((v): v is number => v != null),
);

const prevAvg = computed(() =>
  prevValues.value.length > 0
    ? prevValues.value.reduce((a, b) => a + b, 0) / prevValues.value.length
    : null,
);
const prevMedian = computed(() => medianOf(prevValues.value));
const hasPrevData = computed(() => prevValues.value.length > 0);

/** 环比 Δ（正值=改善，6 指标均为越高越好）；无上窗数据时 null（不作 0） */
const avgDelta = computed(() =>
  hasPrevData.value &&
  avgValue.value != null &&
  prevAvg.value != null
    ? avgValue.value - prevAvg.value
    : null,
);
const medianDelta = computed(() =>
  hasPrevData.value &&
  medianValue.value != null &&
  prevMedian.value != null
    ? medianValue.value - prevMedian.value
    : null,
);
const countDelta = computed(() =>
  hasPrevData.value
    ? filteredItems.value.length - prevFilteredItems.value.length
    : null,
);

/** 满分回路（该指标达满值，如自控率 100%；治理健康度信号） */
const fullScoreCount = computed(
  () => curValues.value.filter((v) => v >= metricMeta.value.max - 1e-9).length,
);
const fullScorePct = computed(() =>
  curValues.value.length > 0
    ? (fullScoreCount.value / curValues.value.length) * 100
    : null,
);

/** 环比恶化最多回路（当前窗口 vs 上窗同回路；无恶化/无上窗数据时 undefined） */
const worstDecline = computed(() => {
  const prevMap = new Map(
    prevFilteredItems.value.map((it) => [
      it.loopId,
      it[metricMeta.value.field] as null | number,
    ]),
  );
  let worst: undefined | { delta: number; item: MetricApi.RankingItem };
  for (const it of filteredItems.value) {
    const cur = it[metricMeta.value.field] as null | number;
    const prev = prevMap.get(it.loopId);
    if (cur == null || prev == null) continue;
    const delta = cur - prev;
    if (delta < 0 && (!worst || delta < worst.delta)) {
      worst = { delta, item: it };
    }
  }
  return worst;
});

/** 环比格式化：+3.20pp / -0.50pp / ±0.00pp */
function formatDelta(d: number): string {
  if (Math.abs(d) < 0.005) return '±0.00pp';
  return `${d > 0 ? '+' : ''}${d.toFixed(2)}pp`;
}

/** 单回路环比（D 区表格列）；上窗无该回路或无值时 null */
function deltaOf(it: MetricApi.RankingItem): null | number {
  const prev = prevFilteredItems.value.find((p) => p.loopId === it.loopId);
  if (!prev) return null;
  const pv = prev[metricMeta.value.field] as null | number;
  const cur = it[metricMeta.value.field] as null | number;
  if (pv == null || cur == null) return null;
  return cur - pv;
}

// ===== E 区：指标分布（十档直方图）=====

interface DistBucket {
  count: number;
  /** 档上界（0-100 十档） */
  to: number;
}

const distBuckets = computed<DistBucket[]>(() => {
  const buckets = Array.from(
    { length: 10 },
    (_, i): DistBucket => ({
      count: 0,
      to: (i + 1) * 10,
    }),
  );
  for (const v of curValues.value) {
    const idx = Math.min(9, Math.max(0, Math.floor(v / 10)));
    buckets[idx]!.count += 1;
  }
  return buckets;
});

/** 分布分档语义色：弱区红 / 中间档琥珀 / 健康档工业蓝 */
function distBucketColor(to: number): string {
  if (to <= 60) return themeColors.value.DANGER;
  if (to <= 80) return themeColors.value.WARNING;
  return themeColors.value.INFO;
}

/** C 区：节点按所选指标升序（最差装置在前）。
 *  层级自适应：节点快照按「有数据的最深层级」取用（EQUIPMENT>UNIT>AREA，
 *  均无则回退非 FACTORY，最后兜底 FACTORY）——回路挂载层级随环境而异
 *  （zpdev 生产挂 AREA、种子环境挂 UNIT），固定请求单一层级会在另一侧恒空 */
const sortedNodes = computed(() => {
  const field = metricMeta.value.nodeField;
  const typeRank = ['AREA', 'UNIT', 'EQUIPMENT'];
  const present = new Set(
    nodeItems.value.map((n) => n.plantNodeType ?? '').filter(Boolean),
  );
  const deepest = [...typeRank].toReversed().find((t) => present.has(t));
  const pool = deepest
    ? nodeItems.value.filter((n) => n.plantNodeType === deepest)
    : nodeItems.value.filter(
        (n) => n.plantNodeType !== 'FACTORY' || nodeItems.value.length === 1,
      );
  return [...pool]
    .map((n) => ({
      node: n,
      value: (n as Record<string, unknown>)[field] as null | number,
    }))
    .filter((r) => r.value != null)
    .toSorted((a, b) => a.value! - b.value!) // R03-4：null 已前置剔除，不作 0 排序
    .slice(0, 10);
});

const worstUnitName = computed(() =>
  sortedNodes.value.length > 0
    ? (sortedNodes.value[0]?.node.plantNodeName ?? '—')
    : '—',
);

/** C 区空态文案：有装置数据但当前指标聚合值全空（如快速率）时给精准提示 */
const unitChartEmptyText = computed(() =>
  nodeItems.value.length > 0 ? '该指标暂无装置聚合数据' : '暂无装置聚合数据',
);

// ===== 深链（query 为真相源；02b：消费全部参数+回显+单项清除+返回恢复）=====

function applyQuery(q: RouteLocationNormalizedLoaded['query']): boolean {
  const parsed = parseIndicatorQuery(q as Record<string, unknown>, {
    defaultMetric: 'auto_mode_rate',
    defaultWindow: 'today',
    validMetrics: METRIC_OPTIONS.map((m) => m.sortKey),
    validWindows: timeWindowOptions.map((o) => o.value),
  });
  queryNotices.value = parsed.notices;
  let changed = false;
  if (parsed.metric !== selectedMetricKey.value) {
    selectedMetricKey.value = parsed.metric;
    changed = true;
  }
  // 精确窗激活时预设窗保留为用户兜底（清除精确窗后回原预设，不回默认档）
  if (!parsed.customWindow && parsed.window !== timeWindow.value) {
    timeWindow.value = parsed.window;
    changed = true;
  }
  const nextCw = parsed.customWindow;
  const cwKey = (c: IndicatorCustomWindow | null) =>
    c ? `${c.start}~${c.end}@${c.timezone}` : '';
  if (cwKey(nextCw) !== cwKey(customWindow.value)) {
    customWindow.value = nextCw;
    changed = true;
  }
  // 追溯矩阵 G6：fitness=L0/L1 深链（整定 Tab 适用性环形图下钻）时关闭适用性
  // 过滤，否则 L0/L1 目标回路会被默认过滤排除，下钻落地看不到任何数据
  fitnessRaw.value = parsed.fitness;
  if (
    (parsed.fitness === 'L0' || parsed.fitness === 'L1') &&
    fitnessFilter.value
  ) {
    fitnessFilter.value = false;
    changed = true;
  }
  if (parsed.plantNodeId !== selectedPlantNodeId.value) {
    selectedPlantNodeId.value = parsed.plantNodeId;
    changed = true;
  }
  // 节点名随 query 携带（syncQuery 写入）：query 变化后恢复时无名称则显示占位符“指定节点”
  selectedPlantNodeName.value = parsed.plantNodeId
    ? parsed.plantNodeName || '指定节点'
    : '全厂';
  fromRef.value = parsed.from;
  return changed;
}

function syncQuery() {
  router.replace({
    query: buildIndicatorQuery({
      customWindow: customWindow.value,
      fitness: fitnessRaw.value,
      fitnessFilterOn: fitnessFilter.value,
      from: fromRef.value,
      metric: selectedMetricKey.value,
      plantNodeId: selectedPlantNodeId.value,
      plantNodeName: selectedPlantNodeName.value,
      window: timeWindow.value,
    }),
  });
}

function onControlChange() {
  // 用户手动重开适用性过滤 → 深链 fitness 触发条件失效，不残留回写
  if (fitnessFilter.value) fitnessRaw.value = '';
  syncQuery();
  loadAll();
}

/** 02b：窗口下拉（精确窗作为临时首项回显；allow-clear=单项清除精确窗） */
const CUSTOM_WINDOW_VALUE = '__custom';

const customWindowLabel = computed(() => {
  const c = customWindow.value;
  if (!c) return '';
  const fmt = (iso: string) => {
    const d = new Date(iso);
    const pad = (n: number) => String(n).padStart(2, '0');
    return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  };
  return `${fmt(c.start)} ~ ${fmt(c.end)}（${c.timezone}）`;
});

const windowSelectOptions = computed(() =>
  customWindow.value
    ? [
        { label: `指定窗口：${customWindowLabel.value}`, value: CUSTOM_WINDOW_VALUE },
        ...timeWindowOptions,
      ]
    : timeWindowOptions,
);

const windowSelectValue = computed({
  get: () => (customWindow.value ? CUSTOM_WINDOW_VALUE : timeWindow.value),
  set: (v: string | undefined) => {
    if (!v || v === CUSTOM_WINDOW_VALUE) {
      if (!v && customWindow.value) {
        // allow-clear 单项清除：仅移除精确窗，回退用户原预设，其余筛选不动
        customWindow.value = null;
        onControlChange();
      }
      return;
    }
    customWindow.value = null;
    timeWindow.value = v;
    onControlChange();
  },
});

/** 02b：返回来源页（from 消费；来源 query 原样恢复其筛选现场） */
function goBackFrom() {
  if (fromRef.value) void router.push(fromRef.value);
}

function onTreeSelect(node: null | PlantNodeApi.PlantNode) {
  if (node) {
    selectedPlantNodeId.value = node.id;
    selectedPlantNodeName.value = node.name;
  } else {
    selectedPlantNodeId.value = undefined;
    selectedPlantNodeName.value = '全厂';
  }
  treeDrawerOpen.value = false;
  onControlChange();
}

/** C 区点击装置 → 下钻该节点 */
function drillUnit(nodeId: string, nodeName: string) {
  selectedPlantNodeId.value = nodeId;
  selectedPlantNodeName.value = nodeName;
  onControlChange();
}

/** 清除装置下钻（C 区“清除”入口） */
function clearPlantNode() {
  selectedPlantNodeId.value = undefined;
  selectedPlantNodeName.value = '全厂';
  onControlChange();
}

// ===== 02b：回路 → 回路工作台（发送端接通 wb360 上下文协议）=====

/** 当前生效窗口的精确起止/时区（禁止滚动窗枚举近似替换） */
function effectivePreciseWindow(): {
  timezone: string;
  tsEnd: string;
  tsStart: string;
} {
  if (customWindow.value) {
    return {
      timezone: customWindow.value.timezone,
      tsEnd: customWindow.value.end,
      tsStart: customWindow.value.start,
    };
  }
  const dur = WINDOW_DURATIONS_MS[timeWindow.value] ?? WINDOW_DURATIONS_MS.today!;
  const now = Date.now();
  return {
    timezone: localTimezone(),
    tsEnd: new Date(now).toISOString(),
    tsStart: new Date(now - dur).toISOString(),
  };
}

/** SPONSOR 无工作台路由权限 → 纯文本降级，不造死链（同 ClpmLoopLink 口径） */
const canOpenWorkbench = computed(() =>
  canOpenWorkbench360(userStore.userInfo?.roles ?? []),
);

function openLoopInWorkbench(loopId: string) {
  const { path, query } = wb360DrillQuery(
    {
      loopId,
      window: effectivePreciseWindow(),
      ...(selectedPlantNodeId.value
        ? { scope: { nodeId: selectedPlantNodeId.value } }
        : {}),
    },
    // from=当前完整 query：返回恢复本页 metric/窗口/节点/fitness 筛选现场
    { from: route.fullPath },
  );
  void router.push({ path, query });
}

// ===== B/C 区图表 =====

const topChartRef = ref<EchartsUIType>();
const unitChartRef = ref<EchartsUIType>();
const distChartRef = ref<EchartsUIType>();
const { renderEcharts: renderTopChart } = useEcharts(topChartRef);
const { renderEcharts: renderUnitChart } = useEcharts(unitChartRef);
const { renderEcharts: renderDistChart } = useEcharts(distChartRef);

async function renderCharts() {
  // 首渲竞态修复（2026-08-25 浏览器验收缺陷①）：pre-flush watch 触发时
  // EchartsUI 可能尚未挂载（ClpmDataCanvas 骨架屏分支 + v-if 双重未就绪），
  // use-echarts 的 30ms 重试链存在静默失败窗口导致 TOP10 空白；post watch
  // + nextTick 确保 DOM 就绪后再渲染，容器对应数据为空时跳过该分支
  await nextTick();

  // B：TOP10 最差优先横向条形（inverse 使最差置顶）
  if (top10.value.length > 0) {
    const topChart = await renderTopChart({
      grid: { left: 8, right: 40, top: 8, bottom: 8, containLabel: true },
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        valueFormatter: (v) =>
          formatNumber(v as number, metricMeta.value.max === 100 ? '%' : ''),
      },
      xAxis: {
        type: 'value',
        max: metricMeta.value.max,
        ...axisBase.value,
      },
      yAxis: {
        type: 'category',
        inverse: true,
        data: top10.value.map((it) => it.tagName || it.loopId),
        ...axisBase.value,
      },
      series: [
        {
          type: 'bar',
          barMaxWidth: 18,
          // 单蓝 accent 纪律：排行条形统一工业蓝（use-clpm-theme INFO）
          itemStyle: {
            color: themeColors.value.INFO,
            borderRadius: [0, 2, 2, 0],
          },
          data: top10.value.map((it) => ({
            value: it[metricMeta.value.field] as number,
            loopId: it.loopId,
          })),
        },
      ],
    });
    // B 区条形点击 → 回路工作台（02b：携带精确窗口/节点 scope/from 返回）：
    // echarts 实例级事件（模板 @click 只能收到 DOM 事件拿不到数据项）；
    // 主题切换重建实例后随每次渲染重绑，off 防重复
    topChart?.off('click');
    topChart?.on('click', (params) => {
      const data = params.data as undefined | { loopId?: string };
      if (params.componentType === 'series' && data?.loopId) {
        openLoopInWorkbench(data.loopId);
      }
    });
  }

  // C：装置对比横向条形（前端按所选指标重排，最差置顶）
  if (sortedNodes.value.length > 0) {
    const unitChart = await renderUnitChart({
      grid: { left: 8, right: 40, top: 8, bottom: 8, containLabel: true },
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        valueFormatter: (v) =>
          formatNumber(v as number, metricMeta.value.max === 100 ? '%' : ''),
      },
      xAxis: {
        type: 'value',
        max: metricMeta.value.max,
        ...axisBase.value,
      },
      yAxis: {
        type: 'category',
        inverse: true,
        data: sortedNodes.value.map(
          (r) => r.node.plantNodeName ?? r.node.plantNodeId,
        ),
        ...axisBase.value,
      },
      series: [
        {
          type: 'bar',
          barMaxWidth: 18,
          itemStyle: {
            color: themeColors.value.INFO,
            borderRadius: [0, 2, 2, 0],
          },
          // 全厂视角 + 当前下钻节点实色、其余半透明（缺陷②语义显式化：UNIT
          // 为回路挂载层无子级快照，C 区定位为全厂横向对比 + 下钻入口）
          data: sortedNodes.value.map((r) => ({
            value: r.value as number,
            nodeId: r.node.plantNodeId,
            nodeName: r.node.plantNodeName,
            itemStyle:
              selectedPlantNodeId.value &&
              r.node.plantNodeId !== selectedPlantNodeId.value
                ? { opacity: 0.45 }
                : undefined,
          })),
        },
      ],
    });
    // C 区条形点击 → 下钻该装置（实例级事件，同 B 区说明）
    unitChart?.off('click');
    unitChart?.on('click', (params) => {
      const data = params.data as
        | undefined
        | { nodeId?: string; nodeName?: null | string };
      if (params.componentType === 'series' && data?.nodeId) {
        drillUnit(data.nodeId, data.nodeName ?? data.nodeId);
      }
    });
  }

  // E：指标分布十档直方图（分布形态，与 TOP10 尾部/装置空间维度互补）
  if (curValues.value.length > 0) {
    const labels = distBuckets.value.map((b) => `${b.to - 10}-${b.to}`);
    // 均值参考线：定位到最近档位（类目轴 markLine）
    const avgBucketLabel =
      avgValue.value == null
        ? null
        : (labels[Math.min(9, Math.max(0, Math.floor(avgValue.value / 10)))] ??
          null);
    await renderDistChart({
      grid: { left: 8, right: 16, top: 24, bottom: 8, containLabel: true },
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        formatter: (params) => {
          const p = Array.isArray(params) ? params[0] : params;
          const bucket = distBuckets.value[p?.dataIndex ?? 0];
          if (!bucket) return '';
          const pct = ((bucket.count / curValues.value.length) * 100).toFixed(
            1,
          );
          return `${bucket.to - 10}-${bucket.to}：${bucket.count} 条（${pct}%）`;
        },
      },
      xAxis: {
        type: 'category',
        data: labels,
        ...axisBase.value,
      },
      yAxis: {
        type: 'value',
        minInterval: 1,
        ...axisBase.value,
      },
      series: [
        {
          type: 'bar',
          barMaxWidth: 36,
          // 分档语义色：≤60 弱区红 / 60-80 中间琥珀 / ≥80 健康工业蓝
          data: distBuckets.value.map((b) => ({
            value: b.count,
            itemStyle: { color: distBucketColor(b.to) },
          })),
          ...(avgBucketLabel
            ? {
                markLine: {
                  silent: true,
                  symbol: 'none',
                  lineStyle: {
                    color: themeColors.value.NEUTRAL,
                    type: 'dashed',
                  },
                  label: {
                    formatter: '均值',
                    position: 'insideEndTop',
                    color: themeColors.value.NEUTRAL,
                  },
                  data: [{ xAxis: avgBucketLabel }],
                },
              }
            : {}),
        },
      ],
    });
  }
}

// ===== D 区行动清单 =====

const actionColumns: TableColumnsType = [
  {
    title: '排名',
    key: 'rank',
    dataIndex: 'rank',
    width: 56,
    align: 'center' as const,
  },
  {
    title: '回路位号',
    key: 'tagName',
    dataIndex: 'tagName',
    width: 170,
    ellipsis: true,
  },
  { title: '名称', key: 'loopName', dataIndex: 'loopName', ellipsis: true },
  {
    title: '装置',
    key: 'unitName',
    dataIndex: 'unitName',
    width: 140,
    ellipsis: true,
  },
  {
    title: () => metricMeta.value.label,
    key: 'metricValue',
    width: 110,
    align: 'right' as const,
  },
  {
    title: '环比',
    key: 'delta',
    width: 96,
    align: 'right' as const,
  },
  {
    title: '综合评分',
    key: 'score',
    dataIndex: 'score',
    width: 90,
    align: 'right' as const,
  },
  {
    title: '可信度',
    key: 'confidenceLevel',
    dataIndex: 'confidenceLevel',
    width: 90,
    align: 'center' as const,
  },
  {
    title: '适用性',
    key: 'fitness',
    width: 90,
    align: 'center' as const,
  },
];

function metricValueOf(it: MetricApi.RankingItem): null | number {
  return it[metricMeta.value.field] as null | number;
}

// ===== 工具栏 =====

const { toolbarItems } = usePageToolbar(() => ({
  refresh: { onClick: loadAll, loading: loading.value },
  help: {
    onClick: () =>
      showPageHelp({
        title: '指标分析',
        content:
          '按单一指标横切全厂回路：TOP10 最差排行定位问题回路，装置对比定位薄弱单元，行动清单直达处置入口。支持工厂节点下钻、时间窗切换与适用性过滤（L0/L1 不适用回路默认排除）。图表给结论，清单给行动。',
      }),
  },
}));

// ===== 主题切换重渲 =====

watch(isDark, () => renderCharts());
// post flush：DOM 更新（v-if 应用）后再渲染，消除首渲竞态（缺陷①）；
// selectedPlantNodeId 入 watch 使 C 区选中高亮随下钻即时刷新；
// filteredItems 覆盖 top10/distBuckets（均为其派生）
watch([filteredItems, sortedNodes, selectedPlantNodeId], () => renderCharts(), {
  flush: 'post',
});
// keep-alive 返回时 use-echarts 已重建实例，主动重渲（缺陷①实测场景）
onActivated(() => renderCharts());

// ===== 生命周期 =====

// fullPathKey=false 后组件跨 query 变化复用（不重挂载）：外部深链携新 query
// 进入时 onMounted 不再执行，由 query watch 响应；仅状态有实质变化才重载
// （本页 syncQuery 触发的 watch 回调中状态已一致，不会重复请求）
watch(
  () => route.query,
  (q) => {
    if (applyQuery(q)) loadAll();
  },
);

onMounted(() => {
  applyQuery(route.query);
  loadAll();
});
</script>

<template>
  <Page auto-content-height>
    <div class="flex h-full flex-col gap-3">
      <ClpmPageToolbar
        title="指标分析"
        subtitle="指标维度横切 · TOP 排行 · 装置对比 · 行动清单"
        :loading="loading"
      >
        <template #actions>
          <ClpmStandardActions :items="toolbarItems" />
        </template>
      </ClpmPageToolbar>

      <!-- 控制条 -->
      <Card :body-style="{ padding: '12px 16px' }">
        <div class="flex flex-wrap items-center gap-3">
          <Button
            v-if="fromRef"
            size="small"
            title="返回来源页（恢复其筛选现场）"
            @click="goBackFrom"
          >
            <template #icon>
              <IconifyIcon icon="lucide:arrow-left" />
            </template>
            返回来源
          </Button>
          <Select
            v-model:value="selectedMetricKey"
            :options="
              METRIC_OPTIONS.map((m) => ({ label: m.label, value: m.sortKey }))
            "
            style="width: 140px"
            @change="onControlChange"
          />
          <!-- 02b：精确窗作为首项回显；allow-clear=单项清除（回退原预设窗） -->
          <Select
            v-model:value="windowSelectValue"
            :options="windowSelectOptions"
            :allow-clear="!!customWindow"
            style="min-width: 120px"
          />
          <Button size="small" @click="treeDrawerOpen = true">
            <template #icon>
              <IconifyIcon icon="lucide:git-fork" />
            </template>
            {{ selectedPlantNodeName }}
          </Button>
          <Tooltip
            :title="
              fitnessFilter
                ? '已排除 L0/L1 不适用回路（如 SP 恒定/手动模式），避免排行误导'
                : '未排除不适用回路，排行可能包含不适用数据'
            "
          >
            <span class="flex items-center gap-1.5 text-sm text-gray-600">
              适用性过滤
              <Switch
                v-model:checked="fitnessFilter"
                size="small"
                @change="onControlChange"
              />
            </span>
          </Tooltip>
          <span class="ml-auto text-xs text-gray-400">
            {{ metricMeta.label }}：{{ metricMeta.help }}
          </span>
        </div>
        <!-- 02b：深链无效参数逐条回显（不静默丢弃） -->
        <div
          v-if="queryNotices.length > 0"
          class="mt-2 rounded border border-amber-300 bg-amber-50 px-3 py-1.5 text-xs text-amber-700"
          role="alert"
        >
          <div v-for="(n, i) in queryNotices" :key="i">{{ n }}</div>
        </div>
      </Card>

      <ClpmDataCanvas
        class="indicator-canvas flex-1 min-h-0"
        :loading="loading"
        :loading-variant="rankingItems.length > 0 ? 'opacity' : 'skeleton'"
        :error="loadError"
        :empty="!loading && !loadError && filteredItems.length === 0"
        empty-reason="当前时间窗内暂无参评回路快照；可调整时间窗，或先发起评估（单回路→回路工作台评估区，批量/全量→评估任务页）"
        @retry="loadAll"
      >
        <div class="flex h-full flex-col gap-3">
          <!-- A：指标概览卡（均值/中位数/满分/参评数带环比，恶化最多，最薄弱装置） -->
          <div class="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">
            <Card :body-style="{ padding: '12px 16px' }">
              <div class="text-xs text-gray-500">
                {{ selectedPlantNodeName }}{{ metricMeta.label }}均值
              </div>
              <div class="mt-1 text-2xl font-semibold">
                {{ formatNumber(avgValue, metricMeta.max === 100 ? '%' : '') }}
              </div>
              <div
                v-if="avgDelta != null"
                class="mt-0.5 flex items-center gap-1 text-xs font-medium"
                :class="
                  avgDelta > 0
                    ? 'text-emerald-600'
                    : avgDelta < 0
                      ? 'text-red-600'
                      : 'text-gray-400'
                "
              >
                <IconifyIcon
                  :icon="
                    avgDelta > 0
                      ? 'lucide:trending-up'
                      : avgDelta < 0
                        ? 'lucide:trending-down'
                        : 'lucide:minus'
                  "
                  class="size-3.5"
                />
                {{ formatDelta(avgDelta) }}
                <span class="font-normal text-gray-400">环比</span>
              </div>
              <!-- E5：上窗拉取失败与"真无上窗数据"区分显示 -->
              <div
                v-else-if="prevLoadFailed"
                class="mt-0.5 text-xs text-amber-500"
              >
                上窗数据不可用
              </div>
              <div v-else class="mt-0.5 text-xs text-gray-400">—</div>
            </Card>
            <Card :body-style="{ padding: '12px 16px' }">
              <div class="text-xs text-gray-500">
                {{ selectedPlantNodeName }}{{ metricMeta.label }}中位数
              </div>
              <div class="mt-1 text-2xl font-semibold">
                {{
                  formatNumber(medianValue, metricMeta.max === 100 ? '%' : '')
                }}
              </div>
              <div
                v-if="medianDelta != null"
                class="mt-0.5 flex items-center gap-1 text-xs font-medium"
                :class="
                  medianDelta > 0
                    ? 'text-emerald-600'
                    : medianDelta < 0
                      ? 'text-red-600'
                      : 'text-gray-400'
                "
              >
                <IconifyIcon
                  :icon="
                    medianDelta > 0
                      ? 'lucide:trending-up'
                      : medianDelta < 0
                        ? 'lucide:trending-down'
                        : 'lucide:minus'
                  "
                  class="size-3.5"
                />
                {{ formatDelta(medianDelta) }}
                <span class="font-normal text-gray-400">环比</span>
              </div>
              <div v-else class="mt-0.5 text-xs text-gray-400">—</div>
            </Card>
            <Card :body-style="{ padding: '12px 16px' }">
              <Tooltip
                title="该指标达满值的回路（如自控率 100%）；满分占比越高，该指标治理健康度越好"
              >
                <div class="text-xs text-gray-500">满分回路</div>
              </Tooltip>
              <div class="mt-1 text-2xl font-semibold">
                {{ fullScoreCount
                }}<span class="text-sm font-normal text-gray-400">条</span>
              </div>
              <div class="mt-0.5 text-xs text-gray-400">
                {{
                  fullScorePct != null
                    ? `占比 ${fullScorePct.toFixed(1)}%`
                    : '—'
                }}
              </div>
            </Card>
            <Card :body-style="{ padding: '12px 16px' }">
              <div class="text-xs text-gray-500">参评回路数</div>
              <div class="mt-1 text-2xl font-semibold">
                {{ filteredItems.length }}
              </div>
              <!-- R03-4：null 剔除出统计并显式对账——均值/中位数/分布桶
                   只统计有效值行，未计算行不冒充 0 分参与统计 -->
              <div class="mt-0.5 text-xs text-gray-400">
                有效 {{ curValues.length }} 条（共
                {{ participationBaseItems.length }} 条<template
                  v-if="uncomputedCount > 0"
                  >，{{ uncomputedCount }} 条未计算</template
                >）
              </div>
              <div
                v-if="countDelta != null"
                class="mt-0.5 text-xs text-gray-400"
              >
                环比 {{ countDelta > 0 ? '+' : '' }}{{ countDelta }} 条
              </div>
            </Card>
            <Card :body-style="{ padding: '12px 16px' }">
              <Tooltip
                title="当前窗口 vs 上一等长窗口，该指标下降最多的回路；无下降或无上窗数据时显示其他状态"
              >
                <div class="text-xs text-gray-500">环比恶化最多</div>
              </Tooltip>
              <template v-if="worstDecline">
                <div class="mt-1 truncate font-mono text-base font-semibold">
                  {{ worstDecline.item.tagName || worstDecline.item.loopId }}
                </div>
                <div class="mt-0.5 text-xs font-medium text-red-600">
                  {{ formatDelta(worstDecline.delta) }}
                </div>
              </template>
              <template v-else>
                <div
                  class="mt-1 text-base font-semibold"
                  :class="hasPrevData ? 'text-emerald-600' : 'text-gray-400'"
                >
                  {{ hasPrevData ? '无恶化回路' : '—' }}
                </div>
                <div class="mt-0.5 text-xs text-gray-400">
                  {{ hasPrevData ? '较上一窗口' : '无上窗数据' }}
                </div>
              </template>
            </Card>
            <Card :body-style="{ padding: '12px 16px' }">
              <div class="text-xs text-gray-500">最薄弱装置</div>
              <div class="mt-1 truncate text-2xl font-semibold">
                {{ worstUnitName }}
              </div>
            </Card>
          </div>

          <!-- B + C + E：一行三图（按 5:3:4 分配压缩各区宽度，中间区域 flex-1 自适应填满） -->
          <div
            class="mt-3 grid min-h-[280px] flex-1 grid-cols-1 gap-3 lg:grid-cols-12"
          >
            <Card
              class="lg:col-span-5 h-full flex flex-col"
              :body-style="{
                padding: '12px 16px',
                display: 'flex',
                flexDirection: 'column',
                flex: '1 1 auto',
                minHeight: 0,
              }"
            >
              <template #title>
                <span class="text-sm"
                  >TOP10 最差回路（{{ metricMeta.label }}）</span
                >
              </template>
              <template #extra>
                <Tooltip
                  v-if="topValuesUniform"
                  title="当前窗口内适用回路的该指标全部同值，无薄弱回路可排行；可切换时间窗或关闭适用性过滤查看差异"
                >
                  <span class="text-xs text-gray-400">指标无差异</span>
                </Tooltip>
              </template>
              <div class="flex-1 min-h-0">
                <EchartsUI
                  v-if="top10.length > 0"
                  ref="topChartRef"
                  height="100%"
                />
                <div
                  v-else
                  class="flex h-full items-center justify-center text-xs text-gray-400"
                >
                  暂无排行数据
                </div>
              </div>
            </Card>
            <Card
              class="lg:col-span-3 h-full flex flex-col"
              :body-style="{
                padding: '12px 16px',
                display: 'flex',
                flexDirection: 'column',
                flex: '1 1 auto',
                minHeight: 0,
              }"
            >
              <template #title>
                <span class="text-sm">装置对比</span>
              </template>
              <template #extra>
                <span v-if="selectedPlantNodeId" class="text-xs text-gray-400">
                  当前：{{ selectedPlantNodeName }}
                </span>
                <Button
                  v-if="selectedPlantNodeId"
                  type="link"
                  size="small"
                  @click="clearPlantNode"
                >
                  清除下钻
                </Button>
              </template>
              <div class="flex-1 min-h-0">
                <EchartsUI
                  v-if="sortedNodes.length > 0"
                  ref="unitChartRef"
                  height="100%"
                />
                <div
                  v-else
                  class="flex h-full items-center justify-center text-xs text-gray-400"
                >
                  {{ unitChartEmptyText }}
                </div>
              </div>
            </Card>
            <!-- E：指标分布（grid 第三列，紧凑图例） -->
            <Card
              class="lg:col-span-4 h-full flex flex-col"
              :body-style="{
                padding: '12px 16px',
                display: 'flex',
                flexDirection: 'column',
                flex: '1 1 auto',
                minHeight: 0,
              }"
            >
              <template #title>
                <span class="text-sm">{{ metricMeta.label }}分布</span>
              </template>
              <template #extra>
                <span class="flex items-center gap-2 text-xs text-gray-400">
                  <span class="flex items-center gap-0.5">
                    <span
                      class="inline-block size-2 rounded-sm"
                      :style="{ background: themeColors.DANGER }"
                    ></span>
                    ≤60
                  </span>
                  <span class="flex items-center gap-0.5">
                    <span
                      class="inline-block size-2 rounded-sm"
                      :style="{ background: themeColors.WARNING }"
                    ></span>
                    60-80
                  </span>
                  <span class="flex items-center gap-0.5">
                    <span
                      class="inline-block size-2 rounded-sm"
                      :style="{ background: themeColors.INFO }"
                    ></span>
                    ≥80
                  </span>
                </span>
              </template>
              <div class="flex-1 min-h-0">
                <EchartsUI
                  v-if="curValues.length > 0"
                  ref="distChartRef"
                  height="100%"
                />
                <div
                  v-else
                  class="flex h-full items-center justify-center text-xs text-gray-400"
                >
                  暂无分布数据
                </div>
              </div>
            </Card>
          </div>

          <!-- D：行动清单 -->
          <Card
            class="mt-3 flex-1 min-h-[400px]"
            :body-style="{ padding: '12px 16px' }"
          >
            <template #title>
              <span class="text-sm">行动清单（最差 TOP20）</span>
            </template>
            <div class="h-full overflow-auto">
              <Spin :spinning="loading">
                <Table
                  :columns="actionColumns"
                  :data-source="actionRows"
                  :pagination="false"
                  row-key="loopId"
                  size="small"
                >
                  <template #bodyCell="{ column, record }">
                    <template v-if="column.key === 'tagName'">
                      <!-- 02b：位号 → 回路工作台（携带精确窗口/节点 scope/from
                           返回，恢复本页筛选现场）；无权角色降级纯文本 -->
                      <a
                        v-if="canOpenWorkbench"
                        class="clpm-indicator__loop-link"
                        :title="`${(record as MetricApi.RankingItem).tagName} · 在回路工作台打开（携带当前窗口与节点范围，可返回本页）`"
                        @click="
                          openLoopInWorkbench(
                            (record as MetricApi.RankingItem).loopId,
                          )
                        "
                      >
                        {{ (record as MetricApi.RankingItem).tagName }}
                      </a>
                      <span v-else class="clpm-indicator__loop-link--static">
                        {{ (record as MetricApi.RankingItem).tagName }}
                      </span>
                    </template>
                    <template v-else-if="column.key === 'metricValue'">
                      <span class="font-mono font-medium">
                        {{
                          formatNumber(
                            metricValueOf(record as MetricApi.RankingItem),
                            metricMeta.max === 100 ? '%' : '',
                          )
                        }}
                      </span>
                    </template>
                    <template v-else-if="column.key === 'delta'">
                      <template
                        v-if="deltaOf(record as MetricApi.RankingItem) != null"
                      >
                        <span
                          class="flex items-center justify-end gap-0.5 font-mono text-xs font-medium"
                          :class="
                            deltaOf(record as MetricApi.RankingItem)! > 0
                              ? 'text-emerald-600'
                              : deltaOf(record as MetricApi.RankingItem)! < 0
                                ? 'text-red-600'
                                : 'text-gray-400'
                          "
                        >
                          <IconifyIcon
                            :icon="
                              deltaOf(record as MetricApi.RankingItem)! > 0
                                ? 'lucide:trending-up'
                                : deltaOf(record as MetricApi.RankingItem)! < 0
                                  ? 'lucide:trending-down'
                                  : 'lucide:minus'
                            "
                            class="size-3"
                          />
                          {{
                            formatDelta(
                              deltaOf(record as MetricApi.RankingItem)!,
                            )
                          }}
                        </span>
                      </template>
                      <span v-else class="text-gray-400">—</span>
                    </template>
                    <template v-else-if="column.key === 'score'">
                      <span class="font-mono text-xs">
                        {{
                          formatNumber((record as MetricApi.RankingItem).score)
                        }}
                      </span>
                    </template>
                    <template v-else-if="column.key === 'confidenceLevel'">
                      <ConfidenceBadge
                        v-if="(record as MetricApi.RankingItem).confidenceLevel"
                        :level="
                          (record as MetricApi.RankingItem).confidenceLevel!
                        "
                        :valid-rate="
                          (record as MetricApi.RankingItem).validRate
                        "
                      />
                      <span v-else class="text-gray-400">—</span>
                    </template>
                    <template v-else-if="column.key === 'fitness'">
                      <Tooltip
                        :title="
                          fitnessNATip(
                            (record as MetricApi.RankingItem).fitnessLevel ??
                              null,
                            (record as MetricApi.RankingItem).fitnessTags ??
                              null,
                          )
                        "
                      >
                        <span class="font-mono text-xs text-slate-500">
                          {{
                            (record as MetricApi.RankingItem).fitnessLevel ??
                            '—'
                          }}
                        </span>
                      </Tooltip>
                    </template>
                  </template>
                </Table>
              </Spin>
            </div>
          </Card>
        </div>
      </ClpmDataCanvas>

      <!-- 工厂节点树抽屉（与总览一致的下钻交互） -->
      <Drawer
        v-model:open="treeDrawerOpen"
        title="选择工厂节点"
        placement="left"
        :width="280"
        :mask-closable="true"
      >
        <PlantNodeTree card-title="" :width="260" @select="onTreeSelect" />
      </Drawer>
    </div>
  </Page>
</template>

<style scoped>
/* 仅本页面：让 ClpmDataCanvas body 成为 flex 容器，使内部 A/B+C+E/D
   的 flex-1 能正确分配高度（原始公共组件 body 是 block 容器） */
.indicator-canvas :deep(.clpm-data-canvas__body) {
  display: flex;
  flex-direction: column;
}

/* 02b：位号 → 回路工作台上下文深链（等宽字体口径同原 ClpmLoopLink） */
.clpm-indicator__loop-link {
  font-family: 'SF Mono', Monaco, 'Cascadia Code', monospace;
  font-size: 13px;
  font-weight: 500;
  color: hsl(var(--status-info));
  cursor: pointer;
}

.clpm-indicator__loop-link:hover {
  text-decoration: underline;
  opacity: 0.85;
}

.clpm-indicator__loop-link--static {
  font-family: 'SF Mono', Monaco, 'Cascadia Code', monospace;
  font-size: 13px;
  font-weight: 500;
}
</style>
