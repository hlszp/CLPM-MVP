/**
 * Mock 数据：回路台账（DDS loops，用户创建实体）
 *
 * 17 个回路，分布在 3 个装置的 5 个回路组中。
 * 部分回路缺失必填槽位（用于演示 Tag 关联校验状态）。
 */

import type { Loop, LoopTagMapping } from './types';
import type { PVQuality } from '../components/PVQualityBadge';
import type { ComputeStatus, ControlMode } from '../components/StatusBadge';

/** 构造 Tag 关联映射 */
function mapping(pv: string, sp: string, op: string, mode: string, pidp?: string, pidi?: string, pidd?: string): LoopTagMapping {
  const m: LoopTagMapping = { PV: pv, SP: sp, OP: op, MODE: mode };
  if (pidp) m.PID_P = pidp;
  if (pidi) m.PID_I = pidi;
  if (pidd) m.PID_D = pidd;
  return m;
}

/** 检查必填槽位完整性 */
function isComplete(m: LoopTagMapping): boolean {
  return !!(m.PV && m.SP && m.OP && m.MODE);
}

interface LoopSeed {
  loopId: string;
  loopName: string;
  loopCode: string;
  nodeId: string;
  nodeName: string;
  description: string;
  mapping: LoopTagMapping;
  controlMode: ControlMode;
  pvValue: number;
  pvQuality: PVQuality;
  spValue: number;
  opValue: number;
  score: number | null;
  computeStatus: ComputeStatus;
}

