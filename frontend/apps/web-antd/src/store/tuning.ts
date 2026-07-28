/**
 * CLPM 回路整定 Store（Phase 2）
 *
 * 跨页面共享整定流程状态：当前回路 → 辨识结果 → 候选 PID → 仿真对比 → 任务创建。
 * 解决原方案靠路由 query 传递多组 PID 参数的可维护性问题（§6.2）。
 *
 * 状态流：
 *   currentLoop → identifyResult → pidCandidates → simulationResult → createTask
 *
 * 对齐 IDS v3.2 §2.5 + 实现契约 v2.1 状态机
 *   DRAFT → RUNNING → IDENTIFIED → SIMULATED → COMPLETED
 *                     ↓              ↓
 *                 INCONCLUSIVE    ROLLED_BACK
 */
import type { TuningApi } from '#/api/tuning';

import { ref } from 'vue';

import { defineStore } from 'pinia';

import {
  cancelTuningTaskApi,
  comparePidsApi,
  getTaskStatusApi,
  identifyHistoryApi,
  previewSegmentsApi,
  simulateTuningApi,
} from '#/api/tuning';

export const useTuningStore = defineStore('tuning', () => {
  // ---- 跨页面共享状态 ----

  /** 当前选中回路（从工作台/辨识页选择后共享） */
  const currentLoopId = ref<string>('');
  const currentLoopTagName = ref<string>('');

  /** 历史辨识结果（Phase 2 异步任务完成后写入） */
  const identifyResult = ref<TuningApi.IdentifyHistoryResult | null>(null);

  /** 异步任务进度（轮询写入） */
  const taskProgress = ref<TuningApi.TaskProgress | null>(null);

  /** 候选 PID 列表（用于多 PID 对比仿真） */
  const pidCandidates = ref<TuningApi.PidParamsWithLabel[]>([]);

  /** 闭环仿真结果 */
  const simulationResult = ref<TuningApi.SimulationResult | null>(null);

  /** 可辨识片段预览结果 */
  const segments = ref<TuningApi.IdentifySegmentsResult | null>(null);

  /** 轮询定时器句柄 */
  let pollTimer: null | ReturnType<typeof setInterval> = null;

  // ---- Actions ----

  /** 设置当前回路 */
  function setCurrentLoop(loopId: string, tagName = '') {
    currentLoopId.value = loopId;
    currentLoopTagName.value = tagName;
  }

  /** 添加一组候选 PID */
  function addPidCandidate(label: string, pid: TuningApi.PidParams) {
    pidCandidates.value.push({ label, ...pid });
  }

  /** 移除指定索引的候选 PID */
  function removePidCandidate(index: number) {
    pidCandidates.value.splice(index, 1);
  }

  /** 清空候选 PID */
  function clearPidCandidates() {
    pidCandidates.value = [];
  }

  /** 重置全部状态（新建整定流程时调用） */
  function $reset() {
    stopPolling();
    currentLoopId.value = '';
    currentLoopTagName.value = '';
    identifyResult.value = null;
    taskProgress.value = null;
    pidCandidates.value = [];
    simulationResult.value = null;
    segments.value = null;
  }

  // ---- 异步任务流程 ----

  /**
   * 提交历史数据辨识（异步任务）
   * @returns taskId，可用于后续轮询
   */
  async function submitIdentify(
    params: TuningApi.IdentifyHistoryRequest,
  ): Promise<string> {
    const resp = await identifyHistoryApi(params);
    taskProgress.value = {
      taskId: resp.taskId,
      status: resp.status,
      progress: 0,
    };
    return resp.taskId;
  }

  /**
   * 轮询任务进度（启动定时器，每 2s 查一次）
   * 任务终态（SUCCESS/FAILED）自动停止轮询
   */
  function startPolling(taskId: string, onDone?: (progress: TuningApi.TaskProgress) => void) {
    stopPolling();
    pollTimer = setInterval(async () => {
      try {
        const progress = await getTaskStatusApi(taskId);
        taskProgress.value = progress;
        if (progress.status === 'SUCCESS') {
          // 任务成功，提取辨识结果
          if (progress.result) {
            identifyResult.value = progress.result as unknown as TuningApi.IdentifyHistoryResult;
          }
          stopPolling();
          onDone?.(progress);
        } else if (progress.status === 'FAILED') {
          stopPolling();
          onDone?.(progress);
        }
      } catch {
        // 轮询失败不中断，等待下一轮
      }
    }, 2000);
  }

  /** 停止轮询 */
  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  /** 取消异步任务 */
  async function cancelTask(taskId: string) {
    await cancelTuningTaskApi(taskId);
    stopPolling();
    if (taskProgress.value) {
      taskProgress.value = {
        ...taskProgress.value,
        status: 'FAILED',
        error: '用户取消',
      };
    }
  }

  // ---- 可辨识片段预览 ----

  /** 预览可辨识片段（不执行辨识） */
  async function previewSegments(params: TuningApi.IdentifySegmentsRequest) {
    segments.value = await previewSegmentsApi(params);
  }

  // ---- 仿真对比 ----

  /** 执行闭环仿真（双 PID 对比，兼容旧接口） */
  async function runSimulation(params: TuningApi.SimulateRequest) {
    simulationResult.value = await simulateTuningApi(params);
  }

  /** 多 PID 对比仿真（至少 2 组候选） */
  async function runComparePids(params: TuningApi.SimulateRequest) {
    simulationResult.value = await comparePidsApi(params);
  }

  return {
    // state
    currentLoopId,
    currentLoopTagName,
    identifyResult,
    taskProgress,
    pidCandidates,
    simulationResult,
    segments,
    // actions
    setCurrentLoop,
    addPidCandidate,
    removePidCandidate,
    clearPidCandidates,
    submitIdentify,
    startPolling,
    stopPolling,
    cancelTask,
    previewSegments,
    runSimulation,
    runComparePids,
    $reset,
  };
});
