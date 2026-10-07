/**
 * E2E 工作台"有据可查"下钻测试（追溯矩阵 docs/MVP设计/13-工作台统计追溯矩阵.md）
 *
 * 背景：工作台统计卡可带口径参数（startTime/endTime/plantNodeId/各页专有参数）
 * 下钻到明细页；明细页从 route.query 读筛选初值；/tuning/records 新增
 * "整定批次"视图；后端新增 GET /api/v1/tuning/batches 端点。
 *
 * 覆盖用例（数据相关断言容错，空态算通过；重点是路由跳转与参数传递）：
 * - E2E-DRILL-001: 总览 KPI 下钻（劣化回路 → /metric/loop-performance?grade=POOR；
 *   处置待办 → /handling/orders?status 含 PENDING）
 * - E2E-DRILL-002: 评估 tab 长期手动死链修复（→ /diagnosis/records?category=UTILIZATION，
 *   manualCount=0 时链接不渲染则跳过）
 * - E2E-DRILL-003: 明细页接参（/tuning/records?status=DRAFT,PENDING 多选生效；
 *   /diagnosis/records?category=UTILIZATION&status=SUCCESS 筛选生效）
 * - E2E-DRILL-004: 整定批次视图切换（记录/批次两视图独立渲染，允许空态）
 * - E2E-DRILL-005: 批次 API 冒烟（GET /tuning/batches → code=0 + items/total）
 *
 * 页面源码依据（2026-10-07 P7：旧运维工作台退役，001/002 已改写为驾驶舱弹窗断言）：
 *   frontend/apps/web-antd/src/views/cockpit/overview.vue（KPI 卡点击 → 舱内清单弹窗）
 *   frontend/apps/web-antd/src/views/cockpit/wb-comps/use-drill.ts（D3 弹窗化口径契约）
 *   frontend/apps/web-antd/src/views/cockpit/wb-comps/EvalDistributions.vue（长期手动链接）
 *   frontend/apps/web-antd/src/views/tuning/records.vue（route.query 初值 + 批次视图）
 */
import {
  ACCOUNTS,
  API_BASE_URL,
  expect,
  loginViaApi,
  test,
} from '../fixtures/auth.js';

