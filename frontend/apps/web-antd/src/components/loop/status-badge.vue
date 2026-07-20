<script lang="ts" setup>
import type { LoopApi } from '#/api/loop';

/**
 * 回路状态徽章组件（ZL 工业风格 v6.1）
 *
 * 对齐 ZL-MES-UI-Design-Kit/IndustrialDesignReference.html §1 状态语义色：
 * - READY → emerald（就绪）
 * - PARTIAL → rose（部分关联）
 * - INACTIVE → slate（未启用）
 *
 * 视觉：状态点（实心圆） + 文字徽章（边框 + 浅色背景）
 */
import { computed } from 'vue';

defineOptions({ name: 'LoopStatusBadge' });

const props = defineProps<{
  isActive?: boolean;
  status?: LoopApi.LoopStatus;
}>();

interface StatusConfig {
  /** 状态点色 */
  dotClass: string;
  /** 徽章容器 class */
  badgeClass: string;
  /** 中文标签 */
  label: string;
}

const statusMap: Record<string, StatusConfig> = {
  INACTIVE: {
    dotClass: 'bg-slate-400',
    badgeClass:
      'bg-slate-50 text-slate-600 border-slate-200 dark:bg-slate-500/10 dark:text-slate-400 dark:border-slate-500/30',
    label: '未启用',
  },
  PARTIAL: {
    dotClass: 'bg-rose-500',
    badgeClass:
      'bg-rose-50 text-rose-700 border-rose-200 dark:bg-rose-500/10 dark:text-rose-400 dark:border-rose-500/30',
    label: '部分关联',
  },
  READY: {
    dotClass: 'bg-emerald-500',
    badgeClass:
      'bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-500/10 dark:text-emerald-400 dark:border-emerald-500/30',
    label: '就绪',
  },
};

const current = computed<StatusConfig>(() => {
  if (props.isActive === false) {
    return statusMap.INACTIVE as StatusConfig;
  }
  return (
    statusMap[props.status ?? 'INACTIVE'] ??
    (statusMap.INACTIVE as StatusConfig)
  );
});
</script>

<template>
  <span
    class="inline-flex items-center gap-1.5 rounded border px-2 py-0.5 text-xs font-semibold"
    :class="current.badgeClass"
  >
    <span class="h-1.5 w-1.5 rounded-full" :class="current.dotClass"></span>
    {{ current.label }}
  </span>
</template>
