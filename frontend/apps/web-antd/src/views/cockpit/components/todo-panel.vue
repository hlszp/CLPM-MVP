<script lang="ts" setup>
/**
 * 驾驶舱总览 §4 处置待办（方案 11 §5.2）
 *
 * 数据：GET /handling/orders（v2.0 双实体工单口径）。
 * - 顶部五态胶囊：待处理(PENDING) / 处理中(EXECUTING) / 验证中(VERIFYING)
 *   / 已闭环(CLOSED) / 重开(REOPENED)，计数 = 各状态分页 total（精确值）
 * - TOP 待办清单：在途工单按停留时长倒排（最久在前），回路号+问题摘要
 *   +状态+停留时长（自 updatedAt 起算）；VERIFYING 停留 >24h 红标「验证超期」
 * 点行 → 抛 open-todo，父级打开待办详情弹窗。
 */
import type { HandlingApi } from '#/api/handling';

import { computed, onMounted, onUnmounted, ref, watch } from 'vue';

import { getHandlingOrdersApi } from '#/api/handling';
import { useCockpitStore } from '#/store/cockpit';
import { normalizeUtcTimestamp } from '#/utils/format';

import { formatDuration } from '../utils/format';

const emit = defineEmits<{ openTodo: [orderId: string] }>();

const cockpitStore = useCockpitStore();

/** 五态胶囊定义（顺序固定） */
const CAPSULES: { key: HandlingApi.OrderStatus; label: string }[] = [
  { key: 'PENDING', label: '待处理' },
  { key: 'EXECUTING', label: '处理中' },
  { key: 'VERIFYING', label: '验证中' },
  { key: 'CLOSED', label: '已闭环' },
  { key: 'REOPENED', label: '重开' },
];

const ACTIVE_STATUSES = new Set<HandlingApi.OrderStatus>([
  'EXECUTING',
  'PENDING',
  'REOPENED',
  'VERIFYING',
]);

const loading = ref(true);
const counts = ref<Record<HandlingApi.OrderStatus, number>>({
  CANCELLED: 0,
  CLOSED: 0,
  EXECUTING: 0,
  PENDING: 0,
  REOPENED: 0,
  VERIFYING: 0,
});
const items = ref<HandlingApi.OrderItem[]>([]);

async function load() {
  loading.value = true;
  // 计数：每状态 pageSize=1 取分页 total（精确）；清单：单页 100 条前端聚合
  const [list, ...stats] = await Promise.allSettled([
    getHandlingOrdersApi({ page: 1, pageSize: 100 }),
    ...CAPSULES.map((c) =>
      getHandlingOrdersApi({ page: 1, pageSize: 1, status: c.key }),
    ),
  ]);
  if (list.status === 'fulfilled') items.value = list.value?.items ?? [];
  stats.forEach((s, i) => {
    if (s.status === 'fulfilled') {
      counts.value[CAPSULES[i]!.key] = s.value?.total ?? 0;
    }
  });
  loading.value = false;
}

onMounted(load);
watch(() => cockpitStore.timeWindow, load);

/** C5 混合刷新：由父级（手动刷新/恢复补拉）触发重拉 */
defineExpose({ reload: load });

// C5：待办 60s 轮询（方案 §9）；顶栏暂停时跳过触发
let pollTimer: null | ReturnType<typeof setInterval> = null;
onMounted(() => {
  pollTimer = setInterval(() => {
    if (!cockpitStore.autoRefreshPaused) void load();
  }, 60_000);
});
onUnmounted(() => {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
});

/** TOP 待办：在途工单按停留时长（updatedAt，最久在前）取前 8 */
const topTodos = computed(() =>
  items.value
    .filter((o) => ACTIVE_STATUSES.has(o.status))
    .toSorted((a, b) => {
      const ta = a.updatedAt
        ? new Date(normalizeUtcTimestamp(a.updatedAt)).getTime()
        : 0;
      const tb = b.updatedAt
        ? new Date(normalizeUtcTimestamp(b.updatedAt)).getTime()
        : 0;
      return ta - tb;
    })
    .slice(0, 8),
);

function stayHours(o: HandlingApi.OrderItem): number {
  if (!o.updatedAt) return 0;
  const t = new Date(normalizeUtcTimestamp(o.updatedAt)).getTime();
  if (Number.isNaN(t)) return 0;
  return (Date.now() - t) / 3_600_000;
}

/** VERIFYING 停留 >24h → 红标「验证超期」（方案 §5.2） */
function isVerifyOverdue(o: HandlingApi.OrderItem): boolean {
  return o.status === 'VERIFYING' && stayHours(o) > 24;
}

