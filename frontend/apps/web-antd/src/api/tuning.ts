/**
 * 整定模块 API（09 设计方案恢复为一级模块，2026-08-19）
 *
 * 设计文档：docs/MVP设计/09-整定模块设计方案.md §5.2/§5.3
 * 后端：backend/app/api/v1/endpoints/tuning.py（/api/v1/tuning/*）
 */

import { requestClient } from '#/api/request';

export namespace TuningApi {
  /** 模型类型 */
  export type ModelType = 'FOPDT' | 'IPDT' | 'SOPDT';
  /** 整定算法（矩阵 5 算法 + 手动整定） */
  export type TuningAlgorithm =
    | 'COHEN_COON'
    | 'IDENTIFICATION_ONLY'
    | 'IMC'
    | 'LAMBDA'
    | 'MANUAL_TUNING'
    | 'SIMC'
    | 'ZN';
  /** 可信度等级 */
  export type ConfidenceLevel = 'A' | 'B' | 'C' | 'D' | 'E' | 'INCONCLUSIVE';
  /** 推荐链模型来源（P3-B2 W4：最终方案保存链同协议——来源身份由服务端校验冻结） */
  export type ModelSource =
    | 'IDENTIFICATION_RECORD'
    | 'MANUAL'
    | 'STEP_EXPERIMENT';
  /** 整定任务状态（MVP 实际使用 SIMULATED→APPLIED→VERIFIED 路径） */
  export type TuningTaskStatus =
    | 'APPLIED'
    | 'COMPLETED'
    | 'DRAFT'
    | 'IDENTIFIED'
    | 'INCONCLUSIVE'
    | 'PENDING'
    | 'ROLLED_BACK'
    | 'RUNNING'
    | 'SIMULATED'
    | 'VERIFIED';

  /** 模型参数（FOPDT: K/tau/theta；SOPDT 标准形: K/T1/T2/theta） */
  export interface ModelParams {
    K?: null | number;
    T1?: null | number;
    T2?: null | number;
    tau?: null | number;
    theta?: null | number;
  }

  export interface PidParams {
    kp: number;
    ti: number;
    td: number;
  }

  export interface PidParamsWithLabel extends PidParams {
    label: string;
  }

  /** 阶跃实验辨识结果 */
  export interface ModelIdentifyResult {
    modelType: ModelType;
    params: ModelParams;
    fittingScore: number;
    algorithmVersion: string;
    dataPoints: number;
    recordId?: null | string;
    fittedCurve?: null | { [key: string]: any[] };
  }

  /** 历史数据辨识异步响应 */
  export interface IdentifyHistoryAsyncResponse {
    taskId: string;
    [key: string]: any;
  }

  /** 异步任务进度 */
  export interface TaskProgress {
    taskId: string;
    status: 'FAILED' | 'PENDING' | 'RUNNING' | 'SUCCESS';
    progress: number;
    stage?: null | string;
    message?: null | string;
    result?: null | {
      [key: string]: any;
      confidenceLevel?: ConfidenceLevel;
      fittingScore?: number;
      modelType?: ModelType;
      params?: ModelParams;
      reason?: string;
    };
    error?: null | string;
  }

  /** 整定方法信息 */
  export interface TuningMethodInfo {
    code: string;
    name: string;
    description: string;
    applicableModel: string;
    params: Record<string, any>[];
  }

  /**
   * R03 适用性限制条目（G3 增量纠偏 2026-10-13；reasonCode 为后端稳定身份：
   * LOW_SNR_PARAM_UNCOMMITTED=低 SNR 不承诺参数级精度 /
   * CI95_UNCALIBRATED=ci95 未校准仅证明被测集合成员）
   */
  export interface ApplicabilityLimitationItem {
    reasonCode: string;
    scope?: null | string;
    calibrationStatus?: null | string;
    evidence?: null | Record<string, any>;
    statement?: null | string;
  }

  /**
   * 适用性限制载体。G3 F3 登记：HTTP 层无限制时以 null 序列化（键在场值空），
   * 消费方必须按值判空（limitationItemsOf），勿按键存在性制造警告
   */
  export interface ApplicabilityLimitations {
    items?: ApplicabilityLimitationItem[] | null;
  }

