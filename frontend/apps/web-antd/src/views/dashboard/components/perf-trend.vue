<script lang="ts" setup>
/**
 * 装置总览 · 行4 D 列：绩效趋势（双 Y 轴柱线组合，手写 SVG 零动画）
 *
 * 搬运原 §5 全部功能：悬浮十字线 + 统一悬浮框、图例 toggle、告警线（定级阈值配置）、
 * 参评数柱。管理者版默认图例收敛：默认只显示综合评分主线 + 自控率，
 * 平稳率/快速率/准确率默认关闭（点击图例可叠加）。
 */
import type { TrendLines } from '../types';

import { computed, ref } from 'vue';

import { warningColor, warningThreshold } from '../use-grade';

const props = defineProps<{
  /** 当前范围名（全厂 / 装置 / 单元名，底部状态行） */
  scopeLabel: string;
  /** 趋势序列（全厂/节点双口径；null = 加载失败） */
  trend: null | TrendLines;
  /** 当前窗口小时数（≥120 → X 轴按 M/D 标注） */
  trendHours: number;
  /** 时间窗短标签（底部状态行） */
  twLabel: string;
}>();

/** 五线颜色（评分主线 + 四率辅线） */
const LINE_COLORS = {
  acc: '#1a7f4b',
  auto: '#0284c7',
  fast: '#7c3aed',
  score: '#1d4ed8',
  steady: '#2563eb',
} as const;

/** 率指标图例开关（管理者版默认仅自控率叠加，其余点击开启） */
const lineVisible = ref({ acc: false, auto: true, fast: false, steady: false });
/** 评分主线图例开关 */
const scoreVisible = ref(true);

// ================ 趋势双轴柱线图（左轴五线 + 右轴参评回路数柱） ================
/** 趋势图几何（viewBox 坐标）：SVG 生成与悬浮十字线映射共用，避免两处漂移 */
const trendGeo = computed(() => {
  const t = props.trend;
  if (!t || t.timestamps.length === 0) return null;
  const all = [...t.score, ...t.steady, ...t.fast, ...t.acc, ...t.auto].filter(
    (v): v is number => v !== null && v !== undefined,
  );
  if (all.length === 0) return null;
  /** D 卡片 60% 宽（≈1100px 内宽 / 图区 ≈320px 高，3.1:1），viewBox 比例匹配容器避免文字单向拉伸 */
  const W = 960;
  const H = 310;
  const L = 46;
  const R = 16;
  const T = 14;
  const B = 30;
  return {
    n: t.timestamps.length,
    W,
    H,
    L,
    R,
    T,
    B,
    iw: W - L - R,
    ih: H - T - B,
    yMin: Math.min(
      warningThreshold.value - 10,
      Math.floor((Math.min(...(all.length > 0 ? all : [100])) - 6) / 10) * 10,
    ),
    yMax: 100,
  };
});

