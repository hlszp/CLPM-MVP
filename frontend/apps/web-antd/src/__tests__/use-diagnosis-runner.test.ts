/**
 * useDiagnosisRunner 测试：发起 → 进度轮询 → 终态 → 结果加载。
 */
import type { DiagnosisApi } from '#/api/diagnosis';

import { describe, expect, it, vi } from 'vitest';

const triggerMock = vi.fn();
const taskDetailMock = vi.fn();
const runsMock = vi.fn();

vi.mock('#/api/diagnosis', () => ({
  triggerDiagnosisApi: (...args: unknown[]) => triggerMock(...args),
  getDiagnosisRunsApi: (...args: unknown[]) => runsMock(...args),
}));
vi.mock('#/api/task', () => ({
  getTaskDetailApi: (...args: unknown[]) => taskDetailMock(...args),
}));

import { useDiagnosisRunner } from '../views/diagnosis/composables/use-diagnosis-runner';

function makeItem(id: string): DiagnosisApi.RunListItem {
  return {
    id,
    taskId: 'task-1',
    loopId: 'loop-1',
    loopTagName: 'FIC-101',
    triggeredBy: 'tester',
    timeWindowStart: '2026-08-15T00:00:00',
    timeWindowEnd: '2026-08-15T07:00:00',
    operatorGroup: 'full',
    status: 'SUCCESS',
    primaryCategory: 'VALVE',
    primaryCategoryLabel: '阀门/执行机构问题',
    primaryConfidence: 0.88,
    secondaryCategories: [],
    pendingReview: [],
    severity: 'MEDIUM',
    createdAt: '2026-08-16T12:00:00',
  };
}

describe('useDiagnosisRunner', () => {
  it('trigger 后轮询进度，终态 SUCCESS 停止并加载结果', async () => {
    vi.useFakeTimers();
    try {
      triggerMock.mockResolvedValue({ taskId: 'task-1', accepted: 2 });
      taskDetailMock
        .mockResolvedValueOnce({
          status: 'RUNNING',
          progress: 0.2,
          currentStage: '回路 1/2：算子 3/11',
        })
        .mockResolvedValueOnce({
          status: 'SUCCESS',
          progress: 1,
          currentStage: '完成 2/2',
        });
      const finished = vi.fn();
      runsMock.mockResolvedValue({
        items: [makeItem('run-1'), makeItem('run-2')],
        total: 2,
      });

      const runner = useDiagnosisRunner({ onFinished: finished });
      await runner.trigger({
        loopIds: ['loop-1'],
        timeWindow: { preset: 'last_7d' },
        operatorGroup: 'full',
      });

      expect(runner.running.value).toBe(true);
      await vi.advanceTimersByTimeAsync(500); // 第一次轮询（RUNNING）
      expect(runner.progress.value).toBeCloseTo(0.2);
      expect(runner.stage.value).toContain('算子');

      await vi.advanceTimersByTimeAsync(3000); // 第二次轮询（SUCCESS 终态）
      expect(runner.running.value).toBe(false);
      expect(runner.resultItems.value).toHaveLength(2);
      expect(finished).toHaveBeenCalledTimes(1);
      expect(runsMock).toHaveBeenCalledWith(
        expect.objectContaining({ taskId: 'task-1' }),
      );
    } finally {
      vi.useRealTimers();
    }
  });

  it('FAILED 终态记录错误信息', async () => {
    vi.useFakeTimers();
    try {
      triggerMock.mockResolvedValue({ taskId: 'task-2', accepted: 1 });
      taskDetailMock.mockResolvedValue({
        status: 'FAILED',
        progress: 1,
        currentStage: '诊断失败',
        errorMessage: '全部回路诊断失败',
      });
      runsMock.mockResolvedValue({ items: [], total: 0 });

      const runner = useDiagnosisRunner();
      await runner.trigger({
        loopIds: ['loop-1'],
        timeWindow: { preset: 'last_24h' },
        operatorGroup: 'fast',
      });
      await vi.advanceTimersByTimeAsync(500);

      expect(runner.running.value).toBe(false);
      expect(runner.errorMessage.value).toContain('全部回路诊断失败');
      expect(runner.resultItems.value).toHaveLength(0);
    } finally {
      vi.useRealTimers();
    }
  });
});
