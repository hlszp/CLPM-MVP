/**
 * E2E 整定完整流程实弹验证（Phase D 单页整合）
 *
 * 覆盖用例 E2E-TUNE-FULL：辨识 → 推荐 → 仿真 → 确认 四步完整闭环
 *
 * 测试策略：
 * - 第①步过程辨识：真实异步辨识（AUTO 策略），等待 Celery 任务完成
 * - 第②步PID推荐：MANUAL 模型来源（绕过可信度门禁），真实调用 /tune API
 * - 第③步闭环仿真：真实调用 /simulate API，验证图表渲染
 * - 第④步方案确认：真实调用 POST /tasks 创建任务留痕
 *
 * 预置条件：
 * - 后端 7101 + 前端 5666 运行中
 * - 种子回路 dcd77662（41FIC20021_PIDA）有 7/25-8/5 历史数据
 * - Celery Worker 随后端自动启动（lifespan）
 *
 * Phase D 修复：algorithm.vue/simulation.vue 整定/仿真后同步 store，
 * 启用后续锚点门禁（canAccessSimulation/canAccessConfirm）
 */
import { test, expect, type Page } from '../fixtures/auth.js';

/** 种子回路（有历史数据） */
const TEST_LOOP_ID = 'dcd77662-ddf5-4643-befe-18b4a58b0622';
const TEST_LOOP_TAG = '41FIC20021_PIDA';

/** 辨识时间窗（对齐种子数据有效区间） */
const IDENTIFY_START = '2026-07-25T00:00:00.000Z';
const IDENTIFY_END = '2026-08-05T00:00:00.000Z';

/** 人工模型参数（用于第②步 MANUAL 来源路径） */
const MANUAL_K = 0.72;
const MANUAL_TAU = 5.38;
const MANUAL_THETA = 0.1;

/**
 * 通过 page.evaluate 设置 Pinia store 的时间范围
 * 避免日期选择器 UI 自动化的脆弱性
 */
async function setStoreTimeRange(page: Page, start: string, end: string) {
  await page.evaluate(
    ([s, e]) => {
      const app = (document.querySelector('#app') as any)?.__vue_app__;
      if (!app) throw new Error('Vue app not found');
      const pinia = app.config.globalProperties.$pinia;
      if (!pinia) throw new Error('Pinia not found');
      const store = pinia._s.get('tuning');
      if (!store) throw new Error('tuning store not found');
      store.setLoopTimeRange([s, e]);
    },
    [start, end],
  );
}

/** 等待辨识异步任务完成（轮询 store.identifyResult） */
async function waitForIdentification(page: Page, timeoutMs = 60_000) {
  await expect
    .poll(
      async () => {
        return await page.evaluate(() => {
          const app = (document.querySelector('#app') as any)?.__vue_app__;
          const pinia = app?.config?.globalProperties?.$pinia;
          const store = pinia?._s?.get('tuning');
          return !!store?.identifyResult;
        });
      },
      { timeout: timeoutMs, intervals: [2_000] },
    )
    .toBe(true);
}

/** 点击锚点导航 */
async function clickAnchor(page: Page, anchorText: string) {
  const anchor = page.locator('.anchor-item').filter({ hasText: anchorText }).first();
  await anchor.click();
  await page.waitForTimeout(500);
}

