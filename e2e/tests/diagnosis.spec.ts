/**
 * E2E 诊断中心测试（2026-08-23 对齐现行两页式 IA 重写）
 *
 * 现行 IA（MVP v2 重设计，router/routes/modules/diagnosis.ts）：
 *   两页式 = 诊断工作台（发起+结果一体）/ 诊断记录（历史+筛选+导出）。
 *   原诊断中心 5 页结构（list/waveform/tracker/detail/tasks）已在 MVP 精简时删除。
 *
 * 覆盖用例：
 * - E2E-DIAG-001: 诊断工作台（/diagnosis/workbench → 标题 + 发起诊断入口）
 * - E2E-DIAG-002: 诊断记录（/diagnosis/records → 筛选栏 + 导出 + 表格/空态）
 * - E2E-DIAG-003: 诊断记录行点击抽屉（有数据行时打开"诊断结论"抽屉）
 *
 * 删除的旧 IA 用例（页面已随 MVP 两页式下线，无现行对应行为）：
 * - 原 E2E-DIAG-001/002：/diagnosis/list 筛选与 /diagnosis/waveform 波形页。
 * - 原 E2E-DIAG-003/006：/diagnosis/tracker Tracker 处理与 SPA 导航——
 *   tracker 服务已随处置 v2.0 批次 A1 关停，页面 404 兜底行为由
 *   diagnosis-tracker-flow.spec.ts D1 用例覆盖。
 * - 原 E2E-DIAG-004：/diagnosis/detail/:loopId 详情页（版本号竞态回归）——
 *   详情改为诊断记录行点击抽屉（本文件 DIAG-003 等价覆盖入口）。
 * - 原 E2E-DIAG-005：/diagnosis/tasks 显示已归档开关——页面已删除。
 *
 * 页面源码依据：
 *   frontend/apps/web-antd/src/views/diagnosis/{workbench,records}.vue
 *   - workbench: ClpmPageToolbar title=诊断工作台 + 回路选择 + 发起诊断
 *   - records: 筛选 Select（主分类/严重度/状态）+ 导出 CSV + 表格 + 行点击抽屉"诊断结论"
 */
import { test, expect } from '../fixtures/auth.js';

test.describe('诊断中心 E2E（两页式）', () => {
  test.beforeEach(async ({ page, loginAs }) => {
    // IC_ENGINEER 拥有诊断发起与记录查看权限
    await loginAs('IC_ENGINEER');
  });

  test('E2E-DIAG-001: 诊断工作台', async ({ page }) => {
    await page.goto('/diagnosis/workbench', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('body')).toContainText('诊断工作台', {
      timeout: 20_000,
    });

    // 副标题（症状证据 → 原因分类 → 处置建议）
    const pageText = await page.locator('body').innerText();
    expect(pageText).toMatch(/原因分类|症状证据|处置建议/);

    // 「发起诊断」按钮仅在勾选回路后渲染（workbench.vue
    // v-if="selectedLoopIds.length > 0"），先勾选左侧首个回路
    const firstLoop = page.locator('.diag-loop-item').first();
    await expect(firstLoop).toBeVisible({ timeout: 20_000 });
    await firstLoop.click();
    await page.waitForTimeout(1000);

    // 发起诊断入口存在（IC_ENGINEER 有发起权限）
    const runBtn = page.getByRole('button', { name: /发起诊断/ }).first();
    await expect(runBtn).toBeVisible({ timeout: 15_000 });

    expect(page.url()).toContain('/diagnosis/workbench');
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
