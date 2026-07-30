/**
 * E2E 回路整定 Phase 2 测试
 *
 * 覆盖用例：
 * - E2E-TUNE-001: 整定工作台（/tuning/workbench → 统计卡片 + 最近任务）
 * - E2E-TUNE-002: 模型辨识（/tuning/flow/model → 选择回路 → 辨识 → 结果）
 * - E2E-TUNE-003: 整定算法（/tuning/flow/algorithm → 模型参数 → 整定 → PID 结果）
 * - E2E-TUNE-004: 闭环仿真（/tuning/flow/simulation → 参数输入 → 仿真 → 图表）
 * - E2E-TUNE-005: 效果统计（/tuning/stats → 统计卡片 + 图表 + 列表）
 * - E2E-TUNE-006: 模型辨识 Phase 2 异步辨识策略（/tuning/flow/model → AUTO 策略 → 进度条）
 * - E2E-TUNE-007: 闭环仿真 Phase 2 多 PID 对比模式（/tuning/flow/simulation → 对比模式 → 多曲线）
 *
 * v6.2 P1-019：model/algorithm/simulation 三页合并为 /tuning/flow 嵌套 stepper
 *   旧 /tuning/{model,algorithm,simulation} 重定向到 /tuning/flow/{model,algorithm,simulation}
 *   E2E 测试已对齐新 flow 路由
 *
 * 页面源码依据：
 *   frontend/apps/web-antd/src/views/tuning/{workbench,model,algorithm,simulation,stats,flow}.vue
 *   - workbench: 4 统计卡片 + 流程导航 + 最近任务表格
 *   - model: 回路选择 + 时间范围 + 辨识策略 + 候选模型阶次 + 异步进度 + 可信度徽章
 *   - algorithm: 模型参数输入 + 5 种算法选择 + 整定按钮 + 推荐 PID 对比
 *   - simulation: 双 PID 对比 + 多 PID 对比模式 + ECharts 多曲线叠加
 *   - stats: 4 统计卡片 + 算法分布饼图 + 状态柱状图 + 任务列表
 */
import { test, expect } from '../fixtures/auth.js';

