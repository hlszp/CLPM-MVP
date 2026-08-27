<script setup lang="ts">
/**
 * 系统总览 · 处置待办 · 闭环质量（原型对齐 1:1）
 *
 * 复刻原型 fun-row + mini-stat + miniBars：
 * - 3 泳道条：待处理(pending) / 处理中(executing) / 验证中(verifying)
 * - 摘要行：待办合计 13 ｜ 已闭环 18 · 重开 1
 * - 3 mini-stat：闭环及时率 / 平均闭环周期（▼1.8h 环比） / 重开率
 * - 底部 mini 柱图：近 6 周每周闭环数（原型 miniBars，演示数据）
 *
 * 注：近 6 周每周闭环数暂用演示数据（后端无周聚合端点，待 A-0x 扩展）
 */
import type { HandlingApi } from '#/api/handling';
import type { WorkbenchApi } from '#/api/workbench';

import { computed } from 'vue';

const props = defineProps<{
  funnel?: null | WorkbenchApi.FunnelStat;
}>();

const emit = defineEmits<{
  (e: 'laneClick', status: HandlingApi.OrderStatus): void;
}>();

const lanes = computed(() => {
  const f = props.funnel;
  if (!f) return [];
  const maxLane = Math.max(f.pending, f.executing, f.verifying, 1);
  return [
    {
      key: 'pending',
      label: '待处理',
      value: f.pending,
      color: '#f59e0b', // warn
      pct: Math.round((f.pending / maxLane) * 100),
      status: 'PENDING' as HandlingApi.OrderStatus,
    },
    {
      key: 'executing',
      label: '处理中',
      value: f.executing,
      color: '#0d9488', // accent
      pct: Math.round((f.executing / maxLane) * 100),
      status: 'EXECUTING' as HandlingApi.OrderStatus,
    },
    {
      key: 'verifying',
      label: '验证中',
      value: f.verifying,
      color: '#3b82f6', // info
      pct: Math.round((f.verifying / maxLane) * 100),
      status: 'VERIFYING' as HandlingApi.OrderStatus,
    },
  ];
});

const totalTodo = computed(() => {
  const f = props.funnel;
  if (!f) return 0;
  return f.pending + f.executing + f.verifying;
});

// 闭环及时率：closed / (closed + breached) 的近似（原型 78.1% = 25/32）
const timelyRate = computed(() => {
  const f = props.funnel;
  if (!f) return null;
  const denom = f.closed + f.breached + f.reopened;
  if (denom <= 0) return null;
  return Math.round((f.closed / denom) * 1000) / 10;
});

const reopenRate = computed(() => {
  const f = props.funnel;
  if (!f) return null;
  const denom = f.closed + f.reopened;
  if (denom <= 0) return null;
  return Math.round((f.reopened / denom) * 1000) / 10;
});

// 近 6 周每周闭环数（演示数据，对齐原型 [3,5,4,6,5,4]）
const weeklyClosed = [3, 5, 4, 6, 5, 4];
const weeklyMax = Math.max(...weeklyClosed, 1);
</script>

