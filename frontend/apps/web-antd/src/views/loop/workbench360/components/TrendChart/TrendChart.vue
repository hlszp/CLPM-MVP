<script lang="ts">
/**
 * 趋势图基座（workbench360 P1）——SVG 手绘 + 视口交互
 *
 * 渲染规格（v3 §5.2，对齐原型 drawChart proc 模式）：
 * - 绘制恒采样 3600 点（resampleFrames 等距网格，插值+最近邻）；
 * - 系列：PV/SP 左轴、OP 右轴 20–80%；MODE=Manual 背景带（红半透明+虚线边界）；
 * - 质量码段：BAD 灰虚线 / UNCERTAIN 琥珀点划（垫层断开主线）；
 * - 密集（样本数 > 绘制宽度像素）自动切 min/max 包络带；
 * - 轴：Y niceStep 网格 + X 五档刻度（≤1.2 天时钟制，否则相对 -xD）。
 *
 * 交互规格（v3 §5.3）：
 * - 滚轮 X 缩放（因子 1.15、光标中心）、Shift+滚轮 Y 缩放；
 * - 双击复位；底部 X 滚动条 / 右侧 Y 滚动条（14%/86% 两端拉伸、中段平移）；
 * - 全部重绘经 rAF 节流（scheduleDraw）。
 */
import type { PropType } from 'vue';
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';

import { usePreferences } from '@vben/preferences';

import { resolveModeLabel } from '#/composables/use-loop-realtime';
import {
  WB360_ENVELOPE_FILL,
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
import type { TimeRange, TrendFrame, YRange } from './types';

/** 绘图区边距（原型：L=46 R=54 T=10 B=30） */
const M = { b: 30, l: 46, r: 54, t: 10 };
const DAY_MS = 86_400_000;
/** X 视口最小跨度（60s，v3 §5.3） */
const X_MIN_SPAN_MS = 60_000;
/** Y 视口最小量程（0.5，v3 §5.3） */
const Y_MIN_SPAN = 0.5;
/** 缩放因子 */
const ZOOM_FACTOR = 1.15;
/** OP 右轴固定量程（原型口径 20–80%） */
const OP_LO = 20;
const OP_HI = 80;

const NS = 'http://www.w3.org/2000/svg';

function svgEl(tag: string, attrs: Record<string, string | number>) {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, String(v));
  return el;
}

/** 数据域 Y 范围（PV/SP min/max + 8% 余量） */
function computeFramesY(frames: TrendFrame[]): YRange {
  let lo = Number.POSITIVE_INFINITY;
  let hi = Number.NEGATIVE_INFINITY;
  for (const f of frames) {
    for (const v of [f.pv, f.sp]) {
      if (v === null || v === undefined || !Number.isFinite(v)) continue;
      if (v < lo) lo = v;
      if (v > hi) hi = v;
    }
  }
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return { hi: 1, lo: 0 };
  if (hi === lo) return { hi: hi + 1, lo: lo - 1 };
  const pad = (hi - lo) * 0.08;
  return { hi: hi + pad, lo: lo - pad };
}

