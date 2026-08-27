<script setup lang="ts">
import type { WorkbenchApi } from '#/api/workbench';

/**
 * 模块维护面纱（方案 §5.1 F-GL-05：veil）
 *
 * MAINTENANCE 态时覆盖在 Tab 内容区上方，显示"维护中 + 进度条"。
 * 父容器需 position:relative + 内容区可被遮挡。
 */
import { computed } from 'vue';

const props = defineProps<{ plugin: WorkbenchApi.Plugin }>();

const visible = computed(() => props.plugin.status === 'MAINTENANCE');
const progress = computed(
  () => props.plugin.maintenance_window?.progress_pct ?? 0,
);
const message = computed(
  () => props.plugin.maintenance_window?.message ?? '模块维护中',
);
</script>

<template>
  <div
    v-if="visible"
    class="absolute inset-0 z-10 flex items-center justify-center bg-white/85 backdrop-blur-[2px]"
  >
    <div class="flex flex-col items-center gap-2">
      <span class="text-sm text-orange-600">{{ message }}</span>
      <div class="h-1.5 w-48 overflow-hidden rounded-full bg-gray-200">
        <div
          class="h-full rounded-full bg-orange-500"
          :style="{ width: `${progress}%` }"
        ></div>
      </div>
      <span class="text-xs text-gray-500">升级中 {{ progress }}%</span>
    </div>
  </div>
</template>
