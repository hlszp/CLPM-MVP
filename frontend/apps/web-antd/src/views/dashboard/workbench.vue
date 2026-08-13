<script lang="ts" setup>
/**
 * 系统概览 — 04-系统概览标杆页 v3.1
 * 管理者一屏总览 · 联动式（锚点层 R2 常驻 + 联动层 R3/R4 聚焦）
 *
 * 设计规范：docs/设计文档/页面标杆设计/04-系统概览/04-系统概览标杆设计-2026-08-12.md
 */
import type { TimeWindow } from '#/api/metric';
import type { PlantNodeApi } from '#/api/plant-node';
import type { DashboardApi, MetricApi } from '#/api';
import type { MonitorApi } from '#/api/monitor';

import { computed, onMounted, ref, watch } from 'vue';
import { useRouter } from 'vue-router';

import { Page } from '@vben/common-ui';
import { IconifyIcon } from '@vben/icons';
import { Input, Spin } from 'ant-design-vue';

import { getAutoRateRtApi, getBoardAggregateApi, getBoardTrendApi } from '#/api';
import { getAttentionListApi } from '#/api/monitor';
import { getPlantNodeTreeApi } from '#/api/plant-node';
import { getRankingApi } from '#/api/metric';

// ================ 类型 ================
interface ScopeState {
  key: string;
  name: string;
  plantId: string | undefined;
  type: 'AREA' | 'FACTORY' | 'UNIT';
  parentKey: null | string;
}

interface TreeRow {
  node: PlantNodeApi.PlantNode;
  level: number;
  hasChildren: boolean;
  expanded: boolean;
  isVirtualRoot?: boolean;
  parentId: null | string;
}

interface ParetoItem {
  name: string;
  score: number | null;
  steady: number | null;
  fast: number | null;
  acc: number | null;
}

interface DonutCell {
  name: string;
  score: number | null;
  loops: number;
  cur: boolean;
}

// ================ 常量 ================
const TIME_WINDOW_OPTIONS: { label: string; value: TimeWindow; gran: string }[] = [
  { label: '近 8h', value: 'last_8_hours', gran: '按小时' },
  { label: '今日', value: 'today', gran: '按小时' },
  { label: '昨日', value: 'yesterday', gran: '按小时' },
  { label: '近 7 天', value: 'last_7_days', gran: '按日聚合' },
  { label: '近 30 天', value: 'last_30_days', gran: '按日聚合' },
];

const GRADE_COLORS: Record<string, string> = {
  excellent: '#1a7f4b',
  fail: '#a12222',
  fair: '#b45309',
  good: '#2563eb',
  none: '#94a3b8',
  warn: '#c23434',
};

function getGrade(score: null | number): { label: string; color: string; key: string } {
  if (score === null || score === undefined) return { label: '—', color: '#94a3b8', key: 'none' };
  if (score >= 95) return { label: '优秀', color: '#1a7f4b', key: 'excellent' };
  if (score >= 85) return { label: '良好', color: '#2563eb', key: 'good' };
  if (score >= 70) return { label: '合格', color: '#b45309', key: 'fair' };
  if (score >= 60) return { label: '警告', color: '#c23434', key: 'warn' };
  return { label: '不合格', color: '#a12222', key: 'fail' };
}

function fmt(v: null | number, digits = 1): string {
  if (v === null || v === undefined) return '--';
  return v.toFixed(digits);
}

// ================ 状态 ================
const router = useRouter();
const timeWindow = ref<TimeWindow>('today');
const scope = ref<ScopeState>({ key: '__plant__', name: '全厂', plantId: undefined, type: 'FACTORY', parentKey: null });

const plantTree = ref<PlantNodeApi.PlantNode[]>([]);
const expandedKeys = ref<Set<string>>(new Set());
const treeSearch = ref('');
const showHealthPanel = ref(false);

// R2 锚点数据（全厂，仅随 timeWindow）
const anchorAgg = ref<DashboardApi.BoardAggregateResult | null>(null);
const autoRate = ref<DashboardApi.AutoRateRt | null>(null);

// R3/R4 联动数据（随 scope + timeWindow）
const scopeTrend = ref<DashboardApi.BoardTrendResult | null>(null);
const scopeTop10 = ref<MetricApi.RankingItem[]>([]);
const scopeAttention = ref<MonitorApi.AttentionAggregates | null>(null);
const scopeAgg = ref<DashboardApi.BoardAggregateResult | null>(null);

// 树节点评分缓存
const unitScoreMap = ref<Record<string, { loops: number; score: null | number }>>({});
const loadedDevices = ref<Set<string>>(new Set());

const anchorLoading = ref(false);
const scopeLoading = ref(false);

// ================ 数据加载 ================
async function loadPlantTree() {
  try {
    const tree = await getPlantNodeTreeApi();
    plantTree.value = tree;
    // 默认展开 1 级：factory → devices 可见
    for (const node of tree) {
      expandedKeys.value.add(node.id);
    }
  } catch (e) {
    console.error('Failed to load plant tree:', e);
  }
}

async function loadAnchorData() {
  anchorLoading.value = true;
  try {
    const [agg, rt] = await Promise.all([
      getBoardAggregateApi({ timeWindow: timeWindow.value }),
      getAutoRateRtApi(),
    ]);
    anchorAgg.value = agg;
    autoRate.value = rt;
  } catch (e) {
    console.error('Failed to load anchor data:', e);
  } finally {
    anchorLoading.value = false;
  }
}

