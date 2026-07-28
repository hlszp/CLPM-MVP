/**
 * usePolling — 通用轮询 composable
 *
 * 统一各业务页（任务列表、监控看板等）重复的轮询样板，行为约定：
 *
 * - 防堆积：递归 setTimeout，等上一次任务执行完成后才排定下一次
 *   （参考 diagnosis/tasks.vue 的 schedulePoll 既有模式），慢请求不会回调叠加；
 * - 页面隐藏自动暂停：`document.visibilitychange` 监听，hidden 时清定时器，
 *   可见时恢复（默认立即补跑一次刷新过期数据）；
 * - 失败熔断：连续失败 N 次（默认 3）才停止轮询并回调 `onGiveUp`，
 *   单次成功即清零失败计数（偶发抖动不会误停）；
 * - 自动清理：组件卸载（effect scope 销毁）时停止轮询并移除监听。
 *
 * 用法：
 * ```ts
 * const { isPolling, failureCount, start, stop, resetFailures } = usePolling(
 *   () => loadTasks(true),
 *   { interval: 5000, onGiveUp: () => message.warning('刷新已暂停，请手动重试') },
 * );
 * onMounted(start);
 * ```
 */
import type { Ref } from 'vue';

import { onScopeDispose, readonly, ref } from 'vue';

export interface UsePollingOptions {
  /** 轮询间隔（毫秒），默认 5000 */
  interval?: number;
  /** 连续失败次数上限，达到后停止并回调 onGiveUp，默认 3 */
  maxFailures?: number;
  /** 连续失败达到上限时的回调，参数为当前连续失败次数 */
  onGiveUp?: (failures: number) => void;
  /** 页面隐藏时暂停、可见时恢复，默认 true */
  pauseOnHidden?: boolean;
  /** 页面恢复可见时立即执行一次（刷新过期数据），默认 true */
  runImmediateOnResume?: boolean;
}

export interface UsePollingReturn {
  /** 当前连续失败次数（只读） */
  failureCount: Readonly<Ref<number>>;
  /** 是否处于轮询中（只读；页面隐藏暂停期间仍为 true） */
  isPolling: Readonly<Ref<boolean>>;
  /** 清零连续失败计数（手动重试场景配合 start 使用） */
  resetFailures: () => void;
  /** 开始轮询（幂等：轮询中重复调用不会叠加定时器） */
  start: () => void;
  /** 停止轮询并清零失败计数 */
  stop: () => void;
}

export function usePolling(
  task: () => Promise<unknown> | unknown,
  options: UsePollingOptions = {},
): UsePollingReturn {
  const {
    interval = 5000,
    maxFailures = 3,
    onGiveUp,
    pauseOnHidden = true,
    runImmediateOnResume = true,
  } = options;

  const isPolling = ref(false);
  const failureCount = ref(0);

  let timer: null | ReturnType<typeof setTimeout> = null;
  /** 页面隐藏导致的暂停标记（isPolling 保持 true，仅暂停计时） */
  let pausedByHidden = false;

  function clearTimer() {
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
  }

  /**
   * 执行一次任务并按结果排定下一次（递归 setTimeout 防堆积核心）
   */
  async function tick() {
    timer = null;
    if (!isPolling.value || pausedByHidden) return;

    try {
      await task();
      failureCount.value = 0;
    } catch {
      failureCount.value += 1;
      if (failureCount.value >= maxFailures) {
        const failures = failureCount.value;
        stop();
        onGiveUp?.(failures);
        return;
      }
    }

    // 任务执行期间可能已被 stop / 页面隐藏，排定前再检查
    if (isPolling.value && !pausedByHidden) {
      timer = setTimeout(() => void tick(), interval);
    }
  }

  function schedule() {
    clearTimer();
    if (isPolling.value && !pausedByHidden) {
      timer = setTimeout(() => void tick(), interval);
    }
  }

  function handleVisibilityChange() {
    if (!pauseOnHidden) return;
    if (document.hidden) {
      if (isPolling.value) {
        pausedByHidden = true;
        clearTimer();
      }
    } else if (pausedByHidden) {
      pausedByHidden = false;
      if (isPolling.value) {
        if (runImmediateOnResume) {
          // 立即补跑一次，由 tick 自行排定后续
          void tick();
        } else {
          schedule();
        }
      }
    }
  }

  function start() {
    if (isPolling.value) return;
    isPolling.value = true;
    pausedByHidden = pauseOnHidden && document.hidden;
    schedule();
  }

  function stop() {
    isPolling.value = false;
    pausedByHidden = false;
    failureCount.value = 0;
    clearTimer();
  }

  function resetFailures() {
    failureCount.value = 0;
  }

  if (pauseOnHidden && typeof document !== 'undefined') {
    document.addEventListener('visibilitychange', handleVisibilityChange);
  }

  onScopeDispose(() => {
    stop();
    if (pauseOnHidden && typeof document !== 'undefined') {
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    }
  });

  return {
    failureCount: readonly(failureCount),
    isPolling: readonly(isPolling),
    resetFailures,
    start,
    stop,
  };
}