const trendSvg = computed(() => {
  const t = props.trend;
  const geo = trendGeo.value;
  if (!t || !geo) return '';

  const { n, W, H, L, R, T, iw, ih, yMin, yMax } = geo;
  const x = (i: number) => L + (iw * i) / Math.max(1, n - 1);
  const y = (v: number) =>
    T + ih * (1 - (Math.max(yMin, Math.min(yMax, v)) - yMin) / (yMax - yMin));

  const path = (arr: (null | number)[]) => {
    let d = '';
    let started = false;
    for (let i = 0; i < n; i++) {
      const v = arr[i];
      if (v === null || v === undefined) {
        started = false;
        continue;
      }
      const px = x(i).toFixed(1);
      const py = y(v).toFixed(1);
      d += started ? ` L${px},${py}` : `M${px},${py}`;
      started = true;
    }
    return d;
  };

  // 网格 + 告警线（取自定级阈值配置，禁硬编码）
  const wt = warningThreshold.value;
  const wc = warningColor.value;
  let grid = '';
  for (let v = yMin; v <= yMax; v += 10) {
    grid += `<line x1="${L}" y1="${y(v).toFixed(1)}" x2="${W - R}" y2="${y(v).toFixed(1)}" stroke="#eef2f7"/>`;
    grid += `<text x="${L - 5}" y="${(y(v) + 3).toFixed(1)}" font-size="9" fill="#94a3b8" text-anchor="end">${v}</text>`;
  }
  // 告警线：单独画虚线（不依赖网格刻度）
  const wy = y(Math.max(yMin, Math.min(yMax, wt))).toFixed(1);
  grid += `<line x1="${L}" y1="${wy}" x2="${W - R}" y2="${wy}" stroke="${wc}" stroke-dasharray="5,4" stroke-width="1.2"/>`;
  grid += `<text x="${L + 4}" y="${(Number(wy) - 4).toFixed(1)}" font-size="9" fill="${wc}" text-anchor="start">告警线 ${wt}</text>`;

  // X 轴标签（≥120 小时 → M/D；其余 → HH:00）
  let xl = '';
  const step = Math.ceil(n / 8);
  const byDay = props.trendHours >= 120;
  for (let i = 0; i < n; i += step) {
    const ts = t.timestamps[i]!;
    const d = new Date(ts);
    const lab = byDay
      ? `${d.getMonth() + 1}/${d.getDate()}`
      : `${d.getHours().toString().padStart(2, '0')}:00`;
    const anchor = i === 0 ? 'start' : (i + step >= n ? 'end' : 'middle');
    xl += `<text x="${x(i).toFixed(1)}" y="${H - 8}" font-size="9" fill="#94a3b8" text-anchor="${anchor}">${lab}</text>`;
  }

  // 评分主系列数据点（稀疏）
  let dots = '';
  const interval = n <= 8 ? 1 : Math.ceil(n / 14);
  if (scoreVisible.value) {
    for (let i = 0; i < n; i++) {
      const v = t.score[i];
      if (v === null || v === undefined) continue;
      if (i % interval !== 0 && i !== n - 1) continue;
      dots += `<circle cx="${x(i).toFixed(1)}" cy="${y(v).toFixed(1)}" r="2.4" fill="${LINE_COLORS.score}"/>`;
    }
  }

  const aux = (arr: (null | number)[], color: string) =>
    `<path d="${path(arr)}" fill="none" stroke="${color}" stroke-width="1.2" stroke-dasharray="5,3" opacity=".85"/>`;

  return `<svg viewBox="0 0 ${W} ${H}" width="100%" height="100%" preserveAspectRatio="none" style="display:block">
    ${grid}
    ${lineVisible.value.steady ? aux(t.steady, LINE_COLORS.steady) : ''}
    ${lineVisible.value.fast ? aux(t.fast, LINE_COLORS.fast) : ''}
    ${lineVisible.value.acc ? aux(t.acc, LINE_COLORS.acc) : ''}
    ${lineVisible.value.auto ? aux(t.auto, LINE_COLORS.auto) : ''}
    ${scoreVisible.value ? `<path d="${path(t.score)}" fill="none" stroke="${LINE_COLORS.score}" stroke-width="2.2"/>` : ''}
    ${dots}${xl}
  </svg>`;
});

// ================ 悬浮十字线 + 统一悬浮框 ================
/** 悬停桶索引（null = 不显示）；viewBox X 坐标用于十字线/悬浮框定位 */
const trendHoverIdx = ref<null | number>(null);
const trendHoverX = ref(0);

function onTrendMove(e: MouseEvent) {
  const geo = trendGeo.value;
  const rect = (e.currentTarget as HTMLElement).getBoundingClientRect();
  if (!geo || rect.width <= 0) return;
  const vx = ((e.clientX - rect.left) / rect.width) * geo.W;
  if (vx < geo.L - 6 || vx > geo.W - geo.R + 6) {
    trendHoverIdx.value = null;
    return;
  }
  const i = Math.round(((vx - geo.L) / geo.iw) * (geo.n - 1));
  if (Number.isNaN(i) || i < 0 || i >= geo.n) {
    trendHoverIdx.value = null;
    return;
  }
  trendHoverIdx.value = i;
  trendHoverX.value = geo.L + (geo.iw * i) / Math.max(1, geo.n - 1);
}

function onTrendLeave() {
  trendHoverIdx.value = null;
}

/** 悬浮框内容：时间 + 当前可见序列在该桶的取值 */
const trendTip = computed(() => {
  const i = trendHoverIdx.value;
  const t = props.trend;
  if (i === null || !t) return null;
  const d = t.timestamps[i] ? new Date(t.timestamps[i]!) : null;
  const time = d
    ? (props.trendHours >= 120 ? `${d.getMonth() + 1}/${d.getDate()} ` : '') +
      `${d.getHours().toString().padStart(2, '0')}:00`
    : '—';
  const rows: { color: string; label: string; text: string }[] = [];
  const line = (label: string, v: null | number | undefined, color: string) =>
    rows.push({
      color,
      label,
      text: typeof v === 'number' ? `${v.toFixed(1)}%` : '—',
    });
  if (scoreVisible.value) line('综合评分', t.score[i], LINE_COLORS.score);
  if (lineVisible.value.steady) line('平稳率', t.steady[i], LINE_COLORS.steady);
  if (lineVisible.value.fast) line('快速率', t.fast[i], LINE_COLORS.fast);
  if (lineVisible.value.acc) line('准确率', t.acc[i], LINE_COLORS.acc);
  if (lineVisible.value.auto) line('自控率', t.auto[i], LINE_COLORS.auto);
  return { rows, time };
});
</script>

