/**
 * 回路工作台（新版）回路上下文 —— 清单/装置树/选中回路/实时值
 *
 * 数据链路（对齐现有监控页，复用全局单例，禁止创建第二连接）：
 * - 清单：GET /loops/monitor（一次拉全量供本地筛选/搜索/虚拟滚动）
 * - 装置树：GET /plant-nodes（回路计数由前端按清单 unitName 聚合）
 * - 实时值：realtimeWs 推送经 useLoopRealtime().applyMessage 局部更新；
 *   bindLoopInterest 声明兴趣集合（服务端订阅过滤，非本页回路零流量）
 */
import { computed, ref, shallowRef } from 'vue';
import { watch } from 'vue';

import type { LoopApi } from '#/api/loop';

import { getLoopMonitorListApi } from '#/api/loop';
import { getPlantNodeTreeApi } from '#/api/plant-node';
import type { PlantNodeApi } from '#/api/plant-node';
import {
  bindLoopInterest,
  parseTagCode,
  useLoopRealtime,
} from '#/composables/use-loop-realtime';

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

/** 清单拉取页大小（本地筛选所需全量；>500 回路场景再引入服务端筛选） */
const LIST_PAGE_SIZE = 500;

/** score → 等级字母（对齐 GRADE_THRESHOLDS 1–5 档） */
function scoreToGrade(score: null | number | undefined): GradeFilter | null {
  if (score === null || score === undefined || Number.isNaN(score))
    return null;
  if (score >= 90) return 'A';
  if (score >= 80) return 'B';
  if (score >= 60) return 'C';
  if (score >= 40) return 'D';
  return 'E';
}

export function useWb360Loop(initialLoopId: null | string) {
  const loops = shallowRef<LoopApi.MonitorListItem[]>([]);
  const loopsLoading = ref(false);
  const loopsError = ref<null | string>(null);
  const tree = shallowRef<SpineTreeNode[]>([]);

  const selectedLoopId = ref<null | string>(initialLoopId);
  const selectedUnit = ref<null | string>(null); // unitName（清单口径）
  const keyword = ref('');
  const gradeFilter = ref<GradeFilter | 'all'>('all');

  /** 实时值承载对象（applyMessage 鸭子类型；切换回路时整体重建） */
  const current = ref<LoopApi.MonitorListItem | null>(null);

  const { applyMessage, connectionStatus, lastMessageAt, onMessage, start } =
    useLoopRealtime();

  /** 拉取回路清单（全量本地筛选） */
  async function loadLoops() {
    loopsLoading.value = true;
    loopsError.value = null;
    try {
      const res = await getLoopMonitorListApi({
        page: 1,
        pageSize: LIST_PAGE_SIZE,
        sortBy: 'tagName',
        sortOrder: 'asc',
        view: 'list',
      });
      loops.value = res.items;
      if (res.total > LIST_PAGE_SIZE) {
        // 诚实化：清单超页禁止静默截断，显式提示
        loopsError.value = `回路清单仅加载前 ${LIST_PAGE_SIZE} 条（共 ${res.total} 条），请用搜索/筛选缩小范围`;
      }
    } catch (err) {
      loopsError.value = err instanceof Error ? err.message : '回路清单加载失败';
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

  // WS：兴趣集合跟随选中回路位号；消息更新头部实时值并转发趋势层
  const realtimeHandlers = new Set<(payload: {
    collectTime: number;
    quality: null | string;
    role: string;
    tagName: string;
    value: null | number;
  }) => void>();

  bindLoopInterest(() => {
    const tag = current.value?.tagName;
    return tag ? [tag] : [];
  });

  onMessage((msg) => {
    if (!current.value) return;
    const parsed = parseTagCode(msg.tagCode);
    if (!parsed || parsed.tagName !== current.value.tagName) return;
    const applied = applyMessage(msg, [current.value]);
    if (!applied) return;
    for (const h of realtimeHandlers) {
      const ts = msg.collectTime ? Date.parse(msg.collectTime) : Date.now();
      const value =
        msg.valueValid === false || msg.value === undefined
          ? Number.NaN
          : Number.parseFloat(msg.value);
      // WS 数值质量码（共享契约：1=Good 0=Bad）→ 趋势层字符串口径
      const quality = msg.quality === 0 ? 'BAD' : msg.quality > 0 ? 'GOOD' : null;
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
    selectLoop,
    selectedLoop,
    selectedLoopId,
    selectedUnit,
    startRealtime: start,
    tree,
  };
}
