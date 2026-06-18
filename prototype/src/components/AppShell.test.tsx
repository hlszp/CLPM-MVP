import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { AppShell } from './AppShell';
import { renderWithSession } from '../test/renderWithSession';

describe('AppShell role-aware navigation', () => {
  it('hides system management for engineer role', () => {
    renderWithSession(
      <AppShell>
        <div>body</div>
      </AppShell>
    );

    expect(screen.queryByText('系统管理')).not.toBeInTheDocument();
  });

  it('switches navigation groups when role changes to sponsor', async () => {
    const user = userEvent.setup();

    renderWithSession(
      <AppShell>
        <div>body</div>
      </AppShell>
    );

    await user.selectOptions(screen.getByLabelText('当前角色'), 'sponsor');

    expect(screen.getByText('管理首页')).toBeInTheDocument();
    expect(screen.queryByText('实施记录')).not.toBeInTheDocument();
  });
});
