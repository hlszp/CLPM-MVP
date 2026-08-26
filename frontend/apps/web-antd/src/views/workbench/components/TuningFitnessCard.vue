<script setup lang="ts">
/**
 * 适用性分级概览 · V3.2 SVG 饼图（L0~L4 占比饼图 + 图例区顶部级别摘要）
 *
 * 数据来源：WorkbenchApi.DiagnosisFitnessGates.level / score / level_counts
 * 缺失 level_counts 时用 8/16/32/28/16 demo 占比并标「（示例）」
 *
 * 视觉：
 *   实心扇形饼图（半径 42，0°=12 点顺时针）
 *   5 段扇形（L0 红 / L1 橙 / L2 绿 / L3 蓝 / L4 浅绿）
 *   图例区顶部：当前级别徽章 + 评分/100（原 donut 中心徽章位移此）
 *   底部：5 段图例 + 数字刻度
 */
import type { WorkbenchApi } from '#/api/workbench';

import { computed } from 'vue';

import HelpBubble from './HelpBubble.vue';

interface Props {
  gates: null | WorkbenchApi.DiagnosisFitnessGates;
  queue?: WorkbenchApi.TuneQueueItem[];
}
const props = withDefaults(defineProps<Props>(), {
  queue: () => [],
});

const helpItems = [
  { label: '饼图', text: 'L0~L4 五档分级占比（按 KpiSnapshotHourly.score 分桶）。' },
  { label: '中心徽章', text: '整体最高非空级别 + 评分/100（取自 fitness_gates.score）。' },
  { label: '示例', text: 'level_counts 为空时使用 8/16/32/28/16 demo 占比并标注「（示例）」。' },
];

const LEVEL_COLORS: Record<string, string> = {
  L0: '#FF4D4F', L1: '#FA8C16', L2: '#52C41A', L3: '#1F4E79', L4: '#95DE64',
};
const LEVEL_ORDER = ['L0', 'L1', 'L2', 'L3', 'L4'] as const;
const LEVEL_LABELS: Record<string, string> = {
  L0: '阻塞', L1: '待确认', L2: '待数据', L3: '待激励', L4: '就绪',
};

// 极坐标转笛卡尔（0°=12 点，顺时针）
function polarToCartesian(cx: number, cy: number, r: number, angleDeg: number) {
  const rad = (angleDeg * Math.PI) / 180;
  return { x: cx + r * Math.sin(rad), y: cy - r * Math.cos(rad) };
}
// 扇形 path（顺时针，0°=12 点）；整圆特殊处理避免起止重合
function arcPath(cx: number, cy: number, r: number, startDeg: number, endDeg: number) {
  if (endDeg - startDeg >= 359.99) {
    return `M${cx} ${cy - r} A${r} ${r} 0 1 1 ${cx - 0.01} ${cy - r} Z`;
  }
  const start = polarToCartesian(cx, cy, r, startDeg);
  const end = polarToCartesian(cx, cy, r, endDeg);
  const largeArc = endDeg - startDeg > 180 ? 1 : 0;
  return `M${cx} ${cy} L${start.x.toFixed(2)} ${start.y.toFixed(2)} A${r} ${r} 0 ${largeArc} 1 ${end.x.toFixed(2)} ${end.y.toFixed(2)} Z`;
}

const levelCountsDemo: Record<string, number> = {
  L0: 8, L1: 16, L2: 32, L3: 28, L4: 16,
};
const levelCounts = computed<Record<string, number>>(() => {
  const lc = props.gates?.level_counts as unknown as Record<string, number> | undefined;
  if (lc && (lc.L0 || lc.L1 || lc.L2 || lc.L3 || lc.L4)) return lc as Record<string, number>;
  return levelCountsDemo;
});
const usingFallback = computed(() => {
  const lc = props.gates?.level_counts as unknown as Record<string, number> | undefined;
  return !(lc && (lc.L0 || lc.L1 || lc.L2 || lc.L3 || lc.L4));
});
const total = computed(() =>
  Object.values(levelCounts.value).reduce((s, n) => s + (Math.max(n, 0)), 0),
);

