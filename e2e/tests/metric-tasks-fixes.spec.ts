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
 * - F8 手动任务：行内 评估 → 删除（ClpmDangerConfirmModal typed confirmation）
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

  // 保存按钮在无变更时 disabled
  const saveBtn = page.getByRole('button', { name: /保存配置/ });
  await expect(saveBtn).toBeDisabled();

  // 修改为 17 → 保存按钮可用
  await concurrencyInput.fill('17');
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
  await gotoTasks(page);
  await switchTab(page, '评估历史');

  // API 侧同口径计数（7/19 本地日 = 2026-07-18T16:00Z ~ 2026-07-19T15:59:59Z）
  const login = await loginViaApi(request, 'admin', 'admin123');
  const apiResp = await request.get(
    `${API_BASE_URL}/performance/loops/snapshots?latestOnly=false` +
      `&startTime=2026-07-18T16:00:00.000Z&endTime=2026-07-19T15:59:59.999Z&pageSize=1`,
    { headers: { Authorization: `Bearer ${login.accessToken}` } },
  );
  const apiTotal = (await apiResp.json()).data.total as number;
  expect(apiTotal).toBeGreaterThan(100); // 全天数据 >> 27（修复前只能命中 1 小时）

  // UI 选 2026-07-19 ~ 2026-07-19
  const picker = page.locator('.ant-picker-range:visible').first();
  await fillDateRange(picker, '2026-07-19', '2026-07-19');
  await page.waitForTimeout(2000);

  const uiTotal = await readTotal(page);
  // 允许小时级快照新增带来的漂移
  expect(Math.abs(uiTotal - apiTotal)).toBeLessThanOrEqual(60);
});

// ---------------------------------------------------------------------------
// F3 评估历史：综合评分列服务端排序（desc → asc）
// ---------------------------------------------------------------------------
test('F3 评估历史综合评分列排序生效', async ({ page }) => {
  await gotoTasks(page);
  await switchTab(page, '评估历史');

  // 先限定到今天窗口，数据量适中且含 SUCCESS 快照
  const picker = page.locator('.ant-picker-range:visible').first();
  await fillDateRange(picker, '2026-07-19', '2026-07-19');
  await page.waitForTimeout(2000);

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
  await scoreHeader.click();
  await page.waitForTimeout(2000);
  const first = await readScores();
  expect(first.length).toBeGreaterThan(3);
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
test('F4/F5 详情抽屉血缘完整且 E 级评分掩码', async ({ page }) => {
  test.setTimeout(90_000);
  await gotoTasks(page);
  await switchTab(page, '评估历史');

  // F4：限定今天窗口 + 状态=成功，点击第一行 详情 → 数据血缘详情字段完整
  const picker = page.locator('.ant-picker-range:visible').first();
  await fillDateRange(picker, '2026-07-19', '2026-07-19');
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
  // 清空状态筛选，仅留可信度（先点掉日期筛选不动，直接加可信度）
  const confidenceSelect = page
    .locator('.ant-select:visible', { hasText: '可信度' })
    .first();
  await confidenceSelect.click();
  await page
    .locator('.ant-select-item-option', { hasText: 'E 不足' })
    .first()
    .click();
  await page.waitForTimeout(2000);

  const firstScoreCell = page.locator('tbody tr:visible td:nth-child(3)').first();
  await expect(firstScoreCell).toHaveText('—', { timeout: 10_000 });
});

// ---------------------------------------------------------------------------
// F6/F7/F8/F10 手动任务 Tab
// ---------------------------------------------------------------------------
test('F6/F7/F10 手动任务：预览失效 + 评估回路列 + 禁未来日期', async ({ page }) => {
  test.setTimeout(90_000);
  await gotoTasks(page); // 默认即手动任务 Tab

  // F7：既有回填任务「7-19」评估回路列应为 27（修复前为工作项 594）
  const row719 = page.locator('tbody tr', { hasText: '7-19' }).first();
  await expect(row719).toBeVisible({ timeout: 10_000 });
  // 列序：多选框/任务标题/任务类型/评估回路(4th)
  await expect(row719.locator('td').nth(3)).toHaveText('27');

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

test('F8 手动任务：行内评估 → 删除（typed confirmation）', async ({
  page,
  request,
}) => {
  test.setTimeout(120_000);
  const login = await loginViaApi(request, 'admin', 'admin123');
  const headers = {
    Authorization: `Bearer ${login.accessToken}`,
    'Content-Type': 'application/json',
  };

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
  const taskId = (await createResp.json()).data.taskId as string;
  const shortCode = taskId.slice(-8).toUpperCase();

  try {
    await gotoTasks(page);
    const row = page.locator('tbody tr', { hasText: 'e2e-row-actions' });
    await expect(row).toBeVisible({ timeout: 10_000 });
    await expect(row).toContainText('待执行');

    // 行内 评估 按钮仅 PENDING 可见，点击启动
    await row.getByRole('button', { name: '评估' }).click();
    await expect(page.locator('.ant-message')).toContainText('任务已开始执行');

    // 等待任务完成（轮询刷新，1 回路 1 窗口秒级）
    await expect(row).toContainText('成功', { timeout: 60_000 });

    // 行内 删除 → ClpmDangerConfirmModal → typed confirmation
    await row.getByRole('button', { name: '删除' }).click();
    const modal = page.locator('.clpm-danger-confirm:visible');
    await expect(modal).toBeVisible({ timeout: 5000 });
    await expect(modal).toContainText(shortCode);
    // 未输入确认码时确认按钮 disabled
    const okBtn = modal.getByRole('button', { name: '确认删除' });
    await expect(okBtn).toBeDisabled();
    await modal.locator('input[placeholder*="请输入"]').fill(shortCode);
    await expect(okBtn).toBeEnabled();
    await okBtn.click();
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
test('F9 自动任务：行点击详情抽屉 + 时间筛选 endOfDay', async ({ page }) => {
  await gotoTasks(page);
  await switchTab(page, '自动任务');

  // 时间筛选选今天（endOfDay 修复前选「今天」会漏掉当日全部任务）
  const picker = page.locator('.ant-picker-range:visible').first();
  await fillDateRange(picker, '2026-07-19', '2026-07-19');
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
