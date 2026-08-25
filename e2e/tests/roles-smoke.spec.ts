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
 *   /diagnosis/records      : 全五角色（MVP 两页式；原 /diagnosis/tasks 已不存在）
 *   /metric/indicator-analysis : ADMIN, IC, PE, SPONSOR（EXPERT 无评估模块，
 *                                2026-08-25 指标分析页 M3 联动新增）
 *
 * MW-P5-03 特别验证项：
 *   - EXPERT 进入回路工作台无阻断性 403 内容页（已知例外：后端
 *     /configs/grading-thresholds 仅对 ADMIN/IC/PE 开放，EXPERT 访问工作台时
 *     前端会触发一次 403 toast，属现行代码预期行为，前端已 .catch 降级，不计入阻断）
 *   - SPONSOR 关注队列只读且无 403 toast
 *
 * 2026-08-23 口径变更：原"view=table 回退 workspace"断言随工作台 Grid 布局
 * 重构下线（workbench.vue 已无 view query 处理逻辑），改为工作台可进入性验证。
 */
import type { Page } from '@playwright/test';
import { expect, test } from '../fixtures/auth.js';

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

  test('E2E-SMOKE-IC: 工作台 + 诊断记录 + 整定工作台', async ({ page, loginAs }) => {
    await loginAs('IC_ENGINEER');

    // 1. 回路工作台
    await page.goto('/monitor/loop-workbench');
    await waitForRender(page);
    await assertPageHealthy(page);

    // 2. 诊断记录（原 /diagnosis/tasks 已下线，MVP 两页式为 workbench/records）
    await page.goto('/diagnosis/records');
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

  test('E2E-SMOKE-EXPERT: 诊断 + 整定 + 回路工作台无阻断性 403', async ({
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

    // 1. 诊断记录（EXPERT 有查看权限；原 /diagnosis/tasks 已下线）
    await page.goto('/diagnosis/records');
    await waitForRender(page);
    await assertPageHealthy(page);

    // 2. 整定工作台
    await page.goto('/tuning/workbench');
    await waitForRender(page);
    await assertPageHealthy(page);
    await assertNoForbiddenToast(page);

    // 3. EXPERT 无评估模块：直接访问指标分析页应渲染 403 内容页（指标分析页 M3）
    await page.goto('/metric/indicator-analysis');
    await waitForRender(page);
    const deniedMetric = await assertAccessDenied(page);
    expect(deniedMetric, 'EXPERT 访问指标分析应显示 403 内容页').toBeTruthy();

    // 4. 回路工作台（EXPERT 在 authority 内）：页面健康、无 403 内容页。
    // 已知例外：后端 /configs/grading-thresholds 拒绝 EXPERT（require_roles
    // ADMIN/IC/PE），前端全局拦截器会弹一次"无权限访问" toast，但页面本身
    // 已 .catch 降级不影响渲染——此为现行代码预期行为，仅断言无预期外 403。
    await page.goto('/monitor/loop-workbench');
    await waitForRender(page);
    await assertPageHealthy(page);
    const unexpected403 = forbiddenResponses.filter(
      (r) => !r.includes('/configs/grading-thresholds'),
    );
    expect(
      unexpected403,
      `不应出现预期外 403（已知例外：grading-thresholds）: ${JSON.stringify(forbiddenResponses)}`,
    ).toHaveLength(0);
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

    // 3. 指标分析页（SPONSOR 在 authority 内，指标分析页 M3）：页面健康
    await page.goto('/metric/indicator-analysis');
    await waitForRender(page);
    await assertPageHealthy(page);
    await assertNoForbiddenToast(page);

    // 4. SPONSOR 无工作台权限：直接访问 /monitor/loop-workbench 应显示 403 内容页
    await page.goto('/monitor/loop-workbench');
    await waitForRender(page);
    const denied = await assertAccessDenied(page);
    expect(denied, 'SPONSOR 访问工作台应显示 403 内容页').toBeTruthy();
  });
});
