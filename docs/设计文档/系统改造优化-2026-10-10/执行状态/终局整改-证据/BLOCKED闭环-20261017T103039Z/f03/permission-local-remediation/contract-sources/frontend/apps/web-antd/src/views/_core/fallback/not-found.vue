<script lang="ts" setup>
/**
 * 兜底页（整改 C2-2）：路径存在但被角色权限过滤 → 403；路径不存在 → 404。
 * 避免"无权限"被误报为"页面不存在"（用户会以为系统坏了）。
 */
import { computed } from 'vue';
import { useRoute } from 'vue-router';

import { Fallback } from '@vben/common-ui';

import { isKnownRoutePath } from '#/router/routes';

defineOptions({ name: 'FallbackNotFound' });

const route = useRoute();
const status = computed(() =>
  isKnownRoutePath(route.path) ? ('403' as const) : ('404' as const),
);
</script>

<template>
  <Fallback :status="status" />
</template>