const seeds: LoopSeed[] = [
  // 加氢精制 - 反应系统
  { loopId: 'L001', loopName: 'R-101 反应器入口温度', loopCode: 'HDS-RX-TIC-101', nodeId: 'G001', nodeName: '反应系统', description: '反应器入口温度控制，影响催化剂寿命', mapping: mapping('T-HDS-001-PV', 'T-HDS-001-SP', 'T-HDS-001-OP', 'T-HDS-001-MODE', 'T-HDS-001-PID_P', 'T-HDS-001-PID_I', 'T-HDS-001-PID_D'), controlMode: 'Auto', pvValue: 320.5, pvQuality: 'Good', spValue: 325.0, opValue: 58.2, score: 85, computeStatus: 'SUCCESS' },
  { loopId: 'L002', loopName: 'R-101 反应器床层温度', loopCode: 'HDS-RX-TIC-102', nodeId: 'G001', nodeName: '反应系统', description: '床层温度多点加权控制', mapping: mapping('T-HDS-002-PV', 'T-HDS-002-SP', 'T-HDS-002-OP', 'T-HDS-002-MODE', 'T-HDS-002-PID_P', 'T-HDS-002-PID_I'), controlMode: 'Auto', pvValue: 345.2, pvQuality: 'Good', spValue: 350.0, opValue: 62.5, score: 72, computeStatus: 'SUCCESS' },
  { loopId: 'L003', loopName: 'F-101 加热炉出口温度', loopCode: 'HDS-RX-TIC-103', nodeId: 'G001', nodeName: '反应系统', description: '加热炉出口温度，存在振荡', mapping: mapping('T-HDS-003-PV', 'T-HDS-003-SP', 'T-HDS-003-OP', 'T-HDS-003-MODE', 'T-HDS-003-PID_P', 'T-HDS-003-PID_I', 'T-HDS-003-PID_D'), controlMode: 'Auto', pvValue: 310.8, pvQuality: 'Uncertain', spValue: 315.0, opValue: 45.0, score: 45, computeStatus: 'PARTIAL' },
  { loopId: 'L004', loopName: 'C-101 塔顶压力', loopCode: 'HDS-RX-PIC-104', nodeId: 'G001', nodeName: '反应系统', description: '塔顶压力控制，PV 质量码异常', mapping: mapping('T-HDS-004-PV', 'T-HDS-004-SP', 'T-HDS-004-OP', 'T-HDS-004-MODE'), controlMode: 'Manual', pvValue: 0, pvQuality: 'Bad', spValue: 1.2, opValue: 50.0, score: null, computeStatus: 'INCONCLUSIVE' },

  // 加氢精制 - 分馏系统
  { loopId: 'L005', loopName: 'C-102 塔顶温度', loopCode: 'HDS-FR-TIC-105', nodeId: 'G002', nodeName: '分馏系统', description: '分馏塔顶温度控制', mapping: mapping('T-HDS-005-PV', 'T-HDS-005-SP', 'T-HDS-005-OP', 'T-HDS-005-MODE', 'T-HDS-005-PID_P', 'T-HDS-005-PID_I', 'T-HDS-005-PID_D'), controlMode: 'Auto', pvValue: 145.3, pvQuality: 'Good', spValue: 148.0, opValue: 35.2, score: 88, computeStatus: 'SUCCESS' },
  { loopId: 'L006', loopName: 'C-102 塔底液位', loopCode: 'HDS-FR-LIC-106', nodeId: 'G002', nodeName: '分馏系统', description: '塔底液位控制', mapping: mapping('T-HDS-006-PV', 'T-HDS-006-SP', 'T-HDS-006-OP', 'T-HDS-006-MODE', 'T-HDS-006-PID_P', 'T-HDS-006-PID_I'), controlMode: 'Auto', pvValue: 62.5, pvQuality: 'Good', spValue: 65.0, opValue: 48.0, score: 78, computeStatus: 'SUCCESS' },
  { loopId: 'L007', loopName: 'C-102 回流量', loopCode: 'HDS-FR-FIC-107', nodeId: 'G002', nodeName: '分馏系统', description: '回流量控制，参数过保守', mapping: mapping('T-HDS-007-PV', 'T-HDS-007-SP', 'T-HDS-007-OP', 'T-HDS-007-MODE', 'T-HDS-007-PID_P', 'T-HDS-007-PID_I', 'T-HDS-007-PID_D'), controlMode: 'Cascade', pvValue: 85.0, pvQuality: 'Good', spValue: 85.0, opValue: 52.3, score: 68, computeStatus: 'SUCCESS' },

  // 加氢裂化 - 反应系统
  { loopId: 'L008', loopName: 'R-201 反应器入口温度', loopCode: 'HDC-RX-TIC-201', nodeId: 'G003', nodeName: '反应系统', description: '反应器入口温度控制', mapping: mapping('T-HDC-008-PV', 'T-HDC-008-SP', 'T-HDC-008-OP', 'T-HDC-008-MODE', 'T-HDC-008-PID_P', 'T-HDC-008-PID_I', 'T-HDC-008-PID_D'), controlMode: 'Auto', pvValue: 380.5, pvQuality: 'Good', spValue: 385.0, opValue: 65.0, score: 82, computeStatus: 'SUCCESS' },
  { loopId: 'L009', loopName: 'R-201 反应器床层温度', loopCode: 'HDC-RX-TIC-202', nodeId: 'G003', nodeName: '反应系统', description: '床层温度控制，存在粘滞阀', mapping: mapping('T-HDC-009-PV', 'T-HDC-009-SP', 'T-HDC-009-OP', 'T-HDC-009-MODE', 'T-HDC-009-PID_P', 'T-HDC-009-PID_I'), controlMode: 'Auto', pvValue: 410.2, pvQuality: 'Good', spValue: 415.0, opValue: 70.5, score: 52, computeStatus: 'SUCCESS' },
  { loopId: 'L010', loopName: 'F-201 加热炉出口温度', loopCode: 'HDC-RX-TIC-203', nodeId: 'G003', nodeName: '反应系统', description: '加热炉出口温度控制', mapping: mapping('T-HDC-010-PV', 'T-HDC-010-SP', 'T-HDC-010-OP', 'T-HDC-010-MODE', 'T-HDC-010-PID_P', 'T-HDC-010-PID_I', 'T-HDC-010-PID_D'), controlMode: 'Auto', pvValue: 365.0, pvQuality: 'Good', spValue: 370.0, opValue: 55.0, score: 75, computeStatus: 'SUCCESS' },
  { loopId: 'L011', loopName: 'C-201 塔顶压力', loopCode: 'HDC-RX-PIC-204', nodeId: 'G003', nodeName: '反应系统', description: '塔顶压力控制，PV 质量不确定', mapping: mapping('T-HDC-011-PV', 'T-HDC-011-SP', 'T-HDC-011-OP', 'T-HDC-011-MODE'), controlMode: 'Auto', pvValue: 1.5, pvQuality: 'Uncertain', spValue: 1.5, opValue: 48.0, score: 58, computeStatus: 'PARTIAL' },

  // 加氢裂化 - 分馏系统
  { loopId: 'L012', loopName: 'C-202 塔顶温度', loopCode: 'HDC-FR-TIC-205', nodeId: 'G004', nodeName: '分馏系统', description: '分馏塔顶温度控制', mapping: mapping('T-HDC-012-PV', 'T-HDC-012-SP', 'T-HDC-012-OP', 'T-HDC-012-MODE', 'T-HDC-012-PID_P', 'T-HDC-012-PID_I', 'T-HDC-012-PID_D'), controlMode: 'Auto', pvValue: 155.0, pvQuality: 'Good', spValue: 158.0, opValue: 38.0, score: 90, computeStatus: 'SUCCESS' },
  { loopId: 'L013', loopName: 'C-202 塔底液位', loopCode: 'HDC-FR-LIC-206', nodeId: 'G004', nodeName: '分馏系统', description: '塔底液位控制', mapping: mapping('T-HDC-013-PV', 'T-HDC-013-SP', 'T-HDC-013-OP', 'T-HDC-013-MODE', 'T-HDC-013-PID_P', 'T-HDC-013-PID_I'), controlMode: 'Auto', pvValue: 58.0, pvQuality: 'Good', spValue: 60.0, opValue: 45.0, score: 80, computeStatus: 'SUCCESS' },
  { loopId: 'L014', loopName: 'C-202 回流量', loopCode: 'HDC-FR-FIC-207', nodeId: 'G004', nodeName: '分馏系统', description: '回流量控制，参数过激', mapping: mapping('T-HDC-014-PV', 'T-HDC-014-SP', 'T-HDC-014-OP', 'T-HDC-014-MODE', 'T-HDC-014-PID_P', 'T-HDC-014-PID_I', 'T-HDC-014-PID_D'), controlMode: 'Cascade', pvValue: 92.0, pvQuality: 'Good', spValue: 92.0, opValue: 58.0, score: 42, computeStatus: 'SUCCESS' },

  // S Zorb - 吸附系统
  { loopId: 'L015', loopName: 'R-301 反应器温度', loopCode: 'SZB-AD-TIC-301', nodeId: 'G005', nodeName: '吸附系统', description: '反应器温度控制', mapping: mapping('T-SZB-015-PV', 'T-SZB-015-SP', 'T-SZB-015-OP', 'T-SZB-015-MODE', 'T-SZB-015-PID_P', 'T-SZB-015-PID_I', 'T-SZB-015-PID_D'), controlMode: 'Auto', pvValue: 420.0, pvQuality: 'Good', spValue: 425.0, opValue: 62.0, score: 86, computeStatus: 'SUCCESS' },
  { loopId: 'L016', loopName: 'C-301 塔顶压力', loopCode: 'SZB-AD-PIC-302', nodeId: 'G005', nodeName: '吸附系统', description: '塔顶压力控制', mapping: mapping('T-SZB-016-PV', 'T-SZB-016-SP', 'T-SZB-016-OP', 'T-SZB-016-MODE', 'T-SZB-016-PID_P', 'T-SZB-016-PID_I'), controlMode: 'Auto', pvValue: 0.8, pvQuality: 'Good', spValue: 0.8, opValue: 40.0, score: 76, computeStatus: 'SUCCESS' },
  { loopId: 'L017', loopName: 'C-301 塔底液位', loopCode: 'SZB-AD-LIC-303', nodeId: 'G005', nodeName: '吸附系统', description: '塔底液位控制，缺失 PID 参数', mapping: mapping('T-SZB-017-PV', 'T-SZB-017-SP', 'T-SZB-017-OP', 'T-SZB-017-MODE'), controlMode: 'Auto', pvValue: 55.0, pvQuality: 'Good', spValue: 55.0, opValue: 42.0, score: 70, computeStatus: 'SUCCESS' },
];

