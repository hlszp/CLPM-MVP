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
  status?: 'INACTIVE' | LoopApi.LoopStatus;
}>();

const statusMap = {
  INACTIVE: { color: 'default', label: '未启用', status: 'default' as const },
  INCONCLUSIVE: {
    color: 'default',
    label: '未确定',
    status: 'default' as const,
  },
  Partial: { color: 'red', label: '部分关联', status: 'error' as const },
  Ready: { color: 'green', label: '就绪', status: 'success' as const },
};

const defaultStatus = statusMap.INCONCLUSIVE;

const current = computed(() => {
  if (props.isActive === false) {
    return statusMap.INACTIVE ?? defaultStatus;
  }
  return statusMap[props.status ?? 'INCONCLUSIVE'] ?? defaultStatus;
});
</script>

<template>
  <Badge
    :color="current.color"
    :text="current.label"
    :status="current.status"
  />
</template>
