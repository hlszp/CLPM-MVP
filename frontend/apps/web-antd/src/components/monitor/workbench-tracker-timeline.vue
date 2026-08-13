<script lang="ts" setup>
/**
 * 工作台闭环时间线（MW-P3-08 + MW-P3-09 · Phase 2 重构）
 *
 * 固定 4 节点闭环：评估 → 诊断 → 处置 → 验证
 * - 处置节点聚合整定/维修/实施等动作
 * - 每个节点独立显示时间、状态、关键信息
 * - 节点间用连接线标识流程进度
 */
import type { MonitorApi } from '#/api/monitor';

import { computed } from 'vue';

import { Button, Empty, Tag } from 'ant-design-vue';

import { formatTime } from '#/utils/format';

defineOptions({ name: 'WorkbenchTrackerTimeline' });

const props = defineProps<{
  /** Tracker 时间线数据（来自 summary.trackerTimeline） */
  tracker?: MonitorApi.TrackerTimeline | null;
  /** 评估摘要 */
  assessment?: MonitorApi.AssessmentSummary | null;
  /** 诊断摘要 */
  diagnosis?: MonitorApi.DiagnosisSummary | null;
  /** 整定摘要 */
  tuning?: MonitorApi.TuningSummary | null;
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

/** 4 个固定节点定义 */
type NodeKey = 'assess' | 'diagnose' | 'action' | 'verify';

interface TimelineNode {
  key: NodeKey;
  label: string;
  icon: string;
  state: 'done' | 'current' | 'pending' | 'skipped' | 'failed';
  stateLabel: string;
  time: string;
  details: { label: string; value: string; highlight?: boolean }[];
}

/** 根据数据推导 4 节点状态 */
const nodes = computed<TimelineNode[]>(() => {
  const t = props.tracker;
  const a = props.assessment;
  const d = props.diagnosis;
  const tu = props.tuning;

  const actionStatus = t?.actionStatus ?? 'PENDING';
  const isVerifying = actionStatus === 'VERIFYING';
  const isClosed = actionStatus === 'CLOSED';
  const isReopened = actionStatus === 'REOPENED';
  const hasImplemented = !!t?.implementedAt;
  const hasDiagnosis = !!d;
  const hasAssessment = !!a;
  const hasTuning = !!tu;

  // 评估节点
  const assessState = hasAssessment ? 'done' as const : 'pending' as const;
  const assess: TimelineNode = {
    key: 'assess',
    label: '评估',
    icon: '📊',
    state: assessState,
    stateLabel: hasAssessment ? '已完成' : '待评估',
    time: a?.resultAt ? formatTime(a.resultAt) : '—',
    details: [
      { label: '评分', value: a?.score != null ? a.score.toFixed(1) : '—' },
      { label: '可信度', value: a?.confidenceLevel ?? '—' },
      { label: '状态', value: a?.status ?? '—' },
    ].filter((x) => x.value !== '—'),
  };

  // 诊断节点
  const diagState: TimelineNode['state'] = hasDiagnosis
    ? 'done'
    : hasAssessment
      ? 'pending'
      : 'skipped';
  const diag: TimelineNode = {
    key: 'diagnose',
    label: '诊断',
    icon: '🔍',
    state: diagState,
    stateLabel: hasDiagnosis ? '已诊断' : '待诊断',
    time: d?.resultAt ? formatTime(d.resultAt) : '—',
    details: [
      { label: '诊断', value: d?.diagLabel ?? t?.diagnosisLabel ?? '—', highlight: true },
      { label: '置信度', value: d?.confidence != null ? d.confidence.toFixed(2) : '—' },
    ].filter((x) => x.value !== '—'),
  };

  // 处置节点（整定 + 实施 + 维修）
  const actionState: TimelineNode['state'] = hasImplemented
    ? 'done'
    : isVerifying || isClosed
      ? 'done'
      : hasDiagnosis
        ? 'current'
        : 'skipped';

  const actionDetails: TimelineNode['details'] = [];
  if (hasTuning) {
    actionDetails.push({
      label: '整定算法',
      value: tu?.algorithm ?? tu?.modelType ?? '—',
      highlight: true,
    });
    if (tu?.recommendedPid) {
      const pid = tu.recommendedPid;
      actionDetails.push({
        label: '推荐 PID',
        value: `P=${pid.p ?? '—'}, I=${pid.i ?? '—'}, D=${pid.d ?? '—'}`,
      });
    }
    actionDetails.push({
      label: '拟合度',
      value: tu?.fittingScore != null ? `${(tu.fittingScore * 100).toFixed(1)}%` : '—',
    });
  }
  if (hasImplemented) {
    actionDetails.push({
      label: '实施人',
      value: t?.implementedBy ?? '—',
    });
    if (t?.newPid) {
      const pid = t.newPid;
      actionDetails.push({
        label: '实施 PID',
        value: `P=${pid.p ?? '—'}, I=${pid.i ?? '—'}, D=${pid.d ?? '—'}`,
      });
    }
    if (t?.mocRef) {
      actionDetails.push({ label: 'MOC', value: t.mocRef });
    }
  }

  const action: TimelineNode = {
    key: 'action',
    label: '处置',
    icon: '⚙️',
    state: actionState,
    stateLabel: hasImplemented
      ? '已实施'
      : isVerifying || isClosed
        ? '已实施'
        : hasTuning
          ? '整定中'
          : '待处置',
    time: hasImplemented ? formatTime(t?.implementedAt) : '—',
    details: actionDetails.filter((x) => x.value !== '—'),
  };

  // 验证节点
  const hasEffectVerified = !!t?.effectVerifiedAt || isClosed;

  let verifyState: TimelineNode['state'] = 'pending';
  let verifyLabel = '待验证';
  if (isVerifying && !t?.isOverdue) {
    verifyState = 'current';
    verifyLabel = '验证中';
  } else if (isVerifying && t?.isOverdue) {
    verifyState = 'current';
    verifyLabel = '验证超期';
  } else if (isClosed || hasEffectVerified) {
    verifyState = 'done';
    verifyLabel = '已验证';
  } else if (isReopened) {
    verifyState = 'failed';
    verifyLabel = '验证失败';
  } else if (hasImplemented) {
    verifyState = 'pending';
    verifyLabel = '待验证';
  } else {
    verifyState = 'skipped';
    verifyLabel = '待验证';
  }

  const verifyDetails: TimelineNode['details'] = [];
  if (t?.effectCompare?.conclusionLabel) {
    verifyDetails.push({
      label: '结论',
      value: t.effectCompare.conclusionLabel,
      highlight: true,
    });
  } else if (t?.effectCompare?.conclusion) {
    const labelMap: Record<string, string> = {
      IMPROVED: '改善',
      NO_CHANGE: '无明显变化',
      DETERIORATED: '恶化',
    };
    verifyDetails.push({
      label: '结论',
      value: labelMap[t.effectCompare.conclusion] ?? t.effectCompare.conclusion,
      highlight: true,
    });
  }
  if (t?.effectCompare?.scoreChange) {
    const sc = t.effectCompare.scoreChange;
    const change = sc.change != null ? `${sc.change > 0 ? '+' : ''}${sc.change.toFixed(1)}` : '—';
    verifyDetails.push({
      label: '评分',
      value: `${sc.before?.toFixed(1) ?? '—'} → ${sc.after?.toFixed(1) ?? '—'} (${change})`,
    });
  }
  if (t?.isOverdue && isVerifying) {
    verifyDetails.push({
      label: '超期',
      value: `${t.overdueHours?.toFixed(0) ?? ''}h`,
    });
  }

  const verify: TimelineNode = {
    key: 'verify',
    label: '验证',
    icon: '✅',
    state: verifyState as TimelineNode['state'],
    stateLabel: verifyLabel,
    time:
      t?.effectVerifiedAt
        ? formatTime(t.effectVerifiedAt)
        : t?.closedAt
          ? formatTime(t.closedAt)
          : t?.plannedAt
            ? `计划 ${formatTime(t.plannedAt)}`
            : '—',
    details: verifyDetails.filter((x) => x.value !== '—'),
  };

  return [assess, diag, action, verify];
});

const hasAnyData = computed(
  () =>
    !!props.assessment ||
    !!props.diagnosis ||
    !!props.tuning ||
    !!props.tracker,
);

const isVerifying = computed(() => props.tracker?.actionStatus === 'VERIFYING');

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

    <!-- 无数据 -->
    <Empty
      v-else-if="!hasAnyData"
      description="暂无闭环数据"
      :image="Empty.PRESENTED_IMAGE_SIMPLE"
      class="!py-4"
    />

    <!-- 4 节点闭环时间线 -->
    <div v-else class="tracker-timeline__body">
      <!-- 顶部：工单状态 + 操作 -->
      <div v-if="tracker" class="tracker-timeline__header">
        <div class="tracker-timeline__header-left">
          <Tag
            v-if="STATUS_META[tracker.actionStatus]"
            :color="STATUS_META[tracker.actionStatus]?.color ?? 'default'"
            class="!m-0 !text-[11px]"
          >
            {{ STATUS_META[tracker.actionStatus]?.label ?? tracker.actionStatus }}
          </Tag>
          <span v-if="tracker.diagnosisLabel" class="tracker-timeline__label">
            {{ tracker.diagnosisLabel }}
          </span>
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

      <!-- 4 节点时间线 -->
      <div class="tt-flow">
        <template v-for="(node, idx) in nodes" :key="node.key">
          <!-- 连接线（除最后一个节点） -->
          <div
            v-if="idx < nodes.length - 1"
            class="tt-flow__connector"
            :class="{
              'tt-flow__connector--done':
                node.state === 'done' || node.state === 'current',
            }"
          ></div>
          <!-- 节点 -->
          <div
            class="tt-node"
            :class="`tt-node--${node.state}`"
          >
            <div class="tt-node__head">
              <span class="tt-node__icon">{{ node.icon }}</span>
              <span class="tt-node__label">{{ node.label }}</span>
              <span class="tt-node__state">{{ node.stateLabel }}</span>
            </div>
            <div class="tt-node__body">
              <span v-if="node.time !== '—'" class="tt-node__time">{{ node.time }}</span>
              <span v-for="(d, di) in node.details" :key="di" class="tt-node__detail" :class="{ 'tt-node__detail--highlight': d.highlight }">
                <span class="tt-node__detail-label">{{ d.label }}</span>
                <span class="tt-node__detail-value">{{ d.value }}</span>
              </span>
              <span v-if="node.details.length === 0 && node.time === '—'" class="tt-node__empty">暂无数据</span>
            </div>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
