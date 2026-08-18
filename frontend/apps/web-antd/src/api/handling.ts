/**
 * 处置模块 API（Phase 1）
 *
 * 设计文档：docs/MVP设计/08-处置模块设计方案.md §6 API 定义
 * 后端：backend/app/api/v1/endpoints/handling.py（/api/v1/handling/*）
 */

import type { PageQuery, PaginatedResponse } from '#/api/types';

import { requestClient } from '#/api/request';

export namespace HandlingApi {
  /** 处置状态（§4.1，5 态 + 忽略终态） */
  export type Status =
    | 'CLOSED'
    | 'HANDLING'
    | 'IGNORED'
    | 'PENDING'
    | 'REOPENED'
    | 'VERIFYING';

  /** 处置类型（§5.1，8 类） */
  export type ActionType =
    | 'INSTRUMENT'
    | 'LINK'
    | 'OTHER'
    | 'PROCESS'
    | 'RECONFIG'
    | 'TUNING'
    | 'UTILIZATION'
    | 'VALVE';

  export type Source = 'MANUAL' | 'SYSTEM';
  export type VerifyResult = 'EFFECTIVE' | 'INEFFECTIVE';

  /** KPI 快照摘要（§4.3：score + 六率 + 可信度 + 窗口） */
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
  }

  /** PID 参数组（TUNING 类型 actionDetail 子字段） */
  export interface PidValues {
    p?: null | number;
    i?: null | number;
    d?: null | number;
  }

  /** 清单行（GET /items，§6.1） */
  export interface ListItem {
    id: string;
    runId: string;
    loopId: string;
    loopTagName: string;
    loopDescription?: null | string;
    importanceLevel?: null | number;
    unitId?: null | string;
    /** 装置.单元（plant_node 树回溯） */
    unitPath?: null | string;
    source: Source;
    category?: null | string;
    categoryLabel?: null | string;
    content: string;
    actionType?: ActionType | null;
    actionTypeLabel?: null | string;
    status: Status;
    statusLabel: string;
    priority?: null | number;
    suggestedBy: string;
    suggestedAt?: null | string;
    handledBy?: null | string;
    handledAt?: null | string;
    submittedAt?: null | string;
    verifyResult?: null | VerifyResult;
    verifyResultLabel?: null | string;
    verifiedBy?: null | string;
    verifiedAt?: null | string;
    updatedAt?: null | string;
  }

  /** 处置详情（GET /items/{id}：清单行 + 结构化字段） */
  export interface Detail extends ListItem {
    basis?: null | string;
    actionDetail?: null | Record<string, any>;
    kpiBefore?: KpiSummary | null;
    kpiAfter?: KpiSummary | null;
    verifyRunId?: null | string;
    verifyNote?: null | string;
    ignoreReason?: null | string;
    tuningRecordId?: null | string;
  }

  /** 状态统计（GET /items/stats） */
  export interface Stats {
    counts: Record<Status, number>;
    /** 本月闭环数（北京时间月界） */
    monthClosed: number;
  }

  export interface ListQuery extends PageQuery {
    /** 状态多值，逗号分隔 */
    status?: string;
    actionType?: ActionType;
    source?: Source;
    plantNodeId?: string;
    loopId?: string;
    importanceLevel?: number;
    keyword?: string;
    startTime?: string;
    endTime?: string;
  }

  /** 开始处置请求体（§6.2：actionType 必填；handler 缺省=当前登录用户） */
  export interface StartBody {
    actionType: ActionType;
    handler?: string;
    actionDetail?: Record<string, any>;
    pidBefore?: PidValues;
  }

  /** 提交验证请求体（TUNING 必填 pidAfter） */
  export interface SubmitBody {
    actionDetail: Record<string, any>;
  }

  export interface VerifyBody {
    verifyResult: VerifyResult;
    verifyNote?: string;
    verifyRunId?: string;
  }

  export interface IgnoreBody {
    ignoreReason: string;
  }

  /** KPI 前后对比预览（POST /items/{id}/kpi-comparison，不落库） */
  export interface KpiComparison {
    id: string;
    loopId: string;
    kpiBefore: KpiSummary | null;
    kpiAfter: KpiSummary | null;
    window: {
      afterEnd: null | string;
      afterStart: null | string;
      beforeEnd: null | string;
      beforeStart: null | string;
    };
  }

  /** 按回路聚合行（GET /loops，档案页主查询，§6.4） */
  export interface LoopAggregateItem {
    loopId: string;
    loopTagName: string;
    loopDescription?: null | string;
    importanceLevel?: null | number;
    unitPath?: null | string;
    /** 六状态计数（小写键） */
    counts: {
      closed: number;
      handling: number;
      ignored: number;
      pending: number;
      reopened: number;
      verifying: number;
    };
    /** 累计处置项数 */
    totalCount: number;
    lastSuggestedAt?: null | string;
    lastHandledAt?: null | string;
    lastHandledBy?: null | string;
    /** 最近一次闭环 kpi_after.score − kpi_before.score（无闭环为 null） */
    lastClosedKpiDelta?: null | number;
  }

  /** 档案页查询（GET /loops） */
  export interface LoopQuery extends PageQuery {
    plantNodeId?: string;
    importanceLevel?: number;
    keyword?: string;
    /** 仅看有在途（pending+handling+verifying>0） */
    activeOnly?: boolean;
    /** recent=最近建议时间倒序（默认）/ reopened=重开次数倒序 */
    sort?: 'recent' | 'reopened';
  }

  /** 统计页汇总指标（GET /statistics，§6.4；无 CLOSED 记录时为 null） */
  export interface StatisticsSummary {
    /** 本月闭环数 */
    closedThisMonth: null | number;
    /** 闭环率（closed / 已验证，无验证记录为 null） */
    closeRate: null | number;
    /** 平均处置时长（suggested_at → verified_at 均值，小时） */
    avgCycleHours: null | number;
    /** 无效重开率（INEFFECTIVE / 已验证） */
    ineffectiveRate: null | number;
    /** 平均 KPI 改善分（闭环项 kpi delta 均值） */
    avgKpiDelta: null | number;
  }

  /** 月度趋势行 */
  export interface MonthlyTrendItem {
    /** 月份（YYYY-MM，北京时间） */
    month: string;
    closed: number;
    closeRate: null | number;
  }

  /** 处置类型分布行 */
  export interface TypeDistItem {
    type: null | string;
    label: string;
    count: number;
  }

  /** 装置分布行 */
  export interface UnitDistItem {
    unit: string;
    closed: number;
  }

  /** Top 问题回路行（重开次数降序） */
  export interface TopLoopItem {
    loopId: string;
    loopTagName: string;
    unitPath?: null | string;
    totalCount: number;
    reopened: number;
    lastClosedKpiDelta?: null | number;
  }

  /** 统计页数据（GET /statistics） */
  export interface StatisticsData {
    summary: StatisticsSummary;
    monthly: MonthlyTrendItem[];
    byType: TypeDistItem[];
    byUnit: UnitDistItem[];
    topLoops: TopLoopItem[];
  }
}

