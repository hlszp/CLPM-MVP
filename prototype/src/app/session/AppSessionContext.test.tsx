import { act, fireEvent, render, renderHook, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { AppSessionProvider, useAppSession } from './AppSessionContext';
import { ContextSummaryBar } from '../../components/ContextSummaryBar';
import { RoleSwitcher } from '../../components/RoleSwitcher';
import { UnauthorizedState } from '../../components/UnauthorizedState';

describe('AppSessionContext', () => {
  it('switches role and keeps a valid default route', () => {
    const { result } = renderHook(() => useAppSession(), {
      wrapper: ({ children }) => <AppSessionProvider>{children}</AppSessionProvider>,
    });

    act(() => {
      result.current.setRole('sponsor');
    });

    expect(result.current.role).toBe('sponsor');
    expect(result.current.defaultRoute).toBe('/sponsor');
  });

  it('updates session-driven UI when role changes from the switcher', () => {
    render(
      <AppSessionProvider>
        <RoleSwitcher />
        <ContextSummaryBar />
      </AppSessionProvider>
    );

    fireEvent.change(screen.getByRole('combobox'), {
      target: { value: 'admin' },
    });

    expect(screen.getByDisplayValue('管理员')).toBeInTheDocument();
    expect(screen.getByText(/角色：admin/)).toBeInTheDocument();
    expect(screen.getByText(/样本：/)).toBeInTheDocument();
    expect(screen.getByText(/当前回路：/)).toBeInTheDocument();
    expect(screen.getByText(/证据包：PACKAGE_/)).toBeInTheDocument();
  });

  it('renders unauthorized fallback copy', () => {
    render(<UnauthorizedState />);

    expect(screen.getByRole('heading', { name: '当前角色不可访问' })).toBeInTheDocument();
    expect(screen.getByText('请切换角色，或回到该角色的默认入口页继续操作。')).toBeInTheDocument();
  });
});
