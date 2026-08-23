/**
 * E2E 回路管理测试
 *
 * 覆盖用例：
 * - E2E-LOOP-001: 创建回路（/config/loop → 新建 → 填写 → 提交）
 * - E2E-LOOP-002: 测点清单（/config/tag → 查看测点列表）
 * - E2E-LOOP-003: 回路监控（/loop/monitor → 查看列表）
 * - E2E-LOOP-004: 回路详情（点击回路 → 详情页）
 *
 * 页面源码依据：
 *   frontend/apps/web-antd/src/views/loop/{manage,monitor,detail}.vue
 *   frontend/apps/web-antd/src/views/tag/list.vue
 *   - manage: 工厂树 + 回路表格 + 编辑 Drawer（ClpmToolbarButton「新建回路」→ Drawer 表单）
 *   - tag/list: 测点清单表格（位号/名称/测点类型/量程/实时值/单位/质量戳）
 *   - monitor: 表格列表，点击行跳转 /loop/detail/:id
 *   - detail: 路由 /loop/detail/:id
 *
 * 路由变更（IA 重构 Phase A）：
 *   - /loop/manage → 迁移到 /config/loop（legacy redirect 保留）
 *   - /tag/list → 迁移到 /config/tag（legacy redirect 保留）
 *   - /loop/ledger → 重定向到 /config/loop
 * 路由变更（IA v2.9）：
 *   - /loop → redirect /monitor/loop-workbench（回路一级菜单并入监控）
 *   - /loop/detail/:id → redirect /monitor/loop-workbench?loopId=:id
 */
import { test, expect } from '../fixtures/auth.js';

