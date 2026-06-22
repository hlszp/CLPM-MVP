/**
 * CLPM E2E 测试登录 Fixture
 *
 * 通过后端 /api/v1/auth/login 接口获取 accessToken / refreshToken，
 * 然后将其写入前端 Pinia 持久化的 localStorage 中，
 * 让路由守卫在下次导航时识别为已登录状态。
 *
 * localStorage key 格式：`${namespace}-core-access`
 *   - namespace = `${VITE_APP_NAMESPACE}-${VITE_APP_VERSION}-${env}`
 *   - 开发环境示例：`clpm-web-antd-5.7.0-dev-core-access`
 *
 * 由于版本号可能变化，fixture 采用「先访问页面触发 Pinia 初始化 →
 * 扫描 localStorage 找到 key → 写入 token → 重新导航」的策略。
 */
import { test as base, type Page, type APIRequestContext } from '@playwright/test';

/** 后端 API 基础地址（对齐 .env.development VITE_GLOB_API_URL） */
export const API_BASE_URL = 'http://localhost:8001/api/v1';

/** 前端 baseURL */
export const WEB_BASE_URL = 'http://localhost:5666';

/** 登录页路径 */
export const LOGIN_PATH = '/auth/login';

/** CLPM 5 类角色账户（密码统一为 admin123，对齐 db/postgresql/02_seed_data.sql） */
export const ACCOUNTS = {
  ADMIN: { username: 'admin', password: 'admin123' },
  IC_ENGINEER: { username: 'ic_engineer', password: 'admin123' },
  PE_ENGINEER: { username: 'pe_engineer', password: 'admin123' },
  SPONSOR: { username: 'sponsor', password: 'admin123' },
  EXPERT: { username: 'expert', password: 'admin123' },
} as const;

export type ClpmRole = keyof typeof ACCOUNTS;

/** 登录接口返回的 user 信息 */
export interface LoginUserInfo {
  id: string;
  username: string;
  displayName: string;
  email: string;
  role: string;
  permissions: string[];
  defaultHome: string;
  lastLoginAt?: string;
}

/** 登录接口返回结果 */
export interface LoginResult {
  accessToken: string;
  refreshToken: string;
  tokenType: string;
  expiresIn: number;
  user: LoginUserInfo;
}

/** 后端统一响应格式 */
interface ApiResponse<T> {
  code: number | string;
  message: string;
  data: T;
}

/**
 * 调用后端登录接口获取 token
 */
export async function loginViaApi(
  request: APIRequestContext,
  username: string,
  password: string,
): Promise<LoginResult> {
  const resp = await request.post(`${API_BASE_URL}/auth/login`, {
    data: { username, password, rememberMe: false },
    headers: { 'Content-Type': 'application/json' },
  });

  if (!resp.ok()) {
    throw new Error(
      `登录接口请求失败: HTTP ${resp.status()} ${resp.statusText()}`,
    );
  }

  const body = (await resp.json()) as ApiResponse<LoginResult>;
  if (body.code !== 0 && body.code !== '0') {
    throw new Error(`登录失败: ${body.message ?? '未知错误'}`);
  }
  return body.data;
}

/**
 * 调用后端登出接口
 */
export async function logoutViaApi(
  request: APIRequestContext,
  accessToken: string,
): Promise<void> {
  try {
    await request.post(`${API_BASE_URL}/auth/logout`, {
      headers: { Authorization: `Bearer ${accessToken}` },
    });
  } catch {
    // 忽略登出错误
  }
}

/**
 * 获取当前用户信息（/auth/me）
 */
export async function getCurrentUser(
  request: APIRequestContext,
  accessToken: string,
): Promise<LoginUserInfo> {
  const resp = await request.get(`${API_BASE_URL}/auth/me`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!resp.ok()) {
    throw new Error(`获取用户信息失败: HTTP ${resp.status()}`);
  }
  const body = (await resp.json()) as ApiResponse<LoginUserInfo>;
  return body.data;
}

/**
 * 将 accessToken / refreshToken 写入前端 Pinia 持久化的 localStorage。
 *
 * 策略：
 * 1. 先访问任意页面（/auth/login）触发 Vue 应用启动与 Pinia store 初始化
 * 2. 扫描 localStorage 找到 key 以 `-core-access` 结尾的项
 * 3. 解析现有 value，合并 token 字段后写回
 * 4. 返回 key 供调用方在重新导航前使用
 */
export async function injectAccessToken(
  page: Page,
  accessToken: string,
  refreshToken: string,
): Promise<string> {
  // 1. 触发应用启动
  await page.goto(LOGIN_PATH);
  await page.waitForLoadState('domcontentloaded');

  // 2. 扫描 localStorage 找到 access store 的 key
  const accessKey = await page.evaluate(() => {
    const keys = Object.keys(localStorage);
    const match = keys.find((k) => k.endsWith('-core-access'));
    return match ?? '';
  });

  if (!accessKey) {
    throw new Error(
      '未找到 Pinia access store 的 localStorage key，前端可能未正确启动',
    );
  }

  // 3. 写入 token（保留其它已持久化字段）
  await page.evaluate(
    ({ key, accessToken, refreshToken }) => {
      const raw = localStorage.getItem(key);
      let state: Record<string, unknown> = {};
      if (raw) {
        try {
          state = JSON.parse(raw) as Record<string, unknown>;
        } catch {
          state = {};
        }
      }
      state.accessToken = accessToken;
      state.refreshToken = refreshToken;
      state.isAccessChecked = false;
      localStorage.setItem(key, JSON.stringify(state));
    },
    { key: accessKey, accessToken, refreshToken },
  );

  return accessKey;
}

/**
 * 清除登录态：删除 access store 的 localStorage 项
 */
export async function clearAccessToken(page: Page): Promise<void> {
  await page.evaluate(() => {
    const keys = Object.keys(localStorage);
    for (const k of keys) {
      if (k.endsWith('-core-access') || k.endsWith('-core-user')) {
        localStorage.removeItem(k);
      }
    }
  });
}

/** AuthFixture 暴露给测试用例的能力 */
export interface AuthFixture {
  /** 通过 API 登录指定角色并注入 token 到 localStorage */
  loginAs: (role: ClpmRole) => Promise<LoginResult>;
  /** 清除当前登录态 */
  logout: () => Promise<void>;
}

/** 当前登录态上下文（内部共享） */
interface AuthContext {
  current: LoginResult | null;
}

export const test = base.extend<AuthFixture & { _authContext: AuthContext }>({
  _authContext: async ({}, use) => {
    const ctx: AuthContext = { current: null };
    await use(ctx);
  },

  loginAs: async ({ page, request, _authContext }, use) => {
    const ctx = _authContext;

    const loginAs = async (role: ClpmRole): Promise<LoginResult> => {
      const account = ACCOUNTS[role];
      const result = await loginViaApi(
        request,
        account.username,
        account.password,
      );
      ctx.current = result;
      await injectAccessToken(page, result.accessToken, result.refreshToken);
      return result;
    };

    await use(loginAs);
  },

  logout: async ({ page, request, _authContext }, use) => {
    const ctx = _authContext;

    const logout = async () => {
      if (ctx.current) {
        await logoutViaApi(request, ctx.current.accessToken);
        ctx.current = null;
      }
      await clearAccessToken(page);
    };

    await use(logout);
  },
});

export { expect } from '@playwright/test';
