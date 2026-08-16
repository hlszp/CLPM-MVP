/**
 * operational-context.fixtures.ts
 * Phase 1 共享载体：测试/Storybook/开发用 mock 数据工厂
 */
import type { MonitorApi } from '#/api/monitor';
import type { UrlContext } from './types/operational-context';

export function createDefaultUrlContext(overrides: Partial<UrlContext> = {}): UrlContext {
  return {
    loopId: 'LP-001',
    from: null,
    section: null,
    anchor: null,
    eventId: null,
    trackerId: null,
    taskId: null,
    timeWindow: '24h',
    plantNodeId: null,
    ...overrides,
  };
}

export function createMockWorkbenchSummary(
  overrides: Partial<MonitorApi.WorkbenchSummary> = {},
): MonitorApi.WorkbenchSummary {
  const base: MonitorApi.WorkbenchSummary = {
    loopId: 'LP-001',
    tagName: 'FIC101',
    description: '精馏塔塔顶流量控制回路',
    unitName: '乙烯装置',
    loopType: 'FLOW',
    controlType: 'PID',
    loopStatus: 'READY',
    isActive: true,
    importanceLevel: 8,
    runtime: {
      pv: 125.6,
      sp: 125.0,
      op: 45.2,
      mode: 1,
      modeLabel: 'Auto',
      pvQuality: 'GOOD',
      pvUnit: 'm³/h',
      pvRange: { max: 200, min: 0 },
      opRange: { max: 100, min: 0 },
      readAt: '2026-08-14T10:30:00+08:00',
      controlMode: 'Auto',
    },
    dataFreshness: {
      status: 'FRESH',
      thresholdSeconds: 60,
      reason: null,
    },
    dataHealth: {
      validRate: 0.982,
      confidenceLevel: 'A',
      pvCompleteness: 0.991,
      overallCompleteness: 0.975,
      integrityStatus: 'OK',
    },
    scoreTrend: {
      score: 72.5,
      scoreDelta: -3.2,
      dayTrend: 'WORSENED',
      resultAt: '2026-08-14T10:00:00+08:00',
      confidenceLevel: 'B',
      status: 'SUCCESS',
    },
    activeAttention: {
      total: 2,
      highestPriority: 'HIGH',
      items: [],
    },
    assessment: {
      score: 72.5,
      confidenceLevel: 'B',
      status: 'SUCCESS',
      resultAt: '2026-08-14T10:00:00+08:00',
      timeWindow: '最近24小时',
      summary: '控制性能一般，近期出现小幅振荡',
    },
    diagnosis: {
      diagLabel: 'STICTION',
      confidence: 78,
      status: 'SUCCESS',
      resultAt: '2026-08-14T09:45:00+08:00',
      taskId: 'diag-task-001',
      labels: ['STICTION', 'OSCILLATION'],
      summary: '检测到阀门迟滞，可能导致控制回路振荡',
    },
    tuning: null,
    trackerTimeline: null,
    lifecycle: {
      stages: [
        { stage: 'ASSESS', status: 'COMPLETED', resultAt: '2026-08-14T10:00:00+08:00', reason: '' },
        { stage: 'MONITOR', status: 'READY', resultAt: null, reason: '' },
      ],
      currentStage: 'MONITOR',
    },
    nextAction: {
      actionType: 'RUN_ASSESSMENT',
      label: '运行评估',
      reason: '评分下降，建议重新评估',
      enabled: true,
      disabledReason: null,
      target: {
        route: '/monitor/loop-workbench',
        query: { loopId: 'LP-001', section: 'assessment' },
      },
    },
    partial: false,
    unavailableSections: [],
  };
  return { ...base, ...overrides };
}

/** 加载中状态 */
export function createLoadingContext() {
  return { loading: true, error: null, summary: null, stateFace: 'loading' as const };
}

/** 错误状态 */
export function createErrorContext(message = '网络请求失败') {
  return {
    loading: false,
    error: new Error(message),
    summary: null,
    stateFace: 'error' as const,
  };
}

/** 空状态（无 loopId） */
export function createEmptyContext() {
  return { loading: false, error: null, summary: null, stateFace: 'empty' as const };
}

/** partial 部分失败状态 */
export function createPartialContext(failedSections: string[] = ['tuning']) {
  return {
    loading: false,
    error: null,
    summary: createMockWorkbenchSummary({
      partial: true,
      unavailableSections: failedSections,
      tuning: null,
    }),
    stateFace: 'partial' as const,
  };
}

/** stale 数据陈旧状态 */
export function createStaleContext() {
  return {
    loading: false,
    error: null,
    summary: createMockWorkbenchSummary({
      dataFreshness: { status: 'DELAYED', thresholdSeconds: 60, reason: '已停滞 320 秒' },
    }),
    stateFace: 'stale' as const,
  };
}
