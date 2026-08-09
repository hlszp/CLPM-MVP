/** * 工作台 Tracker/实施/验证时间线（MW-P3-08） * *
显示建单来源、状态变化、负责人、MOC、实施 PID、实施时间、验证计划和结果。 *
VERIFYING 超期显示超期时长和"立即验证"。 * CLOSED
显示改善/无变化/恶化结论；REOPENED 显示原因。 * 所有编辑动作复用 Tracker API
和权限，不另建状态机。 * 平台安全边界文案始终可见：只读建议、人工实施、需留痕。
* * 对齐整改方案 §7.1 闭环时间线。 */
<script lang="ts" setup>
import type { MonitorApi } from '#/api/monitor';

import { computed } from 'vue';

import { Button, Empty, Tag } from 'ant-design-vue';

import { formatTime } from '#/utils/format';

defineOptions({ name: 'WorkbenchTrackerTimeline' });

const props = defineProps<{
  /** Tracker 时间线数据（来自 summary.trackerTimeline） */
  tracker?: MonitorApi.TrackerTimeline | null;
  /** 是否数据来源不可用 */
  unavailable?: boolean;
}>();

const emit = defineEmits<{
  (e: 'verify', trackerId: string): void;
  (e: 'viewDetail', trackerId: string): void;
}>();

const STATUS_META: Record<string, { color: string; label: string }> = {
  PENDING: { color: 'default', label: '待处理' },
  IN_PROGRESS: { color: 'processing', label: '处理中' },
  VERIFYING: { color: 'warning', label: '验证中' },
  CLOSED: { color: 'success', label: '已闭环' },
  REOPENED: { color: 'error', label: '已重开' },
  IGNORED: { color: 'default', label: '已忽略' },
};

const statusMeta = computed(() => {
  if (!props.tracker) return null;
  return (
    STATUS_META[props.tracker.actionStatus] ?? {
      color: 'default',
      label: props.tracker.actionStatus,
    }
  );
});

const isVerifying = computed(() => props.tracker?.actionStatus === 'VERIFYING');

const isClosed = computed(() => props.tracker?.actionStatus === 'CLOSED');

const isReopened = computed(() => props.tracker?.actionStatus === 'REOPENED');

const effectConclusion = computed(() => {
  if (!props.tracker?.abCompareSummary) return null;
  return props.tracker.abCompareSummary;
});

function pidText(pid?: null | { d?: number; i?: number; p?: number }): string {
  if (!pid) return '—';
  return `P=${pid.p ?? '—'}, I=${pid.i ?? '—'}, D=${pid.d ?? '—'}`;
}

function goToTrackerDetail(trackerId: string) {
  emit('viewDetail', trackerId);
}

function handleVerify() {
  if (props.tracker) {
    emit('verify', props.tracker.trackerId);
  }
}
</script>

<template>
  <div class="tracker-timeline" role="region" aria-label="闭环时间线">
    <!-- 数据不可用 -->
    <div v-if="unavailable" class="tracker-timeline__unavailable">
      <span class="text-amber-600">闭环时间线数据暂时不可用</span>
    </div>

    <!-- 无 Tracker -->
    <Empty
      v-else-if="!tracker"
      description="暂无整改工单"
      :image="Empty.PRESENTED_IMAGE_SIMPLE"
      class="!py-4"
    />

    <!-- Tracker 时间线 -->
    <div v-else class="tracker-timeline__body">
      <!-- 顶部：状态 + 标签 + 操作 -->
      <div class="tracker-timeline__header">
        <div class="tracker-timeline__header-left">
          <Tag
            v-if="statusMeta"
            :color="statusMeta.color"
            class="!m-0 !text-[11px]"
          >
            {{ statusMeta.label }}
          </Tag>
          <span v-if="tracker.diagnosisLabel" class="tracker-timeline__label">
            {{ tracker.diagnosisLabel }}
          </span>
          <span v-if="tracker.severity" class="tracker-timeline__severity">
            严重度：{{ tracker.severity }}
          </span>
          <!-- VERIFYING 超期标记 -->
          <Tag v-if="tracker.isOverdue" color="error" class="!m-0 !text-[10px]">
            超期 {{ tracker.overdueHours?.toFixed(0) ?? '' }}h
          </Tag>
        </div>
        <div class="tracker-timeline__header-right">
          <Button
            size="small"
            type="link"
            @click="goToTrackerDetail(tracker.trackerId)"
          >
            详情
          </Button>
          <Button
            v-if="isVerifying"
            size="small"
            type="primary"
            @click="handleVerify"
          >
            {{ tracker.isOverdue ? '立即验证' : '进入验证' }}
          </Button>
        </div>
      </div>

      <!-- 时间线节点 -->
      <div class="tracker-timeline__steps">
        <!-- 建单 -->
        <div class="tt-step tt-step--done">
          <span class="tt-step__dot"></span>
          <div class="tt-step__content">
            <span class="tt-step__label">建单</span>
            <span class="tt-step__time">{{
              formatTime(tracker.createdAt) || '—'
            }}</span>
            <span v-if="tracker.triggerType" class="tt-step__meta">
              来源：{{ tracker.triggerType === 'auto' ? '自动' : '手动' }}
            </span>
            <span v-if="tracker.assignee" class="tt-step__meta">
              负责人：{{ tracker.assignee }}
            </span>
          </div>
        </div>

        <!-- 实施 -->
        <div
          class="tt-step"
          :class="{
            'tt-step--done': tracker.implementedAt,
            'tt-step--pending': !tracker.implementedAt,
          }"
        >
          <span class="tt-step__dot"></span>
          <div class="tt-step__content">
            <span class="tt-step__label">人工实施</span>
            <span class="tt-step__time">{{
              formatTime(tracker.implementedAt) || '待实施'
            }}</span>
            <span v-if="tracker.implementedBy" class="tt-step__meta">
              实施人：{{ tracker.implementedBy }}
            </span>
            <span v-if="tracker.newPid" class="tt-step__meta">
              实施 PID：{{ pidText(tracker.newPid) }}
            </span>
            <!-- MOC 信息 -->
            <span v-if="tracker.mocNotApplicable" class="tt-step__meta">
              MOC：不适用
            </span>
            <span v-else-if="tracker.mocRef" class="tt-step__meta">
              MOC：{{ tracker.mocRef }}
            </span>
          </div>
        </div>

        <!-- 验证 -->
        <div
          class="tt-step"
          :class="{
            'tt-step--done':
              isClosed || (tracker.effectVerifiedAt && !isReopened),
            'tt-step--pending': isVerifying && !tracker.isOverdue,
            'tt-step--overdue': isVerifying && tracker.isOverdue,
            'tt-step--failed': isReopened,
          }"
        >
          <span class="tt-step__dot"></span>
          <div class="tt-step__content">
            <span class="tt-step__label">
              {{ isClosed ? '效果验证' : isReopened ? '验证失败' : '效果验证' }}
            </span>
            <span class="tt-step__time">{{
              formatTime(tracker.effectVerifiedAt || tracker.closedAt) ||
              (isVerifying ? '等待验证' : '—')
            }}</span>
            <!-- CLOSED 结论 -->
            <span v-if="isClosed && effectConclusion" class="tt-step__meta">
              结论：{{ effectConclusion }}
            </span>
            <!-- REOPENED 原因 -->
            <span
              v-if="isReopened && tracker.reopenReason"
              class="tt-step__meta tt-step__meta--error"
            >
              重开原因：{{ tracker.reopenReason }}
            </span>
            <!-- 计划验证时间 -->
            <span v-if="tracker.plannedAt && !isClosed" class="tt-step__meta">
              计划验证：{{ formatTime(tracker.plannedAt) }}
            </span>
          </div>
        </div>
      </div>

      <!-- 安全边界文案（始终可见） -->
      <div class="tracker-timeline__safety" role="note">
        安全边界：只读建议 · 人工实施 · 需留痕；平台不直接修改 DCS 的 P/I/D
        参数。
      </div>
    </div>
  </div>
