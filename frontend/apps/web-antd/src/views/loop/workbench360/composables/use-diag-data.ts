/**
 * 诊断剖面数据（workbench360 P3，P3-2/P3-3）
 *
 * 对齐 API 契约 §1.4：
 * - 最新结论卡：GET /diagnosis/runs/latest?loopId=（一回路一条；未诊断 runId=null，
 *   含 metricSummary 正负指标与 runCount"第 N 次"）；
 * - 历史表：GET /diagnosis/runs?loopId=&page=（createdAt 倒序，后端默认排序）。
 *
 * 页面层（index.vue）创建实例并 provide（旅程条/缩略卡 P4 收口可共用）；
 * 剖面组件经 useInjectedDiagData 消费，避免二次请求。
 */
import type { InjectionKey, Ref } from 'vue';

import type { DiagnosisApi } from '#/api/diagnosis';

import { inject, provide, ref, watch } from 'vue';

import { getDiagnosisRunsApi, getDiagnosisRunsLatestApi } from '#/api/diagnosis';

/** 历史页大小（工作区内滚动列表取适中值；后端分页上限 100） */
export const DIAG_HISTORY_PAGE_SIZE = 20;

export interface DiagDataApi {
  error: Ref<null | string>;
  /** 每回路最新诊断概览（未诊断为 runId=null 行；加载中/失败为 null） */
  latest: Ref<DiagnosisApi.LatestRunItem | null>;
  loadHistory: (page?: number) => Promise<void>;
  loadLatest: () => Promise<void>;
  loading: Ref<boolean>;
  page: Ref<number>;
  pageSize: number;
  /** 任务完成/复核提交后就地刷新（最新结论 + 当前页历史） */
  refresh: () => Promise<void>;
  rows: Ref<DiagnosisApi.RunListItem[]>;
  total: Ref<number>;
}

const DIAG_DATA_KEY: InjectionKey<DiagDataApi> = Symbol('wb360-diag-data');

export function useDiagData(loopId: Ref<null | string>): DiagDataApi {
  const latest = ref<DiagnosisApi.LatestRunItem | null>(null);
  const rows = ref<DiagnosisApi.RunListItem[]>([]);
  const total = ref(0);
  const page = ref(1);
  const loading = ref(false);
  const error = ref<null | string>(null);

  async function loadLatest() {
    const id = loopId.value;
    if (!id) {
      latest.value = null;
      return;
    }
    try {
      const res = await getDiagnosisRunsLatestApi(undefined, id);
      latest.value = res.items?.[0] ?? null;
    } catch {
      // 最新结论加载失败不阻断历史表（各自可见化），保留上次值
      latest.value = null;
    }
  }

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
      const res = await getDiagnosisRunsApi({
        loopId: id,
        page: targetPage,
        pageSize: DIAG_HISTORY_PAGE_SIZE,
      });
      rows.value = res.items ?? [];
      total.value = res.total ?? rows.value.length;
      page.value = targetPage;
    } catch (error_) {
      rows.value = [];
      total.value = 0;
      error.value =
        error_ instanceof Error ? error_.message : '诊断历史加载失败';
    } finally {
      loading.value = false;
    }
  }

  /** 就地刷新（诊断完成/复核提交后）：最新结论 + 当前页 */
  async function refresh() {
    await Promise.all([loadLatest(), loadHistory(page.value)]);
  }

  // 选中回路变化即重载首页（历史 + 最新结论）
  watch(
    loopId,
    (id) => {
      if (!id) return;
      loadLatest();
      loadHistory(1);
    },
    { immediate: true },
  );

  const api: DiagDataApi = {
    error,
    latest,
    loadHistory,
    loadLatest,
    loading,
    page,
    pageSize: DIAG_HISTORY_PAGE_SIZE,
    refresh,
    rows,
    total,
  };
  provide(DIAG_DATA_KEY, api);
  return api;
}

/** 剖面组件消费页面级诊断数据（未提供时本地降级创建，保证可独立渲染） */
export function useInjectedDiagData(loopId: Ref<null | string>): DiagDataApi {
  return inject(DIAG_DATA_KEY, null) ?? useDiagData(loopId);
}

/** naive UTC ISO → dayjs 可解析字符串（对齐诊断模块补 Z 口径） */
export function diagWithZone(naiveIso: null | string): null | string {
  if (!naiveIso) return null;
  return /[Zz]|[+-]\d{2}:?\d{2}$/.test(naiveIso) ? naiveIso : `${naiveIso}Z`;
}