export default {
  name: 'Wb360TrendChart',
  props: {
    /** 数据域（XDOMAIN；null=无数据） */
    domain: {
      type: Object as PropType<null | TimeRange>,
      default: null,
    },
    frames: {
      type: Array as PropType<TrendFrame[]>,
      default: () => [],
    },
    /** 末刻度文案：实时="现在" / 历史="截至" */
    liveTail: {
      type: String,
      default: '截至',
    },
    /** 回路 MODE 数值映射（REST 下发；Manual 段判定用） */
    modeMapping: {
      type: Object as PropType<null | Record<string, string>>,
      default: null,
    },
    /** 迷你态（隐藏滚动条） */
    mini: {
      type: Boolean,
      default: false,
    },
    /** 视口外部复位信号（窗口/回路/模式切换时递增） */
    resetTick: {
      type: Number,
      default: 0,
    },
  },
  emits: ['view-change'],
  setup(props, { emit, expose }) {
    const { isDark } = usePreferences();
    const hostRef = ref<HTMLElement | null>(null);
    const xbarRef = ref<HTMLElement | null>(null);
    const xthumbRef = ref<HTMLElement | null>(null);
    const ybarRef = ref<HTMLElement | null>(null);
    const ythumbRef = ref<HTMLElement | null>(null);

    const pal = computed(() =>
      isDark.value ? WB360_TREND_PALETTE_DARK : WB360_TREND_PALETTE_LIGHT,
    );

    // ── 视口状态 ──
    const viewX = ref<TimeRange>({ t0: 0, t1: 1 });
    const viewY = ref<YRange>({ hi: 1, lo: 0 });
    const yDomain = ref<YRange>({ hi: 1, lo: 0 });
    const xThumbStyle = ref<Record<string, string>>({});
    const yThumbStyle = ref<Record<string, string>>({});

    function emitView() {
      emit('view-change', { ...viewX.value });
    }

    function resetView() {
      if (props.domain) viewX.value = { ...props.domain };
      yDomain.value = computeFramesY(props.frames);
      viewY.value = { ...yDomain.value };
      scheduleDraw();
      emitView();
    }

    // ── 视口钳制 / 缩放（对齐原型 clampViewX/Y、zoomX/Y） ──
    function clampViewX(t0: number, t1: number) {
      const dom = props.domain;
      if (!dom) return;
      const s = Math.max(X_MIN_SPAN_MS, Math.min(t1 - t0, dom.t1 - dom.t0));
      let a = t0;
      let b = a + s;
      if (b > dom.t1) {
        b = dom.t1;
        a = b - s;
      }
      if (a < dom.t0) {
        a = dom.t0;
        b = a + s;
      }
      viewX.value = { t0: a, t1: b };
      scheduleDraw();
      emitView();
    }

    function clampViewY(lo: number, hi: number) {
      const s = Math.max(
        Y_MIN_SPAN,
        Math.min(hi - lo, yDomain.value.hi - yDomain.value.lo),
      );
      let l = lo;
      let h = l + s;
      if (h > yDomain.value.hi) {
        h = yDomain.value.hi;
        l = h - s;
      }
      if (l < yDomain.value.lo) {
        l = yDomain.value.lo;
        h = l + s;
      }
      viewY.value = { lo: l, hi: h };
      scheduleDraw();
    }

    function zoomX(factor: number, center?: number) {
      const v = viewX.value;
      const s0 = v.t1 - v.t0;
      const s = Math.max(X_MIN_SPAN_MS, s0 * factor);
      const c = center ?? (v.t0 + v.t1) / 2;
      const k = s / s0;
      clampViewX(c - (c - v.t0) * k, c + (v.t1 - c) * k);
    }

    function zoomY(factor: number, center?: number) {
      const v = viewY.value;
      const s0 = v.hi - v.lo;
      const s = Math.max(Y_MIN_SPAN, s0 * factor);
      const c = center ?? (v.lo + v.hi) / 2;
      const k = s / s0;
      clampViewY(c - (c - v.lo) * k, c + (v.hi - c) * k);
    }

    // ── rAF 节流绘制（图 + 滚动条） ──
    let rafPending = false;
    function scheduleDraw() {
      if (rafPending) return;
      rafPending = true;
      requestAnimationFrame(() => {
        rafPending = false;
        draw();
        drawBars();
      });
    }

    function draw() {
      const host = hostRef.value;
      if (!host) return;
      const W = host.clientWidth || 800;
      const H = host.clientHeight || 220;
      host.textContent = '';
      const svg = svgEl('svg', {
        height: '100%',
        preserveAspectRatio: 'none',
        viewBox: `0 0 ${W} ${H}`,
        width: '100%',
      });
      const p = pal.value;
      const pw = W - M.l - M.r;
      const ph = H - M.t - M.b;
      if (pw <= 0 || ph <= 0) return;

      const yLo = viewY.value.lo;
      const yHi = viewY.value.hi;
      const yv = (v: number) => M.t + ph * (1 - (v - yLo) / (yHi - yLo));
      const opv = (v: number) =>
        M.t + ph * (1 - (v - OP_LO) / (OP_HI - OP_LO));

      const grid = svgEl('g', {});
      svg.appendChild(grid);
      // Y 主轴网格 + 刻度
      const yStep = niceStep((yHi - yLo) / 4);
      for (
        let g = Math.ceil(yLo / yStep - 1e-9) * yStep;
        g <= yHi + 1e-9;
        g += yStep
      ) {
        const y = yv(g);
        grid.appendChild(
          svgEl('line', {
            stroke: p.grid,
            x1: M.l,
            x2: W - M.r,
            y1: y,
            y2: y,
          }),
        );
        const txt = svgEl('text', {
          'font-size': 10,
          'text-anchor': 'end',
          fill: p.axis,
          x: M.l - 6,
          y: y + 3.5,
        });
        txt.textContent = g.toFixed(yStep < 1 ? 1 : 0);
        grid.appendChild(txt);
      }
      // OP 右轴刻度（40/60/80）
      for (const g of [40, 60, 80]) {
        const txt = svgEl('text', {
          'font-size': 9.5,
          fill: p.opAxis,
          x: W - M.r + 6,
          y: opv(g) + 3.5,
        });
        txt.textContent = `${g}%`;
        grid.appendChild(txt);
      }
      // X 五档刻度
      const span = viewX.value.t1 - viewX.value.t0;
      const clockMode = span <= DAY_MS * 1.2;
      for (let i = 0; i < 5; i++) {
        const t = viewX.value.t0 + (span * i) / 4;
        let label: string;
        if (i === 4) {
          label = props.liveTail;
        } else if (clockMode) {
          const d = new Date(t);
          label = `${String(d.getHours()).padStart(2, '0')}:${String(
            d.getMinutes(),
          ).padStart(2, '0')}`;
        } else {
          label = `-${(Math.round(((Date.now() - t) / DAY_MS) * 10) / 10).toFixed(1)}D`;
        }
        const x = M.l + (pw * i) / 4;
        if (i > 0 && i < 4) {
          grid.appendChild(
            svgEl('line', {
              stroke: p.grid2,
              x1: x,
              x2: x,
              y1: M.t,
              y2: M.t + ph,
            }),
          );
        }
        const txt = svgEl('text', {
          'font-size': 10,
          'text-anchor': 'middle',
          fill: p.axis,
          x,
          y: H - 8,
        });
        txt.textContent = label;
        grid.appendChild(txt);
      }

      // ── 数据层 ──
      if (props.frames.length === 0 || !props.domain) {
        host.appendChild(svg);
        return;
      }
      const rs = resampleFrames(
        props.frames,
        viewX.value.t0,
        viewX.value.t1,
        WB360_SAMPLE_POINTS,
      );
      const Np = rs.pv.length - 1;
      if (Np <= 0) {
        host.appendChild(svg);
        return;
      }
      const X = (i: number) => M.l + (pw * i) / Np;

      // MODE=Manual 背景带（虚线边界）
      const isManual = (m: null | number) =>
        resolveModeLabel(m, props.modeMapping) === 'Manual';
      const manFill = isDark.value
        ? WB360_MANUAL_BAND_FILL.dark
        : WB360_MANUAL_BAND_FILL.light;
      for (const seg of segmentsOf(rs.mode, isManual)) {
        const x0 = X(seg.startIdx);
        const x1 = X(seg.endIdx);
        svg.appendChild(
          svgEl('rect', {
            fill: manFill,
            height: ph,
            width: Math.max(2, x1 - x0),
            x: x0,
            y: M.t,
          }),
        );
        for (const bx of [x0, x1]) {
          svg.appendChild(
            svgEl('line', {
              'stroke-dasharray': '4 3',
              'stroke-width': 1,
              stroke: p.manualBand,
              opacity: 0.5,
              x1: bx,
              x2: bx,
              y1: M.t,
              y2: M.t + ph,
            }),
          );
        }
      }

      const mkPath = (vals: (null | number)[], map: (v: number) => number) => {
        let d = '';
        let started = false;
        for (let i = 0; i <= Np; i++) {
          const v = vals[i];
          if (v === null || v === undefined || !Number.isFinite(v)) {
            started = false;
            continue;
          }
          d += `${started ? 'L' : 'M'}${X(i).toFixed(1)} ${map(v).toFixed(1)} `;
          started = true;
        }
        return d.trim();
      };

      // SP 基线
      const spPath = mkPath(rs.sp, yv);
      if (spPath) {
        svg.appendChild(
          svgEl('path', {
            d: spPath,
            fill: 'none',
            stroke: p.sp,
            'stroke-width': 1.8,
          }),
        );
      }

      // PV / OP：密集时包络带，否则线
      const dense = isDense(Np, pw);
      if (dense) {
        const cols = Math.max(2, Math.floor(pw));
        const drawEnv = (
          vals: (null | number)[],
          map: (v: number) => number,
          fill: string,
        ) => {
          const { cMin, cMax } = buildEnvelope(vals, cols);
          let d = '';
          for (let c = 0; c < cols; c++) {
            if (!Number.isFinite(cMin[c]) || !Number.isFinite(cMax[c]))
              continue;
            d += `${d ? 'L' : 'M'}${X((c * Np) / cols).toFixed(1)} ${map(
              cMax[c]!,
            ).toFixed(1)} `;
          }
          for (let c = cols - 1; c >= 0; c--) {
            if (!Number.isFinite(cMin[c])) continue;
            d += `L${X((c * Np) / cols).toFixed(1)} ${map(cMin[c]!).toFixed(1)} `;
          }
          if (!d) return;
          svg.appendChild(svgEl('path', { d: `${d}Z`, fill, stroke: 'none' }));
        };
        drawEnv(
          rs.op,
          opv,
          isDark.value
            ? WB360_ENVELOPE_FILL.opDark
            : WB360_ENVELOPE_FILL.opLight,
        );
        drawEnv(
          rs.pv,
          yv,
          isDark.value
            ? WB360_ENVELOPE_FILL.pvDark
            : WB360_ENVELOPE_FILL.pvLight,
        );
      } else {
        const opPath = mkPath(rs.op, opv);
        if (opPath) {
          svg.appendChild(
            svgEl('path', {
              d: opPath,
              fill: 'none',
              opacity: 0.7,
              stroke: p.op,
              'stroke-width': 1.1,
            }),
          );
        }
        const pvPath = mkPath(rs.pv, yv);
        if (pvPath) {
          svg.appendChild(
            svgEl('path', {
              d: pvPath,
              fill: 'none',
              stroke: p.pv,
              'stroke-width': 1.5,
            }),
          );
        }
      }

      // 质量码段覆盖（BAD 灰虚线 / UNCERTAIN 琥珀点划；垫层断开主线）
      const qualityStyles: Array<{
        dash: string;
        kind: 'BAD' | 'UNCERTAIN';
        stroke: string;
      }> = [
        { dash: '3 2', kind: 'BAD', stroke: p.qualityBad },
        { dash: '6 3 1 3', kind: 'UNCERTAIN', stroke: p.qualityUncertain },
      ];
      for (const qs of qualityStyles) {
        for (const seg of segmentsOf(rs.quality, (q) => q === qs.kind)) {
          let d = '';
          for (let k = seg.startIdx; k <= seg.endIdx; k++) {
            const v = rs.pv[k];
            if (v === null || v === undefined) continue;
            d += `${d ? 'L' : 'M'}${X(k).toFixed(1)} ${yv(v).toFixed(1)} `;
          }
          if (!d) continue;
          svg.appendChild(
            svgEl('path', {
              d,
              fill: 'none',
              stroke: p.underlay,
              'stroke-width': 3.4,
            }),
          );
          svg.appendChild(
            svgEl('path', {
              d,
              'stroke-dasharray': qs.dash,
              fill: 'none',
              stroke: qs.stroke,
              'stroke-width': 1.5,
            }),
          );
        }
      }

      host.appendChild(svg);
    }

    function drawBars() {
      const dom = props.domain;
      if (dom) {
        const w = ((viewX.value.t1 - viewX.value.t0) / (dom.t1 - dom.t0)) * 100;
        const l = ((viewX.value.t0 - dom.t0) / (dom.t1 - dom.t0)) * 100;
        xThumbStyle.value = {
          left: `${l}%`,
          width: `${Math.max(1.5, w)}%`,
        };
      }
      const yd = yDomain.value;
      const h = ((viewY.value.hi - viewY.value.lo) / (yd.hi - yd.lo)) * 100;
      const b = ((viewY.value.lo - yd.lo) / (yd.hi - yd.lo)) * 100;
      yThumbStyle.value = {
        bottom: `${b}%`,
        height: `${Math.max(1.5, h)}%`,
      };
    }

    // ── 滚动条拖拽（14%/86% 两端拉伸、中段平移） ──
    function onXbarDown(e: PointerEvent) {
      const bar = xbarRef.value;
      const thumb = xthumbRef.value;
      if (!bar || !thumb) return;
      e.preventDefault();
      const rect = thumb.getBoundingClientRect();
      const off = (e.clientX - rect.left) / (rect.width || 1);
      const mode: 'hi' | 'lo' | 'pan' =
        off < 0.14 ? 'lo' : off > 0.86 ? 'hi' : 'pan';
      const sx = e.clientX;
      const v0 = { ...viewX.value };
      const dom = props.domain;
      bar.style.cursor = 'grabbing';
      const onMove = (ev: PointerEvent) => {
        if (!dom) return;
        const dd = ((ev.clientX - sx) / (bar.clientWidth || 1)) * (dom.t1 - dom.t0);
        if (mode === 'pan') clampViewX(v0.t0 + dd, v0.t1 + dd);
        else if (mode === 'lo')
          clampViewX(Math.min(v0.t1 - X_MIN_SPAN_MS, v0.t0 + dd), v0.t1);
        else clampViewX(v0.t0, Math.max(v0.t0 + X_MIN_SPAN_MS, v0.t1 + dd));
      };
      const onUp = () => {
        bar.style.cursor = 'grab';
        window.removeEventListener('pointermove', onMove);
        window.removeEventListener('pointerup', onUp);
      };
      window.addEventListener('pointermove', onMove);
      window.addEventListener('pointerup', onUp);
    }

    function onYbarDown(e: PointerEvent) {
      const bar = ybarRef.value;
      const thumb = ythumbRef.value;
      if (!bar || !thumb) return;
      e.preventDefault();
      const rect = thumb.getBoundingClientRect();
      const off = (e.clientY - rect.top) / (rect.height || 1);
      const mode: 'hi' | 'lo' | 'pan' =
        off < 0.14 ? 'lo' : off > 0.86 ? 'hi' : 'pan';
      const sy = e.clientY;
      const v0 = { ...viewY.value };
      bar.style.cursor = 'grabbing';
      const onMove = (ev: PointerEvent) => {
        const dd =
          (-(ev.clientY - sy) / (bar.clientHeight || 1)) *
          (yDomain.value.hi - yDomain.value.lo);
        if (mode === 'pan') clampViewY(v0.lo + dd, v0.hi + dd);
        else if (mode === 'lo')
          clampViewY(Math.min(v0.hi - Y_MIN_SPAN, v0.lo + dd), v0.hi);
        else clampViewY(v0.lo, Math.max(v0.lo + Y_MIN_SPAN, v0.hi + dd));
      };
      const onUp = () => {
        bar.style.cursor = 'grab';
        window.removeEventListener('pointermove', onMove);
        window.removeEventListener('pointerup', onUp);
      };
      window.addEventListener('pointermove', onMove);
      window.addEventListener('pointerup', onUp);
    }

    // ── 图表区交互 ──
    function onWheel(e: WheelEvent) {
      e.preventDefault();
      const host = hostRef.value;
      if (!host) return;
      const rect = host.getBoundingClientRect();
      if (e.shiftKey) {
        const frac = 1 - (e.clientY - rect.top) / (rect.height || 1);
        const c =
          viewY.value.lo +
          Math.max(0, Math.min(1, frac)) * (viewY.value.hi - viewY.value.lo);
        zoomY(e.deltaY > 0 ? ZOOM_FACTOR : 1 / ZOOM_FACTOR, c);
      } else {
        const frac =
          (e.clientX - rect.left - M.l) / (rect.width - M.l - M.r || 1);
        const t =
          viewX.value.t0 +
          Math.max(0, Math.min(1, frac)) * (viewX.value.t1 - viewX.value.t0);
        zoomX(e.deltaY > 0 ? ZOOM_FACTOR : 1 / ZOOM_FACTOR, t);
      }
    }

    function onDblClick() {
      resetView();
    }

    // ── 生命周期与响应 ──
    let resizeObserver: null | ResizeObserver = null;

    onMounted(() => {
      resetView();
      if (hostRef.value && typeof ResizeObserver !== 'undefined') {
        resizeObserver = new ResizeObserver(() => scheduleDraw());
        resizeObserver.observe(hostRef.value);
      }
    });

    onBeforeUnmount(() => {
      resizeObserver?.disconnect();
    });

    // 数据/主题/末刻度变化 → 重绘；实时追加时视口跟随数据尾（保持跨度）
    watch(
      () => [props.frames, isDark.value, props.liveTail, props.modeMapping],
      () => {
        if (props.domain) {
          const v = viewX.value;
          if (v.t1 > props.domain.t1) {
            const span = v.t1 - v.t0;
            viewX.value = { t0: props.domain.t1 - span, t1: props.domain.t1 };
            emitView();
          }
        }
        scheduleDraw();
      },
    );

    // 复位信号（窗口/回路/模式切换）→ 视口重置
    watch(
      () => props.resetTick,
      () => resetView(),
    );

    expose({
      resetView,
      viewX,
      viewY,
    });

    return {
      hostRef,
      onDblClick,
      onWheel,
      onXbarDown,
      onYbarDown,
      pal,
      xThumbStyle,
      xbarRef,
      xthumbRef,
      yThumbStyle,
      ybarRef,
      ythumbRef,
    };
  },
};
</script>