type Seg = { color: string; count: number; key: string; path: string; pct: number };
const segments = computed<Seg[]>(() => {
  const t = total.value > 0 ? total.value : 1;
  let acc = 0;
  return LEVEL_ORDER.map((k) => {
    const count = levelCounts.value[k] ?? 0;
    const pct = count > 0 ? (count / t) * 100 : 0;
    const startAngle = (acc / t) * 360;
    acc += count;
    const endAngle = (acc / t) * 360;
    const path = count > 0 ? arcPath(50, 50, 42, startAngle, endAngle) : '';
    return { color: LEVEL_COLORS[k] ?? '#1F4E79', key: k, count, pct, path };
  });
});

const level = computed(() => props.gates?.level ?? 'L3');
const levelLabel = computed(() => LEVEL_LABELS[level.value] ?? '未知');
const levelColor = computed(() => LEVEL_COLORS[level.value] ?? '#1F4E79');
const score = computed(() => {
  const n = props.gates?.score;
  return typeof n === 'number' ? Math.round(n) : 70;
});

const evaluated = computed(() => props.gates?.evaluated ?? total.value);
</script>

<template>
  <div class="flex h-full min-h-0 flex-col">
    <div class="flex h-[22px] flex-none items-center border-b border-[#E4E7ED] px-[7px] text-[10.5px] font-semibold text-[#1F4E79]">
      <span class="mr-[5px] inline-block h-[11px] w-[3px] rounded-[2px] bg-[#1F4E79]"></span>
      适用性分级概览 · L0~L4
      <HelpBubble :size="12" theme="blue" title="适用性分级说明" :items="helpItems" class="ml-1" />
      <span class="ml-auto text-[9.5px] font-normal text-[#8C8C8C]">B-09</span>
    </div>
    <div class="min-h-0 flex-1 flex items-center justify-center overflow-hidden p-[6px_8px]">
      <div class="flex h-full w-full items-center gap-[6px]">
        <!-- 饼图 -->
        <div class="relative flex h-full max-h-[150px] w-[150px] flex-none items-center justify-center">
          <svg viewBox="0 0 100 100" class="h-full w-full">
            <!-- L0~L4 各扇形（顺时针从 12 点起） -->
            <path
              v-for="seg in segments"
              v-show="seg.count > 0"
              :key="seg.key"
              :d="seg.path"
              :fill="seg.color"
              stroke="#fff"
              stroke-width="0.5"
            />
          </svg>
        </div>
        <!-- 图例 + 数字刻度 -->
        <div class="flex min-h-0 flex-1 flex-col justify-center gap-[3px] text-[10px]">
          <!-- 当前级别摘要（原 donut 中心徽章移此） -->
          <div class="flex items-center gap-[4px] border-b border-dashed border-[#E4E7ED] pb-[3px] text-[9.5px]">
            <span
              class="rounded-[2px] px-[5px] py-[0.5px] text-[10.5px] font-bold text-white"
              :style="{ background: levelColor }"
            >{{ level }}</span>
            <span class="font-medium text-[#595959]">{{ levelLabel }}</span>
            <span class="ml-auto font-bold tabular-nums" :style="{ color: levelColor }">{{ score }}<span class="text-[7.5px] text-[#8C8C8C]">/100</span></span>
          </div>
          <template v-for="seg in segments" :key="seg.key">
            <div class="flex items-center gap-[4px]">
              <span
                class="inline-block h-[8px] w-[8px] flex-none rounded-[1px]"
                :style="{ background: seg.color }"
              ></span>
              <span class="flex-none font-medium" :style="{ color: seg.color }">{{ seg.key }}</span>
              <span class="flex-none text-[9px] text-[#8C8C8C]">{{ LEVEL_LABELS[seg.key] }}</span>
              <span class="ml-auto font-bold tabular-nums">{{ seg.count }}</span>
              <span class="w-[34px] text-right text-[9px] tabular-nums text-[#8C8C8C]">{{ seg.pct.toFixed(0) }}%</span>
            </div>
          </template>
          <div class="mt-[2px] flex items-center gap-[4px] border-t border-dashed border-[#E4E7ED] pt-[3px] text-[9.5px]">
            <span class="text-[#8C8C8C]">已评估</span>
            <span class="ml-auto font-bold tabular-nums text-[#1F4E79]">{{ evaluated }}</span>
            <span v-if="usingFallback" class="text-[#FA8C16]">（示例）</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
