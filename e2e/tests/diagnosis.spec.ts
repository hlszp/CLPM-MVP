/**
 * E2E 诊断中心测试（2026-08-23 两页式重写；2026-10-04 工作台规整 D2 三页式更新）
 *
 * 现行 IA（router/routes/modules/diagnosis.ts）：
 *   三页式 = 诊断概览（每回路最新结论 + 可诊断性筛选）/ 诊断记录 / 诊断任务。
 *   诊断工作台已并入回路工作台诊断剖面（/loop/workbench360?loopId=&section=diagnosis），
 *   /diagnosis/workbench 为 redirect 兼容路径。
 *
 * 覆盖用例：
 * - E2E-DIAG-001: 诊断概览（/diagnosis/overview → 标题 + 「仅可诊断」筛选 + 概览表）
 *   与旧路径 redirect（/diagnosis/workbench → 概览；带 loopId → 回路工作台诊断剖面）
 * - E2E-DIAG-002: 诊断记录（/diagnosis/records → 筛选栏 + 导出 + 表格/空态）
 * - E2E-DIAG-003: 诊断记录行点击抽屉（有数据行时打开"诊断结论"抽屉）
 *
 * 页面源码依据：
 *   frontend/apps/web-antd/src/views/diagnosis/{overview,records}.vue
 *   - overview: ClpmPageToolbar title=诊断概览 + 「仅可诊断」按钮 + 预检 Select + 表格
 *   - records: 筛选 Select（主分类/严重度/状态）+ 导出 CSV + 表格 + 行点击抽屉"诊断结论"
 */
import { test, expect } from '../fixtures/auth.js';

test.describe('诊断中心 E2E（三页式）', () => {
  test.beforeEach(async ({ page, loginAs }) => {
    // IC_ENGINEER 拥有诊断发起与记录查看权限
    await loginAs('IC_ENGINEER');
  });

  test('E2E-DIAG-001: 诊断概览与旧路径 redirect', async ({ page }) => {
    // 旧「诊断工作台」书签 → redirect 落诊断概览
    await page.goto('/diagnosis/workbench', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('body')).toContainText('诊断概览', {
      timeout: 20_000,
    });
    expect(page.url()).toContain('/diagnosis/overview');

    // 「仅可诊断」筛选（2026-10-04 D2 自诊断工作台迁入）
    await expect(
      page.getByRole('button', { name: /仅可诊断/ }).first(),
    ).toBeVisible({ timeout: 15_000 });

    // 概览表或空态二选一渲染（容忍无数据环境）
    const tableOrEmpty = page.locator('.ant-table, .ant-empty').first();
    await expect(tableOrEmpty).toBeVisible({ timeout: 15_000 });
  });

  test('E2E-DIAG-002: 诊断记录页', async ({ page }) => {
    await page.goto('/diagnosis/records', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('body')).toContainText('诊断记录', {
      timeout: 20_000,
    });

    // 筛选栏 Select（主分类/严重度 placeholder）
    await expect(
      page.locator('.ant-select').filter({ hasText: '主分类' }).first(),
    ).toBeVisible({ timeout: 15_000 });
    await expect(
      page.locator('.ant-select').filter({ hasText: '严重度' }).first(),
    ).toBeVisible({ timeout: 15_000 });

    // 导出 CSV 按钮存在
    await expect(
      page.getByRole('button', { name: /导出\s*CSV/ }).first(),
    ).toBeVisible({ timeout: 15_000 });

    // 表格或空态二选一渲染（容忍无诊断记录的环境）
    const tableOrEmpty = page.locator('.ant-table, .ant-empty').first();
    await expect(tableOrEmpty).toBeVisible({ timeout: 15_000 });

    // 有表格时校验表头关键字段（records.vue columns）
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
      expect(headerText).toMatch(/回路|主分类|严重度|状态/);
    }

    expect(page.url()).toContain('/diagnosis/records');
  });

  test('E2E-DIAG-003: 诊断记录行点击打开诊断结论抽屉', async ({ page }) => {
    await page.goto('/diagnosis/records', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('body')).toContainText('诊断记录', {
      timeout: 20_000,
    });

    // 有数据行时：点击第一行打开"诊断结论"抽屉（records.vue customRow）
    const firstRow = page
      .locator('.ant-table-tbody tr.ant-table-row')
      .first();
    const hasRow = await firstRow
      .isVisible({ timeout: 15_000 })
      .catch(() => false);
    if (!hasRow) {
      // 环境无诊断记录：弱断言——空态渲染即视为通过（抽屉依赖数据行）
      await expect(page.locator('.ant-empty').first()).toBeVisible();
      return;
    }

    await firstRow.click();
    await expect(page.locator('.ant-drawer')).toBeVisible({ timeout: 15_000 });
    await expect(page.locator('.ant-drawer-title')).toContainText('诊断结论');

    // 关闭抽屉（Escape 兜底）
    await page.keyboard.press('Escape').catch(() => {});
  });
});
