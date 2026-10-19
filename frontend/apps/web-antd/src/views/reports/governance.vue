<script setup lang="ts">
/**
 * 统计分析-诊断与处置专题（/reports/governance）
 *
 * 合并原 诊断报告 + 处置报告 + 收益报告（方案 2026-10-19 P1）：
 * 「干了什么、干得怎样」一条故事线，减少经理跨页跳转。
 * 各段仍用原页面实现，保证口径与导出能力不丢。
 */
import { ref } from 'vue';

import BenefitReport from './benefit.vue';
import DiagnosisReport from './diagnosis.vue';
import HandlingReport from './handling.vue';

type Section = 'benefit' | 'diagnosis' | 'handling';
const section = ref<Section>('diagnosis');

const TABS: { label: string; value: Section }[] = [
  { label: '诊断工作量', value: 'diagnosis' },
  { label: '处置闭环', value: 'handling' },
  { label: '收益效果', value: 'benefit' },
];
</script>

<template>
  <div class="flex h-full flex-col">
    <div class="flex items-center gap-2 border-b px-4 py-2">
      <span class="text-base font-semibold">诊断与处置专题</span>
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
      <DiagnosisReport v-if="section === 'diagnosis'" />
      <HandlingReport v-else-if="section === 'handling'" />
      <BenefitReport v-else />
    </div>
  </div>
</template>
