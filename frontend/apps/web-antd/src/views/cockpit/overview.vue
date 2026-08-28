<script lang="ts" setup>
import type { KpiCardKey } from './components/kpi-band.vue';

/**
 * 驾驶舱 · 页1 总览（方案 11 §5.1，C3 全量落地）
 *
 * 布局栅格（1920×1080 满屏无页面滚动，区块内滚动）：
 * - §0 顶栏 64px（cockpit-header）
 * - §1 KPI 指标带 ~120px：6 卡横排（kpi-band）
 * - 行1：§2 装置排名横道图(20%) §3 绩效发展趋势(45%) §4 处置待办(35%)
 * - 行2：§5 闭环治理漏斗(20%) §6 问题回路 TOP-8(45%) §7 预警事件流(35%)
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
import { formatLocalTime } from '#/utils/format';

import AlertStream from './components/alert-stream.vue';
import CockpitHeader from './components/cockpit-header.vue';
import DeviceRankBars from './components/device-rank-bars.vue';
import FunnelPanel from './components/funnel-panel.vue';
import KpiBand from './components/kpi-band.vue';
import LoopTopTable from './components/loop-top-table.vue';
import EventDetailModal from './components/modals/event-detail-modal.vue';
import ListModal from './components/modals/list-modal.vue';
import LoopDetailModal from './components/modals/loop-detail-modal.vue';
import TodoDetailModal from './components/modals/todo-detail-modal.vue';
import TodoPanel from './components/todo-panel.vue';
import TrendPanel from './components/trend-panel.vue';
import { GRADE_LABELS, gradeOfScore } from './composables/use-cockpit-theme';
import { WINDOW_MAP, windowStartDate } from './utils/format';

import './styles/theme.css';

const cockpitStore = useCockpitStore();
const theme = computed(() => cockpitStore.theme);

// ---------------------------------------------------------------------------
// C5 混合刷新（方案 §9）：静态区块 5min 定时 + 顶栏暂停/手动刷新
// ---------------------------------------------------------------------------
const AUTO_REFRESH_MS = 5 * 60_000;

const kpiBandRef = ref<InstanceType<typeof KpiBand>>();
const rankBarsRef = ref<InstanceType<typeof DeviceRankBars>>();
const trendPanelRef = ref<InstanceType<typeof TrendPanel>>();
const todoPanelRef = ref<InstanceType<typeof TodoPanel>>();
const funnelPanelRef = ref<InstanceType<typeof FunnelPanel>>();
const topTableRef = ref<InstanceType<typeof LoopTopTable>>();
const alertStreamRef = ref<InstanceType<typeof AlertStream>>();

/** §1 KPI/§2 排名/§3 趋势/§5 漏斗/§6 问题回路（5min 定时器口径） */
const staticRefs = [kpiBandRef, rankBarsRef, trendPanelRef, funnelPanelRef, topTableRef];

function reloadStatic() {
  for (const r of staticRefs) void r.value?.reload();
}

/** 手动全页刷新：静态 5 区块 + §4 待办 + §7 预警流 */
function reloadAll() {
  reloadStatic();
  void todoPanelRef.value?.reload();
  void alertStreamRef.value?.reload();
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

// 恢复自动刷新（暂停→恢复）时立即补拉一次（静态区块 + §4 待办）
watch(
  () => cockpitStore.autoRefreshPaused,
  (paused, prev) => {
    if (prev && !paused) {
      reloadStatic();
      void todoPanelRef.value?.reload();
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
        scoreText: it.score.toFixed(1),
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
    listModal.rows = (res?.items ?? [])
      .filter((o) => ACTIVE_ORDER_STATUSES.has(o.status))
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

// ---------------------------------------------------------------------------
// §5 漏斗阶段点击 → 阶段计数与口径说明（无后端阶段清单接口，不造数）
// ---------------------------------------------------------------------------
const FUNNEL_STAGE_META: Record<
  string,
  { description: string; label: string }
> = {
  closed: {
    description: '口径：时间窗内处置闭环数（处置工单 CLOSED）。',
    label: '处置闭环',
  },
  diagnosed: {
    description:
      '口径：时间窗内完成诊断的回路数（diagnosis_run COMPLETED 按回路去重）。',
    label: '完成诊断',
  },
  discovered: {
    description:
      '口径：时间窗内评估发现的劣化回路数（警告 + 不合格档）。',
    label: '发现异常',
  },
  tuned: {
    description: '口径：时间窗内整定方案确认数。',
    label: '产出方案',
  },
};

function onFunnelStageClick(stage: string, count: number) {
  const meta = FUNNEL_STAGE_META[stage];
  if (!meta) return;
  openListModal({
    columns: [
      { key: 'label', label: '阶段' },
      { key: 'count', label: '数量', width: '100px' },
    ],
    description: `${meta.description}（阶段明细清单接口暂缺，仅展示阶段计数与口径说明）`,
    rowAction: null,
    title: `闭环治理漏斗 · ${meta.label}`,
  });
  listModal.rows = [{ count, label: meta.label }];
  listModal.loading = false;
}
</script>

<template>
  <div class="cockpit-root cockpit-overview" :data-theme="theme">
    <CockpitHeader />

    <!-- §1 KPI 指标带（6 卡横排） -->
    <KpiBand ref="kpiBandRef" @card-click="onKpiCardClick" />

    <!-- §2~§7 两行三列区块（20% / 45% / 35%） -->
    <section class="block-grid">
      <DeviceRankBars ref="rankBarsRef" class="block-rank" />
      <TrendPanel ref="trendPanelRef" class="block-trend" />
      <TodoPanel
        ref="todoPanelRef"
        class="block-todo"
        @open-todo="openTodoDetail"
      />
      <FunnelPanel
        ref="funnelPanelRef"
        class="block-funnel"
        @stage-click="onFunnelStageClick"
      />
      <LoopTopTable
        ref="topTableRef"
        class="block-top8"
        @open-loop="openLoopDetail"
      />
      <AlertStream
        ref="alertStreamRef"
        class="block-alert"
        @open-event="openEventDetail"
      />
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

/* §2~§7 两行三列：20% / 45% / 35%，行高弹性均分剩余空间 */
.block-grid {
  display: grid;
  flex: 1;
  grid-template-rows: 1fr 1fr;
  grid-template-columns: 20% 1fr 35%;
  gap: 12px;
  min-height: 0;
  padding: 0 12px;
}
</style>
