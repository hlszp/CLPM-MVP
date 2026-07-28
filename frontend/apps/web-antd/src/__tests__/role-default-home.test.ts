/**
 * ROLE_DEFAULT_HOME 前端落地单元测试
 *
 * 对齐实现契约 §5 + UI/UX §4.2 三方权限基准：
 * - EXPERT → /diagnosis（仅诊断中心 + 回路整定）
 * - SPONSOR → /metric（仅汇总视图）
 * - 其余角色 → /dashboard
 *
 * 后端 auth.py ROLE_DEFAULT_HOME 当前全角色返回 /dashboard（后端归属另一波次），
 * 前端 resolveHomePath 以角色映射优先于后端 defaultHome 返回值。
 */
import { describe, expect, it, vi } from 'vitest';

vi.mock('vue-router', () => ({
  useRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
    currentRoute: { value: { fullPath: '/dashboard' } },
  }),
}));

vi.mock('@vben/preferences', () => ({
  preferences: {
    app: {
      locale: 'zh-CN',
      enableRefreshToken: true,
      loginExpiredMode: 'modal',
      defaultHomePath: '/dashboard',
    },
  },
}));

vi.mock('@vben/constants', () => ({
  LOGIN_PATH: '/auth/login',
}));

vi.mock('ant-design-vue', () => ({
  notification: { success: vi.fn(), error: vi.fn() },
}));

vi.mock('#/locales', () => ({
  $t: (key: string) => key,
}));

vi.mock('#/api', () => ({
  loginApi: vi.fn(),
  logoutApi: vi.fn(),
  getUserInfoApi: vi.fn(),
  getAccessCodesApi: vi.fn(),
}));

const { resolveHomePath, ROLE_DEFAULT_HOME } = await import('#/store/auth');

describe('ROLE_DEFAULT_HOME（实现契约 §5 三方对齐）', () => {
  it('EXPERT 默认首页为 /diagnosis', () => {
    expect(ROLE_DEFAULT_HOME.EXPERT).toBe('/diagnosis');
    expect(resolveHomePath('EXPERT', '/dashboard')).toBe('/diagnosis');
  });

  it('SPONSOR 默认首页为 /metric', () => {
    expect(ROLE_DEFAULT_HOME.SPONSOR).toBe('/metric');
    expect(resolveHomePath('SPONSOR', '/dashboard')).toBe('/metric');
  });

  it('ADMIN / IC_ENGINEER / PE_ENGINEER 默认首页为 /dashboard', () => {
    for (const role of ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER']) {
      expect(resolveHomePath(role, '/dashboard')).toBe('/dashboard');
    }
  });

  it('角色映射优先于后端 defaultHome 返回值', () => {
    // 后端当前全角色返回 /dashboard，前端映射必须覆盖之
    expect(resolveHomePath('EXPERT', '/dashboard')).toBe('/diagnosis');
    expect(resolveHomePath('SPONSOR', '/dashboard')).toBe('/metric');
  });

  it('未知角色回退后端 defaultHome，再兜底 /dashboard', () => {
    expect(resolveHomePath('UNKNOWN_ROLE', '/metric')).toBe('/metric');
    expect(resolveHomePath('UNKNOWN_ROLE', null)).toBe('/dashboard');
    expect(resolveHomePath('UNKNOWN_ROLE')).toBe('/dashboard');
  });
});
