<script lang="ts" setup>
import type { Dayjs } from 'dayjs';

import type { ModeRow, TrendLines, WorkbenchSelection } from './types';

/**
 * 装置总览 — 管理者版（方案 A，2026-08-24 重排）
 *
 * 布局（1920×1080 一屏无滚动，保持固定高度框架）：
 *   行1 标题行（页面标题 + 实际时间范围 + 页面级时间窗总开关）
 *   行2 全厂结论带（components/conclusion-cards.vue，6 张结论卡，替代原 88px 十一区块）
 *   行3 三列 2:5:3：A 全厂健康结构（health-structure）/ B 装置-单元排名（node-ranking，主区）/
 *        C 治理漏斗（governance-funnel，新增）
 *   行4 两列 3:2：D 绩效趋势（perf-trend）/ E 重点关注回路（focus-loops）
 *   已删除：§2 六维雷达、§7 装置指标对比柱图、行2.5 独立适用性条（迁入 A 列）、
 *           §6 独立运行状态卡（拆入行2 卡6 与 A 列，阀门越限列表收敛为 A 列底部计数行）
 *
 * 本文件只保留组装与数据加载编排：
 *   loadCards / loadRankings / loadTopLoops / loadTrend / loadValveAlerts / loadGovernance
 *   + watch 联动（排名即导航、时间窗总开关、环比基线）。
 *   展示逻辑与 SVG 生成全部下沉 components/ 子组件；定级/格式化共享 use-grade.ts。
 *
 * 设计规范：docs/设计文档/页面标杆设计/04-系统概览/
 */
import type { DashboardApi, MetricApi } from '#/api';
import type { GovernanceApi } from '#/api/governance';
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
} from '#/api';
import { getGovernanceSummaryApi } from '#/api/governance';
import {
  getGradeDistributionApi,
  getLoopSnapshotsApi,
  getNodeRankingApi,
  getNodeTrendApi,
  getRankingApi,
} from '#/api/metric';
import { getPlantNodeTreeApi } from '#/api/plant-node';

import ConclusionCards from './components/conclusion-cards.vue';
import FocusLoops from './components/focus-loops.vue';
import GovernanceFunnel from './components/governance-funnel.vue';
import HealthStructure from './components/health-structure.vue';
import NodeRanking from './components/node-ranking.vue';
import PerfTrend from './components/perf-trend.vue';
import { loadGradeCfgs } from './use-grade';

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

/** 页面级时间窗总开关（行2–行4 统一口径；实时部分不随时间窗变化） */
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

function fmt(v: null | number | undefined, digits = 1): string {
  return v === null || v === undefined ? '--' : v.toFixed(digits);
}

// ================ 状态 ================
const router = useRouter();

/** 选中节点（排名即导航；null = 全厂） */
const selected = ref<null | WorkbenchSelection>(null);

/** E 列重点回路排序：asc = 评分最低 10 / desc = 评分最高 10 */
const topMode = ref<'asc' | 'desc'>('asc');

// 行2 结论卡数据（随 pageTimeWindow 刷新；实时自控率为实时值与时间窗无关）
const agg = ref<DashboardApi.BoardAggregateResult | null>(null);
const autoRate = ref<DashboardApi.AutoRateRt | null>(null);
const gradeDist = ref<GradeDistributionResult | null>(null);
/** 上一窗口聚合（环比基线；加载失败为 null → 不显示环比） */
const prevAgg = ref<null | typeof agg.value>(null);
/** 治理聚合（处置闭环 + 漏斗 + 问题回路，随 pageTimeWindow 刷新） */
const governance = ref<GovernanceApi.GovernanceSummary | null>(null);

// 阀门 OP 行程越限回路（实时快照，与时间窗无关；A 列底部仅展示计数）
interface ValveAlertItem {
  loopId: string;
  tagName: string;
  range: string;
}
const valveAlerts = ref<ValveAlertItem[]>([]);

// B 列排名数据（始终全厂层级）
const areaRanking = ref<MetricApi.NodeRankingItem[]>([]);
const unitRanking = ref<MetricApi.NodeRankingItem[]>([]);

// E 列重点回路（随 selected + topMode）
const topLoops = ref<MetricApi.RankingItem[]>([]);

// D 列趋势（随 selected + pageTimeWindow；evaluated 仅全厂口径有值）
const trend = ref<null | TrendLines>(null);

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
  const fmtDate = (d: Date) =>
    `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:00`;
  return `${fmtDate(start)} ~ ${fmtDate(now)}`;
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

