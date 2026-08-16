/**
 * OperationalContext 类型定义（P1-A）
 *
 * 基于 MonitorApi.WorkbenchSummary 的轻量包装，
 * 额外封装 URL 导航上下文、六态计算和便捷访问器。
 *
 * 设计约束：
 * - URL 是导航上下文真相源（loopId/taskId/eventId/trackerId/section/from/plantNodeId）
 * - 业务展示字段从 MonitorApi.WorkbenchSummary 直接获取，单一事实源
 * - partial=true 时 unavailableSections 列出失败来源，不让整页崩溃
 */

import type { MonitorApi } from '#/api/monitor';

// ---------------------------------------------------------------------------
// 直接复用 MonitorApi 类型，不再重复定义
// ---------------------------------------------------------------------------

export type { MonitorApi };

// 六态
export type StateFace = 'loading' | 'empty' | 'partial' | 'stale' | 'error' | 'ready';

// Evidence 可用状态
export type EvidenceState = StateFace;

// ---------------------------------------------------------------------------
// URL 导航上下文（仅导航字段，不含业务展示数据）
// ---------------------------------------------------------------------------

export interface UrlContext {
  loopId: string | null;
  from: 'overview' | 'list' | 'attention' | null;
  section: string | null;
  anchor: string | null;
  eventId: string | null;
  trackerId: string | null;
  taskId: string | null;
  timeWindow: '24h' | '7d' | '30d';
  plantNodeId: string | null;
}

/** URL query 键 */
export const URL_CONTEXT_KEYS = {
  loopId: 'loopId',
  from: 'from',
  section: 'section',
  anchor: 'anchor',
  eventId: 'eventId',
  trackerId: 'trackerId',
  taskId: 'taskId',
  timeWindow: 'timeWindow',
  plantNodeId: 'plantNodeId',
} as const;

// ---------------------------------------------------------------------------
// 导航目标
// ---------------------------------------------------------------------------

export interface DeepLink {
  path: string;
  query?: Record<string, string>;
}

// ---------------------------------------------------------------------------
// OperationalContext 顶层信封
// ---------------------------------------------------------------------------

/**
 * 统一操作上下文（Phase 1 共享载体 P1-A 核心类型）
 *
 * - summary: 后端原始 WorkbenchSummary（单一事实源）
 * - urlContext: URL 导航上下文
 * - navigation: 派生导航信息（backTo 等）
 * - stateFace: 六态
 */
export interface OperationalContext {
  summary: MonitorApi.WorkbenchSummary;
  urlContext: UrlContext;
  navigation: {
    from: UrlContext['from'];
    backTo: DeepLink | null;
  };
  stateFace: StateFace;
}

// ---------------------------------------------------------------------------
// Decision Dock 类型（P1-B）—— 从 nextAction 派生
// ---------------------------------------------------------------------------

export type Decision = MonitorApi.NextAction;

// ---------------------------------------------------------------------------
// State Face 配置（P1-D）
// ---------------------------------------------------------------------------

export interface StateFaceConfig {
  status: StateFace;
  title?: string;
  description?: string;
  retryable?: boolean;
  retryText?: string;
  actions?: Array<{
    label: string;
    onClick: () => void;
    type?: 'primary' | 'default' | 'link';
  }>;
  unavailableSections?: string[];
  staleReason?: string;
}
