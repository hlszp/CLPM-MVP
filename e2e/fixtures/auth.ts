/**
 * CLPM E2E 测试登录 Fixture
 *
 * 通过 UI 操作登录（填写用户名密码 + 点击登录按钮），
 * 让前端自行管理 token 持久化，避免依赖 localStorage key 格式。
 */
import { test as base, type Page, type APIRequestContext } from '@playwright/test';

/** 后端 API 基础地址（可用 E2E_API_BASE_URL 覆盖；MVP 隔离端口为 17101） */
export const API_BASE_URL =
  process.env.E2E_API_BASE_URL ?? 'http://localhost:7101/api/v1';

/** 前端 baseURL（可用 E2E_BASE_URL 覆盖；MVP 隔离端口为 15666） */
export const WEB_BASE_URL =
  process.env.E2E_BASE_URL ?? 'http://localhost:5666';

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
 * Token 缓存：同一角色的 JWT 在 TTL 内复用，避免连续 E2E 测试
 * 触发登录接口速率限制（10 次/分钟/IP + 10 次/分钟/账号）。
 * JWT 有效期 30 分钟，缓存 TTL 25 分钟（留 5 分钟缓冲）。
 */
const _tokenCache = new Map<string, { result: LoginResult; expiresAt: number }>();
const TOKEN_CACHE_TTL = 25 * 60 * 1000;

/** 清除 token 缓存（登出或测试结束时调用，避免复用失效 token） */
export function clearTokenCache(username?: string): void {
  if (username) {
    _tokenCache.delete(username);
  } else {
    _tokenCache.clear();
  }
}

/**
 * 通过 UI 操作登录：填写用户名密码 + 点击登录按钮
 *
 * v6.2 P1-023：登录等待从 networkidle 改为 domcontentloaded，
 * 避免 SignalR 实时订阅持续网络活动导致 networkidle 永不触发；
 * 超时从 15s 提升至 30s 以容忍后端冷启动。
 */
export async function loginViaUI(
  page: Page,
  username: string,
  password: string,
): Promise<void> {
  await page.goto(LOGIN_PATH, { waitUntil: 'domcontentloaded' });
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

  // 等待跳转离开登录页（最多 30 秒，容忍后端冷启动/DB 连接池预热）
  await page.waitForURL((url) => !url.pathname.includes('/auth/login'), {
    timeout: 30_000,
  });
  await page.waitForLoadState('domcontentloaded');
}

/**
 * 混合登录：API 预取 token + 浏览器端 route mock 加速
 *
 * v6.2 P1-023：后端在连续 E2E 测试中因 SignalR 订阅/Celery 任务
 * 逐渐变慢，纯 UI 登录在后期测试中超时。此方案：
 * 1. 通过 API 直接获取 token（单次 HTTP 请求，比 UI 流程快）
 * 2. Mock 浏览器端 /auth/login 和 /auth/me 响应（前端正常流程处理 token）
 * 3. 填写表单并提交（前端走完整鉴权流程，token 持久化由前端管理）
 */
export async function loginViaUIWithMock(
  page: Page,
  request: APIRequestContext,
  username: string,
  password: string,
): Promise<void> {
  // 1. API 预取 token（缓存命中直接返回，未命中走 API + 429 重试）
  const loginResult = await loginViaApi(request, username, password);

  // 2. Mock 浏览器端 API 响应，避免后端慢请求
  await page.route(/\/api\/v1\/auth\/login/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ code: 0, message: 'success', data: loginResult }),
    });
  });
  await page.route(/\/api\/v1\/auth\/me/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ code: 0, message: 'success', data: loginResult.user }),
    });
  });

  // 3. 填写表单并提交（前端走完整鉴权流程）
  await page.goto(LOGIN_PATH, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(500);
  await page.getByPlaceholder('请输入用户名').fill(username);
  await page.getByPlaceholder('请输入密码').fill(password);
  await page.getByText('登录', { exact: true }).click();

  // 等待跳转离开登录页（mock 了 login/me，但菜单/权限等请求仍走后端，需留足时间）
  await page.waitForURL((url) => !url.pathname.includes('/auth/login'), {
    timeout: 30_000,
  });
}

