/**
 * E2E 多角色权限验证测试
 *
 * 覆盖用例：
 * - E2E-ROLE-001: SPONSOR 菜单限制（仅可见工作台/性能评估/诊断中心）
 * - E2E-ROLE-002: SPONSOR 直接访问受限页 → 跳转 403 或重定向
 * - E2E-ROLE-003: PE_ENGINEER 无整定菜单
 * - E2E-ROLE-004: EXPERT 有整定权限
 * - E2E-ROLE-005: IC_ENGINEER 全业务权限
 *
 * 角色权限对齐：
 *   frontend/apps/web-antd/src/router/routes/modules/*.ts
 *   - SPONSOR：仅 dashboard / metric(看板/排行/统计) / diagnosis(列表/波形/统计)
 *   - PE_ENGINEER：dashboard / loop(查看) / metric(查看) / diagnosis(查看+tracker) / 无 tuning
 *   - EXPERT：dashboard / metric(查看) / diagnosis(查看+tracker) / tuning(全部)
 *   - IC_ENGINEER：全部模块（含配置）
 *   - ADMIN：全部模块
 */
import { test, expect } from '../fixtures/auth.js';

/** 等待菜单渲染完成 */
async function waitForMenu(page: import('@playwright/test').Page) {
  await page.waitForLoadState('networkidle');
  // Vben Admin 菜单容器
  await page
    .locator('.vben-menu, .ant-menu, [class*="menu"]')
    .first()
    .waitFor({ state: 'visible', timeout: 15_000 })
    .catch(() => {
      // 菜单可能采用不同 class，兜底等待任意菜单项出现
    });
}

/** 获取侧边栏可见菜单文本列表 */
async function getMenuTexts(page: import('@playwright/test').Page): Promise<string[]> {
  // Vben BasicLayout 侧边栏菜单项
  const items = page.locator(
    '.vben-menu .vben-menu-item, .ant-menu .ant-menu-item, .vben-menu-submenu-title, .ant-menu-submenu-title',
  );
  const count = await items.count();
  const texts: string[] = [];
  for (let i = 0; i < count; i++) {
    const text = (await items.nth(i).innerText()).trim();
    if (text) texts.push(text);
  }
  return texts;
}

test.describe('多角色权限验证 E2E', () => {
  test('E2E-ROLE-001: SPONSOR 菜单限制', async ({ page, loginAs }) => {
    await loginAs('SPONSOR');
    await page.goto('/dashboard');
    await page.waitForURL(/\/dashboard/, { timeout: 30_000 });
    await waitForMenu(page);

    const menuTexts = await getMenuTexts(page);
    const menuText = menuTexts.join('|');

    // SPONSOR 应可见：工作台、性能评估、诊断中心
    expect(menuText).toContain('工作台');
    expect(menuText).toContain('性能评估');
    expect(menuText).toContain('诊断中心');

    // SPONSOR 不应可见：回路管理、回路整定、系统管理
    expect(menuText).not.toContain('回路管理');
    expect(menuText).not.toContain('回路整定');
    expect(menuText).not.toContain('系统管理');
  });

  test('E2E-ROLE-002: SPONSOR 直接访问受限页 → 403 或重定向', async ({
    page,
    loginAs,
  }) => {
    await loginAs('SPONSOR');

    // 手动访问 /loop/ledger（SPONSOR 无权限）
    await page.goto('/loop/ledger');

    // 预期：跳转 403 页面 或 重定向到默认首页
    await page.waitForLoadState('networkidle');
    const url = page.url();
    const bodyText = (await page.locator('body').innerText()).toLowerCase();

    const is403 =
      url.includes('/403') ||
      url.includes('forbidden') ||
      bodyText.includes('403') ||
      bodyText.includes('无权') ||
      bodyText.includes('禁止访问');
    const isRedirected = url.includes('/dashboard') || url.includes('/auth/login');

    expect(is403 || isRedirected).toBeTruthy();
  });

  test('E2E-ROLE-003: PE_ENGINEER 无整定菜单', async ({ page, loginAs }) => {
    await loginAs('PE_ENGINEER');
    await page.goto('/dashboard');
    await page.waitForURL(/\/dashboard/, { timeout: 30_000 });
    await waitForMenu(page);

    const menuTexts = await getMenuTexts(page);
    const menuText = menuTexts.join('|');

    // PE_ENGINEER 应可见：工作台、回路管理（查看）、性能评估、诊断中心
    expect(menuText).toContain('工作台');
    expect(menuText).toContain('回路管理');

    // PE_ENGINEER 不应可见：回路整定
    expect(menuText).not.toContain('回路整定');
  });

  test('E2E-ROLE-004: EXPERT 有整定权限', async ({ page, loginAs }) => {
    await loginAs('EXPERT');
    await page.goto('/dashboard');
    await page.waitForURL(/\/dashboard/, { timeout: 30_000 });
    await waitForMenu(page);

    const menuTexts = await getMenuTexts(page);
    const menuText = menuTexts.join('|');

    // EXPERT 应可见：回路整定
    expect(menuText).toContain('回路整定');

    // 验证可访问整定工作台
    await page.goto('/tuning/workbench');
    await page.waitForLoadState('networkidle');
    // 不应跳转到 403
    expect(page.url()).not.toContain('/403');
    expect(page.url()).not.toContain('/auth/login');
  });

  test('E2E-ROLE-005: IC_ENGINEER 全业务权限', async ({ page, loginAs }) => {
    await loginAs('IC_ENGINEER');
    await page.goto('/dashboard');
    await page.waitForURL(/\/dashboard/, { timeout: 30_000 });
    await waitForMenu(page);

    const menuTexts = await getMenuTexts(page);
    const menuText = menuTexts.join('|');

    // IC_ENGINEER 应可见全部业务模块
    expect(menuText).toContain('工作台');
    expect(menuText).toContain('回路管理');
    expect(menuText).toContain('性能评估');
    expect(menuText).toContain('诊断中心');
    expect(menuText).toContain('回路整定');
    expect(menuText).toContain('系统管理');
  });
});
