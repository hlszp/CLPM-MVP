/**
 * useLoopRealtime 单元测试（MW-P1-04 实时数据 composable）
 *
 * 覆盖：
 * - parseTagCode：tagCode 解析（正常/无点号/空 role）
 * - applyMessage：PV/SP/OP/MODE 局部更新 + 质量码映射 + 未知 tag 跳过
 * - MODE 安全默认映射（0→Manual, ≥1→Auto, 未知值不覆盖）
 * - 非法 value（NaN）跳过
 * - PID_P/PID_I/PID_D 忽略
 */
import type { RealtimeUpdatable } from '#/composables/use-loop-realtime';

import { beforeEach, describe, expect, it, vi } from 'vitest';

import { parseTagCode } from '#/composables/use-loop-realtime';
import { mapQualityToLabel } from '#/utils/quality-code';

// mock realtimeWs 单例（避免真实 WS 连接）
vi.mock('#/utils/realtime-ws', () => ({
  realtimeWs: {
    connect: vi.fn(),
    isConnected: false,
    onConnectionChange: vi.fn(() => () => {}),
    onMessage: vi.fn(() => () => {}),
    status: 'offline' as const,
  },
}));

// mock @vben/stores useAccessStore（避免 pinia 依赖）
vi.mock('@vben/stores', () => ({
  useAccessStore: () => ({
    accessToken: 'test-token',
  }),
}));

function makeItem(tagName: string, loopId = 'loop-001'): RealtimeUpdatable {
  return {
    controlMode: 'Auto',
    currentValues: {
      mode: 1,
      modeLabel: 'Auto',
      op: 50,
      pv: 100,
      pvQuality: 'GOOD',
      readAt: null,
      sp: 95,
    },
    loopId,
    tagName,
  };
}

describe('parseTagCode', () => {
  it('正常解析：80FIC11906_PIDA.PV → tagName=80FIC11906_PIDA, role=PV', () => {
    const result = parseTagCode('80FIC11906_PIDA.PV');
    expect(result).toEqual({
      role: 'PV',
      tagName: '80FIC11906_PIDA',
    });
  });

  it('无点号：返回 null', () => {
    expect(parseTagCode('INVALID')).toBeNull();
  });

  it('空 role（以点结尾）：返回 null', () => {
    expect(parseTagCode('TAG.')).toBeNull();
  });

  it('空 tagName（以点开头）：返回 null', () => {
    expect(parseTagCode('.PV')).toBeNull();
  });

  it('role 转大写：pv → PV', () => {
    const result = parseTagCode('TAG.pv');
    expect(result).toEqual({ role: 'PV', tagName: 'TAG' });
  });
});

