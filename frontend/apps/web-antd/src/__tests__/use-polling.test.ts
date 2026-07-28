/**
 * usePolling 单元测试
 *
 * 覆盖：
 * - 递归 setTimeout 按间隔轮询 / stop 后不再触发
 * - 防堆积：慢请求未完成前不排定下一次
 * - 失败熔断：连续 N 次失败才停止并回调 onGiveUp，单次成功清零计数
 * - visibilitychange：页面隐藏暂停、可见恢复（默认立即补跑）
 * - effect scope 销毁自动清理
 */
import { effectScope } from 'vue';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { usePolling } from '#/composables/use-polling';

describe('usePolling', () => {
  let hiddenGetter: () => boolean;
  let originalHidden: PropertyDescriptor | undefined;

  beforeEach(() => {
    vi.useFakeTimers();
    let hidden = false;
    hiddenGetter = () => hidden;
    originalHidden = Object.getOwnPropertyDescriptor(document, 'hidden');
    Object.defineProperty(document, 'hidden', {
      configurable: true,
      get: () => hiddenGetter(),
    });
    // 暴露修改入口
    setHiddenValue = (v: boolean) => {
      hidden = v;
    };
  });

  let setHiddenValue: (v: boolean) => void;

  afterEach(() => {
    vi.useRealTimers();
    if (originalHidden) {
      Object.defineProperty(document, 'hidden', originalHidden);
    }
  });

  function setHidden(hidden: boolean) {
    setHiddenValue(hidden);
    document.dispatchEvent(new Event('visibilitychange'));
  }

  it('按间隔递归轮询，stop 后不再触发', async () => {
    const task = vi.fn().mockResolvedValue(undefined);
    const scope = effectScope();
    const polling = scope.run(() =>
      usePolling(task, { interval: 1000, pauseOnHidden: false }),
    )!;

    polling.start();
    expect(task).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(1000);
    expect(task).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(1000);
    expect(task).toHaveBeenCalledTimes(2);

    polling.stop();
    expect(polling.isPolling.value).toBe(false);
    await vi.advanceTimersByTimeAsync(5000);
    expect(task).toHaveBeenCalledTimes(2);

    scope.stop();
  });

  it('防堆积：慢请求未完成前不触发下一次', async () => {
    let resolveTask: (() => void) | undefined;
    const task = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          resolveTask = resolve;
        }),
    );
    const scope = effectScope();
    const polling = scope.run(() =>
      usePolling(task, { interval: 1000, pauseOnHidden: false }),
    )!;

    polling.start();
    await vi.advanceTimersByTimeAsync(1000);
    expect(task).toHaveBeenCalledTimes(1);

    // 任务挂起期间推进 10 个间隔，不应叠加触发
    await vi.advanceTimersByTimeAsync(10_000);
    expect(task).toHaveBeenCalledTimes(1);

    // 任务完成后才排定下一次
    resolveTask?.();
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(1000);
    expect(task).toHaveBeenCalledTimes(2);

    scope.stop();
  });

  it('连续失败达到上限（默认 3）后停止并回调 onGiveUp', async () => {
    const task = vi.fn().mockRejectedValue(new Error('boom'));
    const onGiveUp = vi.fn();
    const scope = effectScope();
    const polling = scope.run(() =>
      usePolling(task, { interval: 1000, onGiveUp, pauseOnHidden: false }),
    )!;

    polling.start();
    await vi.advanceTimersByTimeAsync(1000); // 失败 1
    expect(polling.isPolling.value).toBe(true);
    await vi.advanceTimersByTimeAsync(1000); // 失败 2
    expect(polling.isPolling.value).toBe(true);
    await vi.advanceTimersByTimeAsync(1000); // 失败 3 → 熔断
    expect(task).toHaveBeenCalledTimes(3);
    expect(polling.isPolling.value).toBe(false);
    expect(onGiveUp).toHaveBeenCalledTimes(1);
    expect(onGiveUp).toHaveBeenCalledWith(3);

    // 熔断后不再轮询
    await vi.advanceTimersByTimeAsync(5000);
    expect(task).toHaveBeenCalledTimes(3);

    scope.stop();
  });

  it('单次成功即清零连续失败计数', async () => {
    let calls = 0;
    const task = vi.fn(() => {
      calls += 1;
      // 第 2 次成功，其余失败：fail(1) → ok(0) → fail(1) → fail(2) → fail(3) 熔断
      return calls === 2 ? Promise.resolve() : Promise.reject(new Error('x'));
    });
    const onGiveUp = vi.fn();
    const scope = effectScope();
    const polling = scope.run(() =>
      usePolling(task, { interval: 1000, onGiveUp, pauseOnHidden: false }),
    )!;

    polling.start();
    for (let i = 0; i < 5; i++) {
      await vi.advanceTimersByTimeAsync(1000);
    }
    expect(task).toHaveBeenCalledTimes(5);
    expect(polling.isPolling.value).toBe(false);
    expect(onGiveUp).toHaveBeenCalledWith(3);

    scope.stop();
  });

  it('页面隐藏暂停、恢复可见时默认立即补跑一次', async () => {
    const task = vi.fn().mockResolvedValue(undefined);
    const scope = effectScope();
    const polling = scope.run(() => usePolling(task, { interval: 1000 }))!;

    polling.start();
    await vi.advanceTimersByTimeAsync(1000);
    expect(task).toHaveBeenCalledTimes(1);

    setHidden(true);
    await vi.advanceTimersByTimeAsync(5000);
    expect(task).toHaveBeenCalledTimes(1);
    expect(polling.isPolling.value).toBe(true); // 暂停中仍是轮询态

    setHidden(false);
    await vi.advanceTimersByTimeAsync(0); // 立即补跑
    expect(task).toHaveBeenCalledTimes(2);

    // 恢复后按间隔继续
    await vi.advanceTimersByTimeAsync(1000);
    expect(task).toHaveBeenCalledTimes(3);

    scope.stop();
  });

  it('effect scope 销毁时自动清理，不再触发任务', async () => {
    const task = vi.fn().mockResolvedValue(undefined);
    const scope = effectScope();
    const polling = scope.run(() => usePolling(task, { interval: 1000 }))!;

    polling.start();
    await vi.advanceTimersByTimeAsync(1000);
    expect(task).toHaveBeenCalledTimes(1);

    scope.stop();
    await vi.advanceTimersByTimeAsync(5000);
    expect(task).toHaveBeenCalledTimes(1);

    // scope 销毁后 visibilitychange 监听已移除，不应抛错或触发任务
    setHidden(true);
    setHidden(false);
    await vi.advanceTimersByTimeAsync(1000);
    expect(task).toHaveBeenCalledTimes(1);
  });
});
