<script lang="ts" setup>
import type { KpiCardKey } from './components/kpi-band.vue';
import type { RankSelection } from './utils/score-rank';

/**
 * 驾驶舱 · 页1 总览（方案 11 §5.1；2026-10-05 整合裁决 D2 合并重构；
 * 2026-10-10 修订：排名区两级化 + 排名→趋势/雷达联动 + 底行改柏拉图/环形图）
 *
 * 新总览 = 原驾驶舱总览 + 原运维工作台「系统总览」Tab 核心内容合并，
 * 重点显示当前全厂级各装置、单元的性能情况；按裁决删除「问题回路
 * TOP-8」「闭环治理漏斗」。
 *
 * 布局栅格（1920×1080 满屏无页面滚动，区块内滚动）：
 * - §0 顶栏 64px（cockpit-header，六页签）
 * - §1 KPI 指标带 ~120px：6 卡横排（kpi-band）
 * - 行1：综合评分排名(26%，装置×单元两级可折叠) | 绩效趋势(1fr，随选中
 *   节点联动) | 六维绩效雷达(32%，随选中节点联动，高度=趋势行)
 * - 行2：单元平稳率(26%) | 单元自控率柏拉图(1fr) | 适用性L0~L4环形图(32%)
 *
 * 联动协议（2026-10-10）：排名区点击装置/单元行 → selection（RankSelection）
 * 由本页持有：趋势面板与雷达面板消费 plant_node.id 刷新；再点同行取消选中
 * （趋势回全厂口径，雷达回空态提示）。处置待办 5 态胶囊与预警事件流两区
 * 块已按修订移除（KPI 卡待办/预警点击弹窗仍保留）。
 *
 * 交互铁律：纯查看零操作——所有点击仅打开舱内深度弹窗，
 * 无写操作按钮、无后台跳转链接（唯一后台入口为顶栏「管理后台」）。
 * 所有区块 watch cockpit store 的 timeWindow 重新拉取。
 */
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue';

import { getAlertEventsApi } from '#/api/alert';
import { getHandlingOrdersApi } from '#/api/handling';
import { getRankingApi } from '#/api/metric';
import { useCockpitStore } from '#/store/cockpit';
import { formatLocalTime, normalizeUtcTimestamp } from '#/utils/format';

import CockpitHeader from './components/cockpit-header.vue';
import DeviceRankBars from './components/device-rank-bars.vue';
import FitnessDonut from './components/fitness-donut.vue';
import KpiBand from './components/kpi-band.vue';
import EventDetailModal from './components/modals/event-detail-modal.vue';
import ListModal from './components/modals/list-modal.vue';
import LoopDetailModal from './components/modals/loop-detail-modal.vue';
import TodoDetailModal from './components/modals/todo-detail-modal.vue';
import NodeRadarPanel from './components/node-radar-panel.vue';
import TrendPanel from './components/trend-panel.vue';
import UnitAutoPareto from './components/unit-auto-pareto.vue';
import UnitSteadyBars from './components/unit-steady-bars.vue';
import { GRADE_LABELS, gradeOfScore } from './composables/use-cockpit-theme';
import { WINDOW_MAP, windowStartDate } from './utils/format';

import './styles/theme.css';

const cockpitStore = useCockpitStore();
const theme = computed(() => cockpitStore.theme);

// ---------------------------------------------------------------------------
// 排名区选中态（2026-10-10 联动修订）：趋势 + 雷达共享
// ---------------------------------------------------------------------------
const selection = ref<null | RankSelection>(null);

function onRankSelect(payload: null | RankSelection) {
  selection.value = payload;
}

// ---------------------------------------------------------------------------
// C5 混合刷新（方案 §9）：静态区块 5min 定时 + 顶栏暂停/手动刷新
// ---------------------------------------------------------------------------
const AUTO_REFRESH_MS = 5 * 60_000;

const kpiBandRef = ref<InstanceType<typeof KpiBand>>();
const rankBarsRef = ref<InstanceType<typeof DeviceRankBars>>();
const trendPanelRef = ref<InstanceType<typeof TrendPanel>>();
const radarPanelRef = ref<InstanceType<typeof NodeRadarPanel>>();
const steadyBarsRef = ref<InstanceType<typeof UnitSteadyBars>>();
const autoParetoRef = ref<InstanceType<typeof UnitAutoPareto>>();
const fitnessDonutRef = ref<InstanceType<typeof FitnessDonut>>();

/** §1 KPI/§2 排名/§3 趋势/§4 雷达/§8 单元平稳率/自控率柏拉图/适用性环形（5min 定时器口径） */
const staticRefs = [
  kpiBandRef,
  rankBarsRef,
  trendPanelRef,
  radarPanelRef,
  steadyBarsRef,
  autoParetoRef,
  fitnessDonutRef,
];

function reloadStatic() {
  for (const r of staticRefs) void r.value?.reload();
}

/** 手动全页刷新：全部静态区块 */
function reloadAll() {
  reloadStatic();
}

let autoTimer: null | ReturnType<typeof setInterval> = null;

