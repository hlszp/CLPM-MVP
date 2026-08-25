<script setup lang="ts">
/**
 * 性能评估 · 综合评分 + 分项趋势（原型对齐 1:1 · Row3 c7）
 *
 * 复刻原型 renderEval() Row3 左：
 * - 左 flex:4：lineChart（全厂蓝实线+面积 / 上一周期灰虚线 / 目标 90 绿虚线）
 * - 右 flex:3：分项近 24h 变化量 6 条（恶化居上红 · 改善居下绿 · 零轴居中）
 * - 头部 2 开关：目标线 / 上一周期（可切换显隐）
 * - 图例：全厂综合评分 / 上一周期 / 目标 90
 *
 * 数据：trend.series.{current,previous}（ScoreTrendPoint[]）+ trend.slopes（6 项）
 * previous 缺失时前端按原型公式派生（current[i]-1.2+噪声）。
 * 工业约束：无动画、色码 #1F4E79/#52C41A/#FF4D4F。
 */
import type { WorkbenchApi } from '#/api/workbench';

import { computed, ref } from 'vue';

const props = defineProps<{
  trend?: null | WorkbenchApi.AssessmentTrend;
}>();

// 图表几何（对齐原型 lineChart w:430 h:236）
const W = 430;
const H = 236;
const PL = 34;
const PR = 10;
const PT = 14;
const PB = 20;
const INNER_W = W - PL - PR;
const INNER_H = H - PT - PB;

const yMin = 78;
const yMax = 92;
const targetLine = computed(() => props.trend?.target ?? 90);

// 开关（对齐原型 .sw：目标线 / 上一周期）
const showTarget = ref(true);
const showPrev = ref(true);

function sx(i: number, n: number): number {
  return n > 1 ? PL + (i * INNER_W) / (n - 1) : PL;
}
function sy(v: number): number {
  const clamped = Math.max(yMin, Math.min(yMax, v));
  return PT + (1 - (clamped - yMin) / (yMax - yMin)) * INNER_H;
}

function toArr(series: undefined | WorkbenchApi.ScoreTrendPoint[]) {
  return series ?? [];
}

// 当前系列（全厂）
const currentPts = computed(() => {
  const t = toArr(props.trend?.series?.current);
  if (t.length === 0) return [] as { v: number; x: number; y: number; }[];
  const n = t.length;
  return t.map((p, i) => ({ x: sx(i, n), y: sy(p.v), v: p.v }));
});

