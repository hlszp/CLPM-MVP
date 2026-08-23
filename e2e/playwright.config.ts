import { defineConfig, devices } from '@playwright/test';

/**
 * CLPM Playwright E2E 测试配置
 *
 * - baseURL: 前端开发服务器，默认 http://localhost:5666，
 *   可用 E2E_BASE_URL 覆盖（MVP 隔离端口为 http://localhost:15666）
 * - 仅启用 chromium 项目
 * - webServer 自动启动前端开发服务（pnpm dev，已运行时复用）
 * - 后端 API 需手动启动（默认 7101，MVP 隔离端口 17101，
 *   fixtures/auth.ts 中可用 E2E_API_BASE_URL 覆盖）
 */
const WEB_BASE_URL = process.env.E2E_BASE_URL ?? 'http://localhost:5666';

export default defineConfig({
  testDir: './tests',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : 1,
  reporter: [
    ['list'],
    ['html', { outputFolder: 'playwright-report', open: 'never' }],
  ],
  outputDir: 'test-results',
  expect: {
    timeout: 10_000,
  },
  timeout: 60_000,
  use: {
    baseURL: WEB_BASE_URL,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
    locale: 'zh-CN',
    timezoneId: 'Asia/Shanghai',
  },
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 1440, height: 900 },
      },
    },
  ],
  webServer: {
    command: 'cd ../frontend/apps/web-antd && pnpm dev',
    url: WEB_BASE_URL,
    reuseExistingServer: true,
    timeout: 120_000,
    cwd: process.cwd(),
  },
});