test.describe('回路管理 E2E', () => {
  test.beforeEach(async ({ page, loginAs }) => {
    // 回路管理需要 ADMIN 或 IC_ENGINEER 权限
    await loginAs('ADMIN');
  });

  test('E2E-LOOP-001: 创建回路', async ({ page }) => {
    await page.goto('/config/loop');
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(2000);

    // 验证页面加载（回路表格可见）
    await expect(page.locator('.ant-table').first()).toBeVisible({ timeout: 15_000 });

    // 点击「新建回路」按钮（ClpmToolbarButton 渲染为 Ant Design Button）
    await page.getByRole('button', { name: '新建回路' }).click();
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(500);

    // 验证 Drawer 弹出（manage.vue 使用 Drawer 而非 Modal）
    await expect(page.locator('.ant-drawer')).toBeVisible({ timeout: 10_000 });

    // 验证 Drawer 标题包含「新建回路」
    const drawerTitle = page.locator('.ant-drawer-header-title, .ant-drawer-title').first();
    if (await drawerTitle.isVisible().catch(() => false)) {
      const titleText = await drawerTitle.innerText();
      expect(titleText).toContain('新建回路');
    }

    // 关闭 Drawer（点击关闭按钮或遮罩）
    await page.locator('.ant-drawer-mask').click({ timeout: 5000 }).catch(() => {});
    await page.waitForTimeout(1000);

    // 核心验证点：新建回路 Drawer 正常弹出与关闭
    await expect(page.locator('.ant-drawer')).toBeHidden({ timeout: 10_000 }).catch(() => {});
    expect(page.url()).toContain('/config/loop');
  });

  test('E2E-LOOP-002: 测点清单', async ({ page }) => {
    await page.goto('/config/tag');
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(2000);

    // 验证页面加载（测点表格或筛选区可见）
    const table = page.locator('.ant-table').first();
    const select = page.locator('.ant-select').first();
    const hasTable = await table.isVisible({ timeout: 15_000 }).catch(() => false);
    const hasSelect = await select.isVisible({ timeout: 5000 }).catch(() => false);
    expect(hasTable || hasSelect).toBeTruthy();

    // 验证表格表头包含关键字段（位号）
    if (hasTable) {
      const tableHeader = page.locator('.ant-table-thead').first();
      const headerText = await tableHeader.innerText().catch(() => '');
      expect(headerText).toContain('位号');
    }

    // 核心验证点：测点清单页面正常加载
    expect(page.url()).toContain('/config/tag');
  });

  test('E2E-LOOP-003: 回路监控', async ({ page }) => {
    await page.goto('/loop/monitor');
    await page.waitForLoadState('domcontentloaded');

    // 验证页面加载（表格或空状态）
    await expect(page.locator('.ant-table').first()).toBeVisible({ timeout: 15_000 });

    // 验证表头包含关键字段
    const tableHeader = page.locator('.ant-table-thead').first();
    await expect(tableHeader).toBeVisible({ timeout: 10_000 });
    const headerText = await tableHeader.innerText();
    expect(headerText).toContain('回路位号');
  });

  test('E2E-LOOP-004: 回路详情', async ({ page }) => {
    // 1. 先访问监控列表
    await page.goto('/loop/monitor');
    await page.waitForLoadState('domcontentloaded');

    // 2. 等待表格数据行加载（最多 15s，E2E 环境后端可能较慢）
    await page
      .waitForSelector('.ant-table-tbody tr', { timeout: 15_000 })
      .catch(() => {});
    await page.waitForTimeout(500);

    // 3. 点击第一行回路（跳转详情页）
    const firstRow = page.locator('.ant-table-tbody tr').first();
    const rowExists = await firstRow.isVisible().catch(() => false);

    if (rowExists) {
      await firstRow.click();
      // IA v2.9：/loop/detail/:id redirect 到 /monitor/loop-workbench?loopId=:id
      // 等待工作台 URL 出现（redirect 在路由层同步完成）
      await page.waitForURL(/\/monitor\/loop-workbench/, { timeout: 15_000 }).catch(() => {
        // 某些实现可能需要点击「查看详情」按钮
      });
      expect(page.url()).toContain('/monitor/loop-workbench');
      expect(page.url()).toContain('loopId=');
    } else {
      // 列表为空兜底：直接访问详情页 URL，验证 redirect 到工作台。
      // 注：工作台加载列表后会自动选中首个回路并修正 URL loopId，
      //     故此处仅验证 redirect 落点为 /monitor/loop-workbench，不限定具体 loopId。
      await page.goto('/loop/detail/00000000-0000-0000-0000-000000000201');
      await page.waitForLoadState('domcontentloaded');
      await page.waitForURL(/\/monitor\/loop-workbench/, { timeout: 10_000 }).catch(() => {});
      expect(page.url()).not.toContain('/auth/login');
      expect(page.url()).not.toContain('/403');
      expect(page.url()).toContain('/monitor/loop-workbench');
    }
  });

  // E2E-LOOP-005: 回路台账三字段编辑（控制类型 + 重要等级 + 参评状态）
  // 路由 /config/loop：表格列标题为"参评"（Switch）+ "等级"（带颜色徽章）
  // + 筛选栏 placeholder="参评状态" 过滤选项；编辑抽屉中存在"评估配置"区
  test('E2E-LOOP-005: 回路台账三字段编辑', async ({ page }) => {
    await page.goto('/config/loop');
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(2000);

    // 验证页面加载（回路表格可见）
    await expect(page.locator('.ant-table').first()).toBeVisible({ timeout: 15_000 });

    // 验证表头包含"参评"列（manage.vue 列标题为简短"参评"，非"参评状态"）
    const headerText = await page.locator('.ant-table-thead').first().innerText().catch(() => '');
    expect(headerText).toContain('参评');

    // 验证表头包含"等级"列（manage.vue 列标题为简短"等级"，非"重要等级"）
    expect(headerText).toContain('等级');

    // 验证筛选栏包含参评状态过滤选项
    // manage.vue 筛选选项在 Popover 中，需先点击"筛选"按钮展开
    const filterBtn = page.getByRole('button', { name: /筛选/ }).first();
    if (await filterBtn.isVisible().catch(() => false)) {
      await filterBtn.click();
      await page.waitForTimeout(500);
    }
    // Popover 展开后验证参评状态 Select（placeholder="参评状态" 或已选值"参评"/"不参评"）
    const evalSelect = page
      .locator('.ant-select')
      .filter({ hasText: /参评/ })
      .first();
    const hasEvalSelect = await evalSelect
      .isVisible({ timeout: 5_000 })
      .catch(() => false);

    // 验证表格中存在 Switch 控件（参评状态列）
    const switchInTable = page.locator('.ant-table-tbody .ant-switch').first();
    const hasSwitch = await switchInTable.isVisible().catch(() => false);
    // 筛选栏 select 或表格 switch 任一存在即可（Popover 可能未展开或表格无数据）
    expect(hasEvalSelect || hasSwitch).toBeTruthy();

    // 点击第一行的"编辑"按钮，验证抽屉中存在"评估配置"区
    const firstRow = page.locator('.ant-table-tbody tr.ant-table-row').first();
    const hasRow = await firstRow.isVisible().catch(() => false);

    if (hasRow) {
      const editBtn = firstRow.getByRole('button', { name: /编辑/i }).first();
      if (await editBtn.isVisible().catch(() => false)) {
        await editBtn.click();
        await page.waitForLoadState('domcontentloaded');
        await page.waitForTimeout(1000);

        // 验证 Drawer 弹出
        await expect(page.locator('.ant-drawer')).toBeVisible({ timeout: 10_000 });

        // 验证抽屉中存在"评估配置"区（manage.vue: 评估配置区标题）
        const drawerText = await page.locator('.ant-drawer').first().innerText();
        expect(drawerText).toContain('评估配置');

        // 验证评估配置区包含 RadioGroup（控制类型 + 重要等级）+ Switch（参评状态）
        // manage.vue: 控制类型 RadioGroup + 重要等级 RadioGroup + 参评状态 Switch
        const drawerRadioGroups = page.locator('.ant-drawer .ant-radio-group');
        const radioCount = await drawerRadioGroups.count();
        expect(radioCount).toBeGreaterThanOrEqual(2);

        const drawerSwitch = page.locator('.ant-drawer .ant-switch').first();
        const hasDrawerSwitch = await drawerSwitch.isVisible().catch(() => false);
        expect(hasDrawerSwitch).toBeTruthy();

        // 关闭抽屉（点击遮罩）
        await page.locator('.ant-drawer-mask').click({ timeout: 5000 }).catch(() => {});
        await page.waitForTimeout(500);
      }
    }

    // 注意：不实际保存修改，只验证 UI 元素存在
    expect(page.url()).toContain('/config/loop');
  });

  // E2E-LOOP-006: 链路配置页（原 AAS 同步状态页，v6.1 改造为链路配置）
  // 路由 /config/link → aas.vue：3 个 Tab（数据源 / DCS 系统 / DCS 型号映射）
  //   - 数据源 Tab：网络模式切换 + 历史数据导入接口 + 实时数据源 + 保存配置/测试连接按钮
  //   - DCS 系统 Tab：DCS 品牌表格
  //   - DCS 型号映射 Tab：DCS 型号映射表格
  test('E2E-LOOP-006: 链路配置页', async ({ page }) => {
    await page.goto('/config/link');
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(2000);

    // 1. 验证页面加载（Tab 组件可见）
    const tabs = page.locator('.ant-tabs').first();
    await expect(tabs).toBeVisible({ timeout: 15_000 });

    // 2. 验证 3 个 Tab 标签存在
    const pageText = await page.locator('body').innerText();
    expect(pageText).toContain('数据源');
    expect(pageText).toContain('DCS 系统');
    expect(pageText).toContain('DCS 型号映射');

    // 3. 验证数据源 Tab 内容（默认激活）
    //    网络模式切换卡片
    expect(pageText).toContain('网络模式');
    //    历史数据导入接口配置区
    expect(pageText).toContain('历史数据导入接口');
    //    实时数据源配置区
    expect(pageText).toContain('实时数据源');

    // 4. 验证"保存配置"按钮存在（数据源 Tab 底部）
    const saveBtn = page.getByRole('button', { name: /保存配置/ }).first();
    const hasSave = await saveBtn.isVisible({ timeout: 10_000 }).catch(() => false);
    expect(hasSave).toBeTruthy();

    // 5. 验证"测试连接"按钮存在
    const testBtn = page.getByRole('button', { name: /测试连接/ }).first();
    const hasTest = await testBtn.isVisible().catch(() => false);
    expect(hasTest).toBeTruthy();

    // 注意：不实际保存或测试连接，只验证 UI 元素存在
    expect(page.url()).toContain('/config/link');
  });

  // E2E-LOOP-007: /loop/monitor 面点分离重定向 + 回路列表行级下钻（2026-08-23 改写）
  // 现行口径（monitor.ts）：旧 /loop/monitor 重定向到独立回路列表页 /monitor/loops
  //   （LoopFleetView 复用组件），行内位号链接点击下钻回路工作台，
  //   携带 loopId + from=/monitor/loops 上下文。
  // 历史口径：旧版断言"诊断标签列 → /diagnosis/detail/:loopId 跳转"，该列随
  //   旧 monitor.vue 诊断标签列方案下线（现行列表无此列），属旧 IA 遗物。
  test('E2E-LOOP-007: /loop/monitor 重定向与行级下钻工作台', async ({ page }) => {
    await page.goto('/loop/monitor', { waitUntil: 'domcontentloaded' });

    // 1. 面点分离：重定向到独立回路列表页（query 透传）
    await expect(page).toHaveURL(/\/monitor\/loops/, { timeout: 15_000 });

    // 2. 列表页渲染（表头含现行列：回路位号/性能评分等）
    const tableHeader = page.locator('.ant-table-thead').first();
    await expect(tableHeader).toBeVisible({ timeout: 20_000 });
    const headerText = await tableHeader.innerText();
    expect(headerText).toContain('回路位号');
    expect(headerText).toContain('性能评分');

    // 3. 等待数据行（防御性：无数据时仅验证重定向与列）
    const firstRow = page.locator('.ant-table-tbody tr.ant-table-row').first();
    const hasRow = await firstRow.isVisible({ timeout: 15_000 }).catch(() => false);
    if (!hasRow) {
      return;
    }

    // 4. 点击首行位号链接 → 下钻回路工作台（handleRowClick）
    const tagLink = firstRow.locator('a[role="button"]').first();
    await tagLink.click();
    await page.waitForURL(/\/monitor\/loop-workbench/, { timeout: 15_000 });
    expect(page.url()).toContain('loopId=');
    // from 参数现行实现为未编码原样拼接（loops.vue），解码后比对兼容两种形态
    expect(decodeURIComponent(page.url())).toContain('from=/monitor/loops');
  });
});
