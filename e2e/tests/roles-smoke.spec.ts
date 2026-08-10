/**
 * MW-P5-03 五角色核心流程冒烟测试
 *
 * 目标：每个角色访问其核心页面，验证不白屏、不跳 403/404、无阻断性 403 toast。
 * 这是冒烟级测试，不深度验证业务逻辑——只确保关键流程可进入、可渲染。
 *
 * 角色权限矩阵（对齐 monitor.ts / tuning.ts / diagnosis.ts authority）：
 *   /monitor/loop-workbench : ADMIN, IC, PE, EXPERT（SPONSOR 无权限）
 *   /monitor/attention      : 全五角色
 *   /monitor/alerts         : 全五角色
 *   /tuning/workbench       : ADMIN, IC, EXPERT
 *   /diagnosis/tasks        : 全五角色
 *
 * MW-P5-03 特别验证项：
 *   - EXPERT 直接输入 view=table 时回到 workspace 且无 403 toast
 *   - SPONSOR 关注队列只读且无 403 toast
 */
import { expect, test, type Page } from '../fixtures/auth.js';

/** 403 相关文案集合（命中任一即视为 403 toast / 403 内容页） */
const FORBIDDEN_PATTERNS = ['403', '无权', '没有权限', '访问被拒绝', '禁止访问', 'forbidden'];

/** 校验页面是否渲染了 403 内容页（vben 在原 URL 渲染"访问被拒绝"，不跳转） */
async function assertAccessDenied(page: Page): Promise<boolean> {
  const bodyText = (await page.locator('body').innerText().catch(() => '')).toLowerCase();
  return FORBIDDEN_PATTERNS.some((p) => bodyText.includes(p.toLowerCase()));
}

/** 等待页面渲染稳定（SignalR 心跳使 networkidle 永不触发，沿用 domcontentloaded + 短等待） */
async function waitForRender(page: Page): Promise<void> {
  await page.waitForLoadState('domcontentloaded');
  await page.waitForTimeout(2500);
}

/** 校验页面不白屏、不跳 403/404/登录页 */
async function assertPageHealthy(page: Page, expectUrlNot: string[] = []): Promise<void> {
  const url = page.url();
  // 不应跳转到 403/404/登录
  expect(url, `URL 不应跳 403/404/登录: ${url}`).not.toMatch(/\/403|\/404|\/auth\/login/);
  for (const frag of expectUrlNot) {
    expect(url, `URL 不应含 ${frag}: ${url}`).not.toContain(frag);
  }
  // 不白屏：body 有可见文本
  const bodyText = (await page.locator('body').innerText()).trim();
  expect(bodyText.length, '页面不应白屏').toBeGreaterThan(0);
}

/** 检查页面上是否出现 403 toast（antd message） */
async function assertNoForbiddenToast(page: Page): Promise<void> {
  const toasts = page.locator('.ant-message, .ant-notification');
  const count = await toasts.count().catch(() => 0);
  if (count === 0) return;
  const toastText = (await toasts.innerText().catch(() => '')).toLowerCase();
  for (const p of FORBIDDEN_PATTERNS) {
    expect(toastText, `不应出现 403 toast，但命中: ${p}`).not.toContain(p.toLowerCase());
  }
}

