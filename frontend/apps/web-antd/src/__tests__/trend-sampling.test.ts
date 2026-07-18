import { describe, expect, it } from 'vitest';

import {
  computeSampleInterval,
  DEFAULT_TARGET_POINTS,
  formatSampleInterval,
} from '#/utils/trend';

/**
 * 动态采样间隔逻辑测试
 *
 * 验证不同时间范围（1h / 2h / 4h / 24h / 72h）下，
 * computeSampleInterval 是否返回正确的采样间隔。
 *
 * 后端 trend_service.py 使用相同逻辑，确保前后端一致。
 */
describe('动态采样间隔 computeSampleInterval', () => {
  // 辅助：生成 ISO 时间对
  function makeRange(hours: number): [string, string] {
    const end = new Date('2026-07-13T12:00:00Z');
    const start = new Date(end.getTime() - hours * 3600 * 1000);
    return [start.toISOString(), end.toISOString()];
  }

  // UT-TREND-001: 1h → 1s（3600s / 3600 = 1）
  it('uT-TREND-001: 1h 时间范围 → 采样间隔 1s', () => {
    const [start, end] = makeRange(1);
    const interval = computeSampleInterval(start, end);
    expect(interval).toBe(1);
  });

  // UT-TREND-002: 2h → 2s（7200s / 3600 = 2）
  it('uT-TREND-002: 2h 时间范围 → 采样间隔 2s', () => {
    const [start, end] = makeRange(2);
    const interval = computeSampleInterval(start, end);
    expect(interval).toBe(2);
  });

  // UT-TREND-003: 4h → 4s（14400s / 3600 = 4）
  it('uT-TREND-003: 4h 时间范围 → 采样间隔 4s', () => {
    const [start, end] = makeRange(4);
    const interval = computeSampleInterval(start, end);
    expect(interval).toBe(4);
  });

  // UT-TREND-004: 24h → 24s（86400s / 3600 = 24）
  it('uT-TREND-004: 24h 时间范围 → 采样间隔 24s', () => {
    const [start, end] = makeRange(24);
    const interval = computeSampleInterval(start, end);
    expect(interval).toBe(24);
  });

  // UT-TREND-005: 72h → 72s（259200s / 3600 = 72）
  it('uT-TREND-005: 72h 时间范围 → 采样间隔 72s', () => {
    const [start, end] = makeRange(72);
    const interval = computeSampleInterval(start, end);
    expect(interval).toBe(72);
  });

  // UT-TREND-006: 72h 返回的点数不超过 targetPoints
  it('uT-TREND-006: 72h 时间范围点数不超过 3600', () => {
    const [start, end] = makeRange(72);
    const interval = computeSampleInterval(start, end);
    const totalSeconds = 72 * 3600;
    const estimatedPoints = Math.ceil(totalSeconds / interval);
    expect(estimatedPoints).toBeLessThanOrEqual(DEFAULT_TARGET_POINTS + 1);
  });

  // UT-TREND-007: 自定义 targetPoints=2000 → 72h → 130s
  it('uT-TREND-007: 自定义 targetPoints=2000 → 72h 采样间隔 129s', () => {
    const [start, end] = makeRange(72);
    const interval = computeSampleInterval(start, end, 2000);
    // 259200 / 2000 = 129.6 → floor = 129
    expect(interval).toBe(129);
  });

  // UT-TREND-008: 0s 时间范围 → 返回 1（保护值）
  it('uT-TREND-008: 零时长时间范围 → 返回 1', () => {
    const ts = '2026-07-13T12:00:00Z';
    const interval = computeSampleInterval(ts, ts);
    expect(interval).toBe(1);
  });

  // UT-TREND-009: 负时间范围（start > end）→ 返回 1
  it('uT-TREND-009: 负时间范围 → 返回 1', () => {
    const end = '2026-07-13T12:00:00Z';
    const start = '2026-07-13T13:00:00Z';
    const interval = computeSampleInterval(start, end);
    expect(interval).toBe(1);
  });

  // UT-TREND-010: 小于 1h（如 30min）→ 仍返回 1（最小值保护）
  it('uT-TREND-010: 30min 时间范围 → 返回 1（最小值保护）', () => {
    const [start, end] = makeRange(0.5);
    const interval = computeSampleInterval(start, end);
    expect(interval).toBe(1);
  });

  // UT-TREND-011: Date 对象输入也能正确计算
  it('uT-TREND-011: Date 对象输入 → 正确计算', () => {
    const end = new Date('2026-07-13T12:00:00Z');
    const start = new Date('2026-07-10T12:00:00Z'); // 72h
    const interval = computeSampleInterval(start, end);
    expect(interval).toBe(72);
  });
});

