/**
 * 整定工作台跨锚点共享状态（09 设计方案 §6.2）
 *
 * 单页 4 锚点数据流：辨识结果 → 整定矩阵 → 勾选进仿真 → 确认保存。
 * 由 workbench.vue 创建一次，以 props 传给各锚点组件（显式依赖，类型安全）。
 * 锚点顺序解锁由 canTune/canSimulate/canConfirm 计算属性驱动（Poka-Yoke）。
 */
import type { TuningApi } from '#/api/tuning';

import { computed, reactive, toRefs } from 'vue';

import { message } from 'ant-design-vue';

import { getLoopDetailApi, getLoopMonitorListApi } from '#/api/loop';
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

/** P2 IA优化：fitness tag 中文映射（与 fitness-badge/诊断 对齐） */
const FITNESS_TAG_CN: Record<string, string> = {
  T_UNKNOWN: '未知',
  T_LOCAL_DATA_MISSING: '本地无历史数据',
  T_LOW_COVERAGE_7D: '近 7 日覆盖不足 50%',
  T_LOW_COVERAGE_30D: '近 30 日覆盖不足 50%',
  T_BAD_QUALITY: '数据质量差（PV 坏值/不确定）',
  T_MODE_NOT_AUTO: '当前处于手动控制模式',
  T_SETPOINT_MISSING: 'OPC 未绑定 SP 位号',
  T_OUTPUT_MISSING: 'OPC 未绑定 OP 位号',
  T_PID_PARAMS_INCOMPLETE: 'OPC 未绑定 P/I/D 位号',
  T_CONSTANT_SETPOINT: 'SP 长时间未变（如 30 天全恒定）',
  T_OOS_PV: 'PV 量程外点比例过高',
  T_BAD_OP_RANGE: 'OP 长期顶边或贴底（<5% / >95%）',
  T_DAMPED_OSC: '存在阻尼振荡趋势',
  T_SUSTAINED_OSC: '存在持续振荡趋势',
  T_VALVE_STICTION: '阀门疑似粘滞',
  T_DEADTIME_HIGH: '纯滞后/惯性比偏高',
  T_DRIFT: 'SP-PV 长期偏移（均值偏差）',
  T_HIGH_PV_NOISE: 'PV 高频噪声过大',
};
const tagToCn = (t: string) => FITNESS_TAG_CN[t] ?? t;
const tagsToText = (tags: string[]) => tags.map((t) => tagToCn(t)).join('、');

