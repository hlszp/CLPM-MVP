/**
 * 监控模块 API——关注队列与工作台摘要（整改方案 §8）
 *
 * 关注队列统一聚合 ALERT/DEGRADATION/DATA_QUALITY/TRACKER/VERIFICATION/FITNESS_ABNORMAL 六类来源。
 * 动作由服务端按角色生成，前端不自行推断权限。
 */
import { requestClient } from '#/api/request';

export namespace MonitorApi {
  /** 关注来源 */
  export type AttentionSource =
    | 'ALERT'
    | 'DATA_QUALITY'
    | 'DEGRADATION'
    | 'FITNESS_ABNORMAL';

  /** 优先级 */
  export type AttentionPriority = 'HIGH' | 'LOW' | 'MEDIUM' | 'URGENT';

  /** 统一状态 */
  export type AttentionStatus =
    | 'ACKNOWLEDGED'
    | 'IN_PROGRESS'
    | 'OPEN'
    | 'SUPPRESSED';

  /** 可信度等级 */
  export type ConfidenceLevel = 'A' | 'B' | 'C' | 'D' | 'E';

  /** 动作类型 */
  export type AttentionActionType =
    | 'ACKNOWLEDGE'
    | 'BACK_TO_OVERVIEW'
    | 'MARK_FALSE_POSITIVE'
    | 'OPEN_WORKBENCH'
    | 'RESOLVE'
    | 'VIEW_ALERT_HISTORY'
    | 'VIEW_DETAIL';

  /** 动作跳转目标 */
  export interface AttentionActionTarget {
    route:
      | '/dashboard/workbench'
      | '/monitor/alerts'
      | '/monitor/loop-workbench';
    query: Record<string, string>;
  }

  /** 动作 */
  export interface AttentionAction {
    type: AttentionActionType;
    label: string;
    enabled: boolean;
    disabledReason?: string;
    target?: AttentionActionTarget;
  }

  /** 关注项 */
  export interface AttentionItem {
    attentionId: string;
    source: AttentionSource;
    sourceId: string;
    loopId: string;
    tagName: string;
    unitName?: string;
    title: string;
    summary: string;
    priority: AttentionPriority;
    sourceSeverity?: string;
    status: AttentionStatus;
    sourceStatus: string;
    rankReasons: string[];
    occurredAt: string;
    updatedAt?: string;
    confidenceLevel?: ConfidenceLevel;
    score?: number;
    scoreDelta?: number;
    eventId?: string;
    taskId?: string;
    primaryAction: AttentionAction;
    actions: AttentionAction[];
    isOverdue?: boolean;
    /** P2 IA优化：适用性等级（仅 FITNESS_ABNORMAL 来源有） */
    fitnessLevel?: null | string;
    /** P2 IA优化：适用性原因标签（仅 FITNESS_ABNORMAL 来源有） */
    fitnessTags?: null | string[];
  }

  /** 关注组（按回路合并，v1.1+） */
  export interface AttentionGroup {
    groupId: string;
    loopId: string;
    tagName: string;
    unitName?: string;
    priority: AttentionPriority;
    priorityLabel: string;
    status: AttentionStatus;
    sources: AttentionSource[];
    sourceLabels: string[];
    summary: string;
    title: string;
    updatedAt?: string;
    isOverdue: boolean;
    itemCount: number;
    rankReasons: string[];
    primaryAction: AttentionAction;
    actions: AttentionAction[];
    children: AttentionItem[];
    /** P2 IA优化：组内 FITNESS_ABNORMAL 对应的适用性等级（若有） */
    fitnessLevel?: null | string;
    /** P2 IA优化：组内 FITNESS_ABNORMAL 对应的适用性原因标签（若有） */
    fitnessTags?: null | string[];
  }

  /** 聚合统计 */
  export interface AttentionAggregates {
    byPriority: Record<string, number>;
    bySource: Record<string, number>;
    byStatus: Record<string, number>;
    byGroupPriority?: Record<string, number>;
    groupCount?: number;
    openCount?: number;
    urgentCount?: number;
    dataQualityCount?: number;
  }

