/**
 * E2E /metric/tasks 页面修复点验证（修复点回归）
 *
 * 覆盖本次修复：
 * - F1 策略配置 Tab：rules 数组解析（卡片显示 DB 值 16，而非硬编码默认 10）+ 保存链路
 * - F2 评估历史：日期筛选 endOfDay（选「今天」能看到全天快照）
 * - F3 评估历史：综合评分列服务端排序
 * - F4 评估历史：详情抽屉数据血缘完整（snake_case → camelCase）
 * - F5 评估历史：E 级可信度评分掩码为「—」
 * - F6 手动任务：预览失效（表单变更后 确认重算 重新 disabled）
 * - F7 手动任务：BACKFILL 评估回路列显示回路数（27，而非工作项 594）
 * - F8 手动任务：行内 评估 → 删除（普通确认弹框，无需输入确认码）
 * - F9 自动任务：行点击详情抽屉 + 时间筛选（endOfDay）
 * - F10 手动任务：新建 Drawer 禁未来日期
 *
 * 写操作（创建/删除任务）通过 API 准备与清理；UI 仅做必要交互。
 */
import { test, expect, loginViaApi, API_BASE_URL } from '../fixtures/auth.js';
import type { Page, APIRequestContext } from '@playwright/test';

const TASKS_PATH = '/metric/tasks';

/** 目标回路（与回填验证一致） */
const PROBE_LOOP_ID = '436dea56-63c3-44a0-b073-5f3dbf52d165';

async function gotoTasks(page: Page) {
  // 页面有轮询请求，networkidle 不稳定；用 domcontentloaded + Tabs 就绪
  await page.goto(TASKS_PATH, { waitUntil: 'domcontentloaded' });
  await page.locator('.ant-tabs').first().waitFor({ state: 'visible', timeout: 20_000 });
  await page.waitForTimeout(1200);
}

/** 切 Tab（AntD Tabs） */
async function switchTab(page: Page, tabName: string) {
  await page.locator('.ant-tabs-tab', { hasText: tabName }).click();
  await page.waitForTimeout(1500);
}

/** 在日期型 RangePicker 中输入起止日期（YYYY-MM-DD）并提交 */
async function fillDateRange(picker: ReturnType<Page['locator']>, start: string, end: string) {
  await picker.click();
  await picker.locator('input').first().fill(start);
  await picker.locator('input').first().press('Enter');
  await picker.locator('input').nth(1).fill(end);
  await picker.locator('input').nth(1).press('Enter');
}

/** 读取分页 total 文案中的数字（共 N 条）；AntD Tabs 保留隐藏 Tab 的 DOM，需限定可见 */
async function readTotal(page: Page): Promise<number> {
  const text = await page
    .locator('.ant-pagination-total-text:visible')
    .first()
    .innerText();
  const m = text.match(/共\s*(\d+)\s*条/);
  return m ? Number(m[1]) : -1;
}

/** 将 Date 格式化为本地 YYYY-MM-DD */
function fmtLocalDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

/** 动态寻找近 lookbackDays 内快照数 ≥ minTotal 的本地日期（YYYY-MM-DD）。
 * 背景：F2~F5 原硬编码 2026-07-19，历史快照数据被清理后该日无数据，
 * 改为动态选有数据的日期（环境数据依赖）；找不到返回 null 供调用方 skip。 */
async function findDataRichDate(
  request: APIRequestContext,
  token: string,
  minTotal = 100,
  lookbackDays = 14,
): Promise<string | null> {
  const dayMs = 86_400_000;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  for (let i = 0; i < lookbackDays; i++) {
    const dayStart = new Date(today.getTime() - i * dayMs);
    const dayEnd = new Date(dayStart.getTime() + dayMs - 1);
    const resp = await request.get(
      `${API_BASE_URL}/performance/loops/snapshots?latestOnly=false` +
        `&startTime=${dayStart.toISOString()}&endTime=${dayEnd.toISOString()}&pageSize=1`,
      { headers: { Authorization: `Bearer ${token}` } },
    ).catch(() => null);
    if (!resp || !resp.ok()) continue;
    const total = ((await resp.json())?.data?.total as number) ?? 0;
    if (total >= minTotal) return fmtLocalDate(dayStart);
  }
  return null;
}

