import { LoopTable } from '../../components/ui';
import type { LoopRecord } from '../../types';

interface PerformanceRankingTableProps {
  loops: LoopRecord[];
  selectedLoopId: string;
  selectedIds: string[];
  onSelect: (loop: LoopRecord) => void;
  onToggleSelection: (loopId: string) => void;
}

export function PerformanceRankingTable({
  loops,
  selectedLoopId,
  selectedIds,
  onSelect,
  onToggleSelection,
}: PerformanceRankingTableProps) {
  return (
    <section className="panel">
      <h2>低效排行</h2>
      <p>仅对可评估、可诊断、可整定、需现场核实对象排序；数据不足与不可判定不会被当作真实 0 分。</p>
      <LoopTable
        loops={loops}
        onSelect={onSelect}
        selectedId={selectedLoopId}
        selectedIds={selectedIds}
        onToggleSelection={onToggleSelection}
        showSelection
      />
    </section>
  );
}
