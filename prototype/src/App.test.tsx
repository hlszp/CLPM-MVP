import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import App from './App';

describe('App route access', () => {
  it('renders unauthorized state when engineer opens an admin-only path', () => {
    window.history.pushState({}, '', '/system/safety');

    render(<App />);

    expect(screen.getByRole('heading', { name: '当前角色不可访问' })).toBeInTheDocument();
    expect(screen.getByText('请切换角色，或回到该角色的默认入口页继续操作。')).toBeInTheDocument();
  });
});
