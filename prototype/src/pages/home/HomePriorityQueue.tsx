import { Link } from 'react-router-dom';
import { LoopCardList } from '../../components/ui';
import type { LoopRecord } from '../../types';

export function HomePriorityQueue({
  loops,
  selectedId,
  onSelect,
}: {
  loops: LoopRecord[];
  selectedId: string;
  onSelect: (loopId: string) => void;
}) {
  return (
    <section className="panel task-queue" aria-label="低性能优先级清单">
      <div className="section-heading">
        <div>
          <h2>今日优先处理队列</h2>
          <p>选中回路后，证据与动作在同屏更新，不打断工程师判断。</p>
        </div>
        <Link className="text-link" to="/performance/ranking">
          完整排行
        </Link>
      </div>
      <LoopCardList loops={loops} selectedId={selectedId} onSelect={(loop) => onSelect(loop.id)} />
    </section>
  );
}
