/**
 * MW-P5-05 视觉走查：三档分辨率截图 + 暗色模式
 *
 * 三档分辨率：
 *   - Desktop: 1920×1080
 *   - Laptop:  1366×768
 *   - Tablet:  768×1024
 *
 * 截图保存到 e2e/visual-inspection/ 目录
 */
import { expect, test } from '../fixtures/auth.js';
import * as fs from 'node:fs';
import * as path from 'node:path';

const SCREENSHOTS_DIR = 'visual-inspection';
fs.mkdirSync(SCREENSHOTS_DIR, { recursive: true });

const RESOLUTIONS = [
  { name: 'desktop-1920x1080', width: 1920, height: 1080 },
  { name: 'laptop-1366x768', width: 1366, height: 768 },
  { name: 'tablet-768x1024', width: 768, height: 1024 },
];

const PAGES = [
  { name: 'workbench', path: '/monitor/loop-workbench' },
  { name: 'attention', path: '/monitor/attention' },
  { name: 'diagnosis', path: '/diagnosis/tasks' },
  { name: 'dashboard', path: '/metric/pid-dashboard' },
];

test.describe('MW-P5-05 视觉走查', () => {
  test('VISUAL-LIGHT: 三档分辨率亮色模式截图', async ({ page, loginAs }) => {
    await loginAs('ADMIN');

    for (const resolution of RESOLUTIONS) {
      await page.setViewportSize({
        width: resolution.width,
        height: resolution.height,
      });

      for (const pageInfo of PAGES) {
        await page.goto(pageInfo.path);
        await page.waitForLoadState('domcontentloaded');
        await page.waitForTimeout(3000);

        const filename = path.join(
          SCREENSHOTS_DIR,
          `light-${pageInfo.name}-${resolution.name}.png`,
        );
        await page.screenshot({ path: filename, fullPage: false });
        console.log(`📸 ${filename}`);
      }
    }

    // 验证截图文件已生成
    const files = fs.readdirSync(SCREENSHOTS_DIR).filter((f) => f.startsWith('light-'));
    expect(files.length, '应生成至少 9 张亮色截图').toBeGreaterThanOrEqual(9);
  });

  test('VISUAL-DARK: 暗色模式截图', async ({ page, loginAs }) => {
    await loginAs('ADMIN');

    // 切换到暗色模式
    await page.evaluate(() => {
      const prefsStr = localStorage.getItem('__VBF__preferences');
      const prefs = prefsStr ? JSON.parse(prefsStr) : {};
      if (!prefs.theme) prefs.theme = {};
      prefs.theme.mode = 'dark';
      localStorage.setItem('__VBF__preferences', JSON.stringify(prefs));
    });
    await page.reload();
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(2000);

    await page.setViewportSize({ width: 1920, height: 1080 });

    for (const pageInfo of PAGES) {
      await page.goto(pageInfo.path);
      await page.waitForLoadState('domcontentloaded');
      await page.waitForTimeout(3000);

      const filename = path.join(
        SCREENSHOTS_DIR,
        `dark-${pageInfo.name}-1920x1080.png`,
      );
      await page.screenshot({ path: filename, fullPage: false });
      console.log(`📸 ${filename}`);
    }

    // 恢复亮色模式
    await page.evaluate(() => {
      const prefsStr = localStorage.getItem('__VBF__preferences');
      const prefs = prefsStr ? JSON.parse(prefsStr) : {};
      if (!prefs.theme) prefs.theme = {};
      prefs.theme.mode = 'light';
      localStorage.setItem('__VBF__preferences', JSON.stringify(prefs));
    });

    const darkFiles = fs.readdirSync(SCREENSHOTS_DIR).filter((f) => f.startsWith('dark-'));
    expect(darkFiles.length, '应生成至少 4 张暗色截图').toBeGreaterThanOrEqual(4);
  });
});