  /** 关注队列列表响应 */
  export interface AttentionListData {
    items: AttentionGroup[];
    total: number;
    totalGroups: number;
    totalItems: number;
    page: number;
    pageSize: number;
    aggregates: AttentionAggregates;
    truncated?: Record<string, boolean>;
    loadedAt?: string;
  }

  /** 关注队列查询参数 */
  export interface AttentionQueryParams {
    plantNodeId?: string;
    source?: AttentionSource[];
    priority?: AttentionPriority[];
    status?: AttentionStatus[];
    loopId?: string;
    keyword?: string;
    page?: number;
    pageSize?: number;
  }

  // ===== 工作台摘要 summary（MW-P3-01 ~ MW-P3-04）=====

  /** 生命周期阶段名 */
  export type LifecycleStageName = 'ASSESS' | 'MONITOR';

  /** 生命周期阶段状态 */
  export type LifecycleStageStatus =
    | 'BLOCKED'
    | 'COMPLETED'
    | 'INCONCLUSIVE'
    | 'NOT_REQUIRED'
    | 'NOT_STARTED'
    | 'OVERDUE'
    | 'READY'
    | 'RUNNING';

  /** 生命周期阶段 */
  export interface LifecycleStage {
    stage: LifecycleStageName;
    status: LifecycleStageStatus;
    resultAt?: null | string;
    reason: string;
  }

  /** 生命周期 */
  export interface Lifecycle {
    stages: LifecycleStage[];
    currentStage?: LifecycleStageName | null;
  }

  /** nextAction 动作类型 */
  export type NextActionType =
    | 'CONTINUE_MONITORING'
    | 'FIX_TAG_CONFIG'
    | 'IMPORT_DATA'
    | 'RUN_ASSESSMENT';

  /** 推荐下一步 */
  export interface NextAction {
    actionType: NextActionType;
    label: string;
    reason: string;
    enabled: boolean;
    disabledReason?: null | string;
    target?: null | {
      query: Record<string, string>;
      route: '/monitor/loop-workbench';
    };
  }

  /** 运行态 */
  export interface RuntimeState {
    pv?: null | number;
    sp?: null | number;
    op?: null | number;
    mode?: null | number;
    modeLabel?: null | string;
    pvQuality?: null | string;
    pvUnit?: null | string;
    pvRange?: null | { max: null | number; min: null | number };
    opRange?: null | { max: null | number; min: null | number };
    readAt?: null | string;
    controlMode?: null | string;
  }

  /** 数据新鲜度 */
  export interface DataFreshness {
    status: 'DELAYED' | 'FRESH' | 'UNKNOWN';
    thresholdSeconds: number;
    reason?: null | string;
  }

  /** 数据健康度 */
  export interface DataHealth {
    validRate?: null | number;
    confidenceLevel?: null | string;
    pvCompleteness?: null | number;
    overallCompleteness?: null | number;
    integrityStatus?: null | string;
  }

  /** 评分趋势 */
  export interface ScoreTrend {
    score?: null | number;
    scoreDelta?: null | number;
    dayTrend?: null | string;
    resultAt?: null | string;
    confidenceLevel?: null | string;
    status?: null | string;
  }

  /** 活跃关注项汇总 */
  export interface ActiveAttentionSummary {
    total: number;
    highestPriority?: AttentionPriority | null;
    items: AttentionItem[];
  }

  /** 评估摘要 */
  export interface AssessmentSummary {
    score?: null | number;
    confidenceLevel?: null | string;
    status?: null | string;
    resultAt?: null | string;
    timeWindow?: null | string;
    summary?: null | string;
  }

  /** 诊断摘要 */
  export interface DiagnosisSummary {
    diagLabel?: null | string;
    confidence?: null | number;
    status?: null | string;
    resultAt?: null | string;
    taskId?: null | string;
    labels?: string[];
    summary?: null | string;
  }

  /** 整定摘要 */
  export interface TuningSummary {
    status?: null | string;
    modelType?: null | string;
    algorithm?: null | string;
    confidenceLevel?: null | string;
    resultAt?: null | string;
    currentPid?: null | { d?: number; i?: number; p?: number };
    recommendedPid?: null | { d?: number; i?: number; p?: number };
    fittingScore?: null | number;
    riskLevel?: null | string;
    summary?: null | string;
  }

