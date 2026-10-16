import type { LoopApi } from '#/api/loop';
import type { PlantNodeApi } from '#/api/plant-node';

/**
 * 回路工作台全目录（P6-02a O02-CORE，2026-10-14 从 use-wb360-loop 拆出）
 *
 * 仅供全回路宿主（AllLoopWorkbenchHost）使用；单回路核心禁止调用本目录
 * （单对象读取走 use-wb360-loop，不拉全厂 monitor 列表）。
 *
 * - 清单：GET /loops/monitor 渐进加载（首页即渲染，余页分批并发并入，全量无截断）；
 * - 装置树：GET /plant-nodes，按稳定 nodeId 组织 FACTORY→UNIT（AREA 归并展示为
 *   其 UNIT 子层）；树选择身份为 nodeId（A21 同名节点不混），清单行只有 unitName
 *   可供关联，过滤时由 nodeId 解析名称后匹配（数据口径限制如实注释）；
 * - 回路计数随清单渐进到达自动重算；不做"选中回退首行"（对象有效性判定归宿主）。
 */
import { computed, ref, shallowRef, watch } from 'vue';

import { getLoopMonitorListApi } from '#/api/loop';
import { getPlantNodeTreeApi } from '#/api/plant-node';
import { scoreToGradeInfo } from '#/constants/clpm-ui';

/** 装置树节点（展示模型：plant → unit，unit 附回路计数；id 为稳定 nodeId） */
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

/** 清单拉取页大小（后端上限 100/页） */
const LIST_PAGE_SIZE = 100;
/** 余页拉取并发上限（monitor 列表是重查询，避免全部页同时压后端） */
const LIST_PAGE_CONCURRENCY = 4;

/** score → 等级字母（A–E 对应 GRADE_THRESHOLDS 1–5 档；单源 scoreToGradeInfo） */
function scoreToGrade(score: null | number | undefined): GradeFilter | null {
  const info = scoreToGradeInfo(score);
  return info ? (info.letter as GradeFilter) : null;
}

export function useWb360Directory() {
  const loops = shallowRef<LoopApi.MonitorListItem[]>([]);
  const loopsLoading = ref(false);
  /** 全部页加载完成（对象存在性判定的前提：渐进加载中不做"未命中"结论） */
  const loopsComplete = ref(false);
  const loopsError = ref<null | string>(null);
  const tree = shallowRef<SpineTreeNode[]>([]);
  /** 装置树加载失败信息（null=无失败；失败后可重试 loadTree） */
  const treeError = ref<null | string>(null);

  /** 树选择身份：稳定 nodeId（null=全部单元） */
  const selectedUnitNodeId = ref<null | string>(null);
  const keyword = ref('');
  const gradeFilter = ref<'all' | GradeFilter>('all');

  /** nodeId → unitName（清单行仅携带 unitName，过滤关联的唯一数据口径） */
  const unitNameByNodeId = new Map<string, string>();

  /** 拉取回路清单（渐进加载：首页到达立即渲染，余页按并发上限分批并行并入） */
  async function loadLoops() {
    loopsLoading.value = true;
    loopsComplete.value = false;
    loopsError.value = null;
    try {
      const fetchPage = (page: number) =>
        getLoopMonitorListApi({
          page,
          pageSize: LIST_PAGE_SIZE,
          sortBy: 'tagName',
          sortOrder: 'asc',
          // 全目录不消费 aggregate（全量聚合+Redis MODE 分布是每页固定全量开销）
          withAggregate: false,
        });
      const first = await fetchPage(1);
      loops.value = [...first.items];
      loopsLoading.value = false; // 首页即解锁首屏
      const totalPages = Math.ceil(first.total / LIST_PAGE_SIZE);
      const rest: number[] = [];
      for (let p = 2; p <= totalPages; p++) rest.push(p);
      for (let i = 0; i < rest.length; i += LIST_PAGE_CONCURRENCY) {
        const batch = await Promise.all(
          rest.slice(i, i + LIST_PAGE_CONCURRENCY).map((p) => fetchPage(p)),
        );
        loops.value = [...loops.value, ...batch.flatMap((b) => b.items)];
      }
      loopsComplete.value = true;
    } catch (error) {
      loopsError.value =
        error instanceof Error ? error.message : '回路清单加载失败';
      loops.value = [];
      loopsComplete.value = true; // 失败也是终态：宿主按"无法验证"显示而非永远等待
    } finally {
      loopsLoading.value = false;
    }
  }

  /** 拉取装置树（节点只拉一次缓存；失败显式置 treeError 可重试） */
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
    unitNameByNodeId.clear();
    const unitCount = new Map<string, number>();
    for (const l of loops.value) {
      const key = l.unitName ?? '';
      unitCount.set(key, (unitCount.get(key) ?? 0) + 1);
    }
    const walk = (node: PlantNodeApi.PlantNode): SpineTreeNode[] => {
      const out: SpineTreeNode[] = [];
      for (const child of node.children ?? []) {
        if (child.type === 'UNIT') {
          unitNameByNodeId.set(child.id, child.name);
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

  /** 当前选中单元名（nodeId 解析；未选/树未就绪为 null） */
  const selectedUnitName = computed(() =>
    selectedUnitNodeId.value
      ? (unitNameByNodeId.get(selectedUnitNodeId.value) ?? null)
      : null,
  );

  /** 左脊柱过滤后的清单（单元/关键词/等级三重过滤） */
  const filteredLoops = computed(() => {
    const kw = keyword.value.trim().toLowerCase();
    const unitName = selectedUnitName.value;
    return loops.value.filter((l) => {
      if (unitName && l.unitName !== unitName) return false;
      if (kw && !l.tagName.toLowerCase().includes(kw)) return false;
      if (gradeFilter.value !== 'all') {
        const g = scoreToGrade(l.score);
        if (gradeFilter.value === 'none' ? g !== null : g !== gradeFilter.value)
          return false;
      }
      return true;
    });
  });

  /** 对象存在性判定（宿主用；清单未完整前不做结论） */
  function hasLoop(loopId: string): boolean {
    return loops.value.some((l) => l.loopId === loopId);
  }

  return {
    filteredLoops,
    gradeFilter,
    hasLoop,
    keyword,
    loadLoops,
    loadTree,
    loops,
    loopsComplete,
    loopsError,
    loopsLoading,
    selectedUnitName,
    selectedUnitNodeId,
    tree,
    treeError,
  };
}
