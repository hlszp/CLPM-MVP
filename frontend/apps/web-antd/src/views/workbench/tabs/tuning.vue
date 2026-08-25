<script setup lang="ts">
/**
 * 工作台 Tab：参数整定（M1 骨架，M2 填充 F-TN-01~03）
 *
 * M2 接 A-04 tuning + A-13 tuning-scatters：整定批次列表 + 待整定队列 + 前后散点。
 */
import { useWorkbenchStore } from '#/store/workbench';

import WorkbenchShell from '../components/WorkbenchShell.vue';

const store = useWorkbenchStore();
</script>

<template>
  <WorkbenchShell>
    <div
      class="flex h-full flex-col items-center justify-center gap-3 px-6 py-4 text-center"
    >
      <span class="text-lg font-medium text-[#1F4E79]">参数整定</span>
      <span class="text-xs text-gray-500"
        >当前范围：{{ store.scopeType }} · 窗口：{{ store.timeWindow }}</span
      >
      <span class="max-w-md text-xs leading-5 text-gray-400">
        M2 填充：F-TN-01 整定批次列表 · F-TN-02 待整定队列 · F-TN-03 前后散点
      </span>
    </div>
  </WorkbenchShell>
</template>
