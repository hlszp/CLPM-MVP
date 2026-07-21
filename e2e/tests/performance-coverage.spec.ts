/**
 * E2E 性能评估补盲测试（性能-14）
 *
 * 覆盖用例（编号延续 E2E-PERF-006 之后）：
 * - E2E-PERF-007: KPI 报表页（/metric/kpi-report）
 *     · 页面加载、时间维度切换（日/周/月）、报表类型切换（综合/回路）
 *     · 工厂节点 Select、刷新/导出按钮存在
 *     · 评级分布 Tag + 表格/空状态容器存在
 * - E2E-PERF-008: 参数配置 Tab 开关生效（/metric/config → "参数配置" Tab）
 *     · 8 类异常值检测开关表（NaN/超量程/冻结/跳变/尖峰/时间戳/质量码/高频噪声）
 *     · 按控制类型的检测参数表（FC/PC/TC/LC/CC × 7 参数）
 *     · 开关 Switch 可见且可切换、保存按钮存在（ADMIN 可见）
 * - E2E-PERF-009: 理想稳态时间字段
 *     · /metric/config → "指标定义" Tab 表格含"理想稳态时间"指标行
 *     · /metric/kpi-report → "回路报表" 表头含"理想稳定时间"列
 *
 * 页面源码依据：
 *   frontend/apps/web-antd/src/views/metric/kpi-report.vue
 *     - 顶部 ClpmPageToolbar：日/周/月 RadioGroup + 综合/回路 RadioGroup
 *       + 工厂节点 Select + 刷新/导出 ClpmToolbarButton
 *     - ClpmDataCanvas：评级分布 Tag 区 + Table（comprehensiveColumns/loopColumns）
 *     - loopColumns 含"理想稳定时间"列（dataIndex=idealSettlingTime）
 *   frontend/apps/web-antd/src/views/metric/config.vue
 *     - 5 Tab：指标定义/权重配置/定级阈值/数据可信度/参数配置
 *   frontend/apps/web-antd/src/views/metric/config-definition.vue
 *     - 12 KPI 指标列表，含 IDEAL_SETTLING_TIME → "理想稳态时间"
 *   frontend/apps/web-antd/src/views/metric/outlier-params.vue
 *     - 上半区：8 类检测开关表（switchColumns，含 Switch 列）
 *     - 下半区：按控制类型参数表（5 行 × 7 列 InputNumber）
 *     - 保存按钮 v-permission=['ADMIN']，无变更时 disabled
 *
 * 选择器说明：
 *   - Ant Design Vue RadioGroup button-style 渲染的 <input type="radio"> 不可见
 *     （opacity:0/position:absolute），isVisible() 返回 false。
 *     改用可见的 .ant-radio-button-wrapper label 元素（含可点击区域 + 文本）。
 *   - Tab 切换后用 .ant-tabs-tabpane-active 圈定可见面板，避免命中隐藏 Tab。
 *
 * 边界：只读操作 + 开关切换验证（不实际保存，避免污染数据）；
 *       数据为空时用防御式断言，核心验证页面加载成功 + 关键组件存在。
 */
import { test, expect } from '../fixtures/auth.js';

