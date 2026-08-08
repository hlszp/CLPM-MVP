/**
 * E2E 多角色权限验证测试
 *
 * 覆盖用例：
 * - E2E-ROLE-001: SPONSOR 菜单限制（仅可见监控/评估/诊断）
 * - E2E-ROLE-002: SPONSOR 直接访问受限页 → 跳转 403 或重定向
 * - E2E-ROLE-003: PE_ENGINEER 无整定菜单
 * - E2E-ROLE-004: EXPERT 有整定权限
 * - E2E-ROLE-005: IC_ENGINEER 全业务权限
 *
 * 角色权限对齐（IA 重构 Phase A 后菜单结构）：
 *   frontend/apps/web-antd/src/router/routes/modules/*.ts
 *   顶级菜单：监控 / 回路 / 评估 / 诊断 / 整定 / 配置 / 系统
 *   - SPONSOR：监控 / 评估 / 诊断
 *   - PE_ENGINEER：监控 / 评估 / 诊断（无整定/配置/系统）
 *   - EXPERT：诊断 / 整定（无监控/配置/系统）
 *   - IC_ENGINEER：监控 / 评估 / 诊断 / 整定 / 系统（无配置）
 *   - ADMIN：全部
 *   注：Phase A "回路"菜单组仅含 hideInMenu 的详情页，侧边栏不显示，
 *       Phase B 回路工作台上线后回归。
 */
import { test, expect } from '../fixtures/auth.js';

/** 等待菜单渲染完成 */
async function waitForMenu(page: import('@playwright/test').Page) {
  await page.waitForLoadState('networkidle');
  // Vben Admin 菜单容器：.vben-menu
  await page
    .locator('.vben-menu')
    .first()
    .waitFor({ state: 'visible', timeout: 15_000 })
    .catch(() => {
      // 菜单可能采用不同 class，兜底等待任意菜单项出现
    });
  await page.waitForTimeout(1000);
}

/** 获取侧边栏可见菜单文本列表（含 submenu 标题和叶子项） */
async function getMenuTexts(page: import('@playwright/test').Page): Promise<string[]> {
  // Vben Menu: .vben-menu-item（叶子项）+ .vben-sub-menu-content__title（父级标题）
  const items = page.locator(
    '.vben-menu-item, .vben-sub-menu-content__title, .vben-sub-menu-title',
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

    // SPONSOR 应可见：监控、评估、诊断（数组精确匹配，避免子串误判）
    expect(menuTexts).toContain('监控');
    expect(menuTexts).toContain('评估');
    expect(menuTexts).toContain('诊断');

    // SPONSOR 不应可见：整定、配置、系统
    expect(menuTexts).not.toContain('整定');
    expect(menuTexts).not.toContain('配置');
    expect(menuTexts).not.toContain('系统');
  });

  test('E2E-ROLE-002: SPONSOR 直接访问受限页 → 403/404 或重定向', async ({
    page,
    loginAs,
  }) => {
    await loginAs('SPONSOR');

    // 手动访问 /config/loop（SPONSOR 无权限）
    await page.goto('/config/loop');

    // 预期：跳转 403/404 页面 或 重定向到默认首页
    // 用 domcontentloaded 替代 networkidle：SignalR 心跳使 networkidle 永不触发
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(2000);
    const url = page.url();
    const bodyText = (await page.locator('body').innerText()).toLowerCase();

    const is403 =
      url.includes('/403') ||
      url.includes('forbidden') ||
      bodyText.includes('403') ||
      bodyText.includes('无权') ||
      bodyText.includes('没有权限') || // vben Fallback 403 文案（整改 C2-2）
      bodyText.includes('访问被拒绝') ||
      bodyText.includes('禁止访问');
    const is404 =
      url.includes('/404') ||
      bodyText.includes('404') ||
      bodyText.includes('未找到') ||
      bodyText.includes('not found');
    const isRedirected = url.includes('/dashboard') || url.includes('/auth/login');

    // SPONSOR 无权限访问受限页，应显示 403/404 或重定向
    expect(is403 || is404 || isRedirected).toBeTruthy();
  });

  test('E2E-ROLE-003: PE_ENGINEER 无整定菜单', async ({ page, loginAs }) => {
    await loginAs('PE_ENGINEER');
    await page.goto('/dashboard');
    await page.waitForURL(/\/dashboard/, { timeout: 30_000 });
    await waitForMenu(page);

    const menuTexts = await getMenuTexts(page);

    // PE_ENGINEER 应可见：监控、评估、诊断
    expect(menuTexts).toContain('监控');
    expect(menuTexts).toContain('评估');
    expect(menuTexts).toContain('诊断');

    // PE_ENGINEER 不应可见：整定
    expect(menuTexts).not.toContain('整定');
  });

  test('E2E-ROLE-004: EXPERT 有整定权限', async ({ page, loginAs }) => {
    await loginAs('EXPERT');
    await page.goto('/dashboard');
    await page.waitForURL(/\/dashboard/, { timeout: 30_000 });
    await waitForMenu(page);

    const menuTexts = await getMenuTexts(page);

    // EXPERT 应可见：整定
    expect(menuTexts).toContain('整定');

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

    // IC_ENGINEER 应可见：监控、评估、诊断、整定、系统（不含配置：ADMIN 专属）
    expect(menuTexts).toContain('监控');
    expect(menuTexts).toContain('评估');
    expect(menuTexts).toContain('诊断');
    expect(menuTexts).toContain('整定');
    expect(menuTexts).toContain('系统');
    // IC_ENGINEER 不应可见：配置
    expect(menuTexts).not.toContain('配置');
  });
});
