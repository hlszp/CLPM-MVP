/**
 * 诊断模块 API（MVP v2 重设计版）
 *
 * 设计文档：docs/MVP设计/07-诊断模块设计方案.md §9.1
 * 后端：backend/app/api/v1/endpoints/diagnosis_v2.py（/api/v1/diagnosis/*）
 */

import type { MetricApi } from '#/api/metric';
import type { PageQuery, PaginatedResponse } from '#/api/types';

import { requestClient } from '#/api/request';

export namespace DiagnosisApi {
  /** 原因分类代码（8 类，设计 §3.1）；展示元数据见 views/diagnosis/constants.ts */
  export type Category =
    | 'COMMUNICATION'
    | 'DATA_INSUFFICIENT'
    | 'DESIGN'
    | 'INSTRUMENT'
    | 'PROCESS'
    | 'TUNING'
    | 'UTILIZATION'
    | 'VALVE';

  export type Severity = 'HIGH' | 'LOW' | 'MEDIUM';
  export type RunStatus = 'FAILED' | 'PARTIAL' | 'RUNNING' | 'SUCCESS';
  /** 触发类型：手动 / 分级定时 / 预警事件（§12 三层自动诊断） */
  export type TriggerType = 'EVENT' | 'MANUAL' | 'SCHEDULED';

  /** 分类判定（主/次/待复核三态，设计 §7.3） */
  export interface CategoryJudgement {
    category: Category;
    categoryLabel: string;
    confidence: number;
    basis: string[];
    status: 'pending_review' | 'primary' | 'secondary';
    contaminationNote?: null | string;
  }

  export interface Recommendation {
    content: string;
    basis: string;
    direction: string;
    priority: number;
  }

  /** 数据门禁结论（设计 §4.3：消费日常质量结论） */
  export interface GateInfo {
    passed: boolean;
    pointCount: number;
    expectedPoints: number;
    validRate: number;
    confidenceLevel: string;
    gapRatio: number;
    reason?: null | string;
  }

  /** 证据波形快照（LTTB ≤2000 点，自包含） */
  export interface ChartSnapshot {
    trend: {
      op?: (null | number)[];
      /** OP 右轴量程 */
      opRange?: { max: number; min: number };
      pv?: (null | number)[];
      /** PV/SP 左轴量程（诊断时从 Tag 解析；旧记录无此字段前端自适应） */
      pvRange?: { max: number; min: number };
      sp?: (null | number)[];
      ts: number[];
    };
    scatter: { op: number[]; pv: number[] };
  }

  export interface OperatorResult {
    operator: string;
    executed: boolean;
    skipReason?: null | string;
    detected: boolean;
    confidence: number;
    features: Record<string, any>;
    evidence: Array<{
      feature: string;
      judgment: string;
      threshold?: any;
      value: any;
    }>;
    error?: null | string;
  }

  /** 记录列表行（列表页） */
  export interface RunListItem {
    id: string;
    taskId?: null | string;
    loopId: string;
    loopTagName?: null | string;
    triggeredBy: string;
    /** 触发类型（MANUAL 手动 / SCHEDULED 分级定时 / EVENT 预警事件） */
    triggerType?: null | TriggerType;
    triggerTypeLabel?: null | string;
    timeWindowStart: string;
    timeWindowEnd: string;
    operatorGroup: string;
    status: RunStatus;
    primaryCategory?: Category | null;
    primaryCategoryLabel?: null | string;
    primaryConfidence?: null | number;
    secondaryCategories: CategoryJudgement[];
    pendingReview: CategoryJudgement[];
    severity?: null | Severity;
    /** 复核闭环（2026-08-18） */
    reviewStatus?: null | ReviewStatus;
    reviewResults?: string[];
    reviewResultLabels?: string[];
    reviewComment?: null | string;
    reviewedBy?: null | string;
    reviewedAt?: null | string;
    /** P2 IA优化：诊断运行时若触发回路 fitnessLevel=L2 置 true，前端据此渲染条件异常横幅 */
    conditionWarning?: boolean | null;
    /** P2 IA优化：诊断时回路 fitness 快照（L0/L1 时后端会拒绝；L2 时仍跑诊断但标警告） */
    fitnessLevel?: null | string;
    fitnessTags?: null | string[];
    createdAt: string;
  }