/** 治理聚合（行2 卡3/4/5 + C 列漏斗；与 board/aggregate 同时间窗口径） */
async function loadGovernance() {
  try {
    governance.value = await getGovernanceSummaryApi({
      ...windowParams.value,
    });
  } catch {
    governance.value = null;
  }
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

// 阀门 OP 行程越限（OP min ≤5% 或 max ≥95%，实时快照最新一条；仅计数展示）
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

/** 单元 → 所属装置 映射（B 列树形排名 join 层级；树缺失/未挂载单元归兜底组） */
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

/** 跳转到处置模块（处置任务 Tab：未闭环工单的排程/下达入口） */
function goToHandling() {
  router.push({ path: '/handling/tasks', query: { from: 'overview' } });
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
      loadGovernance();
      loadRankings();
      loadTopLoops();
      loadTrend();
    }
    return;
  }
  showCustomPicker.value = false;
  loadCards();
  loadGovernance();
  loadRankings();
  loadTopLoops();
  loadTrend();
});
/** 自定义范围选定后刷新（小时颗粒度归零即触发） */
watch(customRange, () => {
  if (pageTimeWindow.value === 'custom' && customRange.value) {
    loadCards();
    loadGovernance();
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
    loadGovernance(),
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

// 行2 结论卡（评分/参评/实时自控率）
const r1 = computed(() => {
  const a = agg.value?.aggregate;
  return {
    avgScore: a?.avgScore ?? null,
    evaluatedLoops: a?.evaluatedLoops ?? 0,
    rtAutoRate: autoRate.value?.rate ?? null,
    totalLoops: a?.totalLoops ?? 0,
  };
});

/** 评分环比差值（当前窗口 − 上一窗口；缺基线为 null 不显示） */
const scoreDelta = computed(() => {
  const cur = agg.value?.aggregate?.avgScore;
  const prev = prevAgg.value?.aggregate?.avgScore;
  return typeof cur === 'number' && typeof prev === 'number'
    ? cur - prev
    : null;
});

/** 治理聚合兜底（接口未就绪时各卡显示 0/—） */
const badLoops = computed(() => ({
  warning: governance.value?.badLoops.warning ?? 0,
  poor: governance.value?.badLoops.poor ?? 0,
}));
const handlingSummary = computed(() => ({
  openOrders: governance.value?.handling.openOrders ?? 0,
  overdueOrders: governance.value?.handling.overdueOrders ?? 0,
  closedInWindow: governance.value?.handling.closedInWindow ?? 0,
}));
const funnel = computed(() => governance.value?.funnel ?? null);
const openItems = computed(() => governance.value?.handling.openItems ?? 0);

// ================ 实时口径（行2 卡6 + A 列 MODE 区共用） ================
/** 实时数据过期阈值（分钟），超过则角标/卡标灰警示 */
const RT_STALE_MINUTES =
  Number(import.meta.env.VITE_RT_STALE_MINUTES ?? 10) || 10;

const rtStale = computed(() => {
  const readAt = autoRate.value?.readAt;
  if (!readAt) return true;
  return Date.now() - new Date(readAt).getTime() > RT_STALE_MINUTES * 60_000;
});

/** 实时数据 meta 角标（中断时提示） */
const rtMeta = computed(() => {
  const readAt = autoRate.value?.readAt;
  if (!readAt) return '实时数据中断';
  return `实时 · ${new Date(readAt).toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })}`;
});

/** MODE 分布行（1自动/2串级/3远程/4先控/0手动；行2 卡6 微条 + A 列横条共用） */
const modeRows = computed<ModeRow[]>(() => {
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
</script>

<template>
  <Page auto-content-height>
    <Spin :spinning="pageLoading" class="h-full" wrapper-class-name="h-full">
      <div class="flex h-full flex-col gap-1 overflow-hidden">
        <!-- ══════ 行1 标题行（页面标题深蓝加粗 + 实际时间范围 + 页面级时间窗总开关） ══════ -->
        <div class="flex h-8 flex-none items-center">
          <span class="text-[16px] font-bold tracking-wide text-[#1e40af]"
            >装置总览</span
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
            <!-- 时间窗按钮组（选中蓝底白字，未选中白底灰字+左分隔线） -->
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

        <!-- ══════ 行2 全厂结论带（6 张结论卡，110px） ══════ -->
        <ConclusionCards
          :avg-score="r1.avgScore"
          :score-delta="scoreDelta"
          :evaluated-loops="r1.evaluatedLoops"
          :total-loops="r1.totalLoops"
          :bad-loops="badLoops"
          :handling="handlingSummary"
          :rt-rate="r1.rtAutoRate"
          :mode-rows="modeRows"
          :rt-meta="rtMeta"
          :rt-stale="rtStale"
          @go-attention="goToAttention()"
          @go-handling="goToHandling"
        />

        <!-- ══════ 行3 三列 2:5:3：A 全厂健康结构 / B 装置-单元排名 / C 治理漏斗 ══════ -->
        <div class="flex h-[400px] flex-none gap-1">
          <HealthStructure
            class="flex-[2]"
            :grade-dist="gradeDist"
            :mode-rows="modeRows"
            :rt-meta="rtMeta"
            :rt-stale="rtStale"
            :valve-alert-count="valveAlerts.length"
            @go-attention="goToAttention()"
          />
          <NodeRanking
            class="flex-[5]"
            :area-ranking="areaRanking"
            :unit-ranking="unitRanking"
            :unit-parent-map="unitParentMap"
            :selected-id="selected?.id ?? null"
            @toggle-area="toggleArea"
            @toggle-unit="toggleUnit"
            @clear="clearSelection"
          />
          <GovernanceFunnel
            class="flex-[3]"
            :funnel="funnel"
            :open-items="openItems"
            :tw-label="twLabel"
          />
        </div>

        <!-- ══════ 行4 两列 3:2：D 绩效趋势 / E 重点关注回路 ══════ -->
        <div class="flex h-[400px] flex-none gap-1">
          <PerfTrend
            class="w-[calc(60%_-_2px)] flex-none"
            :trend="trend"
            :trend-hours="trendHours"
            :scope-label="scopeLabel"
            :tw-label="twLabel"
          />
          <FocusLoops
            class="w-[calc(40%_-_2px)] flex-none"
            :top-loops="topLoops"
            :top-mode="topMode"
            :scope-label="scopeLabel"
            :has-selection="!!selected"
            @set-mode="topMode = $event"
            @go-loop="goToLoop"
            @clear="clearSelection"
          />
        </div>
      </div>
    </Spin>
  </Page>
</template>
