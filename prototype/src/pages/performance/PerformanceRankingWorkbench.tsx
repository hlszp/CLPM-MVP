import { useNavigate } from 'react-router-dom';
import { useAppSession } from '../../app/session/AppSessionContext';
import { PerformanceContextPanel } from './PerformanceContextPanel';
import { PerformanceFilterBar } from './PerformanceFilterBar';
import { PerformanceHeldOutTable } from './PerformanceHeldOutTable';
import { PerformanceOverviewBoard } from './PerformanceOverviewBoard';
import { PerformanceRankingTable } from './PerformanceRankingTable';
import { getPerformanceRankingViewModel } from './performanceRankingModel';

export function PerformanceRankingWorkbench() {
  const navigate = useNavigate();
  const {
    currentLoopId,
    selectLoop,
    performanceRanking,
    setPerformanceFilters,
    toggleRankedLoopSelection,
    clearRankedLoopSelection,
  } = useAppSession();

  const { summaryCards, rankedLoops, heldOutLoops } = getPerformanceRankingViewModel(
    performanceRanking.filters
  );
  const selectedLoop = rankedLoops.find((loop) => loop.id === currentLoopId) ?? rankedLoops[0];

  return (
    <section className="performance-ranking-workbench">
      <PerformanceOverviewBoard cards={summaryCards} />
      <PerformanceFilterBar
        filters={performanceRanking.filters}
        onChange={setPerformanceFilters}
        selectedCount={performanceRanking.selectedLoopIds.length}
        onClearSelection={clearRankedLoopSelection}
      />
      <section className="performance-ranking-layout">
        <div className="performance-ranking-main">
          <PerformanceRankingTable
            loops={rankedLoops}
            selectedLoopId={selectedLoop?.id ?? ''}
            selectedIds={performanceRanking.selectedLoopIds}
            onSelect={(loop) => {
              selectLoop(loop.id);
              navigate(`/diagnosis/loop/${loop.id}`);
            }}
            onToggleSelection={toggleRankedLoopSelection}
          />
          <PerformanceHeldOutTable loops={heldOutLoops} />
        </div>
        <PerformanceContextPanel
          selectedLoop={selectedLoop}
          selectedCount={performanceRanking.selectedLoopIds.length}
        />
      </section>
    </section>
  );
}
