/**
 * ROLE_DEFAULT_HOME 前端落地单元测试
 *
 * 对齐实现契约 §5 + UI/UX §4.2 三方权限基准 + 驾驶舱方案 11 §3.1（C2）：
 * - 全角色 → /cockpit 驾驶舱（2026-10-05 驾驶舱整合裁决 D5/D6：旧运维工作台退役，默认落地驾驶舱）
 * - EXPERT → /cockpit（同上）
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
  it('EXPERT 默认首页为 /cockpit 驾驶舱', () => {
    expect(ROLE_DEFAULT_HOME.EXPERT).toBe('/cockpit');
    expect(resolveHomePath('EXPERT', '/dashboard')).toBe('/cockpit');
  });

  it('sPONSOR / IC_ENGINEER / PE_ENGINEER 默认首页为 /cockpit 驾驶舱', () => {
    for (const role of ['SPONSOR', 'IC_ENGINEER', 'PE_ENGINEER']) {
      expect(ROLE_DEFAULT_HOME[role]).toBe('/cockpit');
      expect(resolveHomePath(role, '/dashboard')).toBe('/cockpit');
    }
  });

  it('aDMIN 默认首页为 /cockpit 驾驶舱（0929 总览收敛）', () => {
    expect(ROLE_DEFAULT_HOME.ADMIN).toBe('/cockpit');
    expect(resolveHomePath('ADMIN', '/dashboard')).toBe('/cockpit');
  });

  it('角色映射优先于后端 defaultHome 返回值', () => {
    // 后端 defaultHome 返回任意值，前端映射必须覆盖之
    expect(resolveHomePath('EXPERT', '/dashboard')).toBe('/cockpit');
    expect(resolveHomePath('SPONSOR', '/reports/overview')).toBe('/cockpit');
  });

  it('未知角色回退后端 defaultHome，再兜底 /cockpit', () => {
    expect(resolveHomePath('UNKNOWN_ROLE', '/metric')).toBe('/metric');
    expect(resolveHomePath('UNKNOWN_ROLE', null)).toBe('/cockpit');
    expect(resolveHomePath('UNKNOWN_ROLE')).toBe('/cockpit');
  });
});
