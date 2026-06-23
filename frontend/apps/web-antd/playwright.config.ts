import type { PlaywrightTestConfig } from '@playwright/test';

import { devices } from '@playwright/test';

/**
 * Playwright E2E 配置（S3-C6: CI 集成 E2E 测试）
 *
 * 仅包含基本冒烟测试，首次集成使用 continue-on-error: true 标记为非阻塞。
 */
const config: PlaywrightTestConfig = {
  expect: {
    timeout: 5000,
  },
  forbidOnly: !!process.env.CI,
  outputDir: 'node_modules/.e2e/test-results/',
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
      },
    },
  ],
  reporter: [['list']],
  retries: process.env.CI ? 1 : 0,
  testDir: './__tests__/e2e',
  timeout: 30 * 1000,
  use: {
    actionTimeout: 0,
    baseURL: 'http://localhost:5555',
    headless: !!process.env.CI,
    trace: 'retain-on-failure',
  },

  webServer: {
    command: process.env.CI ? 'pnpm preview --port 5555' : 'pnpm dev',
    port: 5555,
    reuseExistingServer: !process.env.CI,
    timeout: 120 * 1000,
  },

  workers: process.env.CI ? 1 : undefined,
};

export default config;
