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

/** 清单拉取页大小（后端上限 100/页） */
const LIST_PAGE_SIZE = 100;
/** 余页拉取并发上限（monitor 列表是重查询，避免全部页同时压后端） */
const LIST_PAGE_CONCURRENCY = 4;

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
  /**
   * 拉取回路清单（渐进加载，2026-10-03 生产 1209 回路提速）：
   * 首页到达立即渲染（首屏解锁），余页按并发上限分批并行并入、
   * 每批到达即更新（树/筛选为派生响应式，自动重算）；全量无截断。
   */
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
          // 回路工作台不消费 aggregate（全量聚合+Redis MODE 分布是每页
          // 固定全量开销，生产 1209 回路下秒级——2026-10-03 提速必关）
          withAggregate: false,
        });
      const first = await fetchPage(1);
      loops.value = [...first.items];
      loopsLoading.value = false; // 首页即解锁首屏（树/选中/趋势链路启动）
      const totalPages = Math.ceil(first.total / LIST_PAGE_SIZE);
      const rest: number[] = [];
      for (let p = 2; p <= totalPages; p++) rest.push(p);
      for (let i = 0; i < rest.length; i += LIST_PAGE_CONCURRENCY) {
        const batch = await Promise.all(
          rest.slice(i, i + LIST_PAGE_CONCURRENCY).map((p) => fetchPage(p)),
        );
        loops.value = [...loops.value, ...batch.flatMap((b) => b.items)];
      }
    } catch (error) {
      loopsError.value =
        error instanceof Error ? error.message : '回路清单加载失败';
      loops.value = [];
    } finally {
      loopsLoading.value = false;
    }
  }

  /** 装置树加载失败信息（null=无失败；失败后可重试 loadTree） */
  const treeError = ref<null | string>(null);

  /** 拉取装置树（节点只拉一次缓存；回路计数聚合随清单渐进到达自动重算）。
   *  失败显式置 treeError（此前静默置空会被"加载中"文案掩盖，用户无感知无重试） */
  async function loadTree() {
    if (plantNodesCache) {
      rebuildTree();
      return;
    }
    treeError.value = null;
    try {
      plantNodesCache = await getPlantNodeTreeApi();
      rebuildTree();
    } catch {
      tree.value = [];
      treeError.value = '装置树加载失败，请检查网络后重试';
    }
  }

  // 清单渐进到达（余页分批并入）→ 树计数自动重算
  watch(loops, () => {
    if (plantNodesCache) rebuildTree();
  });

  let plantNodesCache: null | PlantNodeApi.PlantNode[] = null;

  /** 按当前清单重建 FACTORY→UNIT 两级树（AREA 归并展示为其 UNIT 子层） */
  function rebuildTree() {
    const nodes = plantNodesCache;
    if (!nodes) return;
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
    treeError,
  };
}
