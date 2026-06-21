/**
 * Mock 数据：时序波形数据生成器
 *
 * 规则（UI/UX §3.1.5 + §10.5）：
 * - 30 天时间窗口，LTTB 降采样至 maxPoints=2000
 * - PV 按质量码分段：Good 实线 / Bad 灰色虚线断线 / Uncertain 琥珀虚线
 * - SP/OP 不受 PV 质量码影响，始终正常显示
 * - 根据回路诊断标签生成不同波形模式（振荡/粘滞/过激/正常）
 */

import type { TimeseriesPoint, TimeseriesDataset, ScatterPoint } from './types';
import type { PVQuality } from '../components/PVQualityBadge';
import { findLoop } from './loops';

const MAX_POINTS = 2000;
const WINDOW_DAYS = 30;

/** 简易 LTTB 降采样（保留趋势特征点） */
function lttbDownsample(points: TimeseriesPoint[], threshold: number): TimeseriesPoint[] {
  if (points.length <= threshold) return points;
  const sampled: TimeseriesPoint[] = [];
  const bucketSize = (points.length - 2) / (threshold - 2);
  let a = 0;
  sampled.push(points[a]);

  for (let i = 0; i < threshold - 2; i++) {
    const rangeStart = Math.floor((i + 1) * bucketSize) + 1;
    const rangeEnd = Math.min(Math.floor((i + 2) * bucketSize) + 1, points.length - 1);
    const avgRangeStart = Math.floor(i * bucketSize) + 1;
    const avgRangeEnd = Math.min(Math.floor((i + 1) * bucketSize) + 1, points.length - 1);

    // 计算下一桶的平均值
    let avgX = 0, avgY = 0;
    let count = 0;
    for (let j = avgRangeStart; j < avgRangeEnd; j++) {
      avgX += points[j].timestamp;
      avgY += points[j].pv ?? 0;
      count++;
    }
    avgX /= count;
    avgY /= count;

    // 在当前桶中选择与上一个选中点和下一桶平均值构成的三角形面积最大的点
    const pointA = points[a];
    let maxArea = -1;
    let nextA = rangeStart;
    const paX = pointA.timestamp;
    const paY = pointA.pv ?? 0;

    for (let j = rangeStart; j < rangeEnd; j++) {
      const area = Math.abs((paX - avgX) * (points[j].pv ?? 0 - paY) - (paX - points[j].timestamp) * (avgY - paY)) * 0.5;
      if (area > maxArea) {
        maxArea = area;
        nextA = j;
      }
    }
    sampled.push(points[nextA]);
    a = nextA;
  }
  sampled.push(points[points.length - 1]);
  return sampled;
}

/** 根据回路 ID 生成波形数据（含 PV 质量码分段） */
function generatePoints(loopId: string): TimeseriesPoint[] {
  const loop = findLoop(loopId);
  if (!loop) return [];

  const now = Date.now();
  const windowStart = now - WINDOW_DAYS * 24 * 3600 * 1000;
  // 30 天，每 10 分钟一个点 = 4320 个原始点，降采样到 2000
  const interval = 10 * 60 * 1000;
  const rawCount = Math.floor((now - windowStart) / interval);

  const points: TimeseriesPoint[] = [];
  const basePv = loop.pvValue || 50;
  const baseSp = loop.spValue || 50;
  const baseOp = loop.opValue || 50;

  // 根据回路特征确定波形模式
  const isOscillating = loopId === 'L003' || loopId === 'L001';
  const isSticky = loopId === 'L009';
  const isAggressive = loopId === 'L014';
  const hasBadPv = loop.pvQuality === 'Bad';
  const hasUncertainPv = loop.pvQuality === 'Uncertain';

  for (let i = 0; i < rawCount; i++) {
    const t = windowStart + i * interval;
    const dayProgress = (i / rawCount) * WINDOW_DAYS;

    // PV 质量码分段
    const isBadSegment = hasBadPv && i > rawCount * 0.7;
    const isUncertainSegment = hasUncertainPv && i > rawCount * 0.6 && i < rawCount * 0.8;
    const pvQuality: PVQuality = isBadSegment ? 'Bad' : isUncertainSegment ? 'Uncertain' : 'Good';

    // PV 值（按质量码与波形模式生成）
    const pv: number | null = isBadSegment ? null
      : isUncertainSegment ? basePv + (Math.random() - 0.5) * 8
      : isOscillating ? basePv + Math.sin(i * 0.3) * 3.5 + (Math.random() - 0.5) * 0.5
      : isSticky ? basePv + (Math.floor(i / 50) % 4) * 0.8 + (Math.random() - 0.5) * 0.3
      : isAggressive ? basePv + Math.sin(i * 0.15) * 5 * Math.exp(-i * 0.0005) + (Math.random() - 0.5) * 0.8
      : basePv + Math.sin(i * 0.05) * 0.8 + (Math.random() - 0.5) * 0.5;

    // SP：偶尔有设定值阶跃
    const sp: number | null = Math.floor(dayProgress) !== Math.floor((i + 1) / rawCount * WINDOW_DAYS)
      ? baseSp + (Math.random() - 0.5) * 2
      : baseSp;

    // OP：根据波形模式变化
    const op: number | null = isOscillating ? baseOp + Math.sin(i * 0.3 + 0.5) * 8
      : isSticky ? baseOp + Math.sin(i * 0.1) * 12 + (Math.random() - 0.5) * 2
      : isAggressive ? baseOp + Math.sin(i * 0.15 + 1) * 10 * Math.exp(-i * 0.0005)
      : baseOp + Math.sin(i * 0.03) * 3 + (Math.random() - 0.5) * 1;

    points.push({ timestamp: t, pv, sp, op, pvQuality });
  }

  return lttbDownsample(points, MAX_POINTS);
}

/** 缓存已生成的数据集 */
const datasetCache = new Map<string, TimeseriesDataset>();

/** 获取回路的波形数据集 */
export function getTimeseries(loopId: string): TimeseriesDataset {
  if (datasetCache.has(loopId)) return datasetCache.get(loopId)!;

  const loop = findLoop(loopId);
  const points = generatePoints(loopId);
  const dataset: TimeseriesDataset = {
    loopId,
    loopName: loop?.loopName ?? loopId,
    points,
    windowStart: points[0]?.timestamp ?? Date.now() - WINDOW_DAYS * 24 * 3600 * 1000,
    windowEnd: points[points.length - 1]?.timestamp ?? Date.now(),
    sampleCount: points.length,
  };
  datasetCache.set(loopId, dataset);
  return dataset;
}

/** 生成 PV-OP 散点数据（诊断详情用） */
export function getScatterData(loopId: string): ScatterPoint[] {
  const dataset = getTimeseries(loopId);
  return dataset.points
    .filter((p) => p.pv !== null && p.op !== null && p.pvQuality === 'Good')
    .map((p) => ({ pv: p.pv!, op: p.op!, timestamp: p.timestamp }))
    .slice(-500); // 最近 500 个点
}

/** 获取 A/B 对比数据（RESOLVED Tracker 用） */
export function getABComparison(loopId: string, baselineStart: string, baselineEnd: string) {
  const before = getTimeseries(loopId).points.slice(0, 1000);
  const after = getTimeseries(loopId).points.slice(1000);
  return {
    before: {
      label: '调整前',
      period: `${baselineStart} ~ ${baselineEnd}`,
      points: before,
      avgScore: 42,
    },
    after: {
      label: '调整后',
      period: '2026-06-19 00:00 ~ 2026-06-21 10:00',
      points: after,
      avgScore: 78,
    },
  };
}