/**
 * 通过 API 登录获取 token（带缓存 + 429 重试）
 *
 * v6.2 P1-023：后端登录限流 10 次/分钟，60 个 E2E 测试频繁登录会触发 429。
 * - 同一用户名的 token 在 25 分钟内复用（JWT 有效期 30 分钟）
 * - 429 速率限制时等待 5s 重试，最多 3 次
 * - 其他错误（超时等）等待 2s 重试
 */
export async function loginViaApi(
  request: APIRequestContext,
  username: string,
  password: string,
): Promise<LoginResult> {
  // 1. 缓存命中直接返回
  const cached = _tokenCache.get(username);
  if (cached && cached.expiresAt > Date.now()) {
    return cached.result;
  }

  // 2. API 请求（带重试）
  let lastError: unknown;
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const resp = await request.post(`${API_BASE_URL}/auth/login`, {
        data: { username, password, rememberMe: false },
        headers: { 'Content-Type': 'application/json' },
        // 30s 与 loginViaUI 一致：E2E 全量跑时后端因 SignalR 订阅/Celery 任务
        // 偶发慢响应（P2-018 已记录），15s 在全量压力下不够。
        timeout: 30_000,
      });

      if (resp.status() === 429) {
        throw new Error('登录接口速率限制 (429)');
      }
      if (!resp.ok()) {
        throw new Error(`登录接口请求失败: HTTP ${resp.status()} ${resp.statusText()}`);
      }

      const body = await resp.json();
      if (body.code !== 0 && body.code !== '0') {
        throw new Error(`登录失败: ${body.message ?? '未知错误'}`);
      }

      const result = body.data as LoginResult;
      // 3. 缓存 token（25 分钟 TTL）
      _tokenCache.set(username, {
        result,
        expiresAt: Date.now() + TOKEN_CACHE_TTL,
      });
      return result;
    } catch (e) {
      lastError = e;
      if (attempt < 2) {
        const is429 = e instanceof Error && e.message.includes('429');
        await new Promise((r) => setTimeout(r, is429 ? 5000 : 2000));
      }
    }
  }
  throw lastError;
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
  // 清除 token 缓存，避免登出后复用失效 token
  clearTokenCache();
}

/** AuthFixture 暴露给测试用例的能力 */
export interface AuthFixture {
  /** 通过 UI 登录指定角色 */
  loginAs: (role: ClpmRole) => Promise<void>;
  /** 清除当前登录态 */
  logout: () => Promise<void>;
}

export const test = base.extend<AuthFixture>({
  loginAs: async ({ page, request }, use) => {
    const loginAs = async (role: ClpmRole): Promise<void> => {
      const account = ACCOUNTS[role];
      // P2-03：跳过首次登录 Onboarding Tour，避免引导 Modal 拦截 E2E 点击
      await page.addInitScript(() => {
        localStorage.setItem('clpm-onboarding-completed', 'true');
      });
      // v6.2 P1-023：使用 mock 登录避免后端在连续 E2E 测试中变慢导致超时
      await loginViaUIWithMock(page, request, account.username, account.password);
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

// P2-018：测试失败时自动输出 PG 连接数快照，辅助区分代码回归 vs 环境问题
test.afterEach(async ({ request }, testInfo) => {
  if (testInfo.status === 'passed' || testInfo.status === testInfo.expectedStatus) {
    return;
  }
  try {
    const resp = await request.get(`${API_BASE_URL}/health/db-connections`, {
      timeout: 3_000,
    });
    if (resp.ok()) {
      const data = await resp.json();
      const snapshot = {
        testTitle: testInfo.title,
        testStatus: testInfo.status,
        timestamp: new Date().toISOString(),
        ...data,
      };
      const snapshotPath = testInfo.outputPath('connection-snapshot.json');
      const fs = await import('node:fs');
      fs.writeFileSync(snapshotPath, JSON.stringify(snapshot, null, 2));
    }
  } catch {
    // 静默失败：连接快照是辅助诊断信息，不干扰测试结果
  }
});

export { expect } from '@playwright/test';
