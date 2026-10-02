<!--
  趋势图组件（workbench360 P1）

  对齐原型 #cv-body 结构：事件标注层(固定高) + canvas + 底部 X 滚动条 + 右侧 Y 滚动条。
  - 绘制：等距网格重采样 3600 点（resample.ts），密集窗口自动切逐像素 min/max 包络带；
  - 系列：PV/SP/OP(右轴 20-80%)、MANUAL 背景带、质量码段（BAD 灰虚线 / UNCERTAIN 琥珀点划）；
  - 交互：滚动条拖拽平移 + 两端(14%/86%)拉伸缩放、滚轮缩放(1.15, 光标中心)、
    Shift+滚轮缩 Y、双击复位；X 最小跨度 60s、Y 最小 0.5；
  - mini 模式（最大化态迷你趋势）：仅标注层(22px)+canvas，无滚动条/滚轮。
  颜色全部来自 constants/clpm-ui.ts（hex 棘轮白名单目录），无内联 hex。
-->
<script setup lang="ts">
import type { SeriesVisible, TrendEventMark, TrendFrame } from './types';

import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';

import { useClpmTheme } from '#/composables/use-clpm-theme';
import { resolveModeLabel } from '#/composables/use-loop-realtime';
import {
  WB360_ENVELOPE_FILL,
  WB360_EVENT_MARK_COLORS,
  WB360_MANUAL_BAND_FILL,
  WB360_SAMPLE_POINTS,
  WB360_TREND_PALETTE_DARK,
  WB360_TREND_PALETTE_LIGHT,
} from '#/constants/clpm-ui';

import {
  buildEnvelope,
  isDense,
  niceStep,
  resampleFrames,
  segmentsOf,
} from './resample';

const props = withDefaults(
  defineProps<{
    /** 数据域（返回序列实际覆盖范围；null=无数据） */
    domain: null | { t0: number; t1: number };
    /** 事件徽标（诊断/整定/验证；P1 传空数组，P2-P4 接数据） */
    events?: TrendEventMark[];
    /** 源序列（ts 升序毫秒） */
    frames: TrendFrame[];
    /** 实时模式（末刻度"现在"、视口跟随数据域右推） */
    live: boolean;
    /** 迷你模式（最大化态） */
    mini?: boolean;
    /** MODE 数值映射（回路 modeMapping） */
    modeMapping?: null | Record<string, string>;
    seriesVisible: SeriesVisible;
    /** Y 数据域（PV/SP min/max + 余量） */
    yDomain: { hi: number; lo: number };
  }>(),
  { events: () => [], mini: false, modeMapping: null },
);

const emit = defineEmits<{
  (e: 'eventClick', mark: TrendEventMark): void;
}>();

const DAY_MS = 86_400_000;
/** X 最小视口跨度 / Y 最小视口高度（v3 §5.3） */
const X_MIN_SPAN = 60_000;
const Y_MIN_SPAN = 0.5;
const ZOOM_FACTOR = 1.15;

const { isDark } = useClpmTheme();

const canvasHostRef = ref<HTMLDivElement | null>(null);
const canvasRef = ref<HTMLCanvasElement | null>(null);

const viewX = ref({ t0: 0, t1: 1 });
const viewY = ref({ hi: 1, lo: 0 });

const NP = WB360_SAMPLE_POINTS;
/** 绘图区边距（对齐原型 drawChart：L46/R54/T10/B30） */
const M = { b: 30, l: 46, r: 54, t: 10 };

const isManual = (m: null | number) =>
  resolveModeLabel(m, props.modeMapping) === 'Manual';

const palette = computed(() =>
  isDark.value ? WB360_TREND_PALETTE_DARK : WB360_TREND_PALETTE_LIGHT,
);
const envelopeFill = computed(() =>
  isDark.value
    ? { op: WB360_ENVELOPE_FILL.opDark, pv: WB360_ENVELOPE_FILL.pvDark }
    : { op: WB360_ENVELOPE_FILL.opLight, pv: WB360_ENVELOPE_FILL.pvLight },
);
const manualFill = computed(() =>
  isDark.value ? WB360_MANUAL_BAND_FILL.dark : WB360_MANUAL_BAND_FILL.light,
);

