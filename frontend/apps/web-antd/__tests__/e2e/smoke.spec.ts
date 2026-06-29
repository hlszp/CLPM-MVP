import { expect, test } from '@playwright/test';

/**
 * S3-C6: 基本冒烟测试
 *
 * 首次集成 E2E 测试，仅验证应用能正常启动和访问登录页。
 * 使用 continue-on-error: true 标记为非阻塞。
 */

test.describe('冒烟测试', () => {
  test('应用应能正常启动并访问登录页', async ({ page }) => {
    await page.goto('/');

    // 等待页面加载完成（登录页应包含表单或登录相关元素）
    await page.waitForLoadState('networkidle');

    // 验证页面标题不为空
    const title = await page.title();
    expect(title).toBeTruthy();
  });

  test('登录页应渲染基本 UI 元素', async ({ page }) => {
    await page.goto('/auth/login');

    await page.waitForLoadState('networkidle');

    // 验证页面 body 有内容（非空白页）
    const bodyText = await page.locator('body').innerText();
    expect(bodyText.length).toBeGreaterThan(0);
  });
});