/** 处置清单（分页/筛选；排序=状态分组优先级 + updatedAt DESC） */
export function getHandlingItemsApi(params: HandlingApi.ListQuery) {
  return requestClient.get<PaginatedResponse<HandlingApi.ListItem>>(
    '/handling/items',
    {
      params,
    },
  );
}

/** 状态统计（清单页顶部卡片：各状态计数 + 本月闭环数） */
export function getHandlingStatsApi() {
  return requestClient.get<HandlingApi.Stats>('/handling/items/stats');
}

/** 处置详情（清单行全部字段 + action_detail/kpi 固化/ignore_reason/basis） */
export function getHandlingItemApi(id: string) {
  return requestClient.get<HandlingApi.Detail>(`/handling/items/${id}`);
}

/** 开始处置（PENDING/REOPENED → HANDLING） */
export function startHandlingApi(id: string, data: HandlingApi.StartBody) {
  return requestClient.post<HandlingApi.Detail>(
    `/handling/items/${id}/start`,
    data,
  );
}

/** 提交验证（HANDLING → VERIFYING；TUNING 必填 pidAfter） */
export function submitHandlingApi(id: string, data: HandlingApi.SubmitBody) {
  return requestClient.post<HandlingApi.Detail>(
    `/handling/items/${id}/submit`,
    data,
  );
}

/** 验证结论（VERIFYING → CLOSED/REOPENED；服务端固化 KPI 前后快照） */
export function verifyHandlingApi(id: string, data: HandlingApi.VerifyBody) {
  return requestClient.post<HandlingApi.Detail>(
    `/handling/items/${id}/verify`,
    data,
  );
}

/** 忽略（PENDING → IGNORED 终态；ignoreReason 必填） */
export function ignoreHandlingApi(id: string, data: HandlingApi.IgnoreBody) {
  return requestClient.post<HandlingApi.Detail>(
    `/handling/items/${id}/ignore`,
    data,
  );
}

/** KPI 前后对比预览（VERIFYING 阶段实时拉取，不落库） */
export function getKpiComparisonApi(id: string) {
  return requestClient.post<HandlingApi.KpiComparison>(
    `/handling/items/${id}/kpi-comparison`,
  );
}

/** 按回路聚合（档案页主查询，§6.4；Phase 1F 后端待交付） */
export function getHandlingLoopsApi(params: HandlingApi.LoopQuery) {
  return requestClient.get<PaginatedResponse<HandlingApi.LoopAggregateItem>>(
    '/handling/loops',
    { params },
  );
}

/** 处置统计页数据（§6.4；Phase 1F 后端待交付） */
export function getHandlingStatisticsApi(months = 6) {
  return requestClient.get<HandlingApi.StatisticsData>('/handling/statistics', {
    params: { months },
  });
}
