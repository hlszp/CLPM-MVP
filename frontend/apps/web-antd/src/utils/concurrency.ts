/**
 * 并发控制工具
 *
 * 用途：批量请求场景下限制并发数，避免瞬间打满后端连接池；
 * 同时用 allSettled 语义保证单项失败不中断其余项。
 */

/**
 * 以固定并发数批量执行异步任务，返回成功/失败计数。
 *
 * - 使用 allSettled 语义：单项 reject 不会中断其他任务
 * - 通过 worker 池控制最大并发数，避免一次性发起数十个请求
 *
 * @param items 待处理项数组
 * @param fn 单项处理函数（reject 会被捕获并计入 rejected）
 * @param limit 最大并发数，默认 8
 * @returns { fulfilled, rejected } 成功与失败计数
 */
export async function runWithConcurrency<T>(
  items: T[],
  fn: (item: T, index: number) => Promise<unknown>,
  limit = 8,
): Promise<{ fulfilled: number; rejected: number }> {
  let fulfilled = 0;
  let rejected = 0;
  let cursor = 0;

  async function worker() {
    while (cursor < items.length) {
      const index = cursor++;
      const item = items[index];
      if (item === undefined) continue;
      try {
        await fn(item, index);
        fulfilled++;
      } catch {
        rejected++;
      }
    }
  }

  const workerCount = Math.min(limit, items.length);
  await Promise.allSettled(Array.from({ length: workerCount }, () => worker()));
  return { fulfilled, rejected };
}
