/**
 * Mock 数据：AAS 同步 Tag 列表（DDS tag_registry）
 *
 * 每个回路对应 7 个 OPC Tag（PV/SP/OP/MODE/PID_P/PID_I/PID_D）
 * Tag 质量码分布：大部分 Good，少量 Bad/Uncertain（用于演示 PV 质量码渲染）
 */

import type { AasTag } from './types';
import type { PVQuality } from '../components/PVQualityBadge';

/** Tag 命名规范：{装置码}-{回路号}-{槽位} */
function makeTagId(unitCode: string, loopNum: number, slot: string): string {
  return `T-${unitCode}-${String(loopNum).padStart(3, '0')}-${slot}`;
}

/** 生成单个回路的 7 个 Tag */
function makeLoopTags(
  unitCode: string,
  loopNum: number,
  loopName: string,
  linkedLoopId: string,
  pvQuality: PVQuality = 'Good',
): AasTag[] {
  const slots: Array<{ slot: string; desc: string; unit: string; value: number | string }> = [
    { slot: 'PV', desc: `${loopName} 过程测量值`, unit: '°C', value: 320.5 },
    { slot: 'SP', desc: `${loopName} 设定值`, unit: '°C', value: 325.0 },
    { slot: 'OP', desc: `${loopName} 输出值`, unit: '%', value: 58.2 },
    { slot: 'MODE', desc: `${loopName} 控制模式`, unit: '', value: 'Auto' },
    { slot: 'PID_P', desc: `${loopName} 比例参数`, unit: '', value: 1.2 },
    { slot: 'PID_I', desc: `${loopName} 积分参数`, unit: 's', value: 30 },
    { slot: 'PID_D', desc: `${loopName} 微分参数`, unit: 's', value: 0 },
  ];
  return slots.map((s) => ({
    tagId: makeTagId(unitCode, loopNum, s.slot),
    tagName: makeTagId(unitCode, loopNum, s.slot),
    description: s.desc,
    unit: s.unit,
    currentValue: s.value,
    quality: s.slot === 'PV' ? pvQuality : 'Good',
    linkedLoopId,
    linkedLoopName: loopName,
    lastSyncAt: '2026-06-21 10:30:00',
  }));
}

/** 未关联的游离 Tag（用于演示 Tag 关联管理页面） */
function makeUnlinkedTag(unitCode: string, loopNum: number, slot: string, desc: string): AasTag {
  return {
    tagId: makeTagId(unitCode, loopNum, slot),
    tagName: makeTagId(unitCode, loopNum, slot),
    description: desc,
    unit: '°C',
    currentValue: 0,
    quality: 'Good',
    linkedLoopId: null,
    linkedLoopName: null,
    lastSyncAt: '2026-06-21 10:30:00',
  };
}

export const aasTags: AasTag[] = [
  // 加氢精制装置 - 反应系统（4 个回路，L001-L004）
  ...makeLoopTags('HDS', 1, 'R-101 反应器入口温度', 'L001'),
  ...makeLoopTags('HDS', 2, 'R-101 反应器床层温度', 'L002', 'Good'),
  ...makeLoopTags('HDS', 3, 'F-101 加热炉出口温度', 'L003', 'Uncertain'),
  ...makeLoopTags('HDS', 4, 'C-101 塔顶压力', 'L004', 'Bad'),

  // 加氢精制装置 - 分馏系统（3 个回路，L005-L007）
  ...makeLoopTags('HDS', 5, 'C-102 塔顶温度', 'L005'),
  ...makeLoopTags('HDS', 6, 'C-102 塔底液位', 'L006'),
  ...makeLoopTags('HDS', 7, 'C-102 回流量', 'L007'),

  // 加氢裂化装置 - 反应系统（4 个回路，L008-L011）
  ...makeLoopTags('HDC', 8, 'R-201 反应器入口温度', 'L008'),
  ...makeLoopTags('HDC', 9, 'R-201 反应器床层温度', 'L009', 'Good'),
  ...makeLoopTags('HDC', 10, 'F-201 加热炉出口温度', 'L010'),
  ...makeLoopTags('HDC', 11, 'C-201 塔顶压力', 'L011', 'Uncertain'),

  // 加氢裂化装置 - 分馏系统（3 个回路，L012-L014）
  ...makeLoopTags('HDC', 12, 'C-202 塔顶温度', 'L012'),
  ...makeLoopTags('HDC', 13, 'C-202 塔底液位', 'L013'),
  ...makeLoopTags('HDC', 14, 'C-202 回流量', 'L014'),

  // S Zorb 装置 - 吸附系统（3 个回路，L015-L017）
  ...makeLoopTags('SZB', 15, 'R-301 反应器温度', 'L015'),
  ...makeLoopTags('SZB', 16, 'C-301 塔顶压力', 'L016'),
  ...makeLoopTags('SZB', 17, 'C-301 塔底液位', 'L017'),

  // 未关联的游离 Tag（用于 Tag 关联管理页面演示）
  makeUnlinkedTag('HDS', 18, 'PV', '备用温度测量点（未关联）'),
  makeUnlinkedTag('HDS', 18, 'SP', '备用设定值（未关联）'),
  makeUnlinkedTag('HDC', 19, 'PV', '新增压力测量点（未关联）'),
  makeUnlinkedTag('SZB', 20, 'OP', '待调试输出点（未关联）'),
];

/** 按 tagId 查询 */
export function findTag(tagId: string): AasTag | undefined {
  return aasTags.find((t) => t.tagId === tagId);
}

/** 按回路 ID 查询关联的 7 个 Tag */
export function findTagsByLoop(loopId: string): AasTag[] {
  return aasTags.filter((t) => t.linkedLoopId === loopId);
}