  /** 单算法整定结果 */
  export interface TuneResult {
    algorithm: string;
    recommendedPid: PidParams;
    currentPid?: null | PidParams;
    algorithmParams?: null | Record<string, any>;
    algorithmVersion: string;
    notes?: null | string;
    risk?: null | {
      description?: null | string;
      factors: string[];
      riskLevel: string;
    };
    rollbackPid?: null | PidParams;
    /** R03：适用性限制（普通建议链/矩阵逐行同源同文；null=授权来源无已知限制） */
    applicabilityLimitations?: ApplicabilityLimitations | null;
  }

  /** 全算法矩阵行（09 §4.2：单行失败不阻断） */
  export interface TuneMatrixRow {
    algorithm: TuningAlgorithm;
    ok: boolean;
    result?: TuneResult;
    error?: string;
  }

  /** 仿真性能指标 */
  export interface SimulationMetrics {
    riseTime?: null | number;
    overshoot?: null | number;
    settlingTime?: null | number;
    itae?: null | number;
  }

  /** 候选 PID 响应（多 PID 对比） */
  export interface CandidateResponse {
    label: string;
    response: { op: number[]; pv: number[]; sp: number[] };
    metrics: SimulationMetrics;
  }

  /** 闭环仿真结果 */
  export interface SimulationResult {
    timestamps: number[];
    currentResponse: { op: number[]; pv: number[]; sp: number[] };
    recommendedResponse: { op: number[]; pv: number[]; sp: number[] };
    currentMetrics: SimulationMetrics;
    recommendedMetrics: SimulationMetrics;
    improvement: Record<string, null | number>;
    candidateResponses?: CandidateResponse[] | null;
  }

  /** R01 鲁棒验证总体三态（INCONCLUSIVE=窗内证据不足，绝不代表通过） */
  export type RobustnessOverallStatus = 'FAIL' | 'INCONCLUSIVE' | 'PASS';

  /** R01 仿真证据充分性自述（窗末 ±2% 稳定带判据；G3 增量纠偏加性块） */
  export interface SimulationEvidence {
    criterion?: null | string;
    insufficientMemberIndexes?: null | number[];
    insufficientMembers?: null | number;
    note?: null | string;
    settlingBandPct?: null | number;
    simDurationSeconds?: null | number;
    tailFraction?: null | number;
    tailMinPoints?: null | number;
  }

  /** R02 可信集合构造说明（请求/实际成员、退化维、补样次数、原因码） */
  export interface SetConstruction {
    actualMemberCount?: null | number;
    degenerateKeysFixed?: null | string[];
    fillAttempts?: null | number;
    note?: null | string;
    reasonCodes?: null | string[];
    requestedMembers?: null | number;
  }

  /** 可信集合基础（R03：calibrationStatus=uncalibrated 与 membershipScope 不得隐藏） */
  export interface RobustSetBasis {
    bounds?: null | Record<string, [number, number]>;
    calibrationStatus?: null | string;
    fixedKeys?: null | string[];
    freeKeys?: null | string[];
    memberCount?: null | number;
    membershipScope?: null | string;
    note?: null | string;
    setConstruction?: null | SetConstruction;
    source?: null | string;
  }

  /** 验证身份与预算（记录/模型身份随证据同显：02b 快照语义） */
  export interface RobustnessSources {
    algorithmParams?: null | Record<string, any>;
    algorithmVersion?: null | string;
    datasetSnapshotId?: null | string;
    modelSource?: null | string;
    modelVersionId?: null | string;
    simulationEngineVersion?: null | string;
    sourceRecordId?: null | string;
  }

  /** POST /tuning/tune/robustness 验证报告（R15 冻结指标 + 可信集合） */
  export interface RobustnessReport {
    algorithm?: null | string;
    applicabilityLimitations?: ApplicabilityLimitations | null;
    divergedMembers?: null | number;
    frozenThresholds?: null | {
      gainMarginMin?: null | number;
      maxSetMembers?: null | number;
      overshootMaxPct?: null | number;
      phaseMarginMinDeg?: null | number;
    };
    indicators?: null | {
      [key: string]: any;
      gainMargin?: null | { pass?: boolean; threshold?: null | number; worst?: null | number };
      opLimits?: null | {
        limits?: null | { lower?: number; upper?: number };
        pass?: boolean;
        worstSaturationSteps?: null | number;
      };
      overshoot?: null | {
        evidenceInsufficientMembers?: null | number;
        pass?: boolean;
        status?: null | string;
        thresholdPct?: null | number;
        worstPct?: null | number;
      };
      phaseMargin?: null | { pass?: boolean; threshold?: null | number; worst?: null | number };
    };
    loopId?: null | string;
    lowSnrDisclaimer?: null | {
      disclaimer?: null | string;
      mode?: null | string;
      snrDb?: null | number;
      thresholdDb?: null | number;
    };
    memberCount?: null | number;
    modelType?: null | string;
    overallPass?: boolean | null;
    overallStatus?: null | RobustnessOverallStatus | string;
    setBasis?: null | RobustSetBasis;
    simulationEvidence?: null | SimulationEvidence;
    sources?: null | RobustnessSources;
    verifiedPid?: null | PidParams;
    verifiedPidSource?: null | string;
    [key: string]: any;
  }

