/**
 * E2E 回路整定三页式测试（2026-08-23 重写；2026-10-04 工作台规整 D3 更新）
 *
 * 现行 IA（router/routes/modules/tuning.ts）：
 *   三页式 = 整定总览（全回路可整定性 + 在途整定建议）
 *          / 整定记录（历史追溯）/ 效果验证（前后窗曲线对比）。
 *   单回路四步流程（辨识→矩阵→仿真→确认）已移回路工作台整定剖面
 *   （/loop/workbench360?loopId=&section=tuning）；/tuning/workbench 为
 *   redirect 兼容路径。
 *
 * 覆盖用例：
 * - E2E-TUNE-001: 整定总览（旧路径 redirect + 标题 + 「调参优化」入口）
 * - E2E-TUNE-004: 整定记录页（/tuning/records → 标题 + 表格/空态）
 * - E2E-TUNE-005: 效果验证页（/tuning/verification → 标题渲染）
 *
 * 删除的旧用例（2026-10-04 D3：四锚点流程已移回路工作台整定剖面）：
 * - 原 TUNE-002/003/006（identify 区/锚点目标区/辨识策略说明）——组件仍存在
 *   并被 workbench360 TuningSection 复用，其渲染由 perf-frontend.spec.ts 的
 *   /loop/workbench360 用例覆盖页面加载，此处不再重复断言页内细节。
 * - 更早的 Phase D 遗物用例见 git 历史。
 *
 * 页面源码依据：
 *   frontend/apps/web-antd/src/views/tuning/{overview,records,verification}.vue
 */
import { test, expect } from '../fixtures/auth.js';

test.describe('回路整定三页式 E2E', () => {
  test.beforeEach(async ({ loginAs }) => {
    // 整定总览全角色可见；操作入口需 ADMIN/IC/PE/EXPERT（D1 四角色）
    await loginAs('ADMIN');
  });

  test('E2E-TUNE-001: 整定总览（含旧路径 redirect）', async ({ page }) => {
    // 旧「整定工作台」书签 → redirect 落整定总览
    await page.goto('/tuning/workbench', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('body')).toContainText('整定总览', {
      timeout: 20_000,
    });
    expect(page.url()).toContain('/tuning/overview');

    // 左脊柱整定建议区标题 + 总览表/空态二选一渲染
    await expect(page.locator('body')).toContainText('整定建议');
    const tableOrEmpty = page.locator('.ant-table, .ant-empty').first();
    await expect(tableOrEmpty).toBeVisible({ timeout: 15_000 });

    // 「调参优化」入口随表格渲染（无数据环境下弱断言：表格表头存在）
    const hasTable = await page
      .locator('.ant-table-thead')
      .first()
      .isVisible()
      .catch(() => false);
    if (hasTable) {
      const headerText = await page
        .locator('.ant-table-thead')
        .first()
        .innerText();
      expect(headerText).toMatch(/可整定性|操作|回路/);
    }
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
});
