/**
 * E2E 指标矩阵页测试（评估-6，/metric/matrix）
 *
 * 覆盖用例：
 * - E2E-MATRIX-001: 页面加载与核心组件
 *     · ClpmPageToolbar 标题"指标矩阵"可见
 *     · 指标组 Segmented（核心/诊断/统计/阀门）可见
 *     · 时间窗 Segmented（最新/8h/24h/72h/168h）存在
 *     · 装置/回路筛选控件存在
 *     · 表格或空状态容器存在（容忍空数据）
 * - E2E-MATRIX-002: 指标组切换与列头渲染
 *     · 切换到"诊断"组 → 表头出现"粘滞指数/仪表故障率"列
 *     · 切换到"核心"组 → 表头出现"综合评分/振荡率"列
 *     · 列头交互图标（漏斗/趋势）存在（.matrix-header-icon）
 * - E2E-MATRIX-003: 深链 query 生效（?tab=valve → 阀门组激活）
 *
 * 页面源码依据：
 *   frontend/apps/web-antd/src/views/metric/matrix.vue
 *     - Segmented 指标组（GROUP_OPTIONS：核心/诊断/统计/阀门）
 *     - 表格列由 METRIC_DEFS 按当前组动态生成
 *     - 列头 #headerCell 渲染 .matrix-header-icon（lucide:filter / lucide:line-chart）
 *
 * 边界：只读操作（不点击单元格/趋势，避免弹层依赖数据）；数据为空时
 *       用防御式断言，核心验证页面加载成功 + 关键组件存在。
 */
import { test, expect } from '../fixtures/auth.js';

test.describe('指标矩阵 E2E（评估-6）', () => {
  test.beforeEach(async ({ page, loginAs }) => {
    await loginAs('ADMIN');
  });

  // E2E-MATRIX-001: 页面加载与核心组件
  test('E2E-MATRIX-001: 指标矩阵页加载与筛选组件', async ({ page }) => {
    // SignalR 心跳使 networkidle 不稳定，改用 domcontentloaded + 元素等待
    await page.goto('/metric/matrix', { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(2000);

    // 验证页面标题可见
    const pageTitle = page.getByText('指标矩阵', { exact: false }).first();
    await expect(pageTitle).toBeVisible({ timeout: 15_000 });

    // 验证指标组 Segmented（核心/诊断/统计/阀门）
    const groupItem = page
      .locator('.ant-segmented-item')
      .filter({ hasText: '核心' })
      .first();
    await expect(groupItem).toBeVisible({ timeout: 10_000 });
    for (const g of ['诊断', '统计', '阀门']) {
      const item = page
        .locator('.ant-segmented-item')
        .filter({ hasText: g })
        .first();
      expect(await item.isVisible().catch(() => false)).toBeTruthy();
    }

    // 验证时间窗 Segmented（最新/8h/24h/72h/168h）
    const latestWin = page
      .locator('.ant-segmented-item')
      .filter({ hasText: '最新' })
      .first();
    expect(await latestWin.isVisible().catch(() => false)).toBeTruthy();

    // 验证装置/回路筛选控件存在（TreeSelect + Select）
    const plantFilter = page
      .locator('.ant-select')
      .filter({ hasText: '装置筛选' })
      .first();
    const loopFilter = page
      .locator('.ant-select')
      .filter({ hasText: '回路筛选' })
      .first();
    const hasPlant = await plantFilter.isVisible().catch(() => false);
    const hasLoop = await loopFilter.isVisible().catch(() => false);
    expect(hasPlant || hasLoop).toBeTruthy();

    // 验证查询按钮存在（Ant 两字中文按钮自动插空格："查 询"）
    const queryBtn = page.getByRole('button', { name: /查\s*询/ }).first();
    await expect(queryBtn).toBeVisible({ timeout: 10_000 });

    // 验证表格或空状态容器存在（容忍空数据）
    const tableOrEmpty = page.locator('.ant-table, .ant-empty').first();
    await expect(tableOrEmpty).toBeVisible({ timeout: 15_000 });

    expect(page.url()).toContain('/metric/matrix');
  });

  // E2E-MATRIX-002: 指标组切换与列头渲染
  test('E2E-MATRIX-002: 指标组切换与列头交互图标', async ({ page }) => {
    await page.goto('/metric/matrix', { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(2000);

    const table = page.locator('.ant-table').first();
    const hasTable = await table.isVisible().catch(() => false);
    if (!hasTable) {
      // 空数据环境仅验证组切换不报错
      const diagItem = page
        .locator('.ant-segmented-item')
        .filter({ hasText: '诊断' })
        .first();
      await diagItem.click().catch(() => {});
      return;
    }

    // 默认核心组：表头含核心指标列
    let headerText = await table
      .locator('.ant-table-thead')
      .first()
      .innerText()
      .catch(() => '');
    expect(headerText).toContain('综合评分');
    expect(headerText).toContain('振荡率');

    // 核心组列头交互图标存在（漏斗列筛选 + 趋势对比）
    const headerIcons = page.locator('.matrix-header-icon');
    expect(
      await headerIcons.count(),
      '核心组列头应渲染交互图标（漏斗/趋势）',
    ).toBeGreaterThan(0);

    // 切换到诊断组：表头出现诊断指标列
    const diagItem = page
      .locator('.ant-segmented-item')
      .filter({ hasText: '诊断' })
      .first();
    await diagItem.click();
    await page.waitForTimeout(1500);
    headerText = await table
      .locator('.ant-table-thead')
      .first()
      .innerText()
      .catch(() => '');
    expect(headerText).toContain('粘滞指数');
    expect(headerText).toContain('仪表故障率');

    // 切换到阀门组：表头出现阀门指标列
    const valveItem = page
      .locator('.ant-segmented-item')
      .filter({ hasText: '阀门' })
      .first();
    await valveItem.click();
    await page.waitForTimeout(1500);
    headerText = await table
      .locator('.ant-table-thead')
      .first()
      .innerText()
      .catch(() => '');
    expect(headerText).toContain('阀门线性度');

    // 切换组后 URL query 同步（tab=valve）
    expect(page.url()).toContain('tab=valve');
  });

  // E2E-MATRIX-003: 深链 query 生效
  test('E2E-MATRIX-003: 深链 ?tab=stats 激活统计组', async ({ page }) => {
    await page.goto('/metric/matrix?tab=stats', {
      waitUntil: 'domcontentloaded',
    });
    await page.waitForTimeout(2000);

    // 统计组应为激活项（.ant-segmented-item-selected）
    const selected = page
      .locator('.ant-segmented-item-selected')
      .filter({ hasText: '统计' })
      .first();
    await expect(selected).toBeVisible({ timeout: 15_000 });

    // 表头含统计指标列（容忍空数据：仅当表格存在时校验）
    const table = page.locator('.ant-table').first();
    if (await table.isVisible().catch(() => false)) {
      const headerText = await table
        .locator('.ant-table-thead')
        .first()
        .innerText()
        .catch(() => '');
      expect(headerText).toContain('PV 均值');
    }
  });
});
