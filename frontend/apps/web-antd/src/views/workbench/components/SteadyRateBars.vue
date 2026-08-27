<script setup lang="ts">
/**
 * 系统总览 · 单元平稳率条形图（Row2 c4，替换原「模块健康·热插拔」运维面板）
 *
 * - 数据：A-01 overview.units[].metrics.steady_rate（0~1 小数，前端 ×100 归一）
 * - 排序：按值升序（最差在顶部，视线优先落风险）
 * - 色阶（对齐 EvalHeatMatrix 正向指标阈值）：
 *     ≥92 深绿 / ≥84 浅绿 / ≥76 橙 / <76 红；null → 斜纹 + —
 * - 目标线：92（热力矩阵绿色阈值）竖虚线贯通各行
 * - 底部：全厂平稳率（GLOBAL 窗口 metrics.steady_rate）+ 达标单元数
 * - 工业约束：无动画、无装饰，单行 24px
 */
import type { WorkbenchApi } from '#/api/workbench';

import { computed } from 'vue';

import { useWorkbenchDrill } from '../utils/drill';

const props = defineProps<{
  /** 全厂平稳率（当前窗口 GLOBAL metrics.steady_rate，0~1） */
  globalSteady?: null | number;
  units?: WorkbenchApi.UnitRow[];
}>();

const { drill, resolvePlantNodeId } = useWorkbenchDrill();

const TARGET = 92;

/** metrics 为 0~1 小数（与 KpiCards 同口径），归一到 0~100 */
function toPct(v: null | number | undefined): null | number {
  return v === null || v === undefined ? null : v * 100;
}

const globalSteadyPct = computed(() => toPct(props.globalSteady));

interface Row {
  key: string;
  name: string;
  /** 单元 source_node_id（用于行级 plantNodeId 解析） */
  sourceId: null | number;
  value: null | number;
}

const rows = computed<Row[]>(() =>
  (props.units ?? [])
    .map((u) => ({
      key: `${u.id ?? u.name}`,
      name: u.name,
      sourceId: u.id ?? null,
      value: toPct(u.metrics?.steady_rate),
    }))
    .toSorted((a, b) => {
      // null 排最后；其余升序（最差在顶）
      if (a.value === null && b.value === null) return 0;
      if (a.value === null) return 1;
      if (b.value === null) return -1;
      return a.value - b.value;
    }),
);

const validCount = computed(() => rows.value.filter((r) => r.value !== null).length);
const passCount = computed(
  () => rows.value.filter((r) => r.value !== null && r.value >= TARGET).length,
);

function barColor(v: null | number): string {
  if (v === null) return 'transparent';
  if (v >= TARGET) return '#2E7D32';
  if (v >= 84) return '#7CB342';
  if (v >= 76) return '#E8710A';
  return '#D93025';
}

function barWidth(v: null | number): number {
  if (v === null) return 100; // 斜纹铺满
  return Math.min(100, Math.max(0, v));
}

// N/A 斜纹（与 HeatMatrix 同规范：缺数据用斜纹而非纯灰）
const NA_STYLE = {
  backgroundImage:
    'repeating-linear-gradient(45deg, #F1F4F8 0, #F1F4F8 4px, #E7ECF2 4px, #E7ECF2 8px)',
} as const;

function fmt(v: null | number): string {
  return v === null || v === undefined ? '—' : v.toFixed(1);
}

/**
 * 追溯矩阵 §2 下钻：行点击 → 指标分析·平稳率。
 * 行数据带单元 source_node_id 时尝试解析 plantNodeId（scopeTree 仅含
 * FACTORY/AREA，UNIT 通常解析不到 → 只带 metric，避免错口径）。
 */
function onRowClick(r: Row) {
  const plantNodeId = resolvePlantNodeId(r.sourceId);
  drill('assess', '/metric/indicator-analysis', {
    metric: 'steady_rate',
    ...(plantNodeId ? { plantNodeId } : {}),
  });
}
</script>

<template>
  <div class="flex h-full flex-col rounded border border-[#E4E7ED] bg-white">
    <!-- 头部 -->
    <div class="flex flex-none items-center justify-between border-b border-[#E4E7ED] px-3 py-2">
      <span class="flex items-center gap-1.5 text-xs font-medium text-gray-700">
        <span class="inline-block h-3 w-1 rounded-sm bg-[#2E7D32]"></span>
        单元平稳率
      </span>
      <span class="text-[10px] text-gray-400">{{ rows.length }} 单元 · 升序 · 目标 ≥{{ TARGET }}</span>
    </div>

    <!-- 条形列表 -->
    <div class="flex flex-1 flex-col justify-center gap-[6px] overflow-auto px-3 py-2">
      <div
        v-for="r in rows"
        :key="r.key"
        class="flex cursor-pointer items-center gap-2 rounded-sm hover:bg-[#FAFBFC]"
        :title="`${r.name} · 平稳率：${fmt(r.value)}%（目标 ≥${TARGET}）· 点击查看指标分析`"
        @click="onRowClick(r)"
      >
        <!-- 单元名 -->
        <span class="w-[76px] flex-none truncate text-[11px] text-gray-700">{{ r.name }}</span>

        <!-- 条形轨道（含目标线） -->
        <div class="relative h-[10px] min-w-0 flex-1 rounded-sm bg-[#F5F7FA]">
          <div
            class="h-full rounded-sm"
            :style="{
              width: `${barWidth(r.value)}%`,
              backgroundColor: barColor(r.value),
              ...(r.value === null ? NA_STYLE : {}),
            }"
          ></div>
          <!-- 目标线 92 -->
          <div
            class="absolute top-[-2px] bottom-[-2px] border-l border-dashed border-[#8C8C8C]"
            :style="{ left: `${TARGET}%` }"
          ></div>
        </div>

        <!-- 数值 -->
        <span
          class="w-[34px] flex-none text-right text-[11px] font-medium tabular-nums"
          :style="{ color: r.value === null ? '#BFBFBF' : barColor(r.value) }"
        >{{ fmt(r.value) }}</span>
      </div>

      <!-- 空态 -->
      <div
        v-if="rows.length === 0"
        class="flex flex-1 items-center justify-center text-xs text-gray-400"
      >
        暂无单元数据
      </div>
    </div>

    <!-- 底部汇总 -->
    <div class="flex-none border-t border-[#E4E7ED] px-3 py-1.5 text-[10px] text-gray-500">
      全厂平稳率 <b class="text-gray-700">{{ fmt(globalSteadyPct) }}%</b>
      · 达标（≥{{ TARGET }}）<b class="text-gray-700">{{ passCount }}/{{ validCount }}</b> 单元
    </div>
  </div>
</template>
