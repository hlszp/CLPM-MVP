/**
 * CLPM E2E 测试登录 Fixture
 *
 * 通过 UI 操作登录（填写用户名密码 + 点击登录按钮），
 * 让前端自行管理 token 持久化，避免依赖 localStorage key 格式。
 */
import { test as base, type Page, type APIRequestContext } from '@playwright/test';

/** 后端 API 基础地址 */
export const API_BASE_URL = 'http://localhost:8001/api/v1';

/** 前端 baseURL */
export const WEB_BASE_URL = 'http://localhost:5666';

/** 登录页路径 */
export const LOGIN_PATH = '/auth/login';

/** CLPM 5 类角色账户（密码统一为 admin123） */
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

/**
 * 通过 UI 操作登录：填写用户名密码 + 点击登录按钮
 */
export async function loginViaUI(
  page: Page,
  username: string,
  password: string,
): Promise<void> {
  await page.goto(LOGIN_PATH, { waitUntil: 'networkidle' });
  await page.waitForTimeout(1000);

  // 填写用户名
  const usernameInput = page.getByPlaceholder('请输入用户名');
  await usernameInput.fill(username);

  // 填写密码
  const passwordInput = page.getByPlaceholder('请输入密码');
  await passwordInput.fill(password);

  // 点击登录按钮（按钮在 form 外，用精确文本匹配）
  const loginButton = page.getByText('登录', { exact: true });
  await loginButton.click();

  // 等待跳转离开登录页（最多 15 秒）
  await page.waitForURL((url) => !url.pathname.includes('/auth/login'), {
    timeout: 15_000,
  });
  await page.waitForLoadState('networkidle');
}

/**
 * 通过 API 登录获取 token（用于需要 token 的辅助场景）
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
    throw new Error(`登录接口请求失败: HTTP ${resp.status()} ${resp.statusText()}`);
  }

  const body = await resp.json();
  if (body.code !== 0 && body.code !== '0') {
    throw new Error(`登录失败: ${body.message ?? '未知错误'}`);
  }
  return body.data;
}

/**
 * 清除登录态：导航到前端页面后清除 localStorage
 */
export async function clearAccessToken(page: Page): Promise<void> {
  // 先确保在前端域名下，才能访问 localStorage
  if (!page.url().includes('localhost:5666')) {
    await page.goto(WEB_BASE_URL, { waitUntil: 'domcontentloaded' });
  }
  await page.evaluate(() => {
    localStorage.clear();
    sessionStorage.clear();
  });
}

/** AuthFixture 暴露给测试用例的能力 */
export interface AuthFixture {
  /** 通过 UI 登录指定角色 */
  loginAs: (role: ClpmRole) => Promise<void>;
  /** 清除当前登录态 */
  logout: () => Promise<void>;
}

export const test = base.extend<AuthFixture>({
  loginAs: async ({ page }, use) => {
    const loginAs = async (role: ClpmRole): Promise<void> => {
      const account = ACCOUNTS[role];
      await loginViaUI(page, account.username, account.password);
    };
    await use(loginAs);
  },

  logout: async ({ page }, use) => {
    const logout = async (): Promise<void> => {
      await clearAccessToken(page);
    };
    await use(logout);
  },
});

export { expect } from '@playwright/test';
