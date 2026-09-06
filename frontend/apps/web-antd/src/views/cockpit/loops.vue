<script lang="ts" setup>
import type { LoopCardModel } from './loops-shared';

/**
 * 驾驶舱 · 页2 回路状态墙（方案 11 §6，C4）
 *
 * 三栏壳（1920×1080 满屏无页面滚动，区块内滚动）：
 * - 左：装置树（工厂→装置→单元，215px，GET /cockpit/node-tree，计数角标）
 * - 中：等级/模式筛选 + 排序 + 右上角分页控件 + 回路卡片墙（每页 20 个，WS 实时刷新）
 * - 右：详情面板（~368px，两态：聚合[模式柱状图固定+节点雷达弹性] / 回路详情）
 *
 * 交互规则（§6.3）：
 * - 筛选/排序/装置变化后：有选中回路→跳到其所在页；否则回第 1 页；
 *   选中回路被过滤掉→自动回聚合视图
 * - 详情面板 ‹ › 在当前筛选结果内连续浏览，跨页自动翻页并保持卡片选中
 * - 纯只读：无任何写操作/后台跳转
 *
 * 数据源：GET /loops/monitor（列表+评分+六维+实时值一次取齐，按节点拉全量后
 * 客户端筛选/排序/分页）+ /performance/loops/metric-series（火花线）+
 * WebSocket /api/v1/ws/realtime（PV/SP/OP/MODE 局部刷新，断连 30s 轮询降级）。
 */
import type { CockpitApi } from '#/api/cockpit';
import type { LoopApi } from '#/api/loop';
import type { MetricApi } from '#/api/metric';
import type { CockpitModeKey } from '#/store/cockpit';

import { computed, onMounted, onUnmounted, ref, watch } from 'vue';

