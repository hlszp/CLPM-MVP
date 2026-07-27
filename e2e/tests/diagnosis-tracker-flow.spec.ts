/**
 * E2E D1 全流程：诊断触发 → 自动建单 → Tracker 列表可见
 *
 * 验证整改计划 D1 的端到端闭环：
 * 1. API 触发单回路诊断（POST /diagnosis/trigger）
 * 2. 轮询诊断任务直到 SUCCESS（GET /diagnosis/tasks/{taskId}）
 * 3. 诊断产出标签时 _auto_create_trackers 自动建单（trigger_type='auto'）
 * 4. API 验证 PENDING tracker 存在且 triggerType='auto'
 * 5. UI 验证 /diagnosis/tracker 列表可见
 * 6. UI 验证 /dashboard/workbench 诊断聚合卡渲染（门户卡 + 自动建单徽标）
 *
 * 前置条件：
 * - 后端 API（http://localhost:7101）已启动
 * - 前端（http://localhost:5666）已启动
 * - 至少一个回路有历史诊断结果（用于挑选可产出标签的回路）
 *
 * 页面源码依据：
 *   frontend/apps/web-antd/src/views/metric/pid-dashboard.vue（聚合卡嵌入）
 *   frontend/apps/web-antd/src/views/diagnosis/components/diagnosis-summary-card.vue
 *   frontend/apps/web-antd/src/views/diagnosis/tracker.vue
 */
import { expect, test } from '../fixtures/auth.js';
import {
  ACCOUNTS,
  API_BASE_URL,
  loginViaApi,
  type LoginResult,
} from '../fixtures/auth.js';

/** 诊断任务终态 */
const TERMINAL_STATUSES = ['SUCCESS', 'FAILED', 'CANCELLED'];

/** 轮询诊断任务状态直到终态。
 * 1 小时窗口诊断通常 2~5 秒内完成，轮询间隔 1.5s，最多 40 次（60 秒兜底）。 */
async function pollTaskStatus(
  request: import('@playwright/test').APIRequestContext,
  taskId: string,
  authHeaders: Record<string, string>,
  maxAttempts = 40,
): Promise<{ status: string; errorMessage?: string }> {
  for (let i = 0; i < maxAttempts; i++) {
    await new Promise((resolve) => setTimeout(resolve, 1500));
    const resp = await request.get(`${API_BASE_URL}/diagnosis/tasks/${taskId}`, {
      headers: authHeaders,
    });
    if (!resp.ok()) continue;
    const body = await resp.json();
    const status = body?.data?.status;
    if (status && TERMINAL_STATUSES.includes(status)) {
      return { status, errorMessage: body?.data?.errorMessage };
    }
  }
  return { status: 'TIMEOUT' };
}

/** 清理指定回路的所有开放态 tracker（PENDING/IN_PROGRESS）。
 * 每次 PATCH 只更新最新的一条开放态 tracker，需循环直到无开放态。
 * 返回清理的条数。 */
async function clearOpenTrackers(
  request: import('@playwright/test').APIRequestContext,
  loopId: string,
  authHeaders: Record<string, string>,
  maxRounds = 12,
): Promise<number> {
  let cleared = 0;
  for (let i = 0; i < maxRounds; i++) {
    const checkResp = await request.get(
      `${API_BASE_URL}/diagnosis/list?loopId=${loopId}&actionStatus=PENDING&page=1&pageSize=50`,
      { headers: authHeaders },
    );
    const checkBody = await checkResp.json();
    const openItems: any[] = checkBody?.data?.items ?? [];
    if (openItems.length === 0) break;
    const patchResp = await request.patch(
      `${API_BASE_URL}/tracker/${loopId}/status`,
      {
        headers: authHeaders,
        data: { status: 'IGNORED', comment: 'E2E 清理开放态 tracker' },
      },
    );
    if (patchResp.ok()) cleared += 1;
  }
  return cleared;
}