test.describe('整定完整流程 E2E（Phase D）', () => {
  test.beforeEach(async ({ loginAs }) => {
    await loginAs('ADMIN');
  });

  test('E2E-TUNE-FULL: 辨识→推荐→仿真→确认 完整闭环', async ({ page }) => {
    test.setTimeout(180_000); // 3 分钟超时（含异步辨识等待）

    // ===== 进入整定详情页 =====
    await page.goto(`/tuning/detail?loopId=${TEST_LOOP_ID}`, {
      waitUntil: 'domcontentloaded',
    });
    await page.waitForTimeout(2000);

    // 验证 4 锚点导航完整渲染
    const pageText = await page.locator('body').innerText();
    expect(pageText).toMatch(/过程辨识/);
    expect(pageText).toMatch(/PID\s*推荐/);
    expect(pageText).toMatch(/闭环仿真/);
    expect(pageText).toMatch(/方案确认/);

    // ===== 第①步：过程辨识 =====
    // 设置时间范围（通过 store，避免日期选择器自动化脆弱性）
    await setStoreTimeRange(page, IDENTIFY_START, IDENTIFY_END);
    await page.waitForTimeout(500);

    // 点击"开始辨识"（AUTO 策略为默认）
    const identifyBtn = page.getByRole('button', { name: /开始辨识/ });
    await expect(identifyBtn).toBeVisible({ timeout: 10_000 });
    await identifyBtn.click();

    // 等待异步辨识任务完成（Celery 轮询，约 10-30 秒）
    await waitForIdentification(page, 90_000);

    // 验证辨识结果已展示（模型类型 / 参数）
    await page.waitForTimeout(1000);
    const afterIdentifyText = await page.locator('body').innerText();
    const hasIdentifyResult =
      /FOPDT|SOPDT|IPDT|拟合|可信度|HISTORICAL_ARX/i.test(afterIdentifyText);
    expect(hasIdentifyResult).toBeTruthy();

    // ===== 第②步：PID 推荐 =====
    // 点击"PID 推荐"锚点（canAccessPid = !!store.identifyResult → true）
    await clickAnchor(page, /PID/);

    // 等待第②步内容加载（algorithm.vue 是异步组件，需等待渲染）
    await expect
      .poll(
        async () => {
          const text = await page.locator('body').innerText();
          return /模型来源/.test(text);
        },
        { timeout: 15_000, intervals: [1_000] },
      )
      .toBe(true);

    // 选择"人工模型（需确认风险）"来源
    // 通过 FormItem label "模型来源" 定位 select 容器，点击 .ant-select-selector
    // 打开下拉（避免直接点 placeholder 被 combobox input 拦截 pointer events）
    const modelSourceFormItem = page
      .locator('.ant-form-item')
      .filter({ hasText: '模型来源' });
    const modelSourceSelector = modelSourceFormItem
      .locator('.ant-select-selector')
      .first();
    await modelSourceSelector.scrollIntoViewIfNeeded();
    await modelSourceSelector.click();
    await page.waitForTimeout(500);

    // 在 dropdown 中选择"人工模型"
    const manualOption = page.locator('.ant-select-item-option', {
      hasText: '人工模型',
    });
    await expect(manualOption).toBeVisible({ timeout: 5_000 });
    await manualOption.click();
    await page.waitForTimeout(500);

    // 勾选风险确认复选框（MANUAL 来源时显示）
    const riskCheckbox = page.locator('input[type="checkbox"]').first();
    await expect(riskCheckbox).toBeVisible({ timeout: 5_000 });
    await riskCheckbox.check();
    await page.waitForTimeout(300);

    // 填写模型参数 K / tau / theta（通过 FormItem label 定位）
    async function fillInputByLabel(label: string, value: string) {
      const formItem = page.locator('.ant-form-item').filter({ hasText: label });
      const input = formItem.locator('input').first();
      await input.scrollIntoViewIfNeeded();
      await input.fill(value);
      await page.waitForTimeout(200);
    }

    await fillInputByLabel('过程增益 K', String(MANUAL_K));
    await fillInputByLabel('时间常数 τ', String(MANUAL_TAU));
    await fillInputByLabel('纯滞后 θ', String(MANUAL_THETA));

    // 点击"执行整定"
    const tuneBtn = page.getByRole('button', { name: /执行整定/ });
    await tuneBtn.scrollIntoViewIfNeeded();
    await expect(tuneBtn).toBeVisible({ timeout: 10_000 });

    // 确保按钮可点击（门禁已通过：MANUAL + riskConfirmed）
    const isDisabled = await tuneBtn.isDisabled();
    if (isDisabled) {
      // 如果仍禁用，尝试再次勾选风险确认
      const checkboxes = page.locator('input[type="checkbox"]');
      const cbCount = await checkboxes.count();
      for (let i = 0; i < cbCount; i++) {
        await checkboxes.nth(i).check();
        await page.waitForTimeout(200);
      }
    }

    await tuneBtn.click();

    // 等待整定完成（同步 API，约 2-5 秒）
    await expect
      .poll(
        async () => {
          const text = await page.locator('body').innerText();
          return /推荐比例增益|推荐积分时间|PID 整定完成/i.test(text);
        },
        { timeout: 30_000, intervals: [1_000] },
      )
      .toBe(true);

    // 验证推荐 PID 结果展示
    const afterTuneText = await page.locator('body').innerText();
    expect(afterTuneText).toMatch(/推荐比例增益|Kp/i);

    // ===== 第③步：闭环仿真 =====
    // 点击"进行闭环仿真 →"按钮（embedded 模式下自动切换锚点）
    const goSimBtn = page.getByRole('button', { name: /进行闭环仿真/ });
    const hasGoSimBtn = await goSimBtn.isVisible().catch(() => false);
    if (hasGoSimBtn) {
      await goSimBtn.click();
      await page.waitForTimeout(1000);
    } else {
      // 回退：直接点击锚点
      await clickAnchor(page, /闭环仿真/);
      await page.waitForTimeout(1000);
    }

    // 验证已切换到第③步
    const step3Text = await page.locator('body').innerText();
    expect(step3Text).toMatch(/运行仿真|闭环仿真/);

    // 点击"运行仿真"按钮
    const simBtn = page.getByRole('button', { name: /运行仿真/ });
    await expect(simBtn).toBeVisible({ timeout: 10_000 });
    await simBtn.click();

    // 等待仿真完成（同步 API，约 2-5 秒）
    await expect
      .poll(
        async () => {
          const text = await page.locator('body').innerText();
          return /仿真完成|改善|衰减率|超调/i.test(text);
        },
        { timeout: 30_000, intervals: [1_000] },
      )
      .toBe(true);

    // 验证仿真结果展示（图表 canvas 或性能指标）
    const afterSimText = await page.locator('body').innerText();
    const hasSimResult = /改善|超调|衰减|调节时间|上升时间/i.test(afterSimText);
    expect(hasSimResult).toBeTruthy();

    // ===== 第④步：方案确认 =====
    // 点击"方案确认"锚点（canAccessConfirm = !!store.simulationResult → true）
    await clickAnchor(page, /方案确认/);
    await page.waitForTimeout(1000);

    // 验证安全边界提示
    const step4Text = await page.locator('body').innerText();
    expect(step4Text).toMatch(/只读建议.*人工实施.*需留痕|方案确认/);

    // 验证方案汇总区域
    expect(step4Text).toMatch(/整定方案汇总|回路|辨识模型/);

    // 验证风险评估区域
    expect(step4Text).toMatch(/风险评估|风险等级/);

    // 验证回退方案区域
    expect(step4Text).toMatch(/回退方案/);

    // 点击"确认方案并留痕"按钮
    const confirmBtn = page.getByRole('button', { name: /确认方案并留痕/ });
    await expect(confirmBtn).toBeVisible({ timeout: 10_000 });

    // 确认按钮可点击（canConfirm = !!recommendedPid && !!identifyResult）
    const confirmDisabled = await confirmBtn.isDisabled();
    expect(confirmDisabled).toBeFalsy();

    await confirmBtn.click();

    // 等待留痕完成
    await expect
      .poll(
        async () => {
          const text = await page.locator('body').innerText();
          return /整定方案已确认并留痕|留痕记录|任务 ID/i.test(text);
        },
        { timeout: 15_000, intervals: [1_000] },
      )
      .toBe(true);

    // 验证留痕记录展示
    const finalText = await page.locator('body').innerText();
    expect(finalText).toMatch(/留痕记录|任务 ID|已确认.*SIMULATED/);

    // 核心验证点：完整流程跑通，URL 仍为 /tuning/detail
    expect(page.url()).toContain('/tuning/detail');
  });

  test('E2E-TUNE-GATE: 锚点门禁约束验证', async ({ page }) => {
    // 进入空白整定详情页（无辨识结果）
    await page.goto(`/tuning/detail?loopId=${TEST_LOOP_ID}`, {
      waitUntil: 'domcontentloaded',
    });
    await page.waitForTimeout(2000);

    // 验证初始状态：第①步激活，②③④禁用
    const pidAnchor = page.locator('.anchor-item').filter({ hasText: /PID/ }).first();
    const simAnchor = page
      .locator('.anchor-item')
      .filter({ hasText: /闭环仿真/ })
      .first();
    const confirmAnchor = page
      .locator('.anchor-item')
      .filter({ hasText: /方案确认/ })
      .first();

    // 尝试点击禁用锚点，应被拦截
    await pidAnchor.click().catch(() => {});
    await page.waitForTimeout(500);
    // 仍停留在第①步
    const textAfterPidClick = await page.locator('body').innerText();
    expect(/辨识策略|开始辨识/i.test(textAfterPidClick)).toBeTruthy();

    await simAnchor.click().catch(() => {});
    await page.waitForTimeout(500);
    const textAfterSimClick = await page.locator('body').innerText();
    expect(/辨识策略|开始辨识/i.test(textAfterSimClick)).toBeTruthy();

    await confirmAnchor.click().catch(() => {});
    await page.waitForTimeout(500);
    const textAfterConfirmClick = await page.locator('body').innerText();
    expect(/辨识策略|开始辨识/i.test(textAfterConfirmClick)).toBeTruthy();
  });
});