import { getCockpitNodeTreeApi } from '#/api/cockpit';
import { getLoopMonitorListApi } from '#/api/loop';
import {
  getGradingThresholdsApi,
  getLoopMetricSeriesApi,
} from '#/api/metric';
import {
  parseTagCode,
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
import { MODE_KEY_ORDER, MODE_KEY_ZH, toLoopCardModel } from './loops-shared';

import './styles/theme.css';

const PAGE_SIZE = 20;
/** 服务端单页上限（/loops/monitor pageSize le=100） */
const FETCH_PAGE_SIZE = 100;
/** 拉全量页数上限（防护） */
const MAX_FETCH_PAGES = 20;

const cockpitStore = useCockpitStore();
const theme = computed(() => cockpitStore.theme);

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
// 中：回路列表（节点联动拉全量 → 客户端筛选/排序/分页）
// ---------------------------------------------------------------------------
const allLoops = ref<LoopApi.MonitorListItem[]>([]);
const listLoading = ref(true);
/** 节点口径模式分布（服务端聚合，面板态一柱状图） */
const modeDistribution = ref<null | Record<string, number>>(null);
const nodeLoopTotal = ref(0);

/** 定级阈值（等级五档色染；未加载时 loops-shared 降级国标默认） */
const gradingThresholds = ref<MetricApi.GradingThresholdItem[]>([]);

async function loadLoops(silent = false) {
  if (!silent) listLoading.value = true;
  try {
    const plantNodeId = cockpitStore.loopsNodeId ?? undefined;
    const items: LoopApi.MonitorListItem[] = [];
    let total = 0;
    let aggregate: LoopApi.MonitorAggregate | null = null;
    for (let page = 1; page <= MAX_FETCH_PAGES; page++) {
      const res = await getLoopMonitorListApi({
        plantNodeId,
        page,
        pageSize: FETCH_PAGE_SIZE,
      });
      if (page === 1) {
        total = res.total ?? 0;
        aggregate = res.aggregate ?? null;
      }
      items.push(...(res.items ?? []));
      if (items.length >= total || (res.items ?? []).length === 0) break;
    }
    allLoops.value = items;
    nodeLoopTotal.value = total;
    modeDistribution.value = aggregate?.modeDistribution ?? null;
  } catch {
    if (!silent) {
      allLoops.value = [];
      nodeLoopTotal.value = 0;
      modeDistribution.value = null;
    }
  } finally {
    if (!silent) listLoading.value = false;
  }
}

// ---------------------------------------------------------------------------
// 筛选 / 排序 / 分页
// ---------------------------------------------------------------------------
const models = computed<LoopCardModel[]>(() =>
  allLoops.value.map((item) => toLoopCardModel(item, gradingThresholds.value)),
);

const filteredSorted = computed<LoopCardModel[]>(() => {
  const { grades, modes, sortBy } = cockpitStore.loopsFilters;
  let list = models.value;
  if (grades.length > 0) {
    list = list.filter((m) => m.grade !== null && grades.includes(m.grade.key));
  }
  if (modes.length > 0) {
    list = list.filter((m) => m.mode !== null && modes.includes(m.mode));
  }
  const sorted = [...list];
  if (sortBy === 'scoreDesc') {
    sorted.sort((a, b) => (b.score ?? -1) - (a.score ?? -1));
  } else {
    // 劣化降序：较昨日下降最多在前（scoreDelta 升序），无增量排最后
    sorted.sort((a, b) => (a.scoreDelta ?? 999) - (b.scoreDelta ?? 999));
  }
  return sorted;
});

const pageCount = computed(() =>
  Math.max(1, Math.ceil(filteredSorted.value.length / PAGE_SIZE)),
);

const pageItems = computed<LoopCardModel[]>(() => {
  const start = (cockpitStore.loopsPage - 1) * PAGE_SIZE;
  return filteredSorted.value.slice(start, start + PAGE_SIZE);
});

/** 筛选/排序/装置变化后回跳：选中回路仍在结果内→跳到其所在页，否则回第 1 页 */
function applyPostChangeRule() {
  const list = filteredSorted.value;
  const sel = cockpitStore.loopsSelectedLoopId;
  const idx = sel ? list.findIndex((m) => m.loopId === sel) : -1;
  if (sel && idx >= 0) {
    cockpitStore.setLoopsPage(Math.floor(idx / PAGE_SIZE) + 1);
  } else {
    if (sel) cockpitStore.selectLoop(null);
    cockpitStore.setLoopsPage(1);
  }
}

/** 数据刷新后选中回路被过滤掉 → 自动回聚合视图 */
watch(filteredSorted, (list) => {
  const sel = cockpitStore.loopsSelectedLoopId;
  if (sel && !list.some((m) => m.loopId === sel)) {
    cockpitStore.selectLoop(null);
  }
});

watch(
  () => cockpitStore.loopsFilters,
  () => applyPostChangeRule(),
  { deep: true },
);

watch(
  () => cockpitStore.loopsNodeId,
  async () => {
    await loadLoops();
    applyPostChangeRule();
  },
);

function onPageChange(page: number) {
  cockpitStore.setLoopsPage(page);
}

// ---------------------------------------------------------------------------
// 火花线（近 24h 综合评分，metric-series 每批 ≤10 回路）
// ---------------------------------------------------------------------------
const sparkMap = ref<Map<string, { ts: null | string; value: null | number }[]>>(
  new Map(),
);

async function loadSparks(items: LoopCardModel[]) {
  if (items.length === 0) {
    sparkMap.value = new Map();
    return;
  }
  const end = new Date();
  const start = new Date(end.getTime() - 24 * 60 * 60 * 1000);
  const map = new Map<string, { ts: null | string; value: null | number }[]>();
  const ids = items.map((m) => m.loopId);
  for (let i = 0; i < ids.length; i += 10) {
    const chunk = ids.slice(i, i + 10);
    try {
      const res = await getLoopMetricSeriesApi({
        loopIds: chunk.join(','),
        metricKey: 'score',
        startTime: start.toISOString(),
        endTime: end.toISOString(),
      });
      for (const s of res.series ?? []) {
        if (s.loopId) map.set(s.loopId, s.points ?? []);
      }
    } catch {
      // 火花线失败不阻断卡片墙，占位 "—"
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
// WebSocket 实时刷新（PV/SP/OP/MODE 局部更新；断连 30s 轮询降级）
// ---------------------------------------------------------------------------
const realtime = useLoopRealtime();

function handleRealtimeMessage(msg: {
  collectTime: string;
  quality: number;
  tagCode: string;
  value: string;
}) {
  const parsed = parseTagCode(msg.tagCode);
  if (!parsed) return;
  const item = allLoops.value.find((l) => l.tagName === parsed.tagName);
  if (!item) return;
  const cv = item.currentValues;
  // R06：共享数值契约——无效字面量（-1.#QNAN0/nan/Infinity/空串）→ null
  const numValue = parseFiniteNumber(msg.value);
  switch (parsed.role) {
    case 'MODE': {
      // R17：按「回路 modeMapping（REST 下发）→ 默认映射 → Unknown」解析，
      // 删除"所有正数=Auto"硬编码；未知值显式 Unknown，不保留旧标签冒充
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

// ---------------------------------------------------------------------------
// 详情面板（两态 + ‹ › 连续浏览）
// ---------------------------------------------------------------------------
const selectedModel = computed<LoopCardModel | null>(() => {
  const sel = cockpitStore.loopsSelectedLoopId;
  return sel
    ? (filteredSorted.value.find((m) => m.loopId === sel) ?? null)
    : null;
});

const panelView = computed<'aggregate' | 'loop'>(() =>
  selectedModel.value ? 'loop' : 'aggregate',
);

const selectedIndex = computed(() => {
  const sel = cockpitStore.loopsSelectedLoopId;
  return sel
    ? filteredSorted.value.findIndex((m) => m.loopId === sel)
    : -1;
});

function browseLoop(delta: number) {
  const next = selectedIndex.value + delta;
  if (next < 0 || next >= filteredSorted.value.length) return;
  const target = filteredSorted.value[next];
  if (!target) return;
  cockpitStore.selectLoop(target.loopId);
  // 跨页自动翻页，保持卡片选中高亮
  cockpitStore.setLoopsPage(Math.floor(next / PAGE_SIZE) + 1);
}

function onCardSelect(loopId: string) {
  cockpitStore.selectLoop(loopId);
}

// ---------------------------------------------------------------------------
// 初始化
// ---------------------------------------------------------------------------
onMounted(async () => {
  // 定级阈值（等级色染）
  try {
    const res = await getGradingThresholdsApi();
    gradingThresholds.value = res?.thresholds ?? [];
  } catch {
    gradingThresholds.value = [];
  }

  // 装置树：默认选中「全厂」根节点（节点 watch 负责联动加载回路列表）
  let nodeAutoSelected = false;
  try {
    const tree = await getCockpitNodeTreeApi();
    treeNodes.value = tree ?? [];
    if (!cockpitStore.loopsNodeId && tree?.length) {
      const root = tree[0];
      if (root) {
        cockpitStore.setLoopsNode(root.nodeId);
        nodeAutoSelected = true;
      }
    }
  } catch {
    treeNodes.value = [];
  } finally {
    treeLoading.value = false;
  }

  // 未自动选根（树为空或已有选中节点）时由本处直接加载，避免与节点 watch 双发
  if (!nodeAutoSelected) {
    await loadLoops();
    applyPostChangeRule();
  }

  // WS 实时通道
  realtime.start();
  realtime.onMessage(handleRealtimeMessage);
});

watch(
  () => realtime.connectionStatus.value,
  (status, prev) => {
    if (status === 'offline') {
      realtime.startFallback(() => loadLoops(true));
    } else if (status === 'online' && prev !== 'online') {
      realtime.stopFallback();
      void loadLoops(true);
    }
  },
);

// ---------------------------------------------------------------------------
// C5 混合刷新（方案 §9）：卡片墙主数据/火花线/装置树角标 5min 定时 +
// 顶栏暂停开关 + 手动刷新（store.refreshTick）；实时值 WS 现状不动
// ---------------------------------------------------------------------------
const AUTO_REFRESH_MS = 5 * 60_000;

/** 静态数据全量重拉：回路列表（静默）+ 火花线 + 装置树角标 */
async function refreshStatic() {
  await Promise.all([loadLoops(true), refreshTree()]);
  void loadSparks(pageItems.value);
}

let autoTimer: null | ReturnType<typeof setInterval> = null;

onMounted(() => {
  // 5min 自动刷新：暂停时保持定时器但跳过拉取
  autoTimer = setInterval(() => {
    if (!cockpitStore.autoRefreshPaused) void refreshStatic();
  }, AUTO_REFRESH_MS);
});

onUnmounted(() => {
  if (autoTimer) {
    clearInterval(autoTimer);
    autoTimer = null;
  }
});

// 恢复自动刷新（暂停→恢复）时立即补拉一次
watch(
  () => cockpitStore.autoRefreshPaused,
  (paused, prev) => {
    if (prev && !paused) void refreshStatic();
  },
);

// 顶栏手动刷新（store.refreshTick ++）→ 全页重拉
watch(
  () => cockpitStore.refreshTick,
  () => void refreshStatic(),
);

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
            :total="filteredSorted.length"
            @change="onPageChange"
          />
        </div>

        <div class="cockpit-panel loops-wall__cards">
          <div v-if="listLoading" class="loops-wall__hint">回路加载中…</div>
          <div v-else-if="pageItems.length === 0" class="loops-wall__hint">
            当前条件下无回路
          </div>
          <div v-else class="loops-wall__grid">
            <LoopCard
              v-for="m in pageItems"
              :key="m.loopId"
              :loop="m"
              :selected="cockpitStore.loopsSelectedLoopId === m.loopId"
              :spark="sparkMap.get(m.loopId) ?? []"
              @select="onCardSelect"
            />
          </div>
        </div>
      </main>

      <!-- 右：详情面板区（两态：聚合 / 回路详情） -->
      <aside class="cockpit-panel loops-detail">
        <LoopDetailPanel
          :view="panelView"
          :node-id="cockpitStore.loopsNodeId"
          :node-name="selectedNodeName"
          :node-loop-count="nodeLoopTotal"
          :mode-distribution="modeDistribution"
          :loop="selectedModel"
          :loop-index="selectedIndex + 1"
          :loop-total="filteredSorted.length"
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
  flex: 1;
  min-height: 0;
}

.loops-wall__grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  grid-auto-rows: 118px;
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

.loops-detail {
  flex: none;
  width: 368px;
}
</style>