<template>
  <div
    class="flex h-full min-w-0 flex-col rounded border border-gray-200 bg-white"
  >
    <div
      class="flex h-9 flex-none items-center gap-2 border-b border-gray-100 px-2.5"
    >
      <span class="text-[12px] font-bold text-gray-700">绩效趋势</span>
      <!-- 图例（五项全部可点击 toggle；默认仅综合评分+自控率开启） -->
      <div
        class="ml-auto flex items-center gap-1.5 text-[10px]"
        data-testid="trend-legend"
      >
        <button
          class="flex cursor-pointer items-center gap-1 border-0 bg-white"
          :class="scoreVisible ? 'text-gray-600' : 'text-gray-300'"
          @click="scoreVisible = !scoreVisible"
        >
          <span
            class="inline-block h-0.5 w-2.5 rounded"
            :style="{
              background: scoreVisible ? LINE_COLORS.score : '#cbd5e1',
            }"
          ></span>
          综合评分
        </button>
        <button
          v-for="lg in [
            { key: 'steady', label: '平稳率' },
            { key: 'fast', label: '快速率' },
            { key: 'acc', label: '准确率' },
            { key: 'auto', label: '自控率' },
          ] as {
            key: 'acc' | 'auto' | 'fast' | 'steady';
            label: string;
          }[]"
          :key="lg.key"
          class="flex cursor-pointer items-center gap-1 border-0 bg-white"
          :class="lineVisible[lg.key] ? 'text-gray-600' : 'text-gray-300'"
          @click="lineVisible[lg.key] = !lineVisible[lg.key]"
        >
          <span
            class="inline-block h-0.5 w-2.5 rounded"
            :style="{
              background: lineVisible[lg.key]
                ? LINE_COLORS[lg.key]
                : '#cbd5e1',
            }"
          ></span>
          {{ lg.label }}
        </button>
      </div>
    </div>
    <div class="min-h-0 flex-1 px-2 py-0.5">
      <div
        v-if="trendSvg"
        class="relative h-full w-full"
        data-testid="trend-chart"
        @mousemove="onTrendMove"
        @mouseleave="onTrendLeave"
      >
        <div v-html="trendSvg" class="h-full w-full"></div>
        <!-- 悬浮十字线 + 统一悬浮框 -->
        <template v-if="trendHoverIdx !== null && trendGeo && trendTip">
          <div
            class="pointer-events-none absolute z-10 w-px bg-slate-400/60"
            :style="{
              left: `${(trendHoverX / trendGeo.W) * 100}%`,
              top: `${(trendGeo.T / trendGeo.H) * 100}%`,
              height: `${(trendGeo.ih / trendGeo.H) * 100}%`,
            }"
          ></div>
          <div
            class="pointer-events-none absolute top-1.5 z-20 w-max min-w-28 rounded border border-gray-200 bg-white/95 px-2 py-1.5 text-[10px] shadow-lg"
            :style="
              trendHoverX > trendGeo.W * 0.62
                ? {
                    right: `${((trendGeo.W - trendHoverX + 8) / trendGeo.W) * 100}%`,
                  }
                : {
                    left: `${((trendHoverX + 8) / trendGeo.W) * 100}%`,
                  }
            "
          >
            <div
              class="mb-1 border-b border-gray-100 pb-0.5 font-mono text-[9px] text-gray-400"
            >
              {{ trendTip.time }}
            </div>
            <div
              v-for="row in trendTip.rows"
              :key="row.label"
              class="flex items-center gap-1.5 leading-4"
            >
              <span
                class="inline-block h-1.5 w-1.5 flex-none rounded-sm"
                :style="{ background: row.color }"
              ></span>
              <span class="text-gray-500">{{ row.label }}</span>
              <span class="ml-auto font-mono font-semibold text-gray-700">{{
                row.text
              }}</span>
            </div>
          </div>
        </template>
      </div>
      <div
        v-else
        class="flex h-full items-center justify-center text-xs text-gray-300"
      >
        暂无趋势数据
      </div>
    </div>
    <div
      class="flex h-6 flex-none items-center border-t border-gray-100 px-3 text-[11px] text-gray-400"
    >
      选中:
      <span class="font-bold text-gray-600">{{ scopeLabel }}</span> ·
      {{ twLabel }} · 告警线 {{ warningThreshold }}
    </div>
  </div>
</template>
