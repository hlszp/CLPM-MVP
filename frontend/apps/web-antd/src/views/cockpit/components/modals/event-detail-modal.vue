<script lang="ts" setup>
/**
 * 预警事件详情弹窗（方案 11 §7 · 触发源：§7 预警流行）
 *
 * 数据：GET /alert/events/{eventId}。
 * 内容：规则定义（规则名/编码/级别/触发条件快照）+ 触发值 + 持续时间
 * + 关联回路 + 确认/解决留痕（只读）。无任何操作按钮。
 */
import type { AlertApi } from '#/api/alert';

import { computed, ref, watch } from 'vue';

import { getAlertEventApi } from '#/api/alert';
import { formatLocalTime } from '#/utils/format';

import { useCockpitTheme } from '../../composables/use-cockpit-theme';
import { formatDuration } from '../../utils/format';
import CockpitModal from './cockpit-modal.vue';

const props = withDefaults(
  defineProps<{ eventId?: null | string; open?: boolean }>(),
  { eventId: null, open: false },
);

const emit = defineEmits<{ close: [] }>();

const { severityColors } = useCockpitTheme();

const loading = ref(false);
const failed = ref(false);
const event = ref<AlertApi.EventItem | null>(null);

async function load() {
  if (!props.eventId) return;
  loading.value = true;
  failed.value = false;
  event.value = null;
  try {
    event.value = await getAlertEventApi(props.eventId);
  } catch {
    failed.value = true;
  } finally {
    loading.value = false;
  }
}

watch(
  () => [props.open, props.eventId],
  ([open]) => {
    if (open) load();
  },
  { immediate: true },
);

const SEVERITY_LABELS: Record<AlertApi.Severity, string> = {
  CRITICAL: '严重',
  ERROR: '错误',
  INFO: '提示',
  WARN: '警告',
};

const STATUS_LABELS: Record<AlertApi.EventStatus, string> = {
  ACKNOWLEDGED: '已确认',
  ACTIVE: '活跃',
  ARCHIVED: '已归档',
  RESOLVED: '已解决',
  SUPPRESSED: '已抑制',
};

const sevColor = computed(() =>
  event.value
    ? (severityColors.value[event.value.severity] ?? severityColors.value.INFO)
    : '',
);

/** 触发条件快照（规则定义 JSON，格式化只读展示） */
const conditionText = computed(() => {
  const snap = event.value?.triggerConditionSnapshot;
  if (!snap || Object.keys(snap).length === 0) return '';
  try {
    return JSON.stringify(snap, null, 2);
  } catch {
    return '';
  }
});

const duration = computed(() =>
  event.value
    ? formatDuration(event.value.triggeredAt, event.value.resolvedAt ?? undefined)
    : '—',
);
</script>

<template>
  <CockpitModal
    :open="open"
    :title="`预警事件详情 · ${event?.ruleName ?? event?.ruleCode ?? ''}`"
    @close="emit('close')"
  >
    <div v-if="loading" class="ed__state">加载中…</div>
    <div v-else-if="failed || !event" class="ed__state">事件详情加载失败</div>

    <div v-else class="ed">
      <!-- 头部：级别 + 规则 + 状态 -->
      <div class="ed__head">
        <span class="ed__sev" :style="{ background: sevColor }"></span>
        <div class="ed__head-main">
          <div class="ed__rule">{{ event.ruleName ?? event.ruleCode }}</div>
          <div class="ed__sub">
            {{ SEVERITY_LABELS[event.severity] ?? event.severity }} ·
            规则编码 {{ event.ruleCode }} · v{{ event.ruleVersion }}
          </div>
        </div>
        <span class="ed__status">{{ STATUS_LABELS[event.status] ?? event.status }}</span>
      </div>

      <!-- 指标网格 -->
      <div class="ed__meta">
        <div class="ed__meta-item">
          <span class="k">关联回路</span>
          <span class="v mono">{{ event.loopName ?? event.loopId ?? '—' }}</span>
        </div>
        <div class="ed__meta-item">
          <span class="k">触发值</span>
          <span class="v mono">
            {{ typeof event.triggeredValue === 'number' ? event.triggeredValue : '—' }}
          </span>
        </div>
        <div class="ed__meta-item">
          <span class="k">触发时间</span>
          <span class="v">{{ formatLocalTime(event.triggeredAt, 'YYYY-MM-DD HH:mm:ss') }}</span>
        </div>
        <div class="ed__meta-item">
          <span class="k">持续时长</span>
          <span class="v">{{ duration }}</span>
        </div>
        <div class="ed__meta-item">
          <span class="k">触发次数</span>
          <span class="v">{{ event.triggerCount }}</span>
        </div>
        <div class="ed__meta-item">
          <span class="k">可信度</span>
          <span class="v">{{ event.confidenceLevel ?? '—' }}</span>
        </div>
        <div v-if="event.acknowledgedAt" class="ed__meta-item">
          <span class="k">确认</span>
          <span class="v">
            {{ event.acknowledgedBy ?? '—' }} ·
            {{ formatLocalTime(event.acknowledgedAt, 'MM-DD HH:mm') }}
          </span>
        </div>
        <div v-if="event.resolvedAt" class="ed__meta-item">
          <span class="k">解决</span>
          <span class="v">
            {{ event.resolvedBy ?? '—' }} ·
            {{ formatLocalTime(event.resolvedAt, 'MM-DD HH:mm') }}
          </span>
        </div>
        <div v-if="event.resolutionNote" class="ed__meta-item span2">
          <span class="k">解决备注</span>
          <span class="v">{{ event.resolutionNote }}</span>
        </div>
      </div>

      <!-- 规则定义（触发条件快照） -->
      <template v-if="conditionText">
        <div class="ed__sec-hd">规则定义（触发条件快照）</div>
        <pre class="ed__json">{{ conditionText }}</pre>
      </template>
    </div>
  </CockpitModal>
</template>

<style scoped>
.ed__state {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 160px;
  font-size: 12px;
  color: var(--ck-text-3);
}

.ed__head {
  display: flex;
  gap: 12px;
  align-items: center;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--ck-border);
}

.ed__sev {
  flex: none;
  width: 6px;
  height: 36px;
  border-radius: 3px;
}

.ed__head-main {
  min-width: 0;
}

.ed__rule {
  overflow: hidden;
  font-size: 15px;
  font-weight: 600;
  color: var(--ck-text);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ed__sub {
  margin-top: 2px;
  font-size: 11px;
  color: var(--ck-text-3);
}

.ed__status {
  flex: none;
  padding: 2px 10px;
  font-size: 12px;
  color: var(--ck-text-2);
  background: var(--ck-panel-3);
  border-radius: 999px;
}

.ed__meta {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px 16px;
  margin-top: 12px;
}

.ed__meta-item {
  display: flex;
  gap: 8px;
  font-size: 12px;
}

.ed__meta-item.span2 {
  grid-column: span 2;
}

.ed__meta-item .k {
  flex: none;
  color: var(--ck-text-3);
}

.ed__meta-item .v {
  overflow: hidden;
  color: var(--ck-text);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mono {
  font-variant-numeric: tabular-nums;
}

.ed__sec-hd {
  margin: 14px 0 8px;
  font-size: 12px;
  font-weight: 600;
  color: var(--ck-text);
}

.ed__json {
  max-height: 240px;
  padding: 10px 12px;
  margin: 0;
  overflow: auto;
  font-size: 11px;
  line-height: 1.6;
  color: var(--ck-text-2);
  background: var(--ck-panel-2);
  border: 1px solid var(--ck-border);
  border-radius: 8px;
}
</style>
