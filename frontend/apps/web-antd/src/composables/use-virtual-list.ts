/**
 * useVirtualList —— 轻量定高虚拟滚动（整改 D4，无第三方依赖）
 *
 * 适用：行高固定的长列表（≥100 条启用，对齐 UI/UX 规范 §7.1 性能条款）。
 * 原理：容器滚动时只渲染可视窗口 + overscan 缓冲区，上下用总高占位。
 *
 * 用法：
 * ```vue
 * const { containerRef, onScroll, totalHeight, offsetY, visibleItems } =
 *   useVirtualList({ items: loopList, itemHeight: 57 });
 * <div :ref="(el) => (containerRef = el)" ... @scroll="onScroll">
 * ```
 * 注：containerRef 直接暴露为可写 ref，模板用函数 ref 赋值
 * （字符串 ref 会被 vue-tsc 判为未使用变量）。
 */
import type { Ref } from 'vue';

import { computed, onBeforeUnmount, onMounted, ref } from 'vue';

interface UseVirtualListOptions<T> {
  /** 完整数据列表 */
  items: Ref<T[]>;
  /** 固定行高（px） */
  itemHeight: number;
  /** 上下各多渲染的行数缓冲，默认 5 */
  overscan?: number;
}

export function useVirtualList<T>(options: UseVirtualListOptions<T>) {
  const overscan = options.overscan ?? 5;

  const containerRef = ref<HTMLElement | null>(null);
  const scrollTop = ref(0);
  const viewportHeight = ref(600);

  /** 可视区起始/结束索引（含 overscan） */
  const startIndex = computed(() =>
    Math.max(0, Math.floor(scrollTop.value / options.itemHeight) - overscan),
  );
  const endIndex = computed(() =>
    Math.min(
      options.items.value.length,
      Math.ceil((scrollTop.value + viewportHeight.value) / options.itemHeight) +
        overscan,
    ),
  );

  /** 当前渲染窗口（保留原索引，便于 key/操作映射） */
  const visibleItems = computed(() =>
    options.items.value
      .slice(startIndex.value, endIndex.value)
      .map((item, i) => ({ index: startIndex.value + i, item })),
  );

  /** 列表总高（占位） */
  const totalHeight = computed(
    () => options.items.value.length * options.itemHeight,
  );

  /** 渲染窗口的垂直偏移 */
  const offsetY = computed(() => startIndex.value * options.itemHeight);

  function onScroll(event: Event) {
    scrollTop.value = (event.target as HTMLElement).scrollTop;
  }

  /** 视口高度跟踪（容器挂载/尺寸变化时更新；jsdom 无 ResizeObserver 时跳过） */
  let observer: null | ResizeObserver = null;
  onMounted(() => {
    if (containerRef.value) {
      viewportHeight.value = containerRef.value.clientHeight || 600;
      if (typeof ResizeObserver === 'undefined') return;
      observer = new ResizeObserver((entries) => {
        const h = entries[0]?.contentRect.height;
        if (h) viewportHeight.value = h;
      });
      observer.observe(containerRef.value);
    }
  });
  onBeforeUnmount(() => {
    observer?.disconnect();
    observer = null;
  });

  /** 滚动到指定索引（如选中项定位） */
  function scrollToIndex(index: number) {
    containerRef.value?.scrollTo({ top: index * options.itemHeight });
  }

  return {
    containerRef,
    offsetY,
    onScroll,
    scrollToIndex,
    totalHeight,
    visibleItems,
  };
}