const capsuleRows = computed(() =>
  CAPSULES.map((c) => ({ ...c, count: counts.value[c.key] ?? 0 })),
);
</script>

<template>
  <div class="cockpit-panel todo">
    <div class="cockpit-panel__hd">
      处置待办
      <span class="sub">五态胶囊 + TOP 待办（按停留时长）</span>
    </div>
    <div class="todo__bd">
      <!-- 五态胶囊 -->
      <div class="todo__capsules">
        <div
          v-for="c in capsuleRows"
          :key="c.key"
          class="todo__capsule"
          :class="`st-${c.key.toLowerCase()}`"
        >
          <span class="todo__capsule-count">{{ loading ? '…' : c.count }}</span>
          <span class="todo__capsule-label">{{ c.label }}</span>
        </div>
      </div>

      <!-- TOP 待办清单 -->
      <div v-if="loading" class="todo__state">加载中…</div>
      <div v-else-if="topTodos.length === 0" class="todo__state">
        暂无在途处置工单
      </div>
      <div v-else class="todo__rows">
        <div
          v-for="o in topTodos"
          :key="o.id"
          class="todo__row"
          @click="emit('openTodo', o.id)"
        >
          <div class="todo__row-main">
            <span class="todo__loop">{{ o.loopTagName }}</span>
            <span class="todo__title" :title="o.title">{{ o.title }}</span>
          </div>
          <span class="todo__status" :class="`st-${o.status.toLowerCase()}`">
            {{ o.statusLabel }}
          </span>
          <span
            class="todo__stay"
            :class="{ overdue: isVerifyOverdue(o) }"
            :title="isVerifyOverdue(o) ? '验证超期' : '停留时长'"
          >
            {{ formatDuration(o.updatedAt) }}
            <template v-if="isVerifyOverdue(o)">· 验证超期</template>
          </span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.todo__bd {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 8px;
  min-height: 0;
  padding: 10px 12px;
}

.todo__capsules {
  display: grid;
  flex: none;
  grid-template-columns: repeat(5, 1fr);
  gap: 6px;
}

.todo__capsule {
  display: flex;
  flex-direction: column;
  gap: 2px;
  align-items: center;
  padding: 6px 0;
  background: var(--ck-panel-2);
  border: 1px solid var(--ck-border);
  border-radius: 8px;
}

.todo__capsule-count {
  font-size: 16px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  color: var(--ck-text);
}

.todo__capsule-label {
  font-size: 10px;
  color: var(--ck-text-3);
}

.todo__capsule.st-pending .todo__capsule-count {
  color: var(--ck-grade-fair);
}

.todo__capsule.st-executing .todo__capsule-count {
  color: var(--ck-accent);
}

.todo__capsule.st-verifying .todo__capsule-count {
  color: var(--ck-grade-good);
}

.todo__capsule.st-closed .todo__capsule-count {
  color: var(--ck-grade-excellent);
}

.todo__capsule.st-reopened .todo__capsule-count {
  color: var(--ck-grade-warning);
}

.todo__state {
  display: flex;
  flex: 1;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  color: var(--ck-text-3);
}

.todo__rows {
  flex: 1;
  min-height: 0;
  overflow: auto;
}

.todo__row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto auto;
  gap: 10px;
  align-items: center;
  min-height: 36px;
  padding: 4px 6px;
  cursor: pointer;
  border-bottom: 1px solid var(--ck-border);
}

.todo__row:hover {
  background: var(--ck-hover);
}

.todo__row-main {
  display: flex;
  gap: 8px;
  align-items: baseline;
  min-width: 0;
}

.todo__loop {
  flex: none;
  font-size: 12px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: var(--ck-text);
}

.todo__title {
  overflow: hidden;
  font-size: 11px;
  color: var(--ck-text-2);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.todo__status {
  flex: none;
  padding: 1px 8px;
  font-size: 10px;
  color: var(--ck-text-2);
  background: var(--ck-panel-3);
  border-radius: 999px;
}

.todo__status.st-pending {
  color: var(--ck-grade-fair);
}

.todo__status.st-executing {
  color: var(--ck-accent);
}

.todo__status.st-verifying {
  color: var(--ck-grade-good);
}

.todo__status.st-reopened {
  color: var(--ck-grade-warning);
}

.todo__stay {
  flex: none;
  min-width: 64px;
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  color: var(--ck-text-3);
  text-align: right;
}

.todo__stay.overdue {
  font-weight: 600;
  color: var(--ck-grade-poor);
}
</style>
