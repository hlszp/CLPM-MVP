<script setup lang="ts">
/**
 * 单元热力矩阵（方案 §5.1 F-OV-03 · 6 指标 × N units）
 *
 * - 行=单元，列=6 指标 + 综合得分
 * - 缺数据 metrics[key]=null → CSS 斜纹底（N/A），不查询、不渲染数值
 * - 色阶（与 MiniKpiStrip 同口径）：≥0.90 绿 · 0.80~0.90 黄 · <0.80 红
 * - 工业规范：1px 细线分隔，紧凑行高 28px，表头 sticky
 * - 悬浮 tooltip 显示数值（Glanceability：默认看色，细节 hover）
 */
import type { WorkbenchApi } from '#/api/workbench';

const props = defineProps<{
  units?: WorkbenchApi.UnitRow[];
}>();

const METRICS: { key: WorkbenchApi.KpiMetricKey; label: string; short: string }[] = [
  { key: 'good_value_rate', label: '好值率', short: '好值' },
  { key: 'auto_mode_rate', label: '自控率', short: '自控' },
  { key: 'effective_auto_rate', label: '有效自控率', short: '有效' },
  { key: 'steady_rate', label: '平稳率', short: '平稳' },
  { key: 'accuracy_rate', label: '准确率', short: '准确' },
  { key: 'fast_rate', label: '快速率', short: '快速' },
];

function cellColor(v: null | number | undefined): string {
  if (v === null || v === undefined) return 'transparent';
  if (v >= 0.9) return 'rgba(82, 196, 26, 0.65)'; // 绿
  if (v >= 0.8) return 'rgba(250, 173, 20, 0.65)'; // 黄
  return 'rgba(245, 34, 45, 0.65)'; // 红
}

function fmtPct(v: null | number | undefined): string {
  if (v === null || v === undefined) return 'N/A';
  return `${(v * 100).toFixed(1)}%`;
}

// N/A 斜纹背景（工业规范：缺数据用斜纹而非纯灰，避免误读为 0）
const NA_STYLE = {
  backgroundImage:
    'repeating-linear-gradient(135deg, #F5F5F5 0, #F5F5F5 4px, #E8E8E8 4px, #E8E8E8 6px)',
} as const;

function scoreColor(score: null | number): string {
  if (score === null) return '#BFBFBF';
  if (score >= 90) return '#52C41A';
  if (score >= 75) return '#FAAD14';
  if (score >= 60) return '#FA8C16';
  return '#F5222D';
}

const units = props.units ?? [];
</script>

<template>
  <div class="flex h-full flex-col">
    <!-- 标题栏 -->
    <div class="flex flex-none items-center justify-between border-b border-[#E4E7ED] px-3 py-1.5">
      <span class="text-xs font-medium text-[#1F4E79]">单元热力矩阵</span>
      <span class="text-[10px] text-gray-400">{{ units.length }} 单元 × 6 指标</span>
    </div>

    <div class="flex-1 overflow-auto">
      <table class="w-full border-collapse text-center text-[11px]">
        <thead class="sticky top-0 bg-white">
          <tr class="border-b border-[#E4E7ED] text-[10px] text-gray-400">
            <th class="py-1 text-left font-normal">单元</th>
            <th
              v-for="m in METRICS"
              :key="m.key"
              class="px-1 py-1 font-normal"
              :title="m.label"
            >
              {{ m.short }}
            </th>
            <th class="px-1 py-1 font-normal">得分</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="u in units"
            :key="u.id ?? u.name"
            class="border-b border-[#F0F0F0] last:border-0 hover:bg-[#FAFAFA]"
          >
            <td class="py-1.5 text-left text-gray-700">{{ u.name }}</td>
            <td
              v-for="m in METRICS"
              :key="m.key"
              class="px-1 py-1"
              :style="
                u.metrics?.[m.key] === null || u.metrics?.[m.key] === undefined
                  ? NA_STYLE
                  : { backgroundColor: cellColor(u.metrics?.[m.key]) }
              "
              :title="`${m.label}: ${fmtPct(u.metrics?.[m.key])}`"
            >
              <span class="font-mono text-gray-600">
                {{ fmtPct(u.metrics?.[m.key]) }}
              </span>
            </td>
            <td
              class="px-1 py-1 font-mono font-medium"
              :style="{ color: scoreColor(u.score) }"
            >
              {{ u.score?.toFixed(1) ?? '—' }}
            </td>
          </tr>
          <tr v-if="units.length === 0">
            <td :colspan="METRICS.length + 2" class="py-6 text-center text-xs text-gray-300">
              暂无单元热力数据
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
