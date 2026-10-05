/**
 * 诊断概览（每回路最新一条）模块级共享缓存（1005 性能优化）
 *
 * 生产 1209 回路下，诊断概览与整定总览各自拉一次 /diagnosis/runs/latest
 * （重复大 JOIN）；且各自的 fitness 全量分页拉取把后端并发占满，连累主表
 * 请求排队超时（0930 同款模式）。本缓存以 60s TTL 共享一次结果，跨页面
 * 复用，latest 已带 fitnessLevel/tuneLevel/fitnessTags（同表 LATERAL 扩列）。
 */
import { computed, ref } from 'vue';

import { getDiagnosisRunsLatestApi } from '#/api/diagnosis';

const TTL_MS = 60_000;

interface CacheEntry {
  at: number;
  plantNodeId: string | undefined;
  items: Awaited<ReturnType<typeof getDiagnosisRunsLatestApi>>['items'];
}

const cache = ref<CacheEntry | null>(null);
const loading = ref(false);
const error = ref(false);

export function useLatestOverviewCache() {
  async function load(plantNodeId?: string, force = false): Promise<void> {
    const hit =
      !force &&
      cache.value &&
      Date.now() - cache.value.at < TTL_MS &&
      cache.value.plantNodeId === plantNodeId;
    if (hit || loading.value) return;
    loading.value = true;
    error.value = false;
    try {
      const res = await getDiagnosisRunsLatestApi(plantNodeId);
      cache.value = { at: Date.now(), items: res.items, plantNodeId };
    } catch {
      error.value = true;
    } finally {
      loading.value = false;
    }
  }

  return {
    error,
    items: computed(() => cache.value?.items ?? []),
    load,
    loading,
    /** 命中时间（调试/展示用；null=未加载） */
    loadedAt: computed(() => cache.value?.at ?? null),
  };
}
