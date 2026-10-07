/**
 * 处置 Tab SLA 警示色计算
 *
 * 决策 D：超期 due<now 红 / 临期 due<now+24h 橙 / 正常 due≥now+24h 绿 / 无排程 due==null 灰
 * due 代理 = OrderItem.plannedAt（排程时间）
 * 后端返回 naive UTC ISO（无 Z），前端补 Z 视为 UTC 再转本地比较
 */
import type { HandlingApi } from '#/api/handling';

export type SlaLevel = 'near' | 'none' | 'normal' | 'overdue';

/** SLA 等级 → 颜色（工业惯例 + 原型色阶） */
export const SLA_COLOR: Record<SlaLevel, string> = {
  overdue: '#FF4D4F',
  near: '#FA8C16',
  normal: '#52C41A',
  none: '#8C8C8C',
};

/** SLA 等级 → 中文标签 */
export const SLA_LABEL: Record<SlaLevel, string> = {
  overdue: '超期',
  near: '临期',
  normal: '正常',
  none: '无排程',
};

/** 临期阈值（小时）——决策 D 默认 24h */
const NEAR_HOURS = 24;
const HOUR_MS = 3600 * 1000;

/** naive UTC ISO（无 Z/时区）→ 补 Z 视为 UTC；已带时区直接解析 */
function toUtcMs(input: string): number {
  const s = input.trim();
  const iso = /([zZ]|[+-]\d{2}:?\d{2})$/.test(s) ? s : `${s}Z`;
  return new Date(iso).getTime();
}

/** 计算单条工单 SLA 等级（due = plannedAt） */
export function computeSla(
  plannedAt: HandlingApi.OrderItem['plannedAt'],
  now: number = Date.now(),
): SlaLevel {
  if (!plannedAt) return 'none';
  const due = toUtcMs(plannedAt);
  if (Number.isNaN(due)) return 'none';
  if (due < now) return 'overdue';
  if (due < now + NEAR_HOURS * HOUR_MS) return 'near';
  return 'normal';
}

/** due 倒计时文案：剩 Xh / 已超期 Xh / 无排程（≥24h 取整，<24h 保留 1 位小数） */
export function formatDueCountdown(
  plannedAt: HandlingApi.OrderItem['plannedAt'],
  now: number = Date.now(),
): string {
  if (!plannedAt) return '无排程';
  const due = toUtcMs(plannedAt);
  if (Number.isNaN(due)) return '无排程';
  const diff = due - now;
  const hrs = Math.abs(diff) / HOUR_MS;
  const txt = hrs >= 24 ? `${Math.floor(hrs)}h` : `${Math.round(hrs * 10) / 10}h`;
  return diff < 0 ? `已超期 ${txt}` : `剩 ${txt}`;
}

/** 批量 SLA 分布（HandlingSlaSummary 环形图 + 断言带超期/临期计数） */
export function computeSlaBreakdown(tasks: HandlingApi.OrderItem[]): {
  near: number;
  none: number;
  normal: number;
  overdue: number;
} {
  const r = { near: 0, none: 0, normal: 0, overdue: 0 };
  for (const t of tasks) {
    r[computeSla(t.plannedAt)] += 1;
  }
  return r;
}