  /** 整定任务列表项 */
  export interface TuningTaskItem {
    id: string;
    loopId: string;
    tagName?: null | string;
    modelType: ModelType;
    modelParams?: null | Record<string, any>;
    algorithm: string;
    recommendedPid?: null | PidParams;
    fittingScore?: null | number;
    status: TuningTaskStatus;
    createdBy?: null | string;
    createdAt: string;
    confidenceLevel?: ConfidenceLevel | null;
    confidenceReason?: null | string;
    identifyMethod?: null | string;
    dataSource?: null | string;
    excitationScore?: null | number;
    residualTestPassed?: boolean | null;
    taskId?: null | string;
    completedAt?: null | string;
    /** V62-P3-007 人工实施清单（2026-10-04 出参补齐；旧记录为 null） */
    rollbackPid?: null | PidParams;
    riskAssessment?: null | Record<string, any>;
    /** 辨识时间窗（异步历史辨识落库；同步阶跃/MANUAL 为 null） */
    timeWindowStart?: null | string;
    timeWindowEnd?: null | string;
    /**
     * P3-B2 W4/W5 冻结身份（加性；2026-10-11 前的旧记录为 null）
     * currentPidContext = 三元组（standard 标准 Kp/Ti秒/Td秒 | raw DCS 原始
     * p/i/d | conversion 转换依据）；未知 DCS 表示 standard=null 不默认
     */
    sourceRecordId?: null | string;
    modelSource?: ModelSource | null;
    dcsTemplateRevision?: null | string;
    unitBasis?: null | Record<string, any>;
    currentPidContext?: null | {
      conversion?: null | Record<string, any>;
      raw?: null | Record<string, any>;
      reason?: null | string;
      standard?: null | PidParams;
      status?: string;
    };
    /** V62-P3-005：模型版本引用（新记录非空，遗留记录为 null） */
    processModelVersionId?: null | string;
    /**
     * R03（G3 增量纠偏）：适用性限制回读——保存时冻结于 calc_context，
     * 旧记录无该键 → null（显式未知不伪补；按值判空）
     */
    applicabilityLimitations?: ApplicabilityLimitations | null;
  }

  export interface TuningTaskDetail extends TuningTaskItem {
    simulationResult?: null | Record<string, any>;
    currentPid?: null | PidParams;
  }

  export interface TuningTaskListData {
    items: TuningTaskItem[];
    total: number;
    page: number;
    pageSize: number;
  }

  export interface TuningHistoryStats {
    totalTasks: number;
    byAlgorithm: Record<string, number>;
    byStatus: Record<string, number>;
    avgFittingScore?: null | number;
    recentTasks: TuningTaskItem[];
  }

  /** KPI 快照摘要（窗口均值口径，2026-10-04 B2） */
  export interface KpiSummary {
    score: null | number;
    goodValueRate: null | number;
    effectiveAutoRate: null | number;
    steadyRate: null | number;
    accuracyRate: null | number;
    fastRate: null | number;
    oscillationRate: null | number;
    saturationRate: null | number;
    confidenceLevel: null | string;
    tsStart: null | string;
    tsEnd: null | string;
    /** 窗口内参与均值的快照条数（B2 新增） */
    snapshotCount?: number;
    /** 窗口内最新一条快照的适用性等级（分类型不可均值，B2 新增） */
    fitnessLevel?: null | string;
    tuneLevel?: null | string;
  }

  /** 波形序列（get_waveform 契约） */
  export interface WaveformData {
    loopId: string;
    tagName?: string;
    timestamps: string[];
    pv: (null | number)[];
    sp: (null | number)[];
    op: (null | number)[];
    mode?: (null | number)[];
    pvQuality?: string[];
    downsampled?: boolean;
    pointCount?: number;
    sampleInterval?: number;
  }