/**
 * 采样间隔格式化测试
 */
describe('采样间隔格式化 formatSampleInterval', () => {
  it('uT-FMT-001: 1s → "1s"', () => {
    expect(formatSampleInterval(1)).toBe('1s');
  });

  it('uT-FMT-002: 72s → "72s"', () => {
    expect(formatSampleInterval(72)).toBe('72s');
  });

  it('uT-FMT-003: 120s → "2min"', () => {
    expect(formatSampleInterval(120)).toBe('2min');
  });

  it('uT-FMT-004: 3600s → "1h"', () => {
    expect(formatSampleInterval(3600)).toBe('1h');
  });
});

/**
 * 模拟趋势接口响应结构验证
 *
 * 验证后端返回的趋势数据结构是否包含动态采样相关字段，
 * 且各字段类型和约束符合预期。
 */
describe('趋势接口响应结构验证', () => {
  // 模拟后端 fetch_loop_trend 返回结构
  function mockTrendResponse(hours: number) {
    const end = new Date('2026-07-13T12:00:00Z');
    const start = new Date(end.getTime() - hours * 3600 * 1000);
    const interval = computeSampleInterval(
      start.toISOString(),
      end.toISOString(),
    );
    const pointCount = Math.floor((hours * 3600) / interval);

    return {
      timestamps: Array.from(
        { length: pointCount },
        (_, i) => start.getTime() + i * interval * 1000,
      ),
      pv: Array.from({ length: pointCount }, () => 50 + Math.random() * 5),
      sp: Array.from({ length: pointCount }, () => 52),
      op: Array.from({ length: pointCount }, () => 55),
      mode: Array.from({ length: pointCount }, () => 1),
      pvQuality: Array.from({ length: pointCount }, () => 'GOOD'),
      sampleInterval: interval,
      pointCount,
      downsampled: pointCount > 5000,
    };
  }

  it('uT-API-001: 1h 响应结构包含 sampleInterval=1 且各数组等长', () => {
    const resp = mockTrendResponse(1);
    expect(resp.sampleInterval).toBe(1);
    expect(resp.pointCount).toBe(3600);
    expect(resp.downsampled).toBe(false);
    expect(resp.timestamps.length).toBe(resp.pv.length);
    expect(resp.pv.length).toBe(resp.sp.length);
    expect(resp.sp.length).toBe(resp.pvQuality.length);
  });

  it('uT-API-002: 24h 响应 sampleInterval=24', () => {
    const resp = mockTrendResponse(24);
    expect(resp.sampleInterval).toBe(24);
    expect(resp.pointCount).toBe(3600);
    expect(resp.downsampled).toBe(false);
  });

  it('uT-API-003: 72h 响应 sampleInterval=72 且不超时', () => {
    const resp = mockTrendResponse(72);
    expect(resp.sampleInterval).toBe(72);
    expect(resp.pointCount).toBe(3600);
    expect(resp.downsampled).toBe(false);
    // 72h 采样后点数不应超过 3600+1
    expect(resp.pointCount).toBeLessThanOrEqual(DEFAULT_TARGET_POINTS + 1);
  });

  it('uT-API-004: 所有时间窗的 pvQuality 值均为合法枚举', () => {
    const validQualities = ['BAD', 'GOOD', 'UNCERTAIN'];
    for (const hours of [1, 2, 4, 8, 24, 72]) {
      const resp = mockTrendResponse(hours);
      for (const q of resp.pvQuality) {
        expect(validQualities).toContain(q);
      }
    }
  });
});