/** 辨识结果（统一历史/阶跃双路径的输出形态；MANUAL=人工修改后的模型） */
export interface IdentifyOutcome {
  modelType: TuningApi.ModelType;
  params: TuningApi.ModelParams;
  fittingScore: number;
  confidenceLevel?: null | TuningApi.ConfidenceLevel;
  /** 服务端持久化的辨识记录 ID（阶跃路径 recordId / 历史路径 result.recordId）；人工修改后置空 */
  recordId?: null | string;
  dataSource: 'HISTORY' | 'MANUAL' | 'STEP_EXPERIMENT';
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

/** 仿真最多可勾选的推荐参数组数（+ 当前 PID = 曲线上限 6 条） */
const MAX_SIM_CANDIDATES = 5;

export function useTuningWorkbench() {
  const state = reactive({
    // 回路
    loopId: '' as string,
    currentPid: null as null | TuningApi.PidParams,
    currentPidMissing: false,
    /** P2 IA优化：回路适用性等级 L0/L1/L2/L3/L4/L5（空=未加载） */
    fitnessLevel: null as null | string,
    /** P2 IA优化：回路适用性原因标签列表 */
    fitnessTags: [] as string[],
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

  // ===== P2 IA优化：fitness 门禁（锚点解锁 + 入口按钮 disabled/Tooltip/Toast）=====
  const isFitnessL0L1 = computed(
    () => state.fitnessLevel === 'L0' || state.fitnessLevel === 'L1',
  );
  const isFitnessL2 = computed(() => state.fitnessLevel === 'L2');
  /** L0/L1 → 禁止进入下游按钮；L2/L3/L4/L5/null → 放行 */
  const tuningDisabled = computed(() => isFitnessL0L1.value);
  /** Tooltip 文案（L0/L1 禁用原因） */
  const tuningDisabledReason = computed<string>(() => {
    if (!isFitnessL0L1.value) return '';
    const tags = state.fitnessTags?.length > 0
      ? tagsToText(state.fitnessTags)
      : '适用性不足';
    return `不可整定（${state.fitnessLevel}）：${tags}。先消除异常来源后再做整定。`;
  });
  /** Tooltip 文案（L2 条件异常警告，不禁止，仅在按钮 hover 时提示） */
  const tuningWarningReason = computed<string>(() => {
    if (!isFitnessL2.value) return '';
    const tags = state.fitnessTags?.length > 0
      ? tagsToText(state.fitnessTags)
      : '控制条件异常';
    return `L2 条件异常：${tags}。当前控制状态可能影响整定结论，建议先修正再做整定。`;
  });

  /** 从 monitor/loops endpoint 精确拉取单回路 fitness（失败不阻塞，降级为 null） */
  async function loadFitness(loopId: string): Promise<void> {
    try {
      const res = await getLoopMonitorListApi({
        loopId,
        page: 1,
        pageSize: 1,
      });
      const item = res.items?.[0];
      state.fitnessLevel = (item?.fitnessLevel as null | string) ?? null;
      state.fitnessTags = Array.isArray(item?.fitnessTags)
        ? (item.fitnessTags as string[])
        : [];
    } catch {
      // 降级：fitness 取空（按 L3/L4/L5 放行，不阻塞已有业务）
      state.fitnessLevel = null;
      state.fitnessTags = [];
    }
  }

  /** G3：整定入口按钮点击时弹 Toast 提示 fitness 状态（不阻止流程） */
  function showFitnessToast(
    kind: 'identify' | 'optimize' | 'simulate' = 'optimize',
  ): void {
    const stepMap = {
      identify: '开始辨识',
      simulate: '开始仿真',
      optimize: '调参优化',
    } as const;
    const step = stepMap[kind];
    const level = state.fitnessLevel ?? '未评定';
    if (isFitnessL0L1.value) {
      // L0/L1 正常被 disabled，用户无法点进来；如果到了这里，就是降级情况，依旧提示
      const reason = state.fitnessTags.length > 0
        ? tagsToText(state.fitnessTags)
        : '适用性不足';
      message.warning(`【${step}】回路适用性不足（${state.fitnessLevel}）：${reason}`);
    } else if (isFitnessL2.value) {
      const reason = state.fitnessTags.length > 0
        ? tagsToText(state.fitnessTags)
        : '控制条件异常';
      message.warning({
        content: `【${step}】L2 条件异常（${state.fitnessLevel}）：${reason}。整定结果可能受控制状态干扰，请优先消除异常后重做。`,
        duration: 5,
      });
    } else if (level === 'L3' || level === 'L4' || level === 'L5') {
      message.success(`【${step}】当前适用性等级 = ${level}，可正常整定。`);
    } else {
      // 未评定（接口不通或该回路尚未有评定）→ 仅提示
      message.info(`【${step}】尚未评定适用性等级。`);
    }
  }

  // ===== 计算属性（锚点解锁门禁）=====
  const canTune = computed(
    () =>
      !!state.outcome &&
      state.outcome.confidenceLevel !== 'D' &&
      state.outcome.confidenceLevel !== 'E' &&
      !tuningDisabled.value, // P2 IA优化：L0/L1 禁入下游
  );
  const checkedRows = computed(() =>
    state.matrixRows.filter(
      (r) =>
        r.checked &&
        r.ok &&
        r.pid &&
        // 手动整定行输入清空（null）时视为无效，不可进入仿真
        r.pid.kp != null &&
        r.pid.ti != null,
    ),
  );
  const canSimulate = computed(
    () =>
      canTune.value &&
      checkedRows.value.length > 0 &&
      checkedRows.value.length <= MAX_SIM_CANDIDATES,
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
    state.fitnessLevel = null;
    state.fitnessTags = [];
    resetDownstream();
  }

  async function selectLoop(loopId: string) {
    state.loopId = loopId;
    resetDownstream();
    // 当前 PID（回路详情 runtimeParams，优先 Redis 实时缓存口径由后端保证）
    state.currentPid = null;
    state.currentPidMissing = false;
    state.fitnessLevel = null;
    state.fitnessTags = [];
    // PID 详情 + fitness 并行拉取
    try {
      const [detail] = await Promise.all([
        getLoopDetailApi(loopId),
        loadFitness(loopId),
      ]);
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
    // P2 IA优化：L0/L1 硬拦截（防止 UI 绕过 disabled 直接调函数的场景）
    if (tuningDisabled.value) {
      message.error(tuningDisabledReason.value || '当前回路适用性不足，不允许辨识。');
      return;
    }
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
        ? (o.dataSource === 'STEP_EXPERIMENT'
          ? 'STEP_EXPERIMENT'
          : 'IDENTIFICATION_RECORD')
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
      // 第 6 行：手动整定（P/I/D 工程师手工设定；预填当前 PID 便于起步）
      state.matrixRows.push({
        algorithm: 'MANUAL_TUNING',
        ok: true,
        pid: state.currentPid
          ? { ...state.currentPid }
          : { kp: 1, ti: 10, td: 0 },
        checked: false,
        paramValue: 1,
        recomputing: false,
      });
    } catch (error: any) {
      state.matrixError = error?.message || '矩阵计算失败';
    } finally {
      state.matrixLoading = false;
    }
  }

  /** 人工修改过程模型：替换 outcome 并按新模型重算矩阵（走 MANUAL 来源门禁） */
  async function applyManualModel(
    modelType: TuningApi.ModelType,
    params: TuningApi.ModelParams,
  ) {
    state.outcome = {
      modelType,
      params: { ...params },
      fittingScore: 0,
      confidenceLevel: null,
      recordId: null,
      dataSource: 'MANUAL',
      identifyMethod: null,
      reason: '人工修改模型（脱离辨识记录）',
    };
    state.simResult = null;
    state.simCandidates = [];
    state.finalLabel = '';
    state.savedRecordId = '';
    await runMatrix();
  }

  /** 行内算法参数微调后单行重算（手动整定行不经算法计算，无重算） */
  async function recomputeRow(row: MatrixRow) {
    if (row.algorithm === 'MANUAL_TUNING') return;
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

  /** 勾选控制：最多 MAX_SIM_CANDIDATES 组 */
  function toggleRow(row: MatrixRow) {
    if (!row.ok || !row.pid) return;
    if (!row.checked && checkedRows.value.length >= MAX_SIM_CANDIDATES) return; // 超出禁选
    row.checked = !row.checked;
  }

  // ===== ③ 闭环仿真 =====
  async function runSimulate() {
    if (!canSimulate.value || !state.outcome) return;
    // P2 IA优化：L0/L1 硬拦截（防 UI 绕过 disabled 直接调函数的场景）
    if (tuningDisabled.value) {
      message.error(tuningDisabledReason.value || '当前回路适用性不足，不允许仿真。');
      return;
    }
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
          ? (state.outcome.dataSource === 'STEP_EXPERIMENT'
            ? 'STEP_EXPERIMENT'
            : 'IDENTIFICATION_RECORD')
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
      const isManualModel = state.outcome.dataSource === 'MANUAL';
      const res = await saveTuningTaskApi({
        loopId: state.loopId,
        modelType: state.outcome.modelType,
        modelParams: state.outcome.params,
        algorithm: algoRow?.algorithm ?? 'IMC',
        recommendedPid: chosen.pid,
        currentPid: state.currentPid ?? undefined,
        // 人工修改模型无拟合度可言；辨识来源元数据仅服务端辨识链结果携带
        fittingScore: isManualModel ? undefined : state.outcome.fittingScore,
        simulationResult: state.simResult as unknown as Record<string, any>,
        status: 'SIMULATED',
        identifyMethod: isManualModel
          ? undefined
          : (state.outcome.identifyMethod ?? undefined),
        dataSource: isManualModel ? undefined : state.outcome.dataSource,
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
    applyManualModel,
    recomputeRow,
    toggleRow,
    runSimulate,
    savePlan,
    resetDownstream,
    // P2 IA优化：fitness 门禁（各 section 按钮 disabled/Tooltip/Toast）
    isFitnessL0L1,
    isFitnessL2,
    tuningDisabled,
    tuningDisabledReason,
    tuningWarningReason,
    showFitnessToast,
  };
}

export type TuningWorkbenchContext = ReturnType<typeof useTuningWorkbench>;
