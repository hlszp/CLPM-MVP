import type { CockpitApi } from '../api/cockpit';
import type { WorkbenchApi } from '../api/workbench';

/**
 * 性能页 · 装置×单元树形排名组装测试（2026-10-12 R2 改造）
 *
 * buildAssessmentRankTree：node-tree × assessment 两视图行合并。
 * 覆盖：
 * - 装置按后端 rank 排序；单元按装置内评分升序（风险优先）重编装置内序
 * - source_node_id 主键匹配 + 名称回退；入参 rank 不被污染（复制改写）
 * - node-tree 缺失 → parent_name 兜底归组；无归属单元挂"未挂载单元"尾组
 * - 装置行未入树 → 平铺保留展示
 */
import { describe, expect, it } from 'vitest';

import { buildAssessmentRankTree } from '../views/cockpit/utils/assessment-rank-tree';

function makeTree(): CockpitApi.NodeTreeNode[] {
  return [
    {
      id: 900,
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
          children: [{ id: 21, name: '重整', nodeId: 'unit-21', type: 'UNIT', loopCount: 4 }],
        },
      ],
    },
  ];
}

function makeRow(
  id: null | number,
  name: string,
  score: null | number,
  rank: number,
  extra: Partial<WorkbenchApi.AssessmentRankRow> = {},
): WorkbenchApi.AssessmentRankRow {
  return {
    alarm_count: 0,
    delta: null,
    id,
    join: null,
    loop_count: 3,
    lose_factors: [],
    name,
    overdue_tasks: 0,
    parent_name: null,
    rank,
    score,
    sparkline: [],
    ...extra,
  };
}

describe('buildAssessmentRankTree', () => {
  it('组树后装置组按后端 rank 升序输出（树序与 rank 序相反时仍按 rank）', () => {
    // 树序：一联合(id=1)→二联合(id=2)；rank：二联合=1（低分高风险）、一联合=2
    const plants = [
      makeRow(1, '一联合', 80, 2),
      makeRow(2, '二联合', 65, 1),
    ];
    const units = [
      makeRow(11, '常减压', 90, 3, { parent_name: '一联合' }),
      makeRow(12, '催化', 55, 1, { parent_name: '一联合' }),
      makeRow(21, '重整', 70, 2, { parent_name: '二联合' }),
    ];
    const out = buildAssessmentRankTree(makeTree(), plants, units);
    // D1（验收 REWORK）：装置组序=后端 rank 序（评分升序·风险优先），非树序
    expect(out.map((g) => g.plant.name)).toEqual(['二联合', '一联合']);
    // 装置内单元按评分升序（风险优先），rank 改写为装置内序
    expect(out[0]!.units.map((u) => u.name)).toEqual(['重整']);
    expect(out[1]!.units.map((u) => u.name)).toEqual(['催化', '常减压']);
    expect(out[1]!.units.map((u) => u.rank)).toEqual([1, 2]);
  });

  it('V1 反例：A rank=2 / B rank=1 且树序 A→B → 输出 [B, A]', () => {
    const tree: CockpitApi.NodeTreeNode[] = [
      {
        id: 1,
        name: 'A装置',
        nodeId: 'area-a',
        type: 'AREA',
        loopCount: 4,
        children: [
          { id: 11, name: 'A-1', nodeId: 'unit-a1', type: 'UNIT', loopCount: 4 },
        ],
      },
      {
        id: 2,
        name: 'B装置',
        nodeId: 'area-b',
        type: 'AREA',
        loopCount: 4,
        children: [
          { id: 21, name: 'B-1', nodeId: 'unit-b1', type: 'UNIT', loopCount: 4 },
        ],
      },
    ];
    const plants = [makeRow(1, 'A装置', 80, 2), makeRow(2, 'B装置', 70, 1)];
    const out = buildAssessmentRankTree(tree, plants, [
      makeRow(11, 'A-1', 80, 1),
      makeRow(21, 'B-1', 70, 2),
    ]);
    expect(out.map((g) => g.plant.name)).toEqual(['B装置', 'A装置']);
  });

  it('V5：rank=null 装置垫底（不抢占首行）', () => {
    const plants = [
      makeRow(1, '无分行', null, 3),
      makeRow(2, '有分行', 70, 1),
    ];
    const out = buildAssessmentRankTree(makeTree(), plants, []);
    expect(out.map((g) => g.plant.name)).toEqual(['有分行', '无分行']);
  });

  it('入参单元行 rank 不被污染（复制改写）', () => {
    const units = [
      makeRow(11, '常减压', 90, 9),
      makeRow(12, '催化', 55, 1),
    ];
    buildAssessmentRankTree(makeTree(), [makeRow(1, '一联合', 80, 1)], units);
    expect(units[0]!.rank).toBe(9);
    expect(units[1]!.rank).toBe(1);
  });

  it('名称回退：行 id 为 null 时按节点名匹配', () => {
    const plants = [makeRow(null, '一联合', 80, 1)];
    const units = [makeRow(null, '常减压', 90, 1)];
    const out = buildAssessmentRankTree(makeTree(), plants, units);
    expect(out).toHaveLength(1);
    expect(out[0]!.plant.name).toBe('一联合');
    expect(out[0]!.units.map((u) => u.name)).toEqual(['常减压']);
  });

  it('node-tree 缺失：单元按 parent_name 兜底归组，无归属单元挂尾组', () => {
    const plants = [makeRow(1, '一联合', 80, 1)];
    const units = [
      makeRow(11, '常减压', 90, 1, { parent_name: '一联合' }),
      makeRow(99, '孤立单元', 70, 2, { parent_name: '未知装置' }),
    ];
    const out = buildAssessmentRankTree(null, plants, units);
    expect(out).toHaveLength(2);
    expect(out[0]!.units.map((u) => u.name)).toEqual(['常减压']);
    expect(out[1]!.plant.name).toBe('未挂载单元');
    expect(out[1]!.units.map((u) => u.name)).toEqual(['孤立单元']);
  });

  it('装置行未入树：平铺保留展示', () => {
    const plants = [
      makeRow(1, '一联合', 80, 1),
      makeRow(77, '树外装置', 60, 2),
    ];
    const out = buildAssessmentRankTree(makeTree(), plants, []);
    expect(out.map((g) => g.plant.name)).toEqual(['一联合', '树外装置']);
    expect(out[1]!.units).toEqual([]);
  });

  it('全空输入：空输出（诚实空态）', () => {
    expect(buildAssessmentRankTree(null, [], [])).toEqual([]);
  });
});