  /** 效果验证前后窗数据（09 §4.5） */
  export interface VerificationData {
    loopId: string;
    pointTime: string;
    windowHours: number;
    before: WaveformData;
    after: WaveformData;
    /** 窗口 KPI 均值摘要（2026-10-04 B2：单条→均值；等级取窗口内最新一条） */
    kpiBefore: KpiSummary | null;
    kpiAfter: KpiSummary | null;
    /** 后窗超出当前时刻（数据截至当前时刻） */
    afterTruncated: boolean;
    /** 前后窗快照条数（B2 新增，供"均值基于 N 条快照"标注） */
    beforeSnapshotCount: number;
    afterSnapshotCount: number;
    /** 后窗有效快照不足（< min(24, windowHours)），结论参考性不足需显式提示 */
    dataInsufficient: boolean;
  }

  /** 整定批次状态（status 为 B-06 动态阻塞判定后的有效状态） */
  export type TuningBatchStatus =
    | 'BLOCKED'
    | 'CANCELLED'
    | 'COMPLETED'
    | 'PENDING'
    | 'READY'
    | 'RUNNING';

  /** 整定批次列表摘要（追溯矩阵 GAP-2a） */
  export interface TuningBatchSummary {
    id: number;
    batchNo: string;
    title: string;
    scopeType: string;
    scopeId: number;
    status: TuningBatchStatus;
    storedStatus: string;
    blocked: boolean;
    blockReason?: null | string;
    recordCount: number;
    createdAt?: null | string;
  }

  export interface TuningBatchListData {
    items: TuningBatchSummary[];
    total: number;
    page: number;
    pageSize: number;
  }

  /** 批次前置工单摘要 */
  export interface TuningBatchPrereqOrder {
    orderId: string;
    orderNo?: null | string;
    title?: null | string;
    status?: null | string;
    closed: boolean;
  }

  /** 批次关联整定记录（tuning_batch_records N:M） */
  export interface TuningBatchRecordItem {
    recordId: string;
    sortOrder: number;
    loopId: string;
    tagName?: null | string;
    modelType?: null | string;
    algorithm?: null | string;
    status?: null | string;
    fittingScore?: null | number;
    createdBy?: null | string;
    createdAt?: null | string;
  }

  /** 整定批次详情 */
  export interface TuningBatchDetail extends TuningBatchSummary {
    prereqOrderIds: string[];
    prereqOrders: TuningBatchPrereqOrder[];
    /** scatters JSONB 原样返回（[{loop_id, score, ...}]） */
    scattersBefore?: null | Record<string, any>[];
    scattersAfter?: null | Record<string, any>[];
    ownerId?: null | number;
    expectedStartAt?: null | string;
    actualStartAt?: null | string;
    completedAt?: null | string;
    records: TuningBatchRecordItem[];
  }
}

/** 整定方法信息 */
export function getTuningMethodsApi() {
  return requestClient.get<TuningApi.TuningMethodInfo[]>('/tuning/methods');
}

/** 阶跃实验辨识（同步） */
export function identifyStepApi(data: {
  endTime: string;
  loopId: string;
  method?: string;
  modelType?: TuningApi.ModelType;
  startTime: string;
}) {
  return requestClient.post<TuningApi.ModelIdentifyResult>(
    '/tuning/identify',
    data,
  );
}

/** 历史数据辨识（异步，返回 taskId 轮询） */
export function identifyHistoryApi(data: {
  candidateModelTypes?: TuningApi.ModelType[];
  endTime: string;
  loopId: string;
  startTime: string;
  thetaEstimate?: null | number;
}) {
  return requestClient.post<TuningApi.IdentifyHistoryAsyncResponse>(
    '/tuning/identify/history',
    data,
  );
}

/** 异步任务进度查询（taskId 为 Celery 任务 ID） */
export function getTuningTaskStatusApi(taskId: string) {
  return requestClient.get<TuningApi.TaskProgress>(
    `/tuning/tasks/${taskId}/status`,
  );
}

/** 取消异步任务 */
export function cancelTuningTaskApi(taskId: string) {
  return requestClient.post<{ cancelled: boolean }>(
    `/tuning/tasks/${taskId}/cancel`,
  );
}

/** 单算法整定（矩阵行内参数微调重算） */
export function tuneSingleApi(data: {
  algorithm: TuningApi.TuningAlgorithm;
  algorithmParams?: null | Record<string, any>;
  currentPid?: null | TuningApi.PidParams;
  loopId?: null | string;
  modelParams: TuningApi.ModelParams;
  modelSource?: string;
  modelType: TuningApi.ModelType;
  riskConfirmed?: boolean;
  sourceRecordId?: null | string;
}) {
  return requestClient.post<TuningApi.TuneResult>('/tuning/tune', data);
}

