<script setup lang="ts">
/**
 * 执行摘要三窗口 KPI mini-kpi 卡（方案 §5.1 F-OV-01）
 *
 * - 6 指标 × 3 窗口 = 18 mini-kpi 卡（行=指标，列=24h/7d/30d）
 * - flags 气泡嵌在窗口列头（FlagBubble，接 windows[win].flags 内嵌版）
 * - 值缺失 null → "—" 灰显（数据未预计算/部分失败容错）
 * - 色阶：≥0.90 绿 · 0.80~0.90 黄 · <0.80 红（与 lose_factors 阈值同口径）
 * - 单屏紧凑：表格式布局，1px 分隔线，无多余标签（Glanceability）
 */
import type { WorkbenchApi } from '#/api/workbench';

import { computed } from 'vue';

import FlagBubble from './FlagBubble.vue';

const props = defineProps<{
  windows?: Partial<Record<WorkbenchApi.TimeWindow, null | WorkbenchApi.WindowBlock>>;
}>();

const WINDOWS: { key: WorkbenchApi.TimeWindow; label: string }[] = [
  { key: '24h', label: '近 24h' },
  { key: '7d', label: '近 7d' },
  { key: '30d', label: '近 30d' },
];

// 6 项指标行定义（键 + 中文标签；与后端 KPI_METRICS 对齐）
const METRICS: { key: WorkbenchApi.KpiMetricKey; label: string }[] = [
  { key: 'good_value_rate', label: '好值率' },
  { key: 'auto_mode_rate', label: '自控率' },
  { key: 'effective_auto_rate', label: '有效自控率' },
  { key: 'steady_rate', label: '平稳率' },
  { key: 'accuracy_rate', label: '准确率' },
  { key: 'fast_rate', label: '快速率' },
];

const THRESHOLDS = { good: 0.9, warn: 0.8 };

function valueColor(v: null | number | undefined): string {
  if (v === null || v === undefined) return '#BFBFBF';
  if (v >= THRESHOLDS.good) return '#52C41A';
  if (v >= THRESHOLDS.warn) return '#FAAD14';
  return '#F5222D';
}

function fmtPct(v: null | number | undefined): string {
  if (v === null || v === undefined) return '—';
  return `${(v * 100).toFixed(1)}%`;
}

function getMetric(win: null | undefined | WorkbenchApi.WindowBlock, key: WorkbenchApi.KpiMetricKey) {
  return win?.metrics?.[key] ?? null;
}

const scoreRow = computed(() =>
  WINDOWS.map((w) => ({
    ...w,
    block: props.windows?.[w.key] ?? null,
  })),
);
</script>

<template>
  <div class="flex h-full flex-col">
    <!-- 标题栏 -->
    <div class="flex flex-none items-center justify-between border-b border-[#E4E7ED] px-3 py-1.5">
      <span class="text-xs font-medium text-[#1F4E79]">执行摘要 · 三窗口 KPI</span>
      <span class="text-[10px] text-gray-400">6 指标 × 3 窗口</span>
    </div>

    <div class="flex-1 overflow-auto p-2">
      <table class="w-full border-collapse text-center text-xs">
        <thead>
          <tr class="border-b border-[#E4E7ED] text-[11px] text-gray-400">
            <th class="py-1 text-left font-normal">指标</th>
            <th
              v-for="w in scoreRow"
              :key="w.key"
              class="px-1 py-1 font-normal"
            >
              <div class="flex items-center justify-center gap-1">
                <span>{{ w.label }}</span>
                <FlagBubble :flags="w.block?.flags" />
              </div>
            </th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="m in METRICS"
            :key="m.key"
            class="border-b border-[#F0F0F0] last:border-0 hover:bg-[#FAFAFA]"
          >
            <td class="py-1.5 text-left text-gray-600">{{ m.label }}</td>
            <td
              v-for="w in scoreRow"
              :key="w.key"
              class="px-1 py-1.5"
            >
              <span
                class="font-mono text-sm font-medium"
                :style="{ color: valueColor(getMetric(w.block, m.key)) }"
              >
                {{ fmtPct(getMetric(w.block, m.key)) }}
              </span>
            </td>
          </tr>
          <!-- 综合得分行 -->
          <tr class="border-t-2 border-[#E4E7ED] bg-[#FAFAFA]">
            <td class="py-1.5 text-left font-medium text-[#1F4E79]">综合得分</td>
            <td
              v-for="w in scoreRow"
              :key="w.key"
              class="px-1 py-1.5"
            >
              <span
                class="font-mono text-sm font-bold"
                :style="{ color: valueColor((w.block?.score ?? 0) / 100) }"
              >
                {{ w.block?.score?.toFixed(1) ?? '—' }}
              </span>
              <span class="ml-0.5 text-[10px] text-gray-400">/{{ w.block?.loop_count ?? 0 }}回路</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
