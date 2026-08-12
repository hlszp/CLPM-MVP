/**
 * 工作台 summary mock 数据（临时测试用 · 2026-08-13）
 *
 * 用法：在 workbench.vue 的 loadSummary 中取消注释 mock 注入行
 *       const USE_MOCK = true;  // 切换 mock 开关
 *
 * 场景切换：修改 MOCK_SCENE 常量
 */
import type { MonitorApi } from '#/api/monitor';

/** mock 场景选择器 */
export const MOCK_SCENE:
  | 'CLOSED_IMPROVED'
  | 'VERIFYING_IMPROVED'
  | 'CLOSED_DETERIORATED'
  | 'PENDING_NO_COMPARE'
  | 'IN_PROGRESS_OVERDUE' = 'VERIFYING_IMPROVED';

const NOW = '2026-08-13T10:30:00Z';
const HOUR_AGO = '2026-08-13T09:30:00Z';
const DAY_AGO = '2026-08-12T10:30:00Z';
const TWO_DAYS_AGO = '2026-08-11T10:30:00Z';

const SCENARIOS: Record<string, MonitorApi.WorkbenchSummary> = {
  // 场景1：已闭环 + 改善
  CLOSED_IMPROVED: {
    nextAction: {
      type: 'CONTINUE_MONITORING',
      label: '继续监控',
      reason: '回路已闭环验证，评分改善，进入常规监控',
      primaryButton: { label: '继续监控', enabled: true },
      secondaryButtons: [],
    },
    lifecycle: {
      currentStage: 'MONITOR',
      stages: [
        { stage: 'ASSESS', status: 'COMPLETED', resultAt: TWO_DAYS_AGO, reason: '评估完成' },
        { stage: 'DIAGNOSE', status: 'COMPLETED', resultAt: TWO_DAYS_AGO, reason: '诊断完成' },
        { stage: 'TUNE', status: 'COMPLETED', resultAt: DAY_AGO, reason: '整定完成' },
        { stage: 'VERIFY', status: 'COMPLETED', resultAt: HOUR_AGO, reason: '验证完成，改善' },
        { stage: 'MONITOR', status: 'RUNNING', reason: '常规监控中' },
      ],
    },
    trackerTimeline: {
      trackerId: 'mock-tracker-001',
      diagnosisLabel: 'OSCILLATION',
      actionStatus: 'CLOSED',
      severity: 'HIGH',
      triggerType: 'AUTO',
      assignee: '工程师张三',
      createdAt: TWO_DAYS_AGO,
      updatedAt: HOUR_AGO,
      implementedAt: DAY_AGO,
      plannedAt: DAY_AGO,
      closedAt: HOUR_AGO,
      effectVerified: true,
      effectVerifiedAt: HOUR_AGO,
      isOverdue: false,
      overdueHours: 0,
      effectCompare: {
        status: 'COMPLETED',
        conclusion: 'IMPROVED',
        conclusionLabel: '改善',
        implementedAt: DAY_AGO,
        verifiedAt: HOUR_AGO,
        scoreChange: {
          before: 62.5,
          after: 78.3,
          change: 15.8,
          improved: true,
        },
        coreKpiChanges: [
          { key: 'stability', label: '稳定性', before: 55, after: 82, change: 27, improved: true },
          { key: 'settling_time', label: '调节时间', before: 120, after: 45, change: -75, improved: true },
          { key: 'overshoot', label: '超调量', before: 28, after: 12, change: -16, improved: true },
        ],
        currentPid: { p: 0.45, i: 0.08, d: 0 },
        recommendedPid: { p: 0.6, i: 0.12, d: 0.02 },
      },
    },
    activeAttention: {
      items: [],
      total: 0,
    },
  } as unknown as MonitorApi.WorkbenchSummary,

  // 场景2：验证中 + 改善趋势
  VERIFYING_IMPROVED: {
    nextAction: {
      type: 'CREATE_TRACKER',
      label: '验证整定效果',
      reason: '整定参数已实施，需在 24h 验证窗内确认效果',
      primaryButton: { label: '查看验证进度', enabled: true },
      secondaryButtons: [{ label: '提前闭环', enabled: false, disabledReason: '验证窗未满 24h' }],
    },
    lifecycle: {
      currentStage: 'VERIFY',
      stages: [
        { stage: 'ASSESS', status: 'COMPLETED', resultAt: TWO_DAYS_AGO, reason: '评估完成' },
        { stage: 'DIAGNOSE', status: 'COMPLETED', resultAt: TWO_DAYS_AGO, reason: '诊断完成' },
        { stage: 'TUNE', status: 'COMPLETED', resultAt: DAY_AGO, reason: '整定完成' },
        { stage: 'VERIFY', status: 'RUNNING', resultAt: DAY_AGO, reason: '验证中（实施 23h）' },
        { stage: 'MONITOR', status: 'NOT_STARTED', reason: '待验证完成' },
      ],
    },
    trackerTimeline: {
      trackerId: 'mock-tracker-002',
      diagnosisLabel: 'OVERAGGRESSIVE',
      actionStatus: 'VERIFYING',
      severity: 'MEDIUM',
      triggerType: 'AUTO',
      assignee: '工程师李四',
      createdAt: TWO_DAYS_AGO,
      updatedAt: HOUR_AGO,
      implementedAt: DAY_AGO,
      plannedAt: NOW,
      effectVerified: false,
      isOverdue: false,
      overdueHours: 0,
      effectCompare: {
        status: 'COMPLETED',
        conclusion: 'IMPROVED',
        conclusionLabel: '改善',
        implementedAt: DAY_AGO,
        verifiedAt: null,
        scoreChange: {
          before: 58.2,
          after: 72.1,
          change: 13.9,
          improved: true,
        },
        coreKpiChanges: [
          { key: 'overshoot', label: '超调量', before: 35, after: 15, change: -20, improved: true },
          { key: 'settling_time', label: '调节时间', before: 180, after: 60, change: -120, improved: true },
        ],
        currentPid: { p: 0.8, i: 0.15, d: 0.05 },
        recommendedPid: { p: 0.5, i: 0.1, d: 0.02 },
      },
    },
    activeAttention: {
      items: [],
      total: 0,
    },
  } as unknown as MonitorApi.WorkbenchSummary,

  // 场景3：已闭环 + 恶化
  CLOSED_DETERIORATED: {
    nextAction: {
      type: 'CREATE_TRACKER',
      label: '重新整定',
      reason: '整定后评分恶化，需回退参数并重新整定',
      primaryButton: { label: '发起重新整定', enabled: true },
      secondaryButtons: [{ label: '回退参数', enabled: true }],
    },
    lifecycle: {
      currentStage: 'DIAGNOSE',
      stages: [
        { stage: 'ASSESS', status: 'COMPLETED', resultAt: TWO_DAYS_AGO, reason: '评估完成' },
        { stage: 'DIAGNOSE', status: 'READY', reason: '需重新诊断' },
        { stage: 'TUNE', status: 'OVERDUE', reason: '上次整定效果恶化' },
        { stage: 'VERIFY', status: 'COMPLETED', resultAt: HOUR_AGO, reason: '验证完成，恶化' },
        { stage: 'MONITOR', status: 'BLOCKED', reason: '待重新整定' },
      ],
    },
    trackerTimeline: {
      trackerId: 'mock-tracker-003',
      diagnosisLabel: 'OVERCONSERVATIVE',
      actionStatus: 'REOPENED',
      severity: 'HIGH',
      triggerType: 'AUTO',
      assignee: '工程师王五',
      createdAt: TWO_DAYS_AGO,
      updatedAt: HOUR_AGO,
      implementedAt: DAY_AGO,
      closedAt: HOUR_AGO,
      effectVerified: true,
      effectVerifiedAt: HOUR_AGO,
      isOverdue: false,
      overdueHours: 0,
      effectCompare: {
        status: 'COMPLETED',
        conclusion: 'DETERIORATED',
        conclusionLabel: '恶化',
        implementedAt: DAY_AGO,
        verifiedAt: HOUR_AGO,
        scoreChange: {
          before: 75.0,
          after: 61.3,
          change: -13.7,
          improved: false,
        },
        coreKpiChanges: [
          { key: 'stability', label: '稳定性', before: 80, after: 55, change: -25, improved: false },
          { key: 'settling_time', label: '调节时间', before: 45, after: 120, change: 75, improved: false },
        ],
        currentPid: { p: 0.5, i: 0.1, d: 0.02 },
        recommendedPid: { p: 0.3, i: 0.05, d: 0 },
      },
    },
    activeAttention: {
      items: [],
      total: 0,
    },
  } as unknown as MonitorApi.WorkbenchSummary,

  // 场景4：待处理 + 无对比数据
  PENDING_NO_COMPARE: {
    nextAction: {
      type: 'CREATE_TRACKER',
      label: '创建跟踪案例',
      reason: '检测到振荡标签，需创建跟踪案例进入闭环流程',
      primaryButton: { label: '创建案例', enabled: true },
      secondaryButtons: [],
    },
    lifecycle: {
      currentStage: 'DIAGNOSE',
      stages: [
        { stage: 'ASSESS', status: 'COMPLETED', resultAt: TWO_DAYS_AGO, reason: '评估完成' },
        { stage: 'DIAGNOSE', status: 'COMPLETED', resultAt: HOUR_AGO, reason: '诊断完成' },
        { stage: 'TUNE', status: 'NOT_STARTED', reason: '待整定' },
        { stage: 'VERIFY', status: 'NOT_STARTED', reason: '待整定后验证' },
        { stage: 'MONITOR', status: 'NOT_STARTED', reason: '待验证完成' },
      ],
    },
    trackerTimeline: {
      trackerId: 'mock-tracker-004',
      diagnosisLabel: 'OSCILLATION',
      actionStatus: 'PENDING',
      severity: 'MEDIUM',
      triggerType: 'AUTO',
      createdAt: HOUR_AGO,
      updatedAt: HOUR_AGO,
      isOverdue: false,
      overdueHours: 0,
      effectCompare: null,
    },
    activeAttention: {
      items: [],
      total: 0,
    },
  } as unknown as MonitorApi.WorkbenchSummary,

  // 场景5：处理中 + 超期
  IN_PROGRESS_OVERDUE: {
    nextAction: {
      type: 'CREATE_TRACKER',
      label: '推进整定',
      reason: '跟踪案例处理中超期 28h，需尽快推进整定',
      primaryButton: { label: '发起整定', enabled: true },
      secondaryButtons: [{ label: '转派', enabled: true }],
    },
    lifecycle: {
      currentStage: 'TUNE',
      stages: [
        { stage: 'ASSESS', status: 'COMPLETED', resultAt: TWO_DAYS_AGO, reason: '评估完成' },
        { stage: 'DIAGNOSE', status: 'COMPLETED', resultAt: TWO_DAYS_AGO, reason: '诊断完成' },
        { stage: 'TUNE', status: 'OVERDUE', reason: '整定超期 28h' },
        { stage: 'VERIFY', status: 'NOT_STARTED', reason: '待整定完成' },
        { stage: 'MONITOR', status: 'NOT_STARTED', reason: '待验证完成' },
      ],
    },
    trackerTimeline: {
      trackerId: 'mock-tracker-005',
      diagnosisLabel: 'VALVE_STICTION',
      actionStatus: 'IN_PROGRESS',
      severity: 'HIGH',
      triggerType: 'AUTO',
      assignee: '工程师赵六',
      createdAt: TWO_DAYS_AGO,
      updatedAt: HOUR_AGO,
      plannedAt: DAY_AGO,
      isOverdue: true,
      overdueHours: 28,
      effectCompare: null,
    },
    activeAttention: {
      items: [],
      total: 0,
    },
  } as unknown as MonitorApi.WorkbenchSummary,
};

export function getMockSummary(): MonitorApi.WorkbenchSummary {
  return (SCENARIOS[MOCK_SCENE] ?? SCENARIOS.VERIFYING_IMPROVED!) as unknown as MonitorApi.WorkbenchSummary;
}
