/**
 * 趋势数据采样工具 — 与后端 trend_service.compute_sample_interval 对齐。
 *
 * 核心逻辑：根据时间范围动态计算采样间隔，固定目标点数 ~3600。
 * 后端使用此间隔向远程 API 请求降采样数据，避免传输全量数据。
 */

/** 默认目标点数（与后端 DEFAULT_TARGET_POINTS 一致） */
export const DEFAULT_TARGET_POINTS = 3600;

/**
 * 根据时间范围动态计算采样间隔（秒）。
 *
 * 确保返回的数据点数不超过 targetPoints。
 *
 * Examples:
 *   1h → 1s   (3600s / 3600 = 1)
 *   2h → 2s   (7200s / 3600 = 2)
 *   4h → 4s   (14400s / 3600 = 4)
 *   24h → 24s (86400s / 3600 = 24)
 *   72h → 72s (259200s / 3600 = 72)
 *
 * @param startTime ISO 8601 开始时间
 * @param endTime ISO 8601 结束时间
 * @param targetPoints 目标点数上限（默认 3600）
 * @returns 采样间隔（秒），最小为 1
 */
export function computeSampleInterval(
  startTime: string | Date,
  endTime: string | Date,
  targetPoints: number = DEFAULT_TARGET_POINTS,
): number {
  const start = startTime instanceof Date ? startTime : new Date(startTime);
  const end = endTime instanceof Date ? endTime : new Date(endTime);
  const deltaSeconds = Math.floor((end.getTime() - start.getTime()) / 1000);
  if (deltaSeconds <= 0) return 1;
  return Math.max(1, Math.floor(deltaSeconds / targetPoints));
}

/**
 * 格式化采样间隔为人类可读文本。
 *
 * @param seconds 采样间隔（秒）
 * @returns 如 "1s"、"72s"、"2min"、"1h"
 */
export function formatSampleInterval(seconds: number): string {
  if (seconds < 120) return `${seconds}s`;
  if (seconds < 3600) {
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return s > 0 ? `${m}min${s}s` : `${m}min`;
  }
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return m > 0 ? `${h}h${m}min` : `${h}h`;
}
