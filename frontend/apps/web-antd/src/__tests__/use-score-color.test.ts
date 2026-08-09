/**
 * useScoreColor 单元测试
 *
 * 覆盖：
 * - null/undefined/NaN 评分 → 中性灰 NEUTRAL（严禁红色）
 * - 动态阈值（算法配置）优先，档位颜色取阈值项自带 color
 * - 未传/空阈值时降级 GB/T 默认阈值
 * - 低于所有档位 minScore 时取最低档
 */
import { ref } from 'vue';

import { describe, expect, it, vi } from 'vitest';

import { useScoreColor } from '#/composables/use-score-color';

// 固定浅色模式（NEUTRAL = slate-500 #6c757d）
vi.mock('@vben/preferences', () => ({
  usePreferences: () => ({ isDark: ref(false) }),
}));

const NEUTRAL = '#6c757d';

describe('useScoreColor', () => {
  it('null/undefined/NaN 评分返回中性灰 NEUTRAL，level/label 为 null', () => {
    for (const score of [null, undefined, Number.NaN]) {
      const { color, label, level } = useScoreColor(score);
      expect(color.value).toBe(NEUTRAL);
      expect(color.value).not.toBe('#dc3545'); // 严禁映射为 DANGER 红
      expect(level.value).toBeNull();
      expect(label.value).toBeNull();
    }
  });

  it('未传阈值时降级 GB/T 默认阈值（95 → 优秀 SUCCESS）', () => {
    const { color, label, level } = useScoreColor(95);
    expect(level.value).toBe('1');
    expect(label.value).toBe('优秀');
    expect(color.value).toBe('#198754'); // SUCCESS（浅色板）
  });

  it('动态阈值优先：按配置的 minScore 与 color 命中档位', () => {
    const thresholds = ref([
      {
        level: 1,
        name: 'EXCELLENT',
        label: '优',
        minScore: 70,
        maxScore: 100,
        color: '#00ff00',
      },
      {
        level: 2,
        name: 'POOR',
        label: '差',
        minScore: 0,
        maxScore: 70,
        color: '#0000ff',
      },
    ]);
    const { color, label, level } = useScoreColor(75, thresholds);
    expect(level.value).toBe('1');
    expect(label.value).toBe('优');
    expect(color.value).toBe('#00ff00');
  });

  it('动态阈值响应式更新后颜色随之变化', () => {
    const thresholds = ref([
      {
        level: 1,
        name: 'HIGH',
        label: '高',
        minScore: 90,
        maxScore: 100,
        color: '#111111',
      },
      {
        level: 2,
        name: 'LOW',
        label: '低',
        minScore: 0,
        maxScore: 90,
        color: '#222222',
      },
    ]);
    const { color, label } = useScoreColor(85, thresholds);
    expect(label.value).toBe('低');
    expect(color.value).toBe('#222222');

    // 管理员调整阈值后：85 分落入"高"档
    thresholds.value = [
      {
        level: 1,
        name: 'HIGH',
        label: '高',
        minScore: 80,
        maxScore: 100,
        color: '#111111',
      },
      {
        level: 2,
        name: 'LOW',
        label: '低',
        minScore: 0,
        maxScore: 80,
        color: '#222222',
      },
    ];
    expect(label.value).toBe('高');
    expect(color.value).toBe('#111111');
  });

  it('低于所有档位 minScore 时取最低档', () => {
    const thresholds = [
      { level: 1, name: 'HIGH', minScore: 50, maxScore: 100, color: '#111111' },
      { level: 2, name: 'LOW', minScore: 10, maxScore: 50, color: '#222222' },
    ];
    const { color, level } = useScoreColor(5, thresholds);
    expect(level.value).toBe('2');
    expect(color.value).toBe('#222222');
  });

  it('阈值项未配置 color 时按档位降级 ZL 语义色', () => {
    const thresholds = [
      { level: 1, name: 'HIGH', minScore: 50, maxScore: 100 },
      { level: 5, name: 'LOW', minScore: 0, maxScore: 50 },
    ];
    expect(useScoreColor(80, thresholds).color.value).toBe('#198754'); // SUCCESS
    expect(useScoreColor(20, thresholds).color.value).toBe('#dc3545'); // DANGER
  });

  it('空阈值数组降级默认阈值', () => {
    const { color, level } = useScoreColor(30, []);
    expect(level.value).toBe('5');
    expect(color.value).toBe('#dc3545'); // DANGER（浅色板）
  });
});
