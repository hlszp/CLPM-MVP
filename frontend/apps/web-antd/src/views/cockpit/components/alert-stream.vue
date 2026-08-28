<script lang="ts" setup>
/**
 * 驾驶舱总览 §7 预警事件流（方案 11 §5.2 / §9）
 *
 * 数据：GET /alert/events（limit=8，按时间窗 startTime 过滤）
 * + WebSocket /api/v1/ws/alerts 增量推送（复用 utils/alert-ws 单例，
 * query token 认证，心跳 ping/pong 由客户端内置处理）；
 * WS 未连通时降级为 60s 轮询（Calm UI：无闪烁，数值平滑更新）。
 *
 * 行：级别色条 + 规则名 + 回路 + 触发时间 + 持续时长；未确认（无
 * acknowledgedAt）高亮。点行 → 抛 open-event，父级打开事件详情弹窗。
 */
import type { AlertApi } from '#/api/alert';

import { onMounted, onUnmounted, ref, watch } from 'vue';

import { useAccessStore } from '@vben/stores';

import { getAlertEventsApi } from '#/api/alert';
import { useCockpitStore } from '#/store/cockpit';
import { alertWs } from '#/utils/alert-ws';
import { formatLocalTime } from '#/utils/format';

import { useCockpitTheme } from '../composables/use-cockpit-theme';
import { formatDuration, isWithinWindow, windowStartDate } from '../utils/format';

const emit = defineEmits<{ openEvent: [eventId: string] }>();

const cockpitStore = useCockpitStore();
const accessStore = useAccessStore();
const { severityColors } = useCockpitTheme();

const loading = ref(true);
const events = ref<AlertApi.EventItem[]>([]);
const wsOnline = ref(false);

const MAX_ROWS = 8;
const POLL_INTERVAL = 60_000;

async function load() {
  try {
    const now = new Date();
    const res = await getAlertEventsApi({
      endTime: now.toISOString(),
      limit: MAX_ROWS,
      startTime: windowStartDate(cockpitStore.timeWindow).toISOString(),
    });
    events.value = res?.items ?? [];
  } catch {
    events.value = [];
  } finally {
    loading.value = false;
  }
}

/** WS 增量置顶：按 eventId 去重、时间窗过滤、封顶 8 条 */
function onWsMessage(msg: {
  eventId?: string;
  loopId?: string;
  ruleCode?: string;
  ruleName?: string;
  severity?: AlertApi.Severity;
  triggeredAt?: string;
  triggeredValue?: number;
  type: string;
}) {
  if (msg.type !== 'alert') return;
  if (msg.triggeredAt && !isWithinWindow(msg.triggeredAt, cockpitStore.timeWindow)) {
    return;
  }
  const id = msg.eventId || `${msg.ruleCode}-${msg.triggeredAt ?? Date.now()}`;
  const item: AlertApi.EventItem = {
    eventId: id,
    loopId: msg.loopId ?? '',
    ruleCode: msg.ruleCode ?? '',
    ruleDslSnapshot: {},
    ruleName: msg.ruleName,
    ruleVersion: 0,
    severity: msg.severity ?? 'INFO',
    status: 'ACTIVE',
    triggerConditionSnapshot: {},
    triggerCount: 1,
    triggeredAt: msg.triggeredAt ?? new Date().toISOString(),
    triggeredValue: msg.triggeredValue,
  };
  events.value = [
    item,
    ...events.value.filter((e) => e.eventId !== id),
  ].slice(0, MAX_ROWS);
}

let unsubscribe: (() => void) | null = null;
let pollTimer: null | ReturnType<typeof setInterval> = null;
let connectedByUs = false;

function setupWs() {
  const token = accessStore.accessToken;
  if (token && !alertWs.isConnected) {
    alertWs.connect(token);
    connectedByUs = true;
  }
  unsubscribe = alertWs.onMessage(onWsMessage);
  wsOnline.value = alertWs.isConnected;
  // 降级轮询：WS 未连通时 60s 拉一次
  pollTimer = setInterval(() => {
    wsOnline.value = alertWs.isConnected;
    if (!alertWs.isConnected) load();
  }, POLL_INTERVAL);
}

onMounted(() => {
  load();
  setupWs();
});

onUnmounted(() => {
  unsubscribe?.();
  if (pollTimer) clearInterval(pollTimer);
  if (connectedByUs) alertWs.disconnect();
});