  /** 诊断完整详情（结果面板） */
  export interface RunDetail extends RunListItem {
    dataGate: GateInfo;
    operatorResults: Record<string, OperatorResult>;
    fusionResults: Record<
      string,
      {
        confidence: number;
        detected: boolean;
        family: string;
        fused: boolean;
        symptomTag: string;
      }
    >;
    symptomTags: Record<string, { confidence: number; detected: boolean }>;
    rationale: string[];
    recommendations: Recommendation[];
    evidenceCharts?: ChartSnapshot;
    /** 诊断指标汇总（方案 A：窗口 KPI 均值 + 算子特征，0~100 统一口径） */
    metricSummary?: MetricSummary | null;
    thresholdVersion?: null | string;
    algorithmVersion?: null | string;
    startedAt?: null | string;
    finishedAt?: null | string;
    durationMs?: null | number;
    /** 置信度显式定义（分类级 + 算子级 + 融合规则） */
    confidenceDefinitions?: ConfidenceDefinitions;
  }

  /** 算子注册表项（GET /operators，AI 工具目录同源） */
  export interface OperatorInfo {
    name: string;
    displayName: string;
    family: string;
    diagCode: string;
    description: string;
    requiredSignals: string[];
    minSampleRate: number;
    outputsSchema: Record<string, string>;
    thresholdSchema: Record<string, unknown>;
    symptomTags: string[];
    enabledByDefault: boolean;
    fastGroup: boolean;
    /** 置信度计算口径说明 */
    confidenceBasis?: string;
  }

  /** 置信度显式定义（详情 API 附加返回，不入库） */
  export interface ConfidenceDefinitions {
    categories: Record<string, string>;
    operators: Record<string, string>;
    fusion: string;
    secondaryGate: number;
  }

  /** 诊断指标汇总（方案 A，2026-08-19）：窗口 KPI 均值 + 算子特征，0~100 统一口径 */
  export interface MetricSummary {
    /** 负向指标（值越大越差）：坏值率/饱和率/振荡率/粘滞系数（%），稳定时间（秒），行程指数 */
    negative: {
      badValueRate?: null | number;
      oscillationRate?: null | number;
      outputTravelIndex?: null | number;
      saturationRate?: null | number;
      settlingTime?: null | number;
      stictionIndex?: null | number;
    };
    /** 正向指标（KPI 窗口均值，%）：综合评分 + 6 率 */
    positive: {
      accuracyRate?: null | number;
      autoModeRate?: null | number;
      effectiveAutoRate?: null | number;
      fastRate?: null | number;
      goodValueRate?: null | number;
      score?: null | number;
      steadyRate?: null | number;
    };
    /** 各负向指标来源：kpi（窗口快照均值）/ operator（算子特征换算）/ none（无数据） */
    source: Record<string, string>;
  }

  export type TimeWindowPreset = 'last_7d' | 'last_24h' | 'last_30d';
  export type OperatorGroup = 'fast' | 'full';