onMounted(() => {
  // 5min 自动刷新：暂停时保持定时器但跳过拉取
  autoTimer = setInterval(() => {
    if (!cockpitStore.autoRefreshPaused) reloadStatic();
  }, AUTO_REFRESH_MS);
});

onUnmounted(() => {
  if (autoTimer) {
    clearInterval(autoTimer);
    autoTimer = null;
  }
});

// 恢复自动刷新（暂停→恢复）时立即补拉一次（静态区块）
watch(
  () => cockpitStore.autoRefreshPaused,
  (paused, prev) => {
    if (prev && !paused) {
      reloadStatic();
    }
  },
);

// 顶栏手动刷新（store.refreshTick ++）→ 全页重拉
watch(
  () => cockpitStore.refreshTick,
  () => reloadAll(),
);

// ---------------------------------------------------------------------------
// 弹窗状态（4 类深度弹窗，宽 ~880px，ESC/遮罩关闭）
// ---------------------------------------------------------------------------
const loopDetail = reactive({ loopId: null as null | string, open: false });
const todoDetail = reactive({ open: false, orderId: null as null | string });
const eventDetail = reactive({ eventId: null as null | string, open: false });

/** 清单类弹窗状态（§1 KPI 卡 / §5 漏斗阶段共用） */
const listModal = reactive({
  columns: [] as { key: string; label: string; width?: string }[],
  description: '',
  loading: false,
  open: false,
  /** 行点击动作：再开对应详情弹窗 */
  rowAction: null as 'event' | 'loop' | 'todo' | null,
  rows: [] as Record<string, unknown>[],
  title: '',
});

function openLoopDetail(loopId: string) {
  loopDetail.loopId = loopId;
  loopDetail.open = true;
}

function openTodoDetail(orderId: string) {
  todoDetail.orderId = orderId;
  todoDetail.open = true;
}

function openEventDetail(eventId: string) {
  eventDetail.eventId = eventId;
  eventDetail.open = true;
}

function openListModal(opts: {
  columns: { key: string; label: string; width?: string }[];
  description?: string;
  rowAction?: 'event' | 'loop' | 'todo' | null;
  title: string;
}) {
  listModal.title = opts.title;
  listModal.description = opts.description ?? '';
  listModal.columns = opts.columns;
  listModal.rowAction = opts.rowAction ?? null;
  listModal.rows = [];
  listModal.loading = true;
  listModal.open = true;
}

function onListRowClick(row: Record<string, unknown>) {
  if (listModal.rowAction === 'loop' && typeof row.loopId === 'string') {
    openLoopDetail(row.loopId);
  } else if (
    listModal.rowAction === 'todo' &&
    typeof row.orderId === 'string'
  ) {
    openTodoDetail(row.orderId);
  } else if (
    listModal.rowAction === 'event' &&
    typeof row.eventId === 'string'
  ) {
    openEventDetail(row.eventId);
  }
}

// ---------------------------------------------------------------------------
// §1 KPI 卡点击 → 对应清单类弹窗
// ---------------------------------------------------------------------------
const LOOP_COLUMNS = [
  { key: 'tagName', label: '回路号' },
  { key: 'loopName', label: '名称' },
  { key: 'unitName', label: '装置' },
  { key: 'scoreText', label: '综合评分', width: '90px' },
  { key: 'grade', label: '等级', width: '80px' },
];

async function openScoreList(sortOrder: 'asc' | 'desc', title: string) {
  openListModal({ columns: LOOP_COLUMNS, rowAction: 'loop', title });
  try {
    const items = await getRankingApi({
      limit: 50,
      sortBy: 'score',
      sortOrder,
      timeWindow: WINDOW_MAP[cockpitStore.timeWindow],
    });
    listModal.rows = (items ?? [])
      .filter((it) => it.includeInEvaluation !== false)
      .map((it) => ({
        grade: GRADE_LABELS[gradeOfScore(it.score) ?? 'FAIR'] ?? '—',
        loopId: it.loopId,
        loopName: it.loopName ?? '—',
        scoreText: it.score.toFixed(2),
        tagName: it.tagName,
        unitName: it.unitName,
      }));
  } catch {
    listModal.rows = [];
  } finally {
    listModal.loading = false;
  }
}

const ACTIVE_ORDER_STATUSES = new Set(['EXECUTING', 'PENDING', 'REOPENED', 'VERIFYING']);

async function openTodoList() {
  openListModal({
    columns: [
      { key: 'orderNo', label: '处置编号', width: '150px' },
      { key: 'loopTagName', label: '回路号', width: '110px' },
      { key: 'title', label: '问题摘要' },
      { key: 'statusLabel', label: '状态', width: '90px' },
      { key: 'updatedAtText', label: '最近更新', width: '120px' },
    ],
    rowAction: 'todo',
    title: '处置待办清单',
  });
  try {
    const res = await getHandlingOrdersApi({ page: 1, pageSize: 100 });
    const since = windowStartDate(cockpitStore.timeWindow).getTime();
    listModal.rows = (res?.items ?? [])
      .filter((o) => ACTIVE_ORDER_STATUSES.has(o.status))
      .filter((o) => {
        const t = new Date(
          normalizeUtcTimestamp(o.updatedAt ?? ''),
        ).getTime();
        return Number.isFinite(t) && t >= since;
      })
      .map((o) => ({
        loopTagName: o.loopTagName,
        orderId: o.id,
        orderNo: o.orderNo,
        statusLabel: o.statusLabel,
        title: o.title,
        updatedAtText: formatLocalTime(o.updatedAt, 'MM-DD HH:mm'),
      }));
  } catch {
    listModal.rows = [];
  } finally {
    listModal.loading = false;
  }
}