async function loadScopeData() {
  scopeLoading.value = true;
  try {
    const plantId = scope.value.plantId;
    const tw = timeWindow.value;

    const promises: Promise<unknown>[] = [
      getBoardTrendApi({ plantId, timeWindow: tw }),
      getRankingApi({ plantNodeId: plantId, timeWindow: tw, limit: 10, sortBy: 'score', sortOrder: 'asc' }),
      getAttentionListApi({ plantNodeId: plantId, pageSize: 1 }),
    ];

    // 非 plant 范围需要额外加载 scope 级 aggregate（用于 pareto items + donut 本级）
    if (scope.value.type !== 'FACTORY') {
      promises.push(getBoardAggregateApi({ plantId, timeWindow: tw }));
    }

    const results = await Promise.allSettled(promises);

    scopeTrend.value = results[0]?.status === 'fulfilled' ? (results[0].value as DashboardApi.BoardTrendResult) : null;
    scopeTop10.value = results[1]?.status === 'fulfilled' ? (results[1].value as MetricApi.RankingItem[]) : [];
    scopeAttention.value = results[2]?.status === 'fulfilled' ? (results[2].value as MonitorApi.AttentionListData).aggregates : null;
    if (results[3]?.status === 'fulfilled') {
      scopeAgg.value = results[3].value as DashboardApi.BoardAggregateResult;
    } else if (scope.value.type === 'FACTORY') {
      scopeAgg.value = null; // plant scope 复用 anchorAgg
    }
  } catch (e) {
    console.error('Failed to load scope data:', e);
  } finally {
    scopeLoading.value = false;
  }
}

async function ensureDeviceLoaded(deviceId: string) {
  if (loadedDevices.value.has(deviceId)) return;
  loadedDevices.value.add(deviceId);
  try {
    const res = await getBoardAggregateApi({ plantId: deviceId, timeWindow: timeWindow.value });
    for (const item of res.items) {
      unitScoreMap.value[item.nodeId] = { score: item.avgScore, loops: item.totalLoops };
    }
  } catch {
    loadedDevices.value.delete(deviceId);
  }
}

// ================ watch ================
watch(timeWindow, () => {
  loadAnchorData();
  loadScopeData();
  // 清空 unit 缓存（timeWindow 变了需要重新加载）
  loadedDevices.value.clear();
  unitScoreMap.value = {};
});

watch(scope, () => {
  loadScopeData();
});

onMounted(() => {
  loadPlantTree();
  loadAnchorData();
  loadScopeData();
});

// ================ 树渲染 ================
const deviceScores = computed<Record<string, { loops: number; score: null | number }>>(() => {
  const map: Record<string, { loops: number; score: null | number }> = {};
  if (anchorAgg.value?.items) {
    for (const item of anchorAgg.value.items) {
      map[item.nodeId] = { score: item.avgScore, loops: item.totalLoops };
    }
  }
  return map;
});

const nodeScoreMap = computed(() => ({
  ...deviceScores.value,
  ...unitScoreMap.value,
}));

const factoryScore = computed(() => anchorAgg.value?.aggregate.avgScore ?? null);
const factoryLoops = computed(() => anchorAgg.value?.aggregate.totalLoops ?? 0);

const treeRows = computed<TreeRow[]>(() => {
  const rows: TreeRow[] = [];
  const q = treeSearch.value.trim();
  const match = (s: string) => !q || s.includes(q);

  // 虚拟根节点"全厂"
  rows.push({
    node: { id: '__plant__', name: '全厂', type: 'FACTORY', parentId: null },
    level: 0,
    hasChildren: true,
    expanded: true,
    isVirtualRoot: true,
    parentId: null,
  });

  for (const factory of plantTree.value) {
    if (factory.type !== 'FACTORY' || !factory.children) continue;
    for (const device of factory.children) {
      const deviceMatches = match(device.name);
      const matchingUnits = device.children?.filter((u) => match(u.name)) ?? [];
      if (q && !deviceMatches && matchingUnits.length === 0) continue;

      const deviceExpanded = expandedKeys.value.has(device.id) || (!!q && matchingUnits.length > 0);
      rows.push({
        node: device,
        level: 1,
        hasChildren: (device.children?.length ?? 0) > 0,
        expanded: deviceExpanded,
        parentId: factory.id,
      });

      if (deviceExpanded && device.children) {
        const unitsToShow = q ? matchingUnits : device.children;
        for (const unit of unitsToShow) {
          rows.push({
            node: unit,
            level: 2,
            hasChildren: false,
            expanded: false,
            parentId: device.id,
          });
        }
      }
    }
  }

  return rows;
});

function toggleExpand(nodeId: string) {
  if (expandedKeys.value.has(nodeId)) {
    expandedKeys.value.delete(nodeId);
  } else {
    expandedKeys.value.add(nodeId);
    // 懒加载单元数据
    ensureDeviceLoaded(nodeId);
  }
}

function selectNode(row: TreeRow) {
  if (row.isVirtualRoot) {
    scope.value = { key: '__plant__', name: '全厂', plantId: undefined, type: 'FACTORY', parentKey: null };
    return;
  }
  const node = row.node;
  if (row.level === 1) {
    scope.value = { key: node.id, name: node.name, plantId: node.id, type: 'AREA', parentKey: '__plant__' };
  } else if (row.level === 2) {
    scope.value = { key: node.id, name: node.name, plantId: node.id, type: 'UNIT', parentKey: row.parentId };
  }
}

function expandAll() {
  for (const factory of plantTree.value) {
    expandedKeys.value.add(factory.id);
    if (factory.children) {
      for (const device of factory.children) {
        expandedKeys.value.add(device.id);
        ensureDeviceLoaded(device.id);
      }
    }
  }
}

