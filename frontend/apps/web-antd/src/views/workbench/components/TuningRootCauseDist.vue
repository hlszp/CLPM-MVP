<script setup lang="ts">
/**
 * 劣化分布 · V3.1 SVG 饼图（4 类根因占比饼图 + 右侧图例）
 *
 * 数据来源：props.rows 聚合到 4 类根因
 *   振荡类 / 阀位偏差 / 激励不足 / 模型失配
 *
 * 视觉：
 *   外径 42 饼图（无内圈）
 *   右侧：4 项图例色块 + 标签 + 数字 + 百分比
 *   底部：总数行
 */
import type { WorkbenchApi } from '#/api/workbench';

import { computed } from 'vue';

import HelpBubble from './HelpBubble.vue';

interface Props { rows: WorkbenchApi.TuneQueueItem[]; }
const props = defineProps<Props>();

const helpItems = [
  { label: '饼图', text: '4 类劣化根因占比：振荡类 / 阀位偏差 / 激励不足 / 模型失配。' },
  { label: '分类规则', text: '按建议来源关键词识别；无法识别时按评分阈值 fallback：<65 振荡 / <68 阀位 / <73 激励 / ≥73 模型失配。' },
];

type Cause = 'excitation' | 'model_mismatch' | 'oscillation' | 'valve_bias';

const CAUSE_META: { color: string; key: Cause; label: string }[] = [
  { key: 'oscillation',    color: '#FF4D4F', label: '振荡类' },
  { key: 'valve_bias',     color: '#FA8C16', label: '阀位偏差' },
  { key: 'excitation',     color: '#FADB14', label: '激励不足' },
  { key: 'model_mismatch', color: '#1F4E79', label: '模型失配' },
];

const causeOf = (r: WorkbenchApi.TuneQueueItem): Cause => {
  const src = (r.source ?? '').toLowerCase();
  if (src.includes('激励') || src.includes('excitation')) return 'excitation';
  if (src.includes('阀位') || src.includes('valve')) return 'valve_bias';
  if (src.includes('振荡') || /oscillat|hunt/.test(src)) return 'oscillation';
  if (src.includes('模型') || /model|mismatch/.test(src)) return 'model_mismatch';
  const s = r.score ?? 999;
  if (s < 65) return 'oscillation';
  if (s < 68) return 'valve_bias';
  if (s < 73) return 'excitation';
  return 'model_mismatch';
};

const C = 2 * Math.PI * 42; // 周长 ≈ 263.89

const counts = computed<Record<Cause, number>>(() => {
  const m: Record<Cause, number> = {
    excitation: 0, model_mismatch: 0, oscillation: 0, valve_bias: 0,
  };
  for (const r of props.rows) m[causeOf(r)] += 1;
  return m;
});
const total = computed(() =>
  Object.values(counts.value).reduce((s, n) => s + n, 0),
);

type Seg = { color: string; count: number; key: Cause; label: string; len: number; offset: number; pct: number };
const segments = computed<Seg[]>(() => {
  const t = total.value > 0 ? total.value : 1;
  let acc = 0;
  return CAUSE_META.map((meta) => {
    const count = counts.value[meta.key];
    const pct = count > 0 ? (count / t) * 100 : 0;
    const len = (count / t) * C;
    const seg: Seg = {
      color: meta.color, key: meta.key, label: meta.label,
      count, len, offset: acc, pct,
    };
    acc += len;
    return seg;
  });
});
const hasData = computed(() => total.value > 0);
</script>

<template>
  <div class="flex h-full min-h-0 flex-col">
    <div class="flex h-[22px] flex-none items-center border-b border-[#E4E7ED] px-[7px] text-[10.5px] font-semibold text-[#1F4E79]">
      <span class="mr-[5px] inline-block h-[11px] w-[3px] rounded-[2px] bg-[#FA8C16]"></span>
      劣化分布 · 根因占比
      <HelpBubble :size="12" theme="blue" title="劣化分布说明" :items="helpItems" class="ml-1" />
      <span class="ml-auto text-[9.5px] font-normal text-[#8C8C8C]">共 {{ total }}</span>
    </div>
    <div class="min-h-0 flex-1 flex items-center justify-center overflow-hidden p-[6px_8px]">
      <div class="flex h-full w-full items-center gap-[6px]">
        <!-- 饼图 -->
        <div class="relative flex h-full max-h-[150px] w-[150px] flex-none items-center justify-center">
          <svg v-if="hasData" viewBox="0 0 100 100" class="h-full w-full">
            <circle
              cx="50" cy="50" r="42" fill="none"
              stroke="#F5F5F5" stroke-width="14"
            />
            <circle
              v-for="seg in segments"
              v-show="seg.count > 0"
              :key="seg.key"
              cx="50" cy="50" r="42" fill="none"
              :stroke="seg.color" stroke-width="14"
              :stroke-dasharray="`${seg.len} ${C - seg.len}`"
              :stroke-dashoffset="`-${seg.offset}`"
              transform="rotate(-90 50 50)"
            />
          </svg>
          <div v-else class="flex flex-col items-center justify-center text-[10px] text-[#8C8C8C]">
            <span>暂无劣化</span>
            <span>根因数据</span>
          </div>
          <!-- 中心总数 -->
          <div v-if="hasData" class="absolute inset-0 flex flex-col items-center justify-center">
            <span class="text-[15px] font-bold tabular-nums text-[#1F4E79]">{{ total }}</span>
            <span class="text-[9px] text-[#8C8C8C]">回路</span>
          </div>
        </div>
        <!-- 图例 -->
        <div class="flex min-h-0 flex-1 flex-col justify-center gap-[3px] text-[10px]">
          <template v-for="seg in segments" :key="seg.key">
            <div class="flex items-center gap-[4px]">
              <span
                class="inline-block h-[8px] w-[8px] flex-none rounded-[1px]"
                :style="{ background: seg.color }"
              ></span>
              <span class="flex-none font-medium" :style="{ color: seg.color }">{{ seg.label }}</span>
              <span class="ml-auto font-bold tabular-nums">{{ seg.count }}</span>
              <span class="w-[34px] text-right text-[9px] tabular-nums text-[#8C8C8C]">{{ seg.pct.toFixed(0) }}%</span>
            </div>
          </template>
          <div class="mt-[2px] flex items-center gap-[4px] border-t border-dashed border-[#E4E7ED] pt-[3px] text-[9.5px]">
            <span class="text-[#8C8C8C]">总劣化</span>
            <span class="ml-auto font-bold tabular-nums text-[#FA8C16]">{{ total }}</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