  /** 每回路最新诊断概览行（GET /runs/latest，2026-08-18 重构：一回路一条） */
  export interface LatestRunItem {
    loopId: string;
    loopTagName: string;
    /** 回路名称（loop_ledger.description） */
    loopDescription?: null | string;
    /** 回路等级（1 关键 / 2 重要 / 3 一般） */
    importanceLevel?: null | number;
    /** 该次诊断记录 ID（null=从未诊断） */
    runId: null | string;
    /** 该回路累计诊断次数（"第 N 次"；未诊断为 0） */
    runCount?: number;
    /** 最近性能评分（kpi_snapshot_hourly 最新有值快照） */
    latestScore?: null | number;
    triggerType?: null | TriggerType;
    triggerTypeLabel?: null | string;
    primaryCategory?: Category | null;
    primaryCategoryLabel?: null | string;
    primaryConfidence?: null | number;
    severity?: null | Severity;
    status?: null | RunStatus;
    reviewStatus?: null | ReviewStatus;
    reviewResults?: string[];
    reviewResultLabels?: string[];
    reviewedBy?: null | string;
    reviewedAt?: null | string;
    lastDiagnosedAt?: null | string;
    timeWindowStart?: null | string;
    timeWindowEnd?: null | string;
    /** 诊断指标汇总（窗口 KPI 均值+算子特征，0~100 口径；未诊断为 null）。
     *  回路工作台 R5 诊断卡 / 整定工作台摘要条消费（2026-08-19） */
    metricSummary?: MetricSummary | null;
  }

  /** 复核状态：PENDING 待复核 / REVIEWED 已复核 */
  export type ReviewStatus = 'PENDING' | 'REVIEWED';

  /** 复核请求体 */
  export interface ReviewBody {
    /** 复核结论多选（原因分类代码，与诊断分类同域） */
    reviewResults: string[];
    /** 复核意见（≤500 字可选） */
    reviewComment?: null | string;
  }

  /** 处置建议项（§9.4 处置闭环：建议-处置-验证-关闭，当前仅建议态） */
  export interface ActionItem {
    id: string;
    runId: string;
    loopId: string;
    /** 来源：SYSTEM 系统按诊断/复核结论带出 / MANUAL 人工新增 */
    source: 'MANUAL' | 'SYSTEM';
    category?: null | string;
    categoryLabel?: null | string;
    /** 处置措施内容 */
    content: string;
    /** 依据（如"诊断结论：参数问题"或"人工复核：..."） */
    basis?: null | string;
    /** 优先级（1 最高；人工新增为 null） */
    priority?: null | number;
    /** 生命周期：PENDING 待处置（后续扩展处置/验证/关闭） */
    status: string;
    /** 建议人（SYSTEM="系统"；MANUAL=登录用户名） */
    suggestedBy: string;
    /** 建议时间（naive UTC ISO，Z 后缀） */
    suggestedAt?: null | string;
  }

  /** 人工新增处置措施请求体 */
  export interface CreateActionBody {
    content: string;
    basis?: null | string;
  }

  export interface TriggerBody {
    loopIds: string[];
    timeWindow: { end?: string; preset?: TimeWindowPreset; start?: string };
    operatorGroup: OperatorGroup;
    /** 单算子细选白名单（空/缺省=按 operatorGroup 执行） */
    operators?: string[];
  }

  export interface TriggerResult {
    taskId: string;
    accepted: number;
  }

  export interface RunQuery extends PageQuery {
    loopId?: string;
    category?: Category;
    severity?: Severity;
    status?: RunStatus;
    reviewStatus?: ReviewStatus;
    taskId?: string;
    startTime?: string;
    endTime?: string;
  }

  // ===== 16 号文 F1/F2（诊断扩展 Phase A：回路档案 + 复诊对比） =====

  /** F1 档案时间窗（'all' 后端截断到 90d，前端提示） */
  export type ArchiveWindow = '30d' | '90d' | 'all';

  /** F1 档案 run 摘要行（升序，旧→新） */
  export interface ArchiveRunItem {
    runId: string;
    diagnosedAt: string;
    primaryCategory: Category | null;
    secondaryCategories: CategoryJudgement[];
    severity: null | Severity;
    confidence: null | number;
    triggerType: null | TriggerType;
    status: RunStatus;
    reviewStatus: null | ReviewStatus;
  }

  /** F1 关联干预事件（处置/整定；模块禁用时后端跳过查询段并置 enabled=false） */
  export interface ArchiveEventItem {
    type: 'handling' | 'tuning';
    subtype?: null | string;
    at: string;
    title: string;
    refId?: null | string;
  }