function collapseAll() {
  expandedKeys.value.clear();
  // 保持 factory 展开（1 级可见）
  for (const factory of plantTree.value) {
    expandedKeys.value.add(factory.id);
  }
}

// ================ 计算属性 ================
const granLabel = computed(() => {
  const opt = TIME_WINDOW_OPTIONS.find((o) => o.value === timeWindow.value);
  return opt?.gran ?? '按小时';
});

const twLabel = computed(() => {
  const opt = TIME_WINDOW_OPTIONS.find((o) => o.value === timeWindow.value);
  return opt?.label ?? '今日';
});

const snapshotTime = computed(() => {
  if (!anchorAgg.value?.windowEnd) return '--:--';
  const d = new Date(anchorAgg.value.windowEnd);
  return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
});

// R2 锚点 KPI
const r2 = computed(() => {
  const agg = anchorAgg.value?.aggregate;
  return {
    avgScore: agg?.avgScore ?? null,
    stabilityRate: agg?.stabilityRate ?? null,
    fastRate: agg?.fastRate ?? null,
    accuracyRate: agg?.accuracyRate ?? null,
    goodValueRate: agg?.goodValueRate ?? null,
    autoModeRate: agg?.autoModeRate ?? null,
    totalLoops: agg?.totalLoops ?? 0,
    evaluatedLoops: agg?.evaluatedLoops ?? 0,
    inconclusiveLoops: agg?.inconclusiveLoops ?? 0,
    rtAutoRate: autoRate.value?.rate ?? null,
  };
});

// R4 使用的 scope aggregate（plant 时复用 anchor）
const scopeAggData = computed(() => {
  if (scope.value.type === 'FACTORY') return anchorAgg.value;
  return scopeAgg.value;
});

// 帕累托数据
const paretoItems = computed<ParetoItem[]>(() => {
  if (scope.value.type === 'UNIT') {
    return scopeTop10.value.map((r) => ({
      name: r.tagName,
      score: r.score,
      steady: r.steadyRate,
      fast: r.fastRate,
      acc: r.accuracyRate,
    }));
  }
  const items = scopeAggData.value?.items ?? [];
  return items.map((item) => ({
    name: item.nodeName ?? '—',
    score: item.avgScore,
    steady: item.stabilityRate,
    fast: item.fastRate,
    acc: item.accuracyRate,
  }));
});

// 扇形图数据
const donutCells = computed<DonutCell[]>(() => {
  const agg = scopeAggData.value;
  if (!agg) return [];
  const curCell: DonutCell = {
    name: `${scope.value.name}（本级）`,
    score: agg.aggregate.avgScore,
    loops: agg.aggregate.totalLoops,
    cur: true,
  };
  if (scope.value.type === 'UNIT') return [curCell];
  const childCells: DonutCell[] = agg.items.map((item) => ({
    name: item.nodeName ?? '—',
    score: item.avgScore,
    loops: item.totalLoops,
    cur: false,
  }));
  return [curCell, ...childCells];
});

// 关注事项 chips
const chips = computed(() => {
  const agg = scopeAttention.value;
  const bySrc = agg?.bySource ?? {};
  return [
    { label: '活跃预警', count: bySrc['ALERT'] ?? bySrc['alert'] ?? 0, source: 'ALERT', hot: true },
    { label: '验证超期', count: agg?.verificationOverdue ?? 0, source: 'VERIFICATION', hot: true },
    { label: '数据质量', count: agg?.dataQualityCount ?? bySrc['DATA_QUALITY'] ?? bySrc['data_quality'] ?? 0, source: 'DATA_QUALITY', hot: false },
  ];
});

// ================ SVG: 趋势图 ================
const trendSvg = computed(() => {
  const trend = scopeTrend.value;
  if (!trend || !trend.timestamps.length) return '';
  const n = trend.timestamps.length;
  const W = 620;
  const H = 300;
  const L = 36;
  const R = 8;
  const T = 10;
  const B = 26;
  const iw = W - L - R;
  const ih = H - T - B;

  const all = [...trend.avgScore, ...trend.stabilityRate, ...trend.autoModeRate].filter(
    (v): v is number => v !== null && v !== undefined,
  );
  if (all.length === 0) return '';
  const yMin = Math.min(50, Math.floor((Math.min(...all) - 6) / 10) * 10);
  const yMax = 100;
  const x = (i: number) => L + (iw * i) / Math.max(1, n - 1);
  const y = (v: number) => T + ih * (1 - (v - yMin) / (yMax - yMin));

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
      const py = y(Math.max(yMin, Math.min(yMax, v))).toFixed(1);
      d += started ? ` L${px},${py}` : `M${px},${py}`;
      started = true;
    }
    return d;
  };

  const dots = (arr: (null | number)[]) => {
    let s = '';
    const interval = n <= 8 ? 1 : Math.ceil(n / 12);
    for (let i = 0; i < n; i++) {
      const v = arr[i];
      if (v === null || v === undefined) continue;
      if (i % interval !== 0 && i !== n - 1) continue;
      s += `<circle cx="${x(i).toFixed(1)}" cy="${y(Math.max(yMin, Math.min(yMax, v))).toFixed(1)}" r="2.4" fill="#1d4ed8"/>`;
    }
    return s;
  };

  // 网格线 + 告警线 60
  let gl = '';
  for (let v = yMin; v <= yMax; v += 10) {
    const is60 = v === 60;
    gl += `<line x1="${L}" y1="${y(v)}" x2="${W - R}" y2="${y(v)}" stroke="${is60 ? '#c23434' : '#eef2f7'}" ${is60 ? 'stroke-dasharray="5,4" stroke-width="1.2"' : 'stroke-width="1"'} />`;
    gl += `<text x="${L - 5}" y="${y(v) + 3}" font-size="9" fill="#94a3b8" text-anchor="end">${v}</text>`;
  }
  gl += `<text x="${W - R - 2}" y="${y(60) - 4}" font-size="9" fill="#c23434" text-anchor="end">告警线 60（阈值·配置）</text>`;

  // X 轴标签
  let xl = '';
  const step = Math.ceil(n / 6);
  for (let i = 0; i < n; i += step) {
    const ts = trend.timestamps[i]!;
    const d = new Date(ts);
    const lab =
      timeWindow.value === 'last_7_days' || timeWindow.value === 'last_30_days'
        ? `${d.getMonth() + 1}/${d.getDate()}`
        : `${d.getHours().toString().padStart(2, '0')}:00`;
    const anchor = i === 0 ? 'start' : i + step >= n ? 'end' : 'middle';
    xl += `<text x="${x(i)}" y="${H - 8}" font-size="9" fill="#94a3b8" text-anchor="${anchor}">${lab}</text>`;
  }

  return `<svg viewBox="0 0 ${W} ${H}" width="100%" height="100%" preserveAspectRatio="none" style="display:block;">
    ${gl}
    <path d="${path(trend.autoModeRate)}" fill="none" stroke="#0d9488" stroke-width="1.4" opacity=".85"/>
    <path d="${path(trend.stabilityRate)}" fill="none" stroke="#2563eb" stroke-width="1.4" opacity=".85"/>
    <path d="${path(trend.avgScore)}" fill="none" stroke="#1d4ed8" stroke-width="2.2"/>
    ${dots(trend.avgScore)}
    ${xl}
  </svg>`;
});