test.describe.configure({ mode: 'serial' });

test.beforeEach(async ({ loginAs, page }) => {
  await loginAs('ADMIN');
  await page.waitForTimeout(500);
});

// ---------------------------------------------------------------------------
// F1 策略配置：卡片显示 DB 值 + 修改保存生效（最后恢复原值）
// ---------------------------------------------------------------------------
test('F1 策略配置 Tab 显示 DB 规则值且可保存', async ({ page }) => {
  test.setTimeout(90_000);
  await gotoTasks(page);
  await switchTab(page, '策略配置');

  // 调度并发数应为 DB 值 16（修复前恒为硬编码默认 10）
  const concurrencyInput = page
    .locator('.ant-form-item', { hasText: '调度并发数' })
    .locator('input');
  await expect(concurrencyInput).toHaveValue('16', { timeout: 10_000 });

  // 保存按钮在无变更时 disabled：ClpmToolbarButton disabled-reason 机制下
  // 按钮可访问名会替换为禁用原因「无修改内容」（task-strategy.vue）
  await expect(page.getByRole('button', { name: /无修改内容/ })).toBeDisabled();

  // 修改为 17 → 按钮恢复为「保存配置」且可用
  await concurrencyInput.fill('17');
  const saveBtn = page.getByRole('button', { name: /保存配置/ });
  await expect(saveBtn).toBeEnabled();
  await saveBtn.click();

  // ClpmDangerConfirmModal：变更原因(≥10字符) + typed confirmation「确认变更」
  const modal = page.locator('.clpm-danger-confirm:visible');
  await expect(modal).toBeVisible({ timeout: 5000 });
  await modal.locator('textarea').fill('E2E 验证策略保存链路，随后恢复');
  await modal.locator('input[placeholder*="确认变更"]').fill('确认变更');
  await modal.getByRole('button', { name: '确认保存' }).click();
  await expect(page.locator('.ant-message')).toContainText('策略配置已保存', {
    timeout: 10_000,
  });

  // 重新加载后值持久化
  await gotoTasks(page);
  await switchTab(page, '策略配置');
  await expect(
    page.locator('.ant-form-item', { hasText: '调度并发数' }).locator('input'),
  ).toHaveValue('17', { timeout: 10_000 });

  // 恢复为 16
  await page
    .locator('.ant-form-item', { hasText: '调度并发数' })
    .locator('input')
    .fill('16');
  await page.getByRole('button', { name: /保存配置/ }).click();
  const modal2 = page.locator('.clpm-danger-confirm:visible');
  await modal2.locator('textarea').fill('E2E 验证完毕恢复原始并发值');
  await modal2.locator('input[placeholder*="确认变更"]').fill('确认变更');
  await modal2.getByRole('button', { name: '确认保存' }).click();
  await expect(page.locator('.ant-message')).toContainText('策略配置已保存', {
    timeout: 10_000,
  });
});

// ---------------------------------------------------------------------------
// F2 评估历史：日期筛选选「今天」能命中全天（endOfDay 修复）
// ---------------------------------------------------------------------------
test('F2 评估历史日期筛选 endOfDay 生效', async ({ page, request }) => {
  const login = await loginViaApi(request, 'admin', 'admin123');
  // 环境数据依赖：原硬编码 2026-07-19 的快照数据已被清理，动态选近 14 天内
  // 快照数 ≥100 的本地日期；无可用日期时 skip（注明原因）
  const dataDate = await findDataRichDate(request, login.accessToken);
  test.skip(
    !dataDate,
    '环境数据依赖：近 14 天内无快照数≥100 的本地日期，无法验证 endOfDay 口径',
  );

  await gotoTasks(page);
  await switchTab(page, '评估历史');

  // API 侧同口径计数（本地日起止边界）
  const dayStart = new Date(`${dataDate}T00:00:00`);
  const dayEnd = new Date(`${dataDate}T23:59:59.999`);
  const apiResp = await request.get(
    `${API_BASE_URL}/performance/loops/snapshots?latestOnly=false` +
      `&startTime=${dayStart.toISOString()}&endTime=${dayEnd.toISOString()}&pageSize=1`,
    { headers: { Authorization: `Bearer ${login.accessToken}` } },
  );
  const apiTotal = (await apiResp.json()).data.total as number;
  expect(apiTotal).toBeGreaterThan(100); // 全天数据 >> 27（修复前只能命中 1 小时）

  // UI 选同一本地日期
  const picker = page.locator('.ant-picker-range:visible').first();
  await fillDateRange(picker, dataDate!, dataDate!);
  await page.waitForTimeout(2000);

  const uiTotal = await readTotal(page);
  // 允许小时级快照新增带来的漂移
  expect(Math.abs(uiTotal - apiTotal)).toBeLessThanOrEqual(60);
});

