<script setup lang="ts">
/**
 * 工作台 Tab：性能评估（M1 骨架，M2 填充 F-EV-01~03）
 *
 * M2 接 A-02 assessment：6 项 KPI 卡片 + 单元×6 指标热力 + 回路排名表。
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
      <span class="text-lg font-medium text-[#1F4E79]">性能评估</span>
      <span class="text-xs text-gray-500"
        >当前范围：{{ store.scopeType }} · 窗口：{{ store.timeWindow }}</span
      >
      <span class="max-w-md text-xs leading-5 text-gray-400">
        M2 填充：F-EV-01 6 项 KPI 卡片 · F-EV-02 单元热力矩阵 · F-EV-03 回路排名表
      </span>
    </div>
  </WorkbenchShell>
</template>
