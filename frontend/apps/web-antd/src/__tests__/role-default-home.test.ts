/**
 * ROLE_DEFAULT_HOME 前端落地单元测试
 *
 * 对齐实现契约 §5 + UI/UX §4.2 三方权限基准：
 * - EXPERT → /diagnosis/records（仅诊断中心 + 回路整定，诊断记录页）
 * - SPONSOR → /reports/overview（仅汇总视图，统计报告总览）
 * - 其余角色 → /dashboard
 *
 * 后端 auth.py ROLE_DEFAULT_HOME 已同批对齐（SPONSOR → /reports/overview），
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

describe('rOLE_DEFAULT_HOME（实现契约 §5 三方对齐）', () => {
  it('EXPERT 默认首页为 /diagnosis/records', () => {
    expect(ROLE_DEFAULT_HOME.EXPERT).toBe('/diagnosis/records');
    expect(resolveHomePath('EXPERT', '/dashboard')).toBe('/diagnosis/records');
  });

  it('sPONSOR 默认首页为 /reports/overview', () => {
    expect(ROLE_DEFAULT_HOME.SPONSOR).toBe('/reports/overview');
    expect(resolveHomePath('SPONSOR', '/dashboard')).toBe('/reports/overview');
  });

  it('aDMIN / IC_ENGINEER / PE_ENGINEER 默认首页为 /dashboard', () => {
    for (const role of ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER']) {
      expect(resolveHomePath(role, '/dashboard')).toBe('/dashboard');
    }
  });

  it('角色映射优先于后端 defaultHome 返回值', () => {
    // 后端 defaultHome 返回任意值，前端映射必须覆盖之
    expect(resolveHomePath('EXPERT', '/dashboard')).toBe('/diagnosis/records');
    expect(resolveHomePath('SPONSOR', '/dashboard')).toBe('/reports/overview');
  });

  it('未知角色回退后端 defaultHome，再兜底 /dashboard', () => {
    expect(resolveHomePath('UNKNOWN_ROLE', '/metric')).toBe('/metric');
    expect(resolveHomePath('UNKNOWN_ROLE', null)).toBe('/dashboard');
    expect(resolveHomePath('UNKNOWN_ROLE')).toBe('/dashboard');
  });
});
