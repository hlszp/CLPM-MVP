<template>
  <div class="clpm-disposition-timeline">
    <!-- 空状态 -->
    <ClpmEmptyState v-if="!events.length" scene="tracker" />

    <!-- 时间线 -->
    <Timeline v-else>
      <TimelineItem
        v-for="event in sortedEvents"
        :key="event.eventId"
        :color="getEventColor(event.eventType)"
      >
        <template #dot>
          <div
            class="clpm-disposition-timeline__dot"
            :class="`clpm-disposition-timeline__dot--${event.eventType}`"
          >
            <IconifyIcon :icon="getEventIcon(event.eventType)" :size="14" />
          </div>
        </template>

        <div class="clpm-disposition-timeline__content">
          <!-- 时间戳 -->
          <div class="clpm-disposition-timeline__time">
            {{ formatTime(event.timestamp) }}
            <span class="clpm-disposition-timeline__actor" v-if="event.actor">
              <template v-if="event.actor === 'system'">
                <IconifyIcon
                  icon="lucide:bot"
                  :size="12"
                  style="margin-right: 2px; vertical-align: -1px"
                />
                系统
              </template>
              <template v-else>
                <IconifyIcon
                  icon="lucide:user"
                  :size="12"
                  style="margin-right: 2px; vertical-align: -1px"
                />
                {{ event.actor }}
              </template>
            </span>
          </div>

          <!-- 标题 -->
          <div class="clpm-disposition-timeline__title">
            {{ event.title }}
          </div>

          <!-- 描述 -->
          <div class="clpm-disposition-timeline__desc" v-if="event.description">
            {{ event.description }}
          </div>

          <!-- 元信息标签 -->
          <div class="clpm-disposition-timeline__meta" v-if="hasMeta(event)">
            <!-- 诊断标签 -->
            <Tag
              v-if="event.meta.labelName"
              color="blue"
              style="margin-right: 4px"
            >
              {{ event.meta.labelName }}
            </Tag>
            <!-- 置信度 -->
            <Tag
              v-if="event.meta.confidence != null"
              :color="confidenceColor(event.meta.confidence)"
            >
              置信度 {{ (event.meta.confidence * 100).toFixed(0) }}%
            </Tag>
            <!-- 严重度 -->
            <ClpmSeverityBadge
              v-if="event.meta.severity"
              :severity="event.meta.severity"
              size="small"
            />
            <!-- MOC号 -->
            <Tag
              v-if="event.meta.mocRef"
              color="purple"
              style="margin-left: 4px"
            >
              MOC: {{ event.meta.mocRef }}
            </Tag>
            <!-- 新PID参数 -->
            <span
              v-if="
                event.meta.newPid &&
                (event.meta.newPid.p != null || event.meta.newPid.i != null)
              "
              class="clpm-disposition-timeline__pid-tag"
            >
              <IconifyIcon
                icon="lucide:sliders-horizontal"
                :size="12"
                style="margin-right: 2px"
              />
              P={{ event.meta.newPid.p ?? '-' }}, Ti={{
                event.meta.newPid.i ?? '-'
              }}s
              <template
                v-if="event.meta.newPid.d != null && event.meta.newPid.d !== 0"
                >, Td={{ event.meta.newPid.d }}s</template
              >
            </span>
          </div>

          <!-- 操作按钮（可由外部slot覆盖） -->
          <div
            class="clpm-disposition-timeline__actions"
            v-if="event.eventType === 'implemented' && pendingVerificationAt"
          >
            <Tag color="processing" style="margin-top: 8px">
              <IconifyIcon
                icon="lucide:clock"
                :size="12"
                style="margin-right: 4px; vertical-align: -1px"
              />
              预计 {{ formatRelative(pendingVerificationAt) }} 自动验证
            </Tag>
            <slot name="verify-now" :event="event" />
          </div>
        </div>
      </TimelineItem>

      <!-- 待验证占位（当前状态为VERIFYING时显示） -->
      <TimelineItem v-if="showPendingNode" color="gray">
        <template #dot>
          <div
            class="clpm-disposition-timeline__dot clpm-disposition-timeline__dot--pending"
          >
            <IconifyIcon icon="lucide:loader-2" :size="14" class="clpm-spin" />
          </div>
        </template>
        <div class="clpm-disposition-timeline__content">
          <div class="clpm-disposition-timeline__time">等待验证中</div>
          <div
            class="clpm-disposition-timeline__title clpm-disposition-timeline__title--pending"
          >
            系统将在实施满
            {{ verificationIntervalHours }} 小时后自动抓取数据进行A/B对比验证
          </div>
          <div
            class="clpm-disposition-timeline__desc"
            style="color: hsl(var(--foreground) / 50%)"
          >
            您也可以手动触发立即对比，但建议等待足够时间积累数据以获得可信结论
          </div>
        </div>
      </TimelineItem>
    </Timeline>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue';

