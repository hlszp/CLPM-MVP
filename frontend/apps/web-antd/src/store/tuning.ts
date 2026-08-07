/**
 * CLPM 回路整定 Store（Phase 2 + v6.2 P1-019）
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
 *
 * v6.2 P1-019：新增 sessionStorage 持久化 + taskId 回显兜底
 *   - 关键流程状态写入 sessionStorage，同标签页刷新可恢复
 *   - restoreFromTask(taskId) 调后端 getTuningTaskDetailApi 回显（跨标签页/设备兜底）
 *   - model_source/source_record_id/riskConfirmed 三个门禁字段未在后端持久化，
 *     回显时置空，由子页要求重新确认（Phase 0 安全降级，不绕过门禁）
 */
import type { TuningApi } from '#/api/tuning';

import { ref, watch } from 'vue';

import { defineStore } from 'pinia';

import {
  cancelTuningTaskApi,
  comparePidsApi,
  getTaskStatusApi,
  getTuningTaskDetailApi,
  identifyHistoryApi,
  previewSegmentsApi,
  simulateTuningApi,
} from '#/api/tuning';

/** sessionStorage 持久化 key */
const TUNING_FLOW_STORAGE_KEY = 'clpm:tuning-flow';

export const useTuningStore = defineStore('tuning', () => {
  // ---- 跨页面共享状态 ----

  /** 当前选中回路（从工作台/辨识页选择后共享） */
  const currentLoopId = ref<string>('');
  const currentLoopTagName = ref<string>('');

  /** 当前回路时间窗（ISO 字符串元组，P1-021 统一上下文头共享） */
  const currentLoopTimeRange = ref<[string, string] | null>(null);

  /** 历史辨识结果（Phase 2 异步任务完成后写入） */
  const identifyResult = ref<null | TuningApi.IdentifyHistoryResult>(null);

  /** 异步任务进度（轮询写入） */
  const taskProgress = ref<null | TuningApi.TaskProgress>(null);

  /** 候选 PID 列表（用于多 PID 对比仿真） */
  const pidCandidates = ref<TuningApi.PidParamsWithLabel[]>([]);

  /** 闭环仿真结果 */
  const simulationResult = ref<null | TuningApi.SimulationResult>(null);

  /** 可辨识片段预览结果 */
  const segments = ref<null | TuningApi.IdentifySegmentsResult>(null);

  // ---- v6.2 P1-019：流程门禁与步骤状态 ----

  /** 模型来源（IDENTIFICATION_RECORD / STEP_EXPERIMENT / MANUAL），Phase 0 门禁字段 */
  const modelSource = ref<string>('');
  /** 模型来源记录 ID（辨识记录或阶跃实验 ID） */
  const sourceRecordId = ref<string>('');
  /** 人工风险确认标记（C 级可信度需显式确认） */
  const riskConfirmed = ref<boolean>(false);
  /** 当前 stepper 步骤（0=辨识 1=整定 2=仿真） */
  const currentStep = ref<number>(0);

  /** 轮询定时器句柄 */
  let pollTimer: null | ReturnType<typeof setInterval> = null;

  // ---- 持久化（sessionStorage）----

  /** 将关键流程状态快照写入 sessionStorage */
  function _persist() {
    try {
      const snapshot = {
        currentLoopId: currentLoopId.value,
        currentLoopTagName: currentLoopTagName.value,
        currentLoopTimeRange: currentLoopTimeRange.value,
        identifyResult: identifyResult.value,
        pidCandidates: pidCandidates.value,
        simulationResult: simulationResult.value,
        segments: segments.value,
        taskProgress: taskProgress.value,
        modelSource: modelSource.value,
        sourceRecordId: sourceRecordId.value,
        riskConfirmed: riskConfirmed.value,
        currentStep: currentStep.value,
      };
      sessionStorage.setItem(TUNING_FLOW_STORAGE_KEY, JSON.stringify(snapshot));
    } catch {
      // sessionStorage 不可用（隐私模式等），静默降级
    }
  }

  /** 清除 sessionStorage 快照 */
  function _clearPersist() {
    try {
      sessionStorage.removeItem(TUNING_FLOW_STORAGE_KEY);
    } catch {
      // ignore
    }
  }

  /**
   * 从 sessionStorage 恢复流程状态（同标签页刷新主路径）
   * @returns 是否成功恢复
   */
  function restoreFromSession(): boolean {
    try {
      const raw = sessionStorage.getItem(TUNING_FLOW_STORAGE_KEY);
      if (!raw) return false;
      const snap = JSON.parse(raw) as Record<string, any>;
      currentLoopId.value = snap.currentLoopId ?? '';
      currentLoopTagName.value = snap.currentLoopTagName ?? '';
      currentLoopTimeRange.value = snap.currentLoopTimeRange ?? null;
      identifyResult.value = snap.identifyResult ?? null;
      pidCandidates.value = snap.pidCandidates ?? [];
      simulationResult.value = snap.simulationResult ?? null;
      segments.value = snap.segments ?? null;
      taskProgress.value = snap.taskProgress ?? null;
      modelSource.value = snap.modelSource ?? '';
      sourceRecordId.value = snap.sourceRecordId ?? '';
      riskConfirmed.value = snap.riskConfirmed ?? false;
      currentStep.value = snap.currentStep ?? 0;
      return true;
    } catch {
      return false;
    }
  }

  /**
   * 从后端 taskId 回显流程状态（跨标签页/设备兜底）
   *
   * 按 status 推导 currentStep，回填 identifyResult/pidCandidates。
   * model_source/source_record_id/riskConfirmed 未在后端持久化 → 置空，
   * 由子页检测到缺失时要求重新确认（Phase 0 门禁安全降级）。
   *
   * @returns 是否成功回显
   */
  async function restoreFromTask(taskId: string): Promise<boolean> {
    try {
      const detail = await getTuningTaskDetailApi(taskId);
      setCurrentLoop(detail.loopId, detail.tagName ?? '');

      // 按 status 推导 currentStep
      const stepMap: Record<string, number> = {
        DRAFT: 0,
        RUNNING: 0,
        IDENTIFIED: 1,
        SIMULATED: 2,
        COMPLETED: 2,
        INCONCLUSIVE: 0,
        ROLLED_BACK: 0,
      };
      currentStep.value = stepMap[detail.status] ?? 0;

      // 回填 identifyResult（从 task 元数据构造）
      if (detail.modelType) {
        identifyResult.value = {
          success: true,
          recordId: detail.id,
          modelType: detail.modelType,
          params: detail.modelParams ?? null,
          fittingScore: detail.fittingScore ?? null,
          confidenceLevel: detail.confidenceLevel ?? null,
          confidenceReason: detail.confidenceReason ?? null,
          excitationScore: detail.excitationScore ?? null,
          residualTestPassed: detail.residualTestPassed ?? null,
          identifyMethod: detail.identifyMethod ?? null,
          dataSource: detail.dataSource ?? null,
          tagName: detail.tagName ?? null,
        } as TuningApi.IdentifyHistoryResult;
      }

      // 回填 PID 候选
      const candidates: TuningApi.PidParamsWithLabel[] = [];
      if (detail.currentPid) {
        candidates.push({ label: '当前 PID', ...detail.currentPid });
      }
      if (detail.recommendedPid) {
        candidates.push({ label: '推荐 PID', ...detail.recommendedPid });
      }
      if (candidates.length > 0) {
        pidCandidates.value = candidates;
      }

      // 模型来源门禁字段未持久化 → 置空，子页要求重新确认
      modelSource.value = '';
      sourceRecordId.value = '';
      riskConfirmed.value = false;

      _persist();
      return true;
    } catch {
      return false;
    }
  }

  // 自动持久化：关键状态变更即写入 sessionStorage
  watch(
    [
      currentLoopId,
      currentLoopTagName,
      currentLoopTimeRange,
      identifyResult,
      pidCandidates,
      simulationResult,
      segments,
      modelSource,
      sourceRecordId,
      riskConfirmed,
      currentStep,
    ],
    () => _persist(),
    { deep: true },
  );

  // ---- Actions ----

  /** 设置当前回路 */
  function setCurrentLoop(loopId: string, tagName = '') {
    currentLoopId.value = loopId;
    currentLoopTagName.value = tagName;
  }

  /** 设置当前回路时间窗（ISO 字符串元组，P1-021 统一上下文头） */
  function setLoopTimeRange(range: [string, string] | null) {
    currentLoopTimeRange.value = range;
  }

  /** 设置模型来源（Phase 0 门禁字段） */
  function setModelSource(source: string, recordId = '', confirmed = false) {
    modelSource.value = source;
    sourceRecordId.value = recordId;
    riskConfirmed.value = confirmed;
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
    currentLoopTimeRange.value = null;
    identifyResult.value = null;
    taskProgress.value = null;
    pidCandidates.value = [];
    simulationResult.value = null;
    segments.value = null;
    modelSource.value = '';
    sourceRecordId.value = '';
    riskConfirmed.value = false;
    currentStep.value = 0;
    _clearPersist();
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
  function startPolling(
    taskId: string,
    onDone?: (progress: TuningApi.TaskProgress) => void,
  ) {
    stopPolling();
    pollTimer = setInterval(async () => {
      try {
        const progress = await getTaskStatusApi(taskId);
        taskProgress.value = progress;
        if (progress.status === 'SUCCESS') {
          // 任务成功，提取辨识结果
          if (progress.result) {
            identifyResult.value =
              progress.result as unknown as TuningApi.IdentifyHistoryResult;
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

  /** 多 PID 对比仿真（至少 2 组候选，V62-P0-030 独立 CompareRequest） */
  async function runComparePids(params: TuningApi.CompareRequest) {
    simulationResult.value = await comparePidsApi(params);
  }

  return {
    // state
    currentLoopId,
    currentLoopTagName,
    currentLoopTimeRange,
    identifyResult,
    taskProgress,
    pidCandidates,
    simulationResult,
    segments,
    modelSource,
    sourceRecordId,
    riskConfirmed,
    currentStep,
    // actions
    setCurrentLoop,
    setLoopTimeRange,
    setModelSource,
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
    restoreFromSession,
    restoreFromTask,
    $reset,
  };
});