<template>
  <div class="wb360-trend">
    <div
      ref="hostRef"
      class="wb360-trend-host"
      aria-label="趋势图"
      @dblclick="onDblClick"
      @wheel="onWheel"
    ></div>
    <div
      v-if="!mini"
      ref="xbarRef"
      class="wb360-xbar"
      title="拖动平移 · 拉两端缩放"
      @pointerdown="onXbarDown"
    >
      <i ref="xthumbRef" :style="xThumbStyle"></i>
    </div>
    <div
      v-if="!mini"
      ref="ybarRef"
      class="wb360-ybar"
      title="拖动平移 · 拉两端缩放"
      @pointerdown="onYbarDown"
    >
      <i ref="ythumbRef" :style="yThumbStyle"></i>
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
  width: 100%;
}

.wb360-trend-host {
  flex: 1;
  min-height: 0;
  width: 100%;
  cursor: crosshair;
  touch-action: none;
}

.wb360-xbar {
  position: relative;
  flex: none;
  height: 14px;
  margin: 2px 54px 6px 46px;
  cursor: grab;
  border-radius: 4px;
  background: hsl(var(--accent) / 8%);
  touch-action: none;
}

.wb360-xbar i {
  position: absolute;
  top: 2px;
  bottom: 2px;
  border-radius: 3px;
  min-width: 26px;
  background: var(--wb360-scrollbar-thumb);
  box-shadow: inset 0 0 0 1px rgb(255 255 255 / 35%);
}

.wb360-ybar {
  position: absolute;
  top: 8px;
  right: 8px;
  bottom: 24px;
  width: 14px;
  cursor: grab;
  border-radius: 4px;
  background: hsl(var(--accent) / 8%);
  touch-action: none;
}

.wb360-ybar i {
  position: absolute;
  left: 2px;
  right: 2px;
  border-radius: 3px;
  min-height: 26px;
  background: var(--wb360-scrollbar-thumb);
  box-shadow: inset 0 0 0 1px rgb(255 255 255 / 35%);
}
</style>