// ---------------------------------------------------------------------------
// F3 评估历史：综合评分列服务端排序（desc → asc）
// ---------------------------------------------------------------------------
test('F3 评估历史综合评分列排序生效', async ({ page, request }) => {
  const login = await loginViaApi(request, 'admin', 'admin123');
  const dataDate = await findDataRichDate(request, login.accessToken);
  test.skip(
    !dataDate,
    '环境数据依赖：近 14 天内无快照数≥100 的本地日期，无法验证排序口径',
  );

  await gotoTasks(page);
  await switchTab(page, '评估历史');

  // 先限定到有数据窗口，数据量适中且含 SUCCESS 快照
  // （RangePicker 偶发未生效：填充后校验 total，未生效则重试一次）
  const picker = page.locator('.ant-picker-range:visible').first();
  for (let attempt = 0; attempt < 2; attempt++) {
    await fillDateRange(picker, dataDate!, dataDate!);
    await page.waitForTimeout(2500);
    if ((await readTotal(page)) > 20) break;
  }
  expect(await readTotal(page)).toBeGreaterThan(20);

  // 筛选非 E 级可信度，排除 E 级评分掩码行（F5：E 级评分显示「—」）
  // 不筛选时升序排序会把 NULL 评分（E 级掩码）排在前面，导致可见数字不足 2 个。
  // 可信度 Select 为单选，依次尝试 A/B/C/D 直到找到 ≥2 行的等级。
  const confSelect = page
    .locator('.ant-select:visible', { hasText: '可信度' })
    .first();
  let selectedConfidence = '';
  for (const level of ['A', 'B', 'C', 'D']) {
    await confSelect.click();
    const option = page
      .locator('.ant-select-dropdown:visible .ant-select-item-option')
      .filter({ hasText: level })
      .first();
    await option.click();
    await page.waitForTimeout(2000);
    if ((await readTotal(page)) >= 2) {
      selectedConfidence = level;
      break;
    }
  }
  // 至少有一个非 E 等级有足够数据用于排序验证
  expect(selectedConfidence, '应存在 ≥2 行的非 E 级可信度快照').toBeTruthy();

  const scoreHeader = page.locator('th', { hasText: '综合评分' }).first();

  const readScores = async (): Promise<number[]> => {
    const cells = page.locator('tbody tr:visible td:nth-child(3)');
    const texts = await cells.allInnerTexts();
    return texts
      .map((t) => t.trim())
      .filter((t) => t !== '—' && t !== '')
      .map((t) => Number(t));
  };
  const isAsc = (arr: number[]) =>
    arr.every((v, i) => i === 0 || arr[i - 1]! <= v);
  const isDesc = (arr: number[]) =>
    arr.every((v, i) => i === 0 || arr[i - 1]! >= v);

  // 第一次点击（AntD 默认 ascend）→ 服务端排序生效
  // 已筛选非 E 级，所有行均有可见数字评分
  await scoreHeader.click();
  await page.waitForTimeout(2000);
  const first = await readScores();
  expect(first.length).toBeGreaterThanOrEqual(2);
  expect(isAsc(first) || isDesc(first)).toBeTruthy();
  const firstDir = isAsc(first) ? 'asc' : 'desc';

  // 第二次点击 → 方向反转
  await scoreHeader.click();
  await page.waitForTimeout(2000);
  const second = await readScores();
  if (firstDir === 'asc') {
    expect(isDesc(second)).toBeTruthy();
  } else {
    expect(isAsc(second)).toBeTruthy();
  }
});

