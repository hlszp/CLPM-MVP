import { ArrowRight } from 'lucide-react';
import { Link } from 'react-router-dom';
import { TrendChart } from '../../components/TrendChart';
import { StatusBadge } from '../../components/ui';
import type { EvidenceWindow, LoopRecord } from '../../types';

export function HomeEvidenceWorkspace({ selected, evidence }: { selected: LoopRecord; evidence: EvidenceWindow | undefined }) {
  return (
    <section className="panel evidence-workspace" aria-label="选中回路证据工作区">
      <div className="object-header">
        <div>
          <span className="eyebrow">当前回路</span>
          <h2>{selected.id} 证据摘要</h2>
        </div>
        <div className="object-badges">
          <StatusBadge value={selected.status} />
          <span className={`risk-badge risk-${selected.risk}`}>评分 {selected.score}</span>
        </div>
      </div>
      {evidence ? <TrendChart evidence={evidence} /> : <p>当前回路暂无趋势证据，不会伪装为完整证据链。</p>}
      <div className="evidence-rules">
        {(evidence?.rules ?? ['当前回路暂无规则命中']).map((rule) => (
          <span key={rule}>✓ {rule}</span>
        ))}
      </div>
      <Link className="button" to={`/diagnosis/loop/${selected.id}`}>
        进入证据链 <ArrowRight size={16} />
      </Link>
    </section>
  );
}
