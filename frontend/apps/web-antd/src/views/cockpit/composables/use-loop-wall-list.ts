/**
 * 驾驶舱回路状态墙 · 服务端分页列表（P5-04，03 号文 §8）
 *
 * 筛选/稳定排序/总数/分页全部服务端执行——消除"筛选/劣化排序触发客户端
 * 预抓全量 34 页"反模式（1000 回路场景每次筛选只发 1 个列表请求，
 * 《04》回路墙预算：每次筛选 ≤2 个列表请求）。
 *
 * 竞态（任务书【2】②）：复用 use-latest-request（不分叉第二套守卫）——
 * 装置 A→B 快速切换时，慢的 A 响应迟到被代次+目标键双重校验丢弃，
 * 不得覆盖 B 的数据。
 *
 * 刷新保状态：silent 刷新按"当前页"重拉（不清回第 1 页、不动筛选/选中）；
 * 失败保留旧数据（诚实化：静默清空会伪装成"当前条件下无回路"）。
 */
import type { Ref } from 'vue';

import type { CockpitApi } from '#/api/cockpit';
import type { LoopApi } from '#/api/loop';
import type { CockpitModeKey } from '#/store/cockpit';

import { ref } from 'vue';

import { getLoopMonitorListApi } from '#/api/loop';
import { useLatestRequest } from '#/composables/use-latest-request';

/** 2026-10-06 用户裁决：6 列 × 5 行 = 30 卡/页（页大小恒定对齐服务端分页） */
export const LOOP_WALL_PAGE_SIZE = 30;

/** 回路墙查询态（驾驶舱 store 的筛选 + 页码 + 节点） */
export interface LoopWallQuery {
  grades: CockpitApi.GradeKey[];
  modes: CockpitModeKey[];
  page: number;
  plantNodeId: null | string;
  sortBy: 'degradeDesc' | 'scoreDesc';
}

/** 单次加载选项 */
export interface LoopWallLoadOptions {
  /**
   * 定位回路（筛选/排序/刷新变化后跳到其所在页）：服务端返回 focusPage；
   * null=不在结果集内（调用方据此清选中回聚合视图）
   */
  focusLoopId?: null | string;
  /** 静默刷新：失败保留旧数据、不闪 loading；携带 aggregate 刷新分布 */
  silent?: boolean;
}

/** 查询态 → 服务端排序参数（劣化降序 = scoreDelta 升序 NULLS LAST） */
export function toServerSort(sortBy: 'degradeDesc' | 'scoreDesc'): {
  sortBy: 'score' | 'scoreDelta';
  sortOrder: 'asc' | 'desc';
} {
  return sortBy === 'degradeDesc'
    ? { sortBy: 'scoreDelta', sortOrder: 'asc' }
    : { sortBy: 'score', sortOrder: 'desc' };
}

/** 查询态 → 请求键（竞态守卫的目标 ID；语义相同的不同调用合并同键） */
export function loopWallRequestKey(q: LoopWallQuery): string {
  return [
    q.plantNodeId ?? '',
    [...q.grades].toSorted().join(','),
    [...q.modes].toSorted().join(','),
    q.sortBy,
    q.page,
  ].join('|');
}

export interface UseLoopWallListReturn {
  /** 首次加载是否完成（初始 spinner 与后续局部加载提示的区分） */
  booted: Ref<boolean>;
  /** 当前页原始列表项（WS 实时值直接写入 item.currentValues） */
  items: Ref<LoopApi.MonitorListItem[]>;
  /** 任一加载在途（翻页/筛选；数据保留仅提示） */
  busy: Ref<boolean>;
  /** 服务端筛选后总数（分页页数与"共 N 个"以此为准，>1000 无预抓也准确） */
  total: Ref<number>;
  /** 节点口径 MODE 分布（服务端 aggregate；首页/静默刷新时更新） */
  modeDistribution: Ref<null | Record<string, number>>;
  /** 最近一次响应的 focus 页（null=focus 回路不在结果集） */
  focusPage: Ref<null | number>;
  load: (query: LoopWallQuery, opts?: LoopWallLoadOptions) => Promise<void>;
}

export function useLoopWallList(): UseLoopWallListReturn {
  const guard = useLatestRequest<string>();

  const booted = ref(false);
  const items = ref<LoopApi.MonitorListItem[]>([]);
  const busy = ref(false);
  const total = ref(0);
  const modeDistribution = ref<null | Record<string, number>>(null);
  const focusPage = ref<null | number>(null);

  async function load(
    query: LoopWallQuery,
    opts: LoopWallLoadOptions = {},
  ): Promise<void> {
    const key = loopWallRequestKey(query);
    const { silent = false } = opts;
    guard.bump(key);
    busy.value = true;
    await guard.run(async (_signal, capturedEpoch) => {
      const isCurrent = () => guard.guard(key, capturedEpoch);
      try {
        const sort = toServerSort(query.sortBy);
        const res = await getLoopMonitorListApi({
          page: query.page,
          pageSize: LOOP_WALL_PAGE_SIZE,
          plantNodeId: query.plantNodeId ?? undefined,
          sortBy: sort.sortBy,
          sortOrder: sort.sortOrder,
          grades: query.grades.length > 0 ? query.grades.join(',') : undefined,
          modes: query.modes.length > 0 ? query.modes.join(',') : undefined,
          focusLoopId: opts.focusLoopId ?? undefined,
          // 第 1 页与静默刷新携带全量聚合（模式分布/树角标口径）；
          // 翻页请求跳过——961+ 回路下是固定全量开销（《04》预算）
          withAggregate: query.page === 1 || silent,
          latestScoredHour: true,
        });
        if (!isCurrent()) return; // A→B 竞态：迟到的旧响应丢弃
        items.value = res.items ?? [];
        total.value = res.total ?? 0;
        focusPage.value = res.focusPage ?? null;
        if (res.aggregate?.modeDistribution) {
          modeDistribution.value = res.aggregate.modeDistribution;
        }
        booted.value = true;
      } catch {
        if (!isCurrent()) return;
        if (!silent) {
          // 非静默（筛选/翻页/首载）失败：显式回到空态+总数 0（提示区显示）
          items.value = [];
          total.value = 0;
          focusPage.value = null;
          booted.value = true;
        }
        // 静默刷新失败：保留旧页数据（不静默清空冒充空结果）
      } finally {
        if (isCurrent()) busy.value = false;
      }
    });
  }

  return {
    booted,
    busy,
    focusPage,
    items,
    load,
    modeDistribution,
    total,
  };
}
