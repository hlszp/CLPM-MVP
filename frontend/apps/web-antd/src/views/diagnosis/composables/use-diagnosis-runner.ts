/**
 * 诊断任务执行器：发起 → 细粒度进度轮询 → 终态后按 taskId 拉取结果列表。
 *
 * 模式参照 use-workbench-task-runner（递归 setTimeout 防堆积 +
 * 页面隐藏暂停/恢复 + onScopeDispose 清理）。
 */
import type { DiagnosisApi } from '#/api/diagnosis';

import { onScopeDispose, ref } from 'vue';

import { getDiagnosisRunsApi, triggerDiagnosisApi } from '#/api/diagnosis';
import { getTaskDetailApi } from '#/api/task';

const TERMINAL_STATUSES = new Set(['CANCELLED', 'FAILED', 'SUCCESS']);
const POLL_INTERVAL_MS = 3000;

export function useDiagnosisRunner(options?: {
  onFinished?: (items: DiagnosisApi.RunListItem[]) => void;
}) {
  const running = ref(false);
  const progress = ref(0);
  const stage = ref('');
  const errorMessage = ref('');
  const resultItems = ref<DiagnosisApi.RunListItem[]>([]);

  let timer: null | number = null;
  let pollTaskId = '';
  let hiddenPause = false;

  function clearTimer() {
    if (timer != null) {
      window.clearTimeout(timer);
      timer = null;
    }
  }

  function onVisibilityChange() {
    if (document.hidden) {
      hiddenPause = true;
      clearTimer();
    } else if (hiddenPause && pollTaskId) {
      hiddenPause = false;
      schedulePoll(0);
    }
  }

  function schedulePoll(delay: number = POLL_INTERVAL_MS) {
    clearTimer();
    timer = window.setTimeout(pollOnce, delay);
  }

  async function pollOnce() {
    if (!pollTaskId) return;
    try {
      const detail = await getTaskDetailApi(pollTaskId);
      progress.value = detail.progress ?? 0;
      stage.value = detail.currentStage ?? '';
      if (TERMINAL_STATUSES.has(detail.status ?? '')) {
        if (detail.status === 'FAILED') {
          errorMessage.value = detail.errorMessage ?? '诊断任务执行失败';
        }
        await finish();
        return;
      }
    } catch {
      // 单次查询失败不终止轮询（与既有 runner 口径一致）
    }
    if (!document.hidden) {
      schedulePoll();
    }
  }

  async function finish() {
    clearTimer();
    pollTaskId = '';
    running.value = false;
    try {
      const res = await getDiagnosisRunsApi({ taskId: lastTaskId, page: 1, pageSize: 100 });
      resultItems.value = res.items;
      options?.onFinished?.(res.items);
    } catch {
      resultItems.value = [];
    }
  }

  let lastTaskId = '';

  async function trigger(body: DiagnosisApi.TriggerBody) {
    running.value = true;
    progress.value = 0;
    stage.value = '任务提交中';
    errorMessage.value = '';
    resultItems.value = [];
    const res = await triggerDiagnosisApi(body);
    lastTaskId = res.taskId;
    pollTaskId = res.taskId;
    schedulePoll(500);
    return res;
  }

  function reset() {
    clearTimer();
    pollTaskId = '';
    lastTaskId = '';
    running.value = false;
    progress.value = 0;
    stage.value = '';
    errorMessage.value = '';
    resultItems.value = [];
  }

  document.addEventListener('visibilitychange', onVisibilityChange);
  onScopeDispose(() => {
    clearTimer();
    document.removeEventListener('visibilitychange', onVisibilityChange);
  });

  return {
    errorMessage,
    progress,
    resultItems,
    running,
    stage,
    reset,
    trigger,
  };
}
