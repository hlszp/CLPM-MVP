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
 * previous 缺失时不渲染（不派生伪造）。
 *
 * P6-03（03§9 验收行 + §8 PL-03/04/05、A20）信息层次整改：
 * - Y 轴固定 0–100（评分定义域）：低分 10/40 与 90 同尺真实绘制——旧实现
 *   yMin=78/yMax=92 局部窗把 <78 的真实低分钳到基线（伪高分），已废除；
 * - X 轴按真实时间戳线性映射（后端空桶跳过不插值，build_trend_points）；
 *   缺口不连线伪连续：null 点断开 + 相邻点间距 >1.5×最小桶距判定为数据
 *   缺口断开；孤立单点画圆点（有数据但不可连线，不消失也不伪连线）；
 * - 上一周期按自身时间分位映射叠加（等长窗对齐），其内部缺口同样断开；
 * - 色码全部走 --ck-* 语义变量（驾驶舱深浅双主题；SVG 经 style 绑定消费）；
 * - 图表点击区键盘可达（role=button + tabindex + Enter/Space），头部显式
 *   数据截至时间（trend.snapshot_at）。
 * 工业约束：无动画。
 */
import type { WorkbenchApi } from '#/api/workbench';

import { computed, ref } from 'vue';

import { normalizeUtcTimestamp } from '#/utils/format';

import { useCockpitDrill } from './use-drill';

const props = defineProps<{
  trend?: null | WorkbenchApi.AssessmentTrend;
}>();

const { drill } = useCockpitDrill();

/** 追溯矩阵 §3 下钻：趋势图点击 → 评估历史明细（快照粒度，显式 latestOnly=false） */
function onChartClick() {
  void drill({ kind: 'trend' });
}

// 图表几何（对齐原型 lineChart w:430 h:236）
const W = 430;
const H = 236;
const PL = 34;
const PR = 16;
const PT = 14;
const PB = 20;
const INNER_W = W - PL - PR;
const INNER_H = H - PT - PB;

// Y 轴固定 0–100（评分定义域，P6-03：0–100 或明确动态范围 → 取固定 0–100）
const Y_MIN = 0;
const Y_MAX = 100;

// --ck-* 语义色（深浅双主题；SVG 经 style 绑定消费，presentation attribute
// 不便携带 var()，统一 :style）
const C = {
  line: 'var(--ck-accent-2)',
  prev: 'var(--ck-text-3)',
  target: 'var(--ck-grade-excellent)',
  grid: 'var(--ck-border)',
  tick: 'var(--ck-text-3)',
  good: 'var(--ck-grade-excellent)',
  bad: 'var(--ck-grade-poor)',
  zero: 'var(--ck-border-2)',
} as const;

const targetLine = computed(() => props.trend?.target ?? 90);

// 开关（对齐原型 .sw：目标线 / 上一周期）
const showTarget = ref(true);
const showPrev = ref(true);

/**
 * 后端时间归一（G-V1 返修）：series 点为 naive UTC；snapshot_at 来自 G5 库
 * timestamptz（API 实返 '+00:00' 后缀）——统一走项目标准
 * normalizeUtcTimestamp（tz-aware 原样 / naive 补 Z），非法输入 → NaN
 * （asOfText 显式不渲染，不崩溃）。
 */
function parseTs(s: string): number {
  return new Date(normalizeUtcTimestamp(s)).getTime();
}

interface TrendPt {
  t: number;
  v: number;
}

/**
 * 序列预处理：非法时间/非有限数值 → null（断点）。
 * ScoreTrendPoint.v 类型为 number，但 JSONB 存量可能带 null——null 绝不
 * 参与连线（旧实现 null 经 Math.min/max 钳到基线，属于伪造数据点）。
 */
function toPts(
  series: undefined | WorkbenchApi.ScoreTrendPoint[],
): (null | TrendPt)[] {
  return (series ?? []).map((p) => {
    const t = parseTs(p.t);
    const v = typeof p.v === 'number' && Number.isFinite(p.v) ? p.v : null;
    if (!Number.isFinite(t) || v === null) return null;
    return { t, v };
  });
}

/** 最小正时间差（桶距估计；<2 个有效点时返回 Infinity=不做缺口判定） */
function bucketEstimate(pts: (null | TrendPt)[]): number {
  let min = Number.POSITIVE_INFINITY;
  let prev: null | TrendPt = null;
  for (const p of pts) {
    if (!p) continue;
    if (prev) {
      const d = p.t - prev.t;
      if (d > 0 && d < min) min = d;
    }
    prev = p;
  }
  return min;
}

interface Segments {
  /** ≥2 点的连线段（每段一串 SVG 坐标） */
  lines: { x: number; y: number }[][];
  /** 孤立单点（两侧皆缺口/单点序列）：画圆点，不消失也不伪连线 */
  dots: { x: number; y: number }[];
}