  /** F1 回路诊断档案聚合响应（GET /runs/loop-archive） */
  export interface LoopArchive {
    loop: {
      /** 回路等级（1 关键 / 2 重要 / 3 一般） */
      level: null | number;
      loopId: string;
      loopName: string;
      loopType: null | string;
    };
    summary: {
      firstDiagnosedAt: null | string;
      lastDiagnosedAt: null | string;
      latestCategory: Category | null;
      latestConfidence: null | number;
      totalRuns: number;
    };
    runs: ArchiveRunItem[];
    /** KPI 趋势（kpi_snapshot_hourly，LTTB）；评估模块禁用/无快照时 available=false */
    kpiTrend: {
      available: boolean;
      series: {
        oscillationRate: Array<{ t: string; v: null | number }>;
        score: Array<{ t: string; v: null | number }>;
      };
    };
    /** 事件图层能力（禁用模块对应图层前端隐藏而非置灰） */
    events: {
      handlingEnabled: boolean;
      items: ArchiveEventItem[];
      tuningEnabled: boolean;
    };
  }

  /** F2 对比模式：adjacent=与上一条 SUCCESS run（纯诊断域恒可用）/ verify=处置工单关联前后 */
  export type CompareMode = 'adjacent' | 'verify';

  /** F2 对比单侧 run 概要 */
  export interface CompareRunRef {
    runId: string;
    diagnosedAt: string;
    primaryCategory: Category | null;
    severity: null | Severity;
    confidence: null | number;
    windowStart: null | string;
    windowEnd: null | string;
  }

  /** F2 对比响应（base=基准/处置前，target=对比/处置后；base=null 表示无前序） */
  export interface CompareResult {
    mode: CompareMode;
    base: CompareRunRef | null;
    target: CompareRunRef;
    conclusion: {
      confidence: {
        base: null | number;
        delta: null | number;
        target: null | number;
      };
      primaryCategory: { base: Category | null; target: Category | null };
      severity: { base: null | Severity; target: null | Severity };
    };
    features: Array<{
      baseValue: null | number;
      delta: null | number;
      direction: null | string;
      feature: string;
      operator: string;
      targetValue: null | number;
    }>;
    kpi: Array<{
      base: null | number;
      delta: null | number;
      direction: null | string;
      metric: string;
      target: null | number;
    }>;
    /** 验证对比能力（处置启用且存在 verify_run_id 关联时 true；前端据此显隐该模式） */
    verifyPair: boolean;
  }

  // ===== 16 号文 F3/F4（诊断扩展 Phase B：覆盖台账 + 共性问题回路组对比） =====

  /** F3 新鲜度分档键（按回路最新 SUCCESS 诊断时间） */
  export type FreshnessBucketKey =
    | 'never'
    | 'stale'
    | 'within7d'
    | 'within24h'
    | 'within30d';

  /** F3 分档内回路条目（悬浮/下钻用） */
  export interface CoverageBucketLoop {
    lastDiagnosedAt: null | string;
    loopId: string;
    loopTagName: string;
  }

  /** F3 新鲜度分档桶 */
  export interface CoverageBucket {
    count: number;
    key: FreshnessBucketKey;
    loops: CoverageBucketLoop[];
  }

  /** F3 调度滞后回路（1/2 级；lastScheduledAt=null 表示从未排程） */
  export interface CoverageScheduleLaggingLoop {
    lastScheduledAt: null | string;
    loopId: string;
    loopTagName: string;
  }

  /** F3 调度执行分级行（3 级仅 note，无滞后字段） */
  export interface CoverageScheduleLevel {
    cadence: 'daily' | 'manual' | 'weekly';
    cadenceLabel: string;
    expectedLoops: number;
    lagThresholdHours?: null | number;
    lagging?: CoverageScheduleLaggingLoop[];
    laggingCount?: number;
    lastScheduledAt?: null | string;
    level: number;
    note?: null | string;
  }

