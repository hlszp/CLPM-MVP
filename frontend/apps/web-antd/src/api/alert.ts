/**
 * CLPM 智能预警规则引擎 API（对齐 PRD v6.2 §4.4.6 + IDS v2.7）
 *
 * 覆盖：规则 CRUD / 订阅 CRUD / 事件查询与处置 / 手动抑制 / 审计日志 / 全局开关 / 徽章。
 * - API 前缀：/api/v1/alert/
 */
import { requestClient } from '#/api/request';

export namespace AlertApi {
  /** 规则类型 */
  export type RuleType = 'COMPOSITE' | 'CONFIDENCE' | 'DRIFT' | 'THRESHOLD';

  /** 订阅范围类型 */
  export type ScopeType = 'ALL' | 'CONTROL_TYPE' | 'LOOP' | 'PLANT';

  /** 严重度 */
  export type Severity = 'CRITICAL' | 'ERROR' | 'INFO' | 'WARN';

  /** 事件状态 */
  export type EventStatus =
    | 'ACTIVE'
    | 'ACKNOWLEDGED'
    | 'ARCHIVED'
    | 'RESOLVED'
    | 'SUPPRESSED';

  /** 审计操作类型 */
  export type AuditOperationType =
    | 'CREATE'
    | 'DELETE'
    | 'DISABLE'
    | 'ENABLE'
    | 'UPDATE';

  /** 规则定义 */
  export interface RuleItem {
    ruleId: string;
    ruleCode: string;
    ruleName: string;
    ruleType: RuleType;
    dsl: Record<string, any>;
    description?: string;
    priority: number;
    isEnabled: boolean;
    version: number;
    createdBy: string;
    createdAt?: string;
    updatedBy?: string;
    updatedAt?: string;
  }

  /** 规则列表响应 */
  export interface RuleListResult {
    total: number;
    items: RuleItem[];
  }

  /** 创建规则参数 */
  export interface RuleCreateParams {
    ruleCode: string;
    ruleName: string;
    ruleType: RuleType;
    dsl: Record<string, any>;
    description?: string;
    priority?: number;
    isEnabled?: boolean;
  }

  /** 更新规则参数 */
  export interface RuleUpdateParams {
    ruleName?: string;
    dsl?: Record<string, any>;
    description?: string;
    priority?: number;
    isEnabled?: boolean;
  }

  /** 订阅关系 */
  export interface SubscriptionItem {
    subscriptionId: string;
    ruleId: string;
    loopId: string;
    scopeType: ScopeType;
    scopeValue?: string;
    isActive: boolean;
    createdBy: string;
    createdAt?: string;
  }

  /** 创建订阅参数 */
  export interface SubscriptionCreateParams {
    loopId: string;
    scopeType: ScopeType;
    scopeValue?: string;
  }

  /** 预警事件 */
  export interface EventItem {
    eventId: string;
    ruleId?: string;
    ruleCode: string;
    ruleVersion: number;
    loopId: string;
    severity: Severity;
    status: EventStatus;
    triggerConditionSnapshot: Record<string, any>;
    dataWindow?: Record<string, any>;
    triggeredValue?: number;
    confidenceLevel?: string;
    ruleDslSnapshot: Record<string, any>;
    trackerId?: string;
    isFalsePositive?: boolean;
    triggerCount: number;
    triggeredAt: string;
    acknowledgedBy?: string;
    acknowledgedAt?: string;
    resolvedBy?: string;
    resolvedAt?: string;
    resolutionNote?: string;
    loopName?: string;
  }

  /** 事件列表响应 */
  export interface EventListResult {
    total: number;
    items: EventItem[];
  }

  /** 事件列表查询参数 */
  export interface EventListParams {
    loopId?: string;
    ruleId?: string;
    severity?: Severity;
    status?: EventStatus;
    startTime?: string;
    endTime?: string;
    limit?: number;
    offset?: number;
  }

  /** 手动抑制记录 */
  export interface SuppressionItem {
    suppressionId: string;
    ruleId?: string;
    loopId?: string;
    reason: string;
    suppressedBy: string;
    startAt: string;
    endAt: string;
    isActive: boolean;
    createdAt?: string;
  }

  /** 创建抑制参数 */
  export interface SuppressionCreateParams {
    ruleId?: string;
    loopId?: string;
    reason: string;
    durationMinutes: number;
  }

  /** 审计日志 */
  export interface AuditLogItem {
    logId: string;
    ruleId?: string;
    ruleCode: string;
    operationType: AuditOperationType;
    beforeValue?: string;
    afterValue?: string;
    operator: string;
    operatedAt: string;
  }

  /** 全局开关 */
  export interface GlobalSwitch {
    enabled: boolean;
  }

  /** 徽章计数 */
  export interface BadgeCount {
    count: number;
  }
}

const BASE = '/alert';

// ---------------------------------------------------------------------------
// 规则 CRUD
// ---------------------------------------------------------------------------

export function getAlertRulesApi(params?: {
  ruleType?: AlertApi.RuleType;
  isEnabled?: boolean;
  limit?: number;
  offset?: number;
}) {
  return requestClient.get<AlertApi.RuleListResult>(`${BASE}/rules`, {
    params,
  });
}

export function createAlertRuleApi(data: AlertApi.RuleCreateParams) {
  return requestClient.post<AlertApi.RuleItem>(`${BASE}/rules`, data);
}

export function getAlertRuleApi(ruleId: string) {
  return requestClient.get<AlertApi.RuleItem>(`${BASE}/rules/${ruleId}`);
}