import { IconifyIcon } from '@vben/icons';
import { Tag, Timeline, TimelineItem } from 'ant-design-vue';

import { ClpmEmptyState, ClpmSeverityBadge } from '#/components/clpm';

export interface TimelineEvent {
  eventId: string;
  eventType:
    | 'diagnosis_detected'
    | 'claimed'
    | 'comment'
    | 'tuning_completed'
    | 'implemented'
    | 'verification_passed'
    | 'verification_failed'
    | 'ignored'
    | 'moc_recorded';
  timestamp: string;
  actor?: string | null;
  title: string;
  description?: string | null;
  meta: Record<string, any>;
}

interface Props {
  /** 事件列表（按时间升序排列） */
  events: TimelineEvent[];
  /** 当前跟踪状态 */
  currentStatus?: string | null;
  /** 预计自动验证时间 */
  pendingVerificationAt?: string | null;
  /** 验证间隔（小时），默认24 */
  verificationIntervalHours?: number;
}

const props = withDefaults(defineProps<Props>(), {
  currentStatus: null,
  pendingVerificationAt: null,
  verificationIntervalHours: 24,
});

// 按时间升序
const sortedEvents = computed(() => {
  return [...props.events].toSorted((a, b) =>
    a.timestamp.localeCompare(b.timestamp),
  );
});

// 是否显示待验证节点（最后一个事件是implemented且当前状态为VERIFYING）
const showPendingNode = computed(() => {
  if (
    props.currentStatus !== 'VERIFYING' &&
    props.currentStatus !== 'IMPLEMENTED'
  )
    return false;
  const last = sortedEvents.value[sortedEvents.value.length - 1];
  return last && last.eventType === 'implemented';
});

// 事件类型 → 图标
function getEventIcon(type: TimelineEvent['eventType']): string {
  const map: Record<string, string> = {
    diagnosis_detected: 'lucide:alert-triangle',
    claimed: 'lucide:user-check',
    comment: 'lucide:message-square',
    tuning_completed: 'lucide:wrench',
    implemented: 'lucide:check-circle-2',
    verification_passed: 'lucide:check-circle',
    verification_failed: 'lucide:rotate-ccw',
    ignored: 'lucide:eye-off',
    moc_recorded: 'lucide:file-check-2',
  };
  return map[type] || 'lucide:circle';
}

// 事件类型 → 颜色（ant timeline color 属性）
function getEventColor(type: TimelineEvent['eventType']): string {
  const map: Record<string, string> = {
    diagnosis_detected: 'red',
    claimed: 'blue',
    comment: 'gray',
    tuning_completed: 'gold',
    implemented: 'orange',
    verification_passed: 'green',
    verification_failed: 'red',
    ignored: 'gray',
    moc_recorded: 'purple',
  };
  return map[type] || 'blue';
}

// 置信度颜色标签
function confidenceColor(conf: number): string {
  if (conf >= 0.95) return 'green';
  if (conf >= 0.8) return 'blue';
  if (conf >= 0.6) return 'gold';
  if (conf >= 0.2) return 'orange';
  return 'red';
}

