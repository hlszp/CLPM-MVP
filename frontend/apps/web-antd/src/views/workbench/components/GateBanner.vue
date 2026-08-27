<script setup lang="ts">
/**
 * 适用性门禁横幅（方案 §2 B-09 · F-DG-03）
 *
 * - L0：全局红横幅「诊断数据不足，无法进入根因分析」（L0 阻止 L2）
 * - L1：黄/橙横幅「手动主导回路 N 条，根因分析受限」（L1 阻止 L2）
 * - L2 及以上 / 无数据：不渲染
 */
import type { WorkbenchApi } from '#/api/workbench';

import { computed } from 'vue';

const props = defineProps<{
  gates?: null | WorkbenchApi.DiagnosisFitnessGates;
}>();

const level = computed(() => props.gates?.level ?? null);
const l0Count = computed(() => props.gates?.level_counts.L0 ?? 0);
const l1Count = computed(() => props.gates?.level_counts.L1 ?? 0);

const banner = computed(() => {
  if (level.value === 'L0') {
    return {
      bg: '#FFF1F0',
      border: '#FFA39E',
      color: '#FF4D4F',
      text: `诊断数据不足，无法进入根因分析 —— ${l0Count.value} 个回路 L0（数据严重不足），请先在数据管理补齐历史数据`,
    };
  }
  if (level.value === 'L1') {
    return {
      bg: '#FFF7E6',
      border: '#FFD591',
      color: '#FA8C16',
      text: `手动主导回路 ${l1Count.value} 条（L1 仅可监视），根因分析受限 —— 建议恢复自动投用后再诊断`,
    };
  }
  return null;
});
</script>

<template>
  <div
    v-if="banner"
    class="flex flex-none items-center gap-2 rounded border px-3 py-1 text-[11px] font-medium"
    :style="{
      backgroundColor: banner.bg,
      borderColor: banner.border,
      color: banner.color,
    }"
  >
    <span
      class="inline-block h-2 w-2 flex-none rounded-full"
      :style="{ backgroundColor: banner.color }"
    ></span>
    {{ banner.text }}
  </div>
</template>
