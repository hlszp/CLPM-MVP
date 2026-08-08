/**
 * E2E 旧路由兼容性基线（V62-P0-037 + IA 重构 Phase A + Phase D 单页整合）
 *
 * 覆盖旧路由 redirect：
 * - /tuning/model        → /tuning/detail（Phase D 单页整合）
 * - /tuning/algorithm    → /tuning/detail
 * - /tuning/simulation   → /tuning/detail
 * - /tuning/flow/*       → /tuning/detail（Phase D 前的中间路由也归并）
 * - /diagnosis/records   → /diagnosis/tasks?tab=history
 * - IA Phase A 配置集中化迁移：
 *   /loop/aas-sync       → /config/link
 *   /metric/config       → /config/metric
 *   /diagnosis/config    → /config/diagnosis
 *   /system/pid-template → /config/link
 *   /loop/data           → /config/datasource
 *
 * 验证维度（tuning/diagnosis 每路由 3 个用例，config 每路由 1 个直链用例）：
 * - 直链访问旧路由 → URL 正确 redirect 到新路由，页面不白屏
 * - 硬刷新（page.reload）后 URL 保持，页面不白屏
 * - 前进后退导航正常（历史栈未断裂）
 *
 * 依据：UI/UX v6.1「稳定元素根」防白屏（vben v-show + Transition + KeepAlive）
 *       P1-020 旧路由 redirect + hideInMenu 兼容书签
 *       IA 重构 Phase A §3.3 配置集中化（config.ts legacy redirect 段）
 *       IA 重构 Phase D §4.4.2 整定单页整合（tuning.ts legacy redirect 段）
 */
import { test, expect } from '../fixtures/auth.js';

/** 整定模块旧路由 → 新路由映射（Phase D：统一重定向到 /tuning/detail 单页） */
const TUNING_LEGACY_ROUTES: Array<{ legacy: string; target: RegExp }> = [
  { legacy: '/tuning/model', target: /\/tuning\/detail/ },
  { legacy: '/tuning/algorithm', target: /\/tuning\/detail/ },
  { legacy: '/tuning/simulation', target: /\/tuning\/detail/ },
  { legacy: '/tuning/flow/model', target: /\/tuning\/detail/ },
  { legacy: '/tuning/flow/algorithm', target: /\/tuning\/detail/ },
  { legacy: '/tuning/flow/simulation', target: /\/tuning\/detail/ },
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

/**
 * IA 重构 Phase A 配置集中化迁移的 legacy redirect。
 * 旧路径 → /config/* 新路径，对齐 config.ts legacy redirect 段。
 */
const CONFIG_LEGACY_ROUTES: Array<{ legacy: string; target: RegExp }> = [
  { legacy: '/loop/aas-sync', target: /\/config\/link/ },
  { legacy: '/metric/config', target: /\/config\/metric/ },
  { legacy: '/diagnosis/config', target: /\/config\/diagnosis/ },
  { legacy: '/system/pid-template', target: /\/config\/link/ },
  { legacy: '/loop/data', target: /\/config\/datasource/ },
];

test.describe('旧路由兼容 - 配置集中化迁移（IA 重构 Phase A）', () => {
  test.beforeEach(async ({ loginAs }) => {
    // 配置模块仅 ADMIN 可访问
    await loginAs('ADMIN');
  });

  for (const { legacy, target } of CONFIG_LEGACY_ROUTES) {
    test(`E2E-ROUTE-CONFIG: ${legacy} 直链 redirect 不白屏`, async ({ page }) => {
      await page.goto(legacy, { waitUntil: 'domcontentloaded' });
      await expect(page).toHaveURL(target, { timeout: 15_000 });
      // 防白屏：body 必须有非空可见内容
      await expect(page.locator('body')).not.toBeEmpty();
      const text = await page.locator('body').innerText();
      expect(text.trim().length).toBeGreaterThan(0);
    });
  }
});

/**
 * 工作台快捷导航（UI/UX 整改 B1，2026-08-08 用户决策调整）
 *
 * 回归背景：概览区"历史"按钮曾跳转不存在的 /loop/history（必 404）。
 * 最终决策：概览区只保留"趋势"且改为页内弹窗（LoopTrendModal，
 * 与回路实时趋势弹窗同组件）；"历史"按钮下线。
 */
test.describe('工作台概览区按钮（整改 B1 调整）', () => {
  test.beforeEach(async ({ loginAs }) => {
    await loginAs('ADMIN');
  });

  test('E2E-ROUTE-WB: "历史"按钮已下线，概览区仅保留"趋势"', async ({
    page,
  }) => {
    await page.goto('/loop/workbench', { waitUntil: 'domcontentloaded' });
    // antd 双汉字按钮可访问名带空格（"历 史"），用正则兼容
    const trendBtn = page.getByRole('button', { name: /趋\s*势/ });
    await expect(trendBtn).toBeVisible({ timeout: 15_000 });
    await expect(
      page.getByRole('button', { name: /历\s*史/ }),
    ).toHaveCount(0);
  });

  test('E2E-ROUTE-WB: "趋势"按钮打开页内趋势弹窗（不跳路由）', async ({
    page,
  }) => {
    await page.goto('/loop/workbench', { waitUntil: 'domcontentloaded' });
    const trendBtn = page.getByRole('button', { name: /趋\s*势/ });
    await expect(trendBtn).toBeVisible({ timeout: 15_000 });
    await trendBtn.click();
    // 弹窗打开：标题含"趋势 - <位号>"，含时间范围切换
    await expect(page.locator('.ant-modal').first()).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByText(/趋势 - /)).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText('时间范围：')).toBeVisible();
    // 不跳转路由：URL 仍停留在工作台
    await expect(page).toHaveURL(/\/loop\/workbench/);
  });
});
