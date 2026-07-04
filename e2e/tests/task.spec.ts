/**
 * E2E 评估任务测试
 *
 * 覆盖用例：
 * - E2E-TASK-001: 非标任务结果查询（/metric/tasks → 任务列表）
 *
 * 页面源码依据：
 *   frontend/apps/web-antd/src/views/task/list.vue
 *   - 顶部 KPI Strip：任务总数 / 成功数 / 失败数 / 运行中
 *   - 状态机可视化：PENDING → RUNNING → SUCCESS/FAILED/CANCELLED
 *   - Tab 双轨：标准评估任务 / 自定义评估任务
 *   - 筛选栏：状态筛选 + 查询按钮
 *   - 表格：任务ID / 类型 / 状态 / 进度 / 当前阶段 / 回路进度 / 创建时间 / 操作
 *   - 行点击展开右侧详情抽屉
 *   - 自动轮询：有活跃任务时每 10s 刷新
 *
 * 路由（metric.ts）：
 *   - /metric/tasks → 执行记录页（ADMIN/IC_ENGINEER/PE_ENGINEER 可见）
 */
import { test, expect } from '../fixtures/auth.js';

test.describe('评估任务 E2E', () => {
  test.beforeEach(async ({ page, loginAs }) => {
    // 执行记录页需要 ADMIN 或 IC_ENGINEER / PE_ENGINEER 权限
    await loginAs('ADMIN');
  });

  // E2E-TASK-001: 非标任务结果查询
  // 路由 /metric/tasks：任务列表表格 + 表头关键字段
  test('E2E-TASK-001: 非标任务结果查询', async ({ page }) => {
    await page.goto('/metric/tasks');
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(2000);

    // 验证页面加载（任务列表表格可见）
    // task/list.vue: ClpmDataCanvas 内 Table + 筛选栏
    await expect(page.locator('.ant-table').first()).toBeVisible({ timeout: 15_000 });

    // 验证表头包含关键字段（任务ID/状态/类型/时间等）
    const headerText = await page.locator('.ant-table-thead').first().innerText().catch(() => '');
    if (headerText) {
      // task/list.vue columns: 任务ID / 类型 / 状态 / 进度 / 当前阶段 / 回路进度 / 创建时间 / 操作
      expect(headerText).toContain('任务ID');
      expect(headerText).toContain('状态');
      expect(headerText).toContain('类型');
      // 验证时间字段（创建时间）
      expect(headerText).toContain('时间');
    }

    // 验证 Tab 双轨存在（标准评估任务 / 自定义评估任务）
    const pageText = await page.locator('body').innerText();
    expect(pageText).toContain('标准评估任务');
    expect(pageText).toContain('自定义评估任务');

    // 注意：数据可能为空，验证表格容器存在即可
    expect(page.url()).toContain('/metric/tasks');
  });
});
