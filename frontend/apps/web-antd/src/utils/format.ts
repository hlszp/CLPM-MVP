/**
 * CLPM 通用格式化工具函数
 *
 * 对齐 IDS v3.2 统一响应规范与 UI/UX v4.1 视觉规范。
 * 提取自各视图组件中重复使用的工具函数，便于单元测试与复用。
 *
 * 注意：诊断标签映射已迁移至 `#/constants/diagnosis`，
 * 树形结构扁平化工具已迁移至 `#/utils/plant-node`。
 * 此处通过 re-export 保持向后兼容。
 */

import dayjs from 'dayjs';

export type { DiagnosisLabel } from '#/api/diagnosis';
export {
  DIAGNOSIS_LABEL_NAME_MAP,
  getDiagnosisLabelName as labelName,
} from '#/constants/diagnosis';

export { flattenNodes } from '#/utils/plant-node';
export type { TreeNode } from '#/utils/plant-node';

/**
 * 规范化 UTC 时间戳（"补 Z 转本地"约定）
 *
 * 后端部分接口（dashboard trend、KPI 快照等）返回无时区后缀的 ISO8601 时间戳
 * （如 "2026-07-22T10:00:00"），浏览器 `new Date()` / `dayjs()` 会将其解释为
 * 本地时间，导致与后端 UTC 语义产生偏移。
 *
 * 本函数统一检测时间戳是否已含时区后缀（Z / +HH:MM / +HHMM），
 * 若无则补 "Z" 标记为 UTC，再交由调用方用 `dayjs()` 按本地时区渲染。
 *
 * 与历史 `+8h` hack 的区别：hack 假设浏览器在 UTC+8 且叠加了本地时区偏移，
 * 在非 UTC+8 环境下会双重偏移；"补 Z" 方案让 dayjs 正确解析为 UTC 后由本地
 * 时区负责渲染偏移，跨时区正确。
 *
 * @param ts 时间戳字符串（ISO8601，可能含/不含时区后缀）
 * @returns 规范化后的时间戳字符串（必定含时区信息）
 */
export function normalizeUtcTimestamp(ts: string): string {
  const hasTimezone = /[zZ]$|[+-]\d{2}:?\d{2}$/.test(ts);
  return hasTimezone ? ts : `${ts}Z`;
}

/**
 * 将后端时间戳规范化并格式化为本地时间字符串
 *
 * 封装"补 Z 转本地"约定（`normalizeUtcTimestamp` + `dayjs.format`），
 * 消除各视图重复的 `hasTimezone` 检测逻辑与 `+8h` hack。
 *
 * @param ts 时间戳字符串（空值返回 fallback）
 * @param fmt dayjs 格式串，默认 'YYYY-MM-DD HH:mm'
 * @param fallback 空值占位，默认 '—'
 * @returns 格式化后的本地时间字符串
 */
export function formatLocalTime(
  ts: null | string | undefined,
  fmt = 'YYYY-MM-DD HH:mm',
  fallback = '—',
): string {
  if (!ts) return fallback;
  return dayjs(normalizeUtcTimestamp(ts)).format(fmt);
}

/**
 * 格式化时间字符串为本地化展示
 *
 * 强制使用北京时区（Asia/Shanghai, UTC+8）展示，与后端 Celery Beat 时区配置一致，
 * 避免依赖浏览器本地时区导致跨地区显示不一致。
 *
 * 无效日期（NaN）统一返回 "—"，避免渲染出 "Invalid Date" 字样。
 *
 * @param t ISO8601 时间字符串或空值
 * @returns 格式化后的时间字符串，空值/无效值返回 "—"
 */
export function formatTime(t: null | string | undefined): string {
  if (!t) return '—';
  try {
    const d = new Date(t);
    if (Number.isNaN(d.getTime())) return '—';
    return d.toLocaleString('zh-CN', {
      timeZone: 'Asia/Shanghai',
    });
  } catch {
    return '—';
  }
}

/**
 * 根据置信度返回对应颜色（对齐 UI/UX v4.1 §3 配色规范）
 * - >= 0.8：绿色 #52c41a
 * - >= 0.5：橙色 #faad14
 * - < 0.5：红色 #ff4d4f
 * @param val 置信度数值 [0, 1]
 */
export function confidenceColor(val: number): string {
  if (val >= 0.8) return '#52c41a';
  if (val >= 0.5) return '#faad14';
  return '#ff4d4f';
}
