import type { CockpitApi } from '../api/cockpit';
import type { WorkbenchApi } from '../api/workbench';

/**
 * 驾驶舱总览 · 综合评分排名树组装测试（2026-10-10 修订）
 *
 * buildScoreRankTree：node-tree（AREA→UNIT 层级）× workbench overview
 * （plants/units 预计算行）合并的两级排名。覆盖：
 * - 装置/单元均按评分降序，单元排名为装置内序（并列同名次）
 * - source_node_id 主键匹配 + 名称回退（precalc 行 id 为 null 的旧窗兼容）
 * - 无评分行排末尾且 rank=null（不编造名次）
 * - node-tree 无 AREA 时输出空（诚实空态，不造树）
 */
import { describe, expect, it } from 'vitest';

import { buildScoreRankTree } from '../views/cockpit/utils/score-rank';

function makeTree(): CockpitApi.NodeTreeNode[] {
  return [
    {
      id: 100,
      name: '全厂',
      nodeId: 'factory-1',
      type: 'FACTORY',
      loopCount: 10,
      children: [
        {
          id: 1,
          name: '一联合',
          nodeId: 'area-1',
          type: 'AREA',
          loopCount: 6,
          children: [
            { id: 11, name: '常减压', nodeId: 'unit-11', type: 'UNIT', loopCount: 4 },
            { id: 12, name: '催化', nodeId: 'unit-12', type: 'UNIT', loopCount: 2 },
          ],
        },
        {
          id: 2,
          name: '二联合',
          nodeId: 'area-2',
          type: 'AREA',
          loopCount: 4,
          children: [
            { id: 21, name: '重整', nodeId: 'unit-21', type: 'UNIT', loopCount: 4 },
          ],
        },
      ],
    },
  ];
}

function makeOverview() {
  const plants: WorkbenchApi.PlantRow[] = [
    {
      alarm_count: 0,
      id: 1,
      lose_factors: [],
      loop_count: 6,
      name: '一联合',
      overdue_tasks: 0,
      rank: 2,
      score: 78.5,
      sparkline: [],
      status: 'GOOD',
    },
    {
      alarm_count: 0,
      id: 2,
      lose_factors: [],
      loop_count: 4,
      name: '二联合',
      overdue_tasks: 0,
      rank: 1,
      score: 88.2,
      sparkline: [],
      status: 'EXCELLENT',
    },
  ];
  const units: WorkbenchApi.UnitRow[] = [
    {
      id: 11,
      metrics: { auto_mode_rate: 0.9 },
      name: '常减压',
      score: 72,
      status: 'FAIR',
    },
    {
      id: 12,
      metrics: { auto_mode_rate: 0.8 },
      name: '催化',
      score: 85,
      status: 'GOOD',
    },
    // precalc 行 id=null：按名称回退匹配 unit-21
    {
      id: null,
      metrics: {},
      name: '重整',
      score: 88.2,
      status: 'EXCELLENT',
    },
  ];
  return { plants, units };
}

describe('cockpit/buildScoreRankTree 综合评分排名树', () => {
  it('装置按评分降序，单元为装置内排名，nodeId 供联动使用', () => {
    const { plants, units } = makeOverview();
    const rows = buildScoreRankTree(makeTree(), plants, units);

    expect(rows).toHaveLength(2);
    expect(rows[0]!.name).toBe('二联合');
    expect(rows[0]!.nodeId).toBe('area-2');
    expect(rows[0]!.rank).toBe(1);
    expect(rows[0]!.score).toBe(88.2);
    expect(rows[1]!.name).toBe('一联合');
    expect(rows[1]!.rank).toBe(2);

    // 一联合内：催化 85 > 常减压 72
    expect(rows[1]!.units.map((u) => u.name)).toEqual(['催化', '常减压']);
    expect(rows[1]!.units[0]!.rank).toBe(1);
    expect(rows[1]!.units[1]!.rank).toBe(2);
    expect(rows[1]!.units[0]!.nodeId).toBe('unit-12');
  });

  it('precalc 行 id=null 时按名称回退匹配单元（旧窗口行兼容）', () => {
    const { plants, units } = makeOverview();
    const rows = buildScoreRankTree(makeTree(), plants, units);
    const unit21 = rows[0]!.units.find((u) => u.nodeId === 'unit-21');
    expect(unit21?.name).toBe('重整');
    expect(unit21?.score).toBe(88.2);
  });

  it('并列评分同名次；无评分行排末尾且 rank=null', () => {
    const tree = makeTree();
    const plants: WorkbenchApi.PlantRow[] = [
      {
        alarm_count: 0,
        id: 1,
        lose_factors: [],
        loop_count: 6,
        name: '一联合',
        overdue_tasks: 0,
        rank: 1,
        score: 80,
        sparkline: [],
        status: 'GOOD',
      },
      {
        alarm_count: 0,
        id: 2,
        lose_factors: [],
        loop_count: 4,
        name: '二联合',
        overdue_tasks: 0,
        rank: 1,
        score: 80,
        sparkline: [],
        status: 'GOOD',
      },
    ];
    const rows = buildScoreRankTree(tree, plants, []);
    expect(rows.map((r) => r.score)).toEqual([80, 80]);
    expect(rows[0]!.rank).toBe(1);
    expect(rows[1]!.rank).toBe(1);

    // 二联合的单元无预计算行 → score=null，rank=null
    const u = rows[1]!.units[0]!;
    expect(u.score).toBeNull();
    expect(u.rank).toBeNull();
  });

  it('装置缺失预计算行时 score=null 排末尾；无 AREA 层输出空数组', () => {
    const rows = buildScoreRankTree(makeTree(), [], []);
    // 两装置均无评分：保持稳定序（按名称），rank=null
    expect(rows).toHaveLength(2);
    expect(rows.every((r) => r.rank === null && r.score === null)).toBe(true);

    const emptyTree: CockpitApi.NodeTreeNode[] = [
      { id: 100, name: '全厂', nodeId: 'f', type: 'FACTORY', loopCount: 0 },
    ];
    expect(buildScoreRankTree(emptyTree, [], [])).toEqual([]);
  });
});
