/**
 * MW-P5-04 前端性能测量：首屏请求数 + DOM 节点数
 *
 * 测量工作台首屏加载时的网络请求数量和 DOM 节点数量。
 * 目标：首屏请求 ≤ 20，DOM 节点控制在合理范围。
 */
import { expect, test } from '../fixtures/auth.js';

test.describe('MW-P5-04 前端性能', () => {
  test('PERF-FRONT: 工作台首屏请求数 + DOM 节点数', async ({ page, loginAs }) => {
    await loginAs('ADMIN');

    // 监听所有 API 请求
    const apiRequests: { url: string; method: string }[] = [];
    page.on('request', (req) => {
      const url = req.url();
      // 只统计 API 请求（不含静态资源）
      if (url.includes('/api/v1/') && !url.includes('.js') && !url.includes('.css')) {
        apiRequests.push({ url, method: req.method() });
      }
    });

    // 导航到工作台
    await page.goto('/monitor/loop-workbench');
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(5000); // 等待首屏 API 请求完成

    // 统计 DOM 节点数
    const domNodeCount = await page.evaluate(() => {
      return document.querySelectorAll('*').length;
    });

    // 统计首屏 API 请求数（去重）
    const uniqueApiUrls = new Set(apiRequests.map((r) => r.url.split('?')[0]));
    const apiRequestCount = uniqueApiUrls.size;

    console.log(`\n=== MW-P5-04 前端性能 ===`);
    console.log(`首屏 API 请求数: ${apiRequestCount}`);
    console.log(`首屏 API 请求总次数: ${apiRequests.length}`);
    console.log(`DOM 节点数: ${domNodeCount}`);
    console.log(`API 请求列表:`);
    for (const url of [...uniqueApiUrls].sort()) {
      console.log(`  - ${url}`);
    }
    console.log(`========================\n`);

    // 断言：首屏 API 请求 ≤ 20（合理阈值）
    expect(apiRequestCount, '首屏 API 请求数应 ≤ 20').toBeLessThanOrEqual(20);
    // 断言：DOM 节点 > 0（页面已渲染）
    expect(domNodeCount, 'DOM 节点应 > 0').toBeGreaterThan(0);
  });

  test('PERF-FRONT: 关注队列首屏请求数', async ({ page, loginAs }) => {
    await loginAs('ADMIN');

    const apiRequests: { url: string; method: string }[] = [];
    page.on('request', (req) => {
      const url = req.url();
      if (url.includes('/api/v1/') && !url.includes('.js') && !url.includes('.css')) {
        apiRequests.push({ url, method: req.method() });
      }
    });

    await page.goto('/monitor/attention');
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(3000);

    const uniqueApiUrls = new Set(apiRequests.map((r) => r.url.split('?')[0]));
    console.log(`\n=== 关注队列首屏 ===`);
    console.log(`首屏 API 请求数: ${uniqueApiUrls.size}`);
    for (const url of [...uniqueApiUrls].sort()) {
      console.log(`  - ${url}`);
    }
    console.log(`==================\n`);

    expect(uniqueApiUrls.size, '关注队列首屏 API 请求 ≤ 15').toBeLessThanOrEqual(15);
  });
});