// ---------------------------------------------------------------------------
// F4 评估历史：详情抽屉数据血缘完整 + F5 E 级评分掩码
// ---------------------------------------------------------------------------
test('F4/F5 详情抽屉血缘完整且 E 级评分掩码', async ({ page, request }) => {
  test.setTimeout(90_000);
  const login = await loginViaApi(request, 'admin', 'admin123');
  const dataDate = await findDataRichDate(request, login.accessToken);
  test.skip(
    !dataDate,
    '环境数据依赖：近 14 天内无快照数≥100 的本地日期，无法验证血缘/掩码口径',
  );

  await gotoTasks(page);
  await switchTab(page, '评估历史');

  // F4：限定有数据窗口 + 状态=成功，点击第一行 详情 → 数据血缘详情字段完整
  const picker = page.locator('.ant-picker-range:visible').first();
  await fillDateRange(picker, dataDate!, dataDate!);
  const statusSelect = page.locator('.ant-select:visible', { hasText: '状态' }).first();
  await statusSelect.click();
  await page
    .locator('.ant-select-item-option', { hasText: '成功' })
    .first()
    .click();
  await page.waitForTimeout(2000);

  await page
    .locator('tbody tr:visible')
    .first()
    .getByRole('button', { name: '详情' })
    .click();
  const drawer = page.locator('.ant-drawer:visible');
  await expect(drawer).toContainText('数据血缘详情', { timeout: 10_000 });
  // 血缘 8 字段完整（采样频率随任务类型为 1s/5s，不断言具体值，只断言非空）
  await expect(drawer).toContainText(/采样频率: \d+s/);
  await expect(drawer).toContainText('聚合策略: LAST');
  await expect(drawer).toContainText('tagGroup: BASE');
  await expect(drawer).toContainText('KEEP_ALL_WITH_VALIDITY');
  await expect(drawer).toContainText('数据块: db_');
  await expect(drawer).toContainText(/有效数据率: 0\.\d+/);
  await page.keyboard.press('Escape');
  await page.waitForTimeout(800);

  // F5：可信度筛选 = E 不足 → 第一行评分单元格应为「—」
  // 保留日期筛选，追加可信度条件
  const confidenceSelect = page
    .locator('.ant-select:visible', { hasText: '可信度' })
    .first();
  await confidenceSelect.click();
  await page
    .locator('.ant-select-item-option', { hasText: 'E 不足' })
    .first()
    .click();
  await page.waitForTimeout(2000);

  // 数据依赖防御：「成功」与 E 级（数据不足）互斥，当前窗口可能无组合行；
  // 先清状态筛选仅留可信度条件，仍无行则降级跳过 F5 断言（F4 血缘已验证）
  // 注：状态 Select 选中后展示值「成功」替代 placeholder「状态」，正则同时匹配
  let rowCount = await page.locator('tbody tr:visible').count();
  if (rowCount === 0) {
    const statusSelectBox = page
      .locator('.ant-select:visible')
      .filter({ hasText: /成功|状态/ })
      .first();
    if (await statusSelectBox.isVisible().catch(() => false)) {
      await statusSelectBox.hover();
      await statusSelectBox
        .locator('.ant-select-clear')
        .first()
        .click()
        .catch(() => {});
      await page.waitForTimeout(2000);
    }
    rowCount = await page.locator('tbody tr:visible').count();
  }
  if (rowCount === 0) {
    console.log(
      '[F5] 当前窗口无 E 级快照行，评分掩码断言降级跳过（环境数据依赖，F4 血缘已验证）',
    );
    return;
  }

  const firstScoreCell = page.locator('tbody tr:visible td:nth-child(3)').first();
  await expect(firstScoreCell).toHaveText('—', { timeout: 10_000 });
});