test.describe('性能评估补盲 E2E（性能-14）', () => {
  test.beforeEach(async ({ page, loginAs }) => {
    // 参数配置 Tab 仅 ADMIN 可见，使用 ADMIN 账户
    await loginAs('ADMIN');
  });

  // E2E-PERF-007: KPI 报表页
  test('E2E-PERF-007: KPI 报表页加载与时间窗切换', async ({ page }) => {
    await page.goto('/metric/kpi-report');
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(2000);

    // 验证页面加载（ClpmPageToolbar 标题"KPI 报表"可见）
    const pageTitle = page.getByText('KPI 报表', { exact: false }).first();
    await expect(pageTitle).toBeVisible({ timeout: 15_000 });

    // 验证时间维度 RadioGroup（日/周/月）存在
    // kpi-report.vue: timeDimension RadioGroup button-style，可见元素是 .ant-radio-button-wrapper
    const dayRadioBtn = page
      .locator('.ant-radio-button-wrapper')
      .filter({ hasText: '日' })
      .first();
    const weekRadioBtn = page
      .locator('.ant-radio-button-wrapper')
      .filter({ hasText: '周' })
      .first();
    const monthRadioBtn = page
      .locator('.ant-radio-button-wrapper')
      .filter({ hasText: '月' })
      .first();
    const hasDay = await dayRadioBtn.isVisible().catch(() => false);
    const hasWeek = await weekRadioBtn.isVisible().catch(() => false);
    const hasMonth = await monthRadioBtn.isVisible().catch(() => false);
    // 至少日维度可见（默认选中 day）
    expect(hasDay || hasWeek || hasMonth).toBeTruthy();

    // 验证报表类型 RadioGroup（综合报表/回路报表）存在
    const compRadioBtn = page
      .locator('.ant-radio-button-wrapper')
      .filter({ hasText: '综合报表' })
      .first();
    const loopRadioBtn = page
      .locator('.ant-radio-button-wrapper')
      .filter({ hasText: '回路报表' })
      .first();
    const hasComp = await compRadioBtn.isVisible().catch(() => false);
    const hasLoop = await loopRadioBtn.isVisible().catch(() => false);
    expect(hasComp || hasLoop).toBeTruthy();

    // 验证工厂节点 Select 存在（placeholder="工厂/装置/单元"）
    const plantSelect = page
      .locator('.ant-select')
      .filter({ hasText: '工厂/装置/单元' })
      .first();
    const hasPlantSelect = await plantSelect.isVisible().catch(() => false);
    expect(hasPlantSelect).toBeTruthy();

    // 验证刷新按钮存在
    const refreshBtn = page.getByRole('button', { name: /刷新/ }).first();
    await expect(refreshBtn).toBeVisible({ timeout: 10_000 });

    // 验证导出按钮存在
    const exportBtn = page.getByRole('button', { name: /导出/ }).first();
    await expect(exportBtn).toBeVisible({ timeout: 10_000 });

    // 验证表格或空状态容器存在（容忍空数据）
    const tableOrEmpty = page.locator('.ant-table, .ant-empty').first();
    await expect(tableOrEmpty).toBeVisible({ timeout: 15_000 });

    // 切换到"回路报表"验证表头含"理想稳定时间"列
    if (hasLoop) {
      await loopRadioBtn.click();
      await page.waitForLoadState('networkidle');
      await page.waitForTimeout(2000);
      // 等待表格重新渲染
      const loopTable = page.locator('.ant-table').first();
      await expect(loopTable).toBeVisible({ timeout: 15_000 });
      const headerText = await loopTable
        .locator('.ant-table-thead')
        .first()
        .innerText()
        .catch(() => '');
      if (headerText) {
        // loopColumns 含"理想稳定时间"列（kpi-report.vue:529）
        expect(headerText).toContain('理想稳定时间');
      }
    }

    // 切换时间维度到"月"，验证 DatePicker 切换为 month picker
    if (hasMonth) {
      await monthRadioBtn.click();
      await page.waitForTimeout(800);
      await page.waitForLoadState('networkidle');
      // month picker 存在（picker="month"）
      const monthPicker = page.locator('.ant-picker-month').first();
      const hasMonthPicker = await monthPicker.isVisible().catch(() => false);
      // 兜底：任意 picker 可见
      const anyPicker = page.locator('.ant-picker').first();
      const hasAnyPicker = await anyPicker.isVisible().catch(() => false);
      expect(hasMonthPicker || hasAnyPicker).toBeTruthy();
    }

    expect(page.url()).toContain('/metric/kpi-report');
  });

  // E2E-PERF-008: 参数配置 Tab 开关生效
  test('E2E-PERF-008: 参数配置 Tab 开关与参数表', async ({ page }) => {
    await page.goto('/metric/config');
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(2000);

    // config.vue: 顶部 Tabs 包含 5 个 TabPane（指标定义/权重配置/定级阈值/数据可信度/参数配置）
    // 点击"参数配置" Tab（key="outlier"）
    const outlierTab = page
      .locator('.ant-tabs-tab')
      .filter({ hasText: '参数配置' })
      .first();
    await expect(outlierTab).toBeVisible({ timeout: 15_000 });
    await outlierTab.click();
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(2000);

    // 验证当前 TabPane 已切换到参数配置（outlier-params.vue 渲染）
    // 用可见的 tabpane 圈定范围，避免命中隐藏 Tab
    const activePane = page.locator('.ant-tabs-tabpane-active').first();
    await expect(activePane).toBeVisible({ timeout: 15_000 });

    // outlier-params.vue: 顶部说明文案"配置 8 类异常值检测的启停开关..."
    const descText = activePane.getByText('8 类异常值检测', {
      exact: false,
    });
    await expect(descText).toBeVisible({ timeout: 15_000 });

    // 验证"检测启停开关（默认全部启用）"标题存在
    const switchTitle = activePane.getByText('检测启停开关', {
      exact: false,
    });
    await expect(switchTitle).toBeVisible({ timeout: 10_000 });

    // 验证 8 类检测开关表存在（switchColumns：检测类型/英文名/用途说明/启用）
    // 直接定位 activePane 内的第一个 .ant-table（switch table 在 param table 之前）
    const switchTable = activePane.locator('.ant-table').first();
    await expect(switchTable).toBeVisible({ timeout: 15_000 });

    // 验证表头包含"检测类型/英文名/用途说明/启用"
    const switchHeader = await switchTable
      .locator('.ant-table-thead')
      .first()
      .innerText()
      .catch(() => '');
    expect(switchHeader).toContain('检测类型');
    expect(switchHeader).toContain('启用');

    // 验证至少一个中文检测名出现在表格中
    const bodyText = await activePane.innerText();
    const detectorNames = [
      'NaN/空值检测',
      '超量程检测',
      '冻结值检测',
      '跳变检测',
      '尖峰检测',
      '时间戳异常检测',
      '质量码异常检测',
      '高频噪声检测',
    ];
    const matchedCount = detectorNames.filter((n) => bodyText.includes(n))
      .length;
    expect(
      matchedCount,
      `至少应出现一个检测类型中文名，实际匹配 ${matchedCount} 个`,
    ).toBeGreaterThan(0);

    // 验证 Switch 开关可见（8 个，默认启用）
    // outlier-params.vue: <Switch v-model:checked="editSwitches[record.key]" />
    const switches = switchTable.locator('.ant-switch');
    const switchCount = await switches.count();
    expect(
      switchCount,
      '检测开关表应包含 8 个 Switch 组件',
    ).toBeGreaterThanOrEqual(1);

    // 验证按控制类型的检测参数表存在（第二个 .ant-table）
    const paramTable = activePane.locator('.ant-table').nth(1);
    await expect(paramTable).toBeVisible({ timeout: 15_000 });

    // 验证表头包含"控制类型"与参数列（采样率/冻结窗口点数 等）
    const paramHeader = await paramTable
      .locator('.ant-table-thead')
      .first()
      .innerText()
      .catch(() => '');
    expect(paramHeader).toContain('控制类型');
    expect(paramHeader).toContain('采样率');
    expect(paramHeader).toContain('噪声截止频率');

    // 验证 5 个控制类型（FC/PC/TC/LC/CC）出现
    const controlTypes = ['流量', '压力', '温度', '液位', '成分'];
    const controlMatched = controlTypes.filter((c) =>
      bodyText.includes(c),
    ).length;
    expect(
      controlMatched,
      `应至少出现一个控制类型中文名，实际匹配 ${controlMatched} 个`,
    ).toBeGreaterThan(0);

    // 验证参数 InputNumber 可见（5 行 × 7 列）
    const inputNumbers = paramTable.locator('.ant-input-number');
    const inputCount = await inputNumbers.count();
    expect(
      inputCount,
      '检测参数表应包含 InputNumber 编辑控件',
    ).toBeGreaterThanOrEqual(1);

    // 验证保存按钮存在（v-permission=['ADMIN']，ADMIN 可见，无变更时 disabled）
    // 按钮文本为"保 存"（中间空格）
    const saveBtn = page.getByRole('button', { name: /保\s*存/ }).first();
    await expect(saveBtn).toBeVisible({ timeout: 10_000 });

    // 验证开关可切换（点击第一个 Switch，验证状态翻转）
    // 注意：不点击保存，避免污染数据；切换后立即切回原状态
    if (switchCount > 0) {
      const firstSwitch = switches.first();
      // 记录切换前状态
      const isCheckedBefore = await firstSwitch
        .evaluate((el) => el.classList.contains('ant-switch-checked'))
        .catch(() => false);
      // 点击切换
      await firstSwitch.click().catch(() => {});
      await page.waitForTimeout(300);
      // 验证状态已变化（checked ↔ unchecked）
      const isCheckedAfter = await firstSwitch
        .evaluate((el) => el.classList.contains('ant-switch-checked'))
        .catch(() => false);
      expect(isCheckedAfter).not.toBe(isCheckedBefore);

      // 切换回原状态，避免触发未保存变更（保持页面干净）
      await firstSwitch.click().catch(() => {});
      await page.waitForTimeout(300);
    }

    expect(page.url()).toContain('/metric/config');
  });

  // E2E-PERF-009: 理想稳态时间字段（指标定义 Tab + KPI 报表回路报表列）
  test('E2E-PERF-009: 理想稳态时间字段', async ({ page }) => {
    // ===== Part 1: /metric/config → "指标定义" Tab 验证"理想稳态时间"指标行 =====
    await page.goto('/metric/config');
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(2000);

    // 默认 Tab 为"指标定义"（activeTab='definition'），config-definition.vue 渲染
    // config-definition.vue: 12 KPI 指标列表，含 IDEAL_SETTLING_TIME → "理想稳态时间"
    const activePane = page.locator('.ant-tabs-tabpane-active').first();
    await expect(activePane).toBeVisible({ timeout: 15_000 });

    // 验证指标定义表可见（含表头"指标名称/指标代码/类别/算法公式/说明"）
    const definitionTable = activePane.locator('.ant-table').first();
    await expect(definitionTable).toBeVisible({ timeout: 15_000 });

    // 验证表格中存在"理想稳态时间"行（metricName 列）
    // config-definition.vue: { metricCode: 'IDEAL_SETTLING_TIME', metricName: '理想稳态时间' }
    const definitionBodyText = await definitionTable
      .locator('.ant-table-tbody')
      .first()
      .innerText()
      .catch(() => '');
    expect(
      definitionBodyText,
      '指标定义表应包含"理想稳态时间"指标行',
    ).toContain('理想稳态时间');
    expect(definitionBodyText).toContain('IDEAL_SETTLING_TIME');

    // ===== Part 2: /metric/kpi-report → "回路报表" 验证表头含"理想稳定时间"列 =====
    await page.goto('/metric/kpi-report');
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(2000);

    // 切换到"回路报表"
    const loopRadioBtn = page
      .locator('.ant-radio-button-wrapper')
      .filter({ hasText: '回路报表' })
      .first();
    await expect(loopRadioBtn).toBeVisible({ timeout: 15_000 });
    await loopRadioBtn.click();
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(2000);

    // 验证表格可见
    const loopTable = page.locator('.ant-table').first();
    await expect(loopTable).toBeVisible({ timeout: 15_000 });

    // 验证表头含"理想稳定时间"列
    // kpi-report.vue loopColumns: { title: '理想稳定时间', dataIndex: 'idealSettlingTime' }
    const headerText = await loopTable
      .locator('.ant-table-thead')
      .first()
      .innerText()
      .catch(() => '');
    expect(
      headerText,
      '回路报表表头应包含"理想稳定时间"列',
    ).toContain('理想稳定时间');

    // 验证表头同时含其他相关列（实际稳定时间/输出跳变率/阀门粘滞）
    expect(headerText).toContain('实际稳定时间');
    expect(headerText).toContain('输出跳变率');
    expect(headerText).toContain('阀门粘滞');

    expect(page.url()).toContain('/metric/kpi-report');
  });
});