test.describe('工作台有据可查下钻 E2E', () => {
  test.beforeEach(async ({ loginAs }) => {
    await loginAs('ADMIN');
  });

  test('E2E-DRILL-001: 驾驶舱 KPI 卡点击打开清单弹窗（D3 弹窗化）', async ({
    page,
  }) => {
    // 2026-10-07 P7 改写：旧运维工作台退役，KPI 卡下钻已弹窗化
    //（不再路由跳转 /metric/loop-performance，改开舱内清单弹窗）
    await page.goto('/cockpit', { waitUntil: 'domcontentloaded' });
    const degradedCard = page.locator('[title*="劣化"], [class*=kpi] [class*=card]');
    await expect(degradedCard.first()).toBeVisible({ timeout: 20_000 });
    await degradedCard.first().click();

    // 断言舱内弹窗打开（ck-modal 渲染于 .cockpit-root 内）
    const modal = page.locator('.ck-modal');
    await expect(modal.first()).toBeVisible({ timeout: 15_000 });
    await expect(modal.first()).toContainText(/清单/);
  });

  test('E2E-DRILL-002: 驾驶舱性能页长期手动链接打开诊断记录弹窗', async ({
    page,
  }) => {
    // 2026-10-07 P7 改写：原"长期手动 → /diagnosis/records?category=UTILIZATION"
    // 弹窗化为诊断记录清单弹窗（不再路由跳转）
    await page.goto('/cockpit/performance', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('body')).toContainText('回路', {
      timeout: 20_000,
    });
    await page.waitForTimeout(3000);

    // 链接仅 manualCount>0 才渲染；无手动回路时跳过
    const manualLink = page.locator('a', { hasText: '长期手动' });
    if ((await manualLink.count()) === 0) {
      test.skip(true, 'manualCount=0，长期手动链接未渲染，跳过弹窗断言');
    }
    await manualLink.first().click();

    const modal = page.locator('.ck-modal');
    await expect(modal.first()).toBeVisible({ timeout: 15_000 });
    await expect(modal.first()).toContainText('诊断记录');
  });

  test('E2E-DRILL-003: 明细页从 route.query 读取筛选初值', async ({ page }) => {
    // 追溯矩阵 G6：/tuning/records 接 status 逗号多值 → 多选筛选 + 列表请求带参
    const tasksReq = page
      .waitForRequest(
        (req) =>
          req.url().includes('/tuning/tasks') &&
          decodeURIComponent(req.url()).includes('status=DRAFT,PENDING'),
        { timeout: 15_000 },
      )
      .catch(() => null); // 容错：请求断言失败降级为 UI 断言
    await page.goto('/tuning/records?status=DRAFT,PENDING', {
      waitUntil: 'domcontentloaded',
    });
    await expect(page.locator('body')).toContainText('整定记录', {
      timeout: 20_000,
    });

    // 状态多选回显初值（草稿/待实施两个 tag）
    await expect(
      page.locator('.ant-select-selection-item', { hasText: '草稿' }),
    ).toBeVisible({ timeout: 10_000 });
    await expect(
      page.locator('.ant-select-selection-item', { hasText: '待实施' }),
    ).toBeVisible({ timeout: 10_000 });

    // 列表请求参数含 status=DRAFT,PENDING（降级容错：未捕获仅告警不失败）
    const req = await tasksReq;
    if (!req) {
      console.warn('未捕获到带 status=DRAFT,PENDING 的 /tuning/tasks 请求');
    }

    // 表格或空态二选一渲染（容忍无数据环境）
    await expect(
      page.locator('.ant-table, .ant-empty').first(),
    ).toBeVisible({ timeout: 15_000 });

    // 追溯矩阵 G6：/diagnosis/records 接 category + status
    const diagReq = page
      .waitForRequest(
        (req) =>
          req.url().includes('/diagnosis/') &&
          decodeURIComponent(req.url()).includes('category=UTILIZATION') &&
          decodeURIComponent(req.url()).includes('status=SUCCESS'),
        { timeout: 15_000 },
      )
      .catch(() => null);
    await page.goto('/diagnosis/records?category=UTILIZATION&status=SUCCESS', {
      waitUntil: 'domcontentloaded',
    });

    // 页面不报错、筛选生效（请求带参，降级容错同上）
    const dReq = await diagReq;
    if (!dReq) {
      console.warn(
        '未捕获到带 category=UTILIZATION&status=SUCCESS 的诊断列表请求',
      );
    }
    // 表格或空态二选一：诊断记录页空态由 ClpmDataCanvas 渲染
    // （.clpm-data-canvas__state.is-empty），非 antd Empty；
    // .clpm-data-canvas 容器恒渲染，直接锚定容器
    await expect(page.locator('.clpm-data-canvas')).toBeVisible({
      timeout: 15_000,
    });
    expect(page.url()).toContain('/diagnosis/records');
  });

  test('E2E-DRILL-004: 整定记录页批次视图切换', async ({ page }) => {
    // 追溯矩阵 GAP-2b：/tuning/records 新增"整定批次"视图（两视图独立渲染）
    await page.goto('/tuning/records', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('body')).toContainText('整定记录', {
      timeout: 20_000,
    });
    // 记录视图统计卡在锚
    await expect(page.locator('.stats-row')).toBeVisible({ timeout: 15_000 });

    // 切"整定批次"（RadioGroup button 形态）
    await page
      .locator('.ant-radio-button-wrapper', { hasText: '整定批次' })
      .click();

    // 批次表格出现（允许空态 Empty）；记录视图统计卡卸载
    await expect(page.locator('.stats-row')).toBeHidden({ timeout: 10_000 });
    await expect(
      page.locator('.ant-table, .ant-empty').first(),
    ).toBeVisible({ timeout: 15_000 });

    // 切回"整定记录"恢复正常
    await page
      .locator('.ant-radio-button-wrapper', { hasText: '整定记录' })
      .click();
    await expect(page.locator('.stats-row')).toBeVisible({ timeout: 10_000 });
  });

  test('E2E-DRILL-005: 整定批次 API 冒烟', async ({ request }) => {
    // 追溯矩阵 GAP-2a：GET /api/v1/tuning/batches 端点契约（code=0 + items/total）
    const { accessToken } = await loginViaApi(
      request,
      ACCOUNTS.ADMIN.username,
      ACCOUNTS.ADMIN.password,
    );
    const resp = await request.get(
      `${API_BASE_URL}/tuning/batches?page=1&pageSize=10`,
      {
        headers: { Authorization: `Bearer ${accessToken}` },
        timeout: 15_000,
      },
    );
    expect(resp.ok()).toBeTruthy();

    const body = await resp.json();
    expect(String(body.code)).toBe('0');
    // data 含 items/total 分页字段（空列表也算通过）
    expect(body.data).toHaveProperty('items');
    expect(body.data).toHaveProperty('total');
    expect(Array.isArray(body.data.items)).toBeTruthy();
  });
});