export const loops: Loop[] = seeds.map((s) => {
  const { mapping, ...rest } = s;
  return {
    ...rest,
    tagMapping: mapping,
    mappingComplete: isComplete(mapping),
    lastScoredAt: '2026-06-21 10:00:00',
    createdAt: '2026-05-15 09:00:00',
    updatedAt: '2026-06-20 14:30:00',
  };
});

/** 按 loopId 查询 */
export function findLoop(loopId: string): Loop | undefined {
  return loops.find((l) => l.loopId === loopId);
}

/** 按 nodeId 查询回路列表 */
export function findLoopsByNode(nodeId: string): Loop[] {
  return loops.filter((l) => l.nodeId === nodeId);
}

/** 按评分升序排列（低效回路排行用） */
export function getLoopsByScoreAsc(): Loop[] {
  return [...loops].sort((a, b) => {
    if (a.score === null) return 1;
    if (b.score === null) return -1;
    return a.score - b.score;
  });
}

/** 统计摘要（工作台 KPI 卡片用） */
export function getLoopStats() {
  const total = loops.length;
  const success = loops.filter((l) => l.computeStatus === 'SUCCESS').length;
  const partial = loops.filter((l) => l.computeStatus === 'PARTIAL').length;
  const inconclusive = loops.filter((l) => l.computeStatus === 'INCONCLUSIVE').length;
  const lowPerf = loops.filter((l) => l.score !== null && l.score < 60).length;
  const avgScore = loops.reduce((sum, l) => sum + (l.score ?? 0), 0) / total;
  const pvBad = loops.filter((l) => l.pvQuality === 'Bad').length;
  const pvUncertain = loops.filter((l) => l.pvQuality === 'Uncertain').length;
  const manualMode = loops.filter((l) => l.controlMode === 'Manual').length;
  return { total, success, partial, inconclusive, lowPerf, avgScore: Math.round(avgScore * 10) / 10, pvBad, pvUncertain, manualMode };
}
