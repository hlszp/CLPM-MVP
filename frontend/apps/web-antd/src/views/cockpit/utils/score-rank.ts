/**
 * 驾驶舱总览 · 综合评分排名树组装（纯函数，2026-10-10 修订）
 *
 * 数据合并：/cockpit/node-tree（AREA→UNIT 层级 + nodeId + loopCount）×
 * /workbench/overview（plants=AREA 行 / units=UNIT 行的 score 与 metrics）。
 * 两侧行以 source_node_id（int `id`）为主键匹配，缺失回退按名称匹配
 * （precalc 行 id 可能为 null——G15 之后正常不会，但旧窗口行兼容）。
 *
 * 排序口径：装置按综合评分降序（并列保持稳定序），装置内单元同口径，
 * 单元排名为装置内序；无评分行排末尾（不编造名次，徽标显示 —）。
 */
import type { CockpitApi } from '#/api/cockpit';
import type { WorkbenchApi } from '#/api/workbench';

/** 排名树单元行（装置 children） */
export interface ScoreRankUnit {
  /** workbench 单元行键（id 或 name，模板 :key 用） */
  key: string;
  /** 单元名 */
  name: string;
  /** plant_node.id（联动趋势/雷达用） */
  nodeId: string;
  /** 装置内排名（无评分为 null） */
  rank: null | number;
  /** 综合评分（无数据 null） */
  score: null | number;
}

/** 排名树装置行 */
export interface ScoreRankPlant {
  /** 装置名 */
  name: string;
  /** plant_node.id（联动用） */
  nodeId: string;
  /** 全厂排名（无评分为 null） */
  rank: null | number;
  /** 综合评分（无数据 null） */
  score: null | number;
  /** 单元行（装置内按评分降序） */
  units: ScoreRankUnit[];
}

/** 排名选中态（排名区 ⇄ 趋势/雷达联动协议） */
export interface RankSelection {
  /** AREA=装置 / UNIT=单元 */
  type: 'AREA' | 'UNIT';
  /** 展示名 */
  name: string;
  nodeId: string;
}

function byScoreDesc<T extends { name: string; score: null | number }>(
  rows: T[],
): T[] {
  return [...rows].toSorted((a, b) => {
    if (a.score === null && b.score === null) return a.name.localeCompare(b.name, 'zh');
    if (a.score === null) return 1;
    if (b.score === null) return -1;
    if (b.score !== a.score) return b.score - a.score;
    return a.name.localeCompare(b.name, 'zh');
  });
}

function assignRank<T extends { rank: null | number; score: null | number }>(
  sorted: T[],
): void {
  let rank = 0;
  let prev: null | number = null;
  for (const row of sorted) {
    if (row.score === null) {
      row.rank = null;
    } else {
      // 并列同排名（与后端 rank 口径一致）
      rank = prev !== null && prev === row.score ? rank : rank + 1;
      prev = row.score;
      row.rank = rank;
    }
  }
}

/**
 * 组装装置×单元排名树。
 *
 * @param tree /cockpit/node-tree 结果（取 FACTORY 下 AREA 层；缺失层级容错）
 * @param plants /workbench/overview plants（AREA 预计算行）
 * @param units /workbench/overview units（UNIT 预计算行）
 */
export function buildScoreRankTree(
  tree: CockpitApi.NodeTreeNode[] | null | undefined,
  plants: null | undefined | WorkbenchApi.PlantRow[],
  units: null | undefined | WorkbenchApi.UnitRow[],
): ScoreRankPlant[] {
  // 装置层：node-tree 中所有 AREA 节点（无论嵌套深度，工厂直挂单元的
  // 退化结构也能覆盖）
  const areaNodes: { name: string; nodeId: string; units: CockpitApi.NodeTreeNode[] }[] =
    [];
  const walk = (nodes: CockpitApi.NodeTreeNode[]) => {
    for (const n of nodes) {
      if (n.type === 'AREA') {
        areaNodes.push({
          name: n.name,
          nodeId: n.nodeId,
          units: (n.children ?? []).filter((c) => c.type === 'UNIT'),
        });
      } else if (n.children?.length) {
        walk(n.children);
      }
    }
  };
  walk(tree ?? []);

  const plantByName = new Map((plants ?? []).map((p) => [p.name, p]));
  const unitByKey = new Map<string, WorkbenchApi.UnitRow>();
  for (const u of units ?? []) {
    unitByKey.set(u.id !== null && u.id !== undefined ? `id:${u.id}` : `name:${u.name}`, u);
  }
  const matchUnit = (n: CockpitApi.NodeTreeNode): undefined | WorkbenchApi.UnitRow =>
    unitByKey.get(n.id !== null && n.id !== undefined ? `id:${n.id}` : `name:${n.name}`) ??
    unitByKey.get(`name:${n.name}`);

  const out: ScoreRankPlant[] = areaNodes.map((area) => {
    const unitRows: ScoreRankUnit[] = area.units.map((child) => {
      const row = matchUnit(child);
      return {
        key: `${child.nodeId}`,
        name: child.name,
        nodeId: child.nodeId,
        rank: null,
        score: row?.score ?? null,
      };
    });
    const sortedUnits = byScoreDesc(unitRows);
    assignRank(sortedUnits);
    const plantRow = plantByName.get(area.name);
    return {
      name: area.name,
      nodeId: area.nodeId,
      rank: null,
      score: plantRow?.score ?? null,
      units: sortedUnits,
    };
  });

  const sortedPlants = byScoreDesc(out);
  assignRank(sortedPlants);
  return sortedPlants;
}