  /** F3 数据不足榜条目（近 30d DATA_INSUFFICIENT 占比） */
  export interface CoverageDataInsufficientItem {
    insufficientRuns: number;
    loopId: string;
    loopTagName: null | string;
    ratio: number;
    totalRuns: number;
  }

  /** F3 诊断覆盖台账响应（GET /coverage）；schedule 仅 ADMIN 返回，非管理员为 null */
  export interface CoverageResult {
    dataInsufficient: {
      top: CoverageDataInsufficientItem[];
      windowDays: number;
    };
    freshness: {
      buckets: CoverageBucket[];
      generatedAt: string;
      totalLoops: number;
    };
    /** 调度执行块（仅 ADMIN；null 时前端整块隐藏而非置灰） */
    schedule: null | { levels: CoverageScheduleLevel[] };
  }

  /** F4 回路组条目：该分类×装置下最新一条 run 摘要（latest-per-loop 口径） */
  export interface CategoryCohortItem {
    importanceLevel: null | number;
    lastDiagnosedAt: null | string;
    loopDescription: null | string;
    loopId: string;
    loopTagName: string;
    /** 最新 run 指标汇总（雷达 6 指标数据源；无指标时为 null） */
    metricSummary: MetricSummary | null;
    primaryConfidence: null | number;
    runId: string;
    severity: null | Severity;
  }

  /** F4 共性问题回路组响应（GET /category-cohort） */
  export interface CategoryCohortResult {
    category: Category;
    items: CategoryCohortItem[];
    plantNodeId: null | string;
    total: number;
  }

  // ===== 16 号文 F5/F6（诊断扩展 Phase C：发起前预检徽标 + 复核反馈统计） =====

  /** F5 预检窗口（默认 24h，预期快照行数=窗口小时数） */
  export type PrecheckWindow = '7d' | '24h' | '30d';

  /** F5 密度分级：充足 / 疑似不足 / 不足 / 无评估数据（unknown=数据源缺失中性态，非质量结论） */
  export type PrecheckLevel =
    | 'insufficient'
    | 'marginal'
    | 'sufficient'
    | 'unknown';

  /** F5 单回路预检徽标项 */
  export interface PrecheckItem {
    loopId: string;
    level: PrecheckLevel;
    /** 窗口内快照行数 */
    rowCount: number;
    /** 预期行数（窗口小时数 × 1 行/小时） */
    expectedRows: number;
    /** 密度比 rowCount/expectedRows（unknown 时为 null） */
    ratio: null | number;
  }

  /** F5 预检响应（GET /precheck）；assessEnabled=false 时前端整列隐藏徽标 */
  export interface PrecheckResult {
    window: string;
    expectedRows: number;
    /** 评估模块启用能力字段（false=徽标中性隐藏，不误报不足） */
    assessEnabled: boolean;
    generatedAt: string;
    items: PrecheckItem[];
  }

  /** F6 改判去向条目（Top3） */
  export interface ReviewOverturnTopItem {
    category: Category;
    count: number;
  }

  /** F6 统计行公共字段（算子/分类两维度共用；D4：样本 <10 时比例为 null） */
  export interface ReviewFeedbackRow {
    /** 检出次数（算子=executed+detected；分类=机器主分类命中） */
    detectedCount: number;
    /** 其中已复核次数 */
    reviewedCount: number;
    /** 复核率（已复核/已检出；检出 0 时为 null） */
    reviewRate: null | number;
    /** 改判分母（已复核且机器结论可确认/改判；pending_review 命中已排除） */
    sampleSize: number;
    /** D4 样本门槛：sampleSize < sampleMin 时为 true，比例字段为 null 不误导 */
    insufficientSample: boolean;
    /** 确认率（复核维持机器分类比例） */
    confirmRate: null | number;
    /** 改判率（复核结论不含机器分类比例） */
    overturnRate: null | number;
    /** 改判去向分布 Top3 */
    overturnTop: ReviewOverturnTopItem[];
    /** 阈值调优提示（改判率 > 40% 且样本充足；仅提示，不自动调参） */
    tuningHint: boolean;
  }

