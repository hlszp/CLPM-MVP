/**
 * 整定工作台跨锚点共享状态（09 设计方案 §6.2）
 *
 * 单页 4 锚点数据流：辨识结果 → 整定矩阵 → 勾选进仿真 → 确认保存。
 * 由 workbench.vue 创建一次，以 props 传给各锚点组件（显式依赖，类型安全）。
 * 锚点顺序解锁由 canTune/canSimulate/canConfirm 计算属性驱动（Poka-Yoke）。
 */
import type { TuningApi } from '#/api/tuning';

import { computed, reactive, toRefs } from 'vue';

import { message, Modal } from 'ant-design-vue';

import { getLoopDetailApi, getLoopMonitorListApi } from '#/api/loop';
import {
  comparePidsApi,
  getTuningTaskStatusApi,
  identifyHistoryApi,
  identifyStepApi,
  limitationItemsOf,
  saveTuningTaskApi,
  tuneMatrixApi,
  tuneSingleApi,
  verifyTuningRobustnessApi,
} from '#/api/tuning';
import { fitnessTagToLabel } from '#/constants/clpm-ui';

import { tuningAlgoLabel } from '../constants';

/** P2 IA优化：fitness tag 中文映射收敛单源（clpm-ui.ts FITNESS_TAG_LABEL） */
const tagsToText = (tags: string[]) =>
  tags.map((t) => fitnessTagToLabel(t)).join('、');

/** 辨识结果（统一历史/阶跃双路径的输出形态；MANUAL=人工修改后的模型） */
export interface IdentifyOutcome {
  modelType: TuningApi.ModelType;
  params: TuningApi.ModelParams;
  fittingScore: number;
  confidenceLevel?: null | TuningApi.ConfidenceLevel;
  /** 服务端持久化的辨识记录 ID（阶跃路径 recordId / 历史路径 result.recordId）；人工修改后置空 */
  recordId?: null | string;
  /** fallback_step=AUTO 策略历史辨识失败后阶跃兜底（服务端记录同为阶跃证据） */
  dataSource: 'fallback_step' | 'HISTORY' | 'MANUAL' | 'STEP_EXPERIMENT';
  identifyMethod?: null | string;
  reason?: null | string;
}

/**
 * P3-B2 W4：由辨识结果推导推荐链 modelSource（单源，保存/矩阵/仿真共用）。
 *
 * fallback_step 记录本质是服务端已验证的阶跃辨识证据（identify_method=STEP_*、
 * data_source=fallback_step，authorize_tuning_model 对 STEP_EXPERIMENT 来源
 * 显式接受 fallback_step），按 STEP_EXPERIMENT 声明；此前硬编码 HISTORY 会
 * 被 ERR_TUNING_SOURCE_INVALID 拒绝（来源声明与记录不一致）。
 */
