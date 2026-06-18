import { Link } from 'react-router-dom';
import type { LoopRecord } from '../../types';

interface PerformanceContextPanelProps {
  selectedLoop: LoopRecord | undefined;
  selectedCount: number;
}

export function PerformanceContextPanel({
  selectedLoop,
  selectedCount,
}: PerformanceContextPanelProps) {
  if (!selectedLoop) {
    return (
      <aside className="panel">
        <h2>当前上下文</h2>
        <p>当前没有命中筛选结果，请调整筛选条件。</p>
      </aside>
    );
  }

  return (
    <aside className="panel performance-context-panel">
      <h2>当前回路上下文</h2>
      <p>
        <strong>{selectedLoop.id}</strong> · {selectedLoop.device} · {selectedLoop.type}
      </p>
      <p>下一步：{selectedLoop.nextAction}</p>
      <p>批量已选：{selectedCount} 条</p>
      <div className="top-actions">
        <Link className="button" to={`/diagnosis/loop/${selectedLoop.id}`}>
          进入证据链
        </Link>
        <Link className="button ghost" to="/closure/review">
          进入建议审核
        </Link>
      </div>
    </aside>
  );
}
