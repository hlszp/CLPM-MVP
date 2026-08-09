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

describe('useVirtualList（MW-P0-01 工作台行高 76px）', () => {
  it('76px 行高：总高度 = items.length × 76', () => {
    const scope = effectScope();
    let api!: ReturnType<typeof useVirtualList<number>>;
    scope.run(() => {
      api = useVirtualList({
        itemHeight: 76,
        items: ref(Array.from({ length: 100 }, (_, i) => i)),
        overscan: 5,
      });
    });
    expect(api.totalHeight.value).toBe(7600);
    scope.stop();
  });

  it('76px 行高：滚动到末尾，最后一项完整可达', () => {
    const scope = effectScope();
    let api!: ReturnType<typeof useVirtualList<number>>;
    scope.run(() => {
      api = useVirtualList({
        itemHeight: 76,
        items: ref(Array.from({ length: 100 }, (_, i) => i)),
        overscan: 5,
      });
    });
    // 滚动到最后一项
    api.onScroll({ target: { scrollTop: 76 * 99 } } as unknown as Event);
    const items = api.visibleItems.value;
    expect(items.at(-1)!.index).toBe(99);
    // 最后一项的偏移应在总高度内
    expect(api.offsetY.value).toBeLessThanOrEqual(76 * 99);
    scope.stop();
  });

  it('76px 行高：可视起止索引正确（视口 600px）', () => {
    const scope = effectScope();
    let api!: ReturnType<typeof useVirtualList<number>>;
    scope.run(() => {
      api = useVirtualList({
        itemHeight: 76,
        items: ref(Array.from({ length: 100 }, (_, i) => i)),
        overscan: 5,
      });
    });
    // 初始视口 600px：600/76 ≈ 7.9 → 8 行 + 上下各 5 缓冲
    const first = api.visibleItems.value[0]!;
    const last = api.visibleItems.value.at(-1)!;
    expect(first.index).toBe(0);
    // endIndex = ceil((0+600)/76) + 5 = 13；slice(0,13) → 索引 0..12
    expect(last.index).toBe(12);
    expect(api.totalHeight.value).toBe(7600);
    scope.stop();
  });
});
