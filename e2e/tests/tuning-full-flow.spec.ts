/**
 * E2E 整定完整流程测试（2026-08-23 对齐现行三页式 IA 改写）
 *
 * 背景：原用例基于 Phase D 单页整合（/tuning/detail + .anchor-item 锚点门禁 +
 * Pinia 'tuning' store），该页面与 store 已随 09 设计方案三页式重写整体下线，
 * 原 2 个用例在现行代码下必 404/选择器落空（存量测试债务，HEAD 同样失败）。
 *
 * 现行 IA（views/tuning/workbench.vue）：单页 4 锚点流程
 *   ① 过程辨识 → ② 整定矩阵 → ③ 仿真对比 → ④ 方案确认
 *   （identify/matrix/simulate/confirm-section 四个 Card 区，锚点为纯页内滚动）
 *
 * 本文件改写后覆盖：
 * - E2E-TUNE-FULL-SMOKE：选中回路后四段流程区全部渲染 + 发起辨识入口可用
 *   （流程区仅在已选回路后渲染：workbench.vue v-if="ctx.loopId.value"）
 * - E2E-TUNE-FULL：辨识→矩阵→仿真→确认 完整闭环（skip，原因见用例内注释）
 *   闭环 UI 编排依赖种子回路 TDengine 历史数据有效窗口 + Celery 异步辨识，
 *   且现行工作台的回路选择/时间窗交互与旧 /tuning/detail store 直注方式不同，
 *   暂以 API 层覆盖（backend pytest tuning 用例）替代，待专项补 UI 编排。
 * - 删除 E2E-TUNE-GATE（锚点门禁约束）：现行工作台锚点为纯页内滚动
 *   （workbench.vue scrollIntoView），无门禁约束逻辑，属旧 IA 遗物、无现行对应行为。
 */
import { test, expect } from '../fixtures/auth.js';

test.describe('整定完整流程 E2E（三页式工作台）', () => {
  test.beforeEach(async ({ loginAs }) => {
    await loginAs('ADMIN');
  });

  test('E2E-TUNE-FULL-SMOKE: 辨识→矩阵→仿真→确认 四段流程区渲染', async ({
    page,
  }) => {
    await page.goto('/tuning/workbench', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('body')).toContainText('整定工作台', {
      timeout: 20_000,
    });

    // 四段流程区仅在已选回路后渲染（v-if ctx.loopId），先选中左侧首个回路
    const firstLoop = page.locator('.tuning-loop-item').first();
    await expect(firstLoop).toBeVisible({ timeout: 20_000 });
    await firstLoop.click();
    await page.waitForTimeout(1500);

    // 四段流程区（Card id 锚点目标）全部挂载
    for (const id of [
      'tuning-anchor-identify',
      'tuning-anchor-matrix',
      'tuning-anchor-simulate',
      'tuning-anchor-confirm',
    ]) {
      await expect(page.locator(`#${id}`)).toHaveCount(1);
    }

    // 发起辨识入口可见（流程起点）
    const identifyBtn = page.getByRole('button', { name: /开始辨识/ }).first();
    await expect(identifyBtn).toBeVisible({ timeout: 15_000 });

    // URL 停留在工作台（锚点为页内滚动，不发生路由跳转）
    expect(page.url()).toContain('/tuning/workbench');
  });

  test('E2E-TUNE-FULL: 辨识→推荐→仿真→确认 完整闭环', async () => {
    test.skip(
      true,
      '完整闭环依赖种子回路 TDengine 历史数据有效窗口 + Celery 异步辨识；' +
        '原自动化路径（/tuning/detail + Pinia tuning store 直注）已随 09 方案' +
        '三页式重写下线，现行工作台的回路选择/时间窗 UI 编排待专项补齐，' +
        '当前由 backend pytest tuning 用例在 API 层覆盖该闭环。',
    );
  });
});
