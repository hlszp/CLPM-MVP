import { test, expect } from '../fixtures/auth.js';

test('诊断进度自动经历中间百分比、绿色完成和固定计时', async ({
  page,
  loginAs,
}, testInfo) => {
  test.setTimeout(120_000);
  await loginAs('ADMIN');
  let progress = 0;
  let requests = 0;
  const started = new Date();
  const title = '诊断进度验收1043';
  await page.route(/\/api\/v1\/tasks(?:\?|$)/, async (route) => {
    requests += 1;
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({
        code: '0',
        message: 'success',
        data: {
          total: 1,
          items: [{
            taskId: 'progress-1043',
            taskType: 'DIAGNOSIS',
            title,
            status: progress === 1 ? 'SUCCESS' : 'RUNNING',
            progress,
            currentStage: progress === 1 ? '完成 1/1' : '回路 1/1：数据质量预处理',
            loopsTotal: 1,
            loopsDone: progress === 1 ? 1 : 0,
            createdAt: started.toISOString(),
            startedAt: started.toISOString(),
            finishedAt: progress === 1
              ? new Date(started.getTime() + 8000).toISOString() : null,
            createdBy: 'admin',
            tsStart: '2026-10-18T00:00:00Z',
            tsEnd: '2026-10-19T00:00:00Z',
          }],
        },
      }),
    });
  });
  await page.goto('/diagnosis/tasks', { waitUntil: 'domcontentloaded' });
  const bar = page.getByRole('progressbar', { name: title + '进度', exact: true });
  const row = page.locator('.ant-table-row').filter({ hasText: title });
  await expect(bar).toHaveAttribute('aria-valuenow', '0');
  for (const percent of [25, 60]) {
    progress = percent / 100;
    // 不点击刷新：由真实页面的 usePolling 拉取下一次受控任务响应。
    await expect(bar).toHaveAttribute('aria-valuenow', String(percent), { timeout: 10_000 });
    await expect(bar.locator('.ant-progress-bg')).toHaveAttribute(
      'style', new RegExp('width:\\s*' + percent + '%'),
    );
    await page.screenshot({ path: testInfo.outputPath('progress-' + percent + '.png') });
  }
  progress = 1;
  await expect(bar).toHaveAttribute('aria-valuenow', '100', { timeout: 10_000 });
  await expect(bar.locator('.ant-progress-status-success')).toBeVisible();
  await expect(row).toContainText('00:08');
  const completedRequests = requests;
  await page.waitForTimeout(3500);
  expect(requests).toBe(completedRequests);
  await expect(row).toContainText('00:08');
  await page.screenshot({ path: testInfo.outputPath('progress-completed.png') });
});
