<script setup lang="ts">
/**
 * 重开列表 · 反向色阶条（reopened 降序 Top N，默认 8）
 *
 * 数据来源：props.loops（getHandlingLoopsApi sort=reopened）
 *   过滤 orderCounts.reopened > 0 → 取前 8
 *
 * 视觉：
 *   每行：排名 + 回路名 + 色阶条（宽=reopened/max，色=反向色阶）+ 重开数 + KPI delta
 *   反向色阶：reopened/max ≥0.75 红 / ≥0.5 橙 / ≥0.25 黄 / else 蓝
 *   点击 → emit select(loop)（容器可联动 TaskDetailCard 或定位看板）
 */
import type { HandlingApi } from '#/api/handling';

import { computed } from 'vue';

import HelpBubble from '../HelpBubble.vue';

interface Props {
  loops: HandlingApi.LoopAggregateItem[];
  /** 取前 N（默认 8） */
  topN?: number;
}

const props = withDefaults(defineProps<Props>(), {
  topN: 8,
});

const emit = defineEmits<{
  (e: 'select', loop: HandlingApi.LoopAggregateItem): void;
}>();

const helpItems = [
  { label: '色阶条', text: '重开次数降序 Top 8；宽=reopened/max，反向色阶（多=红/少=蓝）。' },
  { label: 'KPI delta', text: '最近一次闭环 kpi_after.score − kpi_before.score（绿改善/红恶化/— 无闭环）。' },
  { label: '联动', text: '点击行 → emit select(loop)，容器可联动任务详情或定位看板工单。' },
];

interface ReopenRow {
  barColor: string;
  barWidth: number;
  delta: null | number;
  deltaColor: string;
  deltaText: string;
  loop: HandlingApi.LoopAggregateItem;
  reopened: number;
}

function barColorOf(ratio: number): string {
  if (ratio >= 0.75) return '#FF4D4F';
  if (ratio >= 0.5) return '#FA8C16';
  if (ratio >= 0.25) return '#FADB14';
  return '#1F4E79';
}

function deltaColor(delta: null | number): string {
  if (delta === null) return '#BFBFBF';
  return delta >= 0 ? '#52C41A' : '#FF4D4F';
}

function deltaText(delta: null | number): string {
  if (delta === null) return '—';
  const sign = delta >= 0 ? '+' : '';
  return `${sign}${Math.round(delta * 10) / 10}`;
}

const rows = computed<ReopenRow[]>(() => {
  const filtered = props.loops
    .filter((l) => l.orderCounts.reopened > 0)
    .toSorted((a, b) => b.orderCounts.reopened - a.orderCounts.reopened)
    .slice(0, props.topN);
  const max = filtered.length > 0
    ? Math.max(...filtered.map((l) => l.orderCounts.reopened))
    : 1;
  return filtered.map((l) => {
    const reopened = l.orderCounts.reopened;
    const ratio = reopened / max;
    return {
      barColor: barColorOf(ratio),
      barWidth: Math.round(ratio * 100),
      delta: l.lastClosedKpiDelta ?? null,
      deltaColor: deltaColor(l.lastClosedKpiDelta ?? null),
      deltaText: deltaText(l.lastClosedKpiDelta ?? null),
      loop: l,
      reopened,
    };
  });
});

const totalReopen = computed(() =>
  rows.value.reduce((s, r) => s + r.reopened, 0),
);
</script>

<template>
  <div class="flex h-full min-h-0 flex-col">
    <div
      class="flex h-[22px] flex-none items-center border-b border-[#E4E7ED] px-[7px] text-[10.5px] font-semibold text-[#1F4E79]"
    >
      <span
        class="mr-[5px] inline-block h-[11px] w-[3px] rounded-[2px] bg-[#FF4D4F]"
      ></span>
      重开列表 · Top {{ props.topN }}
      <HelpBubble
        :size="12"
        theme="blue"
        title="重开列表说明"
        :items="helpItems"
        class="ml-1"
      />
      <span class="ml-auto text-[9.5px] font-normal text-[#8C8C8C]">
        共 {{ totalReopen }}
      </span>
    </div>
    <div
      class="flex min-h-0 flex-1 flex-col gap-[3px] overflow-y-auto p-[5px_8px]"
    >
      <template v-if="rows.length > 0">
        <div
          v-for="(r, i) in rows"
          :key="r.loop.loopId"
          class="flex cursor-pointer items-center gap-[4px] rounded-[1px] px-[2px] py-[1px] transition-colors hover:bg-[#FAFBFC]"
          @click="emit('select', r.loop)"
        >
          <span
            class="w-[14px] flex-none text-center text-[9px] font-bold tabular-nums"
            :style="{ color: r.barColor }"
          >{{ i + 1 }}</span>
          <span class="w-[64px] flex-none truncate text-[10px] font-medium text-[#595959]">
            {{ r.loop.loopTagName }}
          </span>
          <div
            class="relative h-[13px] flex-1 overflow-hidden rounded-[1px] bg-[#F5F5F5]"
          >
            <div
              class="h-full rounded-[1px]"
              :style="{ width: `${r.barWidth}%`, background: r.barColor }"
            ></div>
          </div>
          <span
            class="w-[14px] flex-none text-right text-[9.5px] font-bold tabular-nums"
            :style="{ color: r.barColor }"
          >{{ r.reopened }}</span>
          <span
            class="w-[34px] flex-none text-right text-[9px] font-medium tabular-nums"
            :style="{ color: r.deltaColor }"
          >{{ r.deltaText }}</span>
        </div>
      </template>
      <div
        v-else
        class="flex flex-1 items-center justify-center text-[9.5px] text-[#BFBFBF]"
      >
        无重开工单 ✅
      </div>
    </div>
  </div>
</template>
