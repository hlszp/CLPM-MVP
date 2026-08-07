/**
 * 工作台三区任务运行器（单页四区重构 · 2026-08-07）
 *
 * 统一评估 / 诊断 / 整定三行的"发起任务 → 轮询进度 → 终态反写"流程。
 * 每区独立维护 taskId / isRunning / progress / progressStage / error 状态，
 * 终态 SUCCESS 时回调对应的数据重载函数，实现"任务完成即反写"。
 *
 * 三区 API 对照：
 *   评估  triggerBackfillApi / triggerCustomEvaluateApi → getTaskDetailApi
 *   诊断  triggerDiagnosisApi                          → getDiagnosisTaskDetailApi
 *   整定  identifyHistoryApi                            → getTaskStatusApi
 *
 * 轮询策略：递归 setTimeout（防堆积），3s 间隔，页面隐藏时暂停（visibilitychange）。
 * 终态（SUCCESS/FAILED/CANCELLED）自动停止；组件卸载时清理所有定时器。
 */
import type { Ref } from 'vue';

import { onScopeDispose, reactive, type UnwrapNestedRefs } from 'vue';

import { message } from 'ant-design-vue';
import dayjs from 'dayjs';

import {
  getDiagnosisDetailApi,
  getDiagnosisTaskDetailApi,
  triggerDiagnosisApi,
} from '#/api/diagnosis';
import { getLoopConfidenceLatestApi } from '#/api/metric';
import {
  getTaskDetailApi,
  triggerBackfillApi,
  triggerCustomEvaluateApi,
} from '#/api/task';
import {
  getTaskStatusApi,
  getTuningTasksApi,
  identifyHistoryApi,
} from '#/api/tuning';

// ===== 类型 =====
export interface SectionTaskState {
  /** 当前任务 ID（运行中或终态未清除时保留） */
  taskId: null | string;
  /** 是否运行中（PENDING/RUNNING） */
  isRunning: boolean;
  /** 进度 0~1（null=后端未上报） */
  progress: null | number;
  /** 当前阶段文案 */
  progressStage: null | string;
  /** 错误信息（终态 FAILED 时） */
  error: null | string;
}

export type AssessmentMode = 'backfill' | 'custom';

export interface AssessmentTriggerParams {
  mode: AssessmentMode;
  tsStart: string;
  tsEnd: string;
  /** 任意时段模式的指标子集（整点回算模式忽略） */
  metrics?: string[];
  /** 整点回算模式的任务标题 */
  title?: string;
}

export interface DiagnosisTriggerParams {
  startTime?: string;
  endTime?: string;
}

export interface TuningTriggerParams {
  startTime: string;
  endTime: string;
}

export interface WorkbenchTaskRunnerCallbacks {
  /** 评估任务成功后重载评估数据 */
  onAssessDone?: (loopId: string) => void;
  /** 诊断任务成功后重载诊断数据 */
  onDiagnosisDone?: (loopId: string) => void;
  /** 整定任务成功后重载整定数据 */
  onTuningDone?: (loopId: string) => void;
}

const TERMINAL_STATUSES = new Set([
  'CANCELLED',
  'COMPLETED',
  'FAILED',
  'SUCCESS',
]);

function createState(): UnwrapNestedRefs<SectionTaskState> {
  return reactive({
    taskId: null,
    isRunning: false,
    progress: null,
    progressStage: null,
    error: null,
  });
}

