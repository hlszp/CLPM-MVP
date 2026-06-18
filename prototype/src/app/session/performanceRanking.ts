import type { PerformanceRankingFilters } from '../../types';
import type { PerformanceRankingState } from './types';

export function mergePerformanceFilters(
  current: PerformanceRankingState,
  filters: Partial<PerformanceRankingFilters>
): PerformanceRankingState {
  return {
    ...current,
    filters: {
      ...current.filters,
      ...filters,
    },
  };
}

export function toggleLoopSelection(current: PerformanceRankingState, loopId: string): PerformanceRankingState {
  return current.selectedLoopIds.includes(loopId)
    ? {
        ...current,
        selectedLoopIds: current.selectedLoopIds.filter((id) => id !== loopId),
      }
    : {
        ...current,
        selectedLoopIds: [...current.selectedLoopIds, loopId],
      };
}

export function clearLoopSelection(current: PerformanceRankingState): PerformanceRankingState {
  return {
    ...current,
    selectedLoopIds: [],
  };
}
