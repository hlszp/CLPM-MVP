/**
 * E2E 旧路由兼容性基线（V62-P0-037）
 *
 * 覆盖 4 个旧路由 redirect：
 * - /tuning/model        → /tuning/flow/model
 * - /tuning/algorithm    → /tuning/flow/algorithm
 * - /tuning/simulation   → /tuning/flow/simulation
 * - /diagnosis/records   → /diagnosis/tasks?tab=history
 *
 * 验证维度（每路由 3 个用例，共 12 个）：
 * - 直链访问旧路由 → URL 正确 redirect 到新路由，页面不白屏
 * - 硬刷新（page.reload）后 URL 保持，页面不白屏
 * - 前进后退导航正常（历史栈未断裂）
 *
 * 依据：UI/UX v6.1「稳定元素根」防白屏（vben v-show + Transition + KeepAlive）
 *       P1-020 旧路由 redirect + hideInMenu 兼容书签
 */
import { test, expect } from '../fixtures/auth.js';

/** 整定模块旧路由 → 新路由映射 */
const TUNING_LEGACY_ROUTES: Array<{ legacy: string; target: RegExp }> = [
  { legacy: '/tuning/model', target: /\/tuning\/flow\/model/ },
  { legacy: '/tuning/algorithm', target: /\/tuning\/flow\/algorithm/ },
  { legacy: '/tuning/simulation', target: /\/tuning\/flow\/simulation/ },
];

/** 诊断模块旧路由 → 新路由映射（redirect 目标含 query string） */
const DIAGNOSIS_LEGACY_ROUTES: Array<{ legacy: string; target: RegExp }> = [
  { legacy: '/diagnosis/records', target: /\/diagnosis\/tasks(\?.*)?/ },
];

test.describe('旧路由兼容 - 回路整定（V62-P0-037）', () => {
  test.beforeEach(async ({ loginAs }) => {
    // 整定模块需要 ADMIN / IC_ENGINEER / EXPERT 权限
    await loginAs('ADMIN');
  });

  for (const { legacy, target } of TUNING_LEGACY_ROUTES) {
    test(`E2E-ROUTE-TUNE: ${legacy} 直链 redirect 不白屏`, async ({ page }) => {
      await page.goto(legacy, { waitUntil: 'domcontentloaded' });
      await expect(page).toHaveURL(target, { timeout: 15_000 });
      // 防白屏：body 必须有非空可见内容
      await expect(page.locator('body')).not.toBeEmpty();
      const text = await page.locator('body').innerText();
      expect(text.trim().length).toBeGreaterThan(0);
    });

    test(`E2E-ROUTE-TUNE: ${legacy} 硬刷新后不白屏`, async ({ page }) => {
      await page.goto(legacy, { waitUntil: 'domcontentloaded' });
      await expect(page).toHaveURL(target, { timeout: 15_000 });
      // 硬刷新：模拟用户按 F5 / 点击刷新按钮
      await page.reload({ waitUntil: 'domcontentloaded' });
      await expect(page).toHaveURL(target, { timeout: 15_000 });
      await expect(page.locator('body')).not.toBeEmpty();
      const text = await page.locator('body').innerText();
      expect(text.trim().length).toBeGreaterThan(0);
    });

    test(`E2E-ROUTE-TUNE: ${legacy} 前进后退导航正常`, async ({ page }) => {
      // 先建立历史栈：访问 workbench → 访问旧路由 → 回退 → 前进
      await page.goto('/tuning/workbench', { waitUntil: 'domcontentloaded' });
      await page.goto(legacy, { waitUntil: 'domcontentloaded' });
      await expect(page).toHaveURL(target, { timeout: 15_000 });

      // 回退到 workbench
      await page.goBack({ waitUntil: 'domcontentloaded' });
      await expect(page).toHaveURL(/\/tuning\/workbench/, { timeout: 15_000 });

      // 前进回新路由
      await page.goForward({ waitUntil: 'domcontentloaded' });
      await expect(page).toHaveURL(target, { timeout: 15_000 });
      await expect(page.locator('body')).not.toBeEmpty();
    });
  }
});

test.describe('旧路由兼容 - 诊断中心（V62-P0-037）', () => {
  test.beforeEach(async ({ loginAs }) => {
    // 诊断任务页 IC_ENGINEER 可访问
    await loginAs('IC_ENGINEER');
  });

  for (const { legacy, target } of DIAGNOSIS_LEGACY_ROUTES) {
    test(`E2E-ROUTE-DIAG: ${legacy} 直链 redirect 不白屏`, async ({ page }) => {
      await page.goto(legacy, { waitUntil: 'domcontentloaded' });
      await expect(page).toHaveURL(target, { timeout: 15_000 });
      await expect(page.locator('body')).not.toBeEmpty();
      const text = await page.locator('body').innerText();
      expect(text.trim().length).toBeGreaterThan(0);
    });

    test(`E2E-ROUTE-DIAG: ${legacy} 硬刷新后不白屏`, async ({ page }) => {
      await page.goto(legacy, { waitUntil: 'domcontentloaded' });
      await expect(page).toHaveURL(target, { timeout: 15_000 });
      await page.reload({ waitUntil: 'domcontentloaded' });
      await expect(page).toHaveURL(target, { timeout: 15_000 });
      await expect(page.locator('body')).not.toBeEmpty();
      const text = await page.locator('body').innerText();
      expect(text.trim().length).toBeGreaterThan(0);
    });

    test(`E2E-ROUTE-DIAG: ${legacy} 前进后退导航正常`, async ({ page }) => {
      // 先建立历史栈：访问 overview → 访问旧路由 → 回退 → 前进
      await page.goto('/diagnosis/overview', { waitUntil: 'domcontentloaded' });
      await page.goto(legacy, { waitUntil: 'domcontentloaded' });
      await expect(page).toHaveURL(target, { timeout: 15_000 });

      // 回退到 overview
      await page.goBack({ waitUntil: 'domcontentloaded' });
      await expect(page).toHaveURL(/\/diagnosis\/overview/, { timeout: 15_000 });

      // 前进回新路由
      await page.goForward({ waitUntil: 'domcontentloaded' });
      await expect(page).toHaveURL(target, { timeout: 15_000 });
      await expect(page.locator('body')).not.toBeEmpty();
    });
  }
});
