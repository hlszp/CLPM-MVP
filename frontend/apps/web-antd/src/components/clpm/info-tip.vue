<template>
  <a-tooltip :title="tooltipContent" placement="top">
    <span class="clpm-info-tip">
      <IconifyIcon :icon="icon" :size="size" />
    </span>
  </a-tooltip>
</template>

<script setup lang="ts">
import { computed } from 'vue';

import { IconifyIcon } from '@vben/icons';

interface Props {
  /** 术语名称 */
  term?: string;
  /** 简短说明 */
  tip: string;
  /** 详细说明（可选） */
  detail?: string;
  /** 图标名，默认 info */
  icon?: string;
  /** 图标大小，默认 14 */
  size?: number;
}

const props = withDefaults(defineProps<Props>(), {
  term: '',
  icon: 'lucide:info',
  size: 14,
});

const tooltipContent = computed(() => {
  const parts = [];
  if (props.term) parts.push(`**${props.term}**`);
  parts.push(props.tip);
  if (props.detail) parts.push(props.detail);
  return parts.join('：');
});
</script>

<style scoped>
.clpm-info-tip {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  margin-left: 4px;
  vertical-align: middle;
  color: hsl(var(--foreground) / 35%);
  cursor: help;
  transition: color 0.15s;
}

.clpm-info-tip:hover {
  color: hsl(var(--status-info));
}
</style>
