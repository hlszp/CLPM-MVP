/**
 * CLPM 治理聚合 API（装置总览管理者版）
 *
 * GET /dashboard/governance-summary：处置闭环计数 + 治理漏斗 + 问题回路分布。
 * 时间窗参数与 /dashboard/board/aggregate 同口径（timeWindow + startTime/endTime，custom）。
 * 响应格式 `{code, message, data}`，此处类型对应 data 块（camelCase）。
 */
import { requestClient } from '#/api/request';

export namespace GovernanceApi {
  /** 处置闭环计数（双实体：loop_action_item 建议 + handling_order 工单） */
  export interface GovernanceHandlingSummary {
    /** 未闭环处置建议数（status ∈ PENDING/ACCEPTED） */
    openItems: number;
    /** 未闭环处置工单数（status ∈ PENDING/EXECUTING/VERIFYING/REOPENED） */
    openOrders: number;
    /** 超期未闭环工单数（口径同关注队列 HANDLING 来源） */
    overdueOrders: number;
    /** 时间窗内闭环的工单数（status=CLOSED 且 verified_at ∈ 窗口） */
    closedInWindow: number;
  }

  /** 治理漏斗：发现 → 诊断 → 方案 → 闭环 */
  export interface GovernanceFunnel {
    discovered: number;
    diagnosed: number;
    planned: number;
    closed: number;
  }

  /** 最新等级分布中的问题回路计数（WARNING/POOR 档） */
  export interface GovernanceBadLoops {
    warning: number;
    poor: number;
  }

  /** GET /dashboard/governance-summary 响应 data 块 */
  export interface GovernanceSummary {
    timeWindow: string;
    handling: GovernanceHandlingSummary;
    funnel: GovernanceFunnel;
    badLoops: GovernanceBadLoops;
  }
}

/**
 * 治理聚合（装置总览管理者版）
 * GET /dashboard/governance-summary
 */
export function getGovernanceSummaryApi(params?: {
  /** 自定义窗口结束（ISO 8601 UTC，timeWindow=custom 时必填） */
  endTime?: string;
  /** 自定义窗口起始（ISO 8601 UTC，timeWindow=custom 时必填） */
  startTime?: string;
  timeWindow?: string;
}) {
  return requestClient.get<GovernanceApi.GovernanceSummary>(
    '/dashboard/governance-summary',
    {
      params,
    },
  );
}
