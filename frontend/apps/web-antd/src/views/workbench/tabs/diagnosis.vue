<script setup lang="ts">
/**
 * 工作台 Tab：回路诊断（M1 骨架，M2 填充 F-DG-01~03）
 *
 * M2 接 A-03 diagnosis：异常回路表 + 诊断结论时间线 + 适用性 L0~L4 横幅。
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
      <span class="text-lg font-medium text-[#1F4E79]">回路诊断</span>
      <span class="text-xs text-gray-500"
        >当前范围：{{ store.scopeType }} · 窗口：{{ store.timeWindow }}</span
      >
      <span class="max-w-md text-xs leading-5 text-gray-400">
        M2 填充：F-DG-01 异常回路表 · F-DG-02 诊断结论时间线 · F-DG-03 适用性 L0~L4 横幅
      </span>
    </div>
  </WorkbenchShell>
</template>
