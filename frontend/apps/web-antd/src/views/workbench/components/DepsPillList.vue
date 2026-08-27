<script setup lang="ts">
/**
 * 前置依赖工单 pill 列表（方案 §5.1 F-TN-01 配套 · B-06）
 *
 * 每个前置工单渲染为 pill：工单号 + 状态色：
 * - 已闭合（CLOSED/CANCELLED）→ 绿 ✓
 * - 未闭合（PENDING/EXECUTING/VERIFYING）→ 红 ✗（阻塞源，Poka-Yoke 醒目）
 */
import type { WorkbenchApi } from '#/api/workbench';

defineProps<{
  orders?: WorkbenchApi.TuningPrereqOrder[];
}>();
</script>

<template>
  <div v-if="orders && orders.length > 0" class="flex flex-wrap items-center gap-1">
    <span class="text-[10px] text-gray-400">前置</span>
    <span
      v-for="o in orders"
      :key="o.order_id"
      class="inline-flex items-center gap-0.5 rounded-sm px-1 py-px text-[10px] font-medium"
      :class="
        o.closed
          ? 'bg-[#F6FFED] text-[#52C41A] border border-[#B7EB8F]'
          : 'bg-[#FFF1F0] text-[#FF4D4F] border border-[#FFCCC7]'
      "
      :title="`${o.title ?? ''} ${o.status ?? ''}`.trim() || o.order_id"
    >
      <span>{{ o.closed ? '✓' : '✗' }}</span>
      <span class="tabular-nums">{{ o.order_no ?? o.order_id.slice(0, 8) }}</span>
    </span>
  </div>
</template>
