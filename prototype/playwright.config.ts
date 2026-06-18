import { defineConfig, devices } from '@playwright/test';

const localDevServerUrl = 'http://127.0.0.1:4173';

export default defineConfig({
  testDir: './tests',
  timeout: 30_000,
  use: {
    baseURL: localDevServerUrl,
    trace: 'on-first-retry',
  },
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1 --port 4173 --strictPort',
    url: localDevServerUrl,
    reuseExistingServer: true,
    timeout: 60_000,
  },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'], browserName: 'chromium' } },
    { name: 'mobile', use: { ...devices['Pixel 7'], browserName: 'chromium' } },
  ],
});