watch(() => cockpitStore.timeWindow, load);

/** C5 手动刷新：父级 watch store.refreshTick 触发（自动 5min 不经此区块） */
defineExpose({ reload: load });

/** 持续时长：ACTIVE → 触发至今；已解决 → 触发→解决 */
function durationOf(e: AlertApi.EventItem): string {
  return formatDuration(e.triggeredAt, e.resolvedAt ?? undefined);
}

function sevColor(sev: AlertApi.Severity): string {
  return severityColors.value[sev] ?? severityColors.value.INFO;
}

/** 未确认（无 acknowledgedAt 且非终态）高亮 */
function isUnconfirmed(e: AlertApi.EventItem): boolean {
  return !e.acknowledgedAt && e.status === 'ACTIVE';
}
</script>

<template>
  <div class="cockpit-panel alert-stream">
    <div class="cockpit-panel__hd">
      预警事件流
      <span class="sub">
        时间窗内最新 {{ MAX_ROWS }} 条 · {{ wsOnline ? '实时推送' : '60s 轮询' }}
      </span>
    </div>
    <div class="alert-stream__bd">
      <div v-if="loading" class="alert-stream__state">加载中…</div>
      <div v-else-if="events.length === 0" class="alert-stream__state">
        时间窗内暂无预警事件
      </div>
      <div v-else class="alert-stream__rows">
        <div
          v-for="e in events"
          :key="e.eventId"
          class="alert-stream__row"
          :class="{ unconfirmed: isUnconfirmed(e) }"
          @click="emit('openEvent', e.eventId)"
        >
          <span
            class="alert-stream__sev"
            :style="{ background: sevColor(e.severity) }"
            :title="e.severity"
          ></span>
          <div class="alert-stream__main">
            <div class="alert-stream__line1">
              <span class="alert-stream__rule" :title="e.ruleName ?? e.ruleCode">
                {{ e.ruleName ?? e.ruleCode }}
              </span>
              <span v-if="isUnconfirmed(e)" class="alert-stream__unack">
                未确认
              </span>
            </div>
            <div class="alert-stream__line2">
              <span class="mono">{{ e.loopName ?? e.loopId ?? '—' }}</span>
              <span>{{ formatLocalTime(e.triggeredAt, 'MM-DD HH:mm') }}</span>
            </div>
          </div>
          <span class="alert-stream__duration">{{ durationOf(e) }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.alert-stream__bd {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 4px 8px;
}

.alert-stream__state {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  font-size: 12px;
  color: var(--ck-text-3);
}

.alert-stream__rows {
  display: flex;
  flex-direction: column;
}

.alert-stream__row {
  display: grid;
  grid-template-columns: 4px minmax(0, 1fr) auto;
  gap: 8px;
  align-items: center;
  min-height: 42px;
  padding: 4px 6px;
  cursor: pointer;
  border-bottom: 1px solid var(--ck-border);
  border-radius: 4px;
}

.alert-stream__row:hover {
  background: var(--ck-hover);
}

.alert-stream__row.unconfirmed {
  background: var(--ck-panel-2);
}

.alert-stream__row.unconfirmed:hover {
  background: var(--ck-hover);
}

.alert-stream__sev {
  align-self: stretch;
  width: 4px;
  margin: 4px 0;
  border-radius: 2px;
}

.alert-stream__main {
  display: flex;
  flex-direction: column;
  min-width: 0;
  gap: 1px;
}

.alert-stream__line1 {
  display: flex;
  gap: 6px;
  align-items: center;
  min-width: 0;
}

.alert-stream__rule {
  overflow: hidden;
  font-size: 12px;
  font-weight: 600;
  color: var(--ck-text);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.alert-stream__unack {
  flex: none;
  padding: 0 6px;
  font-size: 10px;
  color: var(--ck-grade-fair);
  border: 1px solid var(--ck-grade-fair);
  border-radius: 999px;
}

.alert-stream__line2 {
  display: flex;
  gap: 10px;
  overflow: hidden;
  font-size: 10px;
  color: var(--ck-text-3);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.alert-stream__line2 .mono {
  font-variant-numeric: tabular-nums;
}

.alert-stream__duration {
  flex: none;
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  color: var(--ck-text-3);
}
</style>
