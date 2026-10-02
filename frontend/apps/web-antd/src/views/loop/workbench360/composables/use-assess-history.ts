/**
 * 评估剖面数据（workbench360 P2，P2-2）
 *
 * 三来源汇聚口径（D6 + API 契约 §1.3 / G2 结论）：
 * - 自动（整点调度）与重算（backfill 覆盖）同写 kpi_snapshot_hourly，
 *   行内无来源字段，二者不可区分 → 来源列/筛选合并为「整点（自动/重算）」；
 * - 手动（自定义时段）写 kpi_snapshot_custom，G2（后端联合查询）落地前
 *   无按回路查询出口 → 显式缺数据提示，禁止演示数据（诚实化红线）。
 *
 * 最新快照 = 历史首页首行（tsStart DESC），一次请求同时供摘要卡/旅程条/列表。
 * 页面层（index.vue）创建实例并 provide（旅程条失分摘要、页头适用性徽章、
 * 评估剖面共用）；剖面组件经 useInjectedAssessHistory 消费，避免二次请求。
 */
import type { InjectionKey, Ref } from 'vue';

import type { KpiSnapshotItem } from '#/api/metric';

import { computed, inject, provide, ref, watch } from 'vue';

import { getLoopSnapshotsApi } from '#/api/metric';

/** 来源筛选档（G2 落地前：hourly=整点自动/重算；manual=手动，缺数据） */
export type AssessSourceFilter = 'all' | 'hourly' | 'manual';

/**
 * 评估历史行：快照 + G1 插接位（后端 KpiSnapshotListItem 已含
 * fitnessLevel/fitnessTags 键，当前恒 null；G1 落地即有值，前端免改）
 */
export type AssessRow = KpiSnapshotItem & {
  fitnessLevel?: null | string;
  fitnessTags?: null | string[];
};

/** 历史页大小（后端 le=100；工作区内滚动列表取适中值） */
export const ASSESS_HISTORY_PAGE_SIZE = 20;

/** 核心六率（失分主因推导输入；名称对齐 KPI_TERM_EXPLANATIONS 口径） */
const CORE_RATES: Array<{
  key: keyof AssessRow & string;
  label: string;
}> = [
  { key: 'steadyRate', label: '平稳率' },
  { key: 'effectiveAutoRate', label: '有效自控率' },
  { key: 'accuracyRate', label: '准确率' },
  { key: 'fastRate', label: '快速率' },
  { key: 'autoModeRate', label: '自控率' },
  { key: 'goodValueRate', label: '好值率' },
];

/**
 * 失分主因摘要（契约 §1.1"失分摘要前端算"）。
 *
 * 口径：核心六率升序取最低 2 项（<80 才列）；稳定时间超理想值 3 倍时附带。
 * 仅描述快照内真实字段，不引入虚构阈值。
 */
export function deriveLossSummary(snap: AssessRow | null): null | string {
  if (!snap || snap.status === 'INCONCLUSIVE') return null;
  const rates = CORE_RATES.map((c) => ({
    label: c.label,
    value: snap[c.key] as null | number,
  }))
    .filter(
      (r): r is { label: string; value: number } => typeof r.value === 'number',
    )
    .toSorted((a, b) => a.value - b.value);
  if (rates.length === 0) return null;
  const parts = rates
    .slice(0, 2)
    .filter((r) => r.value < 80)
    .map((r) => `${r.label} ${r.value.toFixed(1)}%`);
  if (
    typeof snap.settlingTime === 'number' &&
    typeof snap.idealSettlingTime === 'number' &&
    snap.idealSettlingTime > 0 &&
    snap.settlingTime > snap.idealSettlingTime * 3
  ) {
    parts.push(`稳定时间 ${snap.settlingTime.toFixed(0)}s`);
  }
  return parts.length > 0 ? parts.join(' · ') : null;
}

export interface AssessHistoryApi {
  error: Ref<null | string>;
  latest: Ref<AssessRow | null>;
  loadHistory: (page?: number) => Promise<void>;
  loading: Ref<boolean>;
  page: Ref<number>;
  pageSize: number;
  rows: Ref<AssessRow[]>;
  sourceFilter: Ref<AssessSourceFilter>;
  total: Ref<number>;
}

const ASSESS_HISTORY_KEY: InjectionKey<AssessHistoryApi> = Symbol(
  'wb360-assess-history',
);

export function useAssessHistory(loopId: Ref<null | string>): AssessHistoryApi {
  const rows = ref<AssessRow[]>([]);
  const total = ref(0);
  const page = ref(1);
  const loading = ref(false);
  const error = ref<null | string>(null);
  /** 手动来源（G2）：显式缺数据态标记，由 UI 渲染提示 */
  const sourceFilter = ref<AssessSourceFilter>('all');

  const latest = computed(() => rows.value[0] ?? null);

  async function loadHistory(targetPage = 1) {
    const id = loopId.value;
    if (!id) {
      rows.value = [];
      total.value = 0;
      return;
    }
    loading.value = true;
    error.value = null;
    try {
      const res = await getLoopSnapshotsApi({
        latestOnly: false,
        loopId: id,
        page: targetPage,
        pageSize: ASSESS_HISTORY_PAGE_SIZE,
        sortBy: 'tsStart',
        sortOrder: 'desc',
      });
      rows.value = res.items ?? [];
      total.value = res.total ?? rows.value.length;
      page.value = targetPage;
    } catch (error_) {
      rows.value = [];
      total.value = 0;
      error.value =
        error_ instanceof Error ? error_.message : '评估历史加载失败';
    } finally {
      loading.value = false;
    }
  }

  /** 选中回路变化即重载首页；任务完成后由剖面调 loadHistory(1) 刷新 */
  watch(
    loopId,
    (id) => {
      if (id) loadHistory(1);
    },
    { immediate: true },
  );

  const api: AssessHistoryApi = {
    error,
    latest,
    loadHistory,
    loading,
    page,
    pageSize: ASSESS_HISTORY_PAGE_SIZE,
    rows,
    sourceFilter,
    total,
  };
  provide(ASSESS_HISTORY_KEY, api);
  return api;
}

/** 剖面组件消费页面级评估历史（未提供时本地降级创建，保证可独立渲染） */
export function useInjectedAssessHistory(
  loopId: Ref<null | string>,
): AssessHistoryApi {
  return inject(ASSESS_HISTORY_KEY, null) ?? useAssessHistory(loopId);
}
