<script setup lang="ts">
/**
 * 性能评估 · 单元 × 指标热力矩阵（原型对齐 1:1 · Row2 c5）
 *
 * 复刻原型 renderEval() Row2 右：
 * - 8 单元 × 6 指标（有效自控/平稳率/准确率/快速率/好值率/故障率）
 * - 4 级色阶：≥92 绿(#C8E6C9) / 84–92 黄(#FFF59D) / 76–84 红(#FFCDD2) / <76 深红(#EF9A9A)
 * - 故障率列反向着色（越低越好）：≤1.5 绿 / ≤3 黄 / ≤5 红 / >5 深红
 * - 不可评单元格 → 斜纹底（CSS hatch），显示 —
 * - tooltip：单元 · 指标：值%
 * - 底部色阶图例 + 斜纹说明
 *
 * 值口径：后端 shape_heatmap 已归一为 0~100（与原型 heatColor 阈值对齐）。
 */
import type { WorkbenchApi } from '#/api/workbench';

import { computed } from 'vue';

import { useWorkbenchDrill } from '../utils/drill';

const props = defineProps<{
  heatmap?: WorkbenchApi.AssessmentHeatmap;
}>();

const { drill, resolvePlantNodeId } = useWorkbenchDrill();

const metrics = computed(() => props.heatmap?.metrics ?? []);
const units = computed(() => props.heatmap?.units ?? []);

/**
 * 热力指标 key → indicator-analysis 页面支持的 metric 参数（已核验）。
 * 页面 METRIC_OPTIONS sortKey 全集：auto_mode_rate / accuracy_rate / fast_rate /
 * steady_rate / good_value_rate / score（applyQuery 仅接受这些值，其余丢弃）。
 * 热力 effective_auto_rate（有效自控）页面无同名指标，取最接近的 auto_mode_rate（自控率）；
 * instrument_fault_rate（故障率）页面无对应指标 → undefined 不带 metric。
 */
const METRIC_KEY_MAP: Record<string, string | undefined> = {
  accuracy_rate: 'accuracy_rate',
  effective_auto_rate: 'auto_mode_rate',
  fast_rate: 'fast_rate',
  good_value_rate: 'good_value_rate',
  instrument_fault_rate: undefined,
  steady_rate: 'steady_rate',
};

/**
 * 追溯矩阵 §3 下钻：单元格点击 → 指标分析（metric + 单元 plantNodeId）。
 * 单元 source_node_id 通常不在 scopeTree（仅 FACTORY/AREA）→ 解析不到则只带 metric。
 */
function onCellClick(unitId: null | number, metricKey: string | undefined) {
  const metric = metricKey ? METRIC_KEY_MAP[metricKey] : undefined;
  const plantNodeId = resolvePlantNodeId(unitId);
  drill('assess', '/metric/indicator-analysis', {
    ...(metric ? { metric } : {}),
    ...(plantNodeId ? { plantNodeId } : {}),
  });
}

function cellColor(v: null | number, reverse: boolean): string {
  if (v === null || v === undefined) return 'transparent';
  if (reverse) {
    // 故障率反向：越低越好
    if (v <= 1.5) return '#C8E6C9';
    if (v <= 3) return '#FFF59D';
    if (v <= 5) return '#FFCDD2';
    return '#EF9A9A';
  }
  if (v >= 92) return '#C8E6C9';
  if (v >= 84) return '#FFF59D';
  if (v >= 76) return '#FFCDD2';
  return '#EF9A9A';
}

// N/A 斜纹背景（工业规范：缺数据用斜纹而非纯灰，避免误读为 0）
const NA_STYLE = {
  backgroundImage:
    'repeating-linear-gradient(45deg, #F1F4F8 0, #F1F4F8 4px, #E7ECF2 4px, #E7ECF2 8px)',
} as const;

function fmt(v: null | number): string {
  if (v === null || v === undefined) return '—';
  return v.toFixed(1);
}

function tipText(unitName: string, metricLabel: string, v: null | number): string {
  if (v === null || v === undefined) {
    return `${unitName} · ${metricLabel}：含不可评回路，单列不参评`;
  }
  return `${unitName} · ${metricLabel}：${v}%（分母为该单元参评回路）`;
}
</script>

<template>
  <div class="flex h-full flex-col rounded border border-[#E4E7ED] bg-white">
    <!-- 头部 -->
    <div class="flex flex-none items-center gap-2 border-b border-[#E4E7ED] px-3 py-1.5">
      <span class="inline-block h-3.5 w-1 rounded-sm bg-[#1F4E79]"></span>
      <span class="text-xs font-medium text-gray-700">单元 × 指标热力</span>
      <span class="text-[10px] text-gray-400">{{ units.length }} 单元 × 6 指标 · 绿优红劣</span>
    </div>

    <!-- 热力网格 -->
    <div
      class="flex-1 overflow-auto"
      style="display: grid; grid-template-columns: 86px repeat(6, minmax(0, 1fr)); gap: 2px; align-content: start; padding: 8px 12px"
    >
      <!-- 表头行 -->
      <div class="flex items-center justify-center text-[10.5px] text-gray-400"></div>
      <div
        v-for="m in metrics"
        :key="m.key"
        class="flex items-center justify-center whitespace-nowrap text-[10.5px] text-gray-400"
      >
        {{ m.label }}
      </div>

      <!-- 数据行 -->
      <template v-for="u in units" :key="u.id ?? u.name">
        <div
          class="flex items-center justify-end whitespace-nowrap pr-1.5 text-[11px] text-gray-600"
        >
          {{ u.name }}
        </div>
        <div
          v-for="(v, ci) in u.values"
          :key="ci"
          class="flex h-6 cursor-pointer items-center justify-center rounded text-[11px] font-mono tabular-nums text-gray-700 transition-none hover:ring-1 hover:ring-[#1F4E79]"
          :style="
            v === null || v === undefined
              ? NA_STYLE
              : { backgroundColor: cellColor(v, metrics[ci]?.reverse ?? false) }
          "
          :title="`${tipText(u.name, metrics[ci]?.label ?? '', v)} · 点击查看指标分析`"
          @click="onCellClick(u.id, metrics[ci]?.key)"
        >
          {{ fmt(v) }}
        </div>
      </template>

      <!-- 空态 -->
      <div
        v-if="units.length === 0"
        class="col-span-7 py-6 text-center text-xs text-gray-300"
      >
        暂无热力数据
      </div>
    </div>

    <!-- 底部色阶图例 -->
    <div class="flex flex-none items-center gap-2 border-t border-[#E4E7ED] bg-[#FBFCFE] px-3 py-1.5 text-[10.5px] text-gray-400">
      <span>色阶：</span>
      <span class="rounded px-1.5 text-[#1E7E34]" style="background-color: #C8E6C9">优</span>
      <span class="rounded px-1.5 text-[#8A5A00]" style="background-color: #FFF59D">中</span>
      <span class="rounded px-1.5 text-[#C5221F]" style="background-color: #FFCDD2">差</span>
      <span class="rounded px-1.5 text-[#8E1210]" style="background-color: #EF9A9A">劣</span>
      <span class="ml-auto">斜纹格 = 不可评回路</span>
    </div>
  </div>
</template>
