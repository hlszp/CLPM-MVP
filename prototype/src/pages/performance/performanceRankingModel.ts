import { loops, performanceSummaryCards } from '../../data/mockData';
import type { LoopRecord, PerformanceRankingFilters } from '../../types';

const rankedStatuses = ['可评估', '可诊断', '可整定', '需现场核实'] as const;

export function getPerformanceRankingViewModel(filters: PerformanceRankingFilters) {
  const keyword = filters.keyword.trim().toLowerCase();
  const heldOutLoops = loops.filter((loop) => ['数据不足', '不可判定'].includes(loop.status));

  const rankedLoops = loops
    .filter((loop) => rankedStatuses.includes(loop.status as (typeof rankedStatuses)[number]))
    .filter((loop) => (filters.risk === 'all' ? true : loop.risk === filters.risk))
    .filter((loop) => (filters.status === 'all' ? true : loop.status === filters.status))
    .filter((loop) => {
      if (!keyword) {
        return true;
      }

      return [loop.id, loop.device, loop.type, loop.nextAction].some((value) => value.toLowerCase().includes(keyword));
    })
    .sort((left, right) => {
      if (filters.sortBy === 'loop') {
        return left.id.localeCompare(right.id);
      }

      if (filters.sortBy === 'risk') {
        return riskRank(left.risk) - riskRank(right.risk) || left.score - right.score;
      }

      return left.score - right.score;
    });

  return {
    summaryCards: performanceSummaryCards,
    rankedLoops,
    heldOutLoops,
  };
}

function riskRank(risk: LoopRecord['risk']) {
  return risk === 'high' ? 0 : risk === 'medium' ? 1 : 2;
}
