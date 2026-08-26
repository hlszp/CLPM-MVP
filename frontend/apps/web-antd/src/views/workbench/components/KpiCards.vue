<script setup lang="ts">
/**
 * 系统总览 · 6 项 KPI 卡片（原型对齐）
 *
 * 6 张横向卡片：综合评分 / 有效自控率 / 劣化回路 / 处置待办 / 预警事件 / 数据可信
 * - 每张卡片：标题 + 数值 + 副标题 + delta 指示 + 底部进度条
 * - 配色：综合评分蓝、自控率绿、劣化回路橙、处置待办蓝、预警事件红、数据可信绿
 * - 无动画、无多余装饰（Calm UI）
 */
import type { WorkbenchApi } from '#/api/workbench';

import { computed } from 'vue';

const props = defineProps<{
  /** 当前选中窗口（跟随 HeaderBar 时间胶囊联动） */
  currentWindow?: WorkbenchApi.TimeWindow;
  funnel?: null | WorkbenchApi.FunnelStat;
  plants?: WorkbenchApi.PlantRow[];
  windows?: Partial<Record<WorkbenchApi.TimeWindow, null | WorkbenchApi.WindowBlock>>;
}>();

/** 当前窗口块（默认 24h，跟随时间胶囊切换） */
const win = computed(
  () => props.windows?.[props.currentWindow ?? '24h'] ?? null,
);

// 综合评分
const score = computed(() => win.value?.score ?? null);
const scoreStatus = computed(() => win.value?.status ?? '—');

// 有效自控率
const effectiveAuto = computed(() => {
  const v = win.value?.metrics?.effective_auto_rate;
  return v == null ? null : Math.round(v * 1000) / 10;
});

// 数据可信（好值率）
const goodValue = computed(() => {
  const v = win.value?.metrics?.good_value_rate;
  return v == null ? null : Math.round(v * 1000) / 10;
});

// 劣化回路：plants 中分数低于 85 的
const degradedCount = computed(() => {
  if (!props.plants?.length) return 0;
  return props.plants.filter((p) => (p.score ?? 100) < 85).length;
});

// 处置待办
const pendingCount = computed(() => props.funnel?.pending ?? 0);
const executingCount = computed(() => props.funnel?.executing ?? 0);

// 预警事件（sla 警告计数）
const alertCount = computed(() => props.funnel?.breached ?? 0);

// 进度条百分比（0-100）
const scorePct = computed(() => Math.min(100, Math.max(0, Math.round((score.value ?? 0)))))
const autoPct = computed(() => Math.min(100, Math.max(0, Math.round((effectiveAuto.value ?? 0)))))
const goodPct = computed(() => Math.min(100, Math.max(0, Math.round((goodValue.value ?? 0)))))
</script>

