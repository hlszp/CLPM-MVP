import type { LoopApi } from '#/api/loop';
import type { PlantNodeApi } from '#/api/plant-node';
import type { RealtimeUpdatable } from '#/composables/use-loop-realtime';

/**
 * 回路工作台（新版）回路上下文 —— 清单/装置树/选中回路/实时值
 *
 * 数据链路（对齐现有监控页，复用全局单例，禁止创建第二连接）：
 * - 清单：GET /loops/monitor（一次拉全量供本地筛选/搜索/虚拟滚动）
 * - 装置树：GET /plant-nodes（回路计数由前端按清单 unitName 聚合）
 * - 实时值：realtimeWs 推送经 useLoopRealtime().applyMessage 局部更新；
 *   bindLoopInterest 声明兴趣集合（服务端订阅过滤，非本页回路零流量）
 * - 量程：选中回路 → GET /loops/{id}/tags 取 PV/OP tagId → GET /tags/{id}
 *   取 rangeMin/Max/unit（趋势轴定标：PV/SP 左轴满量程、OP 右轴 OP 量程；
 *   量程缺失时趋势退回数据自适应域——诚实降级，不虚构量程）
 */
import { computed, ref, shallowRef } from 'vue';
import { watch } from 'vue';

import { getLoopMonitorListApi, getLoopTagsApi } from '#/api/loop';
import { getPlantNodeTreeApi } from '#/api/plant-node';
import { getTagDetailApi } from '#/api/tag';
import {
  bindLoopInterest,
  parseTagCode,
  useLoopRealtime,
} from '#/composables/use-loop-realtime';
import { scoreToGradeInfo } from '#/constants/clpm-ui';

/** 装置树节点（展示模型：plant → unit，unit 附回路计数） */
export interface SpineTreeNode {
  id: string;
  name: string;
  /** 回路计数（unit 层；plant 层为子单元求和） */
  count: number;
  parentId: null | string;
  type: string;
  units?: SpineTreeNode[];
}

/** 等级筛选档（A–E 对应 GRADE_THRESHOLDS 1–5 档；? = 无评分） */
export type GradeFilter = 'A' | 'B' | 'C' | 'D' | 'E' | 'none';

/** PV/OP 位号量程（趋势轴定标；缺失项为 null → 趋势退回数据域） */
export interface Wb360Ranges {
  opRange: null | { hi: number; lo: number };
  pvRange: null | { hi: number; lo: number };
  pvUnit: null | string;
}

/** 清单拉取页大小（后端上限 100/页）与页数上限（本地筛选所需全量；
 *  超出上限显式截断提示，>500 回路场景再引入服务端筛选） */
const LIST_PAGE_SIZE = 100;
const LIST_MAX_PAGES = 5;

/** score → 等级字母（A–E 对应 GRADE_THRESHOLDS 1–5 档；P2 起单源 scoreToGradeInfo） */
function scoreToGrade(score: null | number | undefined): GradeFilter | null {
  const info = scoreToGradeInfo(score);
  return info ? (info.letter as GradeFilter) : null;
}

