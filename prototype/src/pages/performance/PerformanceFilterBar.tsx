import type { PerformanceRankingFilters } from '../../types';

interface PerformanceFilterBarProps {
  filters: PerformanceRankingFilters;
  onChange: (filters: Partial<PerformanceRankingFilters>) => void;
  selectedCount: number;
  onClearSelection: () => void;
}

export function PerformanceFilterBar({
  filters,
  onChange,
  selectedCount,
  onClearSelection,
}: PerformanceFilterBarProps) {
  return (
    <section className="panel performance-filter-bar" aria-label="排行筛选栏">
      <label>
        <span>风险等级</span>
        <select
          aria-label="风险等级"
          value={filters.risk}
          onChange={(event) => onChange({ risk: event.target.value as PerformanceRankingFilters['risk'] })}
        >
          <option value="all">全部</option>
          <option value="high">高风险</option>
          <option value="medium">中风险</option>
          <option value="low">低风险</option>
        </select>
      </label>
      <label>
        <span>对象状态</span>
        <select
          aria-label="对象状态"
          value={filters.status}
          onChange={(event) => onChange({ status: event.target.value as PerformanceRankingFilters['status'] })}
        >
          <option value="all">全部</option>
          <option value="可评估">可评估</option>
          <option value="可诊断">可诊断</option>
          <option value="可整定">可整定</option>
          <option value="需现场核实">需现场核实</option>
        </select>
      </label>
      <label>
        <span>关键词</span>
        <input
          aria-label="关键词"
          value={filters.keyword}
          onChange={(event) => onChange({ keyword: event.target.value })}
        />
      </label>
      <label>
        <span>排序方式</span>
        <select
          aria-label="排序方式"
          value={filters.sortBy}
          onChange={(event) => onChange({ sortBy: event.target.value as PerformanceRankingFilters['sortBy'] })}
        >
          <option value="score">按评分</option>
          <option value="risk">按风险</option>
          <option value="loop">按回路</option>
        </select>
      </label>
      <div className="performance-batch-actions">
        <span>已选 {selectedCount} 条</span>
        <button type="button" className="button ghost" onClick={onClearSelection}>
          清空选择
        </button>
      </div>
    </section>
  );
}
