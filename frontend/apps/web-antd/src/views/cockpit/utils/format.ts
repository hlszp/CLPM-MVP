/**
 * 驾驶舱通用工具（方案 11 §5.3 时间窗口径 / 环比 / 停留时长格式化）
 *
 * 时间窗总开关（store 的 '24h'|'7d'|'30d'）与各后端接口窗口枚举的映射
 * 集中在此，避免各区块重复实现。
 */
import type { CockpitApi } from '#/api/cockpit';
import type { TimeWindow } from '#/api/metric';

import { normalizeUtcTimestamp } from '#/utils/format';

/** 驾驶舱时间窗 → 性能/看板类接口时间窗枚举（metric.ts TimeWindow / board/trend timeWindow） */
export const WINDOW_MAP: Record<CockpitApi.TimeWindow, TimeWindow> = {
  '24h': 'last_24_hours',
  '30d': 'last_30_days',
  '7d': 'last_7_days',
};

/** 驾驶舱时间窗 → 小时数（环比/窗口起止推算用） */
export const WINDOW_HOURS: Record<CockpitApi.TimeWindow, number> = {
  '24h': 24,
  '30d': 720,
  '7d': 168,
};

/** 驾驶舱时间窗 → 中文标签 */
export const WINDOW_LABELS: Record<CockpitApi.TimeWindow, string> = {
  '24h': '近 24h',
  '30d': '近 30 天',
  '7d': '近 7 天',
};

/** 窗口起始时间（UTC ISO，供 custom range / 客户端时间窗过滤） */
export function windowStartDate(win: CockpitApi.TimeWindow): Date {
  return new Date(Date.now() - WINDOW_HOURS[win] * 3600 * 1000);
}

/** 环比箭头视图模型（delta 为与上一等长时间窗的差值） */
export function deltaView(delta: null | number | undefined): {
  arrow: string;
  text: string;
  trend: 'down' | 'flat' | 'up';
} {
  if (delta === null || delta === undefined || Number.isNaN(delta)) {
    return { arrow: '', text: '—', trend: 'flat' };
  }
  if (Math.abs(delta) < 0.05) {
    return { arrow: '→', text: '0.0', trend: 'flat' };
  }
  const up = delta > 0;
  return {
    arrow: up ? '▲' : '▼',
    text: `${up ? '+' : ''}${delta.toFixed(1)}`,
    trend: up ? 'up' : 'down',
  };
}

/**
 * 停留/持续时长格式化（naive 时间戳按 UTC 约定解析）
 * <1h → "N 分钟"；<24h → "N 小时"；≥24h → "N 天"
 */
export function formatDuration(
  fromIso: null | string | undefined,
  toIso?: null | string,
): string {
  if (!fromIso) return '—';
  const from = new Date(normalizeUtcTimestamp(fromIso)).getTime();
  if (Number.isNaN(from)) return '—';
  const to = toIso
    ? new Date(normalizeUtcTimestamp(toIso)).getTime()
    : Date.now();
  const mins = Math.max(0, Math.round((to - from) / 60_000));
  if (mins < 60) return `${mins} 分钟`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours} 小时`;
  return `${Math.floor(hours / 24)} 天 ${hours % 24} 小时`;
}

/** 判断时间戳是否处于驾驶舱时间窗内（naive 视为 UTC） */
export function isWithinWindow(
  ts: null | string | undefined,
  win: CockpitApi.TimeWindow,
): boolean {
  if (!ts) return false;
  const t = new Date(normalizeUtcTimestamp(ts)).getTime();
  if (Number.isNaN(t)) return false;
  return t >= windowStartDate(win).getTime();
}
