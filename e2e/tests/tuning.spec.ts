/**
 * E2E 回路整定三页式测试（2026-08-23 对齐现行 IA 重写）
 *
 * 现行 IA（09 设计方案 §6.1，router/routes/modules/tuning.ts）：
 *   三页式 = 整定工作台（辨识→整定矩阵→仿真对比→方案确认 单页 4 锚点流程）
 *          / 整定记录（历史追溯）/ 效果验证（前后窗曲线对比）。
 *
 * 覆盖用例：
 * - E2E-TUNE-001: 整定工作台（/tuning/workbench → 标题 + 4 锚点导航）
 * - E2E-TUNE-002: 过程辨识区（identify-section → 时间窗 RangePicker + 开始辨识按钮）
 * - E2E-TUNE-003: 锚点导航 4 步骤目标区存在（tuning-anchor-* section id）
 * - E2E-TUNE-004: 整定记录页（/tuning/records → 标题 + 表格/空态）
 * - E2E-TUNE-005: 效果验证页（/tuning/verification → 标题渲染）
 * - E2E-TUNE-006: 辨识策略说明渲染（历史数据辨识 / 阶跃实验口径）
 *
 * 删除的旧 IA 用例（均为 Phase D 单页整合遗物，现行代码无对应页面/行为）：
 * - 原 TUNE-002/003/004/006/007：访问 /tuning/detail + .anchor-item 锚点门禁。
 *   现行工作台锚点为纯页内滚动（workbench.vue scrollIntoView），无门禁约束，
 *   /tuning/detail 路由不存在。
 * - 原 TUNE-005：/tuning/stats 效果统计页（已删除，效果验证由
 *   /tuning/verification 承接）。
 * - 原 TUNE-008：/tuning/knowledge-base 整定知识库页（现行 tuning.ts 无此路由）。
 * - 原 TUNE-009：工作台"待整定回路相似案例推荐"（现行工作台无该区域）。
 *
 * 页面源码依据：
 *   frontend/apps/web-antd/src/views/tuning/{workbench,records,verification}.vue
 *   frontend/apps/web-antd/src/views/tuning/components/{identify,matrix,simulate,confirm}-section.vue
 */
import { test, expect } from '../fixtures/auth.js';
import type { Page } from '@playwright/test';

/** 选中左侧回路列表首个回路：工作台的 4 锚点整定流程区
 * （anchor-nav + identify/matrix/simulate/confirm section）仅在
 * 已选回路后渲染（workbench.vue v-if="ctx.loopId.value"） */
async function selectFirstLoop(page: Page): Promise<void> {
  const firstLoop = page.locator('.tuning-loop-item').first();
  await expect(firstLoop).toBeVisible({ timeout: 20_000 });
  await firstLoop.click();
  // 流程区渲染 + 诊断基线/建议等异步加载
  await page.waitForTimeout(1500);
}

test.describe('回路整定三页式 E2E', () => {
  test.beforeEach(async ({ loginAs }) => {
    // 整定模块需要 ADMIN / IC_ENGINEER / EXPERT 权限
    await loginAs('ADMIN');
  });

  test('E2E-TUNE-001: 整定工作台', async ({ page }) => {
    await page.goto('/tuning/workbench', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('body')).toContainText('整定工作台', {
      timeout: 20_000,
    });

    // 副标题（09 方案 §6.2：辨识 → 整定矩阵 → 仿真对比 → 方案确认）
    const pageText = await page.locator('body').innerText();
    expect(pageText).toMatch(/辨识/);

    // 选中回路后 4 锚点导航才渲染（v-if ctx.loopId）
    await selectFirstLoop(page);
    const anchors = page.locator('.tuning-anchor-link');
    await expect(anchors).toHaveCount(4, { timeout: 15_000 });

    expect(page.url()).toContain('/tuning/workbench');
  });

  test('E2E-TUNE-002: 过程辨识区（第①步）', async ({ page }) => {
    await page.goto('/tuning/workbench', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('body')).toContainText('整定工作台', {
      timeout: 20_000,
    });

    // identify-section 随已选回路渲染
    await selectFirstLoop(page);

    // identify-section：时间窗 RangePicker（placeholder 开始时间/结束时间）
    const rangePicker = page.locator('.ant-picker-range').first();
    await expect(rangePicker).toBeVisible({ timeout: 15_000 });

    // "开始辨识"按钮存在
    const identifyBtn = page.getByRole('button', { name: /开始辨识/ }).first();
    await expect(identifyBtn).toBeVisible({ timeout: 15_000 });

    expect(page.url()).toContain('/tuning/workbench');
  });

  test('E2E-TUNE-003: 锚点导航 4 步骤目标区存在', async ({ page }) => {
    await page.goto('/tuning/workbench', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('body')).toContainText('整定工作台', {
      timeout: 20_000,
    });

    // 4 个 section 锚点目标（Card id）随已选回路挂载在 DOM
    await selectFirstLoop(page);
    for (const id of [
      'tuning-anchor-identify',
      'tuning-anchor-matrix',
      'tuning-anchor-simulate',
      'tuning-anchor-confirm',
    ]) {
      await expect(page.locator(`#${id}`)).toHaveCount(1);
    }

    // 点击锚点不跳路由（纯页内滚动，URL 保持不变）
    await page
      .locator('.tuning-anchor-link')
      .filter({ hasText: '整定矩阵' })
      .first()
      .click();
    await page.waitForTimeout(800);
    expect(page.url()).toContain('/tuning/workbench');
  });

  test('E2E-TUNE-004: 整定记录页', async ({ page }) => {
    await page.goto('/tuning/records', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('body')).toContainText('整定记录', {
      timeout: 20_000,
    });

    // 表格或空态二选一渲染（容忍无整定记录的环境）
    const tableOrEmpty = page.locator('.ant-table, .ant-empty').first();
    await expect(tableOrEmpty).toBeVisible({ timeout: 15_000 });

    expect(page.url()).toContain('/tuning/records');
  });

  test('E2E-TUNE-005: 效果验证页', async ({ page }) => {
    await page.goto('/tuning/verification', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('body')).toContainText('效果验证', {
      timeout: 20_000,
    });

    // 副标题说明（前后窗曲线对比）
    const pageText = await page.locator('body').innerText();
    expect(pageText).toMatch(/前后窗|曲线对比|效果验证/);

    expect(page.url()).toContain('/tuning/verification');
  });

  test('E2E-TUNE-006: 辨识策略口径说明渲染', async ({ page }) => {
    await page.goto('/tuning/workbench', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('body')).toContainText('整定工作台', {
      timeout: 20_000,
    });

    // identify-section 随已选回路渲染，含两种辨识口径（历史数据辨识 / 阶跃实验）
    await selectFirstLoop(page);
    const pageText = await page.locator('body').innerText();
    const hasStrategyText = /历史数据辨识|阶跃/.test(pageText);
    expect(hasStrategyText).toBeTruthy();

    expect(page.url()).toContain('/tuning/workbench');
  });
});
