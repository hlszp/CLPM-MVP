<script setup lang="ts">
/**
 * 整定批次列表（方案 §5.1 F-TN-01 · 原型 renderTune 批次表 1:1 复刻）
 *
 * 原型列：批次号 | 回路数 | 策略 | 状态 | 评分变化 | 执行人
 * 状态色点（B-06 动态判定）：
 *   COMPLETED 已验证（绿）· RUNNING 执行中（蓝）· READY 就绪（蓝绿）
 *   PENDING 排队中（灰）· BLOCKED 阻塞中（红 + block_reason）· CANCELLED 已回退（红灰）
 * 前置依赖：标题行下方 DepsPillList pills（CL-xxxx + 状态色）
 */
import type { WorkbenchApi } from '#/api/workbench';

import DepsPillList from './DepsPillList.vue';

defineProps<{
  batches?: WorkbenchApi.TuningBatch[];
  window?: string;
}>();

const STATUS_META: Record<
  WorkbenchApi.TuningBatchStatus,
  { bg: string; color: string; label: string }
> = {
  BLOCKED: { bg: '#FFF1F0', color: '#FF4D4F', label: '阻塞中' },
  CANCELLED: { bg: '#FFF1F0', color: '#8C8C8C', label: '已回退' },
  COMPLETED: { bg: '#F6FFED', color: '#52C41A', label: '已验证' },
  PENDING: { bg: '#F5F5F5', color: '#8C8C8C', label: '排队中' },
  READY: { bg: '#E6F7FF', color: '#1890FF', label: '就绪' },
  RUNNING: { bg: '#EBF1F8', color: '#2563EB', label: '执行中' },
};

/** 评分变化文案（原型：71 → 88（▲17）；负 Δ 红 ▼） */
function scoreChange(b: WorkbenchApi.TuningBatch): { color: string; text: string } {
  if (b.score_before === null || b.score_after === null || b.score_delta === null) {
    return { color: '#909399', text: '—' };
  }
  const arrow = b.score_delta >= 0 ? `▲${b.score_delta}` : `▼${Math.abs(b.score_delta)}`;
  const suffix = b.status === 'CANCELLED' ? ' → 回退' : '';
  return {
    color: b.score_delta >= 0 ? '#52C41A' : '#FF4D4F',
    text: `${b.score_before} → ${b.score_after}（${arrow}）${suffix}`,
  };
}
</script>

<template>
  <div class="flex h-full w-full flex-col overflow-hidden bg-white">
    <!-- 标题栏 -->
    <div class="flex flex-none items-center justify-between border-b border-[#E4E7ED] px-3 py-1.5">
      <span class="flex items-center gap-1.5 text-xs font-medium text-[#1F4E79]">
        <span class="inline-block h-1 w-3 rounded-sm bg-[#1F4E79]"></span>
        整定批次队列
        <span class="text-[10px] font-normal text-gray-400">
          {{ window ?? '24h' }} · {{ batches?.length ?? 0 }} 批次
        </span>
      </span>
    </div>

    <!-- 批次表 -->
    <div class="flex-1 overflow-auto">
      <table class="w-full text-[11.5px]">
        <thead class="sticky top-0 bg-[#FAFBFC] text-[10.5px] text-gray-400">
          <tr class="border-b border-[#E4E7ED]">
            <th class="px-3 py-1 text-left font-normal">批次号</th>
            <th class="px-2 py-1 text-left font-normal">回路数</th>
            <th class="px-2 py-1 text-left font-normal">策略</th>
            <th class="px-2 py-1 text-left font-normal">状态</th>
            <th class="px-2 py-1 text-left font-normal">评分变化</th>
            <th class="px-3 py-1 text-left font-normal">执行人</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="b in batches ?? []"
            :key="b.batch_no"
            class="border-b border-[#F5F7FA] align-top hover:bg-[#F7F9FC]"
          >
            <!-- 批次号 + 前置依赖 pills -->
            <td class="px-3 py-1.5">
              <div class="font-semibold text-gray-800 tabular-nums">{{ b.batch_no }}</div>
              <div class="max-w-56 truncate text-[10px] text-gray-400" :title="b.title">
                {{ b.title }}
              </div>
              <DepsPillList :orders="b.prereq_orders" class="mt-0.5" />
            </td>
            <td class="px-2 py-1.5 tabular-nums">
              {{ b.loop_count }}
              <span class="text-[10px] text-gray-400">（{{ b.scope_type }}#{{ b.scope_id }}）</span>
            </td>
            <td class="max-w-32 px-2 py-1.5 text-gray-600">
              {{ b.strategy ?? '—' }}
            </td>
            <!-- 状态 tag + BLOCKED 原因 -->
            <td class="px-2 py-1.5">
              <span
                class="inline-block rounded-sm px-1.5 py-px text-[10.5px] font-medium"
                :style="{
                  backgroundColor: STATUS_META[b.status].bg,
                  color: STATUS_META[b.status].color,
                }"
              >
                {{ STATUS_META[b.status].label }}
              </span>
              <div
                v-if="b.status === 'BLOCKED' && b.block_reason"
                class="mt-0.5 max-w-40 text-[10px] leading-4 text-[#FF4D4F]"
              >
                {{ b.block_reason }}
              </div>
            </td>
            <td
              class="px-2 py-1.5 font-medium whitespace-nowrap tabular-nums"
              :style="{ color: scoreChange(b).color }"
            >
              {{ scoreChange(b).text }}
            </td>
            <td class="px-3 py-1.5 text-gray-600">{{ b.owner ?? '系统（诊断建议）' }}</td>
          </tr>
        </tbody>
      </table>
      <div v-if="(batches ?? []).length === 0" class="py-10 text-center text-xs text-gray-300">
        当前范围无整定批次
      </div>
    </div>

    <!-- 底部辅助行 -->
    <div class="flex-none border-t border-[#F0F0F0] px-3 py-1 text-[10px] text-gray-400">
      阻塞批次将在前置工单闭合后自动转为「就绪」· 批次评分变化 = 批次内回路 Δ 均值
    </div>
  </div>
</template>
