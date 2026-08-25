<script setup lang="ts">
/**
 * 处置看板漏斗（方案 §5.1 F-OV-05 · 4 泳道计数 + 超期红底）
 *
 * - 数据源：A-01 funnel（MV-03 mv_handling_funnel）
 * - 4 泳道：待处理 → 执行中 → 验证中 → 已闭环（箭头流向）
 * - 超期 breached > 0 → 红底徽章（Poka-Yoke 视觉警示）
 * - 重开 reopened 单独标注；平均周期 avg_cycle_hours 辅助
 * - funnel 为 null（容错缺失）→ 显示 "数据缺失"
 */
import type { WorkbenchApi } from '#/api/workbench';

import { computed } from 'vue';

const props = defineProps<{
  funnel?: null | WorkbenchApi.FunnelStat;
}>();

const LANES = [
  { key: 'pending', label: '待处理', color: '#FA8C16' },
  { key: 'executing', label: '执行中', color: '#1890FF' },
  { key: 'verifying', label: '验证中', color: '#13C2C2' },
  { key: 'closed', label: '已闭环', color: '#52C41A' },
] as const;

const lanes = computed(() =>
  LANES.map((l) => ({
    ...l,
    count: props.funnel?.[l.key] ?? 0,
  })),
);

const total = computed(() =>
  lanes.value.reduce((s, l) => s + l.count, 0),
);

const maxCount = computed(() =>
  Math.max(1, ...lanes.value.map((l) => l.count)),
);

const hasBreached = computed(() => (props.funnel?.breached ?? 0) > 0);
</script>

<template>
  <div class="flex h-full flex-col">
    <!-- 标题栏（固定标题栏右侧工具位留空） -->
    <div class="flex flex-none items-center justify-between border-b border-[#E4E7ED] px-3 py-1.5">
      <span class="text-xs font-medium text-[#1F4E79]">处置漏斗</span>
      <span class="text-[10px] text-gray-400"
        >平均周期 {{ funnel?.avg_cycle_hours?.toFixed(1) ?? '—' }}h</span
      >
    </div>

    <div v-if="!funnel" class="flex flex-1 items-center justify-center text-xs text-gray-300">
      数据缺失
    </div>
    <div v-else class="flex flex-1 flex-col gap-2 p-3">
      <!-- 4 泳道漏斗条 -->
      <div class="flex flex-1 items-end gap-1.5">
        <div
            v-for="lane in lanes"
            :key="lane.key"
            class="flex flex-1 flex-col items-center"
          >
          <!-- 条体高度按计数占比 -->
          <div
            class="flex w-full items-start justify-center rounded-t"
            :style="{
              height: `${Math.max(8, (lane.count / maxCount) * 120)}px`,
              backgroundColor: lane.color,
              opacity: lane.count === 0 ? 0.25 : 0.9,
            }"
          >
            <span class="pt-1 text-sm font-semibold text-white">{{ lane.count }}</span>
          </div>
          <span class="mt-1 text-[10px] text-gray-500">{{ lane.label }}</span>
          <!-- 箭头分隔（最后一个不显示） -->
        </div>
      </div>

      <!-- 流向箭头条（视觉引导） -->
      <div class="flex items-center justify-center gap-1 text-[10px] text-gray-300">
        <span>待处理</span>
        <span>→</span>
        <span>执行</span>
        <span>→</span>
        <span>验证</span>
        <span>→</span>
        <span>闭环</span>
      </div>

      <!-- 超期 + 重开徽章 -->
      <div class="flex flex-none items-center gap-2 border-t border-[#F0F0F0] pt-2 text-[11px]">
        <span
          class="rounded px-1.5 py-0.5 font-medium"
          :class="
            hasBreached
              ? 'bg-red-100 text-red-700'
              : 'bg-gray-50 text-gray-500'
          "
        >
          超期 {{ funnel.breached }}
        </span>
        <span
          v-if="funnel.reopened > 0"
          class="rounded bg-orange-50 px-1.5 py-0.5 text-orange-600"
        >
          重开 {{ funnel.reopened }}
        </span>
        <span class="ml-auto text-gray-400">共 {{ total }} 单</span>
      </div>
    </div>
  </div>
</template>
