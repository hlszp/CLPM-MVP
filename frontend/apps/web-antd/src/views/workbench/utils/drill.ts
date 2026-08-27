/**
 * 工作台统计下钻助手（追溯矩阵 13 号文 · G1/G2/G4 口径契约）
 *
 * 统一负责：
 * - G1 窗口映射：timeWindow/customStart/customEnd → startTime/endTime；
 *   endTime 取 lastRefreshAt（最近刷新时刻），与预计算窗口近似对齐
 * - G2 scope 映射：scopeId（plant_node.source_node_id）→ plantNodeId
 *   （plant_node.id，经 scopeTree.node_id 解析；GLOBAL 不带）
 * - G4 模块禁用态：目标模块 MAINTENANCE/UNINSTALLED 时不下钻
 *
 * 使用：const { drill, canDrill } = useWorkbenchDrill();
 * drill('diagnosis', '/diagnosis/records', { category: 'UTILIZATION' });
 */
import { useRouter } from 'vue-router';

import { useWorkbenchStore } from '#/store/workbench';

const WINDOW_MS: Record<string, number> = {
  '24h': 24 * 3600 * 1000,
  '7d': 7 * 24 * 3600 * 1000,
  '30d': 30 * 24 * 3600 * 1000,
};

export function useWorkbenchDrill() {
  const store = useWorkbenchStore();
  const router = useRouter();

  /** G1 窗口映射 → { startTime, endTime }（ISO8601；自定义窗口优先） */
  function windowQuery(): Record<string, string> {
    if (store.customStart && store.customEnd) {
      return { startTime: store.customStart, endTime: store.customEnd };
    }
    const end = store.lastRefreshAt ?? Date.now();
    const ms = WINDOW_MS[store.timeWindow] ?? WINDOW_MS['24h']!;
    return {
      startTime: new Date(end - ms).toISOString(),
      endTime: new Date(end).toISOString(),
    };
  }

  /** G2 scope 映射 → { plantNodeId }；GLOBAL/未解析到时返回 {} */
  function scopeQuery(): Record<string, string> {
    if (store.scopeType === 'GLOBAL' || store.scopeId === null) return {};
    const node = store.scopeTree.find((n) => n.id === store.scopeId);
    return node?.node_id ? { plantNodeId: node.node_id } : {};
  }

  /** G4 目标模块可下钻判定（CORE/ENABLED 才放行） */
  function canDrill(moduleKey: string): boolean {
    const status = store.getModuleStatus(moduleKey);
    return status === 'CORE' || status === 'ENABLED';
  }

  /**
   * 行级 scope 解析：source_node_id（行数据 id）→ plantNodeId。
   * 注意：scopeTree 仅含 FACTORY/AREA 节点，UNIT 行解析不到时返回 undefined
   * （调用方此时应省略 plantNodeId，避免带错口径）。
   */
  function resolvePlantNodeId(
    sourceId: null | number | undefined,
  ): string | undefined {
    if (sourceId === null || sourceId === undefined) return undefined;
    return store.scopeTree.find((n) => n.id === sourceId)?.node_id;
  }

  /**
   * 按节点名称解析 plantNodeId（仅用于行数据只带名称的场景，如诊断堆叠条的装置名）；
   * 名称可能重复，解析不到返回 undefined，调用方应省略该参数。
   */
  function resolvePlantNodeIdByName(
    name: null | string | undefined,
  ): string | undefined {
    if (!name) return undefined;
    return store.scopeTree.find((n) => n.name === name)?.node_id;
  }

  /**
   * 统一下钻入口：模块可用时跳转目标明细页，自动携带窗口+scope 口径。
   * opts.withWindow=false 时不带时间窗口（如整定向导页只接 loopId）。
   */
  function drill(
    moduleKey: string,
    path: string,
    extraQuery: Record<string, number | string> = {},
    opts: { withScope?: boolean; withWindow?: boolean } = {},
  ) {
    if (!canDrill(moduleKey)) return;
    const { withScope = true, withWindow = true } = opts;
    router.push({
      path,
      query: {
        ...(withWindow ? windowQuery() : {}),
        ...(withScope ? scopeQuery() : {}),
        ...extraQuery,
      },
    });
  }

  return {
    canDrill,
    drill,
    resolvePlantNodeId,
    resolvePlantNodeIdByName,
    scopeQuery,
    windowQuery,
  };
}
