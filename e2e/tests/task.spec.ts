/**
 * E2E 任务管理页面测试（/metric/tasks）
 *
 * 覆盖用例：
 * - E2E-TASK-001: 页面加载与 4 个 Tab（手动任务/自动任务/评估历史/策略配置）
 * - E2E-TASK-002: 默认「手动任务」Tab 表格、表头、工具栏按钮与状态筛选
 * - E2E-TASK-003: 「新建任务」Drawer 表单字段与底部按钮（dry-run 预览，不提交）
 * - E2E-TASK-004: 「自动任务」Tab 按钮/表头/行点击详情 Drawer
 * - E2E-TASK-005: 状态筛选下拉选项（待执行/执行中/成功/失败/已取消）
 * - E2E-TASK-006: PE_ENGINEER 无权限（菜单不可见 + 直接访问无操作入口）
 *
 * 页面源码依据：
 *   frontend/apps/web-antd/src/views/metric/tasks.vue
 *   - ClpmPageToolbar title=评估任务 subtitle=管理手动重算任务、自动评估任务、评估历史与策略配置
 *   - Tabs 4 个 TabPane：manual(手动任务) / auto(自动任务) / history(评估历史) / strategy(策略配置)
 *   frontend/apps/web-antd/src/views/metric/recompute.vue（手动任务 Tab）
 *   - 工具栏：新建任务(primary) / 删除 / 刷新 ｜ 状态筛选 Select / RangePicker / 查询
 *   - 状态映射：PENDING→待执行 RUNNING→执行中 SUCCESS→成功 FAILED→失败 CANCELLED→已取消
 *   - 表头：任务标题 / 任务类型 / 评估回路 / 小时窗口 / 时间窗口 / 评估状态 / 评估进度 / ...
 *   - Drawer title=新建任务：任务标题 Input(请输入任务标题) / 时间窗 RangePicker /
 *     装置 TreeSelect(不选=全部装置) / 回路 Select(不选=对应装置全部回路)
 *   - Drawer footer：取消 / 预览影响范围 / 确认重算（!previewResult 时 disabled）
 *   frontend/apps/web-antd/src/views/task/list.vue（自动任务 Tab）
 *   - 工具栏：触发标准评估(primary) / 批量删除 / 刷新 ｜ 状态筛选 / RangePicker / 查询
 *   - 表头：任务标题 / 任务类型 / 评估回路 / 小时窗口 / 时间窗口 / 评估状态 / 评估进度 / ...
 *   - 行点击 customRow → Drawer title=任务详情
 *
 * 路由（router/routes/modules/metric.ts）：
 *   - /metric/tasks → MetricTasks，authority: ADMIN / IC_ENGINEER（菜单 title=评估任务）
 *
 * 边界：只读操作 + Drawer 开关；禁止点击「确认重算」「触发标准评估」「批量删除」
 * 及行内 评估/取消/删除（会产生真实任务或删除数据）。
 */
import { test, expect } from '../fixtures/auth.js';

/** 关闭 Drawer 的多策略兜底（Escape > Close 按钮 > 取消按钮） */
async function closeDrawer(page: import('@playwright/test').Page): Promise<void> {
  await page.keyboard.press('Escape').catch(() => {});
  await page.waitForTimeout(1500);

  const titleVisible1 = await page
    .locator('.ant-drawer-title')
    .first()
    .isVisible()
    .catch(() => false);
  if (titleVisible1) {
    const closeBtn = page
      .locator('.ant-drawer:visible')
      .getByRole('button', { name: 'Close' })
      .first();
    if (await closeBtn.isVisible().catch(() => false)) {
      await closeBtn.click().catch(() => {});
      await page.waitForTimeout(1500);
    }
  }

  const titleVisible2 = await page
    .locator('.ant-drawer-title')
    .first()
    .isVisible()
    .catch(() => false);
  if (titleVisible2) {
    // Ant Design 2 字中文按钮自动加空格（取 消）
    const cancelBtn = page
      .locator('.ant-drawer:visible')
      .getByRole('button', { name: /取\s*消/ })
      .first();
    if (await cancelBtn.isVisible().catch(() => false)) {
      await cancelBtn.click().catch(() => {});
      await page.waitForTimeout(1500);
    }
  }
}

