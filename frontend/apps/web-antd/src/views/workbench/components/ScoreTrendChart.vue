<script setup lang="ts">
/**
 * 系统总览 · 综合评分趋势（原型对齐 1:1）
 *
 * 复刻原型 lineChart()：
 * - 3 条折线：全厂(蓝实线+面积) / 催化裂化(橙) / 上一周期(灰虚线)
 * - 目标线：90 分绿色虚线 + 右侧标注
 * - 事件标注 flags：虚线竖线 + 旗帜标签
 * - 3 个开关：目标线 / 上一周期 / 事件标注（可切换显隐）
 * - 图例：全厂（当前值）/ 催化裂化 / 上一周期 / 目标 90
 *
 * 数据派生（对齐原型 line 650-652）：
 * - prev = trend[i] - 1.2 + 噪声
 * - cat   = trend[i] - 2.1 - 衰减
 */
import type { WorkbenchApi } from '#/api/workbench';

import { computed, ref } from 'vue';

import { useWorkbenchDrill } from '../utils/drill';

const props = defineProps<{
  flags?: WorkbenchApi.WindowFlag[];
  target?: number; // 默认 90
  trend?: WorkbenchApi.ScoreTrendPoint[];
}>();

const { drill } = useWorkbenchDrill();

/** 追溯矩阵 §2 下钻：图表点击 → 评估历史明细（快照粒度，显式 latestOnly=false） */
function onChartClick() {
  drill('assess', '/metric/history', { latestOnly: 'false' });
}

// 图表几何（对齐原型 w:560 h:225）
const W = 560;
const H = 225;
const PL = 34;
const PR = 10;
const PT = 14;
const PB = 20;
const INNER_W = W - PL - PR;
const INNER_H = H - PT - PB;

const yMin = 78;
const yMax = 92;
const targetLine = computed(() => props.target ?? 90);

// 开关
const showTarget = ref(true);
const showPrev = ref(true);
const showFlags = ref(true);

function sx(i: number, n: number): number {
  return n > 1 ? PL + (i * INNER_W) / (n - 1) : PL;
}
function sy(v: number): number {
  const clamped = Math.max(yMin, Math.min(yMax, v));
  return PT + (1 - (clamped - yMin) / (yMax - yMin)) * INNER_H;
}

// 主系列（全厂）
const mainPoints = computed(() => {
  const t = props.trend ?? [];
  if (t.length === 0) return [] as { v: number; x: number; y: number; }[];
  const n = t.length;
  return t.map((p, i) => ({ x: sx(i, n), y: sy(p.v), v: p.v }));
});

// 上一周期（派生）
const prevPoints = computed(() => {
  const t = props.trend ?? [];
  if (t.length === 0) return [] as { x: number; y: number }[];
  const n = t.length;
  return t.map((p, i) => {
    const v = +(p.v - 1.2 + (((i % 3) - 1) * 0.15)).toFixed(2);
    return { x: sx(i, n), y: sy(v) };
  });
});

// 催化裂化（派生）
const catPoints = computed(() => {
  const t = props.trend ?? [];
  if (t.length === 0) return [] as { x: number; y: number }[];
  const n = t.length;
  return t.map((p, i) => {
    const v = +(p.v - 2.1 - (n - 1 - i) * 0.045).toFixed(2);
    return { x: sx(i, n), y: sy(v) };
  });
});

