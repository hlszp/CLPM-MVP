/**
 * ROLE_DEFAULT_HOME 前端落地单元测试
 *
 * 对齐实现契约 §5 + UI/UX §4.2 三方权限基准 + 驾驶舱方案 11 §3.1（C2）：
 * - 全角色 → /workbench 工作台（0930 用户口径：默认落地统一工作台）
 * - EXPERT → /workbench（同上）
 * - ADMIN → /dashboard（保持现行为）
 *
 * 前端 resolveHomePath 以角色映射优先于后端 defaultHome 返回值。
 * 注意：后端 auth.py ROLE_DEFAULT_HOME 仍为旧口径（SPONSOR→/reports/overview），
 * 前端映射覆盖之，后端对齐留待后续任务。
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
  it('EXPERT 默认首页为 /workbench 工作台', () => {
    expect(ROLE_DEFAULT_HOME.EXPERT).toBe('/workbench');
    expect(resolveHomePath('EXPERT', '/dashboard')).toBe('/workbench');
  });

  it('sPONSOR / IC_ENGINEER / PE_ENGINEER 默认首页为 /workbench 工作台', () => {
    for (const role of ['SPONSOR', 'IC_ENGINEER', 'PE_ENGINEER']) {
      expect(ROLE_DEFAULT_HOME[role]).toBe('/workbench');
      expect(resolveHomePath(role, '/dashboard')).toBe('/workbench');
    }
  });

  it('aDMIN 默认首页为 /workbench 工作台（0929 总览收敛）', () => {
    expect(ROLE_DEFAULT_HOME.ADMIN).toBe('/workbench');
    expect(resolveHomePath('ADMIN', '/dashboard')).toBe('/workbench');
  });

  it('角色映射优先于后端 defaultHome 返回值', () => {
    // 后端 defaultHome 返回任意值，前端映射必须覆盖之
    expect(resolveHomePath('EXPERT', '/dashboard')).toBe('/workbench');
    expect(resolveHomePath('SPONSOR', '/reports/overview')).toBe('/workbench');
  });

  it('未知角色回退后端 defaultHome，再兜底 /workbench', () => {
    expect(resolveHomePath('UNKNOWN_ROLE', '/metric')).toBe('/metric');
    expect(resolveHomePath('UNKNOWN_ROLE', null)).toBe('/workbench');
    expect(resolveHomePath('UNKNOWN_ROLE')).toBe('/workbench');
  });
});
