/**
 * useAsyncPdfExport 单元测试（V62-P3-33 异步 PDF 导出 composable）
 *
 * 覆盖：
 * - 提交成功 → 启动轮询 → SUCCESS 自动 window.open 下载
 * - FAILED → message.error + 清理 runningTaskId
 * - 连续 4 次轮询失败 → 熔断 + message.warning
 * - 重复提交被拒绝
 * - onBeforeUnmount 自动 cancel
 * - window.open 抛错时降级为 message.warning
 *
 * 进度语义对齐后端 generate_diagnosis_pdf_task：
 *   0.25 / 0.50 / 0.75 / 0.95 / 1.00
 */
import type { TaskApi } from '#/api/task';

import { effectScope } from 'vue';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// --- mock 依赖 ---
const getTaskDetailApiMock = vi.fn();
const buildTaskDownloadUrlMock = vi.fn(
  (taskId: string) => `/api/v1/tasks/${taskId}/download`,
);

vi.mock('#/api/task', () => ({
  buildTaskDownloadUrl: (taskId: string) => buildTaskDownloadUrlMock(taskId),
  getTaskDetailApi: (taskId: string) => getTaskDetailApiMock(taskId),
}));

const messageMock = {
  error: vi.fn(),
  success: vi.fn(),
  warning: vi.fn(),
};
vi.mock('ant-design-vue', () => ({
  message: messageMock,
}));

// window.open mock
const windowOpenMock = vi.fn();
beforeEach(() => {
  vi.useFakeTimers();
  Object.assign(globalThis, { open: windowOpenMock });
});

afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
});

function makeTaskItem(
  overrides: Partial<TaskApi.TaskItem> = {},
): TaskApi.TaskItem {
  return {
    createdAt: '2026-08-11T10:00:00Z',
    createdBy: 'alice',
    status: 'RUNNING',
    taskId: 'task-001',
    taskType: 'REPORT',
    ...overrides,
  } as TaskApi.TaskItem;
}

