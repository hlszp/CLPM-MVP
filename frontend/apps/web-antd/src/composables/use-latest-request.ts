/**
 * useLatestRequest —— 请求代次保护（MW-P0-04）
 *
 * 背景：工作台快速切换回路时，旧回路的慢响应可能覆盖新回路的数据。
 * 本组合式函数提供"代次 + 目标 ID"双重校验，确保只有最新选择的回路的响应才会写入状态。
 *
 * 原理：
 *   1. 每次选择回路递增 epoch，并记录当前目标 ID；
 *   2. 发起请求前捕获 (epoch, targetId)；
 *   3. 响应写入前校验：若 epoch 已变或 targetId 不匹配，丢弃响应；
 *   4. 可取消的请求接入 AbortController，切换时取消旧请求。
 *
 * 用法：
 * ```ts
 * const { bump, guard, run } = useLatestRequest<string>();
 * function selectLoop(id: string) {
 *   bump(id); // 递增代次 + 记录目标
 *   run(id, async (signal, capturedEpoch) => {
 *     const data = await fetchData(id, { signal });
 *     if (!guard(id, capturedEpoch)) return; // 旧响应，丢弃
 *     state.value = data;
 *   });
 * }
 * ```
 */
import { ref } from 'vue';
import { onBeforeUnmount } from 'vue';

export interface LatestRequestGuard<TId> {
  /** 递增代次并记录当前目标 ID，使所有在途响应失效 */
  bump: (id: null | TId) => void;
  /** 校验指定 id 是否仍为最新目标且代次未变（epoch + targetId 双重校验） */
  guard: (targetId: TId, capturedEpoch: number) => boolean;
  /** 执行受保护的异步任务；自动管理 AbortController */
  run: (
    task: (signal: AbortSignal, capturedEpoch: number) => Promise<void>,
  ) => Promise<void>;
  /** 取消所有在途请求并递增代次 */
  cancelAll: () => void;
}

export function useLatestRequest<TId>(): LatestRequestGuard<TId> {
  const epoch = ref(0);
  const currentId = ref<null | TId>(null);
  let controller: AbortController | null = null;

  function bump(id: null | TId) {
    epoch.value += 1;
    currentId.value = id;
    // 递近代次时取消在途请求，避免旧响应覆盖
    controller?.abort();
    controller = null;
  }

  function guard(targetId: TId, capturedEpoch: number): boolean {
    return capturedEpoch === epoch.value && currentId.value === targetId;
  }

  async function run(
    task: (signal: AbortSignal, capturedEpoch: number) => Promise<void>,
  ): Promise<void> {
    const capturedEpoch = epoch.value;
    controller?.abort();
    controller = new AbortController();
    try {
      await task(controller.signal, capturedEpoch);
    } catch {
      // 被取消或失败：静默丢弃，由调用方决定是否重试
    }
  }

  function cancelAll() {
    controller?.abort();
    controller = null;
    epoch.value += 1;
    currentId.value = null;
  }

  onBeforeUnmount(() => {
    controller?.abort();
    controller = null;
  });

  return { bump, cancelAll, guard, run };
}