/* ── rAF 节流绘制 ── */
let rafPending = false;
let resizeObserver: null | ResizeObserver = null;

function requestDraw() {
  if (rafPending) return;
  rafPending = true;
  requestAnimationFrame(() => {
    rafPending = false;
    draw();
  });
}

/* ── 视口钳制（对齐原型 clampViewX/clampViewY） ── */
function clampViewX(t0: number, t1: number) {
  const d = props.domain;
  if (!d || d.t1 <= d.t0) return;
  const s = Math.min(Math.max(t1 - t0, X_MIN_SPAN), d.t1 - d.t0);
  let a = t0;
  let b = a + s;
  if (b > d.t1) {
    b = d.t1;
    a = b - s;
  }
  if (a < d.t0) {
    a = d.t0;
    b = a + s;
  }
  viewX.value = { t0: a, t1: b };
  requestDraw();
}

function clampViewY(lo: number, hi: number) {
  const d = props.yDomain;
  const s = Math.min(Math.max(hi - lo, Y_MIN_SPAN), d.hi - d.lo);
  let l = lo;
  let h = l + s;
  if (h > d.hi) {
    h = d.hi;
    l = h - s;
  }
  if (l < d.lo) {
    l = d.lo;
    h = l + s;
  }
  viewY.value = { hi: h, lo: l };
  requestDraw();
}

function zoomX(f: number, center?: null | number) {
  const { t0, t1 } = viewX.value;
  const d = props.domain;
  if (!d) return;
  const s = Math.min(Math.max((t1 - t0) * f, X_MIN_SPAN), d.t1 - d.t0);
  const c = center ?? (t0 + t1) / 2;
  const k = s / (t1 - t0);
  clampViewX(c - (c - t0) * k, c + (t1 - c) * k);
}

function zoomY(f: number, c?: null | number) {
  const { lo, hi } = viewY.value;
  const s = Math.min(
    Math.max((hi - lo) * f, Y_MIN_SPAN),
    props.yDomain.hi - props.yDomain.lo,
  );
  const cc = c ?? (lo + hi) / 2;
  const k = s / (hi - lo);
  clampViewY(cc - (cc - lo) * k, cc + (hi - cc) * k);
}

function resetView() {
  if (props.domain) viewX.value = { ...props.domain };
  viewY.value = { ...props.yDomain };
  requestDraw();
}

/* ── 数据域变化：重置视口（实时模式跟随右推） ── */
watch(
  () => props.domain,
  (d) => {
    if (!d || d.t1 <= d.t0) return;
    if (props.live) {
      viewX.value = { ...d };
    } else {
      viewX.value = { ...d };
      viewY.value = { ...props.yDomain };
    }
    requestDraw();
  },
);
watch(
  () => props.yDomain,
  (d) => {
    if (!props.live) viewY.value = { ...d };
    requestDraw();
  },
);
watch([isDark, () => props.frames, () => props.seriesVisible], () =>
  requestDraw(),
);

/* ── 滚动条 thumb 位置 ── */
const xThumbStyle = computed(() => {
  const d = props.domain;
  if (!d || d.t1 <= d.t0) return { left: '0%', width: '100%' };
  const left = ((viewX.value.t0 - d.t0) / (d.t1 - d.t0)) * 100;
  const width = Math.max(
    1.5,
    ((viewX.value.t1 - viewX.value.t0) / (d.t1 - d.t0)) * 100,
  );
  return { left: `${left}%`, width: `${width}%` };
});
const yThumbStyle = computed(() => {
  const d = props.yDomain;
  const bottom = ((viewY.value.lo - d.lo) / (d.hi - d.lo)) * 100;
  const height = Math.max(
    1.5,
    ((viewY.value.hi - viewY.value.lo) / (d.hi - d.lo)) * 100,
  );
  return { bottom: `${bottom}%`, height: `${height}%` };
});