  /** F6 算子维度行 */
  export interface ReviewFeedbackOperatorRow extends ReviewFeedbackRow {
    operator: string;
    displayName: string;
    family: string;
    diagCode: string;
    /** 症状标签映射的归因分类（阈值提示按此定位配置区） */
    category: Category | null;
    /** pending_review 命中排除次数（不计入改判分母） */
    pendingExcludedCount: number;
  }

  /** F6 分类维度行（8 类） */
  export interface ReviewFeedbackCategoryRow extends ReviewFeedbackRow {
    category: Category;
  }

  /** F6 复核反馈统计响应（GET /review-feedback，仅 ADMIN） */
  export interface ReviewFeedbackResult {
    generatedAt: string;
    /** D4 最小样本门槛（10） */
    sampleMin: number;
    /** 阈值调优提示线（0.4） */
    overturnHintThreshold: number;
    totalRuns: number;
    reviewedRuns: number;
    operators: ReviewFeedbackOperatorRow[];
    categories: ReviewFeedbackCategoryRow[];
  }
}

/**
 * 发起诊断（异步任务，返回 taskId 供轮询）
 */
export function triggerDiagnosisApi(data: DiagnosisApi.TriggerBody) {
  return requestClient.post<DiagnosisApi.TriggerResult>('/diagnosis/run', data);
}

/**
 * 诊断记录列表（筛选/分页）
 */
export function getDiagnosisRunsApi(params: DiagnosisApi.RunQuery) {
  return requestClient.get<PaginatedResponse<DiagnosisApi.RunListItem>>(
    '/diagnosis/runs',
    {
      params,
    },
  );
}

/** 每回路最新诊断概览（装置节点下钻 / loopId 单回路；无诊断记录的回路 runId=null） */
export function getDiagnosisRunsLatestApi(
  plantNodeId?: string,
  loopId?: string,
) {
  const params: { loopId?: string; plantNodeId?: string } = {};
  if (plantNodeId) params.plantNodeId = plantNodeId;
  if (loopId) params.loopId = loopId;
  return requestClient.get<{
    items: DiagnosisApi.LatestRunItem[];
    total: number;
  }>('/diagnosis/runs/latest', {
    params: Object.keys(params).length > 0 ? params : undefined,
  });
}

/**
 * 单次诊断完整详情（含算子结果/证据链/波形快照）
 */
export function getDiagnosisRunDetailApi(id: string) {
  return requestClient.get<DiagnosisApi.RunDetail>(`/diagnosis/runs/${id}`);
}

/**
 * 人工复核诊断结论（复核结论多选 + 意见；重复复核覆盖）
 */
export function reviewDiagnosisRunApi(
  id: string,
  data: DiagnosisApi.ReviewBody,
) {
  return requestClient.post<DiagnosisApi.RunListItem>(
    `/diagnosis/runs/${id}/review`,
    data,
  );
}

/**
 * 处置建议列表（首次拉取为空时后端按诊断/复核结论自动生成系统建议）
 */
export function getRunActionsApi(runId: string) {
  return requestClient.get<{ items: DiagnosisApi.ActionItem[] }>(
    `/diagnosis/runs/${runId}/actions`,
  );
}

/**
 * 人工新增处置措施（建议人/建议时间由后端按登录用户与服务器时间填入）
 */
export function createRunActionApi(
  runId: string,
  data: DiagnosisApi.CreateActionBody,
) {
  return requestClient.post<DiagnosisApi.ActionItem>(
    `/diagnosis/runs/${runId}/actions`,
    data,
  );
}

/**
 * 修改人工新增的处置措施（仅 MANUAL 可改；SYSTEM 建议不可编辑）
 */