function hasMeta(event: TimelineEvent): boolean {
  const m = event.meta || {};
  return !!(
    m.labelName ||
    m.confidence != null ||
    m.severity ||
    m.mocRef ||
    (m.newPid && (m.newPid.p != null || m.newPid.i != null))
  );
}

function formatTime(ts: string): string {
  if (!ts) return '';
  const d = new Date(ts);
  if (isNaN(d.getTime())) return ts;
  const pad = (n: number) => n.toString().padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function formatRelative(ts: string): string {
  if (!ts) return '';
  const target = new Date(ts).getTime();
  const now = Date.now();
  const diff = target - now;
  if (diff <= 0) return '即将';
  const hours = Math.ceil(diff / 3600000);
  if (hours < 24) return `${hours}小时后`;
  const days = Math.ceil(hours / 24);
  return `${days}天后`;
}
</script>

<style scoped>
.clpm-disposition-timeline {
  padding: 8px 0;
}

.clpm-disposition-timeline__dot {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  color: white;
  background: hsl(var(--primary));
  border: 2px solid hsl(var(--background));
  border-radius: 50%;
  box-shadow: 0 0 0 1px hsl(var(--border));
}

.clpm-disposition-timeline__dot--diagnosis_detected {
  background: hsl(var(--status-error));
}

.clpm-disposition-timeline__dot--claimed {
  background: hsl(var(--status-info));
}

.clpm-disposition-timeline__dot--comment {
  background: hsl(var(--foreground) / 40%);
}

.clpm-disposition-timeline__dot--tuning_completed {
  background: hsl(var(--status-warning));
}

.clpm-disposition-timeline__dot--implemented {
  background: hsl(28deg 90% 50%);
}

.clpm-disposition-timeline__dot--verification_passed {
  background: hsl(var(--status-ok));
}

.clpm-disposition-timeline__dot--verification_failed {
  background: hsl(var(--status-error));
}

.clpm-disposition-timeline__dot--ignored {
  background: hsl(var(--foreground) / 30%);
}

.clpm-disposition-timeline__dot--moc_recorded {
  background: hsl(270deg 70% 55%);
}

.clpm-disposition-timeline__dot--pending {
  background: hsl(var(--foreground) / 25%);
}

.clpm-spin {
  animation: clpm-spin 1.2s linear infinite;
}

@keyframes clpm-spin {
  from {
    transform: rotate(0deg);
  }

  to {
    transform: rotate(360deg);
  }
}

.clpm-disposition-timeline__content {
  padding-bottom: 20px;
}

.clpm-disposition-timeline__time {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 4px;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  color: hsl(var(--foreground) / 50%);
}

.clpm-disposition-timeline__actor {
  display: inline-flex;
  align-items: center;
  font-size: 12px;
  color: hsl(var(--foreground) / 50%);
}

.clpm-disposition-timeline__title {
  margin-bottom: 4px;
  font-size: 14px;
  font-weight: 600;
  line-height: 1.5;
  color: hsl(var(--foreground));
}

.clpm-disposition-timeline__title--pending {
  font-weight: 500;
  color: hsl(var(--foreground) / 60%);
}

.clpm-disposition-timeline__desc {
  margin-bottom: 6px;
  font-size: 13px;
  line-height: 1.6;
  color: hsl(var(--foreground) / 70%);
}

.clpm-disposition-timeline__meta {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  align-items: center;
  margin-top: 4px;
}

.clpm-disposition-timeline__pid-tag {
  display: inline-flex;
  align-items: center;
  padding: 1px 8px;
  margin-left: 4px;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  color: hsl(28deg 90% 40%);
  background: hsl(28deg 90% 50% / 10%);
  border: 1px solid hsl(28deg 90% 50% / 20%);
  border-radius: 4px;
}

.clpm-disposition-timeline__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin-top: 6px;
}
</style>
