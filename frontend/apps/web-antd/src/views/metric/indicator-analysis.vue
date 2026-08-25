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
  ClpmLoopLink,
  ClpmPageToolbar,
  ClpmStandardActions,
} from '#/components/clpm';
import ConfidenceBadge from '#/components/clpm/confidence-badge.vue';
import PlantNodeTree from '#/components/plant-node/plant-node-tree.vue';
import { useClpmTheme } from '#/composables/use-clpm-theme';
import { useEchartsPreset } from '#/composables/use-echarts-preset';
import { showPageHelp, usePageToolbar } from '#/composables/use-page-toolbar';

defineOptions({ name: 'IndicatorAnalysis' });

const route = useRoute();
const router = useRouter();
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
const selectedPlantNodeId = ref<string | undefined>(undefined);
const selectedPlantNodeName = ref('全厂');
const fitnessFilter = ref(true);
const treeDrawerOpen = ref(false);

const metricMeta = computed(
  () =>
    METRIC_OPTIONS.find((m) => m.sortKey === selectedMetricKey.value) ??
    METRIC_OPTIONS[0]!,
);

// ===== 数据加载 =====

const rankingItems = ref<MetricApi.RankingItem[]>([]);
const prevRankingItems = ref<MetricApi.RankingItem[]>([]);
const nodeItems = ref<MetricApi.NodeRankingItem[]>([]);
const loading = ref(false);
const loadError = ref(false);

