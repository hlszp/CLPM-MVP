import { describe, expect, it } from 'vitest';

import { flattenNodes, formatTime } from '#/utils/format';

describe('工具函数测试', () => {
  // UT-UTIL-001: formatTime-ISO字符串
  it('uT-UTIL-001: formatTime 正确格式化 ISO 字符串', () => {
    const iso = '2024-06-15T08:30:00Z';
    const result = formatTime(iso);
    // 应返回非空字符串，且不等于占位符
    expect(result).not.toBe('—');
    expect(result).toBeTruthy();
    // 应包含年份 2024
    expect(result).toContain('2024');
  });

  // UT-UTIL-002: formatTime-空值返回 "—"
  it('uT-UTIL-002: formatTime 空值返回 "—"', () => {
    expect(formatTime('')).toBe('—');
    expect(formatTime(null)).toBe('—');
    expect(formatTime(undefined)).toBe('—');
  });

  // UT-UTIL-005: flattenNodes-树扁平化
  it('uT-UTIL-005: flattenNodes 正确扁平化树形结构', () => {
    const tree = [
      {
        id: '1',
        name: '工厂',
        children: [
          { id: '1-1', name: '装置A', children: [] },
          {
            id: '1-2',
            name: '装置B',
            children: [{ id: '1-2-1', name: '单元B1' }],
          },
        ],
      },
      { id: '2', name: '工厂2' },
    ];
    const result = flattenNodes(tree);
    expect(result).toHaveLength(5);
    expect(result.map((n) => n.id)).toEqual(['1', '1-1', '1-2', '1-2-1', '2']);
  });

});
