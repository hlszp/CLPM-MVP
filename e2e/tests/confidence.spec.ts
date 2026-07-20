/**
 * E2E 可信度徽章 + INCONCLUSIVE 展示测试
 *
 * 覆盖用例：
 * - E2E-CONF-001: /metric/loop-performance 表格含「可信度」列，单元格为 A~E Badge 或 —
 * - E2E-CONF-002: /metric/loop-performance 评估状态筛选含「不确定」（INCONCLUSIVE）
 * - E2E-CONF-003: /metric/tasks → 评估历史 Tab：可信度/状态筛选与可信度 Tag
 * - E2E-CONF-004: /loop/monitor 行内「性能」Modal：KPI 状态（良好/未确定/部分）
 *   与 INCONCLUSIVE 灰化展示（Alert + opacity-60）
 *
 * 页面源码依据：
 *   frontend/apps/web-antd/src/views/metric/loop-performance.vue
 *   - STATUS_LABEL_MAP：SUCCESS→成功 INCONCLUSIVE→不确定 PARTIAL→部分
 *   - CONFIDENCE_LABEL_MAP：A 优秀 / B 良好 / C 一般 / D 较差 / E 不足
 *   - CONFIDENCE_COLOR_MAP：A=green B=blue C=gold D=orange E=red（Badge 渲染）
 *   - 筛选区：Select placeholder=评估状态（选项 全部/成功/不确定/部分）
 *   - 表格列：可信度（Badge，无值时显示 —）
 *   frontend/apps/web-antd/src/views/metric/history-snapshots.vue（/metric/tasks 评估历史 Tab）
 *   - 筛选区：Select placeholder=状态（成功/不确定/部分）、Select placeholder=可信度（A~E）
 *   - 表格列：可信度（Tag）、状态（Tag）
 *   frontend/apps/web-antd/src/views/loop/monitor.vue
 *   - 行内操作：详情 / 趋势 / 性能 三个 Tag 入口
 *   - 性能 Modal title=`性能 - {tagName}`，kpiStatusMap：SUCCESS→良好 INCONCLUSIVE→未确定 PARTIAL→部分
 *   - INCONCLUSIVE 时：Alert message=该回路本期评估数据不足，结果不确定，
 *     综合评分/KPI 卡片容器带 class opacity-60（灰化）
 *
 * 路由（router/routes/modules/metric.ts、loop.ts）：
 *   - /metric/loop-performance → 回路性能（含 ADMIN 在内多角色可见）
 *   - /metric/tasks → 评估任务（ADMIN/IC_ENGINEER）
 *   - /loop/monitor → 回路监控（含 ADMIN 在内多角色可见）
 *
 * 边界：只读操作 + Modal 开关；数据相关断言防御式（无数据行则跳过）。
 */
import { test, expect } from '../fixtures/auth.js';

/** 可信度文案正则（Badge/Tag 文本或占位符 —） */
const CONFIDENCE_RE = /^([A-E]\s*(优秀|良好|一般|较差|不足)|—)$/;

