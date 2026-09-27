import { describe, expect, it } from 'vitest';

import { CATEGORY_META, normalizeCategory } from '../constants';

describe('normalizeCategory（F4 点击链路修复的单一事实源）', () => {
  it('已是 8 类英文代码时原样返回', () => {
    expect(normalizeCategory('INSTRUMENT')).toBe('INSTRUMENT');
    expect(normalizeCategory('DATA_INSUFFICIENT')).toBe('DATA_INSUFFICIENT');
  });

  it('MV 返回的中文标签归一为代码（本次缺陷根因）', () => {
    expect(normalizeCategory('仪表/测量问题')).toBe('INSTRUMENT');
    expect(normalizeCategory('阀门/执行机构问题')).toBe('VALVE');
    expect(normalizeCategory('参数问题')).toBe('TUNING');
  });

  it('覆盖「数据不足/无法判定」与「数据不足」两个变体', () => {
    expect(normalizeCategory('数据不足/无法判定')).toBe('DATA_INSUFFICIENT');
    expect(normalizeCategory('数据不足')).toBe('DATA_INSUFFICIENT');
  });

  it('8 类 label 全部可归一（与 CATEGORY_META 同源，label 改名不影响逻辑）', () => {
    for (const [code, meta] of Object.entries(CATEGORY_META)) {
      expect(normalizeCategory(meta.label)).toBe(code);
    }
  });

  it('脏数据/空值返回 null（守卫不误开抽屉）', () => {
    expect(normalizeCategory('')).toBeNull();
    expect(normalizeCategory('未知分类')).toBeNull();
    expect(normalizeCategory(undefined as unknown as string)).toBeNull();
  });
});
