import { act, renderHook, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { AppSessionProvider, useAppSession } from '../../app/session/AppSessionContext';
import { clearLoopSelection, mergePerformanceFilters, toggleLoopSelection } from '../../app/session/performanceRanking';
import { renderWithSession } from '../../test/renderWithSession';
import { PerformanceRankingWorkbench } from './PerformanceRankingWorkbench';

describe('performanceRanking session helpers', () => {
  it('merges ranking filters into session state', () => {
    const next = mergePerformanceFilters(
      {
        filters: {
          risk: 'all',
          status: 'all',
          keyword: '',
          sortBy: 'score',
        },
        selectedLoopIds: [],
      },
      {
        risk: 'high',
        keyword: 'tic',
      }
    );

    expect(next.filters).toEqual({
      risk: 'high',
      status: 'all',
      keyword: 'tic',
      sortBy: 'score',
    });
  });

  it('toggles and clears ranked loop selection', () => {
    const selected = toggleLoopSelection(
      {
        filters: {
          risk: 'all',
          status: 'all',
          keyword: '',
          sortBy: 'score',
        },
        selectedLoopIds: [],
      },
      'TIC-1115'
    );

    expect(selected.selectedLoopIds).toEqual(['TIC-1115']);
    expect(toggleLoopSelection(selected, 'TIC-1115').selectedLoopIds).toEqual([]);
    expect(clearLoopSelection({ ...selected, selectedLoopIds: ['TIC-1115', 'FIC-1101'] }).selectedLoopIds).toEqual([]);
  });
});

describe('AppSessionContext performance ranking state', () => {
  it('stores ranking filters and selection inside session', () => {
    const { result } = renderHook(() => useAppSession(), {
      wrapper: ({ children }) => <AppSessionProvider>{children}</AppSessionProvider>,
    });

    act(() => {
      result.current.setPerformanceFilters({
        risk: 'high',
        status: '可诊断',
      });
      result.current.toggleRankedLoopSelection('TIC-1115');
      result.current.toggleRankedLoopSelection('LIC-1143');
    });

    expect(result.current.performanceRanking.filters).toEqual({
      risk: 'high',
      status: '可诊断',
      keyword: '',
      sortBy: 'score',
    });
    expect(result.current.performanceRanking.selectedLoopIds).toEqual(['TIC-1115', 'LIC-1143']);

    act(() => {
      result.current.clearRankedLoopSelection();
    });

    expect(result.current.performanceRanking.selectedLoopIds).toEqual([]);
  });
});

describe('PerformanceRankingWorkbench', () => {
  it('filters ranking table to high risk loops only while keeping held-out loops visible', async () => {
    const user = userEvent.setup();

    renderWithSession(<PerformanceRankingWorkbench />);

    await user.selectOptions(screen.getByLabelText('风险等级'), 'high');

    expect(screen.getByRole('row', { name: /TIC-1115/i })).toBeInTheDocument();
    expect(screen.queryByRole('row', { name: /FIC-1101/i })).not.toBeInTheDocument();
    expect(screen.getByText('未参与真实排序对象')).toBeInTheDocument();
    expect(screen.getByRole('row', { name: /FIC-1136/i })).toBeInTheDocument();
  });

  it('keeps batch selection separate from current loop context until a row is selected', async () => {
    const user = userEvent.setup();

    renderWithSession(<PerformanceRankingWorkbench />);

    await user.click(screen.getByRole('checkbox', { name: /选择 LIC-1143/i }));

    expect(screen.getByText('批量已选：1 条')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '进入证据链' })).toHaveAttribute('href', '/diagnosis/loop/TIC-1115');

    await user.click(screen.getByRole('row', { name: /LIC-1143/i }));

    expect(screen.getByRole('link', { name: '进入证据链' })).toHaveAttribute('href', '/diagnosis/loop/LIC-1143');
    expect(screen.getByText(/下一步：标记扰动并复核评价窗口/)).toBeInTheDocument();
  });
});