export function useWorkbenchTaskRunner(
  loopId: Ref<null | string>,
  callbacks: WorkbenchTaskRunnerCallbacks = {},
) {
  const assessment = createState();
  const diagnosis = createState();
  const tuning = createState();

  // 每区独立的定时器（递归 setTimeout）
  const timers: Record<
    'assessment' | 'diagnosis' | 'tuning',
    null | ReturnType<typeof setTimeout>
  > = {
    assessment: null,
    diagnosis: null,
    tuning: null,
  };

  /** 页面隐藏时暂停所有轮询 */
  let hiddenPaused = false;
  function handleVisibility() {
    if (document.hidden) {
      hiddenPaused = true;
    } else if (hiddenPaused) {
      hiddenPaused = false;
      // 恢复时立即补跑一次运行中的任务
      if (assessment.isRunning && assessment.taskId)
        pollAssessment(assessment.taskId);
      if (diagnosis.isRunning && diagnosis.taskId)
        pollDiagnosis(diagnosis.taskId);
      if (tuning.isRunning && tuning.taskId) pollTuning(tuning.taskId);
    }
  }
  if (typeof document !== 'undefined') {
    document.addEventListener('visibilitychange', handleVisibility);
  }

  function clearTimer(section: 'assessment' | 'diagnosis' | 'tuning') {
    if (timers[section] !== null) {
      clearTimeout(timers[section]!);
      timers[section] = null;
    }
  }

  function clearAll() {
    (
      Object.keys(timers) as Array<'assessment' | 'diagnosis' | 'tuning'>
    ).forEach((k) => clearTimer(k));
  }

  onScopeDispose(() => {
    clearAll();
    if (typeof document !== 'undefined') {
      document.removeEventListener('visibilitychange', handleVisibility);
    }
  });

  // ===== 评估任务 =====
  async function triggerAssessment(
    params: AssessmentTriggerParams,
  ): Promise<boolean> {
    if (!loopId.value) return false;
    clearTimer('assessment');
    assessment.taskId = null;
    assessment.isRunning = true;
    assessment.progress = 0;
    assessment.progressStage = '提交任务…';
    assessment.error = null;

    try {
      let taskId: string;
      if (params.mode === 'backfill') {
        const res = await triggerBackfillApi({
          title: params.title || `工作台回算 ${dayjs().format('MM-DD HH:mm')}`,
          tsStart: params.tsStart,
          tsEnd: params.tsEnd,
          loopIds: [loopId.value],
        });
        taskId = (res as { taskId: string }).taskId;
      } else {
        const res = await triggerCustomEvaluateApi({
          loopIds: [loopId.value],
          metrics: params.metrics || [],
          tsStart: params.tsStart,
          tsEnd: params.tsEnd,
        });
        taskId = res.taskId;
      }
      assessment.taskId = taskId;
      assessment.progressStage = '已提交，等待执行…';
      pollAssessment(taskId);
      return true;
    } catch (error: any) {
      assessment.isRunning = false;
      assessment.progress = null;
      assessment.error = error?.message ?? '提交评估任务失败';
      message.error(assessment.error);
      return false;
    }
  }

  async function pollAssessment(taskId: string) {
    if (document.hidden) return; // 隐藏时暂停，恢复时由 visibility 补跑
    try {
      const detail = await getTaskDetailApi(taskId);
      assessment.progress = detail.progress ?? assessment.progress;
      assessment.progressStage =
        detail.currentStage ?? assessment.progressStage;
      if (TERMINAL_STATUSES.has(detail.status)) {
        assessment.isRunning = false;
        clearTimer('assessment');
        if (detail.status === 'SUCCESS') {
          assessment.progress = 1;
          message.success('评估完成');
          callbacks.onAssessDone?.(loopId.value!);
        } else {
          assessment.error = detail.errorMessage || `任务${detail.status}`;
          if (detail.status === 'FAILED')
            message.error('评估失败：' + assessment.error);
        }
        return;
      }
    } catch {
      // 单次查询失败不终止，继续轮询
    }
    timers.assessment = setTimeout(() => pollAssessment(taskId), 3000);
  }

  // ===== 诊断任务 =====
  async function triggerDiagnosis(
    params: DiagnosisTriggerParams = {},
  ): Promise<boolean> {
    if (!loopId.value) return false;
    clearTimer('diagnosis');
    diagnosis.taskId = null;
    diagnosis.isRunning = true;
    diagnosis.progress = 0;
    diagnosis.progressStage = '提交诊断任务…';
    diagnosis.error = null;

    try {
      const res = await triggerDiagnosisApi({
        loopIds: [loopId.value],
        startTime: params.startTime,
        endTime: params.endTime,
      });
      const task = res.tasks?.[0];
      if (!task?.taskId) {
        throw new Error('诊断任务未创建');
      }
      diagnosis.taskId = task.taskId;
      diagnosis.progressStage = '已提交，等待执行…';
      pollDiagnosis(task.taskId);
      return true;
    } catch (error: any) {
      diagnosis.isRunning = false;
      diagnosis.progress = null;
      diagnosis.error = error?.message ?? '提交诊断任务失败';
      message.error(diagnosis.error);
      return false;
    }
  }

  async function pollDiagnosis(taskId: string) {
    if (document.hidden) return;
    try {
      const detail = await getDiagnosisTaskDetailApi(taskId);
      // 诊断任务详情无 progress 字段，用状态推断
      diagnosis.progressStage =
        detail.status === 'RUNNING' ? '诊断分析中…' : diagnosis.progressStage;
      if (TERMINAL_STATUSES.has(detail.status)) {
        diagnosis.isRunning = false;
        diagnosis.progress = 1;
        clearTimer('diagnosis');
        if (detail.status === 'SUCCESS') {
          message.success('诊断完成');
          callbacks.onDiagnosisDone?.(loopId.value!);
        } else {
          diagnosis.error = `任务${detail.status}`;
          if (detail.status === 'FAILED') message.error('诊断失败');
        }
        return;
      }
      diagnosis.progress =
        diagnosis.progress === null
          ? 0.3
          : Math.min(0.9, diagnosis.progress + 0.1);
    } catch {
      // 单次失败继续轮询
    }
    timers.diagnosis = setTimeout(() => pollDiagnosis(taskId), 3000);
  }

  // ===== 整定任务 =====
  async function triggerTuning(params: TuningTriggerParams): Promise<boolean> {
    if (!loopId.value) return false;
    clearTimer('tuning');
    tuning.taskId = null;
    tuning.isRunning = true;
    tuning.progress = 0;
    tuning.progressStage = '提交辨识任务…';
    tuning.error = null;

    try {
      const res = await identifyHistoryApi({
        loopId: loopId.value,
        startTime: params.startTime,
        endTime: params.endTime,
      });
      tuning.taskId = res.taskId;
      tuning.progressStage = '已提交，等待执行…';
      pollTuning(res.taskId);
      return true;
    } catch (error: any) {
      tuning.isRunning = false;
      tuning.progress = null;
      tuning.error = error?.message ?? '提交整定任务失败';
      message.error(tuning.error);
      return false;
    }
  }

  async function pollTuning(taskId: string) {
    if (document.hidden) return;
    try {
      const detail = await getTaskStatusApi(taskId);
      tuning.progress =
        detail.progress == null ? tuning.progress : detail.progress / 100;
      tuning.progressStage = detail.stage ?? tuning.progressStage;
      if (TERMINAL_STATUSES.has(detail.status)) {
        tuning.isRunning = false;
        clearTimer('tuning');
        if (detail.status === 'SUCCESS') {
          tuning.progress = 1;
          message.success('整定辨识完成');
          callbacks.onTuningDone?.(loopId.value!);
        } else {
          tuning.error = detail.error || `任务${detail.status}`;
          if (detail.status === 'FAILED')
            message.error('整定失败：' + tuning.error);
        }
        return;
      }
    } catch {
      // 单次失败继续轮询
    }
    timers.tuning = setTimeout(() => pollTuning(taskId), 3000);
  }

  /** 停止所有运行中的任务轮询（不取消后端任务） */
  function stopAll() {
    clearAll();
    assessment.isRunning = false;
    diagnosis.isRunning = false;
    tuning.isRunning = false;
  }

  return {
    assessment,
    diagnosis,
    tuning,
    triggerAssessment,
    triggerDiagnosis,
    triggerTuning,
    stopAll,
  };
}

// ===== 默认回调：重载三区数据（供 workbench.vue 直接使用） =====
export function createDefaultReloadCallbacks(
  setAssessment: (v: any) => void,
  setDiagnosis: (v: any) => void,
  setTuning: (v: any) => void,
) {
  return {
    onAssessDone: async (loopId: string) => {
      try {
        const latest = await getLoopConfidenceLatestApi(loopId).catch(
          () => null,
        );
        setAssessment(latest);
      } catch {
        // 忽略
      }
    },
    onDiagnosisDone: async (loopId: string) => {
      try {
        const detail = await getDiagnosisDetailApi(loopId).catch(() => null);
        setDiagnosis(detail);
      } catch {
        // 忽略
      }
    },
    onTuningDone: async (loopId: string) => {
      try {
        const res = await getTuningTasksApi({ loopId, page: 1, pageSize: 5 });
        setTuning(res.items?.[0] ?? null);
      } catch {
        // 忽略
      }
    },
  };
}