// ---------------------------------------------------------------------------
// F6/F7/F8/F10 手动任务 Tab
// ---------------------------------------------------------------------------
test('F6/F7/F10 手动任务：预览失效 + 评估回路列 + 禁未来日期', async ({ page }) => {
  test.setTimeout(90_000);
  await gotoTasks(page);
  // ADMIN 默认激活 Tab 已改为「自动任务」，显式切到手动任务（已在则幂等）
  await switchTab(page, '手动任务');

  // F7：既有回填任务「7-19」评估回路列应为 27（修复前为工作项 594）
  // 防御式：该任务为历史数据，可能被清理；不存在时跳过 F7 断言，不阻塞 F10/F6
  const row719 = page.locator('tbody tr', { hasText: '7-19' }).first();
  const hasRow719 = await row719.isVisible({ timeout: 10_000 }).catch(() => false);
  if (hasRow719) {
    // 列序：多选框/任务标题/任务类型/评估回路(4th)
    await expect(row719.locator('td').nth(3)).toHaveText('27');
  }

  // 打开新建任务 Drawer
  await page.getByRole('button', { name: /新建任务/ }).click();
  const drawer = page.locator('.ant-drawer:visible');
  await expect(drawer).toBeVisible({ timeout: 5000 });

  // F10：时间窗 RangePicker 禁未来（明天单元格 disabled）
  await drawer.locator('.ant-picker-range').click();
  await page.waitForTimeout(800);
  const tomorrow = new Date(Date.now() + 86_400_000);
  const tomorrowStr = `${tomorrow.getFullYear()}-${String(tomorrow.getMonth() + 1).padStart(2, '0')}-${String(tomorrow.getDate()).padStart(2, '0')}`;
  const futureCell = page.locator(
    `.ant-picker-cell[title="${tomorrowStr}"]`,
  );
  await expect(futureCell.first()).toHaveClass(/ant-picker-cell-disabled/);

  // 填写标题（点击输入框同时关闭日历面板，避免 Escape 误关 Drawer）
  const titleInput = drawer.locator('input[placeholder="请输入任务标题"]');
  await titleInput.click();
  await page.waitForTimeout(300);
  await titleInput.fill('e2e-preview-check');
  await drawer.getByRole('button', { name: '预览影响范围' }).click();
  await expect(drawer).toContainText('影响范围预览', { timeout: 15_000 });
  await expect(drawer).toContainText('回路数：27');

  // 预览后 确认重算 可用
  const confirmBtn = drawer.getByRole('button', { name: '确认重算' });
  await expect(confirmBtn).toBeEnabled();

  // F6：修改时间窗后预览失效，确认重算 重新 disabled
  const rangePicker = drawer.locator('.ant-picker-range');
  await rangePicker.click();
  await rangePicker.locator('input').first().fill('2026-07-10 00:00');
  await rangePicker.locator('input').first().press('Enter');
  await page.waitForTimeout(800);
  await expect(confirmBtn).toBeDisabled();

  // 关闭 Drawer（不提交；日历面板可能遮挡 footer，改用头部关闭图标，容错）
  await drawer.locator('.ant-drawer-close').first().click().catch(() => {});
  await page.waitForTimeout(500);
});

