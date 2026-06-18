import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { useAppSession } from '../../app/session/AppSessionContext';
import { renderWithSession } from '../../test/renderWithSession';

vi.mock('./HomeMissionStrip', () => ({
  HomeMissionStrip: () => <div data-testid="mission-strip" />,
}));

vi.mock('./HomeEvidenceWorkspace', () => ({
  HomeEvidenceWorkspace: () => <div data-testid="evidence-workspace" />,
}));

vi.mock('./HomeActionDrawer', () => ({
  HomeActionDrawer: () => <div data-testid="action-drawer" />,
}));

vi.mock('./HomePriorityQueue', () => ({
  HomePriorityQueue: ({
    loops,
    onSelect,
  }: {
    loops: Array<{ id: string }>;
    onSelect: (loopId: string) => void;
  }) => (
    <div>
      {loops.map((loop) => (
        <button key={loop.id} type="button" onClick={() => onSelect(loop.id)}>
          {loop.id}
        </button>
      ))}
    </div>
  ),
}));

function SessionLoopProbe() {
  const { currentLoopId } = useAppSession();

  return <output>当前回路：{currentLoopId}</output>;
}

describe('HomeWorkbench session sync', () => {
  it('updates session selected loop when choosing another priority loop', async () => {
    const user = userEvent.setup();
    const { HomeWorkbench } = await import('./HomeWorkbench');

    expect(HomeWorkbench).toBeTypeOf('function');

    renderWithSession(
      <>
        <HomeWorkbench />
        <SessionLoopProbe />
      </>
    );

    await user.click(screen.getByRole('button', { name: /LIC-1143/i }));

    expect(screen.getByText(/当前回路：LIC-1143/i)).toBeInTheDocument();
  });
});
