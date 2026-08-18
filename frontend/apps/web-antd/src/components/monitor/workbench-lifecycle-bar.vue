/** * 工作台生命周期条（MW-P3-07） * * 两阶段状态可扫描：MONITOR → ASSESS *
当前/阻塞/超期状态有文字和图标，不只靠颜色。 * 点击阶段滚动到对应区。 * *
对齐整改方案 §7.2 生命周期状态。 */
<script lang="ts" setup>
import type { MonitorApi } from '#/api/monitor';

import { computed } from 'vue';

import { Tooltip } from 'ant-design-vue';

import { formatTime } from '#/utils/format';

defineOptions({ name: 'WorkbenchLifecycleBar' });

const props = defineProps<{
  lifecycle: MonitorApi.Lifecycle;
  /** 区级不可用标记（来自 summary.unavailableSections） */
  unavailableSections?: string[];
}>();

const emit = defineEmits<{
  (e: 'stageClick', stage: MonitorApi.LifecycleStageName): void;
}>();

const STAGE_META: Record<
  MonitorApi.LifecycleStageName,
  { icon: string; label: string }
> = {
  MONITOR: { icon: 'lucide:radio', label: '监控' },
  ASSESS: { icon: 'lucide:chart-column', label: '评估' },
};

const STATUS_META: Record<
  MonitorApi.LifecycleStageStatus,
  { cls: string; icon: string; text: string }
> = {
  NOT_STARTED: { cls: 'lcs--idle', icon: 'lucide:circle', text: '未开始' },
  READY: { cls: 'lcs--ready', icon: 'lucide:circle-dot', text: '就绪' },
  RUNNING: { cls: 'lcs--running', icon: 'lucide:loader', text: '进行中' },
  COMPLETED: { cls: 'lcs--done', icon: 'lucide:check', text: '已完成' },
  INCONCLUSIVE: {
    cls: 'lcs--warn',
    icon: 'lucide:alert-triangle',
    text: '证据不足',
  },
  BLOCKED: { cls: 'lcs--blocked', icon: 'lucide:ban', text: '阻塞' },
  OVERDUE: { cls: 'lcs--overdue', icon: 'lucide:clock-alert', text: '超期' },
  NOT_REQUIRED: { cls: 'lcs--idle', icon: 'lucide:minus', text: '不需要' },
};

/** 阶段是否不可用（对应来源失败） */
function isStageUnavailable(stage: MonitorApi.LifecycleStageName): boolean {
  const map: Record<MonitorApi.LifecycleStageName, string[]> = {
    MONITOR: ['runtime'],
    ASSESS: ['assessment'],
  };
  const sections = props.unavailableSections ?? [];
  return map[stage].some((s) => sections.includes(s));
}

const stageItems = computed(() =>
  props.lifecycle.stages.map((s) => {
    const meta = STAGE_META[s.stage];
    const statusMeta = STATUS_META[s.status];
    const isCurrent = props.lifecycle.currentStage === s.stage;
    const unavailable = isStageUnavailable(s.stage);
    return {
      ...s,
      label: meta.label,
      icon: meta.icon,
      statusText: unavailable ? '数据不可用' : statusMeta.text,
      statusIcon: unavailable ? 'lucide:cloud-off' : statusMeta.icon,
      statusCls: unavailable ? 'lcs--warn' : statusMeta.cls,
      isCurrent,
      unavailable,
    };
  }),
);

function handleClick(stage: MonitorApi.LifecycleStageName) {
  emit('stageClick', stage);
}
</script>