/** 全算法矩阵（5 算法一次全算，09 §4.2） */
export function tuneMatrixApi(data: {
  algorithmParams?: null | Record<string, any>;
  currentPid?: null | TuningApi.PidParams;
  loopId?: null | string;
  modelParams: TuningApi.ModelParams;
  modelSource?: string;
  modelType: TuningApi.ModelType;
  riskConfirmed?: boolean;
  sourceRecordId?: null | string;
}) {
  return requestClient.post<{ rows: TuningApi.TuneMatrixRow[] }>(
    '/tuning/tune/matrix',
    data,
  );
}

/** 多 PID 对比仿真（pidCandidates ≥2 组） */
export function comparePidsApi(data: {
  currentPid?: null | TuningApi.PidParams;
  loopId?: null | string;
  modelParams: TuningApi.ModelParams;
  modelSource?: string;
  modelType: TuningApi.ModelType;
  pidCandidates: TuningApi.PidParamsWithLabel[];
  riskConfirmed?: boolean;
  setpointStep?: number;
  simDuration?: number;
  simStep?: number;
  sourceRecordId?: null | string;
}) {
  return requestClient.post<TuningApi.SimulationResult>(
    '/tuning/compare',
    data,
  );
}

/**
 * 可信集合鲁棒性验证（P3-07 R15 冻结指标；G3 增量纠偏 R01/R02/R03 证据链接通）
 *
 * 在可信模型集合（终验通过模型 ± 参数 95% CI 采样，≤64 成员）上验证同一建议：
 * overallStatus 三态（窗内证据不足=INCONCLUSIVE，不得总体 PASS）+
 * simulationEvidence + setBasis.setConstruction（成员/退化维/次数/原因码）+
 * applicabilityLimitations（按值判空）。
 */
export function verifyTuningRobustnessApi(data: {
  algorithm: TuningApi.TuningAlgorithm;
  algorithmParams?: null | Record<string, any>;
  loopId: string;
  modelParams: TuningApi.ModelParams;
  modelSource?: string;
  modelType: TuningApi.ModelType;
  noiseStd?: null | number;
  recommendedPid?: null | TuningApi.PidParams;
  riskConfirmed?: boolean;
  setpointStep?: number;
  simDuration?: number;
  simStep?: number;
  sourceRecordId?: null | string;
}) {
  return requestClient.post<TuningApi.RobustnessReport>(
    '/tuning/tune/robustness',
    data,
  );
}

/**
 * R03 按值判空（G3 F3 登记）：HTTP 层无限制时 applicabilityLimitations 以
 * null 序列化（键在场值空）——null/缺键/空数组一律返回 []，不制造警告；
 * 值缺失不编造限制。
 */
export function limitationItemsOf(
  al?: null | TuningApi.ApplicabilityLimitations,
): TuningApi.ApplicabilityLimitationItem[] {
  const items = al?.items;
  if (!Array.isArray(items)) return [];
  return items.filter((i): i is TuningApi.ApplicabilityLimitationItem =>
    Boolean(i) && typeof i === 'object',
  );
}

/** R03 限制条目 reasonCode 中文标签（原始码同显——码为稳定身份） */
export const LIMITATION_CODE_LABELS: Record<string, string> = {
  CI95_UNCALIBRATED: 'ci95 区间未校准（仅证明被测集合成员）',
  LOW_SNR_PARAM_UNCOMMITTED: '低 SNR · 不承诺参数级精度',
};

/** 限制条目标题（原始码 + 中文标签；未知码原样展示不编造） */
export function limitationTitle(
  item: TuningApi.ApplicabilityLimitationItem,
): string {
  const label = LIMITATION_CODE_LABELS[item.reasonCode];
  return label ? `${item.reasonCode} · ${label}` : item.reasonCode;
}