function pathFrom(pts: { x: number; y: number }[]): string {
  if (pts.length === 0) return '';
  return pts.map((p, i) => `${i ? 'L' : 'M'}${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(' ');
}

function areaPath(pts: { x: number; y: number }[]): string {
  if (pts.length < 2) return '';
  const line = pathFrom(pts);
  const last = pts[pts.length - 1]!;
  const first = pts[0]!;
  return `${line} L${last.x.toFixed(1)} ${sy(yMin)} L${first.x.toFixed(1)} ${sy(yMin)} Z`;
}

const mainPath = computed(() => pathFrom(mainPoints.value));
const mainArea = computed(() => areaPath(mainPoints.value));
const prevPath = computed(() => pathFrom(prevPoints.value));
const catPath = computed(() => pathFrom(catPoints.value));

// 事件标注色阶
const FLAG_COLORS: Record<string, string> = {
  CRITICAL: '#D93025',
  ERROR: '#E8710A',
  WARN: '#F59E0B',
};
const yTicks = computed(() => {
  const ticks: { v: number; y: number }[] = [];
  for (let v = yMin; v <= yMax; v += 2) {
    ticks.push({ v, y: sy(v) });
  }
  return ticks;
});

// X 轴标签（最多 8 个）
const xLabels = computed(() => {
  const t = props.trend ?? [];
  const n = t.length;
  if (!n) return [] as { label: string; x: number; }[];
  const step = Math.ceil(n / 8);
  const labels: { label: string; x: number; }[] = [];
  for (let i = 0; i < n; i += step) {
    const label = t[i]!.t.slice(11, 16) || String(i); // HH:mm
    labels.push({ x: sx(i, n), label });
  }
  return labels;
});

// 事件标注 flags：按时间戳映射到 x 索引
const flagPoints = computed(() => {
  const t = props.trend ?? [];
  const flags = props.flags ?? [];
  if (t.length === 0 || flags.length === 0) return [] as { color: string; label: string; x: number; }[];
  const n = t.length;
  return flags.map((f) => {
    // 按时间戳匹配最近 trend 点；无匹配则均匀分布
    let idx = t.findIndex((p) => p.t === f.t);
    if (idx < 0) idx = Math.floor(n / 2);
    const color = FLAG_COLORS[f.severity] ?? '#2563EB';
    return { x: sx(idx, n), label: `${f.t.slice(11, 16)} ${f.desc ?? f.kind}`, color };
  });
});

const mainCurrent = computed(() => {
  const t = props.trend ?? [];
  return t.length > 0 ? t[t.length - 1]!.v.toFixed(1) : '—';
});
</script>

<template>
  <div class="flex h-full flex-col rounded border border-[#E4E7ED] bg-white">
    <!-- 头部 -->
    <div class="flex items-center justify-between border-b border-[#E4E7ED] px-3 py-2">
      <span class="flex items-center gap-1.5 text-xs font-medium text-gray-700">
        <span class="inline-block h-3 w-1 rounded-sm bg-[#1F4E79]"></span>
        综合评分趋势
      </span>
      <div class="flex items-center gap-1">
        <button
          v-for="(sw, i) in [
            { key: 'target', label: '目标线' },
            { key: 'prev', label: '上一周期' },
            { key: 'flags', label: '事件标注' },
          ]"
          :key="i"
          class="rounded border px-1.5 py-0.5 text-[10px] transition-none"
          :class="
            (sw.key === 'target' ? showTarget : sw.key === 'prev' ? showPrev : showFlags)
              ? 'border-[#1F4E79] bg-[#1F4E79] text-white'
              : 'border-gray-300 text-gray-500'
          "
          @click="
            sw.key === 'target'
              ? (showTarget = !showTarget)
              : sw.key === 'prev'
                ? (showPrev = !showPrev)
                : (showFlags = !showFlags)
          "
        >{{ sw.label }}</button>
      </div>
    </div>

    <!-- 图表 -->
    <div
      class="flex-1 cursor-pointer overflow-hidden p-1"
      title="点击查看评估历史明细"
      @click="onChartClick"
    >
      <svg :viewBox="`0 0 ${W} ${H}`" class="h-full w-full" preserveAspectRatio="none">
        <!-- 网格线 + Y 轴标签 -->
        <template v-for="tick in yTicks" :key="`grid-${tick.v}`">
          <line
            :x1="PL"
            :x2="W - PR"
            :y1="tick.y"
            :y2="tick.y"
            stroke="#F0F0F0"
            stroke-width="1"
          />
          <text
            :x="PL - 5"
            :y="tick.y + 3.5"
            text-anchor="end"
            font-size="9.5"
            fill="#8A94A6"
          >{{ tick.v }}</text>
        </template>

        <!-- X 轴标签 -->
        <template v-for="(lb, i) in xLabels" :key="`xl-${i}`">
          <text
            :x="lb.x"
            :y="H - 5"
            text-anchor="middle"
            font-size="9.5"
            fill="#8A94A6"
          >{{ lb.label }}</text>
        </template>

        <!-- 目标线 -->
        <template v-if="showTarget">
          <line
            :x1="PL"
            :x2="W - PR"
            :y1="sy(targetLine)"
            :y2="sy(targetLine)"
            stroke="#10b981"
            stroke-width="1.2"
            stroke-dasharray="5 4"
          />
          <text
            :x="W - PR + 2"
            :y="sy(targetLine) + 3"
            font-size="9.5"
            fill="#10b981"
          >{{ targetLine }}</text>
        </template>

        <!-- 上一周期（灰虚线） -->
        <path
          v-if="showPrev && prevPath"
          :d="prevPath"
          fill="none"
          stroke="#B9C6D6"
          stroke-width="1.4"
          stroke-dasharray="4 4"
          stroke-linecap="round"
        />

        <!-- 催化裂化（橙） -->
        <path
          v-if="catPath"
          :d="catPath"
          fill="none"
          stroke="#E8710A"
          stroke-width="1.4"
          stroke-linecap="round"
        />

        <!-- 全厂面积 + 主线 -->
        <path v-if="mainArea" :d="mainArea" fill="#2563EB" opacity="0.09" />
        <path
          v-if="mainPath"
          :d="mainPath"
          fill="none"
          stroke="#2563EB"
          stroke-width="2"
          stroke-linecap="round"
        />

        <!-- 事件标注 flags -->
        <template v-if="showFlags">
          <g v-for="(f, i) in flagPoints" :key="`flag-${i}`">
            <line
              :x1="f.x"
              :x2="f.x"
              :y1="PT"
              :y2="H - PB"
              :stroke="f.color"
              stroke-width="1"
              stroke-dasharray="3 3"
            />
            <path
              :d="`M${f.x} ${PT} h11 a2 2 0 0 1 2 2 v7 a2 2 0 0 1-2 2 h-11 Z`"
              :fill="f.color"
            />
            <circle :cx="f.x + 6.5" :cy="PT + 6" r="2" fill="#fff" />
          </g>
        </template>
      </svg>
    </div>

    <!-- 图例 -->
    <div class="flex flex-none flex-wrap items-center gap-x-3 gap-y-0.5 border-t border-[#E4E7ED] px-3 py-1 text-[10px] text-gray-500">
      <span class="flex items-center gap-1">
        <span class="inline-block h-2 w-2 rounded-full bg-[#2563EB]"></span>
        全厂（当前 {{ mainCurrent }}）
      </span>
      <span class="flex items-center gap-1">
        <span class="inline-block h-2 w-2 rounded-full bg-[#E8710A]"></span>
        催化裂化（82.1）
      </span>
      <span class="flex items-center gap-1">
        <span class="inline-block h-2 w-2 rounded-full bg-[#B9C6D6]"></span>
        上一周期
      </span>
      <span class="flex items-center gap-1">
        <span class="inline-block h-0.5 w-3" style="background-color: #10b981"></span>
        目标 {{ targetLine }}
      </span>
    </div>
  </div>
</template>
