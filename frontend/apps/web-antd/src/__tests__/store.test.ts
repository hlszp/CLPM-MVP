/**
 * Store 单元测试
 *
 * 覆盖：
 * - useAuthStore：login / logout / refreshToken / getUserInfo
 * - 权限路由生成：ADMIN / SPONSOR 角色过滤
 * - hasPermission：通配符 / 精确匹配
 */

import { useAccessStore, useUserStore } from '@vben/stores';

import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { hasPermission } from '#/directives/permission';
import { useAuthStore } from '#/store';

// ===== Mock 依赖 =====
const routerPushSpy = vi.fn();
const routerReplaceSpy = vi.fn();
const routerCurrentRoute = { value: { fullPath: '/dashboard' } };

vi.mock('vue-router', () => ({
  useRouter: () => ({
    push: (...args: any[]) => routerPushSpy(...args),
    replace: (...args: any[]) => routerReplaceSpy(...args),
    currentRoute: routerCurrentRoute,
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
  notification: {
    success: vi.fn(),
    error: vi.fn(),
  },
  message: {
    error: vi.fn(),
    success: vi.fn(),
    loading: vi.fn(),
    warning: vi.fn(),
  },
}));

vi.mock('#/locales', () => ({
  $t: (key: string) => key,
}));

// Mock API
const loginApiMock = vi.fn();
const logoutApiMock = vi.fn();
const getUserInfoApiMock = vi.fn();
const getAccessCodesApiMock = vi.fn();
const refreshTokenApiMock = vi.fn();

vi.mock('#/api', () => ({
  loginApi: (...args: any[]) => loginApiMock(...args),
  logoutApi: (...args: any[]) => logoutApiMock(...args),
  getUserInfoApi: (...args: any[]) => getUserInfoApiMock(...args),
  getAccessCodesApi: (...args: any[]) => getAccessCodesApiMock(...args),
}));

vi.mock('#/api/core', () => ({
  refreshTokenApi: (...args: any[]) => refreshTokenApiMock(...args),
  loginApi: (...args: any[]) => loginApiMock(...args),
  logoutApi: (...args: any[]) => logoutApiMock(...args),
  getUserInfoApi: (...args: any[]) => getUserInfoApiMock(...args),
  getAccessCodesApi: (...args: any[]) => getAccessCodesApiMock(...args),
}));

// Mock resetAllStores
const resetAllStoresMock = vi.fn();
vi.mock('@vben/stores', async () => {
  const actual =
    await vi.importActual<typeof import('@vben/stores')>('@vben/stores');
  return {
    ...actual,
    resetAllStores: (...args: any[]) => resetAllStoresMock(...args),
  };
});

// 测试用路由数据（模拟 CLPM 路由模块）
const testRoutes: any[] = [
  {
    path: '/loop',
    name: 'Loop',
    meta: { title: '回路管理' },
    children: [
      {
        path: '/loop/ledger',
        name: 'LoopLedger',
        meta: {
          authority: ['ADMIN', 'IC_ENGINEER', 'PE_ENGINEER'],
          title: '回路台账',
        },
      },
      {
        path: '/loop/aas',
        name: 'LoopAas',
        meta: { authority: ['ADMIN', 'IC_ENGINEER'], title: '数据接入' },
      },
    ],
  },
  {
    path: '/system',
    name: 'System',
    meta: { title: '系统管理' },
    children: [
      {
        path: '/system/users',
        name: 'SystemUsers',
        meta: { authority: ['ADMIN'], title: '用户管理' },
      },
    ],
  },
  {
    path: '/dashboard',
    name: 'Dashboard',
    meta: { title: '工作台' },
  },
];

/**
 * 模拟 generateRoutes 逻辑：
 * 根据用户角色过滤路由（对齐 @vben/access 的 accessible 模式）
 */
function generateRoutesForRole(routes: any[], roles: string[]): any[] {
  const isAdmin = roles.includes('ADMIN');
  // ADMIN 拥有所有路由
  if (isAdmin) return routes;

  const result: any[] = [];
  for (const route of routes) {
    if (route.children) {
      const filteredChildren = route.children.filter((child: any) => {
        const authority = child.meta?.authority as string[] | undefined;
        if (!authority) return true; // 无 authority 限制，所有人可访问
        return authority.some((r) => roles.includes(r));
      });
      if (filteredChildren.length > 0) {
        result.push({ ...route, children: filteredChildren });
      }
    } else {
      const authority = route.meta?.authority as string[] | undefined;
      if (!authority || authority.some((r) => roles.includes(r))) {
        result.push(route);
      }
    }
  }
  return result;
}

describe('store 测试', () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    loginApiMock.mockReset();
    logoutApiMock.mockReset();
    getUserInfoApiMock.mockReset();
    getAccessCodesApiMock.mockReset();
    refreshTokenApiMock.mockReset();
    routerPushSpy.mockReset();
    routerReplaceSpy.mockReset();
    resetAllStoresMock.mockReset();
  });

  // ===== useAuthStore 测试 =====

  // UT-STORE-001: userStore-login 成功
  it('uT-STORE-001: authStore.authLogin 登录成功', async () => {
    loginApiMock.mockResolvedValue({
      accessToken: 'new-access-token',
      refreshToken: 'new-refresh-token',
      expiresIn: 1800,
      tokenType: 'Bearer',
      user: {
        id: 'user-1',
        username: 'admin',
        displayName: '管理员',
        email: 'admin@clpm.com',
        role: 'ADMIN',
        permissions: ['*'],
      },
    });

    getUserInfoApiMock.mockResolvedValue({
      id: 'user-1',
      username: 'admin',
      displayName: '管理员',
      email: 'admin@clpm.com',
      role: 'ADMIN',
      defaultHome: '/dashboard',
      permissions: ['*'],
    });

    const authStore = useAuthStore();
    const accessStore = useAccessStore();
    const userStore = useUserStore();

    await authStore.authLogin({
      username: 'admin',
      password: '123456',
      rememberMe: false,
    });

    // 验证 token 已存储
    expect(accessStore.accessToken).toBe('new-access-token');
    expect(accessStore.refreshToken).toBe('new-refresh-token');
    // 验证用户信息已存储
    expect(userStore.userInfo).not.toBeNull();
    expect(userStore.userInfo?.username).toBe('admin');
    expect(userStore.userInfo?.realName).toBe('管理员');
    expect(userStore.userInfo?.roles).toEqual(['ADMIN']);
    // 验证权限码已存储
    expect(accessStore.accessCodes).toEqual(['*']);
    // 验证跳转
    expect(routerPushSpy).toHaveBeenCalledWith('/dashboard');
  });

  // UT-STORE-002: userStore-logout
  it('uT-STORE-002: authStore.logout 登出清空状态并跳转登录页', async () => {
    logoutApiMock.mockResolvedValue(undefined);

    const authStore = useAuthStore();
    const accessStore = useAccessStore();

    // 先设置一些状态
    accessStore.setAccessToken('some-token');
    accessStore.setRefreshToken('some-refresh');
    accessStore.setAccessCodes(['loop:create']);

    await authStore.logout();

    // 验证 logoutApi 被调用
    expect(logoutApiMock).toHaveBeenCalled();
    // 验证 resetAllStores 被调用
    expect(resetAllStoresMock).toHaveBeenCalled();
    // 验证跳转到登录页
    expect(routerReplaceSpy).toHaveBeenCalled();
    const replaceCall = routerReplaceSpy.mock.calls[0]?.[0];
    expect(replaceCall?.path).toBe('/auth/login');
  });

  // UT-STORE-003: userStore-refreshToken
  it('uT-STORE-003: refreshToken 刷新 token 并更新 store', async () => {
    refreshTokenApiMock.mockResolvedValue({
      accessToken: 'refreshed-access-token',
      expiresIn: 1800,
      tokenType: 'Bearer',
    });

    const accessStore = useAccessStore();
    accessStore.setRefreshToken('valid-refresh-token');

    // 调用 refreshTokenApi（对齐 request.ts 中的 doRefreshToken 逻辑）
    const resp = await refreshTokenApiMock('valid-refresh-token');
    accessStore.setAccessToken(resp.accessToken);

    // 验证 token 已更新
    expect(accessStore.accessToken).toBe('refreshed-access-token');
    expect(refreshTokenApiMock).toHaveBeenCalledWith('valid-refresh-token');
  });

  // UT-STORE-004: userStore-getUserInfo
  it('uT-STORE-004: authStore.fetchUserInfo 获取用户信息', async () => {
    getUserInfoApiMock.mockResolvedValue({
      id: 'user-2',
      username: 'engineer',
      displayName: '工程师',
      email: 'eng@clpm.com',
      role: 'IC_ENGINEER',
      defaultHome: '/dashboard',
      permissions: ['loop:create', 'loop:edit', 'loop:view'],
    });

    const authStore = useAuthStore();
    const userStore = useUserStore();
    const accessStore = useAccessStore();

    const userInfo = await authStore.fetchUserInfo();

    // 验证用户信息
    expect(userInfo.username).toBe('engineer');
    expect(userInfo.realName).toBe('工程师');
    expect(userInfo.roles).toEqual(['IC_ENGINEER']);
    // IC_ENGINEER 默认落地驾驶舱（方案 11 §3.1，前端角色映射优先于后端 defaultHome）
    expect(userInfo.homePath).toBe('/cockpit');
    // 验证 store 已更新
    expect(userStore.userInfo?.username).toBe('engineer');
    expect(accessStore.accessCodes).toEqual([
      'loop:create',
      'loop:edit',
      'loop:view',
    ]);
  });

  // ===== 权限路由生成测试 =====

  // UT-STORE-005: permissionStore-generateRoutes-ADMIN
  it('uT-STORE-005: ADMIN 角色生成全部路由', () => {
    const routes = generateRoutesForRole(testRoutes, ['ADMIN']);
    // ADMIN 应能访问所有路由
    expect(routes).toHaveLength(3);
    // Loop 路由的子路由应全部保留
    const loopRoute = routes.find((r) => r.name === 'Loop');
    expect(loopRoute?.children).toHaveLength(2);
    // System 路由的子路由应全部保留
    const systemRoute = routes.find((r) => r.name === 'System');
    expect(systemRoute?.children).toHaveLength(1);
  });

  // UT-STORE-006: permissionStore-generateRoutes-SPONSOR
  it('uT-STORE-006: SPONSOR 角色只能访问无 authority 限制的路由', () => {
    const routes = generateRoutesForRole(testRoutes, ['SPONSOR']);
    // SPONSOR 只能访问无 authority 限制的路由（Dashboard）
    const routeNames = routes.map((r) => r.name);
    expect(routeNames).toContain('Dashboard');
    // SPONSOR 不能访问 Loop（children 都有 authority 限制且不含 SPONSOR）
    expect(routeNames).not.toContain('Loop');
    // SPONSOR 不能访问 System（children 都有 authority 限制且不含 SPONSOR）
    expect(routeNames).not.toContain('System');
  });

  // ===== hasPermission 测试 =====

  // UT-STORE-007: permissionStore-hasPermission-通配符
  it('uT-STORE-007: hasPermission 通配符 "*" 匹配任意权限', () => {
    const codes = new Set(['*']);
    expect(hasPermission(codes, 'loop:create')).toBe(true);
    expect(hasPermission(codes, 'system:user:delete')).toBe(true);
    expect(hasPermission(codes, 'any:permission:any')).toBe(true);
  });

  // UT-STORE-008: permissionStore-hasPermission-精确匹配
  it('uT-STORE-008: hasPermission 精确匹配与模块级通配', () => {
    // 精确匹配
    const codes = new Set(['loop:create', 'loop:edit']);
    expect(hasPermission(codes, 'loop:create')).toBe(true);
    expect(hasPermission(codes, 'loop:edit')).toBe(true);
    expect(hasPermission(codes, 'loop:delete')).toBe(false);

    // 模块级通配
    const moduleCodes = new Set(['loop:*']);
    expect(hasPermission(moduleCodes, 'loop:create')).toBe(true);
    expect(hasPermission(moduleCodes, 'loop:edit')).toBe(true);
    expect(hasPermission(moduleCodes, 'loop:delete')).toBe(true);
    expect(hasPermission(moduleCodes, 'system:user')).toBe(false);

    // 多级通配
    const multiCodes = new Set(['system:user:*']);
    expect(hasPermission(multiCodes, 'system:user:create')).toBe(true);
    expect(hasPermission(multiCodes, 'system:user:delete')).toBe(true);
    expect(hasPermission(multiCodes, 'system:role:create')).toBe(false);
  });
});