</template>

<style scoped>
.tracker-timeline {
  height: 100%;
  min-height: 0;
  padding: 6px 10px;
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border) / 60%);
  border-radius: 6px;
}

.tracker-timeline__unavailable {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 12px;
  font-size: 12px;
}

.tracker-timeline__body {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

.tracker-timeline__header {
  display: flex;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
}

.tracker-timeline__header-left {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}

.tracker-timeline__header-right {
  display: flex;
  flex-shrink: 0;
  gap: 4px;
  align-items: center;
}

.tracker-timeline__label {
  font-size: 12px;
  font-weight: 500;
  color: hsl(var(--foreground) / 85%);
}

.tracker-timeline__severity {
  font-size: 11px;
  color: hsl(var(--foreground) / 50%);
}

/* 时间线步骤 */
.tracker-timeline__steps {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 0;
  min-height: 0;
  overflow: auto;
}

.tt-step {
  position: relative;
  display: flex;
  gap: 8px;
  padding: 2px 0;
  padding-left: 4px;
}

.tt-step:not(:last-child)::before {
  position: absolute;
  top: 14px;
  bottom: -2px;
  left: 7px;
  width: 1px;
  content: '';
  background: hsl(var(--border));
}

.tt-step__dot {
  flex-shrink: 0;
  width: 8px;
  height: 8px;
  margin-top: 4px;
  background: hsl(var(--muted));
  border: 1px solid hsl(var(--border));
  border-radius: 50%;
}

.tt-step--done .tt-step__dot {
  background: hsl(var(--status-ok));
  border-color: hsl(var(--status-ok));
}

.tt-step--pending .tt-step__dot {
  background: hsl(var(--status-info) / 30%);
  border-color: hsl(var(--status-info));
}

.tt-step--overdue .tt-step__dot {
  background: hsl(var(--status-error));
  border-color: hsl(var(--status-error));
}

.tt-step--failed .tt-step__dot {
  background: hsl(var(--status-error));
  border-color: hsl(var(--status-error));
}

.tt-step__content {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 1px;
  min-width: 0;
}

.tt-step__label {
  font-size: 12px;
  font-weight: 500;
  color: hsl(var(--foreground) / 85%);
}

.tt-step__time {
  font-size: 11px;
  color: hsl(var(--foreground) / 60%);
}

.tt-step__meta {
  font-size: 11px;
  color: hsl(var(--foreground) / 45%);
}

.tt-step__meta--error {
  color: hsl(var(--status-error) / 80%);
}

/* 安全边界文案 */
.tracker-timeline__safety {
  flex-shrink: 0;
  padding: 2px 6px;
  margin-top: 4px;
  font-size: 10px;
  color: hsl(var(--foreground) / 40%);
  text-align: center;
  background: hsl(var(--muted) / 30%);
  border-radius: 3px;
}
</style>
