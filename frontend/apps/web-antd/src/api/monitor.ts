/**
 * 监控模块 API——关注队列与工作台摘要（整改方案 §8）
 *
 * 关注队列统一聚合 ALERT/DEGRADATION/DATA_QUALITY/TRACKER/VERIFICATION 五类来源。
 * 动作由服务端按角色生成，前端不自行推断权限。
 */
import { requestClient } from '#/api/request';

export namespace MonitorApi {
  /** 关注来源 */
  export type AttentionSource =
    | 'ALERT'
    | 'DATA_QUALITY'
    | 'DEGRADATION'
    | 'TRACKER'
    | 'VERIFICATION';

  /** 优先级 */
  export type AttentionPriority = 'HIGH' | 'LOW' | 'MEDIUM' | 'URGENT';

  /** 统一状态 */
  export type AttentionStatus =
    | 'ACKNOWLEDGED'
    | 'IN_PROGRESS'
    | 'OPEN'
    | 'SUPPRESSED'
    | 'VERIFYING';

  /** 可信度等级 */
  export type ConfidenceLevel = 'A' | 'B' | 'C' | 'D' | 'E';

  /** 动作类型 */
  export type AttentionActionType =
    | 'ACKNOWLEDGE'
    | 'BACK_TO_OVERVIEW'
    | 'CREATE_TRACKER'
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
    trackerId?: string;
    taskId?: string;
    primaryAction: AttentionAction;
    actions: AttentionAction[];
  }

  /** 聚合统计 */
  export interface AttentionAggregates {
    byPriority: Record<string, number>;
    bySource: Record<string, number>;
    byStatus: Record<string, number>;
  }

  /** 关注队列列表响应 */
  export interface AttentionListData {
    items: AttentionItem[];
    total: number;
    page: number;
    pageSize: number;
    aggregates: AttentionAggregates;
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