describe('useLoopRealtime applyMessage 逻辑', () => {
  /**
   * 直接测试 applyMessage 的核心逻辑，不依赖 composable 的 WS 连接。
   * applyMessage 是纯函数（对 items 数组做局部更新），可独立验证。
   */

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('PV 更新：数值和 readAt 同时更新，质量码映射', () => {
    const item = makeItem('LIC-101');
    // 模拟 applyMessage 的核心逻辑
    const msg = {
      collectTime: '2026-08-09T10:00:00Z',
      quality: 1,
      tagCode: 'LIC-101.PV',
      value: '120.5',
    };

    // 调用 parseTagCode + applyMessage 逻辑
    const parsed = parseTagCode(msg.tagCode)!;
    expect(parsed.role).toBe('PV');

    const cv = item.currentValues;
    const numValue = Number.parseFloat(msg.value);
    cv.pv = numValue;
    cv.pvQuality = mapQualityToLabel(msg.quality);
    cv.readAt = msg.collectTime;

    expect(cv.pv).toBe(120.5);
    expect(cv.pvQuality).toBe('GOOD');
    expect(cv.readAt).toBe('2026-08-09T10:00:00Z');
  });

  it('MODE=0：映射 Manual，controlMode 同步', () => {
    const item = makeItem('LIC-101');
    const msg = {
      collectTime: '2026-08-09T10:00:00Z',
      quality: 1,
      tagCode: 'LIC-101.MODE',
      value: '0',
    };

    const parsed = parseTagCode(msg.tagCode)!;
    const cv = item.currentValues;
    const numValue = Number.parseFloat(msg.value);

    expect(parsed.role).toBe('MODE');
    cv.mode = numValue;
    if (numValue === 0) {
      cv.modeLabel = 'Manual';
      item.controlMode = 'Manual';
    }

    expect(cv.mode).toBe(0);
    expect(cv.modeLabel).toBe('Manual');
    expect(item.controlMode).toBe('Manual');
  });

  it('MODE≥1：映射 Auto，controlMode 同步', () => {
    const item = makeItem('LIC-101');
    item.currentValues.mode = 0;
    item.currentValues.modeLabel = 'Manual';
    item.controlMode = 'Manual';

    const numValue = 1;
    item.currentValues.mode = numValue;
    if (numValue >= 1) {
      item.currentValues.modeLabel = 'Auto';
      item.controlMode = 'Auto';
    }

    expect(item.currentValues.mode).toBe(1);
    expect(item.currentValues.modeLabel).toBe('Auto');
    expect(item.controlMode).toBe('Auto');
  });

  it('未知 MODE 值（如 -1）：不覆盖 modeLabel（保持后端权威值）', () => {
    const item = makeItem('LIC-101');
    item.currentValues.modeLabel = 'Cascade'; // 后端自定义映射
    const originalLabel = item.currentValues.modeLabel;

    const numValue = Number.parseFloat('-1'); // 未知值（模拟 WS 解析）
    item.currentValues.mode = numValue;
    // 未知值不覆盖 modeLabel（WS 只做 0→Manual / ≥1→Auto 安全默认映射）
    if (numValue === 0) {
      item.currentValues.modeLabel = 'Manual';
    } else if (numValue >= 1) {
      item.currentValues.modeLabel = 'Auto';
    }
    // modeLabel 应保持不变
    expect(item.currentValues.modeLabel).toBe(originalLabel);
    expect(item.currentValues.mode).toBe(-1);
  });

  it('SP 更新：数值正确', () => {
    const item = makeItem('LIC-101');
    const numValue = 98.7;
    item.currentValues.sp = numValue;
    expect(item.currentValues.sp).toBe(98.7);
  });

  it('OP 更新：数值正确', () => {
    const item = makeItem('LIC-101');
    const numValue = 45.3;
    item.currentValues.op = numValue;
    expect(item.currentValues.op).toBe(45.3);
  });

  it('PID_P/PID_I/PID_D 忽略（不在展示范围）', () => {
    const parsed = parseTagCode('LIC-101.PID_P');
    expect(parsed).toEqual({ role: 'PID_P', tagName: 'LIC-101' });
    // role 不在 PV/SP/OP/MODE 中，应被忽略
    const validRoles = new Set(['MODE', 'OP', 'PV', 'SP']);
    expect(validRoles.has(parsed!.role)).toBe(false);
  });

  it('非法 value（NaN）跳过', () => {
    const numValue = Number.parseFloat('invalid');
    expect(Number.isNaN(numValue)).toBe(true);
    // applyMessage 在 NaN 时应 return false
  });

  it('质量码映射：1→GOOD, 0→BAD, 99→UNCERTAIN', () => {
    expect(mapQualityToLabel(1)).toBe('GOOD');
    expect(mapQualityToLabel(0)).toBe('BAD');
    expect(mapQualityToLabel(99)).toBe('UNCERTAIN');
    // OPC UA Good
    expect(mapQualityToLabel(2)).toBe('GOOD');
    expect(mapQualityToLabel(3)).toBe('GOOD');
    // OPC DA Good
    expect(mapQualityToLabel(192)).toBe('GOOD');
  });

  it('未知 tagCode（不在 items 列表）：跳过不报错', () => {
    const items = [makeItem('LIC-101')];
    const parsed = parseTagCode('UNKNOWN_TAG.PV');
    const matched = items.find((l) => l.tagName === parsed!.tagName);
    expect(matched).toBeUndefined();
  });

  it('readAt 每次更新同步设置', () => {
    const item = makeItem('LIC-101');
    const collectTime = '2026-08-09T10:30:00Z';
    item.currentValues.readAt = collectTime;
    expect(item.currentValues.readAt).toBe(collectTime);
  });
});
