/**
 * E2E 整定完整流程测试（2026-08-23 改写；2026-10-04 工作台规整 D3 迁移）
 *
 * 背景：原用例基于 Phase D 单页整合（/tuning/detail + 锚点门禁 + Pinia store），
 * 已随 09 设计方案三页式重写下线。2026-10-04 D3 起单回路四步流程移至
 * 回路工作台整定剖面（TuningSection 整件复用 use-tuning-workbench +
 * identify/matrix/simulate/confirm 四 section），本文件随之迁移。
 *
 * 现行流程入口：
 *   /loop/workbench360?loopId=<id>&section=tuning（?section= 直达协议，D3 新增）
 *
 * 覆盖用例：
 * - E2E-TUNE-FULL-SMOKE：整定剖面直达（section=tuning）后四段流程区渲染 +
 *   发起辨识入口可用（TuningSection 随选中回路渲染）
 * - E2E-TUNE-FULL：辨识→矩阵→仿真→确认 完整闭环（skip，原因见用例内注释）
 */
import { test, expect } from '../fixtures/auth.js';

/** 种子环境固定回路（route-compat.spec.ts /loop/detail 兼容用例同款） */
const SEED_LOOP_ID = '00000000-0000-0000-0000-000000000201';

test.describe('整定完整流程 E2E（回路工作台整定剖面）', () => {
  test.beforeEach(async ({ loginAs }) => {
    await loginAs('ADMIN');
  });

  test('E2E-TUNE-FULL-SMOKE: 整定剖面直达渲染四段流程区', async ({ page }) => {
    await page.goto(
      `/loop/workbench360?loopId=${SEED_LOOP_ID}&section=tuning`,
      { waitUntil: 'domcontentloaded' },
    );
    // 回路工作台渲染 + 整定剖面（section=tuning 一次性消费后展开 half 态）
    await expect(page.locator('body')).toContainText('参数整定', {
      timeout: 30_000,
    });

    // 发起辨识入口可见（流程起点；TuningSection 随选中回路渲染）
    const identifyBtn = page.getByRole('button', { name: /开始辨识/ }).first();
    await expect(identifyBtn).toBeVisible({ timeout: 20_000 });

    // ?section= 消费后从 URL 清除（回写仅保留 loopId）
    await expect(page).toHaveURL(
      new RegExp(`\\/loop\\/workbench360\\?loopId=${SEED_LOOP_ID}`),
      { timeout: 15_000 },
    );
  });

  test('E2E-TUNE-FULL: 辨识→推荐→仿真→确认 完整闭环', async () => {
    test.skip(
      true,
      '完整闭环依赖种子回路 TDengine 历史数据有效窗口 + Celery 异步辨识；' +
        '整定剖面的回路选择/时间窗 UI 编排待专项补齐，' +
        '当前由 backend pytest tuning 用例在 API 层覆盖该闭环。',
    );
  });
});