test.describe('D1 诊断→自动建单→列表可见 全流程', () => {
  test('E2E-DIAG-D1: 触发诊断后自动建单并在 Tracker 列表与门户卡可见', async ({
    page,
    request,
    loginAs,
  }) => {
    test.setTimeout(120_000);

    // 1. API 登录拿 token（IC_ENGINEER 有诊断触发 + tracker 编辑权限）
    const login: LoginResult = await loginViaApi(
      request,
      ACCOUNTS.IC_ENGINEER.username,
      ACCOUNTS.IC_ENGINEER.password,
    );
    const authHeaders = {
      Authorization: `Bearer ${login.accessToken}`,
      'Content-Type': 'application/json',
    };

    // 2. 挑选一个有非 MANUAL_REVIEW 诊断标签的回路（已验证可产出标签）
    const listResp = await request.get(
      `${API_BASE_URL}/diagnosis/list?page=1&pageSize=50&timeWindow=last_7_days`,
      { headers: authHeaders },
    );
    expect(listResp.ok()).toBeTruthy();
    const listBody = await listResp.json();
    const items: any[] = listBody?.data?.items ?? [];
    const candidate = items.find(
      (it) => it.diagnosisLabel && it.diagnosisLabel !== 'MANUAL_REVIEW',
    );
    expect(candidate, '应存在可产出诊断标签的回路').toBeTruthy();
    const loopId: string = candidate.loopId;
    const candidateTagName: string = candidate.tagName;
    console.log(
      `[E2E-D1] 选定回路: ${candidateTagName} (${loopId}) 标签=${candidate.diagnosisLabel}`,
    );

    // 3. 清理该回路所有开放态 tracker（PATCH 到 IGNORED 直到无开放态）
    //    确保后续诊断产出的标签触发"新建"而非"跳过已存在开放态"
    const cleared = await clearOpenTrackers(request, loopId, authHeaders);
    console.log(`[E2E-D1] 清理开放态 tracker: ${cleared} 条`);

    // 4. 触发诊断（1 小时窗口，降低数据量加速诊断；1Hz 数据约 3600 点）
    const endTime = new Date().toISOString();
    const startTime = new Date(Date.now() - 1 * 3600 * 1000).toISOString();
    const triggerResp = await request.post(`${API_BASE_URL}/diagnosis/trigger`, {
      headers: authHeaders,
      data: { loopIds: [loopId], startTime, endTime },
    });
    expect(triggerResp.ok()).toBeTruthy();
    const triggerBody = await triggerResp.json();
    expect(
      triggerBody.code === 0 || triggerBody.code === '0',
      `触发诊断失败: ${triggerBody.message ?? ''}`,
    ).toBeTruthy();
    const taskId: string = triggerBody.data.tasks[0].taskId;
    expect(taskId).toBeTruthy();
    console.log(`[E2E-D1] 诊断任务已触发: ${taskId}`);

    // 5. 轮询任务直到终态（1 小时窗口通常 2~5 秒完成）
    const { status: taskStatus, errorMessage } = await pollTaskStatus(
      request,
      taskId,
      authHeaders,
    );
    expect(
      taskStatus,
      `诊断任务未达终态或失败: status=${taskStatus} error=${errorMessage ?? ''}`,
    ).toBe('SUCCESS');
    console.log(`[E2E-D1] 诊断任务完成: ${taskStatus}`);

    // 6. API 验证自动建单：该回路应有 PENDING tracker 且 triggerType='auto'
    const afterResp = await request.get(
      `${API_BASE_URL}/diagnosis/list?loopId=${loopId}&actionStatus=PENDING&page=1&pageSize=50`,
      { headers: authHeaders },
    );
    const afterBody = await afterResp.json();
    const pendingItems: any[] = afterBody?.data?.items ?? [];
    const autoItem = pendingItems.find((it) => it.triggerType === 'auto');
    expect(
      autoItem,
      '诊断 SUCCESS 后应存在 triggerType=auto 的自动建单 PENDING tracker',
    ).toBeTruthy();
    console.log(
      `[E2E-D1] 自动建单验证通过: label=${autoItem.diagnosisLabel} severity=${autoItem.severity ?? 'N/A'}`,
    );

    // 7. UI 验证：Tracker 列表页可见
    await loginAs('IC_ENGINEER');
    await page.goto('/diagnosis/tracker');
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(2000);

    // 表格渲染
    await expect(page.locator('.ant-table').first()).toBeVisible({
      timeout: 10_000,
    });
    // 至少有一行数据（开放态 tracker 存在）
    const tableRows = page.locator('.ant-table-tbody tr.ant-table-row');
    await expect(tableRows.first()).toBeVisible({ timeout: 10_000 });

    // 8. UI 验证：工作台诊断聚合卡渲染 + 自动建单徽标
    await page.goto('/dashboard/workbench');
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(1500);

    const summaryCard = page.getByTestId('diagnosis-summary-card');
    await expect(summaryCard).toBeVisible({ timeout: 10_000 });

    // 卡片标题可见
    await expect(summaryCard.getByText('诊断与异常跟踪')).toBeVisible();

    // 异常标签分布横条渲染（至少有横条容器）
    await expect(summaryCard.getByTestId('diag-label-bars')).toBeVisible({
      timeout: 10_000,
    });

    // 最近建单列表按 tracker.created_at 降序，新建的自动建单应在前 5 条中展示
    // 硬断言：自动建单徽标应可见（sortBy=created_at 确保新建 tracker 在顶部）
    const autoBadge = summaryCard.getByTestId('diag-auto-badge');
    await expect(autoBadge.first()).toBeVisible({ timeout: 10_000 });
    console.log(`[E2E-D1] 门户卡自动建单徽标可见`);
  });
});