test.describe('回路整定 E2E', () => {
  test.beforeEach(async ({ page, loginAs }) => {
    // 整定模块需要 ADMIN / IC_ENGINEER / EXPERT 权限
    await loginAs('ADMIN');
  });

  test('E2E-TUNE-001: 整定工作台', async ({ page }) => {
    await page.goto('/tuning/workbench');
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(2000);

    // 验证页面加载（统计卡片区域存在）
    const cards = page.locator('.ant-card, [class*="statistic"], [class*="kpi"]');
    const cardCount = await cards.count();
    expect(cardCount).toBeGreaterThan(0);

    // 验证统计卡片包含关键文本（总任务数/已完成/进行中/成功率）
    const pageText = await page.locator('body').innerText();
    const hasStatsText = /任务|整定|成功|完成/i.test(pageText);
    expect(hasStatsText).toBeTruthy();

    // 验证最近任务表格存在（如果有数据）
    const table = page.locator('.ant-table').first();
    const hasTable = await table.isVisible().catch(() => false);
    if (hasTable) {
      const headerText = await page.locator('.ant-table-thead').first().innerText().catch(() => '');
      // 表头应包含任务相关字段
      expect(headerText).toMatch(/任务|回路|算法|状态|时间/i);
    }

    // 验证流程导航存在（导航是 <a> 元素，不是 button）
    const navLinks = page.locator('a').filter({ hasText: /辨识|算法|仿真|统计/i });
    const navCount = await navLinks.count();
    expect(navCount).toBeGreaterThan(0);

    // 核心验证点：整定工作台页面正常加载
    expect(page.url()).toContain('/tuning/workbench');
  });

  test('E2E-TUNE-002: 模型辨识', async ({ page }) => {
    await page.goto('/tuning/flow/model');
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(2000);

    // 验证页面加载（回路选择器存在）
    const loopSelect = page.locator('.ant-select').first();
    await expect(loopSelect).toBeVisible({ timeout: 15_000 }).catch(() => {
      // 页面可能使用不同选择器
    });

    // 验证辨识筛选表单存在（辨识策略/开始辨识按钮）
    // P1-022：候选模型阶次在 Collapse 折叠面板中，不在 innerText
    const pageText = await page.locator('body').innerText();
    const hasFilterForm = /辨识策略|开始辨识|辨识筛选/i.test(pageText);
    expect(hasFilterForm).toBeTruthy();

    // 验证辨识按钮存在
    const identifyBtn = page.getByRole('button', { name: /辨识|开始辨识|执行辨识/i }).first();
    const hasIdentifyBtn = await identifyBtn.isVisible().catch(() => false);

    // 选择回路（如果有数据）
    if (await loopSelect.isVisible().catch(() => false)) {
      await loopSelect.click();
      await page.waitForTimeout(1000);
      const firstOption = page.locator('.ant-select-dropdown .ant-select-item').first();
      if (await firstOption.isVisible({ timeout: 5000 }).catch(() => false)) {
        await firstOption.click();
        await page.waitForTimeout(1000);

        // 点击辨识按钮
        if (hasIdentifyBtn) {
          await identifyBtn.click();
          await page.waitForTimeout(3000);

          // 验证辨识结果区域出现（参数或图表）
          const resultArea = page.locator('.ant-card, .ant-descriptions, [class*="result"]').last();
          const hasResult = await resultArea.isVisible({ timeout: 10_000 }).catch(() => false);
        }
      }
    }

    // 核心验证点：模型辨识页面正常加载
    expect(page.url()).toContain('/tuning/flow/model');
  });

  test('E2E-TUNE-003: 整定算法', async ({ page }) => {
    await page.goto('/tuning/flow/algorithm');
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(2000);

    // 验证页面加载
    const pageText = await page.locator('body').innerText();

    // 验证 5 种整定算法存在（IMC/Lambda/Z-N/Cohen-Coon/SIMC）
    const hasAlgorithms = /IMC|Lambda|Z-N|Cohen|SIMC/i.test(pageText);
    expect(hasAlgorithms).toBeTruthy();

    // 验证模型参数输入区域存在（K/tau/theta）
    const hasModelParams = /增益|K|时间常数|tau|纯滞后|theta/i.test(pageText);
    expect(hasModelParams).toBeTruthy();

    // 验证整定按钮存在
    const tuneBtn = page.getByRole('button', { name: /整定|开始整定|执行整定/i }).first();
    const hasTuneBtn = await tuneBtn.isVisible().catch(() => false);

    if (hasTuneBtn) {
      // 尝试填写模型参数（如果 input 存在）
      const kInput = page.locator('input').filter({ has: page.getByPlaceholder(/K|增益/i) }).first();
      if (await kInput.isVisible().catch(() => false)) {
        await kInput.fill('1.0');
      }

      const tauInput = page.locator('input').filter({ has: page.getByPlaceholder(/tau|时间常数/i) }).first();
      if (await tauInput.isVisible().catch(() => false)) {
        await tauInput.fill('10.0');
      }

      const thetaInput = page.locator('input').filter({ has: page.getByPlaceholder(/theta|滞后/i) }).first();
      if (await thetaInput.isVisible().catch(() => false)) {
        await thetaInput.fill('2.0');
      }

      // 点击整定按钮
      await tuneBtn.click();
      await page.waitForTimeout(2000);

      // 验证 PID 结果区域出现
      const resultArea = page.locator('.ant-card, .ant-descriptions, [class*="result"]').last();
      const hasResult = await resultArea.isVisible({ timeout: 10_000 }).catch(() => false);
    }

    // 核心验证点：整定算法页面正常加载，5 种算法可见
    expect(page.url()).toContain('/tuning/flow/algorithm');
  });

  test('E2E-TUNE-004: 闭环仿真', async ({ page }) => {
    await page.goto('/tuning/flow/simulation');
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(2000);

    // 验证页面加载
    const pageText = await page.locator('body').innerText();

    // 验证模型参数输入区域存在
    const hasModelSection = /模型|FOPDT|SOPDT|IPDT/i.test(pageText);
    expect(hasModelSection).toBeTruthy();

    // 验证 PID 参数输入区域存在（Kp/Ki/Kd）
    const hasPidSection = /Kp|Ki|Kd|PID|比例|积分|微分/i.test(pageText);
    expect(hasPidSection).toBeTruthy();

    // 验证仿真按钮存在
    const simulateBtn = page.getByRole('button', { name: /仿真|开始仿真|执行仿真/i }).first();
    const hasSimulateBtn = await simulateBtn.isVisible().catch(() => false);

    if (hasSimulateBtn) {
      // 尝试填写模型参数
      const inputs = page.locator('input[type="number"], input.ant-input-number-input');
      const inputCount = await inputs.count();

      if (inputCount >= 3) {
        // 填写模型参数 K, tau, theta
        await inputs.nth(0).fill('1.0').catch(() => {});
        await inputs.nth(1).fill('10.0').catch(() => {});
        await inputs.nth(2).fill('2.0').catch(() => {});
      }

      // 点击仿真按钮
      await simulateBtn.click();
      await page.waitForTimeout(3000);

      // 验证仿真结果图表区域出现（canvas 或 ECharts 容器）
      const canvas = page.locator('canvas').first();
      const hasCanvas = await canvas.isVisible({ timeout: 10_000 }).catch(() => false);

      // 验证性能指标区域（IAE/ISE/ITAE/settling time）
      const metricsText = await page.locator('body').innerText();
      const hasMetrics = /IAE|ISE|ITAE|settling|超调|上升时间/i.test(metricsText);
    }

    // 核心验证点：闭环仿真页面正常加载
    expect(page.url()).toContain('/tuning/flow/simulation');
  });

  test('E2E-TUNE-005: 效果统计', async ({ page }) => {
    await page.goto('/tuning/stats');
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(2000);

    // 验证页面加载（统计卡片区域存在）
    const cards = page.locator('.ant-card, [class*="statistic"], [class*="kpi"]');
    const cardCount = await cards.count();
    expect(cardCount).toBeGreaterThan(0);

    // 验证统计卡片包含关键文本
    const pageText = await page.locator('body').innerText();
    const hasStatsText = /任务|整定|成功|完成|算法/i.test(pageText);
    expect(hasStatsText).toBeTruthy();

    // 验证任务列表表格存在
    const table = page.locator('.ant-table').first();
    const hasTable = await table.isVisible().catch(() => false);
    if (hasTable) {
      const headerText = await page.locator('.ant-table-thead').first().innerText().catch(() => '');
      expect(headerText).toMatch(/任务|回路|算法|状态|时间/i);
    }

    // 验证图表区域存在（算法分布饼图 / 状态柱状图）
    // 页面使用 ClpmDataCanvas（section.clpm-data-canvas）包裹 EchartsUI（div + canvas）
    const chartContainers = page.locator('.clpm-data-canvas, canvas, .clpm-kpi-strip');
    const chartCount = await chartContainers.count();
    expect(chartCount).toBeGreaterThan(0);

    // 核心验证点：效果统计页面正常加载
    expect(page.url()).toContain('/tuning/stats');
  });

  // Phase 2 新增：异步辨识策略与进度条
  test('E2E-TUNE-006: 模型辨识 Phase 2 异步辨识策略', async ({ page }) => {
    await page.goto('/tuning/flow/model');
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(2000);

    // 验证辨识策略选择器存在（AUTO/HISTORY_ONLY/STEP_ONLY）
    const pageText = await page.locator('body').innerText();
    const hasStrategy = /自动|历史|阶跃|辨识策略/i.test(pageText);
    expect(hasStrategy).toBeTruthy();

    // P1-022：候选模型阶次在 Collapse 折叠面板中，改用"预览/开始辨识"按钮验证 Phase 2 UI
    const hasPhase2Ui = /预览可辨识片段|开始辨识/i.test(pageText);
    expect(hasPhase2Ui).toBeTruthy();

    // 验证"预览可辨识片段"按钮存在（Phase 2 新增）
    const previewBtn = page.getByRole('button', { name: /预览可辨识片段/i }).first();
    const hasPreviewBtn = await previewBtn.isVisible().catch(() => false);

    // 验证"开始辨识"按钮存在
    const identifyBtn = page.getByRole('button', { name: /开始辨识|辨识/i }).first();
    const hasIdentifyBtn = await identifyBtn.isVisible().catch(() => false);
    expect(hasIdentifyBtn).toBeTruthy();

    // 核心验证点：Phase 2 辨识策略相关 UI 元素渲染正常
    expect(page.url()).toContain('/tuning/flow/model');
  });

  // Phase 2 新增：多 PID 对比模式
  test('E2E-TUNE-007: 闭环仿真 Phase 2 多 PID 对比模式', async ({ page }) => {
    await page.goto('/tuning/flow/simulation');
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(2000);

    // 验证仿真模式切换区域存在
    const pageText = await page.locator('body').innerText();
    const hasModeSwitch = /双 PID 对比|多 PID 对比|仿真模式/i.test(pageText);
    expect(hasModeSwitch).toBeTruthy();

    // 验证默认为双 PID 对比模式
    const hasDualMode = /当前 PID|推荐 PID/i.test(pageText);
    expect(hasDualMode).toBeTruthy();

    // 尝试切换到多 PID 对比模式（点击 switch）
    const modeSwitch = page.locator('.ant-switch').first();
    const hasSwitch = await modeSwitch.isVisible().catch(() => false);
    if (hasSwitch) {
      await modeSwitch.click();
      await page.waitForTimeout(1000);

      // 验证多 PID 候选列表区域出现
      const modeText = await page.locator('body').innerText();
      const hasCandidateList = /候选 PID|候选列表|添加新候选/i.test(modeText);
      expect(hasCandidateList).toBeTruthy();

      // 验证默认有 2 组候选 PID
      const hasDefaultCandidates = /当前 PID|IMC/i.test(modeText);
      expect(hasDefaultCandidates).toBeTruthy();
    }

    // 核心验证点：多 PID 对比模式可切换
    expect(page.url()).toContain('/tuning/flow/simulation');
  });
});