.tracker-timeline {
  height: 100%;
  min-height: 0;
  padding: 4px 8px;
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
  flex-shrink: 0;
  gap: 8px;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 4px;
}

.tracker-timeline__header-left {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
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

/* ===== 4 节点时间流 ===== */
.tt-flow {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 0;
  min-height: 0;
  overflow-y: auto;
}

.tt-flow__connector {
  position: relative;
  width: 2px;
  height: 10px;
  margin-left: 9px;
  background: hsl(var(--border) / 60%);
}

.tt-flow__connector--done {
  background: hsl(var(--status-ok) / 60%);
}

.tt-node {
  position: relative;
  padding: 2px 0;
  padding-left: 20px;
}

/* 节点圆点 */
.tt-node::before {
  position: absolute;
  top: 6px;
  left: 4px;
  width: 12px;
  height: 12px;
  content: '';
  background: hsl(var(--muted));
  border: 2px solid hsl(var(--border));
  border-radius: 50%;
}

.tt-node--done::before {
  background: hsl(var(--status-ok));
  border-color: hsl(var(--status-ok));
}

.tt-node--current::before {
  background: hsl(var(--status-info) / 40%);
  border-color: hsl(var(--status-info));
  box-shadow: 0 0 0 3px hsl(var(--status-info) / 20%);
}

.tt-node--pending::before {
  background: hsl(var(--muted));
  border-color: hsl(var(--border));
}

.tt-node--skipped::before {
  background: transparent;
  border-color: hsl(var(--border) / 40%);
  border-style: dashed;
}

.tt-node--failed::before {
  background: hsl(var(--status-error));
  border-color: hsl(var(--status-error));
}

.tt-node__head {
  display: flex;
  gap: 4px;
  align-items: center;
}

.tt-node__icon {
  font-size: 12px;
}

.tt-node__label {
  font-size: 12px;
  font-weight: 600;
  color: hsl(var(--foreground) / 85%);
}

.tt-node__state {
  font-size: 10px;
  color: hsl(var(--foreground) / 50%);
}

.tt-node--done .tt-node__state {
  color: hsl(var(--status-ok));
}

.tt-node--current .tt-node__state {
  color: hsl(var(--status-info));
}

.tt-node--failed .tt-node__state {
  color: hsl(var(--status-error));
}

.tt-node__body {
  display: flex;
  flex-direction: column;
  gap: 1px;
  padding-left: 2px;
  margin-top: 2px;
  margin-bottom: 2px;
}

.tt-node__time {
  font-size: 10px;
  color: hsl(var(--foreground) / 50%);
}

.tt-node__detail {
  display: flex;
  gap: 4px;
  align-items: baseline;
  font-size: 11px;
}

.tt-node__detail-label {
  flex-shrink: 0;
  color: hsl(var(--foreground) / 50%);
}

.tt-node__detail-value {
  color: hsl(var(--foreground) / 80%);
}

.tt-node__detail--highlight .tt-node__detail-value {
  font-weight: 600;
  color: hsl(var(--foreground) / 90%);
}

.tt-node__empty {
  font-size: 11px;
  color: hsl(var(--foreground) / 30%);
}

.tt-node--skipped .tt-node__body {
  opacity: 0.5;
}
</style>