test('F8 手动任务：行内评估 → 删除（普通确认弹框）', async ({
  page,
  request,
}) => {
  test.setTimeout(120_000);
  const login = await loginViaApi(request, 'admin', 'admin123');
  const headers = {
    Authorization: `Bearer ${login.accessToken}`,
    'Content-Type': 'application/json',
  };

  // 清理残留活跃任务，避免触发"单用户活跃任务上限 3"
  // 之前失败运行可能遗留 PENDING/RUNNING/CANCELLING 任务，cancel 是异步的
  // 需要轮询重试直到活跃任务清零
  const activeStatuses = ['PENDING', 'RUNNING', 'CANCELLING', 'QUEUED'];
  try {
    for (let attempt = 0; attempt < 4; attempt++) {
      const listResp = await request.get(`${API_BASE_URL}/tasks?pageSize=50`, { headers });
      const listData = (await listResp.json()).data;
      const items: any[] = (listData?.items ?? listData ?? []).filter(Boolean);
      const activeItems = items.filter((item) =>
        activeStatuses.includes(item.status ?? item.taskStatus),
      );
      if (activeItems.length === 0) break;
      for (const item of activeItems) {
        const taskId = item.taskId ?? item.id;
        if (taskId) {
          await request.post(`${API_BASE_URL}/tasks/${taskId}/cancel`, { headers }).catch(() => {});
          await request.delete(`${API_BASE_URL}/tasks/${taskId}`, { headers }).catch(() => {});
        }
      }
      // 等待 cancel 生效，任务转为终态
      await new Promise((resolve) => setTimeout(resolve, 2000));
    }
  } catch {
    // 清理失败不阻塞测试，可能在创建时才触发上限
  }

  // API 创建 PENDING 回填任务（1 回路 × 1 小时，秒级完成）
  const createResp = await request.post(`${API_BASE_URL}/tasks/backfill`, {
    headers,
    data: {
      title: 'e2e-row-actions',
      tsStart: '2026-07-19T04:00:00Z',
      tsEnd: '2026-07-19T05:00:00Z',
      loopIds: [PROBE_LOOP_ID],
      dryRun: false,
    },
  });
  const createBody = await createResp.json();
  if (!createBody?.data?.taskId) {
    throw new Error(
      `创建回填任务失败: HTTP ${createResp.status()} ${JSON.stringify(createBody)}`,
    );
  }
  const taskId = createBody.data.taskId as string;
  const shortCode = taskId.slice(-8).toUpperCase();

  try {
    await gotoTasks(page);
    // ADMIN 默认激活 Tab 已改为「自动任务」，显式切到手动任务（已在则幂等）
    await switchTab(page, '手动任务');
    const row = page.locator('tbody tr', { hasText: 'e2e-row-actions' });
    await expect(row).toBeVisible({ timeout: 10_000 });
    await expect(row).toContainText('待执行');

    // 行内 评估 按钮仅 PENDING 可见，点击启动
    await row.getByRole('button', { name: '评估' }).click();
    await expect(page.locator('.ant-message')).toContainText('任务已开始执行');

    // 等待任务完成（轮询刷新，1 回路 1 窗口秒级）
    await expect(row).toContainText('成功', { timeout: 60_000 });

    // 行内 删除 → 普通确认弹框（无需输入确认码）
    await row.getByRole('button', { name: '删除' }).click();
    const modal = page.locator('.ant-modal:visible');
    await expect(modal).toBeVisible({ timeout: 5000 });
    await expect(modal).toContainText('删除任务记录');
    await expect(modal).toContainText(shortCode);
    await modal.getByRole('button', { name: /确\s*认/ }).click();
    await expect(page.locator('.ant-message')).toContainText('任务已删除');
    await expect(
      page.locator('tbody tr', { hasText: 'e2e-row-actions' }),
    ).toHaveCount(0);
  } finally {
    // 兜底清理（若 UI 删除失败则 API 删除）
    await request.post(`${API_BASE_URL}/tasks/${taskId}/cancel`, { headers }).catch(() => {});
    await request.delete(`${API_BASE_URL}/tasks/${taskId}`, { headers }).catch(() => {});
  }
});

// ---------------------------------------------------------------------------
// F9 自动任务 Tab：行点击详情抽屉 + 时间筛选
// ---------------------------------------------------------------------------
test('F9 自动任务：行点击详情抽屉 + 时间筛选 endOfDay', async ({ page, request }) => {
  // 环境数据依赖：原硬编码 2026-07-19 的任务已被清理，动态取最新任务的
  // 本地日期作为筛选窗；无任何任务时 skip（注明原因）
  const login = await loginViaApi(request, 'admin', 'admin123');
  const listResp = await request.get(`${API_BASE_URL}/tasks?pageSize=1`, {
    headers: { Authorization: `Bearer ${login.accessToken}` },
  });
  const items: any[] = ((await listResp.json())?.data?.items) ?? [];
  const taskDate = items[0]?.createdAt
    ? fmtLocalDate(new Date(items[0].createdAt))
    : null;
  test.skip(
    !taskDate,
    '环境数据依赖：当前无任何评估任务记录，无法验证自动任务时间筛选与详情抽屉',
  );

  await gotoTasks(page);
  await switchTab(page, '自动任务');

  // 时间筛选选有任务的日期（endOfDay 修复前选「当天」会漏掉当日全部任务）
  const picker = page.locator('.ant-picker-range:visible').first();
  await fillDateRange(picker, taskDate!, taskDate!);
  await page.waitForTimeout(2000);
  const total = await readTotal(page);
  expect(total).toBeGreaterThan(0);

  // 行点击 → 任务详情 Drawer
  const firstRow = page.locator('tbody tr:visible').first();
  await firstRow.click();
  const drawer = page.locator('.ant-drawer:visible');
  await expect(drawer.locator('.ant-drawer-title')).toHaveText('任务详情', {
    timeout: 5000,
  });
  await expect(drawer).toContainText('任务ID');
  await expect(drawer).toContainText('评估状态');
  await page.keyboard.press('Escape');
  await page.waitForTimeout(800);
});