/** 限制条目描述（后端 statement 原文 + 证据数值；不为缺失证据编造内容） */
export function limitationDesc(
  item: TuningApi.ApplicabilityLimitationItem,
): string {
  const parts: string[] = [];
  if (item.statement) parts.push(item.statement);
  const ev = item.evidence ?? {};
  const bits: string[] = [];
  if (ev.snrDb != null) bits.push(`snrDb=${ev.snrDb}`);
  if (ev.thresholdDb != null) bits.push(`thresholdDb=${ev.thresholdDb}`);
  if (ev.mode) bits.push(`mode=${ev.mode}`);
  if (ev.basis) bits.push(`basis=${ev.basis}`);
  if (ev.ciSource) bits.push(`ciSource=${ev.ciSource}`);
  if (bits.length > 0) parts.push(`证据：${bits.join('，')}`);
  if (item.calibrationStatus) parts.push(`校准状态：${item.calibrationStatus}`);
  return parts.join('；');
}

/**
 * R01 总体三态显示口径（UI 防线，与后端同向）：窗内证据不足
 * （simulationEvidence.insufficientMembers>0）或 overallStatus=INCONCLUSIVE
 * 一律按 INCONCLUSIVE 显示——窗内不足不得显示总体通过；INCONCLUSIVE 不冒充 PASS。
 */
export function robustOverallOf(
  report?: null | TuningApi.RobustnessReport,
): 'FAIL' | 'INCONCLUSIVE' | 'PASS' | 'UNKNOWN' {
  if (!report) return 'UNKNOWN';
  const insufficient =
    (report.simulationEvidence?.insufficientMembers ?? 0) > 0;
  const raw = String(report.overallStatus ?? '').toUpperCase();
  if (insufficient || raw === 'INCONCLUSIVE') return 'INCONCLUSIVE';
  if (raw === 'PASS') return 'PASS';
  if (raw === 'FAIL') return 'FAIL';
  return 'UNKNOWN';
}

/**
 * 保存整定方案（09 §4.4：显式保存才落记录）
 *
 * P3-B2 W4 新协议（G2-INC-c）：只交来源身份引用（modelSource/sourceRecordId），
 * 来源/模型/参数/窗口/currentPid 快照由服务端从来源记录与工程事实权威派生并
 * 冻结——旧申报字段（dataSource/fittingScore/currentPid 等）携带会被
 * ERR_TUNING_PROVENANCE_CONFLICT 拒绝。
 */
export function saveTuningTaskApi(data: {
  [key: string]: any;
  algorithm: string;
  loopId: string;
  modelParams: TuningApi.ModelParams;
  modelSource: TuningApi.ModelSource;
  modelType: TuningApi.ModelType;
  recommendedPid: TuningApi.PidParams;
  riskAssessment?: null | Record<string, any>;
  riskConfirmed?: boolean;
  simulationResult?: null | Record<string, any>;
  sourceRecordId?: null | string;
  status?: TuningApi.TuningTaskStatus;
}) {
  return requestClient.post<{ id: string }>('/tuning/tasks', data);
}

/** 整定任务列表（分页 + 筛选；status 支持逗号多值，startTime/endTime 按创建时间过滤） */
export function getTuningTasksApi(params: {
  algorithm?: string;
  endTime?: string;
  loopId?: string;
  page?: number;
  pageSize?: number;
  startTime?: string;
  status?: string;
}) {
  return requestClient.get<TuningApi.TuningTaskListData>('/tuning/tasks', {
    params,
  });
}

/** 整定任务详情 */
export function getTuningTaskDetailApi(taskId: string) {
  return requestClient.get<TuningApi.TuningTaskDetail>(
    `/tuning/tasks/${taskId}`,
  );
}

/** 整定历史统计 */
export function getTuningHistoryApi() {
  return requestClient.get<TuningApi.TuningHistoryStats>('/tuning/history');
}

/** 整定批次列表（分页 + status/创建时间窗筛选，追溯矩阵 GAP-2a） */
export function getTuningBatchesApi(params: {
  endTime?: string;
  page?: number;
  pageSize?: number;
  startTime?: string;
  status?: string;
}) {
  return requestClient.get<TuningApi.TuningBatchListData>('/tuning/batches', {
    params,
  });
}

/** 整定批次详情（关联整定记录 + 前置工单摘要 + scatters） */
export function getTuningBatchDetailApi(batchId: number | string) {
  return requestClient.get<TuningApi.TuningBatchDetail>(
    `/tuning/batches/${batchId}`,
  );
}

/** 效果验证前后窗曲线数据（09 §4.5，实时拉取不落库） */
export function getVerificationDataApi(params: {
  loopId: string;
  pointTime: string;
  windowHours: number;
}) {
  return requestClient.get<TuningApi.VerificationData>(
    '/tuning/verification/data',
    { params },
  );
}
