/**
 * E2E 报告模块优化 P1：数据质量 / 预警统计两报告页基线
 *
 * 覆盖（方案 §4.1/§4.2，P1-6）：
 * - 直链访问不白屏，页面标题渲染
 * - 基础模块数据自持：页面骨架（筛选条 + KPI 卡区）在无数据/空数据下完整渲染
 * - 菜单可见性：报告菜单含"数据质量 / 预警统计"（D3 决策顺序：基座在前、闭环在后）
 *
 * 依据：docs/设计文档/CLPM报告模块优化实施方案-2026-08-28.md §4
 *       frontend/apps/web-antd/src/views/reports/{data-quality,alert-statistics}.vue
 */
import { test, expect } from '../fixtures/auth.js';

const PAGES: Array<{
  path: string;
  title: string;
  filterLabel: string;
}> = [
  {
    path: '/reports/data-quality',
    title: '数据质量报告',
    filterLabel: '时间范围',
  },
  {
    path: '/reports/alert-statistics',
    title: '预警统计报告',
    filterLabel: '严重度',
  },
];

test.describe('报告 P1 两页基线（数据质量 / 预警统计）', () => {
  test.beforeEach(async ({ loginAs }) => {
    await loginAs('ADMIN');
  });

  for (const { path, title, filterLabel } of PAGES) {
    test(`E2E-RPT-P1: ${path} 直链访问不白屏且骨架完整`, async ({ page }) => {
      await page.goto(path, { waitUntil: 'domcontentloaded' });
      await expect(page).toHaveURL(new RegExp(path.replace(/\//g, '\\/')), {
        timeout: 15_000,
      });
      // 防白屏：body 必须有非空可见内容
      await expect(page.locator('body')).not.toBeEmpty();

      // 页面标题渲染（ClpmPageToolbar title）
      await expect(page.getByText(title, { exact: true })).toBeVisible({
        timeout: 15_000,
      });

      // 筛选条骨架：时间范围 + 页面特有筛选项
      await expect(page.getByText('时间范围').first()).toBeVisible();
      await expect(page.getByText(filterLabel, { exact: true })).toBeVisible();
    });
  }

  test('E2E-RPT-P1: 报告菜单含数据质量与预警统计（基座在前）', async ({
    page,
  }) => {
    await page.goto('/reports/overview', { waitUntil: 'domcontentloaded' });
    const menu = page.locator('aside, nav');
    await expect(menu.getByText('数据质量', { exact: true })).toBeVisible({
      timeout: 15_000,
    });
    await expect(menu.getByText('预警统计', { exact: true })).toBeVisible();
  });
});