<template>
  <div class="lifecycle-bar" role="navigation" aria-label="回路生命周期">
    <template v-for="(item, idx) in stageItems" :key="item.stage">
      <Tooltip v-if="item.unavailable">
        <template #title>
          <span>该阶段数据来源暂时不可用（{{ item.reason }}）</span>
        </template>
        <div class="lcs lcs--warn" role="status">
          <span class="lcs__icon"
            ><span class="lcs__idx">{{ idx + 1 }}</span></span
          >
          <span class="lcs__label">{{ item.label }}</span>
          <span class="lcs__status">{{ item.statusText }}</span>
        </div>
      </Tooltip>
      <Tooltip v-else>
        <template #title>
          <div class="text-xs">
            <div>{{ item.label }}：{{ item.statusText }}</div>
            <div v-if="item.reason" class="text-gray-400">
              {{ item.reason }}
            </div>
            <div v-if="item.resultAt" class="text-gray-400">
              结果时间：{{ formatTime(item.resultAt) }}
            </div>
          </div>
        </template>
        <div
          class="lcs"
          :class="[item.statusCls, { 'lcs--current': item.isCurrent }]"
          role="status"
          :aria-current="item.isCurrent ? 'step' : undefined"
          tabindex="0"
          @click="handleClick(item.stage)"
          @keydown.enter="handleClick(item.stage)"
        >
          <span class="lcs__icon">
            <span class="lcs__idx">{{ idx + 1 }}</span>
          </span>
          <span class="lcs__label">{{ item.label }}</span>
          <span class="lcs__status">{{ item.statusText }}</span>
        </div>
      </Tooltip>
      <!-- 连接线 -->
      <span
        v-if="idx < stageItems.length - 1"
        class="lcs__connector"
        aria-hidden="true"
      ></span>
    </template>
  </div>
</template>

<style scoped>
.lifecycle-bar {
  display: flex;
  gap: 0;
  align-items: center;
  padding: 4px 8px;
  background: hsl(var(--card));
  border: 1px solid hsl(var(--border) / 60%);
  border-radius: 6px;
}

.lcs {
  display: flex;
  flex-shrink: 0;
  gap: 4px;
  align-items: center;
  padding: 2px 6px;
  font-size: 12px;
  cursor: pointer;
  border-radius: 4px;
  transition: background-color 0.15s;
}

.lcs:hover {
  background: hsl(var(--accent) / 10%);
}

.lcs:focus-visible {
  outline: 2px solid hsl(var(--primary));
  outline-offset: 1px;
}

.lcs__icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  font-size: 10px;
  font-weight: 600;
  color: hsl(var(--foreground) / 50%);
  background: hsl(var(--muted) / 50%);
  border-radius: 50%;
}

.lcs__label {
  font-weight: 500;
  color: hsl(var(--foreground) / 85%);
  white-space: nowrap;
}

.lcs__status {
  font-size: 11px;
  color: hsl(var(--foreground) / 50%);
  white-space: nowrap;
}

.lcs__connector {
  width: 12px;
  height: 1px;
  margin: 0 2px;
  background: hsl(var(--border));
}

/* 状态色（不只靠颜色，同时有文字和图标） */
.lcs--done .lcs__icon {
  color: hsl(var(--status-ok) / 90%);
  background: hsl(var(--status-ok) / 15%);
}

.lcs--ready .lcs__icon {
  color: hsl(var(--status-info) / 90%);
  background: hsl(var(--status-info) / 15%);
}

.lcs--running .lcs__icon {
  color: hsl(var(--status-info) / 90%);
  background: hsl(var(--status-info) / 15%);
}

.lcs--blocked .lcs__icon {
  color: hsl(var(--status-error) / 90%);
  background: hsl(var(--status-error) / 15%);
}

.lcs--overdue .lcs__icon {
  color: hsl(var(--status-error) / 90%);
  background: hsl(var(--status-error) / 15%);
}

.lcs--warn .lcs__icon {
  color: hsl(var(--status-warn) / 90%);
  background: hsl(var(--status-warn) / 15%);
}

.lcs--idle .lcs__icon {
  color: hsl(var(--foreground) / 40%);
  background: transparent;
}

/* 当前阶段高亮 */
.lcs--current {
  background: hsl(var(--accent) / 8%);
  box-shadow: inset 0 0 0 1px hsl(var(--primary) / 30%);
}

.lcs--current .lcs__label {
  font-weight: 600;
  color: hsl(var(--primary));
}
</style>