/* ── 事件标注层 pills（固定高，不随趋势压缩） ── */
const pills = computed(() => {
  const list: Array<{
    color: string;
    glyph: string;
    key: string;
    label: string;
    left: number;
    mark: null | TrendEventMark;
  }> = [];
  const d = props.domain;
  if (!d || d.t1 <= d.t0 || props.frames.length === 0) return list;
  const lo = viewX.value.t0;
  const hi = viewX.value.t1;
  const span = hi - lo;
  const pct = (t: number) => Math.max(7, Math.min(93, ((t - lo) / span) * 100));
  // MANUAL 段徽标（真实 MODE 数据；段中点，与视口相交才显示）
  if (props.seriesVisible.mode) {
    let m0 = -1;
    for (let i = 0; i <= props.frames.length; i++) {
      const man = i < props.frames.length && isManual(props.frames[i]!.mode);
      if (man && m0 < 0) m0 = i;
      if ((!man || i === props.frames.length) && m0 >= 0) {
        const i1 = Math.min(i - 1, props.frames.length - 1);
        const mid = (props.frames[m0]!.ts + props.frames[i1]!.ts) / 2;
        if (mid >= lo && mid <= hi) {
          list.push({
            color: WB360_EVENT_MARK_COLORS.manual,
            glyph: '⏸',
            key: `man-${m0}`,
            label: '手动',
            left: pct(mid),
            mark: null,
          });
        }
        m0 = -1;
      }
    }
  }
  for (const mk of props.events) {
    if (mk.ts < lo || mk.ts > hi) continue;
    list.push({
      color: markColorOf(mk),
      glyph: mk.glyph,
      key: mk.key,
      label: mk.label,
      left: pct(mk.ts),
      mark: mk,
    });
  }
  return list;
});

/** 徽标色：按动作剖面映射（▼诊断红 / ◆整定紫 / ▮验证绿；缺省=手动红） */
function markColorOf(mk: TrendEventMark): string {
  switch (mk.section) {
    case 'diag': {
      return WB360_EVENT_MARK_COLORS.diag;
    }
    case 'handling': {
      return WB360_EVENT_MARK_COLORS.verify;
    }
    case 'tuning': {
      return WB360_EVENT_MARK_COLORS.tuning;
    }
    default: {
      return WB360_EVENT_MARK_COLORS.manual;
    }
  }
}

/* ── 绘制 ── */
const p2 = (n: number) => (n < 10 ? `0${n}` : `${n}`);