test.describe('D3 MOC 变更管理闭环', () => {
  test('E2E-DIAG-D3: 标记已实施时 MOC 必填校验 + 字段写入 + UI 展示', async ({
    page,
    request,
    loginAs,
  }) => {
    test.setTimeout(120_000);

    // 1. API 登录（IC_ENGINEER 有 tracker 编辑权限）
    const login: LoginResult = await loginViaApi(
      request,
      ACCOUNTS.IC_ENGINEER.username,
      ACCOUNTS.IC_ENGINEER.password,
    );
    const authHeaders = {
      Authorization: `Bearer ${login.accessToken}`,
      'Content-Type': 'application/json',
    };

    // 2. 查找一个有 PENDING tracker 的回路（若无则触发诊断新建）
    let loopId: string = '';
    let loopTagName: string = '';
    const listResp = await request.get(
      `${API_BASE_URL}/diagnosis/list?actionStatus=PENDING&page=1&pageSize=50&timeWindow=last_7_days`,
      { headers: authHeaders },
    );
    expect(listResp.ok()).toBeTruthy();
    const listBody = await listResp.json();
    const pendingItems: any[] = listBody?.data?.items ?? [];

    if (pendingItems.length > 0) {
      loopId = pendingItems[0].loopId;
      loopTagName = pendingItems[0].tagName;
      console.log(`[E2E-D3] 复用已有 PENDING tracker 回路: ${loopTagName} (${loopId})`);
    } else {
      // 无 PENDING tracker：挑一个有诊断标签的回路，清理后触发诊断
      const allResp = await request.get(
        `${API_BASE_URL}/diagnosis/list?page=1&pageSize=50&timeWindow=last_7_days`,
        { headers: authHeaders },
      );
      const allBody = await allResp.json();
      const allItems: any[] = allBody?.data?.items ?? [];
      const candidate = allItems.find(
        (it) => it.diagnosisLabel && it.diagnosisLabel !== 'MANUAL_REVIEW',
      );
      expect(candidate, '应存在可产出诊断标签的回路').toBeTruthy();
      loopId = candidate.loopId;
      loopTagName = candidate.tagName;

      await clearOpenTrackers(request, loopId, authHeaders);
      const endTime = new Date().toISOString();
      const startTime = new Date(Date.now() - 1 * 3600 * 1000).toISOString();
      const triggerResp = await request.post(`${API_BASE_URL}/diagnosis/trigger`, {
        headers: authHeaders,
        data: { loopIds: [loopId], startTime, endTime },
      });
      expect(triggerResp.ok()).toBeTruthy();
      const triggerBody = await triggerResp.json();
      const taskId: string = triggerBody.data.tasks[0].taskId;
      const { status } = await pollTaskStatus(request, taskId, authHeaders);
      expect(status, `诊断任务未成功: ${status}`).toBe('SUCCESS');
      console.log(`[E2E-D3] 触发诊断新建 PENDING tracker: ${loopTagName}`);
    }

    // 3. 测试 MOC 必填校验：IMPLEMENTED 不带 MOC → 422
    const noMocResp = await request.patch(
      `${API_BASE_URL}/tracker/${loopId}/status`,
      {
        headers: authHeaders,
        data: {
          status: 'IMPLEMENTED',
          changeRemark: 'E2E 测试：无 MOC 应拒绝',
        },
      },
    );
    expect(noMocResp.status(), '无 MOC 应返回 422').toBe(422);
    const noMocBody = await noMocResp.json();
    expect(noMocBody.code, '错误码应为 ERR_MOC_REQUIRED').toBe('ERR_MOC_REQUIRED');
    console.log('[E2E-D3] 无 MOC 拒绝通过 (422 ERR_MOC_REQUIRED)');

    // 4. 测试 MOC 不适用但无依据 → 422
    const naNoReasonResp = await request.patch(
      `${API_BASE_URL}/tracker/${loopId}/status`,
      {
        headers: authHeaders,
        data: {
          status: 'IMPLEMENTED',
          mocNotApplicable: true,
          changeRemark: 'E2E 测试：不适用无依据应拒绝',
        },
      },
    );
    expect(naNoReasonResp.status(), '不适用无依据应返回 422').toBe(422);
    expect((await naNoReasonResp.json()).code).toBe('ERR_MOC_REQUIRED');
    console.log('[E2E-D3] 不适用无依据拒绝通过 (422)');

    // 5. 测试 MOC 成功：IMPLEMENTED + mocRef → 200
    const mocRef = `MOC-E2E-${Date.now()}`;
    const successResp = await request.patch(
      `${API_BASE_URL}/tracker/${loopId}/status`,
      {
        headers: authHeaders,
        data: {
          status: 'IMPLEMENTED',
          mocRef,
          changeRemark: 'E2E 测试：MOC 闭环成功',
          comment: '已联系仪表班确认并实施',
        },
      },
    );
    expect(successResp.ok(), `IMPLEMENTED + mocRef 应成功: ${successResp.status()}`).toBeTruthy();
    const successBody = await successResp.json();
    expect(successBody.data.actionStatus).toBe('IMPLEMENTED');
    expect(successBody.data.mocRef).toBe(mocRef);
    expect(successBody.data.abComparison, 'IMPLEMENTED 应生成 A/B 对比').toBeTruthy();
    console.log(`[E2E-D3] MOC 闭环成功: mocRef=${mocRef}`);

    // 6. API 验证列表返回 MOC 字段（IMPLEMENTED 记录）
    // 使用 last_30_days 窗口 + 大 pageSize，确保刚更新的记录在结果中
    // （列表按 diagnosis.created_at 排序，刚更新的 tracker 不会浮到顶部）
    const implResp = await request.get(
      `${API_BASE_URL}/diagnosis/list?loopId=${loopId}&actionStatus=IMPLEMENTED&page=1&pageSize=100&timeWindow=last_30_days`,
      { headers: authHeaders },
    );
    const implBody = await implResp.json();
    const implItems: any[] = implBody?.data?.items ?? [];
    const implItem = implItems.find((it) => it.mocRef === mocRef);
    expect(implItem, '列表应返回含 mocRef 的已实施记录').toBeTruthy();
    console.log('[E2E-D3] 列表 MOC 字段返回验证通过');

    // 7. UI 验证：Tracker 列表页 + 状态更新 Modal MOC 字段展示
    await loginAs('IC_ENGINEER');
    await page.goto('/diagnosis/tracker');
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(1500);

    // 表格渲染
    await expect(page.locator('.ant-table').first()).toBeVisible({
      timeout: 10_000,
    });

    // 筛选"待处理"状态，确保 PENDING 行在第一页可见（默认按创建时间降序混合展示）
    const statusFilter = page
      .locator('.ant-select')
      .filter({ hasText: '处理状态' })
      .first();
    if (await statusFilter.isVisible().catch(() => false)) {
      await statusFilter.click();
      await page.waitForTimeout(300);
      const pendingOption = page
        .locator('.ant-select-item')
        .filter({ hasText: '待处理' });
      await expect(pendingOption).toBeVisible({ timeout: 3_000 });
      await pendingOption.click();
      await page.waitForTimeout(1000);
    }

    const tableRows = page.locator('.ant-table-tbody tr.ant-table-row');
    await expect(tableRows.first()).toBeVisible({ timeout: 10_000 });

    // 找到 PENDING 状态的行（筛选后应至少有一行），点击状态更新下拉菜单选"已实施"，验证 MOC 字段出现
    const pendingRow = tableRows.filter({ hasText: '待处理' }).first();
    const hasPendingRow = await pendingRow.isVisible().catch(() => false);

    if (hasPendingRow) {
      // 点击该行的"更新状态"按钮（直接打开 Modal，无需 Dropdown 中转）
      const updateStatusBtn = pendingRow.getByRole('button', {
        name: '更新状态',
      });
      await expect(updateStatusBtn).toBeVisible({ timeout: 5_000 });
      await updateStatusBtn.click();

      // Modal 应出现
      const modal = page.locator('.ant-modal').last();
      await expect(modal).toBeVisible({ timeout: 5_000 });

      // 在 Modal 内切换状态为"已实施"（通过 Select 组件）
      const statusSelect = modal.locator('.ant-select').first();
      await statusSelect.click();
      await page.waitForTimeout(300);
      // 使用 .last() 避免与工具栏状态筛选器的下拉选项冲突
      const implementedOption = page
        .locator('.ant-select-item')
        .filter({ hasText: '已实施' })
        .last();
      await expect(implementedOption).toBeVisible({ timeout: 3_000 });
      await implementedOption.click();

      // 切换到"已实施"后 MOC 区块应出现
      await expect(modal.getByText('MOC 变更管理关联')).toBeVisible({
        timeout: 3_000,
      });
      console.log('[E2E-D3] UI MOC 字段展示验证通过');

      // 关闭 Modal（取消）
      await page.keyboard.press('Escape');
    } else {
      console.log('[E2E-D3] 无 PENDING 行可测 UI MOC 展示，跳过 UI 断言');
    }
  });
});