export function updateRunActionApi(
  actionId: string,
  data: DiagnosisApi.CreateActionBody,
) {
  return requestClient.put<DiagnosisApi.ActionItem>(
    `/diagnosis/runs/actions/${actionId}`,
    data,
  );
}

/**
 * 删除处置建议（系统建议与人工新增均可删）
 */
export function deleteRunActionApi(actionId: string) {
  return requestClient.delete<{ deleted: boolean; id: string }>(
    `/diagnosis/runs/actions/${actionId}`,
  );
}

/**
 * 算子注册表元数据（算子说明 + AI 工具目录）
 */
export function getDiagnosisOperatorsApi() {
  return requestClient.get<DiagnosisApi.OperatorInfo[]>('/diagnosis/operators');
}

// ---------------------------------------------------------------------------
// 16 号文 F1/F2（诊断扩展 Phase A）：回路诊断档案 + 复诊对比
// 设计文档：docs/MVP设计/16-诊断模块功能扩展方案.md §5.1
// ---------------------------------------------------------------------------

/**
 * F1 回路诊断档案聚合（run 时间轴 + KPI 趋势 + 处置/整定关联事件）
 *
 * @param window 时间窗（'all' 后端截断到 90d，UI 需提示）
 */
export function getLoopArchiveApi(
  loopId: string,
  window: DiagnosisApi.ArchiveWindow = '90d',
) {
  return requestClient.get<DiagnosisApi.LoopArchive>(
    '/diagnosis/runs/loop-archive',
    { params: { loopId, window } },
  );
}

/**
 * F2 复诊对比（结论/特征值/KPI 结构化对照，后端组装 delta）
 *
 * @param mode adjacent=与上一条 SUCCESS run；verify=处置工单关联前后（404=能力不可用）
 * @param config 附加请求 config（能力探测场景传 { skipErrorMessage: true } 静默 404）
 */
export function getDiagnosisCompareApi(
  runId: string,
  mode: DiagnosisApi.CompareMode,
  config?: Record<string, any>,
) {
  return requestClient.get<DiagnosisApi.CompareResult>(
    `/diagnosis/runs/${runId}/compare`,
    { params: { mode }, ...config },
  );
}

// ---------------------------------------------------------------------------
// 16 号文 F3/F4（诊断扩展 Phase B）：覆盖台账 + 共性问题回路组
// 设计文档：docs/MVP设计/16-诊断模块功能扩展方案.md §5.1
// ---------------------------------------------------------------------------

/**
 * F3 诊断覆盖台账（新鲜度分档 + 数据不足 Top5 全员；调度执行块仅 ADMIN 返回）
 */
export function getDiagnosisCoverageApi() {
  return requestClient.get<DiagnosisApi.CoverageResult>('/diagnosis/coverage');
}

/**
 * F4 共性问题回路组（分类×装置下每回路最新 run 摘要；plantNodeId 递归含子单元）
 */
export function getDiagnosisCategoryCohortApi(
  category: DiagnosisApi.Category,
  plantNodeId?: string,
) {
  return requestClient.get<DiagnosisApi.CategoryCohortResult>(
    '/diagnosis/category-cohort',
    { params: plantNodeId ? { category, plantNodeId } : { category } },
  );
}

// ---------------------------------------------------------------------------
// 16 号文 F5/F6（诊断扩展 Phase C）：发起前预检徽标 + 复核反馈统计
// 设计文档：docs/MVP设计/16-诊断模块功能扩展方案.md §5.1
// ---------------------------------------------------------------------------

/**
 * F5 发起前数据充足性预检（kpi_snapshot_hourly 近 24h 行数密度徽标，零 TDengine）
 *
 * @param loopIds 回路 ID 列表（后端单次 ≤10，超出由调用方分批）
 * @param window 预检窗口（默认 24h；预期行数=窗口小时数）
 */
export function getDiagnosisPrecheckApi(
  loopIds: string[],
  window: DiagnosisApi.PrecheckWindow = '24h',
) {
  return requestClient.get<DiagnosisApi.PrecheckResult>('/diagnosis/precheck', {
    params: { loopIds: loopIds.join(','), window },
  });
}

