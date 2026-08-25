<script lang="ts" setup>
/**
 * 装置总览 · 行4 E 列：重点关注回路
 *
 * 搬运原 §4：评分最低 10 / 最高 10 切换，点击回路卡 → 回路工作台（/monitor/loop-workbench）。
 */
import type { MetricApi } from '#/api';

import { getGrade } from '../use-grade';

defineProps<{
  /** 是否有选中节点（控制底部"清除选择"按钮显隐） */
  hasSelection: boolean;
  /** 当前范围名（全厂 / 装置 / 单元名） */
  scopeLabel: string;
  /** TOP10 回路列表 */
  topLoops: MetricApi.RankingItem[];
  /** 排序模式：asc = 评分最低 10 / desc = 评分最高 10 */
  topMode: 'asc' | 'desc';
}>();

const emit = defineEmits<{
  clear: [];
  goLoop: [loopId: string];
  setMode: [mode: 'asc' | 'desc'];
}>();
</script>

<template>
  <div
    class="flex h-full min-w-0 flex-col rounded border border-gray-200 bg-white"
  >
    <div
      class="flex h-9 flex-none items-center gap-2 border-b border-gray-100 px-2.5 text-[12px] font-bold text-gray-700"
    >
      重点关注回路
      <div
        class="ml-auto flex overflow-hidden rounded border border-gray-200 text-[11px]"
      >
        <button
          class="border-0 px-2 py-0.5"
          :class="
            topMode === 'asc' ? 'bg-blue-700 text-white' : 'bg-white text-gray-600'
          "
          @click="emit('setMode', 'asc')"
        >
          评分最低 10
        </button>
        <button
          class="border-0 border-l border-gray-200 px-2 py-0.5"
          :class="
            topMode === 'desc'
              ? 'bg-blue-700 text-white'
              : 'bg-white text-gray-600'
          "
          @click="emit('setMode', 'desc')"
        >
          评分最高 10
        </button>
      </div>
    </div>
    <div class="min-h-0 flex-1 overflow-y-auto px-2 py-1.5">
      <div
        v-for="(item, idx) in topLoops"
        :key="item.loopId"
        class="mb-1 flex cursor-pointer items-center gap-2 rounded border border-gray-200 bg-white px-2 py-1.5 hover:border-blue-300 hover:shadow-sm"
        @click="emit('goLoop', item.loopId)"
      >
        <span
          class="w-5 flex-none font-mono text-[11px] font-bold"
          :class="topMode === 'asc' && idx < 3 ? 'text-red-500' : 'text-gray-400'"
          >{{ idx + 1 }}</span
        >
        <div class="min-w-0 flex-1">
          <div class="truncate font-mono text-[12px] font-bold text-gray-800">
            {{ item.tagName }}
          </div>
          <div class="truncate text-[10px] text-gray-400">
            {{ item.unitName || '—' }}
          </div>
        </div>
        <span
          v-if="item.score !== null"
          class="flex-none font-mono text-[13px] font-bold"
          :style="{ color: getGrade(item.score).color }"
          >{{ item.score.toFixed(1) }}</span
        >
        <span
          v-else
          class="flex-none rounded border border-gray-200 px-1 text-[10px] text-gray-400"
          >待评估</span
        >
        <span
          v-if="item.score !== null"
          class="flex-none rounded border px-1 text-[10px] font-bold"
          :style="{
            color: getGrade(item.score).color,
            borderColor: `${getGrade(item.score).color}33`,
            background: `${getGrade(item.score).color}11`,
          }"
          >{{ getGrade(item.score).label }}</span
        >
        <span class="flex-none text-[12px] text-blue-600">→</span>
      </div>
      <div
        v-if="topLoops.length === 0"
        class="flex h-full items-center justify-center text-xs text-gray-300"
      >
        暂无回路评分数据
      </div>
    </div>
    <div
      class="flex h-7 flex-none items-center border-t border-gray-100 px-2.5 text-[11px]"
    >
      <span class="text-gray-400"
        >范围:
        <span class="font-bold text-gray-600">{{ scopeLabel }}</span>
        ·
        {{ topMode === 'asc' ? '最低' : '最高' }} 10</span
      >
      <button
        v-if="hasSelection"
        class="ml-auto cursor-pointer rounded border border-gray-200 px-1.5 py-0.5 text-[10px] text-gray-500 hover:border-blue-300 hover:text-blue-600"
        @click="emit('clear')"
      >
        清除选择
      </button>
      <span v-else class="ml-auto text-gray-300">点击行进入回路工作台</span>
    </div>
  </div>
</template>