describe('useAsyncPdfExport', () => {
  it('提交成功 → 轮询 → SUCCESS 自动 window.open 下载', async () => {
    const { useAsyncPdfExport } =
      await import('../composables/use-async-pdf-export');
    const scope = effectScope();
    const api = scope.run(() => useAsyncPdfExport())!;

    // 第一次轮询返回 SUCCESS
    getTaskDetailApiMock.mockResolvedValueOnce(
      makeTaskItem({
        status: 'SUCCESS',
        progress: 1,
        currentStage: '文件已生成',
        resultUrl: '/api/v1/tasks/task-001/download',
      }),
    );

    await api.run(async () => ({ taskId: 'task-001' }));

    expect(api.isRunning.value).toBe(true);
    expect(api.runningTaskId.value).toBe('task-001');

    // 推进定时器到第一次轮询（1500ms 间隔）
    await vi.advanceTimersByTimeAsync(1500);
    // window.open 在 SUCCESS 后 200ms setTimeout 中执行
    await vi.advanceTimersByTimeAsync(200);

    expect(getTaskDetailApiMock).toHaveBeenCalledWith('task-001');
    expect(api.isRunning.value).toBe(false);
    expect(api.runningTaskId.value).toBe(null);
    expect(api.progress.value).toBe(1);
    // window.open 用 resultUrl
    expect(windowOpenMock).toHaveBeenCalledWith(
      '/api/v1/tasks/task-001/download',
      '_blank',
    );
    expect(messageMock.success).toHaveBeenCalledWith('导出完成，已开始下载');

    scope.stop();
  });

  it('resultUrl 缺失时回退到 buildTaskDownloadUrl', async () => {
    const { useAsyncPdfExport } =
      await import('../composables/use-async-pdf-export');
    const scope = effectScope();
    const api = scope.run(() => useAsyncPdfExport())!;

    getTaskDetailApiMock.mockResolvedValueOnce(
      makeTaskItem({
        status: 'SUCCESS',
        progress: 1,
        resultUrl: null, // 缺失
      }),
    );

    await api.run(async () => ({ taskId: 'task-fallback' }));
    await vi.advanceTimersByTimeAsync(1500);
    await vi.advanceTimersByTimeAsync(200);

    expect(buildTaskDownloadUrlMock).toHaveBeenCalledWith('task-fallback');
    expect(windowOpenMock).toHaveBeenCalledWith(
      '/api/v1/tasks/task-fallback/download',
      '_blank',
    );

    scope.stop();
  });

  it('SUCCESS 后再次轮询到达不重复触发 window.open', async () => {
    const { useAsyncPdfExport } =
      await import('../composables/use-async-pdf-export');
    const scope = effectScope();
    const api = scope.run(() => useAsyncPdfExport())!;

    // 让前两次轮询都返回 SUCCESS（模拟 Redis 副本读时序）
    getTaskDetailApiMock.mockResolvedValue(
      makeTaskItem({
        status: 'SUCCESS',
        progress: 1,
        resultUrl: '/api/v1/tasks/task-dup/download',
      }),
    );

    await api.run(async () => ({ taskId: 'task-dup' }));
    await vi.advanceTimersByTimeAsync(1500);
    await vi.advanceTimersByTimeAsync(200);
    // 第一次轮询已触发 window.open + 清理 runningTaskId（停止轮询）
    expect(windowOpenMock).toHaveBeenCalledTimes(1);

    // 再推进时间，不应再触发下载（轮询已停止）
    await vi.advanceTimersByTimeAsync(3000);
    expect(windowOpenMock).toHaveBeenCalledTimes(1);

    scope.stop();
  });

  it('FAILED → message.error 并清理 runningTaskId', async () => {
    const { useAsyncPdfExport } =
      await import('../composables/use-async-pdf-export');
    const scope = effectScope();
    const api = scope.run(() => useAsyncPdfExport())!;

    getTaskDetailApiMock.mockResolvedValueOnce(
      makeTaskItem({
        status: 'FAILED',
        progress: 0,
        errorMessage: 'PDF 渲染失败：回路数据为空',
      }),
    );

    await api.run(async () => ({ taskId: 'task-fail' }));
    await vi.advanceTimersByTimeAsync(1500);

    expect(api.isRunning.value).toBe(false);
    expect(api.runningTaskId.value).toBe(null);
    expect(api.progress.value).toBe(0);
    expect(messageMock.error).toHaveBeenCalledWith(
      '导出失败：PDF 渲染失败：回路数据为空',
    );
    expect(windowOpenMock).not.toHaveBeenCalled();

    scope.stop();
  });

  it('FAILED 无 errorMessage 时使用通用文案', async () => {
    const { useAsyncPdfExport } =
      await import('../composables/use-async-pdf-export');
    const scope = effectScope();
    const api = scope.run(() => useAsyncPdfExport())!;

    getTaskDetailApiMock.mockResolvedValueOnce(
      makeTaskItem({
        status: 'FAILED',
        errorMessage: null,
      }),
    );

    await api.run(async () => ({ taskId: 'task-fail-no-err' }));
    await vi.advanceTimersByTimeAsync(1500);

    expect(messageMock.error).toHaveBeenCalledWith(
      '导出失败，请重试或查看任务详情',
    );

    scope.stop();
  });

  it('连续 4 次轮询失败 → 熔断 + message.warning', async () => {
    const { useAsyncPdfExport } =
      await import('../composables/use-async-pdf-export');
    const scope = effectScope();
    const api = scope.run(() => useAsyncPdfExport())!;

    // 4 次轮询都抛网络错误
    getTaskDetailApiMock.mockRejectedValue(new Error('Network 500'));

    await api.run(async () => ({ taskId: 'task-network' }));

    // 推进 4 次 1500ms 轮询
    for (let i = 1; i <= 4; i++) {
      await vi.advanceTimersByTimeAsync(1500);
    }

    expect(api.isRunning.value).toBe(false);
    expect(api.runningTaskId.value).toBe(null);
    expect(messageMock.warning).toHaveBeenCalledWith(
      expect.stringMatching(/进度查询已暂停（连续失败 4 次）/),
    );

    scope.stop();
  });

  it('重复提交被拒绝（已有任务进行中）', async () => {
    const { useAsyncPdfExport } =
      await import('../composables/use-async-pdf-export');
    const scope = effectScope();
    const api = scope.run(() => useAsyncPdfExport())!;

    // 第一次提交后保持 RUNNING 状态
    getTaskDetailApiMock.mockResolvedValue(
      makeTaskItem({ status: 'RUNNING', progress: 0.25 }),
    );

    await api.run(async () => ({ taskId: 'task-running' }));
    expect(api.isRunning.value).toBe(true);

    // 第二次提交
    await api.run(async () => ({ taskId: 'task-second' }));
    expect(messageMock.warning).toHaveBeenCalledWith(
      '已有导出任务进行中，请等待完成',
    );
    expect(api.runningTaskId.value).toBe('task-running'); // 仍是第一个

    scope.stop();
  });

  it('提交函数抛错 → runningTaskId 清理 + 错误重新抛出', async () => {
    const { useAsyncPdfExport } =
      await import('../composables/use-async-pdf-export');
    const scope = effectScope();
    const api = scope.run(() => useAsyncPdfExport())!;

    await expect(
      api.run(async () => {
        throw new Error('submit 500');
      }),
    ).rejects.toThrow('submit 500');

    expect(api.isRunning.value).toBe(false);
    expect(api.runningTaskId.value).toBe(null);

    scope.stop();
  });

  it('window.open 抛错时降级为 message.warning', async () => {
    const { useAsyncPdfExport } =
      await import('../composables/use-async-pdf-export');
    const scope = effectScope();
    const api = scope.run(() => useAsyncPdfExport())!;

    getTaskDetailApiMock.mockResolvedValueOnce(
      makeTaskItem({
        status: 'SUCCESS',
        progress: 1,
        resultUrl: '/api/v1/tasks/task-blocked/download',
      }),
    );
    windowOpenMock.mockImplementationOnce(() => {
      throw new Error('popup blocked');
    });

    await api.run(async () => ({ taskId: 'task-blocked' }));
    await vi.advanceTimersByTimeAsync(1500);
    // message.success 在 SUCCESS 时立即弹（不等 200ms）
    expect(messageMock.success).toHaveBeenCalledWith('导出完成，已开始下载');
    // window.open 在 200ms 后执行并抛错 → 降级 message.warning
    windowOpenMock.mockImplementationOnce(() => {
      throw new Error('popup blocked');
    });
    await vi.advanceTimersByTimeAsync(200);

    expect(messageMock.warning).toHaveBeenCalledWith(
      '导出完成，浏览器阻止了新窗口，建议调整弹窗权限',
    );

    scope.stop();
  });

  it('手动 cancel() 停止轮询并清理状态', async () => {
    const { useAsyncPdfExport } =
      await import('../composables/use-async-pdf-export');
    const scope = effectScope();
    const api = scope.run(() => useAsyncPdfExport())!;

    getTaskDetailApiMock.mockResolvedValue(
      makeTaskItem({ status: 'RUNNING', progress: 0.5 }),
    );

    await api.run(async () => ({ taskId: 'task-cancel' }));
    expect(api.isRunning.value).toBe(true);

    // 手动取消（如关闭页面/换回路时调用）
    api.cancel();
    expect(api.isRunning.value).toBe(false);
    expect(api.runningTaskId.value).toBe(null);
    expect(api.currentStage.value).toBe('');

    // 推进时间不应再触发 API 调用
    const callCountBefore = getTaskDetailApiMock.mock.calls.length;
    await vi.advanceTimersByTimeAsync(5000);
    expect(getTaskDetailApiMock.mock.calls.length).toBe(callCountBefore);

    scope.stop();
  });

  it('进度阶段语义对齐后端五段（0.25→0.50→0.75→0.95→1.00）', async () => {
    const { useAsyncPdfExport } =
      await import('../composables/use-async-pdf-export');
    const scope = effectScope();
    const api = scope.run(() => useAsyncPdfExport())!;

    const stages = [
      { progress: 0.25, currentStage: '加载回路信息与诊断快照' },
      { progress: 0.5, currentStage: '获取整改推荐方案' },
      { progress: 0.75, currentStage: '生成诊断建议书 PDF' },
      { progress: 0.95, currentStage: '写入导出文件' },
      { progress: 1, currentStage: '文件已生成', status: 'SUCCESS' as const },
    ];

    for (const stage of stages) {
      getTaskDetailApiMock.mockResolvedValueOnce(
        makeTaskItem({
          status: stage.status ?? 'RUNNING',
          progress: stage.progress,
          currentStage: stage.currentStage,
          resultUrl: '/api/v1/tasks/task-stages/download',
        }),
      );
    }

    await api.run(async () => ({ taskId: 'task-stages' }));

    // 推进 5 次轮询
    for (let i = 0; i < 5; i++) {
      await vi.advanceTimersByTimeAsync(1500);
    }

    // 最终进度为 1，状态清理
    expect(api.progress.value).toBe(1);
    expect(api.currentStage.value).toBe('文件已生成');
    expect(api.isRunning.value).toBe(false);

    scope.stop();
  });
});
