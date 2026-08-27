<script setup lang="ts">
/**
 * 诊断规则 · 命中×解决率（Row3 c5，替换原「诊断频次·时间分布」）
 *
 * 管理价值：回答「规则库是否在有效运转」——哪些规则在命中、命中后问题是否被解决
 *
 * - 数据：A-03 rule_stats（rule_id/name/hits/resolved_rate）
 * - 排序：按命中次数降序，最多显示 8 条
 * - 每行：规则名 + 命中条形（宽 = hits/maxHits）+ 命中数 + 解决率色阶
 * - 解决率色阶：≥80% 绿 / ≥50% 橙 / <50% 红 / null → —
 * - 底部：总命中 + 加权平均解决率
 * - 工业约束：无动画、无装饰，单行 24px
 */
import type { WorkbenchApi } from '#/api/workbench';

import { computed } from 'vue';

const props = defineProps<{
  ruleStats?: WorkbenchApi.DiagnosisRuleStat[];
}>();

const MAX_ROWS = 8;

interface Row {
  hits: number;
  key: string;
  name: string;
  resolvedRate: null | number; // 0~1
}

const rows = computed<Row[]>(() =>
  (props.ruleStats ?? [])
    .map((r, i) => ({
      key: r.rule_id ?? `rule-${i}`,
      name: r.name ?? r.rule_id ?? '未命名规则',
      hits: r.hits ?? 0,
      resolvedRate: r.resolved_rate ?? null,
    }))
    .toSorted((a, b) => b.hits - a.hits)
    .slice(0, MAX_ROWS),
);

const maxHits = computed(() => Math.max(1, ...rows.value.map((r) => r.hits)));
const totalHits = computed(() => (props.ruleStats ?? []).reduce((s, r) => s + (r.hits ?? 0), 0));

/** 加权平均解决率（按命中数加权，仅计有解决率的规则） */
const avgResolved = computed<null | number>(() => {
  let wSum = 0;
  let wTotal = 0;
  for (const r of props.ruleStats ?? []) {
    if (r.resolved_rate === null || r.resolved_rate === undefined) continue;
    const w = Math.max(1, r.hits ?? 0);
    wSum += r.resolved_rate * w;
    wTotal += w;
  }
  return wTotal > 0 ? wSum / wTotal : null;
});

function barWidth(hits: number): number {
  return Math.max(2, (hits / maxHits.value) * 100);
}

function rateColor(rate: null | number): string {
  if (rate === null) return '#BFBFBF';
  if (rate >= 0.8) return '#2E7D32';
  if (rate >= 0.5) return '#E8710A';
  return '#D93025';
}

function fmtRate(rate: null | number): string {
  return rate === null ? '—' : `${Math.round(rate * 100)}%`;
}
</script>

<template>
  <div class="flex h-full flex-col bg-white">
    <!-- 标题栏 -->
    <div class="flex flex-none items-center justify-between border-b border-[#E4E7ED] px-3 py-1.5">
      <span class="flex items-center gap-1.5 text-xs font-medium text-[#1F4E79]">
        <span class="inline-block h-1 w-3 rounded-sm bg-[#1F4E79]"></span>
        诊断规则 · 命中 × 解决率
        <span class="text-[10px] font-normal text-gray-400">
          {{ rows.length }} 条规则 · 总命中 {{ totalHits }} 次
        </span>
      </span>
      <span class="text-[10px] text-gray-500">按命中降序 · Top {{ MAX_ROWS }}</span>
    </div>

    <!-- 规则列表 -->
    <div class="flex flex-1 flex-col justify-center gap-[7px] overflow-auto px-3 py-2">
      <div
        v-for="r in rows"
        :key="r.key"
        class="flex items-center gap-2"
        :title="`${r.name}：命中 ${r.hits} 次 · 解决率 ${fmtRate(r.resolvedRate)}`"
      >
        <!-- 规则名 -->
        <span class="w-[128px] flex-none truncate text-[11px] text-gray-700">{{ r.name }}</span>

        <!-- 命中条形 -->
        <div class="relative h-[10px] min-w-0 flex-1 rounded-sm bg-[#F5F7FA]">
          <div
            class="h-full rounded-sm bg-[#1F4E79]"
            :style="{ width: `${barWidth(r.hits)}%` }"
          ></div>
        </div>

        <!-- 命中数 -->
        <span class="w-[26px] flex-none text-right text-[11px] font-medium tabular-nums text-gray-700">
          {{ r.hits }}
        </span>

        <!-- 解决率 -->
        <span
          class="w-[36px] flex-none text-right text-[11px] font-medium tabular-nums"
          :style="{ color: rateColor(r.resolvedRate) }"
        >{{ fmtRate(r.resolvedRate) }}</span>
      </div>

      <!-- 空态 -->
      <div
        v-if="rows.length === 0"
        class="flex flex-1 items-center justify-center text-xs text-gray-400"
      >
        近窗口暂无规则命中数据
      </div>
    </div>

    <!-- 底部汇总 -->
    <div class="flex-none border-t border-dashed border-[#E4E7ED] px-3 py-1.5 text-[10.5px] text-gray-500">
      总命中 <span class="font-semibold text-gray-700">{{ totalHits }}</span> 次
      · 加权平均解决率
      <span class="font-semibold" :style="{ color: rateColor(avgResolved) }">{{ fmtRate(avgResolved) }}</span>
    </div>
  </div>
</template>
