<script lang="ts" setup>
import type { LoopCardModel } from './loops-shared';

/**
 * 驾驶舱 · 页2 回路状态墙（方案 11 §6，C4；P5-04 服务端筛选改造）
 *
 * 三栏壳（1920×1080 满屏无页面滚动，区块内滚动）：
 * - 左：装置树（工厂→装置→单元，215px，GET /cockpit/node-tree，计数角标）
 * - 中：等级/模式筛选 + 排序 + 右上角分页控件 + 回路卡片墙（每页 30 个，WS 实时刷新）
 * - 右：详情面板（~368px，两态：聚合[模式柱状图固定+节点雷达弹性] / 回路详情）
 *
 * P5-04（03 号文 §8）：
 * - 筛选/稳定排序/总数/分页全部服务端执行（use-loop-wall-list）——消除
 *   "筛选/劣化排序触发客户端预抓全量 34 页"反模式；筛选变化只发 1 个列表
 *   请求（选中回路跨页定位 +1），总数含 >1000 场景准确；
 * - 竞态：use-latest-request 守卫（装置 A→B 快速切换，迟到的 A 被丢弃）；
 * - focus 协议：筛选/排序/装置变化后有选中回路 → 服务端返回 focusPage 跳到
 *   其所在页；不在结果集 → 自动回聚合视图；
 * - 实时节流：tagName 索引 + 500ms 短时合并（同位号窗口内只保留最新值）+
 *   document.hidden 停更（可见时冲刷）+ 订阅收缩到当前页位号；
 * - 刷新保状态：5min/手动静默刷新按当前页重拉，不清页码/不重置筛选/不关
 *   选中；浏览器刷新（F5）经 URL query 恢复节点/筛选/页码/选中回路。
 *
 * 数据源：GET /loops/monitor（服务端筛选排序分页+评分+六维+实时值）+
 * /performance/loops/metric-series（火花线）+ WebSocket /api/v1/ws/realtime
 * （PV/SP/OP/MODE 短窗合并后局部刷新，断连 30s 轮询降级）。
 */
import type { CockpitApi } from '#/api/cockpit';
import type { LoopApi } from '#/api/loop';
import type { MetricApi } from '#/api/metric';
import type { RealtimeMessage } from '#/composables/use-loop-realtime';
import type { CockpitModeKey } from '#/store/cockpit';

