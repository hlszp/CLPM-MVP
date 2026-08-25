<script setup lang="ts">
/**
 * 模块 4 态 Pill 标签（方案 §5.1 F-GL-05：pills）
 *
 * 显示：状态点 + 模块名 + 版本号。用于工作台顶栏插件列表/系统页模块管理。
 */
import type { WorkbenchApi } from '#/api/workbench';

import ModuleStatusDot from './ModuleStatusDot.vue';

defineProps<{ plugin: WorkbenchApi.Plugin }>();
</script>

<template>
  <span
    class="inline-flex items-center gap-1.5 rounded border border-[#E4E7ED] bg-white px-2 py-0.5 text-xs"
  >
    <ModuleStatusDot :size="6" :status="plugin.status" />
    <span class="text-gray-700">{{ plugin.display_name }}</span>
    <span v-if="plugin.version" class="text-gray-400">v{{ plugin.version }}</span>
  </span>
</template>
