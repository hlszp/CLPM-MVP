/**
 * 整定工作台跨锚点共享状态（09 设计方案 §6.2）
 *
 * 单页 4 锚点数据流：辨识结果 → 整定矩阵 → 勾选进仿真 → 确认保存。
 * 由 workbench.vue 创建一次，以 props 传给各锚点组件（显式依赖，类型安全）。
 * 锚点顺序解锁由 canTune/canSimulate/canConfirm 计算属性驱动（Poka-Yoke）。
 */
import type { TuningApi } from '#/api/tuning';

import { computed, reactive, toRefs } from 'vue';

import { getLoopDetailApi } from '#/api/loop';
import {
  comparePidsApi,
  getTuningTaskStatusApi,
  identifyHistoryApi,
  identifyStepApi,
  saveTuningTaskApi,
  tuneMatrixApi,
  tuneSingleApi,
} from '#/api/tuning';

import { tuningAlgoLabel } from '../constants';

/** 辨识结果（统一历史/阶跃双路径的输出形态） */
export interface IdentifyOutcome {
  modelType: TuningApi.ModelType;
  params: TuningApi.ModelParams;
  fittingScore: number;
  confidenceLevel?: null | TuningApi.ConfidenceLevel;
  /** 服务端持久化的辨识记录 ID（阶跃路径 recordId / 历史路径 result.recordId） */
  recordId?: null | string;
  dataSource: 'HISTORY' | 'STEP_EXPERIMENT';
  identifyMethod?: null | string;
  reason?: null | string;
}

/** 矩阵行（后端 TuneMatrixRow + 前端勾选/微调状态） */
export interface MatrixRow {
  algorithm: TuningApi.TuningAlgorithm;
  ok: boolean;
  pid: null | TuningApi.PidParams;
  error?: string;
  checked: boolean;
  /** 算法参数微调值（IMC/LAMBDA: lambdaRatio；SIMC: tauCRatio） */
  paramValue: number;
  recomputing: boolean;
}

/** 仿真候选组（当前 PID + 勾选推荐组） */
export interface SimCandidate {
  label: string;
  pid: TuningApi.PidParams;
  isCurrent: boolean;
  /** 推荐组对应的矩阵算法 key（当前 PID 组为空）；显示文案与逻辑匹配解耦 */
  algorithm?: TuningApi.TuningAlgorithm;
}

const ALGO_PARAM_KEY: Record<string, string> = {
  IMC: 'lambdaRatio',
  LAMBDA: 'lambdaRatio',
  SIMC: 'tauCRatio',
};

