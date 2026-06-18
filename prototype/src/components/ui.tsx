import { Link } from 'react-router-dom';
import type { LoopRecord, LoopStatus, RiskLevel, StateKind } from '../types';

export function StatusBadge({ value }: { value: LoopStatus }) {
  return <span className={`status-badge status-${statusClass(value)}`}>{value}</span>;
}

export function RiskBadge({ value }: { value: RiskLevel }) {
  const label = value === 'high' ? '高风险' : value === 'medium' ? '中风险' : '低风险';
  return <span className={`risk-badge risk-${value}`}>{label}</span>;
}

export function MetricCard({ label, value, delta }: { label: string; value: string; delta: string }) {
  return (
    <article className="metric-card">
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{delta}</small>
    </article>
  );
}

export function LoopTable({
  loops,
  onSelect,
  selectedId,
  selectedIds = [],
  onToggleSelection,
  showSelection = false,
}: {
  loops: LoopRecord[];
  onSelect?: (loop: LoopRecord) => void;
  selectedId?: string;
  selectedIds?: string[];
  onToggleSelection?: (loopId: string) => void;
  showSelection?: boolean;
}) {
  return (
    <div className="table-wrap" role="region" aria-label="回路清单表格">
      <table>
        <thead>
          <tr>
            {showSelection ? <th>选择</th> : null}
            <th>回路</th>
            <th>装置</th>
            <th>类型</th>
            <th>状态</th>
            <th>风险</th>
            <th>评分</th>
            <th>下一步</th>
          </tr>
        </thead>
        <tbody>
          {loops.map((loop) => {
            const interactiveProps = onSelect
              ? {
                  onClick: () => onSelect(loop),
                  onKeyDown: (event: React.KeyboardEvent<HTMLTableRowElement>) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault();
                      onSelect(loop);
                    }
                  },
                  tabIndex: 0,
                }
              : {};
            return (
              <tr
                key={loop.id}
                className={selectedId === loop.id ? 'selected-row' : undefined}
                aria-selected={selectedId === loop.id ? 'true' : undefined}
                {...interactiveProps}
              >
                {showSelection ? (
                  <td>
                    <input
                      type="checkbox"
                      aria-label={`选择 ${loop.id}`}
                      checked={selectedIds.includes(loop.id)}
                      onClick={(event) => event.stopPropagation()}
                      onKeyDown={(event) => event.stopPropagation()}
                      onChange={() => onToggleSelection?.(loop.id)}
                    />
                  </td>
                ) : null}
                <th scope="row">{loop.id}</th>
                <td>{loop.device}</td>
                <td>{loop.type}</td>
                <td>
                  <StatusBadge value={loop.status} />
                </td>
                <td>
                  <RiskBadge value={loop.risk} />
                </td>
                <td>{loop.score}</td>
                <td>{loop.nextAction}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function LoopCardList({ loops, selectedId, onSelect }: { loops: LoopRecord[]; selectedId?: string; onSelect: (loop: LoopRecord) => void }) {
  return (
    <div className="loop-card-list" aria-label="回路任务列表">
      {loops.map((loop) => (
        <button key={loop.id} type="button" className={`loop-card ${selectedId === loop.id ? 'active' : ''}`} onClick={() => onSelect(loop)} aria-pressed={selectedId === loop.id}>
          <span>
            <strong>{loop.id}</strong>
            <small>{loop.device} · {loop.type}</small>
          </span>
          <span className="loop-card-meta">
            <RiskBadge value={loop.risk} />
            <b>{loop.score}</b>
          </span>
          <em>{loop.nextAction}</em>
        </button>
      ))}
    </div>
  );
}

export function StatusMetric({ label, value, tone = 'neutral' }: { label: string; value: string; tone?: 'ok' | 'warning' | 'danger' | 'neutral' }) {
  return (
    <div className={`status-metric tone-${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

export function EvidencePackageHeader({
  id,
  packageStatus,
  validityStatus,
  completeness,
  manifestHash,
  generatedAt,
  missingCount,
}: {
  id: string;
  packageStatus: string;
  validityStatus: string;
  completeness: number;
  manifestHash: string;
  generatedAt: string;
  missingCount: number;
}) {
  return (
    <section className="evidence-header" aria-label="EvidencePackage 状态头">
      <div className="evidence-title">
        <span>EvidencePackage</span>
        <strong>{id}</strong>
      </div>
      <div className="evidence-status-grid">
        <StatusMetric label="package_status" value={packageStatus} tone={packageStatus.includes('PARTIAL') ? 'warning' : 'ok'} />
        <StatusMetric label="validity_status" value={validityStatus} tone={validityStatus.includes('MISSING') ? 'warning' : 'ok'} />
        <StatusMetric label="完整度" value={`${Math.round(completeness * 100)}%`} tone={completeness < 1 ? 'warning' : 'ok'} />
        <StatusMetric label="缺失引用" value={`${missingCount} 项`} tone={missingCount > 0 ? 'danger' : 'ok'} />
        <StatusMetric label="hash" value={manifestHash} tone="neutral" />
        <StatusMetric label="生成时间" value={generatedAt} tone="neutral" />
      </div>
    </section>
  );
}

export function EvidenceImpactStrip({ href = '/evidence' }: { href?: string }) {
  return (
    <aside className="impact-strip" aria-label="证据包影响提示">
      <strong>当前决策影响 EvidencePackage</strong>
      <span>缺少现场核实或复评记录时，导出包保持 partial，不允许生成“已完成闭环”结论。</span>
      <Link className="button ghost" to={href}>查看证据包</Link>
    </aside>
  );
}

export function StateBlock({ state }: { state: StateKind }) {
  const copy = {
    loading: ['正在加载', '正在读取当前样本与待办。'],
    empty: ['暂无对象', '当前没有待处理回路，可返回样本验证。'],
    error: ['读取失败', '请检查样本批次或重新计算结果。'],
    success: ['可评审', '当前页面数据完整，可继续主链操作。'],
    partial: ['部分可用', '缺少部分证据，只展示可判定部分，并标明下一步。'],
  }[state];
  return <aside className={`state-block state-${state}`} role={state === 'error' ? 'alert' : 'status'}><strong>{copy[0]}</strong><span>{copy[1]}</span></aside>;
}

function statusClass(value: LoopStatus) {
  const classes: Record<LoopStatus, string> = {
    可评估: 'ok',
    可诊断: 'info',
    可整定: 'ok',
    需现场核实: 'warning',
    数据不足: 'danger',
    不可判定: 'neutral',
  };
  return classes[value];
}