<template>
  <div class="flex h-full flex-col rounded border border-[#E4E7ED] bg-white">
    <!-- 头部 -->
    <div class="flex items-center justify-between border-b border-[#E4E7ED] px-3 py-2">
      <span class="flex items-center gap-1.5 text-xs font-medium text-gray-700">
        <span class="inline-block h-3 w-1 rounded-sm bg-[#3b82f6]"></span>
        处置待办 · 闭环质量
      </span>
      <span class="text-[10px] text-[#1F4E79]">处置详情 →</span>
    </div>

    <div v-if="funnel" class="flex flex-1 flex-col gap-1.5 overflow-hidden p-2.5">
      <!-- 3 泳道条 -->
      <div class="flex flex-col gap-1">
        <div
          v-for="lane in lanes"
          :key="lane.key"
          class="flex cursor-pointer items-center gap-1.5 transition-opacity hover:opacity-80"
          :title="`点击查看${lane.label}工单 → 处置 Tab`"
          @click="emit('laneClick', lane.status)"
        >
          <span class="w-10 flex-none text-[10px] text-gray-500">{{ lane.label }}</span>
          <div class="relative flex h-4 flex-1 items-center overflow-hidden rounded-full bg-gray-100">
            <div
              class="flex h-full items-center justify-end rounded-full pr-1 text-[10px] font-medium text-white"
              :style="{ width: `${lane.pct}%`, backgroundColor: lane.color }"
            >{{ lane.value }}</div>
          </div>
        </div>
      </div>

      <!-- 摘要行 -->
      <div class="text-[10.5px] text-gray-500">
        待办合计 <b class="text-gray-700">{{ totalTodo }}</b>
        ｜ 已闭环 <b class="text-gray-700">{{ funnel.closed }}</b>
        · 重开 <b :class="funnel.reopened > 0 ? 'text-[#D93025]' : 'text-gray-700'">{{ funnel.reopened }}</b>
        <span v-if="funnel.breached > 0" class="text-[#D93025]">· 超期 {{ funnel.breached }}</span>
      </div>

      <!-- 3 mini-stat -->
      <div class="flex items-stretch gap-1.5">
        <div class="flex flex-1 flex-col items-center justify-center rounded border border-[#EBEEF5] py-1">
          <div class="text-sm font-semibold" :class="(timelyRate ?? 0) >= 85 ? 'text-[#2E7D32]' : 'text-[#f59e0b]'">
            {{ timelyRate != null ? `${timelyRate }%` : '—' }}
          </div>
          <div class="text-center text-[9px] text-gray-400">闭环及时率<br />目标 ≥85%</div>
        </div>
        <div class="flex flex-1 flex-col items-center justify-center rounded border border-[#EBEEF5] py-1">
          <div class="text-sm font-semibold text-gray-700">
            {{ funnel.avg_cycle_hours?.toFixed(1) ?? '—' }}h
          </div>
          <div class="text-center text-[9px] text-gray-400">平均闭环周期<br /><span class="text-[#2E7D32]">▼1.8h</span> 环比</div>
        </div>
        <div class="flex flex-1 flex-col items-center justify-center rounded border border-[#EBEEF5] py-1">
          <div class="text-sm font-semibold text-gray-600">
            {{ reopenRate != null ? `${reopenRate }%` : '—' }}
          </div>
          <div class="text-center text-[9px] text-gray-400">重开率<br />{{ funnel.reopened }}/{{ funnel.closed + funnel.reopened }}</div>
        </div>
      </div>

      <!-- 近 6 周每周闭环数 mini 柱图 -->
      <div class="mt-auto border-t border-[#EBEEF5] pt-1.5">
        <div class="mb-0.5 text-[10px] text-gray-500">近 6 周每周闭环数</div>
        <svg viewBox="0 0 220 26" class="h-6 w-full" preserveAspectRatio="none" style="display:block">
          <g v-for="(v, i) in weeklyClosed" :key="`wb-${i}`">
            <rect
              :x="220 * (i + 0.5) / weeklyClosed.length - (220 / weeklyClosed.length * 0.55) / 2"
              :y="26 - (26 * v / weeklyMax)"
              :width="220 / weeklyClosed.length * 0.55"
              :height="26 * v / weeklyMax"
              rx="1.5"
              :fill="i === weeklyClosed.length - 1 ? '#0d9488' : '#A9BBD3'"
            />
            <text
              :x="220 * (i + 0.5) / weeklyClosed.length"
              :y="Math.max(26 - (26 * v / weeklyMax) - 3, 9)"
              text-anchor="middle"
              font-size="9"
              fill="#8A94A6"
            >{{ v }}</text>
          </g>
        </svg>
      </div>
    </div>

    <!-- 空态 -->
    <div
      v-else
      class="flex flex-1 items-center justify-center text-xs text-gray-400"
    >
      暂无处置数据
    </div>
  </div>
</template>
