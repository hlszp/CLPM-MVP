/**
 * E2E 登录流程测试
 *
 * 覆盖用例：
 * - E2E-LOGIN-001: ADMIN 登录成功
 * - E2E-LOGIN-002: 登录失败-错误密码
 * - E2E-LOGIN-003: 登出
 * - E2E-LOGIN-004: Token 过期自动跳转
 *
 * 登录页选择器依据：
 *   frontend/apps/web-antd/src/views/_core/authentication/login.vue
 *   - 使用 AuthenticationLogin 组件 + VbenFormSchema
 *   - 用户名输入框 placeholder: "请输入用户名"
 *   - 密码输入框 placeholder: "请输入密码"
 *   - 提交按钮文案: "登录"
 *   - 标题: "CLPM 登录" / 副标题: "控制回路性能管理系统"
 */
import { test, expect } from '../fixtures/auth.js';
import { LOGIN_PATH, clearAccessToken } from '../fixtures/auth.js';

test.describe('登录流程 E2E', () => {
  test.beforeEach(async ({ page }) => {
    // 确保每个用例从干净状态开始
    await clearAccessToken(page);
  });

  test('E2E-LOGIN-001: ADMIN 登录成功', async ({ page }) => {
    // 1. 访问登录页
    await page.goto(LOGIN_PATH);
    await page.waitForLoadState('domcontentloaded');

    // 2. 确认登录页标题可见
    await expect(page.getByText('CLPM 登录')).toBeVisible({ timeout: 10_000 });

    // 3. 填写用户名 / 密码
    await page.getByPlaceholder('请输入用户名').fill('admin');
    await page.getByPlaceholder('请输入密码').fill('admin123');

    // 4. 点击登录按钮
    await page.getByRole('button', { name: '登录' }).click();

    // 5. 验证跳转到 dashboard（admin 默认首页 /dashboard）
    await page.waitForURL(/\/dashboard/, { timeout: 30_000 });
    expect(page.url()).toContain('/dashboard');
  });

  test('E2E-LOGIN-002: 登录失败-错误密码', async ({ page }) => {
    await page.goto(LOGIN_PATH);
    await page.waitForLoadState('domcontentloaded');

    await page.getByPlaceholder('请输入用户名').fill('admin');
    await page.getByPlaceholder('请输入密码').fill('wrong');

    await page.getByRole('button', { name: '登录' }).click();

    // 验证仍停留在登录页（未跳转）
    await page.waitForTimeout(2000);
    expect(page.url()).toContain('/auth/login');

    // 验证出现错误提示（Ant Design message 或表单校验提示）
    // 后端返回 401 时 errorMessageResponseInterceptor 会调用 message.error
    // 通用错误提示可能包含「用户名或密码错误」「无效」「失败」等关键词
    const errorIndicator = page
      .locator('body')
      .filter({ hasText: /错误|失败|无效|不正确|incorrect|invalid|failed/i });
    await expect(errorIndicator.first()).toBeVisible({ timeout: 10_000 });
  });

  test('E2E-LOGIN-003: 登出', async ({ page, loginAs }) => {
    // 1. 通过 API 登录并注入 token
    await loginAs('ADMIN');

    // 2. 导航到 dashboard
    await page.goto('/dashboard');
    await page.waitForURL(/\/dashboard/, { timeout: 30_000 });

    // 3. 点击用户头像/下拉菜单（UserDropdown 组件）
    //    basic.vue 中通过 userStore.userInfo?.realName 展示用户名
    const userDropdown = page.locator('.vben-layout-user-name, .ant-dropdown-trigger').first();
    await userDropdown.click({ timeout: 10_000 }).catch(async () => {
      // 兜底：点击包含用户名的区域
      await page.getByText('系统管理员', { exact: false }).first().click({ timeout: 10_000 });
    });

    // 4. 点击「退出登录」菜单项
    //    UserDropdown 组件的退出登录项文案为「退出登录」或英文「Logout」
    const logoutItem = page.getByText(/退出登录|logout/i).first();
    await logoutItem.click({ timeout: 10_000 });

    // 5. 验证跳转回登录页
    await page.waitForURL(/\/auth\/login/, { timeout: 30_000 });
    expect(page.url()).toContain('/auth/login');
  });

  test('E2E-LOGIN-004: Token 过期自动跳转', async ({ page }) => {
    // 1. 访问受保护页面（未登录状态）
    await page.goto('/dashboard');

    // 2. 路由守卫检测到无 accessToken，应跳转登录页
    //    guard.ts: if (!accessStore.accessToken) → 跳转 LOGIN_PATH
    await page.waitForURL(/\/auth\/login/, { timeout: 30_000 });
    expect(page.url()).toContain('/auth/login');

    // 3. 验证 URL 中携带 redirect 参数
    const url = new URL(page.url());
    const redirect = url.searchParams.get('redirect');
    expect(redirect).toBeTruthy();
    expect(decodeURIComponent(redirect ?? '')).toContain('/dashboard');
  });
});