import { computed, onMounted, onUnmounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import { getCockpitNodeTreeApi } from '#/api/cockpit';
import { getGradingThresholdsApi, getLoopMetricSeriesApi } from '#/api/metric';
import {
  parseTagCode,
  registerInterest,
  resolveModeLabel,
  useLoopRealtime,
} from '#/composables/use-loop-realtime';
import { useCockpitStore } from '#/store/cockpit';
import { parseFiniteNumber } from '#/utils/numeric';
import { mapQualityToLabel } from '#/utils/quality-code';

import CockpitHeader from './components/cockpit-header.vue';
import DeviceTree from './components/device-tree.vue';
import LoopCard from './components/loop-card.vue';
import LoopDetailPanel from './components/loop-detail-panel.vue';
import LoopPager from './components/loop-pager.vue';
import { createRealtimeMergeBuffer } from './composables/create-realtime-merge-buffer';
import { LOOP_WALL_PAGE_SIZE, useLoopWallList } from './composables/use-loop-wall-list';
import { MODE_KEY_ORDER, MODE_KEY_ZH, toLoopCardModel } from './loops-shared';

import './styles/theme.css';

/** 2026-10-06 用户裁决：6 列 × 5 行（原 4 列 × 5 行） */
const PAGE_SIZE = LOOP_WALL_PAGE_SIZE;
/**
 * P5-04 短时合并窗口（决策点定稿，登记交接）：500ms——WS 1s 采样节拍下，
 * 同位号窗口内仅保留最新值、每至多 2 次响应式更新；感知延迟低于人眼对
 * 数字闪烁的辨识阈值，30 卡 × 4 位号高频推送的重渲染合并为窗口批量。
 */
const REALTIME_MERGE_WINDOW_MS = 500;

const cockpitStore = useCockpitStore();
const route = useRoute();
const router = useRouter();
const theme = computed(() => cockpitStore.theme);

// ---------------------------------------------------------------------------
// URL query 保状态（P5-04：浏览器刷新不关面板/不重置筛选）
// node/grades/modes/sort/page/loop 六键往返同步（router.replace 不留历史）
// ---------------------------------------------------------------------------
const QUERY_GRADE_KEYS = [
  'EXCELLENT',
  'FAIR',
  'GOOD',
  'POOR',
  'WARNING',
] as const;

function restoreFromQuery() {
  const q = route.query;
  if (typeof q.node === 'string' && q.node) cockpitStore.setLoopsNode(q.node);
  if (typeof q.grades === 'string') {
    const gs = q.grades
      .split(',')
      .filter((g): g is CockpitApi.GradeKey =>
        (QUERY_GRADE_KEYS as readonly string[]).includes(g),
      );
    if (gs.length > 0) cockpitStore.setLoopsFilters({ grades: gs });
  }
  if (typeof q.modes === 'string') {
    const ms = q.modes
      .split(',')
      .filter((m): m is CockpitModeKey =>
        (MODE_KEY_ORDER as readonly string[]).includes(m),
      );
    if (ms.length > 0) cockpitStore.setLoopsFilters({ modes: ms });
  }
  if (q.sort === 'degradeDesc' || q.sort === 'scoreDesc') {
    cockpitStore.setLoopsFilters({ sortBy: q.sort });
  }
  if (typeof q.page === 'string') {
    const p = Number.parseInt(q.page, 10);
    if (Number.isFinite(p) && p >= 1) cockpitStore.setLoopsPage(p);
  }
  if (typeof q.loop === 'string' && q.loop) cockpitStore.selectLoop(q.loop);
}

function syncQueryToUrl() {
  const f = cockpitStore.loopsFilters;
  const query: Record<string, string> = {};
  if (cockpitStore.loopsNodeId) query.node = cockpitStore.loopsNodeId;
  if (f.grades.length > 0) query.grades = f.grades.join(',');
  if (f.modes.length > 0) query.modes = f.modes.join(',');
  if (f.sortBy !== 'scoreDesc') query.sort = f.sortBy;
  if (cockpitStore.loopsPage > 1) query.page = String(cockpitStore.loopsPage);
  if (cockpitStore.loopsSelectedLoopId) query.loop = cockpitStore.loopsSelectedLoopId;
  void router.replace({ query });
}

// 启动即恢复（先于下方 watcher 注册，恢复本身不触发加载）
restoreFromQuery();

// ---------------------------------------------------------------------------
// 左：装置树
// ---------------------------------------------------------------------------
const treeNodes = ref<CockpitApi.NodeTreeNode[]>([]);
const treeLoading = ref(true);

/** 节点 ID → 名称（面板口径标题用） */
const nodeNameMap = computed(() => {
  const map = new Map<string, string>();
  const walk = (list: CockpitApi.NodeTreeNode[]) => {
    for (const n of list) {
      map.set(n.nodeId, n.name);
      if (n.children?.length) walk(n.children);
    }
  };
  walk(treeNodes.value);
  return map;
});

const selectedNodeName = computed(() =>
  cockpitStore.loopsNodeId
    ? (nodeNameMap.value.get(cockpitStore.loopsNodeId) ?? '全厂')
    : '全厂',
);

function onSelectNode(node: CockpitApi.NodeTreeNode) {
  cockpitStore.setLoopsNode(node.nodeId);
}

/** 静默重拉装置树（loopCount 角标随 5min 定时器刷新；失败保留旧树） */
async function refreshTree() {
  try {
    const tree = await getCockpitNodeTreeApi();
    treeNodes.value = tree ?? [];
  } catch {
    // 静默失败：保留旧树
  }
}

// ---------------------------------------------------------------------------
// 中：回路列表（P5-04：服务端筛选/排序/总数/分页 + 竞态守卫）
// ---------------------------------------------------------------------------
const {
  booted,
  busy,
  focusPage,
  items,
  load,
  modeDistribution,
  total,
} = useLoopWallList();

/** 定级阈值（等级五档色染；未加载时 loops-shared 降级国标默认） */
const gradingThresholds = ref<MetricApi.GradingThresholdItem[]>([]);

/** 当前查询态（store → 服务端请求参数的单一映射） */
function currentQuery() {
  const f = cockpitStore.loopsFilters;
  return {
    grades: f.grades,
    modes: f.modes,
    page: cockpitStore.loopsPage,
    plantNodeId: cockpitStore.loopsNodeId,
    sortBy: f.sortBy,
  };
}

/** 选中回路的 focus 校正（响应后调用）：不在结果集→清选中；跨页→跳所在页 */
async function reconcileFocus() {
  const sel = cockpitStore.loopsSelectedLoopId;
  if (!sel) return;
  if (focusPage.value == null) {
    // 选中回路被过滤掉 → 自动回聚合视图（方案 §6.3）
    cockpitStore.selectLoop(null);
    if (cockpitStore.loopsPage !== 1) await goToPage(1);
  } else if (focusPage.value !== cockpitStore.loopsPage) {
    await goToPage(focusPage.value);
  }
}

/** 筛选/排序/装置变化（方案 §6.3：有选中→跳其所在页；否则回第 1 页） */
async function handleQueryChange() {
  const sel = cockpitStore.loopsSelectedLoopId;
  if (cockpitStore.loopsPage !== 1) cockpitStore.setLoopsPage(1);
  await load({ ...currentQuery(), page: 1 }, { focusLoopId: sel });
  await reconcileFocus();
}

/** 翻页（分页控件 / focus 跳页 / ‹› 跨页共用；页码先行、加载后消费浏览目标） */
let pendingBrowseGlobal: null | number = null;
async function goToPage(page: number) {
  cockpitStore.setLoopsPage(page);
  await load(currentQuery());
  consumePendingBrowse();
}

function onPageChange(page: number) {
  void goToPage(page);
}

// ---------------------------------------------------------------------------
// 视图模型（服务端已按当前页+排序返回，无客户端筛选/排序）
// ---------------------------------------------------------------------------
const models = computed<LoopCardModel[]>(() =>
  items.value.map((item) => toLoopCardModel(item, gradingThresholds.value)),
);
const pageItems = computed<LoopCardModel[]>(() => models.value);

const pageCount = computed(() =>
  Math.max(1, Math.ceil(total.value / PAGE_SIZE)),
);

const selectedIndexInPage = computed(() =>
  pageItems.value.findIndex((m) => m.loopId === cockpitStore.loopsSelectedLoopId),
);

/** 全局位次（服务端排序下的第 N 个，1 起；不在当前页为 -1） */
const selectedIndex = computed(() =>
  selectedIndexInPage.value >= 0
    ? (cockpitStore.loopsPage - 1) * PAGE_SIZE + selectedIndexInPage.value
    : -1,
);

// 详情面板 ‹ › 连续浏览：跨页自动翻页并保持卡片选中
function browseLoop(delta: number) {
  if (selectedIndexInPage.value < 0) return;
  const globalIdx = selectedIndex.value;
  const next = globalIdx + delta;
  if (next < 0 || next >= total.value) return;
  const nextPage = Math.floor(next / PAGE_SIZE) + 1;
  if (nextPage === cockpitStore.loopsPage) {
    const target = pageItems.value[next - (nextPage - 1) * PAGE_SIZE];
    if (target) cockpitStore.selectLoop(target.loopId);
  } else {
    pendingBrowseGlobal = next;
    void goToPage(nextPage);
  }
}

function consumePendingBrowse() {
  if (pendingBrowseGlobal == null) return;
  const g = pendingBrowseGlobal;
  pendingBrowseGlobal = null;
  const idx = g - (cockpitStore.loopsPage - 1) * PAGE_SIZE;
  const target = pageItems.value[idx];
  if (target) cockpitStore.selectLoop(target.loopId);
}

function onCardSelect(loopId: string) {
  cockpitStore.selectLoop(loopId);
}

// ---------------------------------------------------------------------------
// 火花线（近 24h 综合评分，metric-series 每批 ≤10 回路）
// ---------------------------------------------------------------------------
const sparkMap = ref<Map<string, { ts: null | string; value: null | number }[]>>(
  new Map(),
);

let sparkSeq = 0;

async function loadSparks(itemsOnPage: LoopCardModel[]) {
  if (itemsOnPage.length === 0) {
    sparkMap.value = new Map();
    return;
  }
  // 竞态守卫：快速翻页时旧批次响应不得覆盖新批次（并行化后仍有晚到风险）
  const seq = ++sparkSeq;
  const end = new Date();
  const start = new Date(end.getTime() - 24 * 60 * 60 * 1000);
  const map = new Map<string, { ts: null | string; value: null | number }[]>();
  const ids = itemsOnPage.map((m) => m.loopId);
  // 2026-10-03 提速：chunk 间全并行（首屏 30 卡=3 批并发）
  const chunks: string[][] = [];
  for (let i = 0; i < ids.length; i += 10) {
    chunks.push(ids.slice(i, i + 10));
  }
  const results = await Promise.all(
    chunks.map((chunk) =>
      getLoopMetricSeriesApi({
        loopIds: chunk.join(','),
        metricKey: 'score',
        startTime: start.toISOString(),
        endTime: end.toISOString(),
      }).catch(
        () => null, // 火花线失败不阻断卡片墙，占位 "—"
      ),
    ),
  );
  if (seq !== sparkSeq) return;
  for (const res of results) {
    for (const s of res?.series ?? []) {
      if (s.loopId) map.set(s.loopId, s.points ?? []);
    }
  }
  sparkMap.value = map;
}

watch(
  () => pageItems.value.map((m) => m.loopId).join(','),
  () => void loadSparks(pageItems.value),
  { immediate: true },
);

// ---------------------------------------------------------------------------
// WebSocket 实时刷新（P5-04 节流：tagName 索引 + 短时合并 + 后台停更）
// ---------------------------------------------------------------------------
const realtime = useLoopRealtime();

/** tagName → 列表项索引（O(1) 消息定位，替代每消息线性扫描） */
const itemByTag = computed(() => {
  const map = new Map<string, LoopApi.MonitorListItem>();
  for (const l of items.value) map.set(l.tagName, l);
  return map;
});

function applyRealtimeMessage(msg: RealtimeMessage) {
  const parsed = parseTagCode(msg.tagCode);
  if (!parsed) return;
  const item = itemByTag.value.get(parsed.tagName);
  if (!item) return;
  const cv = item.currentValues;
  // R06：共享数值契约——无效字面量（-1.#QNAN0/nan/Infinity/空串）→ null
  const numValue = parseFiniteNumber(msg.value);
  switch (parsed.role) {
    case 'MODE': {
      // R17：按「回路 modeMapping（REST 下发）→ 默认映射 → Unknown」解析，
      // 未知值显式 Unknown，不保留旧标签冒充
      const label = resolveModeLabel(numValue, item.modeMapping);
      cv.mode = numValue;
      cv.modeLabel = label;
      item.controlMode = label as LoopApi.ControlMode;
      break;
    }
    case 'OP': {
      cv.op = numValue;
      break;
    }
    case 'PV': {
      cv.pv = numValue;
      cv.pvQuality = mapQualityToLabel(msg.quality) as LoopApi.Quality;
      break;
    }
    case 'SP': {
      cv.sp = numValue;
      break;
    }
    default: {
      return;
    }
  }
  cv.readAt = msg.collectTime;
}

/** 短时合并缓冲（窗口内同位号只保留最新值；隐藏中不应用、可见时冲刷） */
const realtimeBuffer = createRealtimeMergeBuffer<RealtimeMessage>(
  (messages) => {
    for (const msg of messages) applyRealtimeMessage(msg);
  },
  { windowMs: REALTIME_MERGE_WINDOW_MS },
);

const handleRealtimeMessage = (msg: RealtimeMessage) => realtimeBuffer.push(msg);

// 服务端订阅过滤（P5-04 收缩：仅当前页可见卡片绑定的位号）。
// O02-RT：owner=本组件实例唯一身份（Symbol 每实例一份）；可见卡片/翻页/筛选
// 变化由 registerInterest 内部 watch 自动重订（对 WS 发送全部 owner 并集，
// 驾驶舱内其他实例互不覆盖）；KeepAlive 停用→释放、恢复→重算 source 重注册；
// 组件卸载→自动停止 watch 并释放。
const realtimeInterestOwner = Symbol('cockpit-loops-realtime');
registerInterest(realtimeInterestOwner, () => ({
  loops: items.value.map((l) => l.tagName),
}));

// ---------------------------------------------------------------------------
// 详情面板（两态 + ‹ › 连续浏览）
// ---------------------------------------------------------------------------
const selectedModel = computed<LoopCardModel | null>(() =>
  selectedIndexInPage.value >= 0
    ? (pageItems.value[selectedIndexInPage.value] ?? null)
    : null,
);

const panelView = computed<'aggregate' | 'loop'>(() =>
  selectedModel.value ? 'loop' : 'aggregate',
);

// ---------------------------------------------------------------------------
// C5 混合刷新（方案 §9）：当前页静默重拉 + 火花线 + 装置树角标 5min 定时 +
// 顶栏暂停开关 + 手动刷新（store.refreshTick）；P5-04：后台页跳过、
// 恢复可见补拉一次；刷新保留页码/筛选/选中（不关面板）
// ---------------------------------------------------------------------------
const AUTO_REFRESH_MS = 5 * 60_000;
let pendingRefreshWhileHidden = false;

/** 当前页静默重拉（选中回路漂移跨页时跟随跳页保持选中） */
async function refreshCurrentPage() {
  const sel = cockpitStore.loopsSelectedLoopId;
  await load(currentQuery(), { silent: true, focusLoopId: sel });
  await reconcileFocus();
}

async function refreshStatic() {
  await Promise.all([refreshCurrentPage(), refreshTree()]);
  void loadSparks(pageItems.value);
}

let autoTimer: null | ReturnType<typeof setInterval> = null;

function onVisibilityChange() {
  if (document.visibilityState !== 'visible') return;
  // 恢复可见：冲刷缓冲的实时值 + 补拉隐藏期间跳过的刷新
  realtimeBuffer.flush();
  if (pendingRefreshWhileHidden) {
    pendingRefreshWhileHidden = false;
    void refreshStatic();
  }
}

// ---------------------------------------------------------------------------
// 初始化
// ---------------------------------------------------------------------------
onMounted(async () => {
  // 定级阈值（等级色染）与装置树互不依赖 → 并行拉取（2026-10-03 提速）
  let nodeAutoSelected = false;
  const [thrRes, treeRes] = await Promise.allSettled([
    getGradingThresholdsApi(),
    getCockpitNodeTreeApi(),
  ]);
  gradingThresholds.value =
    thrRes.status === 'fulfilled' ? (thrRes.value?.thresholds ?? []) : [];
  if (treeRes.status === 'fulfilled') {
    const tree = treeRes.value;
    treeNodes.value = tree ?? [];
    if (!cockpitStore.loopsNodeId && tree?.length) {
      const root = tree[0];
      if (root) {
        cockpitStore.setLoopsNode(root.nodeId);
        nodeAutoSelected = true;
      }
    }
  } else {
    treeNodes.value = [];
  }
  treeLoading.value = false;

  // 未自动选根（树为空或 URL 恢复了节点）时由本处直接加载，
  // 避免与节点 watch 双发；URL 恢复的选中回路经 focus 校正定位/清理
  if (!nodeAutoSelected) {
    await load(currentQuery(), {
      focusLoopId: cockpitStore.loopsSelectedLoopId,
    });
    await reconcileFocus();
  }

  // WS 实时通道
  realtime.start();
  realtime.onMessage(handleRealtimeMessage);

  // 5min 自动刷新：暂停/后台页跳过（后台页停止高频更新），可见后补拉
  autoTimer = setInterval(() => {
    if (cockpitStore.autoRefreshPaused) return;
    if (document.hidden) {
      pendingRefreshWhileHidden = true;
      return;
    }
    void refreshStatic();
  }, AUTO_REFRESH_MS);
  document.addEventListener('visibilitychange', onVisibilityChange);
});

// 装置节点切换：有选中→focus 跳其所在页；否则回第 1 页
watch(
  () => cockpitStore.loopsNodeId,
  () => void handleQueryChange(),
);

// 筛选/排序变化：先回第 1 页探测 focus，再按返回页码跳转
watch(
  () => cockpitStore.loopsFilters,
  () => void handleQueryChange(),
  { deep: true },
);

// 恢复自动刷新（暂停→恢复）时立即补拉一次
watch(
  () => cockpitStore.autoRefreshPaused,
  (paused, prev) => {
    if (prev && !paused) void refreshStatic();
  },
);

// 顶栏手动刷新（store.refreshTick ++）→ 当前页重拉（保状态）
watch(
  () => cockpitStore.refreshTick,
  () => void refreshStatic(),
);

// 状态变化同步 URL（F5 恢复依据；restore 在 setup 早期已完成，无回环）
watch(
  () =>
    [
      cockpitStore.loopsNodeId,
      cockpitStore.loopsPage,
      cockpitStore.loopsFilters.grades.join(','),
      cockpitStore.loopsFilters.modes.join(','),
      cockpitStore.loopsFilters.sortBy,
      cockpitStore.loopsSelectedLoopId,
    ].join('|'),
  () => syncQueryToUrl(),
);

// WS 断连降级轮询：静默重拉当前页（保状态）
watch(
  () => realtime.connectionStatus.value,
  (status, prev) => {
    if (status === 'offline') {
      realtime.startFallback(() => refreshCurrentPage());
    } else if (status === 'online' && prev !== 'online') {
      realtime.stopFallback();
      void refreshCurrentPage();
    }
  },
);

onUnmounted(() => {
  if (autoTimer) {
    clearInterval(autoTimer);
    autoTimer = null;
  }
  realtimeBuffer.dispose();
  document.removeEventListener('visibilitychange', onVisibilityChange);
});

// ---------------------------------------------------------------------------
// 工具条（等级五档多选 + 模式多选 + 排序）
// ---------------------------------------------------------------------------
const GRADE_CHIPS: { colorVar: string; key: CockpitApi.GradeKey; label: string; }[] =
  [
    { key: 'EXCELLENT', label: '优秀', colorVar: '--ck-grade-excellent' },
    { key: 'GOOD', label: '良好', colorVar: '--ck-grade-good' },
    { key: 'FAIR', label: '合格', colorVar: '--ck-grade-fair' },
    { key: 'WARNING', label: '警告', colorVar: '--ck-grade-warning' },
    { key: 'POOR', label: '不合格', colorVar: '--ck-grade-poor' },
  ];

function toggleGrade(key: CockpitApi.GradeKey) {
  const cur = cockpitStore.loopsFilters.grades;
  cockpitStore.setLoopsFilters({
    grades: cur.includes(key) ? cur.filter((g) => g !== key) : [...cur, key],
  });
}

function toggleMode(key: CockpitModeKey) {
  const cur = cockpitStore.loopsFilters.modes;
  cockpitStore.setLoopsFilters({
    modes: cur.includes(key) ? cur.filter((m) => m !== key) : [...cur, key],
  });
}

function setSort(sortBy: 'degradeDesc' | 'scoreDesc') {
  cockpitStore.setLoopsFilters({ sortBy });
}
</script>

<template>
  <div class="cockpit-root cockpit-loops" :data-theme="theme">
    <CockpitHeader />

    <div class="loops-body">
      <!-- 左：装置树区 -->
      <aside class="cockpit-panel loops-tree">
        <DeviceTree
          :nodes="treeNodes"
          :selected-id="cockpitStore.loopsNodeId"
          :loading="treeLoading"
          @select="onSelectNode"
        />
      </aside>

      <!-- 中：筛选排序 + 分页控件 + 回路卡片墙 -->
      <main class="loops-wall">
        <div class="cockpit-panel loops-wall__toolbar">
          <div class="loops-wall__filters">
            <span class="chip-group">
              <span
                v-for="chip in GRADE_CHIPS"
                :key="chip.key"
                class="chip"
                :class="{
                  active: cockpitStore.loopsFilters.grades.includes(chip.key),
                }"
                :style="{ color: `var(${chip.colorVar})` }"
                @click="toggleGrade(chip.key)"
              >
                {{ chip.label }}
              </span>
            </span>
            <span class="chip-group">
              <span
                v-for="key in MODE_KEY_ORDER"
                :key="key"
                class="chip"
                :class="{
                  active: cockpitStore.loopsFilters.modes.includes(key),
                }"
                @click="toggleMode(key)"
              >
                {{ MODE_KEY_ZH[key] }}
              </span>
            </span>
            <span class="chip-group">
              <span
                class="chip"
                :class="{
                  active: cockpitStore.loopsFilters.sortBy === 'scoreDesc',
                }"
                @click="setSort('scoreDesc')"
              >
                评分降序
              </span>
              <span
                class="chip"
                :class="{
                  active: cockpitStore.loopsFilters.sortBy === 'degradeDesc',
                }"
                @click="setSort('degradeDesc')"
              >
                劣化降序
              </span>
            </span>
          </div>
          <LoopPager
            :page="cockpitStore.loopsPage"
            :page-count="pageCount"
            :total="total"
            @change="onPageChange"
          />
        </div>

        <div class="cockpit-panel loops-wall__cards">
          <div v-if="!booted" class="loops-wall__hint">回路加载中…</div>
          <div v-else-if="pageItems.length === 0" class="loops-wall__hint">
            {{ busy ? '加载中…' : '当前条件下无回路' }}
          </div>
          <template v-else>
            <div v-if="busy" class="loops-wall__updating">更新中…</div>
            <div class="loops-wall__grid">
              <LoopCard
                v-for="m in pageItems"
                :key="m.loopId"
                :loop="m"
                :selected="cockpitStore.loopsSelectedLoopId === m.loopId"
                :spark="sparkMap.get(m.loopId) ?? []"
                @select="onCardSelect"
              />
            </div>
          </template>
        </div>
      </main>

      <!-- 右：详情面板区（两态：聚合 / 回路详情） -->
      <aside class="cockpit-panel loops-detail">
        <LoopDetailPanel
          :view="panelView"
          :node-id="cockpitStore.loopsNodeId"
          :node-name="selectedNodeName"
          :node-loop-count="total"
          :mode-distribution="modeDistribution"
          :loop="selectedModel"
          :loop-index="selectedIndex + 1"
          :loop-total="total"
          :grading-thresholds="gradingThresholds"
          @prev="browseLoop(-1)"
          @next="browseLoop(1)"
          @close="cockpitStore.selectLoop(null)"
        />
      </aside>
    </div>
  </div>
