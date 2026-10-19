<script setup lang="ts">
/**
 * 统计分析-数据质量与预警专题（/reports/quality）
 *
 * 合并原 数据质量 + 预警统计（方案 2026-10-19 P1）：
 * 「数据与风险底座」一页看清。
 */
import { ref } from 'vue';

import AlertStatistics from './alert-statistics.vue';
import DataQuality from './data-quality.vue';

type Section = 'alert' | 'quality';
const section = ref<Section>('quality');

const TABS: { label: string; value: Section }[] = [
  { label: '数据质量', value: 'quality' },
  { label: '预警统计', value: 'alert' },
];
</script>

<template>
  <div class="flex h-full flex-col">
    <div class="flex items-center gap-2 border-b px-4 py-2">
      <span class="text-base font-semibold">数据质量与预警专题</span>
      <div class="ml-2 flex gap-1">
        <button
          v-for="t in TABS"
          :key="t.value"
          class="rounded-md border px-3 py-1 text-sm"
          :class="
            section === t.value
              ? 'border-blue-500 bg-blue-50 text-blue-700'
              : 'border-gray-200 text-gray-600'
          "
          @click="section = t.value"
        >
          {{ t.label }}
        </button>
      </div>
    </div>
    <div class="min-h-0 flex-1 overflow-auto">
      <DataQuality v-if="section === 'quality'" />
      <AlertStatistics v-else />
    </div>
  </div>
</template>
