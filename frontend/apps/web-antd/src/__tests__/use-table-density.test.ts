/**
 * useTableDensity 单元测试（整改 A-07）
 *
 * 覆盖：默认紧凑、循环切换顺序、antd size 映射、跨实例持久化
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { useTableDensity } from '#/composables/use-table-density';

describe('useTableDensity', () => {
  beforeEach(() => {
    // 测试环境 localStorage 实现不完整（无 clear/removeItem），
    // 用内存 stub 提供完整 Storage 语义并与环境解耦
    const store = new Map<string, string>();
    vi.stubGlobal('localStorage', {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => {
        store.set(k, v);
      },
      removeItem: (k: string) => {
        store.delete(k);
      },
      clear: () => {
        store.clear();
      },
    });
  });

  it('默认紧凑（工业高密度场景）', () => {
    const { density, tableSize, densityLabel } = useTableDensity('page-a');
    expect(density.value).toBe('compact');
    expect(tableSize.value).toBe('small');
    expect(densityLabel.value).toBe('紧凑');
  });

  it('循环切换：紧凑 → 标准 → 宽松 → 紧凑', () => {
    const { density, tableSize, cycleDensity } = useTableDensity('page-b');
    cycleDensity();
    expect(density.value).toBe('default');
    expect(tableSize.value).toBe('middle');
    cycleDensity();
    expect(density.value).toBe('relaxed');
    expect(tableSize.value).toBe('large');
    cycleDensity();
    expect(density.value).toBe('compact');
  });

  it('密度按 pageKey 持久化，新实例读取同档', async () => {
    const { nextTick } = await import('vue');
    const a = useTableDensity('page-c');
    a.cycleDensity(); // → default
    await nextTick(); // 等待 watch 持久化写入 localStorage
    const b = useTableDensity('page-c');
    expect(b.density.value).toBe('default');
    // 不同 pageKey 互不影响
    const c = useTableDensity('page-d');
    expect(c.density.value).toBe('compact');
  });
});