async function openAlertList() {
  openListModal({
    columns: [
      { key: 'ruleName', label: '规则' },
      { key: 'loop', label: '回路', width: '130px' },
      { key: 'severity', label: '级别', width: '70px' },
      { key: 'statusLabel', label: '状态', width: '80px' },
      { key: 'triggeredAtText', label: '触发时间', width: '130px' },
    ],
    rowAction: 'event',
    title: '预警事件清单',
  });
  try {
    const res = await getAlertEventsApi({
      endTime: new Date().toISOString(),
      limit: 50,
      startTime: windowStartDate(cockpitStore.timeWindow).toISOString(),
    });
    const SEV: Record<string, string> = {
      CRITICAL: '严重',
      ERROR: '错误',
      INFO: '提示',
      WARN: '警告',
    };
    listModal.rows = (res?.items ?? []).map((e) => ({
      eventId: e.eventId,
      loop: e.loopName ?? e.loopId ?? '—',
      ruleName: e.ruleName ?? e.ruleCode,
      severity: SEV[e.severity] ?? e.severity,
      statusLabel: e.acknowledgedAt ? '已确认' : '未确认',
      triggeredAtText: formatLocalTime(e.triggeredAt, 'MM-DD HH:mm:ss'),
    }));
  } catch {
    listModal.rows = [];
  } finally {
    listModal.loading = false;
  }
}

function onKpiCardClick(key: KpiCardKey) {
  switch (key) {
  case 'degraded': {
    openScoreList('asc', '劣化回路清单（按评分升序）');

  break;
  }
  case 'score': {
    openScoreList('desc', '回路评分清单');

  break;
  }
  case 'todo': {
    openTodoList();

  break;
  }
  default: {
    openAlertList();
  }
  }
}
</script>

<template>
  <div class="cockpit-root cockpit-overview" :data-theme="theme">
    <CockpitHeader />

    <!-- §1 KPI 指标带（6 卡横排） -->
    <KpiBand ref="kpiBandRef" @card-click="onKpiCardClick" />

    <!-- 六区块两行三列（26% / 1fr / 32%）；行1=排名/趋势/雷达（联动），行2=平稳率/自控率柏拉图/适用性环形 -->
    <section class="block-grid">
      <DeviceRankBars
        ref="rankBarsRef"
        class="block-rank"
        :selected="selection"
        @select="onRankSelect"
      />
      <TrendPanel ref="trendPanelRef" class="block-trend" :node="selection" />
      <NodeRadarPanel ref="radarPanelRef" class="block-radar" :node="selection" />
      <UnitSteadyBars ref="steadyBarsRef" class="block-steady" />
      <UnitAutoPareto ref="autoParetoRef" class="block-pareto" />
      <FitnessDonut ref="fitnessDonutRef" class="block-donut" />
    </section>

    <!-- 4 类深度弹窗（纯查看，无任何操作按钮） -->
    <LoopDetailModal
      :open="loopDetail.open"
      :loop-id="loopDetail.loopId"
      @close="loopDetail.open = false"
    />
    <TodoDetailModal
      :open="todoDetail.open"
      :order-id="todoDetail.orderId"
      @close="todoDetail.open = false"
    />
    <EventDetailModal
      :open="eventDetail.open"
      :event-id="eventDetail.eventId"
      @close="eventDetail.open = false"
    />
    <ListModal
      :open="listModal.open"
      :title="listModal.title"
      :description="listModal.description"
      :columns="listModal.columns"
      :rows="listModal.rows"
      :loading="listModal.loading"
      :row-clickable="listModal.rowAction !== null"
      @close="listModal.open = false"
      @row-click="onListRowClick"
    />
  </div>
</template>

<style scoped>
.cockpit-overview {
  gap: 12px;
  padding-bottom: 12px;
}

/* 六区块两行三列（26% / 1fr / 32%）；雷达/环形图分踞右列两行 */
.block-grid {
  display: grid;
  flex: 1;
  grid-template-rows: 1fr 1fr;
  grid-template-columns: 26% 1fr 32%;
  gap: 12px;
  min-height: 0;
  padding: 0 12px;
}

.block-rank {
  grid-area: 1 / 1;
}

.block-trend {
  grid-area: 1 / 2;
}

.block-radar {
  grid-area: 1 / 3;
}

.block-steady {
  grid-area: 2 / 1;
}

.block-pareto {
  grid-area: 2 / 2;
}

.block-donut {
  grid-area: 2 / 3;
}
</style>
