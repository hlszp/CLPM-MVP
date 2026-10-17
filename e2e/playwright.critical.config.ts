import { defineConfig } from '@playwright/test';
import config from './playwright.config.js';

// Consume prepared isolated services; never auto-start a development server.
export default defineConfig({
  ...config,
  testMatch: 'critical-business.spec.ts',
  forbidOnly: true,
  retries: 0,
  repeatEach: 1,
  workers: 1,
  webServer: undefined,
});
