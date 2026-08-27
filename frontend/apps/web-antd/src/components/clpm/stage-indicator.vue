<script lang="ts" setup>
/**
 * ClpmStageIndicator - 管理成熟度阶段标识器（S1/S2/S3）
 *
 * 设计文档：docs/设计文档/IA 优化/CLPM-IA优化实施方案-0822.md §2.5
 * 显示在统计报告-管理总览标题旁：
 * - S1 基础可视（slate 中性灰）
 * - S2 闭环管理（amber 琥珀）
 * - S3 持续优化（emerald 绿）
 * locked=true 时显示锁图标（管理员锁定阶段预览）。
 * 颜色走 CSS 变量 token，不使用五色渐变。
 */
import { computed } from 'vue';

import { IconifyIcon } from '@vben/icons';

defineOptions({ name: 'ClpmStageIndicator' });

const props = withDefaults(
  defineProps<{
    locked?: boolean;
    size?: 'middle' | 'small';
    stage?: 'S1' | 'S2' | 'S3';
  }>(),
  { stage: 'S1', size: 'middle', locked: false },
);

const STAGE_META = {
  S1: { label: '基础可视', color: 'var(--color-slate-600)', bg: 'var(--color-slate-100)', dot: 'var(--color-slate-500)' },
  S2: { label: '闭环管理', color: 'var(--color-amber-700)', bg: 'var(--color-amber-50)', dot: 'var(--color-amber-500)' },
  S3: { label: '持续优化', color: 'var(--color-emerald-700)', bg: 'var(--color-emerald-50)', dot: 'var(--color-emerald-500)' },
} as const;

const meta = computed(() => STAGE_META[props.stage]);
</script>

<template>
  <span
    class="clpm-stage-indicator"
    :class="[`is-${size}`, { 'is-locked': locked }]"
    :style="{ color: meta.color, background: meta.bg }"
  >
    <span class="clpm-stage-indicator__dot" :style="{ background: meta.dot }"></span>
    <span class="clpm-stage-indicator__text">{{ stage }} {{ meta.label }}</span>
    <IconifyIcon v-if="locked" icon="lucide:lock" class="clpm-stage-indicator__lock" />
  </span>
</template>

<style scoped>
.clpm-stage-indicator {
  display: inline-flex;
  gap: 6px;
  align-items: center;
  padding: 2px 10px;
  font-size: 12px;
  font-weight: 600;
  line-height: 18px;
  white-space: nowrap;
  border: 1px solid currentcolor;
  border-radius: 999px;
  opacity: 0.92;
}

.clpm-stage-indicator.is-small {
  padding: 1px 8px;
  font-size: 11px;
  line-height: 16px;
}

.clpm-stage-indicator__dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
}

.clpm-stage-indicator__lock {
  font-size: 11px;
}
</style>
