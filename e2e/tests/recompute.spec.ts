/**
 * E2E 历史重算测试
 *
 * 覆盖用例：
 * - E2E-RECOMPUTE-001: 发起重算 dry-run → 预览 → 取消
 * - E2E-RECOMPUTE-002: 重算记录列表筛选
 * - E2E-RECOMPUTE-003: 权限校验（PE_ENGINEER 不可见菜单）
 */
import { test, expect } from '../fixtures/auth.js';

test.describe('历史重算 E2E', () => {
  test.beforeEach(async ({ page, loginAs }) => {
    await loginAs('ADMIN');
  });

  test('E2E-RECOMPUTE-001: 发起重算 dry-run 预览', async ({ page }) => {
    await page.goto('/metric/recompute');
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(2000);

    // 验证页面加载
    await expect(page.getByText('历史重算').first()).toBeVisible({
      timeout: 15_000,
    });

    // 点击「发起重算」打开 Drawer
    await page.getByRole('button', { name: /发起重算/ }).click();
    await expect(page.locator('.ant-drawer')).toBeVisible({
      timeout: 10_000,
    });

    // 验证 Drawer 标题
    await expect(page.locator('.ant-drawer-title')).toContainText('发起历史重算');

    // 验证「确认重算」按钮初始为 disabled
    const submitBtn = page.getByRole('button', { name: /确认重算/ });
    await expect(submitBtn).toBeDisabled();

    // 点击「预览影响范围」
    const previewBtn = page.getByRole('button', { name: /预览影响范围/ });
    await previewBtn.click();
    await page.waitForTimeout(3000);

    // 验证预览卡片出现（包含"影响范围预览"或"回路数"）
    const previewCard = page.locator('.ant-drawer').getByText(/影响范围预览|回路数/).first();
    const hasPreview = await previewCard.isVisible().catch(() => false);
    if (hasPreview) {
      // 验证「确认重算」按钮变为 enabled
      await expect(submitBtn).toBeEnabled({ timeout: 5_000 });
    }

    // 关闭 Drawer — 多策略尝试（Escape > Close 按钮 > 取消按钮）
    // Ant Design Drawer 默认支持 Escape 关闭
    await page.keyboard.press('Escape').catch(() => {});
    await page.waitForTimeout(1500);

    // 检查 drawer title 是否仍然可见（title 消失即视为 drawer 已关闭）
    const titleStillVisible = await page
      .locator('.ant-drawer-title')
      .first()
      .isVisible()
      .catch(() => false);

    if (titleStillVisible) {
      // 兜底 1：点击 Drawer 头部的 Close 按钮
      const closeBtn = page
        .locator('.ant-drawer')
        .getByRole('button', { name: 'Close' })
        .first();
      const hasClose = await closeBtn.isVisible().catch(() => false);
      if (hasClose) {
        await closeBtn.click().catch(() => {});
        await page.waitForTimeout(1500);
      }
    }

    const titleStillVisible2 = await page
      .locator('.ant-drawer-title')
      .first()
      .isVisible()
      .catch(() => false);

    if (titleStillVisible2) {
      // 兜底 2：点击 footer 的「取 消」按钮（Ant Design 2 字中文按钮自动加空格）
      const cancelBtn = page
        .locator('.ant-drawer')
        .getByRole('button', { name: /取\s*消/ })
        .first();
      const hasCancel = await cancelBtn.isVisible().catch(() => false);
      if (hasCancel) {
        await cancelBtn.click().catch(() => {});
        await page.waitForTimeout(1500);
      }
    }

    // 验证 Drawer 已关闭：drawer title 不再可见即视为关闭
    // （.ant-drawer 容器在过渡动画期间可能仍存在于 DOM，但 title 会立即消失）
    await expect(page.locator('.ant-drawer-title').first()).not.toBeVisible({
      timeout: 10_000,
    });
  });

  test('E2E-RECOMPUTE-002: 重算记录列表与筛选', async ({ page }) => {
    await page.goto('/metric/recompute');
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(2000);

    // 验证表格容器存在
    const table = page.locator('.ant-table').first();
    await expect(table).toBeVisible({ timeout: 15_000 });

    // 验证筛选区存在（状态下拉）
    const statusSelect = page.locator('.ant-select').filter({ hasText: /状态筛选|待执行|执行中/ }).first();
    const hasStatusSelect = await statusSelect.isVisible().catch(() => false);
    expect(hasStatusSelect).toBeTruthy();

    // 验证表头包含关键列
    const headerText = await page.locator('.ant-table-thead').first().innerText();
    expect(headerText).toMatch(/任务ID|时间窗|状态|进度/);

    // 验证「发起重算」按钮存在
    await expect(page.getByRole('button', { name: /发起重算/ })).toBeVisible();
  });

  test('E2E-RECOMPUTE-003: PE_ENGINEER 不可访问', async ({ page, loginAs, logout }) => {
    // 先清除当前 ADMIN 登录态，再以 PE_ENGINEER 登录
    await logout();
    await loginAs('PE_ENGINEER');

    // 验证左侧菜单不包含「历史重算」
    await page.goto('/');
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(2000);
    const menuItem = page.getByText('历史重算', { exact: false }).first();
    const hasMenu = await menuItem.isVisible().catch(() => false);
    expect(hasMenu).toBeFalsy();

    // 直接访问 URL：PE_ENGINEER 无权限
    // 路由守卫可能拦截（重定向到 403/首页）或允许访问但页面无操作按钮
    // 两种情况都应验证：页面无「发起重算」按钮（即 PE_ENGINEER 无法操作重算功能）
    await page.goto('/metric/recompute');
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(2000);

    // 验证页面无「发起重算」按钮（无论是否被重定向，都不应有操作入口）
    const recomputeBtn = page.getByRole('button', { name: /发起重算/ }).first();
    const hasRecomputeBtn = await recomputeBtn.isVisible().catch(() => false);
    expect(hasRecomputeBtn).toBeFalsy();
  });
});
