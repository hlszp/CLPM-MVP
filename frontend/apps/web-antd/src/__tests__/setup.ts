/**
 * vitest 全局 setup（jsdom 环境缺口补齐，O-INTEGRATE 2026-10-14）。
 *
 * jsdom 不实现 window.matchMedia；此前部分 metric 组件测试依赖其他测试的
 * polyfill 在 worker 内"泄漏"才得以通过，worker 组成变化即 27 例连锁失败
 * （基线复现，非业务缺陷）。此处统一stub，测试不再依赖执行顺序。
 */
import { vi } from 'vitest';

if (typeof window !== 'undefined' && typeof window.matchMedia !== 'function') {
  window.matchMedia = vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
}
