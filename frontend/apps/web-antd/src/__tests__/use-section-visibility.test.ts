/**
 * useSectionVisibility 单元测试（MW-P3-10 区级延迟加载）
 *
 * 覆盖：
 * - 初始状态：onceVisible=false，isVisible=false
 * - shouldLoad：onceVisible=false 时不加载
 * - register + IntersectionObserver 回调：首次可见后 onceVisible=true 且不再回退
 * - reset：清除 onceVisible 标记
 * - 无 IntersectionObserver 支持：直接标记可见
 */
import { effectScope } from 'vue';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useSectionVisibility } from '#/composables/use-section-visibility';

// mock IntersectionObserver
interface MockObserver {
  observe: ReturnType<typeof vi.fn>;
  unobserve: ReturnType<typeof vi.fn>;
  disconnect: ReturnType<typeof vi.fn>;
  callback: IntersectionObserverCallback;
}

let mockObserver: MockObserver | null = null;

class MockIntersectionObserver {
  callback: IntersectionObserverCallback;
  disconnect = vi.fn();
  observe = vi.fn();
  unobserve = vi.fn();

  constructor(cb: IntersectionObserverCallback) {
    this.callback = cb;
    mockObserver = this as unknown as MockObserver;
  }
}

function setup() {
  const scope = effectScope();
  let api!: ReturnType<typeof useSectionVisibility>;
  scope.run(() => {
    api = useSectionVisibility();
  });
  return { api, scope };
}

describe('useSectionVisibility', () => {
  beforeEach(() => {
    mockObserver = null;
    (globalThis as any).IntersectionObserver = MockIntersectionObserver;
  });

  afterEach(() => {
    (globalThis as any).IntersectionObserver = undefined;
    mockObserver = null;
  });

  it('初始状态：onceVisible=false，isVisible=false', () => {
    const { api, scope } = setup();
    expect(api.onceVisible.value).toBe(false);
    expect(api.isVisible.value).toBe(false);
    scope.stop();
  });

  it('shouldLoad：onceVisible=false 时不加载', () => {
    const { api, scope } = setup();
    expect(api.shouldLoad('loop-1')).toBe(false);
    scope.stop();
  });

  it('shouldLoad：onceVisible=false 且无 loopId 时不加载', () => {
    const { api, scope } = setup();
    expect(api.shouldLoad(null)).toBe(false);
    expect(api.shouldLoad(undefined)).toBe(false);
    scope.stop();
  });

  it('register 后触发可见回调：onceVisible=true 且不再回退', () => {
    const { api, scope } = setup();
    const el = document.createElement('div');
    api.register(el);

    expect(mockObserver).not.toBeNull();
    expect(mockObserver!.observe).toHaveBeenCalledWith(el);

    // 模拟进入视口
    mockObserver!.callback(
      [
        {
          isIntersecting: true,
          target: el,
        } as unknown as IntersectionObserverEntry,
      ],
      mockObserver as unknown as IntersectionObserver,
    );
    expect(api.isVisible.value).toBe(true);
    expect(api.onceVisible.value).toBe(true);

    // 模拟滚出视口——onceVisible 保持 true
    mockObserver!.callback(
      [
        {
          isIntersecting: false,
          target: el,
        } as unknown as IntersectionObserverEntry,
      ],
      mockObserver as unknown as IntersectionObserver,
    );
    expect(api.isVisible.value).toBe(false);
    expect(api.onceVisible.value).toBe(true);

    scope.stop();
  });

  it('onceVisible=true 后 shouldLoad 返回 true（有 loopId）', () => {
    const { api, scope } = setup();
    const el = document.createElement('div');
    api.register(el);
    mockObserver!.callback(
      [
        {
          isIntersecting: true,
          target: el,
        } as unknown as IntersectionObserverEntry,
      ],
      mockObserver as unknown as IntersectionObserver,
    );
    expect(api.shouldLoad('loop-1')).toBe(true);
    scope.stop();
  });

  it('onceVisible=true 但无 loopId 时 shouldLoad 返回 false', () => {
    const { api, scope } = setup();
    const el = document.createElement('div');
    api.register(el);
    mockObserver!.callback(
      [
        {
          isIntersecting: true,
          target: el,
        } as unknown as IntersectionObserverEntry,
      ],
      mockObserver as unknown as IntersectionObserver,
    );
    expect(api.shouldLoad(null)).toBe(false);
    expect(api.shouldLoad('')).toBe(false);
    scope.stop();
  });

  it('reset：清除 onceVisible 和 isVisible', () => {
    const { api, scope } = setup();
    const el = document.createElement('div');
    api.register(el);
    mockObserver!.callback(
      [
        {
          isIntersecting: true,
          target: el,
        } as unknown as IntersectionObserverEntry,
      ],
      mockObserver as unknown as IntersectionObserver,
    );
    expect(api.onceVisible.value).toBe(true);

    api.reset();
    expect(api.onceVisible.value).toBe(false);
    expect(api.isVisible.value).toBe(false);
    scope.stop();
  });

  it('register(null)：不崩溃，不 observe', () => {
    const { api, scope } = setup();
    api.register(null);
    expect(mockObserver).toBeNull();
    scope.stop();
  });

  it('register 切换元素：先 unobserve 旧元素', () => {
    const { api, scope } = setup();
    const el1 = document.createElement('div');
    const el2 = document.createElement('div');
    api.register(el1);
    expect(mockObserver!.observe).toHaveBeenCalledTimes(1);

    api.register(el2);
    expect(mockObserver!.unobserve).toHaveBeenCalledWith(el1);
    expect(mockObserver!.observe).toHaveBeenCalledWith(el2);
    scope.stop();
  });

  it('无 IntersectionObserver 支持：register 直接标记可见', () => {
    delete (globalThis as any).IntersectionObserver;
    const { api, scope } = setup();
    const el = document.createElement('div');
    api.register(el);
    expect(api.onceVisible.value).toBe(true);
    expect(api.isVisible.value).toBe(true);
    expect(api.shouldLoad('loop-1')).toBe(true);
    scope.stop();
  });

  it('自定义 rootMargin 和 threshold 透传', () => {
    mockObserver = null;
    (globalThis as any).IntersectionObserver = MockIntersectionObserver;
    const scope = effectScope();
    let api!: ReturnType<typeof useSectionVisibility>;
    const spy = vi.spyOn(globalThis, 'IntersectionObserver' as any);
    scope.run(() => {
      api = useSectionVisibility({ rootMargin: '100px', threshold: 0.5 });
    });
    const el = document.createElement('div');
    api.register(el);
    // 构造函数被调用，参数包含 options
    expect(spy).toHaveBeenCalledWith(
      expect.any(Function),
      expect.objectContaining({ rootMargin: '100px', threshold: 0.5 }),
    );
    scope.stop();
    spy.mockRestore();
  });
});