// 上一周期：优先取后端 previous；缺失则前端派生（对齐原型 prev 公式）
const prevPts = computed(() => {
  const cur = toArr(props.trend?.series?.current);
  const prev = toArr(props.trend?.series?.previous);
  const n = cur.length;
  if (!n) return [] as { x: number; y: number }[];
  const useDerived = prev.length !== n;
  return cur.map((p, i) => {
    const v = useDerived
      ? +(p.v - 1.2 + (((i % 3) - 1) * 0.15)).toFixed(2)
      : (prev[i]?.v ?? p.v);
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

const currentPath = computed(() => pathFrom(currentPts.value));
const currentArea = computed(() => areaPath(currentPts.value));
const prevPath = computed(() => pathFrom(prevPts.value));

const yTicks = computed(() => {
  const ticks: { v: number; y: number }[] = [];
  for (let v = yMin; v <= yMax; v += 2) ticks.push({ v, y: sy(v) });
  return ticks;
});

// X 轴标签（最多 8 个，对齐原型 step=Math.ceil(X/8)）
const xLabels = computed(() => {
  const t = toArr(props.trend?.series?.current);
  const n = t.length;
  if (!n) return [] as { label: string; x: number }[];
  const step = Math.ceil(n / 8);
  const labels: { label: string; x: number }[] = [];
  for (let i = 0; i < n; i += step) {
    const label = t[i]!.t.slice(11, 16) || String(i); // HH:mm
    labels.push({ x: sx(i, n), label });
  }
  return labels;
});

const currentVal = computed(() => {
  const t = toArr(props.trend?.series?.current);
  return t.length > 0 ? t[t.length - 1]!.v.toFixed(1) : '—';
});

// 分项斜率：恶化（bad）居上红 · 改善（good）居下绿（对齐原型排序）
const slopesSorted = computed(() => {
  const s = props.trend?.slopes ?? [];
  return s.toSorted((a, b) => {
    const ra = a.direction === 'bad' ? 0 : 1;
    const rb = b.direction === 'bad' ? 0 : 1;
    return ra - rb;
  });
});

// 斜率条宽度（对齐原型 pct=min(|delta|/2*100,100) · width=pct*0.5%）
function slopeWidth(delta: number): number {
  return Math.min(Math.abs(delta) / 2, 1) * 50;
}
</script>

<template>
  <div class="flex h-full flex-col rounded border border-[#E4E7ED] bg-white">
    <!-- 头部 -->
    <div class="flex flex-none items-center gap-2 border-b border-[#E4E7ED] px-3 py-1.5">
      <span class="inline-block h-3.5 w-1 rounded-sm bg-[#1F4E79]"></span>
      <span class="text-xs font-medium text-gray-700">综合评分 + 分项趋势</span>
      <span class="text-[10px] text-gray-400">综合评分走势 · 分项近 24h 变化量</span>
      <div class="ml-auto flex items-center gap-1">
        <button
          class="rounded border px-1.5 py-0.5 text-[10px] transition-none"
          :class="
            showTarget
              ? 'border-[#1F4E79] bg-[#1F4E79] text-white'
              : 'border-gray-300 text-gray-500'
          "
          @click="showTarget = !showTarget"
        >目标线</button>
        <button
          class="rounded border px-1.5 py-0.5 text-[10px] transition-none"
          :class="
            showPrev
              ? 'border-[#1F4E79] bg-[#1F4E79] text-white'
              : 'border-gray-300 text-gray-500'
          "
          @click="showPrev = !showPrev"
        >上一周期</button>
      </div>
    </div>

    <!-- 主体：左 lineChart + 右 斜率条 -->
    <div class="flex min-h-0 flex-1">
      <!-- 左：折线图 + 图例 -->
      <div class="flex min-w-0 flex-col" style="flex: 4">
        <div class="min-h-0 flex-1 px-1 pt-1">
          <svg
            :viewBox="`0 0 ${W} ${H}`"
            class="h-full w-full"
            preserveAspectRatio="none"
          >
            <!-- 网格 + Y 轴 -->
            <template v-for="tick in yTicks" :key="`g-${tick.v}`">
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
                stroke="#52C41A"
                stroke-width="1.2"
                stroke-dasharray="5 4"
              />
              <text
                :x="W - PR + 2"
                :y="sy(targetLine) + 3"
                font-size="9.5"
                fill="#52C41A"
              >{{ targetLine }}</text>
            </template>

            <!-- 上一周期（灰虚线） -->
            <path
              v-if="showPrev && prevPath"
              :d="prevPath"
              fill="none"
              stroke="#B9C6D6"
              stroke-width="1.3"
              stroke-dasharray="4 4"
              stroke-linecap="round"
            />

            <!-- 全厂面积 + 主线 -->
            <path v-if="currentArea" :d="currentArea" fill="#2563EB" opacity="0.09" />
            <path
              v-if="currentPath"
              :d="currentPath"
              fill="none"
              stroke="#2563EB"
              stroke-width="2"
              stroke-linecap="round"
            />
          </svg>
        </div>
        <!-- 图例 -->
        <div class="flex flex-none flex-wrap items-center gap-x-3 gap-y-0.5 px-3 py-1 text-[10px] text-gray-500">
          <span class="flex items-center gap-1">
            <span class="inline-block h-2 w-2 rounded-full bg-[#2563EB]"></span>
            全厂综合评分（当前 {{ currentVal }}）
          </span>
          <span class="flex items-center gap-1">
            <span class="inline-block h-2 w-2 rounded-full bg-[#B9C6D6]"></span>
            上一周期
          </span>
          <span class="flex items-center gap-1">
            <span class="inline-block h-0.5 w-3" style="background-color: #52C41A"></span>
            目标 {{ targetLine }}
          </span>
        </div>
      </div>

      <!-- 右：分项斜率条 -->
      <div class="flex min-w-0 flex-col border-l border-dashed border-[#E4E7ED] py-2" style="flex: 3">
        <div class="px-3 pb-1 text-[11px] text-gray-400">分项近 24h 变化量（pct）</div>
        <div class="flex flex-col gap-1.5 px-3">
          <div
            v-for="(s, i) in slopesSorted"
            :key="i"
            class="flex items-center gap-2 text-[11px]"
          >
            <span class="w-16 flex-none text-gray-600">{{ s.metric }}</span>
            <!-- 斜率条容器：零轴居中 50% -->
            <span class="relative flex h-3 flex-1 items-center">
              <!-- 零轴 -->
              <span class="absolute left-1/2 top-0 h-full w-px bg-gray-300"></span>
              <!-- bad：从零轴向左延伸（右端在 50%） -->
              <span
                v-if="s.direction === 'bad'"
                class="absolute h-2 rounded-sm"
                :style="{
                  right: '50%',
                  width: `${slopeWidth(s.delta)}%`,
                  backgroundColor: '#FF4D4F',
                }"
              ></span>
              <!-- good：从零轴向右延伸（左端在 50%） -->
              <span
                v-else
                class="absolute h-2 rounded-sm"
                :style="{
                  left: '50%',
                  width: `${slopeWidth(s.delta)}%`,
                  backgroundColor: '#52C41A',
                }"
              ></span>
            </span>
            <span
              class="w-12 flex-none text-right font-mono"
              :style="{ color: s.direction === 'bad' ? '#FF4D4F' : '#52C41A' }"
            >{{ s.delta > 0 ? '+' : '' }}{{ s.delta }}pct</span>
          </div>
          <div v-if="slopesSorted.length === 0" class="py-4 text-center text-[11px] text-gray-300">
            暂无斜率数据
          </div>
        </div>
        <div class="px-3 pt-1.5 text-[10.5px] text-gray-400">
          恶化居上（红）· 改善居下（绿），零轴居中
        </div>
      </div>
    </div>
  </div>
</template>