/**
 * 分段构建：null 断开；相邻有效点间距 >1.5×最小桶距 = 数据缺口断开
 * （缺口不连线伪连续）。已知限制：全序列仅 2 点时无法估计桶距，按实
 * 连线（两点间无任何中间样本声称）。
 */
function buildSegments(
  pts: (null | TrendPt)[],
  xFor: (t: number) => number,
  yFor: (v: number) => number,
): Segments {
  const bucket = bucketEstimate(pts);
  const lines: { x: number; y: number }[][] = [];
  const dots: { x: number; y: number }[] = [];
  let cur: { x: number; y: number }[] = [];
  let prevPt: null | TrendPt = null;

  function flush() {
    if (cur.length >= 2) lines.push(cur);
    else if (cur.length === 1) dots.push(cur[0]!);
    cur = [];
  }

  for (const p of pts) {
    if (!p) {
      flush();
      prevPt = null;
      continue;
    }
    if (prevPt && p.t - prevPt.t > bucket * 1.5) flush(); // 时间缺口断开
    cur.push({ x: xFor(p.t), y: yFor(p.v) });
    prevPt = p;
  }
  flush();
  return { dots, lines };
}

function sy(v: number): number {
  const clamped = Math.max(Y_MIN, Math.min(Y_MAX, v));
  return PT + (1 - (clamped - Y_MIN) / (Y_MAX - Y_MIN)) * INNER_H;
}

const currentPts = computed(() => toPts(props.trend?.series?.current));

/** 当前窗口时间范围（真实时间轴：x = 时间线性映射，不按索引均布） */
const currentSpan = computed(() => {
  const ts = currentPts.value.filter((p): p is TrendPt => p !== null);
  if (ts.length === 0) return null;
  const t0 = ts[0]!.t;
  const t1 = ts[ts.length - 1]!.t;
  return { t0, t1 };
});

function xForCurrent(t: number): number {
  const span = currentSpan.value;
  if (!span || span.t1 <= span.t0) return PL + INNER_W / 2;
  return PL + ((t - span.t0) / (span.t1 - span.t0)) * INNER_W;
}

const currentSegs = computed(() => buildSegments(currentPts.value, xForCurrent, sy));

// 上一周期：**仅使用后端提供的真实序列**；缺失则不渲染。
//
// 2026-09-13 整改 G40：此前 prev 长度不匹配时会前端派生，即用主序列伪造
// “上一周期”曲线。与 ScoreTrendChart 同属一类问题——后端严守“不生成模拟
// 数据”，前端不应把数造回来。
// P6-03：prev 按自身时间分位映射叠加到当前窗宽（等长窗起止对齐），prev
// 内部缺口/null 同样断开；不再要求 prev 与 current 点数一致（索引对齐在
// 缺口存在时会错位）。
const prevSegs = computed(() => {
  const pts = toPts(props.trend?.series?.previous);
  const valid = pts.filter((p): p is TrendPt => p !== null);
  if (valid.length === 0) return { dots: [], lines: [] } as Segments;
  const p0 = valid[0]!.t;
  const p1 = valid[valid.length - 1]!.t;
  const xFor = (t: number) =>
    p1 > p0 ? PL + ((t - p0) / (p1 - p0)) * INNER_W : PL + INNER_W / 2;
  return buildSegments(pts, xFor, sy);
});

function pathFrom(pts: { x: number; y: number }[]): string {
  if (pts.length === 0) return '';
  return pts.map((p, i) => `${i ? 'L' : 'M'}${p.x.toFixed(2)} ${p.y.toFixed(2)}`).join(' ');
}

function areaFrom(pts: { x: number; y: number }[]): string {
  if (pts.length < 2) return '';
  const line = pathFrom(pts);
  const last = pts[pts.length - 1]!;
  const first = pts[0]!;
  return `${line} L${last.x.toFixed(1)} ${sy(Y_MIN)} L${first.x.toFixed(1)} ${sy(Y_MIN)} Z`;
}

const yTicks = computed(() => {
  const ticks: { v: number; y: number }[] = [];
  for (let v = Y_MIN; v <= Y_MAX; v += 20) ticks.push({ v, y: sy(v) });
  return ticks;
});