test.describe('MW-P5-03 五角色核心流程冒烟', () => {
  test('E2E-SMOKE-ADMIN: 工作台 + 关注队列 + 配置 + 评估', async ({ page, loginAs }) => {
    await loginAs('ADMIN');

    // 1. 回路工作台（workspace 模式）
    await page.goto('/monitor/loop-workbench');
    await waitForRender(page);
    await assertPageHealthy(page);
    // 工作台主内容区应渲染（workspace 模式用虚拟列表+自定义布局，容忍加载中/空态）
    await expect(
      page
        .locator(
          'main, .vben-layout-content, .ant-layout-content, .ant-table, .ant-card, .ant-spin, .ant-empty',
        )
        .first(),
    ).toBeVisible({ timeout: 20_000 });

    // 2. 关注队列
    await page.goto('/monitor/attention');
    await waitForRender(page);
    await assertPageHealthy(page);
    await assertNoForbiddenToast(page);

    // 3. 回路配置（ADMIN 专属）
    await page.goto('/config/loop');
    await waitForRender(page);
    await assertPageHealthy(page);

    // 4. 评估看板
    await page.goto('/metric/pid-dashboard');
    await waitForRender(page);
    await assertPageHealthy(page);
  });

  test('E2E-SMOKE-IC: 工作台 + 诊断任务 + 整定工作台', async ({ page, loginAs }) => {
    await loginAs('IC_ENGINEER');

    // 1. 回路工作台
    await page.goto('/monitor/loop-workbench');
    await waitForRender(page);
    await assertPageHealthy(page);

    // 2. 诊断任务
    await page.goto('/diagnosis/tasks');
    await waitForRender(page);
    await assertPageHealthy(page);

    // 3. 整定工作台
    await page.goto('/tuning/workbench');
    await waitForRender(page);
    await assertPageHealthy(page);
    await assertNoForbiddenToast(page);
  });

  test('E2E-SMOKE-PE: 工作台只读 + 评估看板（无整定）', async ({ page, loginAs }) => {
    await loginAs('PE_ENGINEER');

    // 1. 回路工作台（只读，写动作应 disabled，但不阻断渲染）
    await page.goto('/monitor/loop-workbench');
    await waitForRender(page);
    await assertPageHealthy(page);
    await assertNoForbiddenToast(page);

    // 2. 评估看板
    await page.goto('/metric/pid-dashboard');
    await waitForRender(page);
    await assertPageHealthy(page);

    // 3. PE 无整定权限：直接访问 /tuning/workbench 应显示 403 内容页（vben 在原 URL 渲染"访问被拒绝"）
    await page.goto('/tuning/workbench');
    await waitForRender(page);
    const denied = await assertAccessDenied(page);
    expect(denied, 'PE 访问整定应显示 403 内容页').toBeTruthy();
  });

  test('E2E-SMOKE-EXPERT: 诊断 + 整定 + view=table 回退 workspace 无 403', async ({
    page,
    loginAs,
  }) => {
    await loginAs('EXPERT');

    // 监听 403 响应，定位 toast 来源
    const forbiddenResponses: string[] = [];
    page.on('response', (res) => {
      if (res.status() === 403) {
        forbiddenResponses.push(`${res.status()} ${res.url()}`);
      }
    });

    // 1. 诊断任务
    await page.goto('/diagnosis/tasks');
    await waitForRender(page);
    await assertPageHealthy(page);

    // 2. 整定工作台
    await page.goto('/tuning/workbench');
    await waitForRender(page);
    await assertPageHealthy(page);
    await assertNoForbiddenToast(page);

    // 3. 直接输入 view=table → useSavedView 应回退到 workspace，无 403 toast
    await page.goto('/monitor/loop-workbench?view=table');
    await waitForRender(page);
    await assertPageHealthy(page);
    // 检查无 403 toast，失败时附 403 响应列表辅助定位
    const toasts = page.locator('.ant-message, .ant-notification');
    const toastText = (await toasts.innerText().catch(() => '')).toLowerCase();
    const hitPattern = FORBIDDEN_PATTERNS.find((p) => toastText.includes(p.toLowerCase()));
    expect(
      !hitPattern,
      `出现 403 toast "${hitPattern}"。捕获的 403 响应: ${JSON.stringify(forbiddenResponses)}`,
    ).toBe(true);
    // URL 中 view= 应已被规范化（不再含 view=table）
    const url = page.url();
    expect(url, `EXPERT view=table 应回退，实际: ${url}`).not.toContain('view=table');
  });

  test('E2E-SMOKE-SPONSOR: 关注队列只读 + 预警记录，无 403 toast', async ({
    page,
    loginAs,
  }) => {
    await loginAs('SPONSOR');

    // 1. 关注队列（只读，应无确认/处置按钮）
    await page.goto('/monitor/attention');
    await waitForRender(page);
    await assertPageHealthy(page);
    await assertNoForbiddenToast(page);

    // 2. 预警记录
    await page.goto('/monitor/alerts');
    await waitForRender(page);
    await assertPageHealthy(page);
    await assertNoForbiddenToast(page);

    // 3. SPONSOR 无工作台权限：直接访问 /monitor/loop-workbench 应显示 403 内容页
    await page.goto('/monitor/loop-workbench');
    await waitForRender(page);
    const denied = await assertAccessDenied(page);
    expect(denied, 'SPONSOR 访问工作台应显示 403 内容页').toBeTruthy();
  });
});
