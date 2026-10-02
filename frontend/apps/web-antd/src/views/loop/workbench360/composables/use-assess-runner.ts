/**
 * 评估任务运行器（workbench360 P2，P2-1）
 *
 * 对齐 API 契约 §1.3 发起链路（区别于旧 use-workbench-task-runner）：
 * - 整点回算：POST /tasks/backfill 创建 PENDING 任务后，
 *   必须再调 POST /tasks/{taskId}/start 触发 Celery（后端创建时不自动执行；
 *   旧运行器缺 start 步骤，任务会滞留 PENDING，见移交文件记录）；
 * - 自定义时段：POST /tasks/custom/evaluate（创建即执行）；
 * - 进度轮询：GET /tasks/{taskId}，3s 递归 setTimeout，页面隐藏暂停，
 *   终态（SUCCESS/FAILED/CANCELLED）停止并回调 onDone 供剖面刷新。
 */
import type { Ref } from 'vue';

import { onScopeDispose, reactive } from 'vue';

import { message } from 'ant-design-vue';

import {
  getTaskDetailApi,
  startTaskApi,
  triggerBackfillApi,
  triggerCustomEvaluateApi,
} from '#/api/task';

const TERMINAL_STATUSES = new Set(['CANCELLED', 'FAILED', 'SUCCESS']);

const POLL_INTERVAL_MS = 3000;

export interface AssessRunnerState {
  /** 是否有任务在途（提交中或 PENDING/RUNNING） */
  isRunning: boolean;
  /** 任务 ID（终态后保留至下次发起） */
  taskId: null | string;
  /** 进度 0~1（null=后端未上报） */
  progress: null | number;
  /** 阶段文案 */
  stage: null | string;
  /** 失败原因（终态 FAILED 时） */
  error: null | string;
  /** 最近一次任务模式（终态提示用） */
  mode: 'backfill' | 'custom' | null;
}

export interface AssessRunnerParams {
  loopId: Ref<null | string>;
  /** 终态 SUCCESS 回调（就地刷新评估历史/摘要） */
  onDone?: (loopId: string, mode: 'backfill' | 'custom') => void;
}

export function useAssessRunner({ loopId, onDone }: AssessRunnerParams) {
  const state = reactive<AssessRunnerState>({
    error: null,
    isRunning: false,
    mode: null,
    progress: null,
    stage: null,
    taskId: null,
  });

  let timer: null | ReturnType<typeof setTimeout> = null;
  let hiddenPaused = false;

  function handleVisibility() {
    if (document.hidden) {
      hiddenPaused = true;
    } else if (hiddenPaused) {
      hiddenPaused = false;
      if (state.isRunning && state.taskId) poll(state.taskId);
    }
  }
  if (typeof document !== 'undefined') {
    document.addEventListener('visibilitychange', handleVisibility);
  }

  function clearTimer() {
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
  }

  onScopeDispose(() => {
    clearTimer();
    if (typeof document !== 'undefined') {
      document.removeEventListener('visibilitychange', handleVisibility);
    }
  });

  async function poll(taskId: string) {
    if (document.hidden) return;
    try {
      const detail = await getTaskDetailApi(taskId);
      state.progress = detail.progress ?? state.progress;
      state.stage = detail.currentStage ?? state.stage;
      if (TERMINAL_STATUSES.has(detail.status)) {
        state.isRunning = false;
        clearTimer();
        if (detail.status === 'SUCCESS') {
          state.progress = 1;
          message.success('评估完成，已刷新评估剖面');
          if (loopId.value) onDone?.(loopId.value, state.mode ?? 'backfill');
        } else {
          state.error = detail.errorMessage || `任务${detail.status}`;
          if (detail.status === 'FAILED') {
            message.error(`评估失败：${state.error}`);
          }
        }
        return;
      }
    } catch {
      // 单次轮询失败不终止，继续下一次
    }
    timer = setTimeout(() => poll(taskId), POLL_INTERVAL_MS);
  }

  /**
   * 发起评估（契约 §1.3）。
   * @param params.mode backfill=整点回算（覆盖 hourly）；custom=自定义时段（写 custom 表）
   */
  async function trigger(params: {
    metrics?: string[];
    mode: 'backfill' | 'custom';
    title?: string;
    tsEnd: string;
    tsStart: string;
  }): Promise<boolean> {
    const id = loopId.value;
    if (!id) {
      message.warning('尚未选中回路，无法发起评估');
      return false;
    }
    clearTimer();
    state.taskId = null;
    state.isRunning = true;
    state.progress = 0;
    state.stage = '提交任务…';
    state.error = null;
    state.mode = params.mode;

    try {
      let taskId = '';
      if (params.mode === 'backfill') {
        const created = (await triggerBackfillApi({
          loopIds: [id],
          title:
            params.title ??
            `回路工作台回算 ${params.tsStart.slice(5, 16).replace('T', ' ')}`,
          tsEnd: params.tsEnd,
          tsStart: params.tsStart,
        })) as { taskId: string };
        taskId = created.taskId;
        // 契约 §1.3：backfill 创建为 PENDING，需显式 start 触发 Celery
        await startTaskApi(taskId);
      } else {
        const created = await triggerCustomEvaluateApi({
          loopIds: [id],
          metrics: params.metrics ?? [],
          tsEnd: params.tsEnd,
          tsStart: params.tsStart,
        });
        taskId = created.taskId;
      }
      state.taskId = taskId;
      state.stage = '已提交，等待执行…';
      poll(taskId);
      return true;
    } catch (error_) {
      state.isRunning = false;
      state.progress = null;
      state.error =
        error_ instanceof Error ? error_.message : '提交评估任务失败';
      message.error(state.error);
      return false;
    }
  }

  /** 剖面离场时停止轮询展示（不取消后端任务） */
  function stop() {
    clearTimer();
    state.isRunning = false;
  }

  return { state, stop, trigger };
}