export function useWb360Loop(initialLoopId: null | string) {
  const loops = shallowRef<LoopApi.MonitorListItem[]>([]);
  const loopsLoading = ref(false);
  const loopsError = ref<null | string>(null);
  const tree = shallowRef<SpineTreeNode[]>([]);

  const selectedLoopId = ref<null | string>(initialLoopId);
  const selectedUnit = ref<null | string>(null); // unitName（清单口径）
  const keyword = ref('');
  const gradeFilter = ref<'all' | GradeFilter>('all');

  /** 实时值承载对象（applyMessage 鸭子类型；切换回路时整体重建） */
  const current = ref<LoopApi.MonitorListItem | null>(null);

  const { applyMessage, connectionStatus, lastMessageAt, onMessage, start } =
    useLoopRealtime();

  /** 拉取回路清单（分页拉全量，本地筛选；后端 pageSize 上限 100） */
  async function loadLoops() {
    loopsLoading.value = true;
    loopsError.value = null;
    try {
      const fetchPage = (page: number) =>
        getLoopMonitorListApi({
          page,
          pageSize: LIST_PAGE_SIZE,
          sortBy: 'tagName',
          sortOrder: 'asc',
        });
      const first = await fetchPage(1);
      const items = [...first.items];
      const totalPages = Math.ceil(first.total / LIST_PAGE_SIZE);
      for (let p = 2; p <= Math.min(totalPages, LIST_MAX_PAGES); p++) {
        const res = await fetchPage(p);
        items.push(...res.items);
      }
      loops.value = items;
      if (first.total > items.length) {
        // 诚实化：清单超页禁止静默截断，显式提示
        loopsError.value = `回路清单仅加载前 ${items.length} 条（共 ${first.total} 条），请用搜索/筛选缩小范围`;
      }
    } catch (error) {
      loopsError.value =
        error instanceof Error ? error.message : '回路清单加载失败';
      loops.value = [];
    } finally {
      loopsLoading.value = false;
    }
  }

  /** 拉取装置树并按清单聚合单元回路计数 */
  async function loadTree() {
    try {
      const nodes = await getPlantNodeTreeApi();
      // 展开 FACTORY → UNIT 两级（AREA 归并展示为其 UNIT 子层）
      const plants: SpineTreeNode[] = [];
      const unitCount = new Map<string, number>();
      for (const l of loops.value) {
        const key = l.unitName ?? '';
        unitCount.set(key, (unitCount.get(key) ?? 0) + 1);
      }
      const walk = (node: PlantNodeApi.PlantNode): SpineTreeNode[] => {
        const out: SpineTreeNode[] = [];
        for (const child of node.children ?? []) {
          if (child.type === 'UNIT') {
            out.push({
              count: unitCount.get(child.name) ?? 0,
              id: child.id,
              name: child.name,
              parentId: node.id,
              type: child.type,
            });
          } else {
            out.push(...walk(child));
          }
        }
        return out;
      };
      for (const n of nodes) {
        const units = walk(n);
        plants.push({
          count: units.reduce((s, u) => s + u.count, 0),
          id: n.id,
          name: n.name,
          parentId: null,
          type: n.type,
          units,
        });
      }
      tree.value = plants;
    } catch {
      tree.value = [];
    }
  }

  /** 左脊柱过滤后的清单（单元/关键词/等级三重过滤） */
  const filteredLoops = computed(() => {
    const kw = keyword.value.trim().toLowerCase();
    return loops.value.filter((l) => {
      if (selectedUnit.value && l.unitName !== selectedUnit.value) return false;
      if (kw && !l.tagName.toLowerCase().includes(kw)) return false;
      if (gradeFilter.value !== 'all') {
        const g = scoreToGrade(l.score);
        if (gradeFilter.value === 'none' ? g !== null : g !== gradeFilter.value)
          return false;
      }
      return true;
    });
  });

  const selectedLoop = computed(() => current.value);

  /** 选中回路（query 同步由页面层负责） */
  function selectLoop(loopId: null | string) {
    selectedLoopId.value = loopId;
  }

  // 选中/清单就绪 → 重建 current（初始值取清单行；query 指定未命中时回退首行）
  watch(
    [selectedLoopId, loops],
    ([id, list]) => {
      if (!list || list.length === 0) {
        current.value = null;
        return;
      }
      const hit =
        (id ? list.find((l) => l.loopId === id) : undefined) ?? list[0]!;
      if (current.value?.loopId !== hit.loopId) {
        current.value = { ...hit, currentValues: { ...hit.currentValues } };
      }
      if (selectedLoopId.value !== hit.loopId) {
        selectedLoopId.value = hit.loopId;
      }
    },
    { immediate: true },
  );

  // ── PV/OP 量程（趋势轴定标，2026-10-02 终验需求）──
  const ranges = ref<Wb360Ranges>({
    opRange: null,
    pvRange: null,
    pvUnit: null,
  });
  const rangesCache = new Map<string, Wb360Ranges>();

  function validRange(d: null | { rangeMax?: null | number; rangeMin?: null | number }) {
    if (
      d &&
      typeof d.rangeMax === 'number' &&
      typeof d.rangeMin === 'number' &&
      d.rangeMax > d.rangeMin
    ) {
      return { hi: d.rangeMax, lo: d.rangeMin };
    }
    return null;
  }

  async function loadRanges(loopId: string) {
    const cached = rangesCache.get(loopId);
    if (cached) {
      ranges.value = cached;
      return;
    }
    ranges.value = { opRange: null, pvRange: null, pvUnit: null };
    try {
      const tags = await getLoopTagsApi(loopId);
      const byRole = (role: string) =>
        tags.tags.find((t) => t.role === role && t.tagId) ?? null;
      const pvTag = byRole('PV');
      const opTag = byRole('OP');
      const [pvDetail, opDetail] = await Promise.all([
        pvTag?.tagId
          ? getTagDetailApi(pvTag.tagId).catch(() => null)
          : Promise.resolve(null),
        opTag?.tagId
          ? getTagDetailApi(opTag.tagId).catch(() => null)
          : Promise.resolve(null),
      ]);
      const next: Wb360Ranges = {
        opRange: validRange(opDetail),
        pvRange: validRange(pvDetail),
        pvUnit: pvDetail?.unit ?? null,
      };
      rangesCache.set(loopId, next);
      ranges.value = next;
    } catch {
      // 量程获取失败：保持空（趋势退回数据域），不阻塞主链路
    }
  }

  watch(
    () => current.value?.loopId,
    (id) => {
      if (id) void loadRanges(id);
    },
  );

  // WS：兴趣集合跟随选中回路位号；消息更新头部实时值并转发趋势层
  const realtimeHandlers = new Set<
    (payload: {
      collectTime: number;
      quality: null | string;
      role: string;
      tagName: string;
      value: null | number;
    }) => void
  >();

  bindLoopInterest(() => {
    const tag = current.value?.tagName;
    return tag ? [tag] : [];
  });

  onMessage((msg) => {
    if (!current.value) return;
    const parsed = parseTagCode(msg.tagCode);
    if (!parsed || parsed.tagName !== current.value.tagName) return;
    // 鸭子类型桥接（MonitorCurrentValues 缺 PID 三值字段，applyMessage
    // 按 tagName 匹配后仅写命中角色；先例 tuning/workbench.vue 同口径）
    const applied = applyMessage(msg, [
      current.value as unknown as RealtimeUpdatable,
    ]);
    if (!applied) return;
    for (const h of realtimeHandlers) {
      const ts = msg.collectTime ? Date.parse(msg.collectTime) : Date.now();
      const value =
        msg.valueValid === false || msg.value === undefined
          ? Number.NaN
          : Number.parseFloat(msg.value);
      // WS 数值质量码（共享契约：1=Good 0=Bad）→ 趋势层字符串口径
      const quality =
        msg.quality === 0 ? 'BAD' : (msg.quality > 0 ? 'GOOD' : null);
      h({
        collectTime: Number.isNaN(ts) ? Date.now() : ts,
        quality,
        role: parsed.role,
        tagName: parsed.tagName,
        value: Number.isNaN(value) ? null : value,
      });
    }
  });

  /** 趋势层注册实时点回调（realtime 模式追加用） */
  function onRealtimePoint(
    handler: (payload: {
      collectTime: number;
      quality: null | string;
      role: string;
      tagName: string;
      value: null | number;
    }) => void,
  ) {
    realtimeHandlers.add(handler);
    return () => {
      realtimeHandlers.delete(handler);
    };
  }

  return {
    connectionStatus,
    current,
    filteredLoops,
    gradeFilter,
    keyword,
    lastMessageAt,
    loadLoops,
    loadTree,
    loops,
    loopsError,
    loopsLoading,
    onRealtimePoint,
    ranges,
    selectLoop,
    selectedLoop,
    selectedLoopId,
    selectedUnit,
    startRealtime: start,
    tree,
  };
}
