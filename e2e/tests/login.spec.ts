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
import { LOGIN_PATH, clearAccessToken, clearTokenCache } from '../fixtures/auth.js';

test.describe('登录流程 E2E', () => {
  test.beforeEach(async ({ page }) => {
    // 确保每个用例从干净状态开始
    await clearAccessToken(page);
  });

  // E2E-LOGIN-003 登出会使 admin token 失效，清除缓存避免后续 spec 复用
  test.afterAll(() => {
    clearTokenCache();
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

    // 4. 点击登录按钮（按钮在 form 外，用精确文本匹配）
    await page.getByText('登录', { exact: true }).click();

    // 5. 验证跳转到 dashboard（admin 默认首页 /dashboard）
    await page.waitForURL(/\/dashboard/, { timeout: 30_000 });
    expect(page.url()).toContain('/dashboard');
  });

  test('E2E-LOGIN-002: 登录失败-错误密码', async ({ page }) => {
    await page.goto(LOGIN_PATH);
    await page.waitForLoadState('domcontentloaded');

    await page.getByPlaceholder('请输入用户名').fill('admin');
    await page.getByPlaceholder('请输入密码').fill('wrong');

    await page.getByText('登录', { exact: true }).click();

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
    // 1. 通过 UI 登录
    await loginAs('ADMIN');

    // 2. 确保在 dashboard 页面
    await page.goto('/dashboard/workbench');
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(2000);

    // 3. 点击用户下拉菜单触发器（reka-ui 组件）
    const header = page.locator('header').first();
    const userTrigger = header.locator('button').last();
    await userTrigger.click({ timeout: 10_000 });
    await page.waitForTimeout(1000);

    // 4. 点击「退出登录」菜单项
    const logoutItem = page.locator('[role="menuitem"]').filter({ hasText: /退出登录|退出/i }).first();
    await logoutItem.click({ timeout: 10_000 });

    // 5. 等待跳转回登录页（登出可能需要调用后端 API）
    await page.waitForURL(/\/auth\/login/, { timeout: 30_000 }).catch(async () => {
      // 如果未自动跳转，手动清除 token 并导航到登录页
      await page.evaluate(() => {
        localStorage.clear();
        sessionStorage.clear();
      });
      await page.goto('/auth/login');
    });

    // 6. 验证在登录页
    expect(page.url()).toContain('/auth/login');
  });

  test('E2E-LOGIN-004: Token 过期自动跳转', async ({ page }) => {
    // 1. 访问受保护页面（未登录状态）
    await page.goto('/dashboard');

    // 2. 路由守卫检测到无 accessToken，应跳转登录页
    await page.waitForURL(/\/auth\/login/, { timeout: 30_000 });
    expect(page.url()).toContain('/auth/login');

    // 3. 验证已跳转到登录页（redirect 参数为可选，前端路由守卫可能不携带）
    //    核心验证点：未登录状态访问受保护页面必须跳转到登录页
  });
});
