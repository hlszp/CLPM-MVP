<script lang="ts" setup>
import type { LoopApi } from '#/api/loop';

/**
 * 回路状态徽章组件
 *
 * 对齐 D06 §6 视觉规范：
 * - Ready → 绿色（success）
 * - Partial → 红色（error）
 * - INCONCLUSIVE / INACTIVE → 灰色（default）
 */
import { computed } from 'vue';

import { Badge } from 'ant-design-vue';

defineOptions({ name: 'LoopStatusBadge' });

const props = defineProps<{
  isActive?: boolean;
  status?: LoopApi.LoopStatus;
}>();

const statusMap = {
  INACTIVE: { color: 'default', label: '未启用', status: 'default' as const },
  PARTIAL: {
    color: 'red',
    label: '部分关联',
    status: 'error' as const,
  },
  READY: { color: 'green', label: '就绪', status: 'success' as const },
};

const defaultStatus = statusMap.INACTIVE;

const current = computed(() => {
  if (props.isActive === false) {
    return statusMap.INACTIVE ?? defaultStatus;
  }
  return statusMap[props.status ?? 'INACTIVE'] ?? defaultStatus;
});
</script>

<template>
  <Badge
    :color="current.color"
    :text="current.label"
    :status="current.status"
  />
</template>