test.describe('可信度徽章与 INCONCLUSIVE 展示 E2E', () => {
  test.beforeEach(async ({ page, loginAs }) => {
    await loginAs('ADMIN');
  });

  // E2E-CONF-001: 回路性能页可信度列
  test('E2E-CONF-001: 回路性能表格可信度列', async ({ page }) => {
    await page.goto('/metric/loop-performance');
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(2000);

    // 表格可见且表头含「可信度」列（loop-performance.vue columns）
    const table = page
      .locator('.ant-table')
      .filter({ has: page.locator('.ant-table-thead th', { hasText: '可信度' }) })
      .first();
    await expect(table).toBeVisible({ timeout: 15_000 });
    const headerText = await table.locator('.ant-table-thead').innerText();
    expect(headerText).toContain('可信度');

    // 若有数据行（防御性）：首行可信度单元格 ∈ {A 优秀, B 良好, C 一般, D 较差, E 不足, —}
    const firstRow = table.locator('.ant-table-tbody tr.ant-table-row').first();
    const hasRow = await firstRow.isVisible().catch(() => false);
    if (hasRow) {
      const thTexts = await table.locator('.ant-table-thead th').allInnerTexts();
      const confIdx = thTexts.findIndex((t) => t.includes('可信度'));
      expect(confIdx).toBeGreaterThanOrEqual(0);
      const cellText = (
        await firstRow.locator('td').nth(confIdx).innerText()
      ).trim();
      expect(
        CONFIDENCE_RE.test(cellText),
        `可信度单元格应为 A~E 徽章或 —，实际为「${cellText}」`,
      ).toBeTruthy();
    }
  });

  // E2E-CONF-002: 回路性能页评估状态筛选含「不确定」
  test('E2E-CONF-002: 评估状态筛选含不确定选项', async ({ page }) => {
    await page.goto('/metric/loop-performance');
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(2000);

    // 状态筛选 Select（loop-performance.vue placeholder 评估状态）
    const statusSelect = page
      .locator('.ant-select')
      .filter({ hasText: '评估状态' })
      .first();
    await expect(statusSelect).toBeVisible({ timeout: 15_000 });
    await statusSelect.click();

    // 下拉选项含「不确定」（INCONCLUSIVE 中文文案）
    const options = page.locator(
      '.ant-select-dropdown:visible .ant-select-item-option',
    );
    await expect(options.first()).toBeVisible({ timeout: 5_000 });
    const optionTexts = await options.allInnerTexts();
    expect(
      optionTexts.some((t) => t.includes('不确定')),
      `评估状态筛选选项应包含「不确定」，实际为 ${JSON.stringify(optionTexts)}`,
    ).toBeTruthy();

    // Escape 收起下拉
    await page.keyboard.press('Escape');
    await expect(
      page.locator('.ant-select-dropdown:visible'),
    ).toHaveCount(0, { timeout: 5_000 });
  });

  // E2E-CONF-003: 评估历史 Tab 可信度/状态筛选与可信度 Tag
  test('E2E-CONF-003: 评估历史可信度与状态筛选', async ({ page }) => {
    await page.goto('/metric/tasks');
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(2000);

    // 切换到「评估历史」Tab（tasks.vue 切换时强制重载）
    await page.locator('.ant-tabs-tab').filter({ hasText: '评估历史' }).click();
    await page.waitForTimeout(2000);

    // 当前可见 TabPane（history-snapshots.vue 渲染其中）
    const activePane = page.locator('.ant-tabs-tabpane:visible');

    // 可信度 Select（placeholder 可信度）→ 选项 A~E
    const confSelect = activePane
      .locator('.ant-select')
      .filter({ hasText: '可信度' })
      .first();
    await expect(confSelect).toBeVisible({ timeout: 15_000 });
    await confSelect.click();
    const confOptions = page.locator(
      '.ant-select-dropdown:visible .ant-select-item-option',
    );
    await expect(confOptions.first()).toBeVisible({ timeout: 5_000 });
    const confOptionTexts = await confOptions.allInnerTexts();
    for (const label of ['A 优秀', 'B 良好', 'C 一般', 'D 较差', 'E 不足']) {
      expect(
        confOptionTexts.some((t) => t.includes(label)),
        `可信度筛选选项应包含「${label}」`,
      ).toBeTruthy();
    }
    await page.keyboard.press('Escape');
    await page.waitForTimeout(500);

    // 状态 Select（placeholder 状态）→ 选项含「不确定」
    const statusSelect = activePane
      .locator('.ant-select')
      .filter({ hasText: /^状态$/ })
      .first();
    await expect(statusSelect).toBeVisible();
    await statusSelect.click();
    const statusOptions = page.locator(
      '.ant-select-dropdown:visible .ant-select-item-option',
    );
    await expect(statusOptions.first()).toBeVisible({ timeout: 5_000 });
    const statusOptionTexts = await statusOptions.allInnerTexts();
    expect(
      statusOptionTexts.some((t) => t.includes('不确定')),
      `状态筛选选项应包含「不确定」，实际为 ${JSON.stringify(statusOptionTexts)}`,
    ).toBeTruthy();
    await page.keyboard.press('Escape');
    await expect(
      page.locator('.ant-select-dropdown:visible'),
    ).toHaveCount(0, { timeout: 5_000 });

    // 表格有数据时（防御性）：首行可信度 Tag 文案 ∈ {A 优秀, ..., E 不足}（无值时显示 —）
    const table = activePane.locator('.ant-table').first();
    const firstRow = table.locator('.ant-table-tbody tr.ant-table-row').first();
    const hasRow = await firstRow.isVisible().catch(() => false);
    if (hasRow) {
      const thTexts = await table.locator('.ant-table-thead th').allInnerTexts();
      const confIdx = thTexts.findIndex((t) => t.includes('可信度'));
      expect(confIdx).toBeGreaterThanOrEqual(0);
      const cellText = (
        await firstRow.locator('td').nth(confIdx).innerText()
      ).trim();
      expect(
        CONFIDENCE_RE.test(cellText),
        `可信度单元格应为 A~E Tag 或 —，实际为「${cellText}」`,
      ).toBeTruthy();
    }
  });

  // E2E-CONF-004: 回路监控页性能 Modal 的 INCONCLUSIVE 展示
  test('E2E-CONF-004: 监控页性能 Modal KPI 状态', async ({ page }) => {
    await page.goto('/loop/monitor');
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(2000);

    // 页面表格渲染
    const table = page.locator('.ant-table').first();
    await expect(table).toBeVisible({ timeout: 15_000 });

    // 若有数据行（防御性）：点击第一行的「性能」打开 Modal
    const firstRow = table.locator('.ant-table-tbody tr.ant-table-row').first();
    const hasRow = await firstRow.isVisible().catch(() => false);
    if (hasRow) {
      // 行内操作：详情 / 趋势 / 性能（monitor.vue action 列）
      const perfEntry = firstRow.getByText('性能', { exact: true }).first();
      await expect(perfEntry).toBeVisible();
      await perfEntry.click();

      // Modal title 形如 `性能 - {tagName}`
      const modal = page.locator('.ant-modal');
      await expect(modal).toBeVisible({ timeout: 10_000 });
      await expect(page.locator('.ant-modal-title')).toContainText('性能');

      // 等待 KPI 数据加载（perfDetail 渲染后才有「KPI 状态」）
      const kpiLoaded = await modal
        .getByText('KPI 状态')
        .waitFor({ timeout: 15_000 })
        .then(() => true)
        .catch(() => false);

      if (kpiLoaded) {
        // KPI 状态 Tag 文案 ∈ {良好, 未确定, 部分}
        // （kpiStatusMap：SUCCESS→良好 INCONCLUSIVE→未确定 PARTIAL→部分）
        const tagTexts = (
          await modal.locator('.ant-tag').allInnerTexts()
        ).map((t) => t.trim());
        const statusTag = tagTexts.find((t) =>
          ['良好', '未确定', '部分'].includes(t),
        );
        expect(
          statusTag,
          `KPI 状态 Tag 应为 良好/未确定/部分，实际 Tag 列表 ${JSON.stringify(tagTexts)}`,
        ).toBeTruthy();

        if (statusTag === '未确定') {
          // INCONCLUSIVE：出现「数据不足」Alert
          await expect(
            modal.locator('.ant-alert').filter({ hasText: '数据不足' }),
          ).toBeVisible();
          // 综合评分/KPI 卡片容器灰化（class opacity-60）
          const dimmedCount = await modal.locator('.opacity-60').count();
          expect(
            dimmedCount,
            'INCONCLUSIVE 时综合评分/KPI 卡片应带 opacity-60 灰化',
          ).toBeGreaterThan(0);
        }
      }

      // 关闭 Modal（右上角 X，兜底 Escape）
      const closeBtn = modal.locator('.ant-modal-close').first();
      const hasClose = await closeBtn.isVisible().catch(() => false);
      if (hasClose) {
        await closeBtn.click().catch(() => {});
      } else {
        await page.keyboard.press('Escape').catch(() => {});
      }
      await page.waitForTimeout(1000);
      await expect(page.locator('.ant-modal:visible')).toHaveCount(0, {
        timeout: 10_000,
      });
    }
  });
});