function outcomeModelSource(
  outcome: Pick<IdentifyOutcome, 'dataSource' | 'recordId'>,
): TuningApi.ModelSource {
  const rid = outcome.recordId;
  if (!rid) return 'MANUAL';
  return outcome.dataSource === 'HISTORY'
    ? 'IDENTIFICATION_RECORD'
    : 'STEP_EXPERIMENT';
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
  /** 算法风险评估（随方案保存落 risk_assessment，V62-P3-007） */
  risk?: null | Record<string, any>;
  /**
   * R03：该行建议的适用性限制（tune/matrix 响应 applicabilityLimitations
   * 按值判空后的条目；null=该行无有效响应，空数组=授权来源无已知限制）
   */
  limitations?: null | TuningApi.ApplicabilityLimitationItem[];
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
    /**
     * P2 IA优化：可整定等级（三性分离 R5：优先 tune 维度档，旧快照回退综合
     * fitnessLevel）；L0/L1（空=未加载）。口径与后端 _ensure_tuning_fitness 一致
     */
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
    // ③b 可信集合鲁棒验证（R01 三态 / R02 setConstruction / R03 限制）
    robustLoading: false,
    robustError: '' as string,
    robustReport: null as null | TuningApi.RobustnessReport,
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
  /** L0/L1 → 禁止进入下游按钮；L2/L3/L4/null → 放行 */
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
      // 三性分离（R5）：整定门禁读可整定档 tuneLevel（旧快照 NULL 回退综合档）
      state.fitnessLevel = (item?.tuneLevel ?? item?.fitnessLevel) ?? null;
      state.fitnessTags = Array.isArray(item?.fitnessTags)
        ? (item.fitnessTags as string[])
        : [];
    } catch {
      // 降级：fitness 取空（按 L3/L4 放行，不阻塞已有业务）
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
    } else if (level === 'L3' || level === 'L4') {
      message.success(`【${step}】当前可整定等级 = ${level}，可正常整定。`);
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
    state.robustLoading = false;
    state.robustError = '';
    state.robustReport = null;
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
    state.robustError = '';
    state.robustReport = null;
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
    // 细粒度进度轮询（2s 间隔；后端按阶段更新 progress）。
    // P2 修复（2026-10-01）：加轮询上限——原 for(;;) 无限轮询，worker
    // 死亡/任务丢失时按钮永久 loading 无退出路径。上限 15 分钟
    // （450 次 × 2s；后端 Celery time_limit=1800s 兜底，前端先到先报）
    const MAX_POLLS = 450;
    for (let i = 0; i < MAX_POLLS; i++) {
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
          // P3-B2 W4：透传服务端 dataSource（AUTO 兜底为 fallback_step，
          // 此前硬编码 HISTORY 会导致保存/矩阵链来源声明与记录不一致被拒）
          dataSource: (r as any).dataSource ?? 'HISTORY',
          identifyMethod: (r as any).identifyMethod ?? null,
          reason: r.reason ?? null,
        };
        return;
      }
      if (p.status === 'FAILED') {
        throw new Error(p.error || '历史辨识任务失败');
      }
    }
    throw new Error('辨识任务超时（15 分钟无结果，请在任务中心查看状态后重试）');
  }

  // ===== ② 整定矩阵 =====
  /**
   * T5 修复（2026-10-01）：C 级风险确认。原两处硬编码 riskConfirmed: true
   * 绕过后端 ERR_TUNING_RISK_CONFIRMATION_REQUIRED 门禁（形同虚设）。
   * 改为默认不带确认发起；命中 C 级门禁时弹风险说明，用户确认后
   * 带 riskConfirmed: true 重试一次。
   */
  async function withRiskConfirm<T>(
    call: (riskConfirmed: boolean) => Promise<T>,
  ): Promise<T> {
    try {
      return await call(false);
    } catch (error: any) {
      const code = error?.response?.data?.code ?? error?.code;
      if (code !== 'ERR_TUNING_RISK_CONFIRMATION_REQUIRED') throw error;
      const ok = await new Promise<boolean>((resolve) => {
        Modal.confirm({
          title: 'C 级辨识结果风险确认',
          content:
            '该回路辨识结果可信度为 C 级（可能存在负增益或非最小相位特性），整定建议仅供试验参考。请确认已了解风险并完成人工核验后继续。',
          okText: '已了解风险，继续',
          cancelText: '取消',
          onOk: () => resolve(true),
          onCancel: () => resolve(false),
        });
      });
      if (!ok) throw error;
      return await call(true);
    }
  }

  function matrixRequestBase(riskConfirmed: boolean) {
    const o = state.outcome!;
    const rid = o.recordId;
    return {
      modelType: o.modelType,
      modelParams: o.params,
      currentPid: state.currentPid,
      loopId: state.loopId,
      sourceRecordId: rid || undefined,
      // 来源声明经 outcomeModelSource 单源推导（fallback_step 按 STEP_EXPERIMENT）
      modelSource: outcomeModelSource(o),
      riskConfirmed,
    };
  }

  async function runMatrix() {
    state.matrixLoading = true;
    state.matrixError = '';
    state.robustError = '';
    state.robustReport = null;
    try {
      const res = await withRiskConfirm((rc) => tuneMatrixApi(matrixRequestBase(rc)));
      state.matrixRows = res.rows.map((r) => ({
        algorithm: r.algorithm,
        ok: r.ok,
        pid: r.ok ? (r.result?.recommendedPid ?? null) : null,
        error: r.error,
        checked: false,
        paramValue: 1,
        recomputing: false,
        risk: r.ok ? (r.result?.risk ?? null) : null,
        // R03：逐行限制按值判空携带（null/缺键/空数组 → []，不制造警告）
        limitations: r.ok ? limitationItemsOf(r.result?.applicabilityLimitations) : null,
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
        limitations: null,
      });
    } catch (error: any) {
      // 透出后端 BizError message/code（400 门禁：fitness/来源/风险确认等）
      const bizMsg =
        error?.response?.data?.message ?? error?.data?.message ?? error?.message;
      const bizCode =
        error?.response?.data?.code ?? error?.data?.code ?? error?.code ?? '';
      state.matrixError = bizCode ? `${bizMsg}（${bizCode}）` : bizMsg || '矩阵计算失败';
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
    state.robustError = '';
    state.robustReport = null;
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
      const res = await withRiskConfirm((rc) =>
        tuneSingleApi({
          ...matrixRequestBase(rc),
          algorithm: row.algorithm,
          algorithmParams: paramKey ? { [paramKey]: row.paramValue } : undefined,
        }),
      );
      row.pid = res.recommendedPid;
      row.ok = true;
      row.error = undefined;
      row.risk = res.risk ?? null;
      // R03：重算后限制随新建议按值判空刷新；旧鲁棒验证结论随之失效
      row.limitations = limitationItemsOf(res.applicabilityLimitations);
      state.robustError = '';
      state.robustReport = null;
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
    state.robustError = '';
    state.robustReport = null;
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
      const outcome = state.outcome!;
      const res = await withRiskConfirm((rc) =>
        comparePidsApi({
          modelType: outcome.modelType,
          modelParams: outcome.params,
          pidCandidates: candidates,
          currentPid: state.currentPid ?? undefined,
          loopId: state.loopId,
          sourceRecordId: outcome.recordId ?? undefined,
          // 来源声明经 outcomeModelSource 单源推导（fallback_step 按 STEP_EXPERIMENT）
          modelSource: outcomeModelSource(outcome),
          riskConfirmed: rc,
        }),
      );
      state.simResult = res;
      state.simCandidates = simCandidates;
      state.finalLabel = simCandidates.find((c) => !c.isCurrent)?.label ?? '';
    } catch (error: any) {
      state.simError = error?.message || '仿真失败';
    } finally {
      state.simulating = false;
    }
  }

  // ===== ③b 可信集合鲁棒验证（R01 三态 / R02 setConstruction / R03 限制证据）=====
  /** 可发起鲁棒验证：矩阵已就绪且至少勾选 1 组算法推荐行（手动整定行无算法分派） */
  const canRunRobustness = computed(
    () =>
      canTune.value &&
      checkedRows.value.some((r) => r.algorithm !== 'MANUAL_TUNING' && r.pid),
  );

  /**
   * 鲁棒验证（POST /tuning/tune/robustness）：验证对象 = ④已选定组优先，
   * 否则首个勾选算法行；recommendedPid 由调用方携带（CALLER_PROVIDED），
   * 来源身份与推荐/矩阵/保存链同源（outcomeModelSource 单源推导）。
   */
  async function runRobustness() {
    if (!state.outcome) return;
    // P2 IA优化：L0/L1 硬拦截（与辨识/仿真同口径，后端 _ensure_tuning_fitness 同拒）
    if (tuningDisabled.value) {
      message.error(tuningDisabledReason.value || '当前回路适用性不足，不允许鲁棒验证。');
      return;
    }
    state.robustError = '';
    const chosenCand = state.simCandidates.find(
      (c) => c.label === state.finalLabel && !c.isCurrent,
    );
    const row = chosenCand
      ? state.matrixRows.find((r) => r.algorithm === chosenCand.algorithm)
      : checkedRows.value.find((r) => r.algorithm !== 'MANUAL_TUNING');
    if (!row || row.algorithm === 'MANUAL_TUNING' || !row.pid) {
      state.robustError =
        '鲁棒验证需勾选算法推荐行（手动整定行无算法分派，不经验证链）';
      return;
    }
    state.robustLoading = true;
    state.robustReport = null;
    try {
      const outcome = state.outcome;
      const paramKey = ALGO_PARAM_KEY[row.algorithm];
      state.robustReport = await withRiskConfirm((rc) =>
        verifyTuningRobustnessApi({
          loopId: state.loopId,
          modelType: outcome.modelType,
          modelParams: outcome.params,
          algorithm: row.algorithm,
          algorithmParams: paramKey
            ? { [paramKey]: row.paramValue }
            : undefined,
          recommendedPid: row.pid,
          // 来源声明经 outcomeModelSource 单源推导（fallback_step 按 STEP_EXPERIMENT）
          sourceRecordId: outcome.recordId ?? undefined,
          modelSource: outcomeModelSource(outcome),
          riskConfirmed: rc,
        }),
      );
    } catch (error: any) {
      state.robustError = error?.message || '鲁棒验证失败';
    } finally {
      state.robustLoading = false;
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
      const outcome = state.outcome;
      // P3-B2 W4（G2-INC-c）：保存链新协议——只交来源身份引用，来源/模型/
      // 参数/窗口/currentPid 快照全部由服务端从来源记录与工程事实权威派生
      // 并冻结（旧申报字段 dataSource/fittingScore/currentPid 等不再携带，
      // 携带会被 ERR_TUNING_PROVENANCE_CONFLICT 拒绝）
      const res = await saveTuningTaskApi({
        loopId: state.loopId,
        modelType: outcome.modelType,
        modelParams: outcome.params,
        algorithm: algoRow?.algorithm ?? 'IMC',
        recommendedPid: chosen.pid,
        riskAssessment: algoRow?.risk ?? undefined,
        simulationResult: state.simResult as unknown as Record<string, any>,
        status: 'SIMULATED',
        // 来源声明经 outcomeModelSource 单源推导（fallback_step 按 STEP_EXPERIMENT）
        modelSource: outcomeModelSource(outcome),
        sourceRecordId: outcome.recordId ?? undefined,
        // C 级/人工模型风险确认：矩阵/仿真链 withRiskConfirm 已在本会话内
        // 通过同一门禁（未确认无法走到保存步），此处如实携带
        riskConfirmed: true,
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
    canRunRobustness,
    checkedRows,
    clearLoop,
    selectLoop,
    runIdentify,
    runMatrix,
    applyManualModel,
    recomputeRow,
    toggleRow,
    runSimulate,
    runRobustness,
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
