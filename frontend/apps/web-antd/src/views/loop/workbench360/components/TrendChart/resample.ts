/**
 * 趋势采样引擎（纯函数，workbench360 P1）
 *
 * 生产映射（设计方案 v3 §5.1，防走样重点）：
 * - 原型的连续信号函数（pvAt/spAt/…）→ 生产为 API 返回的离散序列
 *   （monitor ≤2000 点 LTTB / waveform ≤2000 点）；
 * - 视口采样 "dt=span/3600 恒 ≈3600 点" → 对离散序列做等距网格重采样：
 *   数值通道（PV/SP/OP）线性插值，分类通道（MODE/质量码）桶内最近邻；
 * - 该引擎同时服务于：初始绘制 / 缩放平移重采样（视口变化重跑）/ 实时追加重绘。
 */
import type { TrendFrame } from './types';

/** 质量码段（连续段聚合用） */
export interface QualitySegment {
  endIdx: number;
  kind: 'BAD' | 'UNCERTAIN';
  startIdx: number;
}

/** MANUAL 背景带段 */
export interface ModeSegment {
  endIdx: number;
  startIdx: number;
}

/** 等距网格重采样结果（长度 Np+1，索引 i 对应 t0 + i*dt） */
export interface Resampled {
  op: (null | number)[];
  mode: (null | number)[];
  pv: (null | number)[];
  quality: ('BAD' | 'GOOD' | 'UNCERTAIN' | null)[];
  sp: (null | number)[];
}

/**
 * 线性插值取值（通道内 null 视为缺失，不跨缺失段插值：两端都有效才插）。
 */
function interpAt(
  ts: number[],
  vals: (null | number)[],
  t: number,
  hint: number,
): null | number {
  const n = ts.length;
  if (n === 0) return null;
  // 二分定位（hint=上次游标，单调前进场景 O(1)）
  let i = Math.min(hint, n - 1);
  if (ts[i]! > t) i = 0;
  while (i < n - 1 && ts[i + 1]! < t) i++;
  const t0 = ts[i]!;
  const t1 = ts[Math.min(i + 1, n - 1)]!;
  if (t <= t0) return vals[i] ?? null;
  if (t >= ts[n - 1]!) return vals[n - 1] ?? null;
  if (t1 === t0) return vals[i] ?? null;
  const v0 = vals[i];
  const v1 = vals[i + 1];
  if (v0 === null || v0 === undefined) return null;
  if (v1 === null || v1 === undefined) return null;
  const k = (t - t0) / (t1 - t0);
  return v0 + (v1 - v0) * k;
}

/**
 * 等距网格重采样到 Np 点（含端点 Np+1 个样本）。
 *
 * @param frames 源序列（ts 升序、毫秒）
 * @param t0/t1 目标网格起止（毫秒，须 ⊆ 数据域或数据域内）
 */
export function resampleFrames(
  frames: TrendFrame[],
  t0: number,
  t1: number,
  np: number,
): Resampled {
  const out: Resampled = {
    mode: [],
    op: [],
    pv: [],
    quality: [],
    sp: [],
  };
  if (frames.length === 0 || t1 <= t0) {
    return out;
  }
  const ts = frames.map((f) => f.ts);
  const dt = (t1 - t0) / np;
  // 预提取通道
  const pv = frames.map((f) => f.pv);
  const sp = frames.map((f) => f.sp);
  const op = frames.map((f) => f.op);
  const mode = frames.map((f) => f.mode);
  const quality = frames.map((f) => f.quality);

  // 分类通道：桶内最近邻（每个网格点找最近源点）
  let hint = 0;
  for (let i = 0; i <= np; i++) {
    const t = t0 + dt * i;
    out.pv.push(interpAt(ts, pv, t, hint));
    out.sp.push(interpAt(ts, sp, t, hint));
    out.op.push(interpAt(ts, op, t, hint));
    // 最近邻（游标单调前进）
    while (hint < ts.length - 1 && Math.abs(ts[hint + 1]! - t) <= Math.abs(ts[hint]! - t))
      hint++;
    const near = frames[hint]!;
    out.mode.push(near.mode);
    out.quality.push(near.quality);
  }
  return out;
}

/**
 * 逐像素列 min/max 包络（密集渲染防混叠，v3 §5.2）。
 *
 * 生产判据（原型"振荡周期 <12px"的一般化）：网格点数 > 绘制宽度像素时
 * 每列必有多个样本，切包络带；放大后回到波形线。
 */
export function buildEnvelope(
  vals: (null | number)[],
  cols: number,
): { cMax: number[]; cMin: number[] } {
  const n = cols < 2 ? 2 : cols;
  const cMin = new Array<number>(n).fill(Number.POSITIVE_INFINITY);
  const cMax = new Array<number>(n).fill(Number.NEGATIVE_INFINITY);
  for (let i = 0; i < vals.length; i++) {
    const v = vals[i];
    if (v === null || v === undefined) continue;
    const c = Math.min(n - 1, Math.floor((i / vals.length) * n));
    if (v < cMin[c]!) cMin[c] = v;
    if (v > cMax[c]!) cMax[c] = v;
  }
  return { cMax, cMin };
}

/** 是否应启用包络渲染（每像素平均样本数 > 1 即密集） */
export function isDense(np: number, plotWidthPx: number): boolean {
  if (plotWidthPx <= 0) return false;
  return np + 1 > plotWidthPx;
}

/** 连续段提取（质量码 / MANUAL 带） */
export function segmentsOf<T>(
  vals: T[],
  hit: (v: T) => boolean,
): Array<{ endIdx: number; startIdx: number }> {
  const segs: Array<{ endIdx: number; startIdx: number }> = [];
  let start = -1;
  for (let i = 0; i <= vals.length; i++) {
    const h = i < vals.length && hit(vals[i]!);
    if (h && start < 0) start = i;
    if (!h && start >= 0) {
      segs.push({ endIdx: i - 1, startIdx: start });
      start = -1;
    }
  }
  return segs;
}

/** 数据域 Y 轴范围（PV/SP 数值 min/max + 8% 余量；无数据退 [0,1]） */
export function computeYDomain(
  frames: TrendFrame[],
): { hi: number; lo: number } {
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

/** nice 轴步长（原型 niceStep 同款） */
export function niceStep(range: number): number {
  const p = 10 ** Math.floor(Math.log10(range || 1));
  const n = range / p;
  const step = n < 1.5 ? 1 : n < 3.5 ? 2 : n < 7.5 ? 5 : 10;
  return step * p;
}