</template>

<style scoped>
.cockpit-loops {
  padding-bottom: 12px;
}

.loops-body {
  display: flex;
  flex: 1;
  gap: 12px;
  min-height: 0;
  padding: 12px 12px 0;
}

.loops-tree {
  flex: none;
  width: 215px;
}

.loops-wall {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 12px;
  min-width: 0;
  min-height: 0;
}

/* 中部工具条：筛选排序 + 右上角分页控件 */
.loops-wall__toolbar {
  display: flex;
  flex: none;
  flex-direction: row;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
  height: 44px;
  padding: 0 14px;
  font-size: 12px;
  color: var(--ck-text-2);
}

.loops-wall__filters {
  display: flex;
  gap: 14px;
  align-items: center;
  min-width: 0;
}

.chip-group {
  display: flex;
  flex: none;
  gap: 4px;
  align-items: center;
  padding: 2px;
  background: var(--ck-panel-2);
  border: 1px solid var(--ck-border);
  border-radius: 7px;
}

.chip {
  padding: 3px 9px;
  font-size: 11px;
  color: var(--ck-text-2);
  cursor: pointer;
  user-select: none;
  border-radius: 5px;
}

.chip:hover {
  color: var(--ck-text);
}

.chip.active {
  font-weight: 600;
  color: var(--ck-text);
  background: var(--ck-panel-3);
  box-shadow: inset 0 -2px 0 var(--ck-accent);
}

.loops-wall__cards {
  position: relative;
  flex: 1;
  min-height: 0;
}

.loops-wall__grid {
  display: grid;
  /* 2026-10-07 用户裁决：6 列 × 5 行固定排布，行高弹性均分撑满主显示区
     （原 118px 固定行高在剩余空间大时底部留白） */
  grid-template-columns: repeat(6, 1fr);
  grid-template-rows: repeat(5, minmax(0, 1fr));
  gap: 10px;
  height: 100%;
  padding: 12px;
  overflow: hidden;
}

.loops-wall__hint {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  font-size: 12px;
  color: var(--ck-text-3);
}

/* P5-04：翻页/筛选请求在途的轻提示（旧卡片保留，不闪空墙） */
.loops-wall__updating {
  position: absolute;
  top: 6px;
  right: 14px;
  z-index: 2;
  font-size: 11px;
  color: var(--ck-text-3);
  pointer-events: none;
}

.loops-detail {
  flex: none;
  width: 368px;
}
</style>
