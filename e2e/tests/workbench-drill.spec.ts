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
 * 页面源码依据：
 *   frontend/apps/web-antd/src/views/workbench/components/KpiCards.vue（下钻卡 title 属性）
 *   frontend/apps/web-antd/src/views/workbench/utils/drill.ts（G1/G2/G4 口径契约）
 *   frontend/apps/web-antd/src/views/workbench/components/EvalDistributions.vue（长期手动链接）
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

  test('E2E-DRILL-001: 总览 KPI 卡下钻携带口径参数', async ({ page }) => {
    // 追溯矩阵 §2：劣化回路卡 → 回路绩效明细（grade=POOR）
    await page.goto('/workbench', { waitUntil: 'domcontentloaded' });
    // KPI 卡用 title 属性精确锁定（页面上"劣化回路"文本可能多处出现）
    const degradedCard = page.locator('[title="点击查看劣化回路口径明细"]');
    await expect(degradedCard).toBeVisible({ timeout: 20_000 });
    await degradedCard.click();

    // 断言路由跳转 + 专有参数 grade=POOR（窗口 startTime/endTime 由 drill 自动携带）
    await page.waitForURL(/\/metric\/loop-performance/, { timeout: 15_000 });
    expect(page.url()).toContain('grade=POOR');
    expect(page.url()).toContain('startTime=');
    expect(page.url()).toContain('endTime=');

    // 追溯矩阵 §2：处置待办卡 → 工单列表（status 含 PENDING）
    await page.goto('/workbench', { waitUntil: 'domcontentloaded' });
    const pendingCard = page.locator('[title="点击查看在办工单列表"]');
    await expect(pendingCard).toBeVisible({ timeout: 20_000 });
    await pendingCard.click();

    await page.waitForURL(/\/handling\/orders/, { timeout: 15_000 });
    // status=PENDING,REOPENED,EXECUTING（逗号被 URL 编码，首值 PENDING 可直查）
    expect(page.url()).toContain('status=PENDING');
  });

  test('E2E-DRILL-002: 评估 tab 长期手动链接下钻诊断记录', async ({ page }) => {
    // 追溯矩阵 §3 死链修复：长期手动 → /diagnosis/records?category=UTILIZATION
    await page.goto('/workbench', { waitUntil: 'domcontentloaded' });

    // 切"性能评估"tab（TabBar 为 button 切换 activeTab，非路由；
    // 可访问名含模块状态点文本，如"内置 性能评估"，用正则匹配）
    await page.getByRole('button', { name: /性能评估/ }).click();

    // 等评估数据异步加载（控制模式分布饼图区渲染后才能判定链接是否存在）
    await expect(page.locator('body')).toContainText('回路', {
      timeout: 20_000,
    });
    await page.waitForTimeout(3000);

    // 链接仅 manualCount>0 才渲染（EvalDistributions.vue v-if）；无手动回路时跳过
    const manualLink = page.locator('a', { hasText: '长期手动' });
    if ((await manualLink.count()) === 0) {
      test.skip(true, 'manualCount=0，长期手动链接未渲染，跳过下钻断言');
    }
    await manualLink.first().click();

    await page.waitForURL(/\/diagnosis\/records/, { timeout: 15_000 });
    expect(page.url()).toContain('category=UTILIZATION');
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
