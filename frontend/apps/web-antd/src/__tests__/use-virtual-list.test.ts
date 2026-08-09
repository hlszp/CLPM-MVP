/**
 * useVirtualList 单元测试（整改 D4）
 *
 * 覆盖：
 * - 窗口计算：只渲染可视区 + overscan 缓冲，不渲染全量
 * - 滚动驱动：scrollTop 变化后窗口起点/偏移正确移动
 * - 边界：列表为空、滚动到底部时索引收敛不越界
 */
import { effectScope, ref } from 'vue';

import { describe, expect, it } from 'vitest';

import { useVirtualList } from '#/composables/use-virtual-list';

function setup(count: number, itemHeight = 10) {
  const scope = effectScope();
  let api!: ReturnType<typeof useVirtualList<number>>;
  scope.run(() => {
    api = useVirtualList({
      itemHeight,
      items: ref(Array.from({ length: count }, (_, i) => i)),
      overscan: 2,
    });
  });
  return { api, scope };
}

describe('useVirtualList', () => {
  it('初始窗口：仅渲染视口（默认 600px）+ overscan，而非全量', () => {
    const { api } = setup(1000, 10);
    // 600/10 = 60 行 + 上下各 2 行缓冲
    expect(api.visibleItems.value.length).toBe(62);
    expect(api.visibleItems.value[0]!.index).toBe(0);
    expect(api.totalHeight.value).toBe(10_000);
    expect(api.offsetY.value).toBe(0);
  });

  it('滚动后窗口平移：起点索引与 offsetY 正确', () => {
    const { api } = setup(1000, 10);
    api.onScroll({ target: { scrollTop: 500 } } as unknown as Event);
    const first = api.visibleItems.value[0]!;
    expect(first.index).toBe(48); // 500/10 - overscan 2
    expect(api.offsetY.value).toBe(480);
  });

  it('滚到底部：窗口收敛不越界', () => {
    const { api } = setup(100, 10);
    api.onScroll({ target: { scrollTop: 990 } } as unknown as Event);
    const items = api.visibleItems.value;
    expect(items.at(-1)!.index).toBe(99);
    expect(items.length).toBeLessThanOrEqual(100);
  });

  it('空列表：无渲染项、总高为 0', () => {
    const { api } = setup(0, 10);
    expect(api.visibleItems.value.length).toBe(0);
    expect(api.totalHeight.value).toBe(0);
  });
});