test.describe('任务管理页面 E2E', () => {
  test.beforeEach(async ({ page, loginAs }) => {
    // /metric/tasks 路由 authority 为 ADMIN / IC_ENGINEER
    await loginAs('ADMIN');
  });

  // E2E-TASK-001: 页面加载与 4 个 Tab
  test('E2E-TASK-001: 页面加载与 Tab 结构', async ({ page }) => {
    await page.goto('/metric/tasks');
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(2000);

    // 验证 4 个 Tab 存在（tasks.vue TabPane：手动任务/自动任务/评估历史/策略配置）
    const tabs = page.locator('.ant-tabs-tab');
    await expect(tabs.first()).toBeVisible({ timeout: 15_000 });
    await expect(tabs.filter({ hasText: '手动任务' })).toBeVisible();
    await expect(tabs.filter({ hasText: '自动任务' })).toBeVisible();
    await expect(tabs.filter({ hasText: '评估历史' })).toBeVisible();
    await expect(tabs.filter({ hasText: '策略配置' })).toBeVisible();

    // 页面副标题（tasks.vue ClpmPageToolbar subtitle）
    const pageText = await page.locator('body').innerText();
    expect(pageText).toContain('管理手动重算任务');

    expect(page.url()).toContain('/metric/tasks');
  });

  // E2E-TASK-002: 「手动任务」Tab 内容（需显式切换：现行 tasks.vue 中 ADMIN
  // 默认激活 Tab 已改为「自动任务」，不再默认落在手动任务）
  test('E2E-TASK-002: 手动任务 Tab 表格与工具栏', async ({ page }) => {
    await page.goto('/metric/tasks');
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(2000);

    // 显式切到「手动任务」Tab（已在则幂等）
    await page.locator('.ant-tabs-tab').filter({ hasText: '手动任务' }).click();
    await page.waitForTimeout(1500);

    // 表格或空态可见（BACKFILL 任务为 0 时 Phase 1 空态改造渲染 ClpmEmptyState，
    // 数据依赖：容忍 .ant-empty / 空态容器替代表格）
    const tableOrEmpty = page
      .locator('.ant-table, .ant-empty, [class*="empty"]')
      .first();
    await expect(tableOrEmpty).toBeVisible({ timeout: 15_000 });

    // 有表格数据时校验表头（recompute.vue columns：任务标题、评估状态）
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
      expect(headerText).toContain('任务标题');
      expect(headerText).toContain('状态');
    }

    // 工具栏按钮：新建任务(primary) / 刷新 / 查询
    await expect(page.getByRole('button', { name: /新建任务/ })).toBeVisible();
    await expect(page.getByRole('button', { name: /刷\s*新/ }).first()).toBeVisible();
    await expect(page.getByRole('button', { name: /查\s*询/ }).first()).toBeVisible();

    // 状态筛选 Select（placeholder 状态筛选）
    const statusSelect = page
      .locator('.ant-select')
      .filter({ hasText: '状态筛选' })
      .first();
    await expect(statusSelect).toBeVisible();
  });

  // E2E-TASK-003: 新建任务 Drawer（dry-run 预览，不提交）
  test('E2E-TASK-003: 新建任务 Drawer 表单', async ({ page }) => {
    await page.goto('/metric/tasks');
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(2000);

    // 显式切到「手动任务」Tab：「新建任务」仅存在于手动任务工具栏
    // （ADMIN 默认激活 Tab 已改为「自动任务」）
    await page.locator('.ant-tabs-tab').filter({ hasText: '手动任务' }).click();
    await page.waitForTimeout(1500);

    // 点击「新建任务」打开 Drawer
    await page.getByRole('button', { name: /新建任务/ }).click();
    const drawer = page.locator('.ant-drawer');
    await expect(drawer).toBeVisible({ timeout: 10_000 });
    await expect(page.locator('.ant-drawer-title')).toContainText('新建任务');

    // 表单字段：任务标题 Input（recompute.vue placeholder 请输入任务标题）
    await expect(
      drawer.locator('input[placeholder="请输入任务标题"]'),
    ).toBeVisible();

    // 时间窗 RangePicker
    await expect(drawer.locator('.ant-picker').first()).toBeVisible();

    // 装置 TreeSelect（placeholder 不选=全部装置）
    await expect(
      drawer.locator('.ant-select-selection-placeholder', {
        hasText: '不选=全部装置',
      }),
    ).toBeVisible();

    // 回路 Select（placeholder 不选=对应装置全部回路）
    await expect(
      drawer.locator('.ant-select-selection-placeholder', {
        hasText: '不选=对应装置全部回路',
      }),
    ).toBeVisible();

    // footer 按钮：取消 / 预览影响范围 / 确认重算
    await expect(
      drawer.getByRole('button', { name: /取\s*消/ }),
    ).toBeVisible();
    await expect(
      drawer.getByRole('button', { name: /预览影响范围/ }),
    ).toBeVisible();
    const submitBtn = drawer.getByRole('button', { name: /确认重算/ });
    await expect(submitBtn).toBeVisible();

    // 「确认重算」初始 disabled（recompute.vue :disabled="!previewResult"）
    await expect(submitBtn).toBeDisabled();

    // 填写标题后点「预览影响范围」（dry-run 无副作用；不强制断言预览结果）
    await drawer.locator('input[placeholder="请输入任务标题"]').fill('E2E 预览测试');
    await drawer.getByRole('button', { name: /预览影响范围/ }).click();
    await page.waitForTimeout(3000);
    const hasPreview = await drawer
      .getByText(/影响范围预览|回路数/)
      .first()
      .isVisible()
      .catch(() => false);
    if (hasPreview) {
      // 预览成功后「确认重算」变为 enabled（仍不点击，避免产生真实回填任务）
      await expect(submitBtn).toBeEnabled({ timeout: 5_000 });
    }

    // 关闭 Drawer（多策略兜底）
    await closeDrawer(page);
    await expect(page.locator('.ant-drawer-title').first()).not.toBeVisible({
      timeout: 10_000,
    });
  });

  // E2E-TASK-004: 自动任务 Tab
  test('E2E-TASK-004: 自动任务 Tab', async ({ page }) => {
    await page.goto('/metric/tasks');
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(2000);

    // 切换到「自动任务」Tab（tasks.vue 切换时 tabKeys 自增强制重载）
    await page.locator('.ant-tabs-tab').filter({ hasText: '自动任务' }).click();
    await page.waitForTimeout(2000);

    // 当前可见 TabPane（未激活的 TabPane 保留在 DOM 但隐藏）
    const activePane = page.locator('.ant-tabs-tabpane:visible');

    // 按钮存在（task/list.vue 工具栏）——只断言可见，不点击
    // （触发标准评估会产生真实任务，批量删除会删数据）
    await expect(
      activePane.getByRole('button', { name: /触发标准评估/ }),
    ).toBeVisible();
    await expect(
      activePane.getByRole('button', { name: /批量删除/ }),
    ).toBeVisible();
    await expect(
      activePane.getByRole('button', { name: /刷\s*新/ }),
    ).toBeVisible();
    await expect(
      activePane.getByRole('button', { name: /查\s*询/ }),
    ).toBeVisible();

    // 表头包含 任务标题/任务类型/评估状态/评估进度（task/list.vue columns）
    const headerText = await activePane
      .locator('.ant-table-thead')
      .first()
      .innerText();
    expect(headerText).toContain('任务标题');
    expect(headerText).toContain('任务类型');
    expect(headerText).toContain('评估状态');
    expect(headerText).toContain('评估进度');

    // 若有数据行（防御性）：点击第一行打开「任务详情」Drawer
    const firstRow = activePane.locator('.ant-table-tbody tr.ant-table-row').first();
    const hasRow = await firstRow.isVisible().catch(() => false);
    if (hasRow) {
      await firstRow.click();
      await expect(page.locator('.ant-drawer')).toBeVisible({ timeout: 10_000 });
      await expect(page.locator('.ant-drawer-title')).toContainText('任务详情');
      await closeDrawer(page);
      await expect(page.locator('.ant-drawer-title').first()).not.toBeVisible({
        timeout: 10_000,
      });
    }
  });

  // E2E-TASK-005: 状态筛选下拉选项
  test('E2E-TASK-005: 状态筛选下拉选项', async ({ page }) => {
    await page.goto('/metric/tasks');
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(2000);

    // 点击状态筛选 Select（recompute.vue placeholder 状态筛选）
    const statusSelect = page
      .locator('.ant-select')
      .filter({ hasText: '状态筛选' })
      .first();
    await expect(statusSelect).toBeVisible({ timeout: 15_000 });
    await statusSelect.click();

    // 下拉选项（状态映射：PENDING→待执行 RUNNING→执行中 SUCCESS→成功 FAILED→失败 CANCELLED→已取消）
    const options = page.locator(
      '.ant-select-dropdown:visible .ant-select-item-option',
    );
    await expect(options.first()).toBeVisible({ timeout: 5_000 });
    const optionTexts = await options.allInnerTexts();
    for (const label of ['待执行', '执行中', '成功', '失败', '已取消']) {
      expect(
        optionTexts.some((t) => t.includes(label)),
        `状态筛选选项应包含「${label}」`,
      ).toBeTruthy();
    }

    // Escape 收起下拉
    await page.keyboard.press('Escape');
    await expect(
      page.locator('.ant-select-dropdown:visible'),
    ).toHaveCount(0, { timeout: 5_000 });
  });

  // E2E-TASK-006: PE_ENGINEER 无权限
  test('E2E-TASK-006: PE_ENGINEER 不可访问', async ({ page, loginAs, logout }) => {
    // 先清除当前 ADMIN 登录态，再以 PE_ENGINEER 登录
    await logout();
    await loginAs('PE_ENGINEER');

    // 验证左侧菜单不包含「评估任务」（metric.ts authority: ADMIN/IC_ENGINEER）
    await page.goto('/');
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(2000);
    const menuItem = page.getByText('评估任务', { exact: false }).first();
    const hasMenu = await menuItem.isVisible().catch(() => false);
    expect(hasMenu).toBeFalsy();

    // 直接访问 URL：PE_ENGINEER 无权限
    // 路由守卫可能拦截（重定向到 403/首页）或允许访问但页面无操作按钮
    await page.goto('/metric/tasks');
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(2000);

    const redirected = !page.url().includes('/metric/tasks');
    const createBtn = page.getByRole('button', { name: /新建任务/ }).first();
    const hasCreateBtn = await createBtn.isVisible().catch(() => false);
    // 两种结果都接受：被重定向，或页面无「新建任务」按钮
    expect(redirected || !hasCreateBtn).toBeTruthy();
  });
});
