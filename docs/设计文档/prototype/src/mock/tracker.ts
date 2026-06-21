/**
 * Mock 数据：Action Tracker（DDS action_tracker）
 *
 * 状态流转（UI/UX §6.4.4 + §9.7）：
 * PENDING → IN_PROGRESS → RESOLVED（触发 A/B 对比）/ IGNORED
 */

import type { ActionTracker } from './types';

export const actionTrackers: ActionTracker[] = [
  {
    trackerId: 'AT001',
    loopId: 'L003',
    loopName: 'F-101 加热炉出口温度',
    nodeName: '反应系统',
    resultId: 'D-L003',
    label: '振荡',
    actionStatus: 'IN_PROGRESS',
    assignee: '张工（仪控）',
    comment: '已确认振荡现象，正在调整 PID 参数，计划降低 PID_P 至 0.8',
    baselineStart: null,
    baselineEnd: null,
    createdAt: '2026-06-20 14:00:00',
    updatedAt: '2026-06-21 09:00:00',
  },
  {
    trackerId: 'AT002',
    loopId: 'L004',
    loopName: 'C-101 塔顶压力',
    nodeName: '反应系统',
    resultId: 'D-L004',
    label: 'PV 质量异常',
    actionStatus: 'PENDING',
    assignee: '李工（仪控）',
    comment: '',
    baselineStart: null,
    baselineEnd: null,
    createdAt: '2026-06-21 09:30:00',
    updatedAt: '2026-06-21 09:30:00',
  },
  {
    trackerId: 'AT003',
    loopId: 'L009',
    loopName: 'R-201 反应器床层温度',
    nodeName: '反应系统',
    resultId: 'D-L009',
    label: '粘滞阀',
    actionStatus: 'PENDING',
    assignee: '王工（设备）',
    comment: '',
    baselineStart: null,
    baselineEnd: null,
    createdAt: '2026-06-21 09:30:00',
    updatedAt: '2026-06-21 09:30:00',
  },
  {
    trackerId: 'AT004',
    loopId: 'L014',
    loopName: 'C-202 回流量',
    nodeName: '分馏系统',
    resultId: 'D-L014',
    label: '参数过激',
    actionStatus: 'RESOLVED',
    assignee: '张工（仪控）',
    comment: '已将 PID_P 从 1.2 调整至 0.6，PID_I 从 30s 调整至 50s。调整后超调量从 18% 降至 8%，满足要求。',
    baselineStart: '2026-06-18 00:00:00',
    baselineEnd: '2026-06-19 00:00:00',
    createdAt: '2026-06-19 10:00:00',
    updatedAt: '2026-06-20 16:00:00',
  },
  {
    trackerId: 'AT005',
    loopId: 'L001',
    loopName: 'R-101 反应器入口温度',
    nodeName: '反应系统',
    resultId: 'D-L001',
    label: '振荡',
    actionStatus: 'IGNORED',
    assignee: '张工（仪控）',
    comment: '振荡幅值 ±0.8°C 在允许范围内，不影响生产，暂不处理。',
    baselineStart: null,
    baselineEnd: null,
    createdAt: '2026-06-15 10:00:00',
    updatedAt: '2026-06-15 14:00:00',
  },
];

/** 按 trackerId 查询 */
export function findTracker(trackerId: string): ActionTracker | undefined {
  return actionTrackers.find((t) => t.trackerId === trackerId);
}

/** 按 loopId 查询 */
export function findTrackerByLoop(loopId: string): ActionTracker | undefined {
  return actionTrackers.find((t) => t.loopId === loopId);
}

/** 按状态统计 */
export function getTrackerStats() {
  const total = actionTrackers.length;
  const pending = actionTrackers.filter((t) => t.actionStatus === 'PENDING').length;
  const inProgress = actionTrackers.filter((t) => t.actionStatus === 'IN_PROGRESS').length;
  const resolved = actionTrackers.filter((t) => t.actionStatus === 'RESOLVED').length;
  const ignored = actionTrackers.filter((t) => t.actionStatus === 'IGNORED').length;
  return { total, pending, inProgress, resolved, ignored };
}

/** 闭环时长统计（已解决 Tracker 从创建到解决的小时数） */
export function getClosureTimeStats(): Array<{ trackerId: string; loopName: string; hours: number }> {
  return actionTrackers
    .filter((t) => t.actionStatus === 'RESOLVED')
    .map((t) => {
      const created = new Date(t.createdAt).getTime();
      const updated = new Date(t.updatedAt).getTime();
      return {
        trackerId: t.trackerId,
        loopName: t.loopName,
        hours: Math.round((updated - created) / 3600000),
      };
    });
}
