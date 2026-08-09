/**
 * useLatestRequest 单元测试（MW-P0-04 请求代次保护）
 *
 * 覆盖：
 * - guard：epoch + targetId 双重校验
 * - bump：递增代次使所有在途响应失效
 * - run：AbortController 取消旧请求
 * - 快速切换场景：A 慢 / B 快，最终只显示 B
 */
import { effectScope, ref } from 'vue';

import { describe, expect, it } from 'vitest';

import { useLatestRequest } from '#/composables/use-latest-request';

function setup() {
  const scope = effectScope();
  let api!: ReturnType<typeof useLatestRequest<string>>;
  scope.run(() => {
    api = useLatestRequest<string>();
  });
  return { api, scope };
}

describe('useLatestRequest', () => {
  it('guard：初始 epoch 匹配时通过', () => {
    const { api } = setup();
    api.bump('loop-A');
    const epoch = 1;
    expect(api.guard('loop-A', epoch)).toBe(true);
  });

  it('guard：bump 后旧 epoch 失效', () => {
    const { api } = setup();
    api.bump('loop-A');
    const oldEpoch = 1;
    api.bump('loop-B');
    expect(api.guard('loop-A', oldEpoch)).toBe(false);
  });

  it('guard：targetId 不匹配时拒绝（即使 epoch 相同）', () => {
    const { api } = setup();
    api.bump('loop-A');
    const epoch = 1;
    expect(api.guard('loop-B', epoch)).toBe(false);
  });

  it('bump(null)：清空目标，所有 guard 拒绝', () => {
    const { api } = setup();
    api.bump('loop-A');
    api.bump(null);
    expect(api.guard('loop-A', 2)).toBe(false);
  });

  it('run：任务正常完成', async () => {
    const { api } = setup();
    api.bump('loop-A');
    const result = ref<string | null>(null);
    await api.run(async () => {
      result.value = 'done';
    });
    expect(result.value).toBe('done');
  });

  it('快速切换场景：A 慢 / B 快，最终只写入 B（旧响应丢弃）', async () => {
    const { api } = setup();
    const state = ref<string | null>(null);

    // 选择 loop-A，发起慢请求
    api.bump('loop-A');
    const slowPromise = api.run(async (_signal, capturedEpoch) => {
      // 模拟慢响应
      await new Promise((r) => setTimeout(r, 50));
      if (!api.guard('loop-A', capturedEpoch)) return;
      state.value = 'A';
    });

    // 立即切换到 loop-B，发起快请求
    api.bump('loop-B');
    const fastPromise = api.run(async (_signal, capturedEpoch) => {
      await new Promise((r) => setTimeout(r, 10));
      if (!api.guard('loop-B', capturedEpoch)) return;
      state.value = 'B';
    });

    await Promise.all([slowPromise, fastPromise]);

    // A 的响应被丢弃（epoch 已变 + targetId 不匹配），只有 B 写入
    expect(state.value).toBe('B');
  });

  it('cancelAll：递增代次并取消在途请求', async () => {
    const { api } = setup();
    api.bump('loop-A');
    const oldEpoch = 1;

    let taskCompleted = false;
    const promise = api.run(async (signal) => {
      await new Promise((r) => setTimeout(r, 50));
      if (signal.aborted) return;
      taskCompleted = true;
    });

    api.cancelAll();
    await promise;

    expect(api.guard('loop-A', oldEpoch)).toBe(false);
    // 任务被取消，不应完成
    expect(taskCompleted).toBe(false);
  });

  it('连续 20 次切换：最后一次的响应才写入', async () => {
    const { api } = setup();
    const state = ref<string | null>(null);
    const promises: Promise<void>[] = [];

    for (let i = 0; i < 20; i++) {
      const id = `loop-${i}`;
      api.bump(id);
      const delay = (19 - i) * 2; // 越早的越慢
      promises.push(
        api.run(async (_signal, capturedEpoch) => {
          await new Promise((r) => setTimeout(r, delay));
          if (!api.guard(id, capturedEpoch)) return;
          state.value = id;
        }),
      );
    }

    await Promise.all(promises);
    expect(state.value).toBe('loop-19');
  });

  it('run 中抛异常不外泄（静默丢弃）', async () => {
    const { api } = setup();
    api.bump('loop-A');
    await expect(
      api.run(async () => {
        throw new Error('network');
      }),
    ).resolves.toBeUndefined();
  });
});
