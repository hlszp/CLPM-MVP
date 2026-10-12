/**
 * 性能页 · 装置×单元树形排名组装（纯函数，2026-10-12 R2 改造）
 *
 * 数据合并：/cockpit/node-tree（AREA→UNIT 层级）× /workbench/assessment
 * 两个视图（view=plant 装置行 + view=unit 单元行）。行以 source_node_id
 * （int `id`）为主键匹配 node-tree 节点，缺失回退按名称匹配。
 *
 * 排序口径：装置组按后端 rank 升序输出（综合评分升序 · 风险优先，与改造
 * 前原表一致；D1 REWORK——组树来自 node-tree 序，组装完成后统一按 rank
 * 排序，rank 缺失垫底）；装置内单元同口径升序、装置内序（与装置全厂排名
 * 区分）；无评分行排末尾。
 *
 * 容错：node-tree 缺失/为空时按单元行 parent_name 兜底归组（仍无归属的
 * 单元挂到"未挂载"尾组，不静默丢弃）；装置行无匹配也保留展示。
 */
import type { CockpitApi } from '#/api/cockpit';
import type { WorkbenchApi } from '#/api/workbench';

/** 树形排名的装置组（装置行 + 装置内单元行） */
export interface AssessmentPlantGroup {
  /** 装置行（view=plant 的原始行，rank=全厂排名） */
  plant: WorkbenchApi.AssessmentRankRow;
  /** 装置内单元行（rank 已改写为装置内序） */
  units: WorkbenchApi.AssessmentRankRow[];
}

/** 评分升序（风险优先，与后端 shape_ranking_plant 口径一致）；无评分垫底 */
function byScoreAsc(a: WorkbenchApi.AssessmentRankRow, b: WorkbenchApi.AssessmentRankRow) {
  const sa = a.score;
  const sb = b.score;
  if (sa === null && sb === null) return a.name.localeCompare(b.name, 'zh');
  if (sa === null) return 1;
  if (sb === null) return -1;
  if (sa !== sb) return sa - sb;
  return a.name.localeCompare(b.name, 'zh');
}

function matchRow(
  node: CockpitApi.NodeTreeNode,
  rows: WorkbenchApi.AssessmentRankRow[],
  used: Set<WorkbenchApi.AssessmentRankRow>,
): undefined | WorkbenchApi.AssessmentRankRow {
  if (node.id !== null && node.id !== undefined) {
    const byId = rows.find((r) => r.id === node.id && !used.has(r));
    if (byId) {
      used.add(byId);
      return byId;
    }
  }
  const byName = rows.find((r) => r.name === node.name && !used.has(r));
  if (byName) {
    used.add(byName);
  }
  return byName;
}

/**
 * 组装装置×单元树形排名。
 *
 * @param tree /cockpit/node-tree 结果（可为 null=接口失败，走 parent_name 兜底）
 * @param plants view=plant 装置行
 * @param units view=unit 单元行
 */
export function buildAssessmentRankTree(
  tree: CockpitApi.NodeTreeNode[] | null | undefined,
  plants: null | undefined | WorkbenchApi.AssessmentRankRow[],
  units: null | undefined | WorkbenchApi.AssessmentRankRow[],
): AssessmentPlantGroup[] {
  const plantRows = [...(plants ?? [])].toSorted(
    (a, b) => (a.rank ?? 0) - (b.rank ?? 0),
  );
  const unitRows = units ?? [];
  const usedUnits = new Set<WorkbenchApi.AssessmentRankRow>();
  const groups: AssessmentPlantGroup[] = [];
  const usedPlants = new Set<WorkbenchApi.AssessmentRankRow>();

  const areaNodes: CockpitApi.NodeTreeNode[] = [];
  const walk = (nodes: CockpitApi.NodeTreeNode[]) => {
    for (const n of nodes) {
      if (n.type === 'AREA') {
        areaNodes.push(n);
      } else if (n.children?.length) {
        walk(n.children);
      }
    }
  };
  walk(tree ?? []);

  const assignUnitRanks = (rows: WorkbenchApi.AssessmentRankRow[]) => {
    // 复制行再改写 rank（装置内序），不污染入参（全局 rank 不再展示但保持只读）
    return [...rows].toSorted(byScoreAsc).map((r, i) => ({ ...r, rank: i + 1 }));
  };

  for (const area of areaNodes) {
    const plant = matchRow(area, plantRows, usedPlants);
    if (!plant) continue;
    const unitChildren = (area.children ?? []).filter((c) => c.type === 'UNIT');
    const matched = unitChildren
      .map((c) => matchRow(c, unitRows, usedUnits))
      .filter((r): r is WorkbenchApi.AssessmentRankRow => r !== undefined);
    groups.push({ plant, units: assignUnitRanks(matched) });
  }

  // 兜底 1：node-tree 缺失或未覆盖的装置行 → 直接平铺（保留展示）
  for (const p of plantRows) {
    if (!usedPlants.has(p)) {
      usedPlants.add(p);
      groups.push({ plant: p, units: [] });
    }
  }

  // 兜底 2：未入树的单元行 → 按 parent_name 归入同名装置组
  const orphans = unitRows.filter((u) => !usedUnits.has(u));
  for (const u of orphans) {
    const g = groups.find((x) => x.plant.name === u.parent_name);
    if (g) {
      usedUnits.add(u);
      g.units.push(u);
    }
  }
  for (const g of groups) {
    if (g.units.length > 0) g.units = assignUnitRanks(g.units);
  }

  // D1（验收 REWORK）：装置组按后端 rank 升序（评分升序·风险优先，与表头
  // 副标题/原表行为一致）——组树按 node-tree 序（sort_order→name）产生，
  // 不排序会导致红徽章 #1 不在首行；rank 缺失垫底（并列按名称稳定序）。
  // "未挂载单元"伪装置组在排序后追加，恒为尾组。
  const ranked = groups.toSorted((a, b) => {
    const ra = a.plant.rank;
    const rb = b.plant.rank;
    if (ra === null && rb === null) {
      return a.plant.name.localeCompare(b.plant.name, 'zh');
    }
    if (ra === null) return 1;
    if (rb === null) return -1;
    return ra - rb;
  });

  // 兜底 3：仍无归属的单元 → "未挂载"尾组（不静默丢弃）
  const rest = unitRows.filter((u) => !usedUnits.has(u));
  if (rest.length > 0) {
    ranked.push({
      plant: {
        alarm_count: 0,
        delta: null,
        id: null,
        join: null,
        loop_count: rest.reduce((s, u) => s + u.loop_count, 0),
        lose_factors: [],
        name: '未挂载单元',
        overdue_tasks: 0,
        parent_name: null,
        rank: ranked.length + 1,
        score: null,
        sparkline: [],
      },
      units: assignUnitRanks(rest),
    });
  }

  return ranked;
}