// X 轴标签（最多 8 个；位置=真实时间映射，非索引均布）
const xLabels = computed(() => {
  const ts = currentPts.value.filter((p): p is TrendPt => p !== null);
  const n = ts.length;
  if (!n) return [] as { label: string; x: number }[];
  const step = Math.ceil(n / 8);
  const labels: { label: string; x: number }[] = [];
  // 0930：按窗口跨度自适应（同 ScoreTrendChart）
  const spanH = n > 1 ? (ts[n - 1]!.t - ts[0]!.t) / 3_600_000 : 0;
  const byDate = spanH > 36;
  for (let i = 0; i < n; i += step) {
    const d = new Date(ts[i]!.t);
    const label = byDate
      ? `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
      : `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
    labels.push({ x: xForCurrent(ts[i]!.t), label });
  }
  return labels;
});

/** 末点取最后一个非 null 值（尾部缺口不冒充最新） */
const currentVal = computed(() => {
  const ts = currentPts.value.filter((p): p is TrendPt => p !== null);
  return ts.length > 0 ? ts[ts.length - 1]!.v.toFixed(2) : '—';
});

/** 数据截至时间（trend.snapshot_at，naive UTC → 本地 MM-DD HH:mm） */
const asOfText = computed(() => {
  const s = props.trend?.snapshot_at;
  if (!s) return '';
  const ms = parseTs(s);
  if (!Number.isFinite(ms)) return '';
  const d = new Date(ms);
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  const hh = String(d.getHours()).padStart(2, '0');
  const mi = String(d.getMinutes()).padStart(2, '0');
  return `截至 ${mm}-${dd} ${hh}:${mi}`;
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
  <div class="flex h-full flex-col rounded border ckwb-border ckwb-panel">
    <!-- 头部 -->
    <div class="flex flex-none items-center gap-2 border-b ckwb-border px-3 py-1.5">
      <span class="inline-block h-3.5 w-1 rounded-sm bg-[var(--ck-accent)]"></span>
      <span class="text-xs font-medium ckwb-text">综合评分 + 分项趋势</span>
      <span class="text-[10px] ckwb-text-3">综合评分走势 · 分项近 24h 变化量</span>
      <span v-if="asOfText" class="text-[10px] ckwb-text-3" data-testid="trend-asof">
        · {{ asOfText }}
      </span>
      <div class="ml-auto flex items-center gap-1">
        <button
          type="button"
          class="rounded border px-1.5 py-0.5 text-[10px] transition-none"
          :class="
            showTarget
              ? 'border-[var(--ck-accent)] bg-[var(--ck-accent)] text-white'
              : 'border-gray-300 ckwb-text-2'
          "
          @click="showTarget = !showTarget"
        >目标线</button>
        <button
          type="button"
          class="rounded border px-1.5 py-0.5 text-[10px] transition-none"
          :class="
            showPrev
              ? 'border-[var(--ck-accent)] bg-[var(--ck-accent)] text-white'
              : 'border-gray-300 ckwb-text-2'
          "
          @click="showPrev = !showPrev"
        >上一周期</button>
      </div>
    </div>

    <!-- 主体：左 lineChart + 右 斜率条 -->
    <div class="flex min-h-0 flex-1">
      <!-- 左：折线图 + 图例 -->
      <div class="flex min-w-0 flex-col" style="flex: 4">
        <div
          class="min-h-0 flex-1 cursor-pointer px-1 pt-1 outline-none focus-visible:ring-1 focus-visible:ring-[var(--ck-accent)]"
          role="button"
          tabindex="0"
          aria-label="查看评估历史明细"
          title="点击查看评估历史明细"
          @click="onChartClick"
          @keydown.enter.prevent="onChartClick"
          @keydown.space.prevent="onChartClick"
        >
          <svg
            :viewBox="`0 0 ${W} ${H}`"
            class="h-full w-full"
            preserveAspectRatio="none"
          >
            <!-- 网格 + Y 轴（0–100 固定定义域） -->
            <template v-for="tick in yTicks" :key="`g-${tick.v}`">
              <line
                :x1="PL"
                :x2="W - PR"
                :y1="tick.y"
                :y2="tick.y"
                :style="{ stroke: C.grid }"
                stroke-width="1"
              />
              <text
                :x="PL - 5"
                :y="tick.y + 3.5"
                text-anchor="end"
                font-size="9.5"
                :style="{ fill: C.tick }"
              >{{ tick.v }}</text>
            </template>

            <!-- X 轴标签 -->
            <template v-for="(lb, i) in xLabels" :key="`xl-${i}`">
              <text
                :x="lb.x"
                :y="H - 5"
                text-anchor="middle"
                font-size="9.5"
                :style="{ fill: C.tick }"
              >{{ lb.label }}</text>
            </template>

            <!-- 目标线 -->
            <template v-if="showTarget">
              <line
                :x1="PL"
                :x2="W - PR"
                :y1="sy(targetLine)"
                :y2="sy(targetLine)"
                :style="{ stroke: C.target }"
                stroke-width="1.2"
                stroke-dasharray="5 4"
              />
              <text
                :x="W - PR + 2"
                :y="sy(targetLine) + 3"
                font-size="9.5"
                :style="{ fill: C.target }"
              >{{ targetLine }}</text>
            </template>

            <!-- 上一周期（灰虚线；分段渲染，缺口不连线） -->
            <path
              v-for="(seg, i) in prevSegs.lines"
              v-show="showPrev"
              :key="`prev-${i}`"
              :d="pathFrom(seg)"
              fill="none"
              :style="{ stroke: C.prev }"
              stroke-width="1.3"
              stroke-dasharray="4 4"
              stroke-linecap="round"
            />
            <circle
              v-for="(dot, i) in prevSegs.dots"
              v-show="showPrev"
              :key="`prev-dot-${i}`"
              :cx="dot.x"
              :cy="dot.y"
              r="1.6"
              :style="{ fill: C.prev }"
            />

            <!-- 全厂面积 + 主线（分段渲染：null/时间缺口断开，不伪连续） -->
            <path
              v-for="(seg, i) in currentSegs.lines"
              :key="`area-${i}`"
              :d="areaFrom(seg)"
              :style="{ fill: C.line }"
              fill-opacity="0.09"
              stroke="none"
            />
            <path
              v-for="(seg, i) in currentSegs.lines"
              :key="`cur-${i}`"
              :d="pathFrom(seg)"
              fill="none"
              :style="{ stroke: C.line }"
              stroke-width="2"
              stroke-linecap="round"
            />
            <circle
              v-for="(dot, i) in currentSegs.dots"
              :key="`cur-dot-${i}`"
              :cx="dot.x"
              :cy="dot.y"
              r="2"
              :style="{ fill: C.line }"
            />
          </svg>
        </div>
        <!-- 图例 -->
        <div class="flex flex-none flex-wrap items-center gap-x-3 gap-y-0.5 px-3 py-1 text-[10px] ckwb-text-2">
          <span class="flex items-center gap-1" title="趋势曲线末点 = 窗口内最新一个评分快照（与左侧摘要的窗口加权均值口径不同）；缺口断开处为无数据时段，不连线">
            <span class="inline-block h-2 w-2 rounded-full" :style="{ backgroundColor: C.line }"></span>
            全厂综合评分（最新快照 {{ currentVal }}）
          </span>
          <span class="flex items-center gap-1">
            <span class="inline-block h-2 w-2 rounded-full" :style="{ backgroundColor: C.prev }"></span>
            上一周期
          </span>
          <span class="flex items-center gap-1">
            <span class="inline-block h-0.5 w-3" :style="{ backgroundColor: C.target }"></span>
            目标 {{ targetLine }}
          </span>
        </div>
      </div>

      <!-- 右：分项斜率条 -->
      <div class="flex min-w-0 flex-col border-l border-dashed ckwb-border py-2" style="flex: 3">
        <div class="px-3 pb-1 text-[11px] ckwb-text-3">分项近 24h 变化量（pct）</div>
        <div class="flex flex-col gap-1.5 px-3">
          <div
            v-for="(s, i) in slopesSorted"
            :key="i"
            class="flex items-center gap-2 text-[11px]"
          >
            <span class="w-16 flex-none ckwb-text-2">{{ s.metric }}</span>
            <!-- 斜率条容器：零轴居中 50% -->
            <span class="relative flex h-3 flex-1 items-center">
              <!-- 零轴 -->
              <span class="absolute left-1/2 top-0 h-full w-px" :style="{ backgroundColor: C.zero }"></span>
              <!-- bad：从零轴向左延伸（右端在 50%） -->
              <span
                v-if="s.direction === 'bad'"
                class="absolute h-2 rounded-sm"
                :style="{
                  right: '50%',
                  width: `${slopeWidth(s.delta)}%`,
                  backgroundColor: C.bad,
                }"
              ></span>
              <!-- good：从零轴向右延伸（左端在 50%） -->
              <span
                v-else
                class="absolute h-2 rounded-sm"
                :style="{
                  left: '50%',
                  width: `${slopeWidth(s.delta)}%`,
                  backgroundColor: C.good,
                }"
              ></span>
            </span>
            <span
              class="w-12 flex-none text-right font-mono"
              :style="{ color: s.direction === 'bad' ? C.bad : C.good }"
            >{{ s.delta > 0 ? '+' : '' }}{{ s.delta.toFixed(2) }}pct</span>
          </div>
          <div v-if="slopesSorted.length === 0" class="py-4 text-center text-[11px] ckwb-text-3">
            暂无斜率数据
          </div>
        </div>
        <div class="px-3 pt-1.5 text-[10.5px] ckwb-text-3">
          恶化居上（红）· 改善居下（绿），零轴居中
        </div>
      </div>
    </div>
  </div>
</template>