/**
 * F6 复核反馈统计与阈值调优提示（仅 ADMIN；只读提示，不含任何自动调参入口）
 */
export function getDiagnosisReviewFeedbackApi() {
  return requestClient.get<DiagnosisApi.ReviewFeedbackResult>(
    '/diagnosis/review-feedback',
  );
}

// ---------------------------------------------------------------------------
// 诊断配置 CRUD（/configs/diagnosis，2026-08-19 诊断配置页）
// ---------------------------------------------------------------------------

export namespace DiagnosisConfigApi {
  /** 诊断配置项（GET/PUT 批量响应） */
  export interface ConfigItem {
    diagId: string;
    diagKey: null | string;
    diagName: null | string;
    label: null | string;
    algorithmType: null | string;
    calcMethod: null | string;
    params: null | Record<string, any>;
    threshold: null | Record<string, any>;
    isEnabled: boolean;
    algorithmVersion: null | string;
    updatedAt: null | string;
    updatedBy: null | string;
  }

  /** 批量响应 */
  export interface BatchResponse {
    items: ConfigItem[];
    updatedCount?: null | number;
  }

  /** 更新项（批量 PUT） */
  export interface UpdateItem {
    diagId: string;
    label?: null | string;
    algorithmType?: null | string;
    calcMethod?: null | string;
    params?: null | Record<string, any>;
    threshold?: null | Record<string, any>;
    isEnabled?: boolean | null;
  }

  /** 创建项（POST） */
  export interface CreateItem {
    diagKey: string;
    diagName: string;
    algorithmType: string;
    calcMethod?: null | string;
    params?: null | Record<string, any>;
    threshold?: null | Record<string, any>;
    isEnabled?: boolean;
  }
}

/**
 * 批量获取诊断配置（8 类诊断标签）
 */
export function getDiagnosisConfigsApi() {
  return requestClient.get<DiagnosisConfigApi.BatchResponse>(
    '/configs/diagnosis',
  );
}

/**
 * 批量更新诊断配置（事务性，任一项失败全部回滚；仅 ADMIN）
 */
export function updateDiagnosisConfigsApi(
  data: DiagnosisConfigApi.UpdateItem[],
) {
  return requestClient.put<DiagnosisConfigApi.BatchResponse>(
    '/configs/diagnosis',
    { items: data },
  );
}

/**
 * 新增单条诊断配置（diagKey 唯一；仅 ADMIN）
 */
export function createDiagnosisConfigApi(
  data: DiagnosisConfigApi.CreateItem,
) {
  return requestClient.post<DiagnosisConfigApi.BatchResponse>(
    '/configs/diagnosis',
    { item: data },
  );
}

/**
 * 删除单条诊断配置（仅 ADMIN）
 */
export function deleteDiagnosisConfigApi(diagId: string) {
  return requestClient.delete<{ deletedDiagId: string }>(
    `/configs/diagnosis/${diagId}`,
  );
}

/**
 * 获取诊断配置版本历史（快照模式，仅 ADMIN）
 */
export function getDiagnosisConfigHistoryApi() {
  return requestClient.get<MetricApi.VersionHistorySchema>(
    '/configs/diagnosis/history',
  );
}

/**
 * 回滚诊断配置到指定版本（仅 ADMIN）
 */
export function rollbackDiagnosisConfigApi(version: number) {
  return requestClient.post<{ version: number }>(
    `/configs/diagnosis/${version}/rollback`,
  );
}

/**
 * 诊断记录 CSV 导出（返回文本，页面侧构造 Blob 下载）
 */
export function exportDiagnosisRunsApi(
  params: Omit<DiagnosisApi.RunQuery, 'page' | 'pageSize'>,
) {
  return requestClient.get<string>('/diagnosis/export', {
    params,
    responseType: 'blob',
  });
}
