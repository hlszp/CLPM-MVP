<script setup lang="ts">
/**
 * SLA 汇总 · V3.1 SVG 环形图（及时率=normal 占比 + 图例 + 近6月闭环 mini bars）
 *
 * 数据来源：
 *   props.sla —— 容器由在办 orders 经 computeSlaBreakdown 派生（缺口2：用 plannedAt 代理 due）
 *   props.statistics —— getHandlingStatisticsApi().summary（闭环率/均时/无效重开率）
 *   props.monthly —— getHandlingStatisticsApi().monthly（近6月闭环数 mini bars）
 *
 * 视觉：
 *   外径 42 / 内径 30 环（环宽 12）
 *   3 段弧（超期红 / 临期橙 / 正常绿），无排程不计入环
 *   中心：及时率 X%
 *   图例：3 段 + 无排程计数
 *   底部：近6月闭环 mini bars + 闭环率/均时摘要行
 */
import type { HandlingApi } from '#/api/handling';

import { computed } from 'vue';

import { useWorkbenchDrill } from '../../utils/drill';
import HelpBubble from '../HelpBubble.vue';

interface SlaBreakdown {
  near: number;
  none: number;
  normal: number;
  overdue: number;
}

const props = withDefaults(
  defineProps<{
    monthly?: HandlingApi.MonthlyTrendItem[];
    sla: SlaBreakdown;
    statistics: HandlingApi.StatisticsSummary | null;
  }>(),
  {
    monthly: () => [],
  },
);

const { drill } = useWorkbenchDrill();

/** 追溯矩阵 §6 下钻：卡片点击 → 统计报告·处置报告（无额外参数） */
function onCardClick() {
  drill('handling', '/reports/handling', {});
}

const helpItems = [
  { label: '环形图', text: 'SLA 及时率=正常档占比（超期红/临期橙/正常绿）；无排程不计入分母。' },
  { label: 'due 代理', text: '后端无 sla_deadline_at，用 plannedAt（排程时间）作 due 代理。' },
  { label: 'mini bars', text: '近6月闭环数趋势（取 statistics.monthly）；无数据降级静态占位。' },
];

interface SlaSeg {
  color: string;
  count: number;
  key: 'near' | 'normal' | 'overdue';
  label: string;
  path: string;
  pct: number;
}

const SLA_META: { color: string; key: 'near' | 'normal' | 'overdue'; label: string }[] = [
  { color: '#FF4D4F', key: 'overdue', label: '超期' },
  { color: '#FA8C16', key: 'near', label: '临期' },
  { color: '#52C41A', key: 'normal', label: '正常' },
];

// 极坐标转笛卡尔（0°=12 点，顺时针）
function polarToCartesian(cx: number, cy: number, r: number, angleDeg: number) {
  const rad = (angleDeg * Math.PI) / 180;
  return { x: cx + r * Math.sin(rad), y: cy - r * Math.cos(rad) };
}
// 环形扇区 path（外弧顺时针 + 内弧逆时针）
function ringPath(
  cx: number,
  cy: number,
  rOut: number,
  rIn: number,
  startDeg: number,
  endDeg: number,
) {
  if (endDeg - startDeg >= 359.99) {
    return `M ${cx - rOut} ${cy} A ${rOut} ${rOut} 0 1 1 ${cx + rOut} ${cy} A ${rOut} ${rOut} 0 1 1 ${cx - rOut} ${cy} Z M ${cx - rIn} ${cy} A ${rIn} ${rIn} 0 1 0 ${cx + rIn} ${cy} A ${rIn} ${rIn} 0 1 0 ${cx - rIn} ${cy} Z`;
  }
  const outerStart = polarToCartesian(cx, cy, rOut, startDeg);
  const outerEnd = polarToCartesian(cx, cy, rOut, endDeg);
  const innerStart = polarToCartesian(cx, cy, rIn, startDeg);
  const innerEnd = polarToCartesian(cx, cy, rIn, endDeg);
  const largeArc = endDeg - startDeg > 180 ? 1 : 0;
  return `M ${outerStart.x.toFixed(2)} ${outerStart.y.toFixed(2)} A ${rOut} ${rOut} 0 ${largeArc} 1 ${outerEnd.x.toFixed(2)} ${outerEnd.y.toFixed(2)} L ${innerEnd.x.toFixed(2)} ${innerEnd.y.toFixed(2)} A ${rIn} ${rIn} 0 ${largeArc} 0 ${innerStart.x.toFixed(2)} ${innerStart.y.toFixed(2)} Z`;
}

const ringTotal = computed(
  () => props.sla.normal + props.sla.near + props.sla.overdue,
);

const segments = computed<SlaSeg[]>(() => {
  const t = ringTotal.value > 0 ? ringTotal.value : 1;
  let acc = 0;
  return SLA_META.map((meta) => {
    const count = props.sla[meta.key];
    const startAngle = (acc / t) * 360;
    acc += count;
    const endAngle = (acc / t) * 360;
    const path =
      ringTotal.value > 0 && count > 0
        ? ringPath(50, 50, 42, 30, startAngle, endAngle)
        : '';
    const pct = count > 0 ? (count / t) * 100 : 0;
    return { color: meta.color, key: meta.key, label: meta.label, count, pct, path };
  });
});

const timelyRate = computed(() => {
  const t = ringTotal.value;
  if (t === 0) return null;
  return Math.round((props.sla.normal / t) * 100);
});

interface MonthBar {
  closed: number;
  h: number;
  label: string;
  month: string;
}