<template>
  <div class="flex gap-2">
    <!-- 综合评分 -->
    <div
      class="flex flex-1 flex-col gap-1 rounded border border-[#E4E7ED] bg-white px-3 py-2"
    >
      <div class="flex items-center justify-between">
        <span class="text-xs text-gray-500">综合评分</span>
        <span class="text-[10px] text-gray-400">{{ scoreStatus }}</span>
      </div>
      <div class="flex items-baseline gap-1">
        <span class="text-2xl font-semibold text-[#1F4E79]">
          {{ score ?? '—' }}
        </span>
        <span class="text-xs text-gray-400">分</span>
      </div>
      <div class="text-[10px] text-gray-400">
        参评 <span class="text-gray-600">{{ win?.loop_count ?? '—' }}</span> 回路
      </div>
      <div class="h-1 w-full overflow-hidden rounded-full bg-gray-100">
        <div
          class="h-full rounded-full bg-[#1F4E79] transition-none"
          :style="{ width: `${scorePct}%` }"
        ></div>
      </div>
    </div>

    <!-- 有效自控率 -->
    <div
      class="flex flex-1 flex-col gap-1 rounded border border-[#E4E7ED] bg-white px-3 py-2"
    >
      <div class="flex items-center justify-between">
        <span class="text-xs text-gray-500">有效自控率</span>
      </div>
      <div class="flex items-baseline gap-1">
        <span class="text-2xl font-semibold text-[#52C41A]">
          {{ effectiveAuto != null ? `${effectiveAuto}%` : '—' }}
        </span>
      </div>
      <div class="text-[10px] text-gray-400">
        目标 ≥ 85%
      </div>
      <div class="h-1 w-full overflow-hidden rounded-full bg-gray-100">
        <div
          class="h-full rounded-full bg-[#52C41A] transition-none"
          :style="{ width: `${autoPct}%` }"
        ></div>
      </div>
    </div>

    <!-- 劣化回路 -->
    <div
      class="flex flex-1 flex-col gap-1 rounded border border-[#E4E7ED] bg-white px-3 py-2"
    >
      <div class="flex items-center justify-between">
        <span class="text-xs text-gray-500">劣化回路</span>
      </div>
      <div class="flex items-baseline gap-1">
        <span class="text-2xl font-semibold text-[#FA8C16]">
          {{ degradedCount }}
        </span>
        <span class="text-xs text-gray-400">条</span>
      </div>
      <div class="text-[10px] text-gray-400">
        24h 内标注点
      </div>
      <!-- 计数指标无进度条（避免伪完成度误导，保留高度占位对齐比率卡） -->
      <div class="h-1 w-full"></div>
    </div>

    <!-- 处置待办 -->
    <div
      class="flex flex-1 flex-col gap-1 rounded border border-[#E4E7ED] bg-white px-3 py-2"
    >
      <div class="flex items-center justify-between">
        <span class="text-xs text-gray-500">处置待办</span>
      </div>
      <div class="flex items-baseline gap-1">
        <span class="text-2xl font-semibold text-[#1F4E79]">
          {{ pendingCount + executingCount }}
        </span>
        <span class="text-xs text-gray-400">项</span>
      </div>
      <div class="text-[10px] text-gray-400">
        待处理 {{ pendingCount }} · 执行中 {{ executingCount }}
      </div>
      <!-- 计数指标无进度条（避免伪完成度误导，保留高度占位对齐比率卡） -->
      <div class="h-1 w-full"></div>
    </div>

    <!-- 预警事件 -->
    <div
      class="flex flex-1 flex-col gap-1 rounded border border-[#E4E7ED] bg-white px-3 py-2"
    >
      <div class="flex items-center justify-between">
        <span class="text-xs text-gray-500">预警事件</span>
      </div>
      <div class="flex items-baseline gap-1">
        <span class="text-2xl font-semibold text-[#FF4D4F]">
          {{ alertCount }}
        </span>
        <span class="text-xs text-gray-400">条</span>
      </div>
      <div class="text-[10px] text-gray-400">
        SLA 超期
      </div>
      <!-- 计数指标无进度条（避免伪完成度误导，保留高度占位对齐比率卡） -->
      <div class="h-1 w-full"></div>
    </div>

    <!-- 数据可信 -->
    <div
      class="flex flex-1 flex-col gap-1 rounded border border-[#E4E7ED] bg-white px-3 py-2"
    >
      <div class="flex items-center justify-between">
        <span class="text-xs text-gray-500">数据可信</span>
      </div>
      <div class="flex items-baseline gap-1">
        <span class="text-2xl font-semibold text-[#52C41A]">
          {{ goodValue != null ? `${goodValue}%` : '—' }}
        </span>
      </div>
      <div class="text-[10px] text-gray-400">
        {{ win?.loop_count ?? '—' }} 回路完整
      </div>
      <div class="h-1 w-full overflow-hidden rounded-full bg-gray-100">
        <div
          class="h-full rounded-full bg-[#52C41A] transition-none"
          :style="{ width: `${goodPct}%` }"
        ></div>
      </div>
    </div>
  </div>
</template>
