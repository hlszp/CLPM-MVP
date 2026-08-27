/**
 * 处置模块 API
 *
 * 设计文档：docs/MVP设计/08-处置模块设计方案.md
 * - v2.0 契约（§3 双实体 / §4 状态机 / §6.1-6.2 建议+工单 API）：双实体重构
 *   （处置建议 loop_action_item 改造 + 处置工单 handling_order 新建），
 *   H1 后端并行开发中——新端点调用失败属预期，调用方需优雅降级。
 * - v1.x 契约（Phase 1，§6 /items 系列）：仅供 archive.vue / statistics.vue
 *   存量页面引用，**勿删**（业务口径切换属 H2，/items 系列届时下线）。
 * 后端：backend/app/api/v1/endpoints/handling.py（/api/v1/handling/*）
 * 时间约定：后端返回 naive UTC ISO（前端 formatLocalTime 补 Z 转本地）。
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

  /** 按回路聚合行（GET /loops，档案页主查询，§6.3 双实体口径） */
  export interface LoopAggregateItem {
    loopId: string;
    loopTagName: string;
    loopDescription?: null | string;
    importanceLevel?: null | number;
    unitPath?: null | string;
    /** 建议五态计数（小写键） */
    suggestionCounts: {
      accepted: number;
      converted: number;
      ignored: number;
      pending: number;
      rejected: number;
    };
    /** 建议累计数 */
    suggestionTotal: number;
    /** 工单六态计数（小写键） */
    orderCounts: {
      cancelled: number;
      closed: number;
      executing: number;
      pending: number;
      reopened: number;
      verifying: number;
    };
    /** 工单累计数 */
    orderTotal: number;
    /** 闭环率（closed / 已验证，无验证记录为 null） */
    closeRate?: null | number;
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
    /** 状态分布筛选（多值逗号分隔；建议/工单该状态计数>0 即命中） */
    status?: string;
    /** KPI 改善筛选：improved/degraded/closed/unclosed */
    kpiDelta?: 'closed' | 'degraded' | 'improved' | 'unclosed';
    /** 仅看有在途（待审核/已接受建议或非终态工单 >0） */
    activeOnly?: boolean;
    /** recent=最近活动倒序（默认）/ reopened=工单重开次数倒序 */
    sort?: 'recent' | 'reopened';
  }

  /** 统计页汇总指标（GET /statistics，§6.3；无数据时相关项为 null） */
  export interface StatisticsSummary {
    /** 本月闭环数 */
    closedThisMonth: null | number;
    /** 闭环率（closed / 已验证，无验证记录为 null） */
    closeRate: null | number;
    /** 平均处置时长（创建 → 验证闭环均值，小时） */
    avgCycleHours: null | number;
    /** 无效重开率（INEFFECTIVE / 已验证） */
    ineffectiveRate: null | number;
    /** 平均 KPI 改善分（闭环工单 kpi delta 均值） */
    avgKpiDelta: null | number;
    /** 驳回率（建议侧 REJECTED / 已审核） */
    rejectRate: null | number;
    /** 平均排程周期（工单创建 → 开工均值，小时） */
    avgScheduleHours: null | number;
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

  /** Top 问题回路行（重开次数降序，工单口径） */
  export interface TopLoopItem {
    loopId: string;
    loopTagName: string;
    unitPath?: null | string;
    /** 工单总数 */
    orderTotal: number;
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

  // =========================================================================
  // v2.0 双实体契约（§3 数据模型 / §4 状态机 / §6.1-6.2 API）
  // =========================================================================

  /** 建议状态（§4.1，4 态：PENDING → ACCEPTED → CONVERTED；REJECTED/IGNORED 终态） */
  export type SuggestionStatus =
    | 'ACCEPTED'
    | 'CONVERTED'
    | 'IGNORED'
    | 'PENDING'
    | 'REJECTED';

  /** 工单状态（§4.2，6 态：PENDING → EXECUTING → VERIFYING → CLOSED；REOPENED/CANCELLED） */
  export type OrderStatus =
    | 'CANCELLED'
    | 'CLOSED'
    | 'EXECUTING'
    | 'PENDING'
    | 'REOPENED'
    | 'VERIFYING';

  /** 工单来源：建议转化 / 手动新建 */
  export type OrderSource = 'DIAGNOSIS' | 'MANUAL';

  /** 建议清单行（GET /suggestions，§6.1） */
  export interface SuggestionItem {
    id: string;
    /** 诊断来源 run（手动建议为 null） */
    runId: null | string;
    loopId: string;
    loopTagName: string;
    loopDescription?: null | string;
    importanceLevel?: null | number;
    /** 装置.单元（plant_node 树回溯） */
    unitPath?: null | string;
    source: Source;
    category?: null | string;
    categoryLabel?: null | string;
    content: string;
    basis?: null | string;
    priority?: null | number;
    status: SuggestionStatus;
    statusLabel: string;
    suggestedBy: string;
    suggestedAt?: null | string;
    /** 审核人 / 审核时间（accept 或 reject 时记录） */
    reviewedBy?: null | string;
    reviewedAt?: null | string;
    rejectedReason?: null | string;
    ignoreReason?: null | string;
    /** 转工单回链 */
    convertedOrderId?: null | string;
    convertedOrderNo?: null | string;
  }

  /** 建议清单查询（§6.1；排序=状态分组 + suggested_at DESC） */
  export interface SuggestionQuery extends PageQuery {
    /** 状态多值，逗号分隔 */
    status?: string;
    source?: Source;
    plantNodeId?: string;
    loopId?: string;
    importanceLevel?: number;
    keyword?: string;
    startTime?: string;
    endTime?: string;
  }

  /** 手动新增建议（POST /suggestions，§6.1；source=MANUAL，run_id 置空） */
  export interface CreateSuggestionBody {
    loopId: string;
    content: string;
    basis?: string;
    priority?: number;
  }

  /** 驳回建议（PENDING → REJECTED；rejectedReason 必填） */
  export interface RejectSuggestionBody {
    rejectedReason: string;
  }

  /** 转工单（POST /suggestions/convert，§6.1；多建议合一单） */
  export interface ConvertSuggestionsBody {
    /** ≥1，须同为 ACCEPTED（前端另校验同回路） */
    suggestionIds: string[];
    actionType: ActionType;
    plannedAt?: string;
    handler?: string;
    title?: string;
  }

  /** 工单清单行（GET /orders，§6.2） */
  export interface OrderItem {
    id: string;
    /** 处置编号 HD-YYYYMMDD-NNN（唯一） */
    orderNo: string;
    loopId: string;
    loopTagName: string;
    loopDescription?: null | string;
    unitPath?: null | string;
    importanceLevel?: null | number;
    source: OrderSource;
    title: string;
    actionType: ActionType;
    actionTypeLabel?: null | string;
    status: OrderStatus;
    statusLabel: string;
    plannedAt?: null | string;
    plannedBy?: null | string;
    handler?: null | string;
    startedAt?: null | string;
    feedbackCount?: null | number;
    submittedAt?: null | string;
    verifyResult?: null | VerifyResult;
    verifiedBy?: null | string;
    verifiedAt?: null | string;
    updatedAt?: null | string;
  }

  /** 执行反馈条目（feedback_log JSONB 数组元素） */
  export interface FeedbackEntry {
    at: string;
    by: string;
    content: string;
  }

  /** 工单来源建议摘要（详情 suggestions 字段） */
  export interface OrderSuggestionRef {
    id: string;
    content: string;
    status: SuggestionStatus;
    statusLabel: string;
  }

  /** 工单详情（GET /orders/{id}，§6.2：清单行 + 执行域字段） */
  export interface OrderDetail extends OrderItem {
    actionDetail?: null | Record<string, any>;
    feedbackLog?: FeedbackEntry[] | null;
    kpiBefore?: KpiSummary | null;
    kpiAfter?: KpiSummary | null;
    verifyRunId?: null | string;
    verifyNote?: null | string;
    cancelReason?: null | string;
    suggestions?: null | OrderSuggestionRef[];
  }

  /** 工单清单查询（§6.2；排序=状态分组 + updated_at DESC） */
  export interface OrderQuery extends PageQuery {
    status?: OrderStatus;
    actionType?: ActionType;
    source?: OrderSource;
    plantNodeId?: string;
    loopId?: string;
    /** 处置人模糊匹配 */
    handler?: string;
    /** 编号/回路/标题关键字 */
    keyword?: string;
    plannedBefore?: string;
    plannedAfter?: string;
  }

  /** 手动新建工单（POST /orders，§6.2；source=MANUAL） */
  export interface CreateOrderBody {
    loopId: string;
    actionType: ActionType;
    title?: string;
    plannedAt?: string;
    handler?: string;
    actionDetail?: Record<string, any>;
  }

  /** 开工（PENDING/REOPENED → EXECUTING；§6.2） */
  export interface StartOrderBody {
    handler?: string;
    actionDetail?: Record<string, any>;
    pidBefore?: PidValues;
  }

  /** 执行反馈（EXECUTING 中追加 feedback_log；content 必填） */
  export interface FeedbackOrderBody {
    content: string;
  }

  /** 提交验证（EXECUTING → VERIFYING；TUNING 必填 pidAfter） */
  export interface SubmitOrderBody {
    actionDetail: Record<string, any>;
  }

  /** 验证结论（VERIFYING → CLOSED/REOPENED；服务端固化 KPI） */
  export interface VerifyOrderBody {
    verifyResult: VerifyResult;
    verifyNote?: string;
    verifyRunId?: string;
  }

  /** 作废（PENDING → CANCELLED；cancelReason 必填） */
  export interface CancelOrderBody {
    cancelReason: string;
  }
}

// ===========================================================================
// v1.x items 系列端点已废弃（后端无 /handling/items，批次 C 清理死函数）；
// 双实体口径请走 suggestions / orders 系列
// ===========================================================================

/** 按回路聚合（档案页主查询，§6.3 双实体口径） */
export function getHandlingLoopsApi(params: HandlingApi.LoopQuery) {
  return requestClient.get<PaginatedResponse<HandlingApi.LoopAggregateItem>>(
    '/handling/loops',
    { params },
  );
}

/** 处置统计页数据（§6.3 工单维度 + 建议驳回率） */
export function getHandlingStatisticsApi(months = 6) {
  return requestClient.get<HandlingApi.StatisticsData>('/handling/statistics', {
    params: { months },
  });
}

// ===========================================================================
// v2.0 双实体端点（§6.1 建议侧 / §6.2 工单侧）
// H1 后端并行开发中：调用失败由调用方优雅降级（空态/错误提示，不白屏）
// ===========================================================================

/** 建议清单（分页/筛选；排序=状态分组 PENDING→ACCEPTED→其他 + suggested_at DESC） */
export function getHandlingSuggestionsApi(
  params: HandlingApi.SuggestionQuery,
) {
  return requestClient.get<PaginatedResponse<HandlingApi.SuggestionItem>>(
    '/handling/suggestions',
    { params },
  );
}

/** 手动新增建议（source=MANUAL，run_id 置空，§6.1） */
export function createSuggestionApi(data: HandlingApi.CreateSuggestionBody) {
  return requestClient.post<HandlingApi.SuggestionItem>(
    '/handling/suggestions',
    data,
  );
}

/** 接受建议（PENDING → ACCEPTED，§6.1） */
export function acceptSuggestionApi(id: string) {
  return requestClient.post<HandlingApi.SuggestionItem>(
    `/handling/suggestions/${id}/accept`,
    {},
  );
}

/** 驳回建议（PENDING → REJECTED 终态；rejectedReason 必填，§6.1） */
export function rejectSuggestionApi(
  id: string,
  data: HandlingApi.RejectSuggestionBody,
) {
  return requestClient.post<HandlingApi.SuggestionItem>(
    `/handling/suggestions/${id}/reject`,
    data,
  );
}

/** 忽略建议（PENDING → IGNORED 终态；ignoreReason 必填，§6.1） */
export function ignoreSuggestionApi(id: string, data: HandlingApi.IgnoreBody) {
  return requestClient.post<HandlingApi.SuggestionItem>(
    `/handling/suggestions/${id}/ignore`,
    data,
  );
}

/** 转工单（ACCEPTED 多选 → 一个工单；返回工单详情，§6.1） */
export function convertSuggestionsApi(
  data: HandlingApi.ConvertSuggestionsBody,
) {
  return requestClient.post<HandlingApi.OrderDetail>(
    '/handling/suggestions/convert',
    data,
  );
}

/** 工单清单（分页/筛选；排序=状态分组 PENDING→REOPENED→EXECUTING→VERIFYING→其他 + updated_at DESC） */
export function getHandlingOrdersApi(params: HandlingApi.OrderQuery) {
  return requestClient.get<PaginatedResponse<HandlingApi.OrderItem>>(
    '/handling/orders',
    { params },
  );
}

/** 工单 CSV 导出（GAP-4：筛选同 /orders，上限 5000 行；返回文本，页面侧构造 Blob 下载） */
export function exportHandlingOrdersApi(
  params: Omit<HandlingApi.OrderQuery, 'page' | 'pageSize'>,
) {
  return requestClient.get<string>('/handling/orders/export', {
    params,
    responseType: 'blob',
  });
}

/** 工单详情（清单行 + action_detail/feedback_log/KPI 固化/来源建议摘要，§6.2） */
export function getHandlingOrderApi(id: string) {
  return requestClient.get<HandlingApi.OrderDetail>(`/handling/orders/${id}`);
}

/** 手动新建工单（source=MANUAL，§6.2） */
export function createOrderApi(data: HandlingApi.CreateOrderBody) {
  return requestClient.post<HandlingApi.OrderDetail>('/handling/orders', data);
}

/** 开工（PENDING/REOPENED → EXECUTING；handler 缺省=当前登录用户，§6.2） */
export function startOrderApi(id: string, data: HandlingApi.StartOrderBody) {
  return requestClient.post<HandlingApi.OrderDetail>(
    `/handling/orders/${id}/start`,
    data,
  );
}

/** 执行反馈（EXECUTING 中追加 feedback_log；content 必填，状态不变，§6.2） */
export function feedbackOrderApi(
  id: string,
  data: HandlingApi.FeedbackOrderBody,
) {
  return requestClient.post<HandlingApi.OrderDetail>(
    `/handling/orders/${id}/feedback`,
    data,
  );
}

/** 提交验证（EXECUTING → VERIFYING；TUNING 必填 pidAfter，§6.2） */
export function submitOrderApi(id: string, data: HandlingApi.SubmitOrderBody) {
  return requestClient.post<HandlingApi.OrderDetail>(
    `/handling/orders/${id}/submit`,
    data,
  );
}

/** 验证结论（VERIFYING → CLOSED/REOPENED；服务端固化 KPI 前后快照，§6.2） */
export function verifyOrderApi(id: string, data: HandlingApi.VerifyOrderBody) {
  return requestClient.post<HandlingApi.OrderDetail>(
    `/handling/orders/${id}/verify`,
    data,
  );
}

/** 作废（PENDING → CANCELLED 终态；cancelReason 必填，§6.2） */
export function cancelOrderApi(id: string, data: HandlingApi.CancelOrderBody) {
  return requestClient.post<HandlingApi.OrderDetail>(
    `/handling/orders/${id}/cancel`,
    data,
  );
}

/** KPI 前后对比预览（VERIFYING 阶段实时拉取，不落库；口径沿用 v1.x，§6.2） */
export function getOrderKpiComparisonApi(id: string) {
  return requestClient.post<HandlingApi.KpiComparison>(
    `/handling/orders/${id}/kpi-comparison`,
  );
}
