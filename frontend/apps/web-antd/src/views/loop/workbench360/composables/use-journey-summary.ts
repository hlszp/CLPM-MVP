/**
 * 旅程条/缩略卡/状态栏摘要（workbench360 P4-4 统一动线收口）
 *
 * 整定/处置段的页头级真实数据（P1-P3 期间为显式空态）：
 * - 整定段：最新整定记录（算法 + 状态 + 时间）与整定任务总数；
 * - 处置段：在途工单数（待执行/执行中/重开三态并行 status 查询 total 相加）
 *   与最新工单号；
 * - 事件标注层：整定任务 ◆ / 工单实施 ▮ 徽标数据（TrendChart 按域过滤）。
 * 模块禁用（tuning/handling）时由调用方跳过对应请求（零请求口径，v3 §9）。
 */
import type { HandlingApi } from '#/api/handling';
import type { TuningApi } from '#/api/tuning';

import { computed, ref, watch } from 'vue';

import dayjs from 'dayjs';

import { getHandlingOrdersApi } from '#/api/handling';
import { getTuningTasksApi } from '#/api/tuning';

import { diagWithZone } from './use-diag-data';

export interface JourneyEventSeed {
  /** 徽标形（◆ 整定 / ▮ 验证/工单） */
  glyph: string;
  key: string;
  label: string;
  section: 'handling' | 'tuning';
  /** naive-UTC ISO */
  tsIso: string;
}

export function useJourneySummary(
  loopIdRef: () => null | string,
  options: { enabled: () => { handling: boolean; tuning: boolean } },
) {
  /* ── 整定段 ── */
  const latestTask = ref<null | TuningApi.TuningTaskItem>(null);
  const tuningTotal = ref(0);
  const tuningLoading = ref(false);
  const tuningError = ref('');
  /** 近期整定任务（事件徽标种子；首页 10 条） */
  const recentTasks = ref<TuningApi.TuningTaskItem[]>([]);

  /* ── 处置段 ── */
  const inFlightCount = ref(0);
  const latestOrder = ref<HandlingApi.OrderItem | null>(null);
  const handlingLoading = ref(false);
  const handlingError = ref('');
  /** 近期工单（事件徽标种子；首页 10 条） */
  const recentOrders = ref<HandlingApi.OrderItem[]>([]);

  async function loadTuning(loopId: string) {
    tuningLoading.value = true;
    tuningError.value = '';
    try {
      const res = await getTuningTasksApi({
        loopId,
        page: 1,
        pageSize: 10,
      });
      recentTasks.value = res.items;
      latestTask.value = res.items[0] ?? null;
      tuningTotal.value = res.total;
    } catch (error: any) {
      tuningError.value = error?.message ?? '整定记录加载失败';
      recentTasks.value = [];
      latestTask.value = null;
      tuningTotal.value = 0;
    } finally {
      tuningLoading.value = false;
    }
  }

  async function loadHandling(loopId: string) {
    handlingLoading.value = true;
    handlingError.value = '';
    try {
      // 在途口径：三态并行 status 查询 total 相加（同旧整定工作台 loadOpenItems）
      const statuses: HandlingApi.OrderStatus[] = [
        'PENDING',
        'EXECUTING',
        'REOPENED',
      ];
      const [counts, recent] = await Promise.all([
        Promise.all(
          statuses.map((status) =>
            getHandlingOrdersApi({ loopId, page: 1, pageSize: 1, status }),
          ),
        ),
        getHandlingOrdersApi({ loopId, page: 1, pageSize: 10 }),
      ]);
      inFlightCount.value = counts.reduce((s, r) => s + r.total, 0);
      recentOrders.value = recent.items;
      latestOrder.value = recent.items[0] ?? null;
    } catch (error: any) {
      handlingError.value = error?.message ?? '处置工单加载失败';
      inFlightCount.value = 0;
      recentOrders.value = [];
      latestOrder.value = null;
    } finally {
      handlingLoading.value = false;
    }
  }

  function refresh() {
    const id = loopIdRef();
    if (!id) return;
    if (options.enabled().tuning) void loadTuning(id);
    if (options.enabled().handling) void loadHandling(id);
  }

  watch(
    () => [loopIdRef(), options.enabled().tuning, options.enabled().handling],
    () => {
      // 回路切换/模块开关变化：禁用模块清数据（零请求 + 空态一致）
      const id = loopIdRef();
      const en = options.enabled();
      if (!en.tuning) {
        latestTask.value = null;
        tuningTotal.value = 0;
        recentTasks.value = [];
      }
      if (!en.handling) {
        inFlightCount.value = 0;
        latestOrder.value = null;
        recentOrders.value = [];
      }
      if (!id) return;
      if (en.tuning) void loadTuning(id);
      if (en.handling) void loadHandling(id);
    },
    { immediate: true },
  );

  /** 旅程条/缩略卡整定段摘要（null=显式空态） */
  const tuningSummary = computed(() => {
    const t = latestTask.value;
    if (!t) return null;
    const local = diagWithZone(t.createdAt);
    return {
      algoLabel: t.algorithm,
      createdAtText: local ? dayjs(local).format('MM-DD HH:mm') : null,
      statusText: t.status,
      total: tuningTotal.value,
    };
  });

  /** 旅程条/缩略卡处置段摘要（null=显式空态） */
  const handlingSummary = computed(() => {
    if (!latestOrder.value && inFlightCount.value === 0) return null;
    return {
      inFlightCount: inFlightCount.value,
      latestOrderNo: latestOrder.value?.orderNo ?? null,
    };
  });

  /** 事件标注层种子（诊断 ▼ 由页面从 diagData 直接合成；此处只出 ◆/▮） */
  const eventSeeds = computed<JourneyEventSeed[]>(() => {
    const seeds: JourneyEventSeed[] = [];
    for (const t of recentTasks.value) {
      seeds.push({
        glyph: '◆',
        key: `tuning-${t.id}`,
        label: `整定 ${t.algorithm}`,
        section: 'tuning',
        tsIso: t.createdAt,
      });
    }
    for (const o of recentOrders.value) {
      if (!o.submittedAt) continue;
      seeds.push({
        glyph: '▮',
        key: `handling-${o.id}`,
        label: `工单实施 ${o.orderNo}`,
        section: 'handling',
        tsIso: o.submittedAt,
      });
    }
    return seeds;
  });

  return {
    eventSeeds,
    handlingError,
    handlingLoading,
    handlingSummary,
    refresh,
    tuningError,
    tuningLoading,
    tuningSummary,
  };
}
