/**
 * ROLE_DEFAULT_HOME 前端落地单元测试
 *
 * 2026-10-14 P6-06（IAR-D1/D2 已采纳）：
 * - 工程师（IC_ENGINEER/PE_ENGINEER/EXPERT）→ /my-work 我的工作
 * - 管理员/观察者（ADMIN/SPONSOR）→ /cockpit 驾驶舱
 *
 * 前端 resolveHomePath 以角色映射优先于后端 defaultHome 返回值；
 * 后端 auth.py 同批已对齐本表。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

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

/* F-G2：登录首页消费 default_home 偏好（动态 import 目标模块 mock） */
const fetchDefaultHomePreferenceMock = vi.hoisted(() => vi.fn());
vi.mock('#/composables/use-clpm-preferences', async () => {
  const actual = await vi.importActual<any>(
    '#/composables/use-clpm-preferences',
  );
  return {
    ...actual,
    fetchDefaultHomePreference: (...a: any[]) =>
      fetchDefaultHomePreferenceMock(...a),
    handleClpmAccountLogout: vi.fn(),
    noteClpmAccountLogin: vi.fn(),
  };
});

const { resolveHomePath, resolveUserHomePath, ROLE_DEFAULT_HOME } =
  await import('#/store/auth');

describe('rOLE_DEFAULT_HOME（2026-10-14 P6-06 IAR-D1/D2）', () => {
  it('eXPERT 默认首页为 /my-work 我的工作', () => {
    expect(ROLE_DEFAULT_HOME.EXPERT).toBe('/my-work');
    expect(resolveHomePath('EXPERT', '/dashboard')).toBe('/my-work');
  });

  it('iC_ENGINEER / PE_ENGINEER 默认首页为 /my-work 我的工作', () => {
    for (const role of ['IC_ENGINEER', 'PE_ENGINEER']) {
      expect(ROLE_DEFAULT_HOME[role]).toBe('/my-work');
      expect(resolveHomePath(role, '/dashboard')).toBe('/my-work');
    }
  });

  it('aDMIN / SPONSOR 默认首页为 /cockpit 驾驶舱', () => {
    for (const role of ['ADMIN', 'SPONSOR']) {
      expect(ROLE_DEFAULT_HOME[role]).toBe('/cockpit');
      expect(resolveHomePath(role, '/dashboard')).toBe('/cockpit');
    }
  });

  it('角色映射优先于后端 defaultHome 返回值', () => {
    // 后端 defaultHome 返回任意值，前端映射必须覆盖之
    expect(resolveHomePath('EXPERT', '/dashboard')).toBe('/my-work');
    expect(resolveHomePath('SPONSOR', '/reports/overview')).toBe('/cockpit');
  });

  it('未知角色回退后端 defaultHome，再兜底 /cockpit', () => {
    expect(resolveHomePath('UNKNOWN_ROLE', '/metric')).toBe('/metric');
    expect(resolveHomePath('UNKNOWN_ROLE', null)).toBe('/cockpit');
    expect(resolveHomePath('UNKNOWN_ROLE')).toBe('/cockpit');
  });
});

describe('resolveUserHomePath（F-G2：个人 default_home 偏好优先于角色映射）', () => {
  beforeEach(() => {
    fetchDefaultHomePreferenceMock.mockReset();
  });

  it('服务端偏好为合法站内路径 → 优先于角色映射', async () => {
    fetchDefaultHomePreferenceMock.mockResolvedValue('/cockpit');
    await expect(
      resolveUserHomePath('IC_ENGINEER', '/dashboard'),
    ).resolves.toBe('/cockpit');
    fetchDefaultHomePreferenceMock.mockResolvedValue('/my-work');
    await expect(resolveUserHomePath('ADMIN', '/dashboard')).resolves.toBe(
      '/my-work',
    );
  });

  it('无偏好 → 回退角色映射', async () => {
    fetchDefaultHomePreferenceMock.mockResolvedValue(null);
    await expect(
      resolveUserHomePath('IC_ENGINEER', '/dashboard'),
    ).resolves.toBe('/my-work');
    await expect(resolveUserHomePath('SPONSOR', '/x')).resolves.toBe(
      '/cockpit',
    );
  });

  it('非法值（协议外路径）→ 回退角色映射（双端形态校验）', async () => {
    fetchDefaultHomePreferenceMock.mockResolvedValue('//evil.com/x');
    await expect(
      resolveUserHomePath('IC_ENGINEER', '/dashboard'),
    ).resolves.toBe('/my-work');
  });

  it('偏好加载失败（reject）→ 回退角色映射，不阻断登录', async () => {
    fetchDefaultHomePreferenceMock.mockRejectedValue(
      new TypeError('Network Error'),
    );
    await expect(
      resolveUserHomePath('PE_ENGINEER', '/dashboard'),
    ).resolves.toBe('/my-work');
  });
});
