<script setup lang="ts">
/**
 * 工作台 Tab：问题处置（M1 骨架，M2 填充 F-OP-01~02）
 *
 * M2 接 A-05 handling + A-08 staff-load：4 泳道看板 + 人员负载 hbar。
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
      <span class="text-lg font-medium text-[#1F4E79]">问题处置</span>
      <span class="text-xs text-gray-500"
        >当前范围：{{ store.scopeType }} · 窗口：{{ store.timeWindow }}</span
      >
      <span class="max-w-md text-xs leading-5 text-gray-400">
        M2 填充：F-OP-01 4 泳道看板 · F-OP-02 人员负载 hbar
      </span>
    </div>
  </WorkbenchShell>
</template>