export function updateAlertRuleApi(
  ruleId: string,
  data: AlertApi.RuleUpdateParams,
) {
  return requestClient.put<AlertApi.RuleItem>(`${BASE}/rules/${ruleId}`, data);
}

export function deleteAlertRuleApi(ruleId: string) {
  return requestClient.delete(`${BASE}/rules/${ruleId}`);
}

export function toggleAlertRuleApi(ruleId: string, enabled: boolean) {
  return requestClient.put<AlertApi.RuleItem>(
    `${BASE}/rules/${ruleId}/toggle`,
    undefined,
    { params: { enabled } },
  );
}

// ---------------------------------------------------------------------------
// 订阅 CRUD
// ---------------------------------------------------------------------------

export function getRuleSubscriptionsApi(ruleId: string) {
  return requestClient.get<AlertApi.SubscriptionItem[]>(
    `${BASE}/rules/${ruleId}/subscriptions`,
  );
}

export function createSubscriptionApi(
  ruleId: string,
  data: AlertApi.SubscriptionCreateParams,
) {
  return requestClient.post<AlertApi.SubscriptionItem>(
    `${BASE}/rules/${ruleId}/subscriptions`,
    data,
  );
}

export function getSubscriptionsApi(loopId?: string) {
  return requestClient.get<AlertApi.SubscriptionItem[]>(
    `${BASE}/subscriptions`,
    { params: { loopId } },
  );
}

export function deleteSubscriptionApi(subscriptionId: string) {
  return requestClient.delete(`${BASE}/subscriptions/${subscriptionId}`);
}

// ---------------------------------------------------------------------------
// 事件查询与处置
// ---------------------------------------------------------------------------

export function getAlertEventsApi(params: AlertApi.EventListParams) {
  return requestClient.get<AlertApi.EventListResult>(`${BASE}/events`, {
    params,
  });
}

export function getAlertEventApi(eventId: string) {
  return requestClient.get<AlertApi.EventItem>(`${BASE}/events/${eventId}`);
}

export function acknowledgeEventApi(eventId: string, note?: string) {
  return requestClient.post<AlertApi.EventItem>(
    `${BASE}/events/${eventId}/acknowledge`,
    { note },
  );
}

export function resolveEventApi(eventId: string, resolutionNote: string) {
  return requestClient.post<AlertApi.EventItem>(
    `${BASE}/events/${eventId}/resolve`,
    { resolutionNote },
  );
}

export function markFalsePositiveApi(
  eventId: string,
  isFalsePositive: boolean,
) {
  return requestClient.post<AlertApi.EventItem>(
    `${BASE}/events/${eventId}/false-positive`,
    { isFalsePositive },
  );
}

export function archiveEventApi(eventId: string) {
  return requestClient.post<AlertApi.EventItem>(
    `${BASE}/events/${eventId}/archive`,
  );
}

// ---------------------------------------------------------------------------
// 手动抑制
// ---------------------------------------------------------------------------

export function getSuppressionsApi(params?: {
  ruleId?: string;
  loopId?: string;
  isActive?: boolean;
  limit?: number;
  offset?: number;
}) {
  return requestClient.get<{
    total: number;
    items: AlertApi.SuppressionItem[];
  }>(`${BASE}/suppressions`, { params });
}

export function createSuppressionApi(data: AlertApi.SuppressionCreateParams) {
  return requestClient.post<AlertApi.SuppressionItem>(
    `${BASE}/suppressions`,
    data,
  );
}

export function deleteSuppressionApi(suppressionId: string) {
  return requestClient.delete(`${BASE}/suppressions/${suppressionId}`);
}

// ---------------------------------------------------------------------------
// 审计日志
// ---------------------------------------------------------------------------

export function getAlertAuditLogsApi(params?: {
  ruleId?: string;
  operator?: string;
  operationType?: AlertApi.AuditOperationType;
  limit?: number;
  offset?: number;
}) {
  return requestClient.get<{ total: number; items: AlertApi.AuditLogItem[] }>(
    `${BASE}/audit-logs`,
    { params },
  );
}

// ---------------------------------------------------------------------------
// 全局开关
// ---------------------------------------------------------------------------

export function getGlobalSwitchApi() {
  return requestClient.get<AlertApi.GlobalSwitch>(`${BASE}/global-switch`);
}

export function setGlobalSwitchApi(enabled: boolean) {
  return requestClient.put<AlertApi.GlobalSwitch>(`${BASE}/global-switch`, {
    enabled,
  });
}

// ---------------------------------------------------------------------------
// 徽章
// ---------------------------------------------------------------------------

export function getAlertBadgeApi() {
  return requestClient.get<AlertApi.BadgeCount>(`${BASE}/badge`);
}

export function resetAlertBadgeApi() {
  return requestClient.post<AlertApi.BadgeCount>(`${BASE}/badge/reset`);
}

// ---------------------------------------------------------------------------
// Dry-Run 试运行
// ---------------------------------------------------------------------------

export namespace AlertApi {
  /** dry-run 请求参数 */
  export interface DryRunParams {
    loopId: string;
    ruleId?: string;
    dsl?: Record<string, any>;
    confidenceLevel?: 'A' | 'B' | 'C' | 'D' | 'E';
  }

  /** dry-run 结果 */
  export interface DryRunResult {
    triggered: boolean;
    triggeredValue: number | null;
    conditionSnapshot: Record<string, any>;
    severity: string | null;
    confidenceLevel: string | null;
    dedupKey: string | null;
    currentValues: Record<string, any>;
  }
}

export function dryRunAlertRuleApi(data: AlertApi.DryRunParams) {
  return requestClient.post<AlertApi.DryRunResult>(
    `${BASE}/rules/dry-run`,
    data,
  );
}