function draw() {
  const host = canvasHostRef.value;
  const cvs = canvasRef.value;
  if (!host || !cvs) return;
  const W = host.clientWidth || 800;
  const H = host.clientHeight || 220;
  const dpr = window.devicePixelRatio || 1;
  if (cvs.width !== W * dpr || cvs.height !== H * dpr) {
    cvs.width = W * dpr;
    cvs.height = H * dpr;
  }
  const ctx = cvs.getContext('2d');
  if (!ctx) return;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, W, H);

  const P = palette.value;
  const { b: B, l: L, r: R, t: T } = M;
  const pw = W - L - R;
  const ph = H - T - B;
  if (pw <= 10 || ph <= 10) return;

  const yLo = viewY.value.lo;
  const yHi = viewY.value.hi;
  const yv = (v: number) => T + ph * (1 - (v - yLo) / (yHi - yLo));
  const opv = (v: number) => T + ph * (1 - (v - 20) / 60); // OP 右轴 20–80%

  // 无数据：不绘制坐标/波形，仅居中提示（诚实化：不画假轴）
  const hasData =
    props.domain !== null &&
    props.frames.length > 0 &&
    props.domain.t1 > props.domain.t0;
  if (!hasData) {
    ctx.fillStyle = P.axis;
    ctx.textAlign = 'center';
    ctx.font = '12px -apple-system,"PingFang SC","Microsoft YaHei",sans-serif';
    ctx.fillText('暂无趋势数据', W / 2, H / 2);
    return;
  }

  ctx.font = '10px -apple-system,"PingFang SC","Microsoft YaHei",sans-serif';

  // Y 主轴网格 + 标签（niceStep）
  const yStep = niceStep((yHi - yLo) / 4);
  ctx.textAlign = 'right';
  for (
    let g = Math.ceil(yLo / yStep - 1e-9) * yStep;
    g <= yHi + 1e-9;
    g += yStep
  ) {
    const y = yv(g);
    ctx.strokeStyle = P.grid;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(L, y);
    ctx.lineTo(W - R, y);
    ctx.stroke();
    ctx.fillStyle = P.axis;
    ctx.fillText(g.toFixed(yStep < 1 ? 1 : 0), L - 6, y + 3.5);
  }
  // OP 右轴标签
  ctx.textAlign = 'left';
  ctx.fillStyle = P.opAxis;
  ctx.font = '9.5px -apple-system,"PingFang SC","Microsoft YaHei",sans-serif';
  for (let g = 30; g <= 70; g += 20) {
    ctx.fillText(`${g}%`, W - R + 6, opv(g) + 3.5);
  }

  // X 刻度：5 档；跨度 ≤1.2 天用时钟 HH:MM，否则相对 -xD；末刻度 现在/截至
  const span = viewX.value.t1 - viewX.value.t0;
  const clockMode = span <= 1.2 * DAY_MS;
  const refT = props.live ? Date.now() : (props.domain?.t1 ?? Date.now());
  ctx.textAlign = 'center';
  ctx.font = '10px -apple-system,"PingFang SC","Microsoft YaHei",sans-serif';
  for (let k = 0; k <= 4; k++) {
    const t = viewX.value.t0 + (span * k) / 4;
    const x = L + (pw * k) / 4;
    if (k > 0 && k < 4) {
      ctx.strokeStyle = P.grid2;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x, T);
      ctx.lineTo(x, T + ph);
      ctx.stroke();
    }
    let label: string;
    if (k === 4) {
      label = props.live ? '现在' : '截至';
    } else if (clockMode) {
      const dt = new Date(t);
      label = `${p2(dt.getHours())}:${p2(dt.getMinutes())}`;
    } else {
      const dDay = Math.round(((refT - t) / DAY_MS) * 10) / 10;
      label = `-${dDay.toFixed(1)}D`;
    }
    ctx.fillStyle = P.axis;
    ctx.fillText(label, x, H - 8);
  }

  const rs = resampleFrames(props.frames, viewX.value.t0, viewX.value.t1, NP);
  const X = (i: number) => L + (pw * i) / NP;

  // MANUAL 背景带（红色半透明 + 虚线边界）
  if (props.seriesVisible.mode) {
    for (const seg of segmentsOf(rs.mode, (m) => isManual(m))) {
      const x0 = X(seg.startIdx);
      const x1 = X(seg.endIdx);
      ctx.fillStyle = manualFill.value;
      ctx.fillRect(x0, T, Math.max(2, x1 - x0), ph);
      ctx.strokeStyle = P.manualBand;
      ctx.lineWidth = 1;
      ctx.globalAlpha = 0.5;
      ctx.setLineDash([4, 3]);
      for (const bx of [x0, x1]) {
        ctx.beginPath();
        ctx.moveTo(bx, T);
        ctx.lineTo(bx, T + ph);
        ctx.stroke();
      }
      ctx.setLineDash([]);
      ctx.globalAlpha = 1;
    }
  }

  const dense = isDense(NP, pw);

  // 波形线（null 断笔）
  const strokeSeries = (
    vals: (null | number)[],
    mapY: (v: number) => number,
    color: string,
    width: number,
    alpha = 1,
  ) => {
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    ctx.globalAlpha = alpha;
    ctx.beginPath();
    let started = false;
    for (let i = 0; i <= NP; i++) {
      const v = vals[i]!;
      if (v === null) {
        started = false;
        continue;
      }
      const x = X(i);
      const y = mapY(v);
      if (started) ctx.lineTo(x, y);
      else {
        ctx.moveTo(x, y);
        started = true;
      }
    }
    ctx.stroke();
    ctx.globalAlpha = 1;
  };
  // 包络带（逐像素列 min/max）
  const fillEnvelope = (
    vals: (null | number)[],
    mapY: (v: number) => number,
    fill: string,
  ) => {
    const cols = Math.max(2, Math.floor(pw));
    const { cMax, cMin } = buildEnvelope(vals, cols);
    ctx.fillStyle = fill;
    ctx.beginPath();
    for (let c = 0; c < cols; c++) {
      if (!Number.isFinite(cMax[c]!)) continue;
      const x = L + (pw * c) / cols;
      const y = mapY(cMax[c]!);
      if (c === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    for (let c = cols - 1; c >= 0; c--) {
      if (!Number.isFinite(cMin[c]!)) continue;
      ctx.lineTo(L + (pw * c) / cols, mapY(cMin[c]!));
    }
    ctx.closePath();
    ctx.fill();
  };

  // SP 恒为线；PV/OP 密集切包络（v3 §5.2 防混叠）
  if (props.seriesVisible.sp) strokeSeries(rs.sp, yv, P.sp, 1.8);
  if (dense) {
    if (props.seriesVisible.op) fillEnvelope(rs.op, opv, envelopeFill.value.op);
    if (props.seriesVisible.pv) fillEnvelope(rs.pv, yv, envelopeFill.value.pv);
  } else {
    if (props.seriesVisible.op) strokeSeries(rs.op, opv, P.op, 1.1, 0.7);
    if (props.seriesVisible.pv) strokeSeries(rs.pv, yv, P.pv, 1.5);
  }

  // 质量码覆盖段：BAD 灰虚线 / UNCERTAIN 琥珀点划（垫层断开主线）
  const qualityOverlays: Array<{
    color: string;
    dash: number[];
    kind: 'BAD' | 'UNCERTAIN';
  }> = [
    { color: P.qualityBad, dash: [3, 2], kind: 'BAD' },
    { color: P.qualityUncertain, dash: [6, 3, 1, 3], kind: 'UNCERTAIN' },
  ];
  for (const q of qualityOverlays) {
    for (const seg of segmentsOf(rs.quality, (v) => v === q.kind)) {
      const path = new Path2D();
      let started = false;
      for (let k = seg.startIdx; k <= seg.endIdx; k++) {
        const v = rs.pv[k]!;
        if (v === null) {
          started = false;
          continue;
        }
        if (started) path.lineTo(X(k), yv(v));
        else {
          path.moveTo(X(k), yv(v));
          started = true;
        }
      }
      ctx.strokeStyle = P.underlay;
      ctx.lineWidth = 3.4;
      ctx.setLineDash([]);
      ctx.stroke(path);
      ctx.strokeStyle = q.color;
      ctx.lineWidth = 1.5;
      ctx.setLineDash(q.dash);
      ctx.stroke(path);
      ctx.setLineDash([]);
    }
  }
}

/* ── 交互绑定（非 mini：滚轮/双击/滚动条拖拽） ── */
function bindInteractions() {
  const host = canvasHostRef.value;
  if (!host || props.mini) return;

  host.addEventListener(
    'wheel',
    (e: WheelEvent) => {
      e.preventDefault();
      const f = e.deltaY > 0 ? ZOOM_FACTOR : 1 / ZOOM_FACTOR;
      const rect = host.getBoundingClientRect();
      if (e.shiftKey) {
        const frac = 1 - (e.clientY - rect.top - 26) / (rect.height - 56);
        zoomY(
          f,
          viewY.value.lo +
            Math.min(Math.max(frac, 0), 1) * (viewY.value.hi - viewY.value.lo),
        );
      } else {
        const t =
          viewX.value.t0 +
          Math.min(
            Math.max((e.clientX - rect.left - M.l) / (rect.width - 100), 0),
            1,
          ) *
            (viewX.value.t1 - viewX.value.t0);
        zoomX(f, t);
      }
    },
    { passive: false },
  );
  host.addEventListener('dblclick', resetView);

  bindBarDrag(xBarRef.value, true);
  bindBarDrag(yBarRef.value, false);
}

const xBarRef = ref<HTMLDivElement | null>(null);
const yBarRef = ref<HTMLDivElement | null>(null);

function bindBarDrag(bar: HTMLDivElement | null, horiz: boolean) {
  if (!bar) return;
  bar.addEventListener('pointerdown', (e: PointerEvent) => {
    const d = props.domain;
    if (!d) return;
    e.preventDefault();
    const thumbEl = bar.firstElementChild as HTMLElement | null;
    const tr = thumbEl?.getBoundingClientRect();
    const tl = horiz ? (tr?.width ?? 1) : (tr?.height ?? 1);
    const p = horiz ? e.clientX : e.clientY;
    const tp = horiz ? (tr?.left ?? 0) : (tr?.top ?? 0);
    const off = (p - tp) / tl;
    const mode = off < 0.14 ? 'lo' : (off > 0.86 ? 'hi' : 'pan');
    const sx = e.clientX;
    const sy = e.clientY;
    // 按下时视口快照（拖拽基准）
    const v0x: null | { t0: number; t1: number } = horiz
      ? { ...viewX.value }
      : null;
    const v0y: null | { hi: number; lo: number } = horiz
      ? null
      : { ...viewY.value };
    bar.style.cursor = 'grabbing';

    const mv = (ev: PointerEvent) => {
      if (horiz && v0x) {
        const dd = ((ev.clientX - sx) / (bar.clientWidth || 1)) * (d.t1 - d.t0);
        if (mode === 'pan') clampViewX(v0x.t0 - dd, v0x.t1 - dd);
        else if (mode === 'lo')
          clampViewX(Math.min(v0x.t1 - X_MIN_SPAN / 1000, v0x.t0 + dd), v0x.t1);
        else
          clampViewX(v0x.t0, Math.max(v0x.t0 + X_MIN_SPAN / 1000, v0x.t1 + dd));
      } else if (v0y) {
        const yd = props.yDomain;
        const dd =
          (-(ev.clientY - sy) / (bar.clientHeight || 1)) * (yd.hi - yd.lo);
        if (mode === 'pan') clampViewY(v0y.lo - dd, v0y.hi - dd);
        else if (mode === 'lo')
          clampViewY(Math.min(v0y.hi - Y_MIN_SPAN, v0y.lo + dd), v0y.hi);
        else clampViewY(v0y.lo, Math.max(v0y.lo + Y_MIN_SPAN, v0y.hi + dd));
      }
    };
    const up = () => {
      bar.style.cursor = 'grab';
      window.removeEventListener('pointermove', mv);
      window.removeEventListener('pointerup', up);
    };
    window.addEventListener('pointermove', mv);
    window.addEventListener('pointerup', up);
  });
}

onMounted(() => {
  resetView();
  bindInteractions();
  const host = canvasHostRef.value;
  if (host && typeof ResizeObserver !== 'undefined') {
    resizeObserver = new ResizeObserver(() => requestDraw());
    resizeObserver.observe(host);
  }
  window.addEventListener('resize', requestDraw);
});

onBeforeUnmount(() => {
  resizeObserver?.disconnect();
  window.removeEventListener('resize', requestDraw);
});

defineExpose({ requestDraw });
</script>

<template>
  <div class="wb360-trend">
    <!-- 事件标注层：固定高度，与绘图区左右边距对齐（L46/R54） -->
    <div
      :aria-label="mini ? '事件标注（迷你）' : '事件标注'"
      class="tlane"
      :class="{ mini }"
    >
      <span v-if="mini" class="lane-cap">监视（迷你）</span>
      <button
        v-for="pill in pills"
        :key="pill.key"
        class="pill"
        :style="{ color: pill.color, left: `${pill.left}%` }"
        :title="pill.label"
        type="button"
        @click="pill.mark && emit('eventClick', pill.mark)"
      >
        <span>{{ pill.glyph }}</span
        >{{ pill.label }}
        <i v-if="pill.mark" class="tri"></i>
      </button>
    </div>
    <div ref="canvasHostRef" class="chart-host">
      <canvas ref="canvasRef" class="chart-cvs"></canvas>
    </div>
    <div
      v-if="!mini"
      ref="xBarRef"
      aria-label="时间轴滚动条"
      class="xbar"
      title="拖动平移 · 拉两端缩放"
    >
      <i :style="xThumbStyle" class="thumb"></i>
    </div>
    <div
      v-if="!mini"
      ref="yBarRef"
      aria-label="幅值轴滚动条"
      class="ybar"
      title="拖动平移 · 拉两端缩放"
    >
      <i :style="yThumbStyle" class="thumb"></i>
    </div>
  </div>
</template>

<style scoped>
.wb360-trend {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-height: 0;
  position: relative;
}

/* 事件标注层（固定高，不随趋势压缩变形） */
.tlane {
  flex: none;
  height: 26px;
  margin: 0 54px 0 46px;
  position: relative;
}

.tlane.mini {
  height: 22px;
}

.lane-cap {
  color: hsl(var(--muted-foreground) / 60%);
  font-size: 11px;
  left: 0;
  position: absolute;
  top: 5px;
}

.pill {
  align-items: center;
  background: hsl(var(--card));
  border: 1px solid currentcolor;
  border-radius: 9px;
  color: inherit;
  cursor: pointer;
  display: inline-flex;
  font-size: 11px;
  font-weight: 600;
  gap: 4px;
  height: 18px;
  line-height: 1;
  padding: 0 8px;
  position: absolute;
  top: 3px;
  transform: translateX(-50%);
  white-space: nowrap;
}

.pill:hover {
  box-shadow: 0 1px 2px rgb(16 24 40 / 12%);
}

.pill .tri {
  border: 4px solid transparent;
  border-top-color: currentcolor;
  left: 50%;
  margin-left: -4px;
  position: absolute;
  top: 17px;
}

.chart-host {
  flex: 1;
  min-height: 0;
  position: relative;
  width: 100%;
}

.chart-cvs {
  display: block;
  height: 100%;
  width: 100%;
}

/* 底部 X 滚动条（与绘图区对齐：左 46 / 右 54） */
.xbar {
  background: hsl(var(--accent) / 30%);
  border-radius: 4px;
  cursor: grab;
  flex: none;
  height: 14px;
  margin: 2px 54px 6px 46px;
  position: relative;
  touch-action: none;
}

/* 右侧 Y 滚动条 */
.ybar {
  background: hsl(var(--accent) / 30%);
  border-radius: 4px;
  bottom: 24px;
  cursor: grab;
  position: absolute;
  right: 8px;
  top: 8px;
  touch-action: none;
  width: 14px;
}

.thumb {
  background: hsl(var(--accent-foreground) / 25%);
  border-radius: 3px;
  min-height: 26px;
  min-width: 26px;
  position: absolute;
}

.xbar .thumb {
  bottom: 2px;
  position: absolute;
  top: 2px;
  min-width: 26px;
}

.ybar .thumb {
  left: 2px;
  right: 2px;
}
</style>