// ================ SVG: 帕累托对比图 ================
const paretoSvg = computed(() => {
  const items = [...paretoItems.value].sort((a, b) => (b.score ?? 0) - (a.score ?? 0));
  const n = items.length;
  if (n === 0) return '';

  const W = 740;
  const H = 200;
  const L = 34;
  const R = 8;
  const T = 18;
  const B = 36;
  const iw = W - L - R;
  const ih = H - T - B;
  const group = iw / n;
  const bw = Math.max(5, Math.min(13, (group - 16) / 3.4));

  let bars = '';
  const pts: [number, number][] = [];

  for (let i = 0; i < items.length; i++) {
    const d = items[i]!;
    const cx = L + group * i + group / 2;
    const metrics: [string, null | number][] = [
      ['steady', d.steady],
      ['fast', d.fast],
      ['acc', d.acc],
    ];
    const colors = ['#2563eb', '#7c3aed', '#1a7f4b'];
    for (let j = 0; j < metrics.length; j++) {
      const [, val] = metrics[j]!;
      const v = Math.max(0, Math.min(100, val ?? 0));
      const bh = (ih * v) / 100;
      const bx = cx - (bw * 1.5 + 2) + j * (bw + 2);
      bars += `<rect x="${bx.toFixed(1)}" y="${(T + ih - bh).toFixed(1)}" width="${bw.toFixed(1)}" height="${bh.toFixed(1)}" rx="1.5" fill="${colors[j]}"/>`;
    }
    pts.push([cx, d.score ?? 0]);

    const short = n > 6 ? d.name.replace(/单元$/, '').replace(/装置$/, '') : d.name;
    bars += `<text x="${cx}" y="${H - 18}" font-size="${n > 6 ? 8 : 10}" fill="#334155" text-anchor="middle">${short}</text>`;
    bars += `<text x="${cx}" y="${H - 6}" font-size="8" fill="#94a3b8" text-anchor="middle">${fmt(d.score, 1)}</text>`;
  }

  let gl = '';
  for (const v of [0, 25, 50, 75, 100]) {
    gl += `<line x1="${L}" y1="${T + ih - (ih * v) / 100}" x2="${W - R}" y2="${T + ih - (ih * v) / 100}" stroke="#eef2f7"/>`;
    gl += `<text x="${L - 5}" y="${T + ih - (ih * v) / 100 + 3}" font-size="9" fill="#94a3b8" text-anchor="end">${v}</text>`;
  }

  const line = pts.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)},${(T + ih - (ih * p[1]) / 100).toFixed(1)}`).join(' ');
  const dots = pts.map((p) => `<circle cx="${p[0].toFixed(1)}" cy="${(T + ih - (ih * p[1]) / 100).toFixed(1)}" r="2.6" fill="#1d4ed8"/>`).join('');

  return `<svg viewBox="0 0 ${W} ${H}" width="100%" height="100%" preserveAspectRatio="none" style="display:block;">
    ${gl}${bars}
    <path d="${line}" fill="none" stroke="#1d4ed8" stroke-width="2"/>${dots}
  </svg>`;
});

// ================ SVG: 微型扇形图（降级：评分单色环） ================
function donutSVG(score: null | number, loops: number): string {
  if (score === null || score === undefined) {
    return `<svg width="64" height="64" viewBox="0 0 64 64">
      <circle cx="32" cy="32" r="24" fill="none" stroke="#e2e8f0" stroke-width="10" stroke-dasharray="3,4"/>
      <text x="32" y="30" font-size="8" fill="#94a3b8" text-anchor="middle">无数据</text>
      <text x="32" y="40" font-size="8" fill="#94a3b8" text-anchor="middle">${loops}回路</text>
    </svg>`;
  }
  const grade = getGrade(score);
  const C = 2 * Math.PI * 24;
  const len = (Math.max(0, Math.min(100, score)) / 100) * C;
  return `<svg width="64" height="64" viewBox="0 0 64 64">
    <circle cx="32" cy="32" r="24" fill="none" stroke="#eef2f7" stroke-width="10"/>
    <circle cx="32" cy="32" r="24" fill="none" stroke="${grade.color}" stroke-width="10"
      stroke-dasharray="${len.toFixed(1)} ${(C - len).toFixed(1)}" transform="rotate(-90 32 32)"/>
    <text x="32" y="30" font-size="11" fill="#0f172a" text-anchor="middle">${score.toFixed(1)}</text>
    <text x="32" y="41" font-size="7" fill="#94a3b8" text-anchor="middle">${loops}回路</text>
  </svg>`;
}

// ================ 最弱指标 ================
function getWeakest(item: MetricApi.RankingItem): string {
  const metrics: [string, number][] = [
    ['平稳率', item.steadyRate],
    ['快速率', item.fastRate],
    ['准确率', item.accuracyRate],
    ['好值率', item.goodValueRate],
  ];
  const valid = metrics.filter(([, v]) => v !== null && v !== undefined);
  if (valid.length === 0) return '—';
  const weakest = valid.reduce((min, m) => (m[1] < min[1] ? m : min), valid[0]!);
  return `${weakest[0]} ${weakest[1].toFixed(1)}%`;
}

// ================ 下钻 ================
function goToLoop(loopId: string) {
  router.push({ path: '/monitor/loop-workbench', query: { loopId, from: 'overview' } });
}

function goToLoopList() {
  router.push({ path: '/monitor/loops', query: { plantNodeId: scope.value.plantId ?? '', from: 'overview' } });
}

function goToAttention(source: string) {
  router.push({ path: '/monitor/attention', query: { source, from: 'overview' } });
}

// ================ 树节点图标 ================
function nodeIcon(type: string): string {
  if (type === 'FACTORY') return 'ant-design:home-outlined';
  if (type === 'AREA') return 'ant-design:appstore-outlined';
  return 'ant-design:database-outlined';
}

function nodeIconColor(type: string): string {
  if (type === 'FACTORY') return '#2563eb';
  if (type === 'AREA') return '#d97706';
  return '#64748b';
}
</script>

<template>
  <Page auto-content-height>
    <Spin :spinning="anchorLoading && !anchorAgg" class="h-full">
      <div class="flex h-full flex-col overflow-hidden">
        <!-- R1 页头 -->
        <div class="flex flex-none items-center gap-3 border-b border-gray-200 bg-gray-50/80 px-4 py-2">
          <span class="text-sm font-bold text-gray-800">系统概览</span>
          <span class="text-[10px] text-gray-400">
            KPI 快照 <span class="font-mono">{{ snapshotTime }}</span> · B 类口径 ·
            <span class="font-mono">{{ twLabel }}</span> ·
            <span>{{ granLabel }}</span>
          </span>
          <!-- 链路指示灯 -->
          <span
            class="flex cursor-pointer items-center gap-1 rounded-full border border-gray-200 bg-white px-2.5 py-1 text-[10px] text-gray-600"
            @click="showHealthPanel = !showHealthPanel"
          >
            <span class="h-2 w-2 rounded-full bg-emerald-500"></span>
            数据链路正常
          </span>
          <!-- 时间窗五档 segmented -->
          <div class="flex overflow-hidden rounded border border-gray-200">
            <button
              v-for="opt in TIME_WINDOW_OPTIONS"
              :key="opt.value"
              class="border-0 bg-white px-2.5 py-1 text-[11px] text-gray-600"
              :class="{ 'bg-blue-700 text-white': timeWindow === opt.value }"
              @click="timeWindow = opt.value"
            >
              {{ opt.label }}
            </button>
          </div>
          <span class="flex-1"></span>
          <span class="cursor-pointer rounded border border-blue-200 px-2 py-0.5 text-[10px] text-blue-700">
            ? 帮助（三率定义 · 五档定级 · 联动逻辑）
          </span>
        </div>

        <!-- 链路健康展开面板 -->
        <div
          v-if="showHealthPanel"
          class="flex flex-none gap-2.5 border-b border-gray-200 bg-red-50/50 px-4 py-2"
        >
          <div class="flex-1 rounded border border-red-200 bg-white px-2.5 py-1.5 text-[11px]">
            <span class="block text-[10px] text-gray-400">实时订阅</span>
            <span class="font-bold text-red-600">已停机 ✕</span>
          </div>
          <div class="flex-1 rounded border border-gray-200 bg-white px-2.5 py-1.5 text-[11px]">
            <span class="block text-[10px] text-gray-400">网络模式</span>
            <span class="font-bold">局域网直连</span>
          </div>
          <div class="flex-1 rounded border border-gray-200 bg-white px-2.5 py-1.5 text-[11px]">
            <span class="block text-[10px] text-gray-400">最近同步</span>
            <span class="font-bold font-mono">{{ snapshotTime }}</span>
          </div>
          <div class="flex-1 rounded border border-gray-200 bg-white px-2.5 py-1.5 text-[11px]">
            <span class="block text-[10px] text-gray-400">Tailscale</span>
            <span class="font-bold text-emerald-600">可用</span>
          </div>
        </div>

        <!-- R2 KPI 统计带（全厂锚点，不随树节点变化） -->
        <div class="flex flex-none items-stretch gap-0 border-b border-gray-200 bg-white px-4 py-2">
          <!-- 综合评分主卡 -->
          <div class="flex items-center gap-2.5 pr-5">
            <div>
              <div class="text-[10px] text-gray-400">全厂综合评分</div>
              <div
                class="font-mono text-3xl font-normal"
                :style="{ color: getGrade(r2.avgScore).color }"
              >
                {{ fmt(r2.avgScore, 2) }}
              </div>
            </div>
            <span
              class="rounded border px-2 py-0.5 text-[11px] font-bold"
              :style="{
                color: getGrade(r2.avgScore).color,
                borderColor: getGrade(r2.avgScore).color + '33',
                background: getGrade(r2.avgScore).color + '11',
              }"
            >
              {{ getGrade(r2.avgScore).label }}
            </span>
          </div>
          <!-- 平稳率 -->
          <div class="flex flex-col justify-center gap-0.5 border-l border-gray-100 px-4">
            <span class="text-[10px] text-gray-400">平稳率</span>
            <span class="font-mono text-lg text-gray-800">{{ fmt(r2.stabilityRate) }}<span class="text-[10px] text-gray-400">%</span></span>
            <div class="h-1 w-24 rounded bg-gray-100">
              <div class="h-1 rounded bg-blue-500" :style="{ width: `${r2.stabilityRate ?? 0}%` }"></div>
            </div>
          </div>
          <!-- 快速率 -->
          <div class="flex flex-col justify-center gap-0.5 border-l border-gray-100 px-4">
            <span class="text-[10px] text-gray-400">快速率</span>
            <span class="font-mono text-lg text-gray-800">{{ fmt(r2.fastRate) }}<span class="text-[10px] text-gray-400">%</span></span>
            <div class="h-1 w-24 rounded bg-gray-100">
              <div class="h-1 rounded bg-violet-600" :style="{ width: `${r2.fastRate ?? 0}%` }"></div>
            </div>
          </div>
          <!-- 准确率 -->
          <div class="flex flex-col justify-center gap-0.5 border-l border-gray-100 px-4">
            <span class="text-[10px] text-gray-400">准确率</span>
            <span class="font-mono text-lg text-gray-800">{{ fmt(r2.accuracyRate) }}<span class="text-[10px] text-gray-400">%</span></span>
            <div class="h-1 w-24 rounded bg-gray-100">
              <div class="h-1 rounded bg-emerald-600" :style="{ width: `${r2.accuracyRate ?? 0}%` }"></div>
            </div>
          </div>
          <!-- 实时自控率 -->
          <div class="flex flex-col justify-center gap-0.5 border-l border-gray-100 px-4">
            <span class="text-[10px] text-gray-400">实时自控率 <span class="text-[9px]">(实时口径)</span></span>
            <span class="font-mono text-lg text-gray-800">{{ fmt(r2.rtAutoRate) }}<span class="text-[10px] text-gray-400">%</span></span>
            <div class="h-1 w-24 rounded bg-gray-100">
              <div class="h-1 rounded bg-teal-600" :style="{ width: `${r2.rtAutoRate ?? 0}%` }"></div>
            </div>
          </div>
          <!-- 好值率 -->
          <div class="flex flex-col justify-center gap-0.5 border-l border-gray-100 px-4">
            <span class="text-[10px] text-gray-400">好值率</span>
            <span class="font-mono text-lg text-gray-800">{{ fmt(r2.goodValueRate) }}<span class="text-[10px] text-gray-400">%</span></span>
            <div class="h-1 w-24 rounded bg-gray-100">
              <div class="h-1 rounded bg-emerald-600" :style="{ width: `${r2.goodValueRate ?? 0}%` }"></div>
            </div>
          </div>
          <!-- 回路统计 -->
          <div class="flex flex-col justify-center gap-0.5 border-l border-gray-100 px-4 text-[10px] text-gray-400">
            <div>回路 <span class="font-mono text-gray-600">{{ r2.totalLoops }}</span> · 参评 <span class="font-mono text-gray-600">{{ r2.evaluatedLoops }}</span></div>
            <div>INCONCLUSIVE <span class="font-mono text-gray-600">{{ r2.inconclusiveLoops }}</span>（数据不足，显式）</div>
          </div>
          <!-- 五档分布迷你条（降级：用 board/aggregate items 统计） -->
          <div class="ml-auto flex flex-col justify-center gap-1">
            <div class="text-[10px] text-gray-400">单元五档分布</div>
            <div class="flex h-2.5 w-44 overflow-hidden rounded-sm border border-gray-100">
              <div
                v-for="item in (anchorAgg?.items ?? [])"
                :key="item.nodeId"
                class="h-full"
                :style="{ width: `${100 / (anchorAgg?.items?.length ?? 1)}%`, background: getGrade(item.avgScore).color }"
                :title="`${item.nodeName}: ${fmt(item.avgScore, 1)}`"
              ></div>
            </div>
            <div class="flex justify-between text-[9px] text-gray-400">
              <span
                v-for="g in ['excellent', 'good', 'fair', 'warn', 'fail']"
                :key="g"
                :style="{ color: GRADE_COLORS[g] }"
              >
                {{ { excellent: '优秀', good: '良好', fair: '合格', warn: '警告', fail: '不合格' }[g] }}
              </span>
            </div>
          </div>
        </div>

        <!-- R3 联动主区 -->
        <div class="flex min-h-0 flex-1 border-b border-gray-200">
          <!-- 左栏：工厂模型树 -->
          <div class="flex w-[22%] min-w-0 flex-col border-r border-gray-200 bg-gray-50/50">
            <div class="flex flex-none items-center gap-1.5 border-b border-gray-100 px-2.5 py-1.5">
              <Input
                v-model:value="treeSearch"
                placeholder="搜索装置 / 单元…"
                size="small"
                class="flex-1"
                allow-clear
              />
              <span class="cursor-pointer rounded border border-blue-200 bg-white px-1.5 py-0.5 text-[9px] text-blue-700" @click="expandAll">展开</span>
              <span class="cursor-pointer rounded border border-blue-200 bg-white px-1.5 py-0.5 text-[9px] text-blue-700" @click="collapseAll">折叠</span>
            </div>
            <div class="flex-1 overflow-auto py-1">
              <div
                v-for="row in treeRows"
                :key="row.node.id"
                class="flex items-center gap-1.5 rounded-sm py-0.5 text-[11px] text-gray-600"
                :class="{
                  'cursor-pointer hover:bg-blue-50': !row.isVirtualRoot || true,
                  'bg-blue-50 font-bold text-gray-800': scope.key === (row.isVirtualRoot ? '__plant__' : row.node.id),
                }"
                :style="{ paddingLeft: `${row.level * 12 + 6}px`, paddingRight: '6px', height: '24px' }"
                @click="selectNode(row)"
              >
                <span
                  v-if="row.hasChildren"
                  class="w-3 flex-none text-center text-[9px] text-gray-400"
                  @click.stop="toggleExpand(row.node.id)"
                >
                  {{ row.expanded ? '▾' : '▸' }}
                </span>
                <span v-else class="w-3 flex-none"></span>
                <IconifyIcon
                  :icon="nodeIcon(row.node.type)"
                  :size="11"
                  class="flex-none"
                  :style="{ color: nodeIconColor(row.node.type) }"
                />
                <span class="flex-1 truncate">{{ row.node.name }}</span>
                <span
                  v-if="row.isVirtualRoot"
                  class="h-1.5 w-1.5 flex-none rounded-full"
                  :style="{ background: getGrade(factoryScore).color }"
                ></span>
                <span
                  v-else-if="nodeScoreMap[row.node.id]"
                  class="h-1.5 w-1.5 flex-none rounded-full"
                  :style="{ background: getGrade(nodeScoreMap[row.node.id]!.score).color }"
                ></span>
                <span v-else class="h-1.5 w-1.5 flex-none rounded-full bg-gray-300"></span>
                <span class="flex-none font-mono text-[9px] text-gray-400">
                  <template v-if="row.isVirtualRoot">
                    <span class="text-gray-600">{{ fmt(factoryScore, 1) }}</span> · {{ factoryLoops }}
                  </template>
                  <template v-else-if="nodeScoreMap[row.node.id]">
                    <span class="text-gray-600">{{ fmt(nodeScoreMap[row.node.id]!.score, 1) }}</span> · {{ nodeScoreMap[row.node.id]!.loops }}
                  </template>
                  <template v-else>无快照</template>
                </span>
              </div>
            </div>
          </div>

          <!-- 中栏：范围趋势 -->
          <div class="flex w-[48%] min-w-0 flex-col border-r border-gray-200 bg-white">
            <div class="flex flex-none items-center gap-2 border-b border-gray-100 bg-gray-50/50 px-3 py-1.5 text-[11px] font-bold text-gray-700">
              当前范围：{{ scope.name }} · {{ twLabel }}
              <span class="ml-auto text-[10px] font-normal text-gray-400">{{ granLabel }} · /board/trend</span>
            </div>
            <div class="flex-1 px-2 py-1">
              <div v-if="trendSvg" v-html="trendSvg" class="h-full w-full"></div>
              <div v-else class="flex h-full items-center justify-center text-xs text-gray-400">暂无趋势数据</div>
            </div>
            <div class="flex flex-none gap-3 px-3 py-1 text-[10px] text-gray-600">
              <span class="flex items-center gap-1"><span class="inline-block h-0.5 w-2.5 rounded bg-blue-700"></span>综合评分</span>
              <span class="flex items-center gap-1"><span class="inline-block h-0.5 w-2.5 rounded bg-blue-500"></span>平稳率</span>
              <span class="flex items-center gap-1"><span class="inline-block h-0.5 w-2.5 rounded bg-teal-600"></span>自控率</span>
              <span class="text-gray-400">快速/准确率趋势待 E-T1</span>
            </div>
          </div>

          <!-- 右栏：重点关注 -->
          <div class="flex min-w-0 flex-1 flex-col bg-gray-50/30">
            <div class="flex flex-none items-center gap-2 border-b border-gray-100 bg-gray-50/50 px-3 py-1.5 text-[11px] font-bold text-gray-700">
              重点关注回路 Top 10
              <span class="ml-auto text-[10px] font-normal text-gray-400">{{ scope.name }} · 评分升序</span>
            </div>
            <div class="flex-1 overflow-auto px-2 py-1.5">
              <div
                v-for="(item, idx) in scopeTop10"
                :key="item.loopId"
                class="mb-1 flex cursor-pointer items-center gap-2 rounded border border-gray-200 bg-white px-2.5 py-1.5 text-[11px] hover:shadow-sm"
                @click="goToLoop(item.loopId)"
              >
                <span class="font-mono font-bold text-gray-400" :class="{ 'text-red-600': idx < 3 }">{{ idx + 1 }}</span>
                <span class="font-mono font-bold text-gray-800">{{ item.tagName }}</span>
                <span
                  v-if="item.score !== null"
                  class="font-mono"
                  :class="{ 'ml-auto': true }"
                >{{ item.score.toFixed(2) }}</span>
                <span v-else class="ml-auto rounded border border-gray-200 px-1.5 text-[9px] text-gray-400">INCONCLUSIVE</span>
                <span
                  v-if="item.score !== null"
                  class="rounded border px-1.5 text-[9px] font-bold"
                  :style="{
                    color: getGrade(item.score).color,
                    borderColor: getGrade(item.score).color + '33',
                    background: getGrade(item.score).color + '11',
                  }"
                >{{ getGrade(item.score).label }}</span>
                <span class="rounded bg-red-50 px-1.5 text-[9px] text-red-600">{{ getWeakest(item) }}</span>
                <span class="rounded border border-blue-200 bg-blue-50 px-1.5 text-[9px] font-bold text-blue-700">工作台 ↗</span>
              </div>
              <div
                class="flex cursor-pointer items-center justify-center border border-dashed border-blue-300 py-1.5 text-[11px] text-blue-700"
                @click="goToLoopList"
              >
                查看全部 →（进 02，按{{ scope.name }}范围筛选）
              </div>
            </div>
            <!-- 关键事项 chips -->
            <div class="flex flex-none items-center gap-1.5 border-t border-gray-100 px-2.5 py-1.5 text-[10px]">
              <span class="text-gray-400">关键事项：</span>
              <span
                v-for="chip in chips"
                :key="chip.source"
                class="cursor-pointer rounded-full border px-2.5 py-0.5"
                :class="chip.hot ? 'border-red-200 text-red-600' : 'border-gray-200 text-gray-600'"
                @click="goToAttention(chip.source)"
              >
                {{ chip.label }} <span class="font-mono">{{ chip.count }}</span>
              </span>
            </div>
          </div>
        </div>

        <!-- R4 装置对比区 -->
        <div class="flex flex-none border-gray-200" style="height: 236px">
          <!-- 左 58%：帕累托对比 -->
          <div class="flex w-[58%] min-w-0 flex-col border-r border-gray-200">
            <div class="flex flex-none items-center gap-2 border-b border-gray-100 bg-gray-50/50 px-3 py-1.5 text-[11px] font-bold text-gray-700">
              <template v-if="scope.type === 'UNIT'">
                综合性能对比 · {{ scope.name }} 评分最低 10 回路
                <span class="ml-auto text-[10px] font-normal text-gray-400">升序 · /performance/ranking</span>
              </template>
              <template v-else>
                综合性能对比 · {{ scope.name }} 下属 {{ paretoItems.length }} {{ scope.type === 'FACTORY' ? '装置' : '单元' }}
                <span class="ml-auto text-[10px] font-normal text-gray-400">评分降序（帕累托）· /board/aggregate</span>
              </template>
            </div>
            <div class="flex-1 px-3 py-1">
              <div v-if="paretoSvg" v-html="paretoSvg" class="h-full w-full"></div>
              <div v-else class="flex h-full items-center justify-center text-xs text-gray-400">暂无对比数据</div>
            </div>
            <div class="flex flex-none gap-3 px-3 py-1 text-[10px] text-gray-600">
              <span class="flex items-center gap-1"><span class="inline-block h-2 w-2 rounded-sm bg-blue-600"></span>平稳率</span>
              <span class="flex items-center gap-1"><span class="inline-block h-2 w-2 rounded-sm bg-violet-600"></span>快速率</span>
              <span class="flex items-center gap-1"><span class="inline-block h-2 w-2 rounded-sm bg-emerald-600"></span>准确率</span>
              <span class="flex items-center gap-1"><span class="inline-block h-0.5 w-3 rounded bg-blue-700"></span>综合评分</span>
            </div>
          </div>

          <!-- 右 42%：微型扇形图 -->
          <div class="flex min-w-0 flex-1 flex-col bg-gray-50/30">
            <div class="flex flex-none items-center gap-2 border-b border-gray-100 bg-gray-50/50 px-3 py-1.5 text-[11px] font-bold text-gray-700">
              回路等级分布
              <span class="ml-auto text-[10px] font-normal text-gray-400">
                {{ scope.type === 'UNIT' ? `仅本级（${scope.name}）` : `${scope.name} + 下一级` }} · 待 E-1 五档计数
              </span>
            </div>
            <div class="flex flex-1 items-center gap-1 overflow-auto px-3 py-2">
              <div
                v-for="cell in donutCells"
                :key="cell.name"
                class="flex min-w-[100px] flex-1 flex-col items-center gap-1"
                :class="{ 'opacity-50': cell.score === null }"
              >
                <div class="font-mono" v-html="donutSVG(cell.score, cell.loops)"></div>
                <span
                  class="max-w-[110px] truncate text-[10px] font-bold"
                  :class="{ 'text-blue-700': cell.cur, 'text-gray-600': !cell.cur }"
                >{{ cell.name }}</span>
                <span class="text-[8px] text-gray-400">
                  <template v-if="cell.score !== null">
                    {{ getGrade(cell.score).label }}
                  </template>
                  <template v-else>未启用 KPI</template>
                </span>
              </div>
              <div class="min-w-[110px] flex-none border-l border-dashed border-gray-200 pl-2 text-[9px] leading-relaxed text-gray-400">
                图例：
                <span :style="{ color: GRADE_COLORS.excellent }">■</span>优秀
                <span :style="{ color: GRADE_COLORS.good }">■</span>良好
                <span :style="{ color: GRADE_COLORS.fair }">■</span>合格
                <span :style="{ color: GRADE_COLORS.warn }">■</span>警告
                <span :style="{ color: GRADE_COLORS.fail }">■</span>不合格<br>
                降级：评分单色环<br>
                源：02 E-1 范围聚合（待扩展）
              </div>
            </div>
          </div>
        </div>
      </div>
    </Spin>
  </Page>
</template>

<style scoped>
:deep(.ant-input) {
  font-size: 10px;
}
</style>
