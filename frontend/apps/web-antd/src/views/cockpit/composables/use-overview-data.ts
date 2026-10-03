/**
 * 驾驶舱 overview 数据共享单例（2026-10-03 性能优化）
 *
 * KPI 指标带与闭环漏斗两个区块消费同一 /cockpit/overview 接口——
 * 原各自独立拉取导致同一数据每次刷新打两份请求。本单例按 window
 * 维度做 2s 合并窗口：并发调用共享同一 in-flight Promise（一次网络
 * 请求）；5min 定时/手动刷新时首组件拉新、次组件毫秒级跟进命中缓存。
 */
import type { CockpitApi } from '#/api/cockpit';

import { getCockpitOverviewApi } from '#/api/cockpit';

const MERGE_WINDOW_MS = 2000;

const cache = new Map<
  string,
  { at: number; promise: Promise<CockpitApi.OverviewResult | null> }
>();

export function loadOverviewData(
  window: CockpitApi.TimeWindow,
): Promise<CockpitApi.OverviewResult | null> {
  const hit = cache.get(window);
  if (hit && Date.now() - hit.at < MERGE_WINDOW_MS) {
    return hit.promise;
  }
  const promise = getCockpitOverviewApi(window).catch(() => null);
  cache.set(window, { at: Date.now(), promise });
  return promise;
}