const maxClosed = computed(() => {
  let m = 0;
  for (const r of props.monthly) if (r.closed > m) m = r.closed;
  return m > 0 ? m : 1;
});

const monthBars = computed<MonthBar[]>(() =>
  props.monthly.slice(-6).map((r) => ({
    closed: r.closed,
    h: Math.round((r.closed / maxClosed.value) * 100),
    label: r.month.slice(-2),
    month: r.month,
  })),
);

const noneCount = computed(() => props.sla.none);

function fmtPct(n: null | number | undefined): string {
  return n === null || n === undefined ? '—' : `${Math.round(n * 10) / 10}%`;
}

function fmtHours(n: null | number | undefined): string {
  return n === null || n === undefined ? '—' : `${Math.round(n * 10) / 10}h`;
}
</script>

<template>
  <div
    class="flex h-full min-h-0 cursor-pointer flex-col hover:bg-gray-50"
    title="点击查看处置统计报告"
    @click="onCardClick"
  >
    <div
      class="flex h-[22px] flex-none items-center border-b border-[#E4E7ED] px-[7px] text-[10.5px] font-semibold text-[#1F4E79]"
    >
      <span
        class="mr-[5px] inline-block h-[11px] w-[3px] rounded-[2px] bg-[#52C41A]"
      ></span>
      SLA 汇总 · 及时率
      <span @click.stop>
        <HelpBubble
          :size="12"
          theme="blue"
          title="SLA 汇总说明"
          :items="helpItems"
          class="ml-1"
        />
      </span>
    </div>
    <div class="flex min-h-0 flex-1 flex-col items-center overflow-hidden p-[4px_6px]">
      <!-- 环形图 + 中心及时率 -->
      <div
        class="relative flex h-[84px] w-[84px] flex-none items-center justify-center"
      >
        <svg viewBox="0 0 100 100" class="h-full w-full">
          <path
            v-for="seg in segments"
            v-show="seg.count > 0"
            :key="seg.key"
            :d="seg.path"
            :fill="seg.color"
            stroke="#fff"
            stroke-width="0.5"
          />
          <circle
            v-if="ringTotal === 0"
            cx="50"
            cy="50"
            r="36"
            fill="none"
            stroke="#F0F2F5"
            stroke-width="12"
          />
        </svg>
        <div class="absolute flex flex-col items-center">
          <span class="text-[8px] text-[#8C8C8C]">及时率</span>
          <span
            class="text-[14px] font-bold leading-none tabular-nums"
            :style="{ color: timelyRate === null ? '#BFBFBF' : timelyRate >= 80 ? '#52C41A' : timelyRate >= 50 ? '#FA8C16' : '#FF4D4F' }"
          >{{ timelyRate === null ? '—' : `${timelyRate}%` }}</span>
        </div>
      </div>
      <!-- 图例 -->
      <div class="mt-[3px] flex w-full flex-none flex-col gap-[1px] text-[9.5px]">
        <div
          v-for="seg in segments"
          :key="seg.key"
          class="flex items-center gap-[4px]"
        >
          <span
            class="inline-block h-[7px] w-[7px] flex-none rounded-[1px]"
            :style="{ background: seg.color }"
          ></span>
          <span class="flex-none" :style="{ color: seg.color }">{{ seg.label }}</span>
          <span class="ml-auto font-bold tabular-nums">{{ seg.count }}</span>
          <span class="w-[30px] text-right text-[9px] tabular-nums text-[#8C8C8C]">
            {{ ringTotal > 0 ? `${seg.pct.toFixed(0)}%` : '—' }}
          </span>
        </div>
        <div class="flex items-center gap-[4px] border-t border-dashed border-[#E4E7ED] pt-[2px]">
          <span
            class="inline-block h-[7px] w-[7px] flex-none rounded-[1px] bg-[#8C8C8C]"
          ></span>
          <span class="flex-none text-[#8C8C8C]">无排程</span>
          <span class="ml-auto font-bold tabular-nums text-[#8C8C8C]">{{ noneCount }}</span>
          <span class="w-[30px]"></span>
        </div>
      </div>
      <!-- mini bars 近6月闭环（固定高，防纵向溢出） -->
      <div
        v-if="monthBars.length > 0"
        class="mt-[3px] flex h-[34px] w-full flex-none items-end gap-[2px] border-t border-[#F0F2F5] pt-[2px]"
      >
        <div
          v-for="b in monthBars"
          :key="b.month"
          class="flex h-full flex-1 flex-col items-center justify-end"
          :title="`${b.month}: ${b.closed} 闭环`"
        >
          <div
            class="w-full rounded-t-[1px]"
            :style="{ height: `${b.h}%`, background: '#52C41A', minHeight: '1.5px', maxHeight: '22px' }"
          ></div>
          <div class="text-[7.5px] leading-tight tabular-nums text-[#8C8C8C]">{{ b.label }}</div>
        </div>
      </div>
      <!-- 摘要行 -->
      <div
        class="flex w-full flex-none items-center justify-between border-t border-dashed border-[#E4E7ED] px-[2px] py-[1px] text-[9px] text-[#8C8C8C]"
      >
        <span>闭环率 <b class="text-[#52C41A]">{{ fmtPct(statistics?.closeRate) }}</b></span>
        <span>均时 <b class="text-[#1F4E79]">{{ fmtHours(statistics?.avgCycleHours) }}</b></span>
      </div>
    </div>
  </div>
</template>
