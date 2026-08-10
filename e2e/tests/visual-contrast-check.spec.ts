/**
 * MW-P5-05 暗色模式对比度验证
 *
 * 检查暗色模式下关键文本元素的对比度是否符合 WCAG AA 标准（4.5:1）
 */
import { expect, test } from '../fixtures/auth.js';

test.describe('MW-P5-05 暗色对比度', () => {
  test('CONTRAST-CHECK: 工作台暗色模式文本对比度', async ({ page, loginAs }) => {
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

    await page.goto('/monitor/loop-workbench');
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(3000);

    // 检查暗色模式是否生效
    const darkStatus = await page.evaluate(() => {
      const html = document.documentElement;
      const body = document.body;
      return {
        htmlClass: html.className,
        bodyClass: body.className,
        htmlColorScheme: getComputedStyle(html).colorScheme,
        bodyBg: getComputedStyle(body).backgroundColor,
        bodyColor: getComputedStyle(body).color,
      };
    });
    console.log('暗色模式状态:', JSON.stringify(darkStatus, null, 2));

    // 采样文本元素并计算对比度
    const contrastResults = await page.evaluate(() => {
      function parseColor(color: string): [number, number, number] | null {
        const match = color.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
        if (!match) return null;
        return [parseInt(match[1]), parseInt(match[2]), parseInt(match[3])];
      }

      function relativeLuminance([r, g, b]: [number, number, number]): number {
        const toLinear = (c: number) => {
          c /= 255;
          return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
        };
        return 0.2126 * toLinear(r) + 0.7152 * toLinear(g) + 0.0722 * toLinear(b);
      }

      function contrastRatio(color1: string, color2: string): number {
        const c1 = parseColor(color1);
        const c2 = parseColor(color2);
        if (!c1 || !c2) return 0;
        const l1 = relativeLuminance(c1);
        const l2 = relativeLuminance(c2);
        const lighter = Math.max(l1, l2);
        const darker = Math.min(l1, l2);
        return (lighter + 0.05) / (darker + 0.05);
      }

      const elements = document.querySelectorAll(
        'h1, h2, h3, p, span, td, th, .ant-typography, .ant-table-cell, .ant-btn',
      );
      const results: { tag: string; text: string; ratio: number; pass: boolean }[] = [];
      let checked = 0;

      for (const el of elements) {
        if (checked >= 20) break;
        const style = getComputedStyle(el);
        const text = (el.textContent || '').trim().slice(0, 30);
        if (!text) continue;

        // 获取父元素背景色
        let parent = el.parentElement;
        let bgColor = 'rgb(255, 255, 255)';
        while (parent) {
          const bg = getComputedStyle(parent).backgroundColor;
          if (bg !== 'rgba(0, 0, 0, 0)' && bg !== 'transparent') {
            bgColor = bg;
            break;
          }
          parent = parent.parentElement;
        }

        const ratio = contrastRatio(style.color, bgColor);
        results.push({
          tag: el.tagName,
          text,
          ratio: Math.round(ratio * 10) / 10,
          pass: ratio >= 4.5,
        });
        checked++;
      }

      return results;
    });

    console.log('\n=== 暗色模式对比度检查 ===');
    for (const r of contrastResults) {
      console.log(`  ${r.pass ? '✅' : '❌'} ${r.tag} "${r.text}" → ${r.ratio}:1 ${r.pass ? '(AA)' : '(不达标)'}`);
    }

    const passCount = contrastResults.filter((r) => r.pass).length;
    const failCount = contrastResults.filter((r) => !r.pass).length;
    console.log(`\n总计: ${passCount} 通过 / ${failCount} 不达标 / ${contrastResults.length} 采样`);

    // 至少 80% 的文本元素达到 WCAG AA 标准
    const passRate = passCount / contrastResults.length;
    expect(passRate, `对比度达标率应 ≥ 80%, 实际 ${(passRate * 100).toFixed(0)}%`).toBeGreaterThanOrEqual(0.8);
  });
});