export function useTuningWorkbench() {
  const state = reactive({
    // 回路
    loopId: '' as string,
    currentPid: null as null | TuningApi.PidParams,
    currentPidMissing: false,
    // ① 辨识
    identifyPath: 'HISTORY' as 'HISTORY' | 'STEP',
    timeRange: null as [string, string] | null,
    identifying: false,
    identifyProgress: 0,
    identifyStage: '' as string,
    identifyTaskId: '' as string,
    identifyError: '' as string,
    outcome: null as IdentifyOutcome | null,
    // ② 矩阵
    matrixRows: [] as MatrixRow[],
    matrixLoading: false,
    matrixError: '' as string,
    // ③ 仿真
    simulating: false,
    simResult: null as null | TuningApi.SimulationResult,
    simCandidates: [] as SimCandidate[],
    simError: '' as string,
    // ④ 确认
    finalLabel: '' as string,
    saving: false,
    savedRecordId: '' as string,
  });

  // ===== 计算属性（锚点解锁门禁）=====
  const canTune = computed(
    () =>
      !!state.outcome &&
      state.outcome.confidenceLevel !== 'D' &&
      state.outcome.confidenceLevel !== 'E',
  );
  const checkedRows = computed(() =>
    state.matrixRows.filter((r) => r.checked && r.ok && r.pid),
  );
  const canSimulate = computed(
    () =>
      canTune.value &&
      checkedRows.value.length > 0 &&
      checkedRows.value.length <= 2,
  );
  const canConfirm = computed(
    () => !!state.simResult && !!state.finalLabel && !state.saving,
  );

  // ===== 回路 =====
  /** 清除回路选择（返回回路总览；切换装置节点时调用） */
  function clearLoop() {
    state.loopId = '';
    state.currentPid = null;
    state.currentPidMissing = false;
    resetDownstream();
  }

  async function selectLoop(loopId: string) {
    state.loopId = loopId;
    resetDownstream();
    // 当前 PID（回路详情 runtimeParams，优先 Redis 实时缓存口径由后端保证）
    state.currentPid = null;
    state.currentPidMissing = false;
    try {
      const detail = await getLoopDetailApi(loopId);
      const rp = detail.runtimeParams as {
        pidD?: null | number;
        pidI?: null | number;
        pidP?: null | number;
      };
      if (rp && rp.pidP != null && rp.pidI != null) {
        state.currentPid = { kp: rp.pidP, ti: rp.pidI, td: rp.pidD ?? 0 };
      } else {
        state.currentPidMissing = true;
      }
    } catch {
      state.currentPidMissing = true;
    }
  }

  /** 回路切换/重新辨识时清空下游状态 */
  function resetDownstream() {
    state.outcome = null;
    state.identifyError = '';
    state.identifyProgress = 0;
    state.identifyStage = '';
    state.identifyTaskId = '';
    state.matrixRows = [];
    state.matrixError = '';
    state.simResult = null;
    state.simCandidates = [];
    state.simError = '';
    state.finalLabel = '';
    state.savedRecordId = '';
  }

  // ===== ① 过程辨识 =====
  async function runIdentify() {
    if (!state.loopId || !state.timeRange) return;
    state.identifyError = '';
    state.outcome = null;
    state.matrixRows = [];
    state.simResult = null;
    state.simCandidates = [];
    state.finalLabel = '';
    state.identifying = true;
    state.identifyProgress = 0;
    const [startTime, endTime] = state.timeRange;
    try {
      if (state.identifyPath === 'STEP') {
        const res = await identifyStepApi({
          loopId: state.loopId,
          startTime,
          endTime,
          modelType: 'FOPDT',
        });
        state.outcome = {
          modelType: res.modelType,
          params: res.params,
          fittingScore: res.fittingScore,
          confidenceLevel: null,
          recordId: res.recordId ?? null,
          dataSource: 'STEP_EXPERIMENT',
          identifyMethod: 'STEP_TWO_POINT',
        };
      } else {
        const asyncRes = await identifyHistoryApi({
          loopId: state.loopId,
          startTime,
          endTime,
        });
        state.identifyTaskId = asyncRes.taskId;
        await pollIdentifyTask(asyncRes.taskId);
      }
      // 辨识成功且可信度放行 → 自动计算矩阵
      if (state.outcome && canTune.value) {
        await runMatrix();
      }
    } catch (error: any) {
      state.identifyError = error?.message || '辨识失败';
    } finally {
      state.identifying = false;
    }
  }

  async function pollIdentifyTask(taskId: string) {
    // 细粒度进度轮询（2s 间隔；后端按阶段更新 progress）
    for (;;) {
      await new Promise((r) => setTimeout(r, 2000));
      const p = await getTuningTaskStatusApi(taskId);
      state.identifyProgress = p.progress ?? 0;
      state.identifyStage = p.stage ?? '';
      if (p.status === 'SUCCESS') {
        const r = p.result ?? {};
        if (!r.modelType || !r.params) {
          throw new Error(r.reason || '辨识未产出模型（数据不足或激励不足）');
        }
        state.outcome = {
          modelType: r.modelType,
          params: r.params,
          fittingScore: r.fittingScore ?? 0,
          confidenceLevel: r.confidenceLevel ?? null,
          recordId: (r as any).recordId ?? null,
          dataSource: 'HISTORY',
          identifyMethod: (r as any).identifyMethod ?? null,
          reason: r.reason ?? null,
        };
        return;
      }
      if (p.status === 'FAILED') {
        throw new Error(p.error || '历史辨识任务失败');
      }
    }
  }

  // ===== ② 整定矩阵 =====
  function matrixRequestBase() {
    const o = state.outcome!;
    return {
      modelType: o.modelType,
      modelParams: o.params,
      currentPid: state.currentPid,
      loopId: state.loopId,
      sourceRecordId: o.recordId ?? undefined,
      // 阶跃记录后端要求声明 STEP_EXPERIMENT 来源（authorize_tuning_model 门禁）
      modelSource: o.recordId
        ? o.dataSource === 'STEP_EXPERIMENT'
          ? 'STEP_EXPERIMENT'
          : 'IDENTIFICATION_RECORD'
        : 'MANUAL',
      riskConfirmed: true,
    };
  }

  async function runMatrix() {
    state.matrixLoading = true;
    state.matrixError = '';
    try {
      const res = await tuneMatrixApi(matrixRequestBase());
      state.matrixRows = res.rows.map((r) => ({
        algorithm: r.algorithm,
        ok: r.ok,
        pid: r.ok ? (r.result?.recommendedPid ?? null) : null,
        error: r.error,
        checked: false,
        paramValue: 1,
        recomputing: false,
      }));
    } catch (error: any) {
      state.matrixError = error?.message || '矩阵计算失败';
    } finally {
      state.matrixLoading = false;
    }
  }

  /** 行内算法参数微调后单行重算 */
  async function recomputeRow(row: MatrixRow) {
    row.recomputing = true;
    try {
      const paramKey = ALGO_PARAM_KEY[row.algorithm];
      const res = await tuneSingleApi({
        ...matrixRequestBase(),
        algorithm: row.algorithm,
        algorithmParams: paramKey ? { [paramKey]: row.paramValue } : undefined,
      });
      row.pid = res.recommendedPid;
      row.ok = true;
      row.error = undefined;
    } catch (error: any) {
      row.ok = false;
      row.error = error?.message || '重算失败';
    } finally {
      row.recomputing = false;
    }
  }

  /** 勾选控制：最多 2 组 */
  function toggleRow(row: MatrixRow) {
    if (!row.ok || !row.pid) return;
    if (!row.checked && checkedRows.value.length >= 2) return; // 超出禁选
    row.checked = !row.checked;
  }

  // ===== ③ 闭环仿真 =====
  async function runSimulate() {
    if (!canSimulate.value || !state.outcome) return;
    state.simulating = true;
    state.simError = '';
    state.simResult = null;
    try {
      const candidates: TuningApi.PidParamsWithLabel[] = [];
      const simCandidates: SimCandidate[] = [];
      if (state.currentPid) {
        candidates.push({ label: '当前 PID', ...state.currentPid });
        simCandidates.push({
          label: '当前 PID',
          pid: state.currentPid,
          isCurrent: true,
        });
      }
      for (const row of checkedRows.value) {
        const paramKey = ALGO_PARAM_KEY[row.algorithm];
        const label = paramKey
          ? `${tuningAlgoLabel(row.algorithm)}·${paramKey}=${row.paramValue}`
          : tuningAlgoLabel(row.algorithm);
        candidates.push({ label, ...row.pid! });
        simCandidates.push({
          label,
          pid: row.pid!,
          isCurrent: false,
          algorithm: row.algorithm,
        });
      }
      const res = await comparePidsApi({
        modelType: state.outcome.modelType,
        modelParams: state.outcome.params,
        pidCandidates: candidates,
        currentPid: state.currentPid ?? undefined,
        loopId: state.loopId,
        sourceRecordId: state.outcome.recordId ?? undefined,
        modelSource: state.outcome.recordId
          ? state.outcome.dataSource === 'STEP_EXPERIMENT'
            ? 'STEP_EXPERIMENT'
            : 'IDENTIFICATION_RECORD'
          : 'MANUAL',
        riskConfirmed: true,
      });
      state.simResult = res;
      state.simCandidates = simCandidates;
      state.finalLabel = simCandidates.find((c) => !c.isCurrent)?.label ?? '';
    } catch (error: any) {
      state.simError = error?.message || '仿真失败';
    } finally {
      state.simulating = false;
    }
  }

  // ===== ④ 保存方案 =====
  async function savePlan() {
    if (!canConfirm.value || !state.outcome) return null;
    state.saving = true;
    try {
      const chosen = state.simCandidates.find(
        (c) => c.label === state.finalLabel,
      );
      if (!chosen || chosen.isCurrent)
        throw new Error('请选择推荐参数组作为最终方案');
      // 用候选组携带的算法 key 精确匹配矩阵行（不再依赖显示文案 startsWith）
      const algoRow = state.matrixRows.find(
        (r) => r.algorithm === chosen.algorithm,
      );
      const res = await saveTuningTaskApi({
        loopId: state.loopId,
        modelType: state.outcome.modelType,
        modelParams: state.outcome.params,
        algorithm: algoRow?.algorithm ?? 'IMC',
        recommendedPid: chosen.pid,
        currentPid: state.currentPid ?? undefined,
        fittingScore: state.outcome.fittingScore,
        simulationResult: state.simResult as unknown as Record<string, any>,
        status: 'SIMULATED',
        identifyMethod: state.outcome.identifyMethod ?? undefined,
        dataSource: state.outcome.dataSource,
        confidenceLevel: state.outcome.confidenceLevel ?? undefined,
      });
      state.savedRecordId = res.id;
      return res.id;
    } finally {
      state.saving = false;
    }
  }

  return {
    ...toRefs(state),
    canTune,
    canSimulate,
    canConfirm,
    checkedRows,
    clearLoop,
    selectLoop,
    runIdentify,
    runMatrix,
    recomputeRow,
    toggleRow,
    runSimulate,
    savePlan,
    resetDownstream,
  };
}

export type TuningWorkbenchContext = ReturnType<typeof useTuningWorkbench>;
