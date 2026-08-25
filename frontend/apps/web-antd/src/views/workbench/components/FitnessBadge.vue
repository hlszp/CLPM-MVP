<script setup lang="ts">
/**
 * 适用性门禁徽章卡（方案 §2 B-09 · F-DG-03）
 *
 * - 准入徽章：L0 红 / L1 黄 / L2 绿 / L3 蓝 / L4 绿
 * - 得分进度条 0~100（L0=0/L1=25/L2=50/L3=75/L4=100 加权均值）
 * - 4 项门禁 tick/cross（数据充分 / 非手动主导 / 无饱和偏差 / 激励充分）
 * - L0~L4 层级分布 chips + 参评分母自洽（evaluated/total）
 */
import type { WorkbenchApi } from '#/api/workbench';

import { computed } from 'vue';

const props = defineProps<{
  gates?: null | WorkbenchApi.DiagnosisFitnessGates;
}>();

const LEVEL_META: Record<string, { color: string; label: string }> = {
  L0: { color: '#FF4D4F', label: 'L0 不可评估' },
  L1: { color: '#FA8C16', label: 'L1 仅可监视' },
  L2: { color: '#52C41A', label: 'L2 条件异常' },
  L3: { color: '#1F4E79', label: 'L3 待激励' },
  L4: { color: '#52C41A', label: 'L4 可优化' },
};

const LEVELS = ['L0', 'L1', 'L2', 'L3', 'L4'] as const;

const level = computed(() => props.gates?.level ?? null);
const levelMeta = computed(() => (level.value ? LEVEL_META[level.value] : undefined));
const score = computed(() => props.gates?.score ?? null);
const gatesList = computed(() => {
  const passed = props.gates?.gates_passed ?? [];
  const desc = props.gates?.gate_desc ?? [];
  return passed.map((ok, i) => ({ ok, desc: desc[i] ?? `门禁 ${i + 1}` }));
});
const counts = computed(() => props.gates?.level_counts);
const maxCount = computed(() =>
  Math.max(1, ...LEVELS.map((lv) => counts.value?.[lv] ?? 0)),
);
</script>

<template>
  <div class="flex h-full flex-col overflow-hidden bg-white">
    <div class="flex flex-none items-center justify-between border-b border-[#E4E7ED] px-3 py-1.5">
      <span class="text-xs font-medium text-[#1F4E79]">适用性门禁 · L0~L4</span>
      <span class="text-[10px] text-gray-400">B-09 分级漏斗</span>
    </div>

    <div v-if="gates" class="flex flex-1 flex-col gap-2 overflow-auto p-2.5">
      <!-- 准入徽章 + 得分进度条 -->
      <div class="flex items-center gap-2.5">
        <span
          class="flex-none rounded px-1.5 py-0.5 text-[11px] font-semibold text-white"
          :style="{ backgroundColor: levelMeta?.color ?? '#BFBFBF' }"
        >
          {{ levelMeta?.label ?? '暂无层级' }}
        </span>
        <div class="relative h-2.5 min-w-0 flex-1 overflow-hidden rounded-sm bg-gray-100">
          <div
            class="h-full rounded-sm"
            :style="{
              width: `${Math.max(0, Math.min(100, score ?? 0))}%`,
              backgroundColor: levelMeta?.color ?? '#BFBFBF',
            }"
          ></div>
          <span
            class="absolute inset-0 flex items-center justify-center text-[9px] font-semibold text-gray-600"
            >{{ score === null ? '—' : `${score} / 100` }}</span
          >
        </div>
      </div>

      <!-- 参评分母自洽 -->
      <div class="text-[10px] text-gray-400">
        参评 {{ gates.evaluated }}/{{ gates.total }} 回路 · 得分 = L0~L4 加权均值（0/25/50/75/100）
      </div>

      <!-- 4 项门禁 -->
      <div class="flex flex-col gap-1 border-t border-[#F0F0F0] pt-1.5">
        <div
          v-for="(g, i) in gatesList"
          :key="i"
          class="flex items-center gap-1.5 text-[11px]"
          :style="{ color: g.ok ? '#52C41A' : '#FF4D4F' }"
        >
          <span class="flex-none font-bold">{{ g.ok ? '✓' : '✕' }}</span>
          <span class="truncate text-gray-600" :title="g.desc">{{ g.desc }}</span>
        </div>
      </div>

      <!-- L0~L4 层级分布 -->
      <div class="flex flex-col gap-1 border-t border-[#F0F0F0] pt-1.5">
        <div class="text-[10px] text-gray-400">层级分布（回路数）</div>
        <div
          v-for="lv in LEVELS"
          :key="lv"
          class="flex items-center gap-1.5"
        >
          <span
            class="w-16 flex-none text-right text-[10px]"
            :style="{ color: LEVEL_META[lv]?.color }"
            >{{ LEVEL_META[lv]?.label }}</span
          >
          <div class="relative h-3.5 min-w-0 flex-1 rounded-sm bg-gray-50">
            <div
              class="h-full rounded-sm"
              :style="{
                width: `${Math.max(2, ((counts?.[lv] ?? 0) / maxCount) * 100)}%`,
                backgroundColor: LEVEL_META[lv]?.color,
              }"
            ></div>
          </div>
          <span class="w-6 flex-none text-right text-[10px] text-gray-500">{{
            counts?.[lv] ?? 0
          }}</span>
        </div>
      </div>
    </div>

    <div
      v-else
      class="flex flex-1 items-center justify-center text-xs text-gray-300"
    >
      暂无适用性数据
    </div>
  </div>
</template>
