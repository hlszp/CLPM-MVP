/**
 * useLoopPalettes / 回路共享色板常量单元测试
 *
 * 覆盖：
 * - 7 类回路类型的 label / 分类主色 / Tag 浅色常量完整性
 * - MODE 0-4 色板与短标签完整性
 * - modeLabelColor：Auto/Manual/Cascade 映射 ZL 语义色，未知值回退 NEUTRAL
 */
import { ref } from 'vue';

import { describe, expect, it, vi } from 'vitest';

import {
  LOOP_TYPE_COLOR_MAP,
  LOOP_TYPE_LABEL_MAP,
  LOOP_TYPE_TAG_COLOR_MAP,
  MODE_COLOR_MAP,
  MODE_LABEL_MAP,
  useLoopPalettes,
} from '#/composables/use-loop-palettes';

// 固定浅色模式（ZL 浅色语义色板）
vi.mock('@vben/preferences', () => ({
  usePreferences: () => ({ isDark: ref(false) }),
}));

const LOOP_TYPE_KEYS = [
  'TEMPERATURE',
  'PRESSURE',
  'LEVEL',
  'FLOW',
  'ANALYSIS',
  'SPEED',
  'OTHER',
];

describe('loop 色板常量', () => {
  it('7 类回路类型的 label / 主色 / Tag 浅色一一对应且为合法 hex', () => {
    for (const key of LOOP_TYPE_KEYS) {
      expect(LOOP_TYPE_LABEL_MAP[key]).toBeTruthy();
      expect(LOOP_TYPE_COLOR_MAP[key]).toMatch(/^#[0-9a-f]{6}$/i);
      expect(LOOP_TYPE_TAG_COLOR_MAP[key]).toMatch(/^#[0-9a-f]{6}$/i);
    }
  });

  it('mODE 0-4 色板与短标签完整', () => {
    expect(Object.keys(MODE_COLOR_MAP)).toEqual(['0', '1', '2', '3', '4']);
    expect(Object.keys(MODE_LABEL_MAP)).toEqual(['0', '1', '2', '3', '4']);
    expect(MODE_LABEL_MAP['0']).toBe('MAN');
    expect(MODE_LABEL_MAP['1']).toBe('AUTO');
  });
});

describe('useLoopPalettes.modeLabelColor', () => {
  it('auto/Manual/Cascade 映射 ZL 语义色', () => {
    const { modeLabelColor } = useLoopPalettes();
    expect(modeLabelColor('Auto')).toBe('#10b981');
    expect(modeLabelColor('Manual')).toBe('#f59e0b');
    expect(modeLabelColor('Cascade')).toBe('#3b82f6');
  });

  it('未知/空 modeLabel 回退 NEUTRAL 中性灰', () => {
    const { modeLabelColor } = useLoopPalettes();
    expect(modeLabelColor('')).toBe('#64748b');
    expect(modeLabelColor(null)).toBe('#64748b');
    expect(modeLabelColor(undefined)).toBe('#64748b');
  });
});
