/**
 * 区级延迟加载（MW-P3-10）
 *
 * 基于 IntersectionObserver 追踪页面区块是否进入视口，实现
 * "评估趋势、诊断波形/FFT、整定仿真在可见时加载"的延迟加载策略。
 *
 * 设计要点：
 * - `onceVisible` 语义：区块首次进入视口后标记为已可见，即使滚出视口也不再重置
 *   （数据已加载，无需重复触发）；切换回路时由调用方通过 `reset()` 清除标记
 * - `shouldLoad` 计算属性：`onceVisible && hasLoopId`——模板/逻辑判断是否应加载数据
 * - 支持自定义 rootMargin（默认 200px 预加载缓冲）和 threshold
 * - SSR 安全：无 window 时返回无操作桩
 * - 自动清理：onBeforeUnmount 断开 observer
 *
 * 对齐整改方案 §9.3 区级延迟加载。
 */
import { onBeforeUnmount, ref, type Ref } from 'vue';

export interface SectionVisibilityOptions {
  /** 预加载缓冲距离（px），区块进入视口前 rootMargin 距离即触发 */
  rootMargin?: string;
  /** 交叉比例阈值 */
  threshold?: number;
}

export interface UseSectionVisibilityReturn {
  /** 区块当前是否在视口内 */
  isVisible: Ref<boolean>;
  /** 区块是否曾进入过视口（首次后保持 true，直到 reset） */
  onceVisible: Ref<boolean>;
  /** 是否应该加载该区块数据（onceVisible 且有有效 loopId） */
  shouldLoad: (loopId: null | string | undefined) => boolean;
  /** 注册区块 DOM 元素（模板 ref 回调） */
  register: (el: Element | null) => void;
  /** 重置可见标记（切换回路时调用） */
  reset: () => void;
}

/**
 * 区级可见性追踪。
 *
 * @example
 * ```ts
 * const assessmentVisibility = useSectionVisibility();
 * // 模板：
 * // <div :ref="assessmentVisibility.register">...</div>
 * // 逻辑：
 * // watch([() => selectedLoopId.value, assessmentVisibility.onceVisible], ([id, vis]) => {
 * //   if (id && vis) loadAssessment(id);
 * // });
 * ```
 */
export function useSectionVisibility(
  options: SectionVisibilityOptions = {},
): UseSectionVisibilityReturn {
  const {
    rootMargin = '200px',
    threshold = 0,
  } = options;

  const isVisible = ref(false);
  const onceVisible = ref(false);

  let observer: IntersectionObserver | null = null;
  let targetEl: Element | null = null;

  function isSupported(): boolean {
    return (
      typeof window !== 'undefined' && 'IntersectionObserver' in window
    );
  }

  function ensureObserver(): void {
    if (!isSupported() || observer) return;
    observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          const visible = entry.isIntersecting;
          isVisible.value = visible;
          if (visible && !onceVisible.value) {
            onceVisible.value = true;
          }
        }
      },
      { rootMargin, threshold },
    );
  }

  function register(el: Element | null): void {
    if (!isSupported()) {
      // 无 IntersectionObserver 支持（如 SSR 或极老浏览器）——直接标记可见
      onceVisible.value = true;
      isVisible.value = true;
      return;
    }
    // 切换目标元素：先 unobserve 旧的
    if (targetEl && observer) {
      observer.unobserve(targetEl);
    }
    targetEl = el;
    if (!el) return;
    ensureObserver();
    observer?.observe(el);
  }

  function shouldLoad(loopId: null | string | undefined): boolean {
    return onceVisible.value && !!loopId;
  }

  function reset(): void {
    onceVisible.value = false;
    isVisible.value = false;
  }

  onBeforeUnmount(() => {
    if (observer) {
      observer.disconnect();
      observer = null;
    }
    targetEl = null;
  });

  return {
    isVisible,
    onceVisible,
    shouldLoad,
    register,
    reset,
  };
}
