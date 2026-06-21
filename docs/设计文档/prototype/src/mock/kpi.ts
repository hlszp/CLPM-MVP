/**
 * Mock 数据：KPI 指标定义与快照（DDS kpi_definitions + kpi_snapshot_hourly）
 *
 * 6 大 KPI 指标（UI/UX §3.1.4）：
 * - 平稳性：PV 波动率、PV 超限率
 * - 响应性：偏差积分 IAE、设定值响应时间
 * - 鲁棒性：增益裕度、相位裕度
 */

import type { KpiDefinition, KpiSnapshot } from './types';
import { loops } from './loops';

/** 6 大 KPI 指标定义（权重总和 100%） */
export const kpiDefinitions: KpiDefinition[] = [
  { kpiId: 'K01', kpiName: 'PV 波动率', kpiCode: 'PV_STD', category: '平稳性', weight: 20, unit: '%', description: 'PV 标准差占量程百分比，越低越平稳', enabled: true },
  { kpiId: 'K02', kpiName: 'PV 超限率', kpiCode: 'PV_OOL', category: '平稳性', weight: 15, unit: '%', description: 'PV 超出上下限的时间占比', enabled: true },
  { kpiId: 'K03', kpiName: '偏差积分 IAE', kpiCode: 'IAE', category: '响应性', weight: 25, unit: '·s', description: '绝对误差积分，衡量控制偏差', enabled: true },
  { kpiId: 'K04', kpiName: '设定值响应时间', kpiCode: 'SV_RT', category: '响应性', weight: 15, unit: 's', description: '设定值阶跃后 PV 进入 ±2% 带的时间', enabled: true },
  { kpiId: 'K05', kpiName: '增益裕度', kpiCode: 'GM', category: '鲁棒性', weight: 15, unit: 'dB', description: '开环增益裕度，衡量鲁棒性', enabled: true },
  { kpiId: 'K06', kpiName: '相位裕度', kpiCode: 'PM', category: '鲁棒性', weight: 10, unit: '°', description: '开环相位裕度，衡量鲁棒性', enabled: true },
];

/** 为每个回路生成 KPI 快照 */
function makeSnapshot(loopId: string, loopName: string, nodeName: string, score: number | null, status: KpiSnapshot['computeStatus']): KpiSnapshot {
  const baseScore = score ?? 50;
  return {
    loopId,
    loopName,
    nodeName,
    snapshotTime: '2026-06-21 10:00:00',
    score,
    computeStatus: status,
    items: kpiDefinitions.map((kpi, idx) => {
      // 根据回路总分反推各项得分（加随机偏移）
      const offset = ((idx * 7 + loopId.charCodeAt(1) * 3) % 30) - 15;
      const itemScore = Math.max(0, Math.min(100, baseScore + offset));
      return {
        kpiId: kpi.kpiId,
        kpiName: kpi.kpiName,
        kpiCode: kpi.kpiCode,
        value: Math.round((100 - itemScore) * 10) / 10,
        score: status === 'INCONCLUSIVE' ? null : Math.round(itemScore),
        unit: kpi.unit,
      };
    }),
  };
}

export const kpiSnapshots: KpiSnapshot[] = loops.map((l) =>
  makeSnapshot(l.loopId, l.loopName, l.nodeName, l.score, l.computeStatus),
);

/** 全局 KPI 汇总（工作台卡片用） */
export function getGlobalKpiSummary() {
  const total = kpiSnapshots.length;
  const scored = kpiSnapshots.filter((s) => s.score !== null);
  const avgScore = scored.reduce((sum, s) => sum + (s.score ?? 0), 0) / (scored.length || 1);
  const excellent = scored.filter((s) => (s.score ?? 0) >= 80).length;
  const warning = scored.filter((s) => (s.score ?? 0) >= 60 && (s.score ?? 0) < 80).length;
  const low = scored.filter((s) => (s.score ?? 0) < 60).length;
  const partial = kpiSnapshots.filter((s) => s.computeStatus === 'PARTIAL').length;
  const inconclusive = kpiSnapshots.filter((s) => s.computeStatus === 'INCONCLUSIVE').length;
  return {
    total,
    avgScore: Math.round(avgScore * 10) / 10,
    excellent,
    warning,
    low,
    partial,
    inconclusive,
    passRate: Math.round((excellent / total) * 1000) / 10,
  };
}

/** 7 天评分趋势（工作台趋势图用） */
export function getScoreTrend7d(): Array<{ date: string; avg: number; low: number }> {
  const dates = ['06-15', '06-16', '06-17', '06-18', '06-19', '06-20', '06-21'];
  return dates.map((d, i) => ({
    date: d,
    avg: Math.round((75 + Math.sin(i * 0.8) * 5) * 10) / 10,
    low: Math.round((12 + Math.cos(i * 0.6) * 3) * 10) / 10,
  }));
}
