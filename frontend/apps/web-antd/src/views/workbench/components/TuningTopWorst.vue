<script setup lang="ts">
/**
 * Top 5 最劣回路排名（U3b · V3）
 * - score 升序取前 5 → 越差越靠前
 * - 条形宽 = score / 100；色阶：&lt;65 红 / &lt;73 橙 / ≥73 绿
 * - 末端：回退行 → 红 tag；其他 → 蓝色「▶整定」按钮（emit sim）
 * - 行点击 → emit locate(row) 联动清单定位
 */
import type { WorkbenchApi } from '#/api/workbench';

import { computed } from 'vue';

interface Props { rows: WorkbenchApi.TuneQueueItem[]; }
const props = defineProps<Props>();
const emit = defineEmits<{
  (e: 'locate', row: WorkbenchApi.TuneQueueItem): void;
  (e: 'sim',    row: WorkbenchApi.TuneQueueItem): void;
}>();

const top5 = computed(() =>
  [...props.rows]
    .filter((r) => typeof r.score === 'number')
    .toSorted((a, b) => (a.score as number) - (b.score as number))
    .slice(0, 5),
);

const scoreColor = (s: number) => (s < 65 ? '#FF4D4F' : (s < 73 ? '#FA8C16' : '#52C41A'));
const scoreBg = (s: number) => (s < 65 ? '#FFE0E0' : (s < 73 ? '#FFE7BA' : '#D9F7BE'));
const isFallback = (r: WorkbenchApi.TuneQueueItem) =>
  /回退|rollback/i.test(r.source ?? '');
const fmt = (n: null | number | undefined) =>
  (n === null || n === undefined ? '—' : n.toFixed(1));
</script>

<template>
  <div class="flex h-full min-h-0 flex-col">
    <div class="flex h-[22px] flex-none items-center border-b border-[#E4E7ED] px-[7px] text-[10.5px] font-semibold text-[#1F4E79]">
      <span class="mr-[5px] inline-block h-[11px] w-[3px] rounded-[2px] bg-[#FF4D4F]"></span>
      Top 5 最劣回路 · 点击行 → 下方清单联动定位
    </div>
    <div class="min-h-0 flex-1 flex flex-col justify-between gap-[3px] overflow-hidden p-[5px_8px]">
      <template v-if="top5.length === 0">
        <div class="py-4 text-center text-[10.5px] text-[#8C8C8C]">暂无评分数据</div>
      </template>
      <template v-else>
        <template v-for="r in top5" :key="r.loop_id">
          <div
            class="flex items-center gap-[4px]"
            @click="emit('locate', r)"
          >
            <span
              class="w-[56px] flex-none text-[10.5px] font-bold"
              :style="{ color: scoreColor(r.score as number) }"
            >{{ r.loop_id }}</span>
            <div
              class="relative h-[13px] flex-1 overflow-hidden rounded-[1px]"
              :style="{ background: scoreBg(r.score as number) }"
            >
              <div
                class="h-full rounded-[1px]"
                :style="{
                  width: `${r.score as number}%`,
                  background: scoreColor(r.score as number),
                }"
              ></div>
              <span class="absolute inset-0 right-[3px] flex items-center justify-end text-[9px] font-bold text-white">
                {{ fmt(r.score) }}
              </span>
            </div>
            <span
              v-if="isFallback(r)"
              class="flex-none rounded-[1px] border border-[#FFCCC7] bg-[#FFF1F0] px-[4px] text-[9px] text-[#FF4D4F]"
            >⚠回退</span>
            <button
              v-else
              class="flex-none rounded-[1px] bg-[#2563EB] px-[6px] py-[1px] text-[9.5px] font-medium text-white"
              @click.stop="emit('sim', r)"
            >▶整定</button>
          </div>
        </template>
      </template>
    </div>
  </div>
</template>