async function loadAll() {
  loading.value = true;
  loadError.value = false;
  const params: MetricApi.RankingQueryParams = {
    timeWindow: timeWindow.value as TimeWindowParam,
    sortBy: metricMeta.value.sortKey,
    sortOrder: 'asc',
    limit: 100,
  };
  if (selectedPlantNodeId.value) params.plantNodeId = selectedPlantNodeId.value;
  // 环比：上一等长滚动窗口（custom 窗口）；拉取失败不阻塞主数据（catch 内静默降级）
  const prevRange = prevWindowRange(timeWindow.value);
  const prevParams: MetricApi.RankingQueryParams | undefined = prevRange
    ? {
        timeWindow: 'custom',
        startTime: prevRange.startTime,
        endTime: prevRange.endTime,
        sortBy: metricMeta.value.sortKey,
        sortOrder: 'asc',
        limit: 100,
        ...(selectedPlantNodeId.value
          ? { plantNodeId: selectedPlantNodeId.value }
          : {}),
      }
    : undefined;
  try {
    const [ranking, nodes, prev] = await Promise.all([
      getRankingApi(params),
      // C 区：score 排序拉全量节点（返回体含全部指标字段），前端按所选指标重排
      getNodeRankingApi({
        timeWindow: timeWindow.value as TimeWindowParam,
        nodeType: 'UNIT',
        sortBy: 'score',
        sortOrder: 'desc',
        limit: 200,
      }),
      prevParams
        ? getRankingApi(prevParams).catch(() => [] as MetricApi.RankingItem[])
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

const avgValue = computed(() => {
  const vals = curValues.value;
  if (vals.length === 0) return null;
  return vals.reduce((a, b) => a + b, 0) / vals.length;
});

/** 中位数（比均值更抗极端值，治理场景常看） */
function medianOf(vals: number[]): null | number {
  if (vals.length === 0) return null;
  const sorted = [...vals].toSorted((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 1
    ? (sorted[mid] ?? null)
    : ((sorted[mid - 1] ?? 0) + (sorted[mid] ?? 0)) / 2;
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

/** 环比 Δ（正值=改善，6 指标均为越高越好）；无上窗数据时 null */
const avgDelta = computed(() =>
  hasPrevData.value && avgValue.value != null
    ? avgValue.value - (prevAvg.value ?? 0)
    : null,
);
const medianDelta = computed(() =>
  hasPrevData.value && medianValue.value != null
    ? medianValue.value - (prevMedian.value ?? 0)
    : null,
);
const countDelta = computed(() =>
  hasPrevData.value ? filteredItems.value.length - prevFilteredItems.value.length : null,
);

/** 满分回路（该指标达满值，如自控率 100%；治理健康度信号） */
const fullScoreCount = computed(
  () =>
    curValues.value.filter((v) => v >= metricMeta.value.max - 1e-9).length,
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
  const prev = prevFilteredItems.value.find(
    (p) => p.loopId === it.loopId,
  );
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
  const buckets = Array.from({ length: 10 }, (_, i): DistBucket => ({
    count: 0,
    to: (i + 1) * 10,
  }));
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

/** C 区：节点按所选指标升序（最差装置在前） */
const sortedNodes = computed(() => {
  const field = metricMeta.value.nodeField;
  return [...nodeItems.value]
    .map((n) => ({
      node: n,
      value: (n as Record<string, unknown>)[field] as null | number,
    }))
    .filter((r) => r.value != null)
    .toSorted((a, b) => (a.value ?? 0) - (b.value ?? 0))
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

// ===== 深链（query 为真相源）=====

function applyQuery(q: RouteLocationNormalizedLoaded['query']): boolean {
  let changed = false;
  const metric = typeof q.metric === 'string' ? q.metric : '';
  if (
    METRIC_OPTIONS.some((m) => m.sortKey === metric) &&
    metric !== selectedMetricKey.value
  ) {
    selectedMetricKey.value = metric;
    changed = true;
  }
  const win = typeof q.window === 'string' ? q.window : '';
  if (timeWindowOptions.some((o) => o.value === win) && win !== timeWindow.value) {
    timeWindow.value = win;
    changed = true;
  }
  const qNodeId =
    typeof q.plantNodeId === 'string' && q.plantNodeId ? q.plantNodeId : undefined;
  if (qNodeId !== selectedPlantNodeId.value) {
    selectedPlantNodeId.value = qNodeId;
    changed = true;
  }
  // 节点名随 query 携带（syncQuery 写入）：query 变化后恢复时无名称则显示占位符“指定节点”
  const name = typeof q.plantNodeName === 'string' ? q.plantNodeName : '';
  selectedPlantNodeName.value = selectedPlantNodeId.value
    ? name || '指定节点'
    : '全厂';
  return changed;
}

function syncQuery() {
  router.replace({
    query: {
      metric: selectedMetricKey.value,
      window: timeWindow.value,
      ...(selectedPlantNodeId.value
        ? {
            plantNodeId: selectedPlantNodeId.value,
            // 占位符不写入 URL；仅携带真实节点名（展示用途，随深链可还原）
            ...(selectedPlantNodeName.value === '指定节点'
              ? {}
              : { plantNodeName: selectedPlantNodeName.value }),
          }
        : {}),
    },
  });
}

function onControlChange() {
  syncQuery();
  loadAll();
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
          itemStyle: { color: themeColors.value.INFO, borderRadius: [0, 2, 2, 0] },
          data: top10.value.map((it) => ({
            value: it[metricMeta.value.field] as number,
            loopId: it.loopId,
          })),
        },
      ],
    });
    // B 区条形点击 → 回路工作台：echarts 实例级事件（模板 @click 只能收到 DOM
    // 事件拿不到数据项）；主题切换重建实例后随每次渲染重绑，off 防重复
    topChart?.off('click');
    topChart?.on('click', (params) => {
      const data = params.data as undefined | { loopId?: string };
      if (params.componentType === 'series' && data?.loopId) {
        router.push({
          path: '/monitor/loop-workbench',
          query: { loopId: data.loopId },
        });
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
        data: sortedNodes.value.map((r) => r.node.plantNodeName ?? r.node.plantNodeId),
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
        : (labels[
            Math.min(9, Math.max(0, Math.floor(avgValue.value / 10)))
          ] ?? null);
    await renderDistChart({
      grid: { left: 8, right: 16, top: 24, bottom: 8, containLabel: true },
      tooltip: {
        trigger: 'axis',
        axisPointer: { type: 'shadow' },
        formatter: (params) => {
          const p = Array.isArray(params) ? params[0] : params;
          const bucket = distBuckets.value[p?.dataIndex ?? 0];
          if (!bucket) return '';
          const pct = ((bucket.count / curValues.value.length) * 100).toFixed(1);
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
  { title: '装置', key: 'unitName', dataIndex: 'unitName', width: 140, ellipsis: true },
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
          <Select
            v-model:value="selectedMetricKey"
            :options="
              METRIC_OPTIONS.map((m) => ({ label: m.label, value: m.sortKey }))
            "
            style="width: 140px"
            @change="onControlChange"
          />
          <Select
            v-model:value="timeWindow"
            :options="timeWindowOptions"
            style="width: 120px"
            @change="onControlChange"
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
      </Card>

      <ClpmDataCanvas
        class="flex-1 min-h-0"
        :loading="loading"
        :loading-variant="rankingItems.length > 0 ? 'opacity' : 'skeleton'"
        :error="loadError"
        :empty="!loading && !loadError && filteredItems.length === 0"
        empty-reason="当前时间窗内暂无参评回路快照；可先在「评估任务」页发起评估"
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
              :class="avgDelta > 0 ? 'text-emerald-600' : avgDelta < 0 ? 'text-red-600' : 'text-gray-400'"
            >
              <IconifyIcon
                :icon="avgDelta > 0 ? 'lucide:trending-up' : avgDelta < 0 ? 'lucide:trending-down' : 'lucide:minus'"
                class="size-3.5"
              />
              {{ formatDelta(avgDelta) }}
              <span class="font-normal text-gray-400">环比</span>
            </div>
            <div v-else class="mt-0.5 text-xs text-gray-400">—</div>
          </Card>
          <Card :body-style="{ padding: '12px 16px' }">
            <div class="text-xs text-gray-500">
              {{ selectedPlantNodeName }}{{ metricMeta.label }}中位数
            </div>
            <div class="mt-1 text-2xl font-semibold">
              {{ formatNumber(medianValue, metricMeta.max === 100 ? '%' : '') }}
            </div>
            <div
              v-if="medianDelta != null"
              class="mt-0.5 flex items-center gap-1 text-xs font-medium"
              :class="medianDelta > 0 ? 'text-emerald-600' : medianDelta < 0 ? 'text-red-600' : 'text-gray-400'"
            >
              <IconifyIcon
                :icon="medianDelta > 0 ? 'lucide:trending-up' : medianDelta < 0 ? 'lucide:trending-down' : 'lucide:minus'"
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
              {{ fullScoreCount }}<span class="text-sm font-normal text-gray-400">条</span>
            </div>
            <div class="mt-0.5 text-xs text-gray-400">
              {{ fullScorePct != null ? `占比 ${fullScorePct.toFixed(1)}%` : '—' }}
            </div>
          </Card>
          <Card :body-style="{ padding: '12px 16px' }">
            <div class="text-xs text-gray-500">参评回路数</div>
            <div class="mt-1 text-2xl font-semibold">{{ filteredItems.length }}</div>
            <div v-if="countDelta != null" class="mt-0.5 text-xs text-gray-400">
              环比 {{ countDelta > 0 ? '+' : '' }}{{ countDelta }} 条
            </div>
            <div v-else class="mt-0.5 text-xs text-gray-400">—</div>
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
        <div class="mt-3 grid min-h-[280px] flex-1 grid-cols-1 gap-3 lg:grid-cols-12">
          <Card class="lg:col-span-5 h-full" :body-style="{ padding: '12px 16px' }">
            <template #title>
              <span class="text-sm">TOP10 最差回路（{{ metricMeta.label }}）</span>
            </template>
            <template #extra>
              <Tooltip
                v-if="topValuesUniform"
                title="当前窗口内适用回路的该指标全部同值，无薄弱回路可排行；可切换时间窗或关闭适用性过滤查看差异"
              >
                <span class="text-xs text-gray-400">指标无差异</span>
              </Tooltip>
            </template>
            <div class="h-full">
              <EchartsUI
                v-if="top10.length > 0"
                ref="topChartRef"
                height="100%"
              />
              <div v-else class="flex h-full items-center justify-center text-xs text-gray-400">
                暂无排行数据
              </div>
            </div>
          </Card>
          <Card class="lg:col-span-3 h-full" :body-style="{ padding: '12px 16px' }">
            <template #title>
              <span class="text-sm">装置对比</span>
            </template>
            <template #extra>
              <span
                v-if="selectedPlantNodeId"
                class="text-xs text-gray-400"
              >
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
            <div class="h-full">
              <EchartsUI
                v-if="sortedNodes.length > 0"
                ref="unitChartRef"
                height="100%"
              />
              <div v-else class="flex h-full items-center justify-center text-xs text-gray-400">
                {{ unitChartEmptyText }}
              </div>
            </div>
          </Card>
          <!-- E：指标分布（grid 第三列，紧凑图例） -->
          <Card class="lg:col-span-4 h-full" :body-style="{ padding: '12px 16px' }">
            <template #title>
              <span class="text-sm">{{ metricMeta.label }}分布</span>
            </template>
            <template #extra>
              <span class="flex items-center gap-2 text-xs text-gray-400">
                <span class="flex items-center gap-0.5">
                  <span class="inline-block size-2 rounded-sm" :style="{ background: themeColors.DANGER }"></span>
                  ≤60
                </span>
                <span class="flex items-center gap-0.5">
                  <span class="inline-block size-2 rounded-sm" :style="{ background: themeColors.WARNING }"></span>
                  60-80
                </span>
                <span class="flex items-center gap-0.5">
                  <span class="inline-block size-2 rounded-sm" :style="{ background: themeColors.INFO }"></span>
                  ≥80
                </span>
              </span>
            </template>
            <div class="h-full">
              <EchartsUI
                v-if="curValues.length > 0"
                ref="distChartRef"
                height="100%"
              />
              <div v-else class="flex h-full items-center justify-center text-xs text-gray-400">
                暂无分布数据
              </div>
            </div>
          </Card>
        </div>

        <!-- D：行动清单 -->
        <Card class="mt-3 flex-1 min-h-[400px]" :body-style="{ padding: '12px 16px' }">
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
                  <ClpmLoopLink
                    :loop-id="(record as MetricApi.RankingItem).loopId"
                    :tag-name="(record as MetricApi.RankingItem).tagName"
                    default-target="detail"
                  />
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
                  <template v-if="deltaOf(record as MetricApi.RankingItem) != null">
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
                      {{ formatDelta(deltaOf(record as MetricApi.RankingItem)!) }}
                    </span>
                  </template>
                  <span v-else class="text-gray-400">—</span>
                </template>
                <template v-else-if="column.key === 'score'">
                  <span class="font-mono text-xs">
                    {{ formatNumber((record as MetricApi.RankingItem).score) }}
                  </span>
                </template>
                <template v-else-if="column.key === 'confidenceLevel'">
                  <ConfidenceBadge
                    v-if="(record as MetricApi.RankingItem).confidenceLevel"
                    :level="(record as MetricApi.RankingItem).confidenceLevel!"
                    :valid-rate="(record as MetricApi.RankingItem).validRate"
                  />
                  <span v-else class="text-gray-400">—</span>
                </template>
                <template v-else-if="column.key === 'fitness'">
                  <Tooltip
                    :title="
                      fitnessNATip(
                        (record as MetricApi.RankingItem).fitnessLevel ?? null,
                        (record as MetricApi.RankingItem).fitnessTags ?? null,
                      )
                    "
                  >
                    <span class="font-mono text-xs text-slate-500">
                      {{ (record as MetricApi.RankingItem).fitnessLevel ?? '—' }}
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
