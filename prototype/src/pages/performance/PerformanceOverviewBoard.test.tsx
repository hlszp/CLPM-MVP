import { useState } from 'react';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { render } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { performanceSummaryCards } from '../../data/mockData';
import { PerformancePage } from '../overviewPerformancePages';
import type { PerformanceRankingFilters } from '../../types';
import { PerformanceFilterBar } from './PerformanceFilterBar';
import { PerformanceOverviewBoard } from './PerformanceOverviewBoard';
import { getPerformanceRankingViewModel } from './performanceRankingModel';

describe('performanceRankingModel', () => {
  it('separates held out loops from ranked loops', () => {
    const model = getPerformanceRankingViewModel({
      risk: 'all',
      status: 'all',
      keyword: '',
      sortBy: 'score',
    });

    expect(model.heldOutLoops.length).toBeGreaterThan(0);
    expect(model.heldOutLoops.every((loop) => ['数据不足', '不可判定'].includes(loop.status))).toBe(true);
  });
});

describe('PerformanceFilterBar', () => {
  it('emits filter updates and clears selection', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const onClearSelection = vi.fn();

    function FilterBarHarness() {
      const [filters, setFilters] = useState<PerformanceRankingFilters>({
        risk: 'all',
        status: 'all',
        keyword: '',
        sortBy: 'score',
      });

      return (
        <PerformanceFilterBar
          filters={filters}
          onChange={(next) => {
            onChange(next);
            setFilters((current) => ({ ...current, ...next }));
          }}
          selectedCount={2}
          onClearSelection={onClearSelection}
        />
      );
    }

    render(
      <FilterBarHarness />
    );

    await user.selectOptions(screen.getByLabelText('风险等级'), 'high');
    await user.type(screen.getByLabelText('关键词'), 'TIC');
    await user.click(screen.getByRole('button', { name: '清空选择' }));

    expect(onChange).toHaveBeenCalledWith({ risk: 'high' });
    expect(onChange).toHaveBeenCalledWith({ keyword: 'TIC' });
    expect(onClearSelection).toHaveBeenCalledTimes(1);
    expect(screen.getByText('已选 2 条')).toBeInTheDocument();
  });
});

describe('PerformanceOverviewBoard', () => {
  it('renders all summary cards on the overview board', () => {
    render(<PerformanceOverviewBoard cards={performanceSummaryCards} />);

    expect(screen.getByText('样本自控率')).toBeInTheDocument();
    expect(screen.getByText('94.5%')).toBeInTheDocument();
    expect(screen.getByText('闭环候选率')).toBeInTheDocument();
    expect(screen.getByText('3 条需现场核实')).toBeInTheDocument();
  });
});

describe('PerformancePage', () => {
  it('uses the overview board and links into the ranking workbench', () => {
    render(
      <MemoryRouter>
        <PerformancePage
          route={{
            id: 'kpi',
            label: '绩效评估',
            path: '/performance',
            version: 'P0',
            depth: 'deep',
            description: '绩效总览',
          }}
        />
      </MemoryRouter>
    );

    expect(screen.getByText('样本自控率')).toBeInTheDocument();
    expect(screen.getByText(/继续进入排行工作台查看筛选、批量选择与当前回路上下文/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '查看排行' })).toHaveAttribute('href', '/performance/ranking');
  });
});