  /** 实施前后对比单项 KPI（MW-P3-09） */
  export interface EffectCompareKpiItem {
    metricKey: string;
    metricName: string;
    before?: null | number;
    after?: null | number;
    change?: null | number;
    improved?: boolean | null;
  }

  /** 实施前后对比（MW-P3-09） */
  export interface EffectCompare {
    status: 'COMPLETED' | 'INCONCLUSIVE' | 'PENDING';
    conclusion?: 'DETERIORATED' | 'IMPROVED' | 'NO_CHANGE' | null;
    conclusionLabel?: null | string;
    implementedAt?: null | string;
    verifiedAt?: null | string;
    timeWindow?: null | {
      afterEnd: string;
      afterStart: string;
      beforeEnd: string;
      beforeStart: string;
    };
    scoreChange?: null | {
      after?: null | number;
      before?: null | number;
      change?: null | number;
      improved?: boolean | null;
    };
    coreKpiChanges: EffectCompareKpiItem[];
    pidBefore?: null | { d?: number; i?: number; p?: number };
    pidAfter?: null | { d?: number; i?: number; p?: number };
    dataInsufficient: boolean;
    confidence?: null | string;
    reason?: null | string;
  }

  /** Tracker 时间线 */
  export interface TrackerTimeline {
    trackerId: string;
    diagnosisLabel?: null | string;
    actionStatus: string;
    severity?: null | string;
    triggerType?: null | string;
    assignee?: null | string;
    createdAt?: null | string;
    updatedAt?: null | string;
    implementedAt?: null | string;
    implementedBy?: null | string;
    newPid?: null | { d?: number; i?: number; p?: number };
    mocRef?: null | string;
    mocNotApplicable?: boolean | null;
    plannedAt?: null | string;
    closedAt?: null | string;
    effectVerified?: boolean | null;
    effectVerifiedAt?: null | string;
    abCompareSummary?: null | string;
    effectCompare?: EffectCompare | null;
    reopenReason?: null | string;
    isOverdue: boolean;
    overdueHours?: null | number;
  }

  /** 工作台摘要响应 */
  export interface WorkbenchSummary {
    loopId: string;
    tagName: string;
    description?: null | string;
    unitName?: null | string;
    loopType?: null | string;
    controlType?: null | string;
    loopStatus?: null | string;
    isActive?: boolean | null;
    importanceLevel?: null | number;
    runtime: RuntimeState;
    dataFreshness: DataFreshness;
    dataHealth: DataHealth;
    scoreTrend: ScoreTrend;
    activeAttention: ActiveAttentionSummary;
    assessment?: AssessmentSummary | null;
    diagnosis?: DiagnosisSummary | null;
    tuning?: null | TuningSummary;
    trackerTimeline?: null | TrackerTimeline;
    lifecycle: Lifecycle;
    nextAction: NextAction;
    partial: boolean;
    unavailableSections: string[];
    /** P2 IA优化：适用性等级 L0~L4；null 表示待评估 */
    fitnessLevel?: 'L0' | 'L1' | 'L2' | 'L3' | 'L4' | null | string;
    /** P2 IA优化：适用性原因标签枚举 */
    fitnessTags?: null | string[];
  }
}

const BASE = '/monitor';

/**
 * 查询统一关注队列。
 *
 * 动作由服务端按角色生成，前端直接使用 primaryAction/actions，
 * 不自行推断权限或隐藏按钮。
 */
export function getAttentionListApi(params: MonitorApi.AttentionQueryParams) {
  return requestClient.get<MonitorApi.AttentionListData>(`${BASE}/attention`, {
    params,
  });
}

/**
 * 工作台首屏摘要（BFF）——一次返回首屏所需的全部摘要。
 *
 * 单个来源失败时返回 ``partial=true`` 且该来源在 ``unavailableSections`` 中，
 * 其他来源正常返回，不让整页 500。
 *
 * 权限：ADMIN/IC/PE/EXPERT 可读，Sponsor 不开放（前端不发起）。
 */
export function getWorkbenchSummaryApi(loopId: string) {
  return requestClient.get<MonitorApi.WorkbenchSummary>(
    `${BASE}/loops/${loopId}/summary`,
  );
}
