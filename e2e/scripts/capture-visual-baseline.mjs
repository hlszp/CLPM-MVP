/**
 * CLPM 视觉回归基线采集脚本（整改方案 A.8 / 工作清单 R5）
 *
 * 用途：
 *   1. 建立基线：node e2e/scripts/capture-visual-baseline.mjs
 *      → 截图写入 e2e/visual-baseline/baseline/ 并随 git 提交
 *   2. 整改后对比：node e2e/scripts/capture-visual-baseline.mjs --out current
 *      → 截图写入 e2e/visual-baseline/current/，与 baseline/ 对比（人工或工具 diff）
 *
 * 约定：
 *   - 视口 1440×900（与 e2e/playwright.config.ts 一致）
 *   - 默认登录 admin/admin123（可用 CLPM_E2E_USER/CLPM_E2E_PASS 覆盖）
 *   - 回路详情/工作台类页面需要一个真实 loopId：默认从后端 API 取第一个回路
 *   - 环境变量 CLPM_WEB_BASE / CLPM_API_BASE 可覆盖地址
 */
import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const e2eRequire = createRequire(path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'package.json'));
const { chromium } = e2eRequire('@playwright/test');

const WEB = process.env.CLPM_WEB_BASE || 'http://localhost:5666';
const API = process.env.CLPM_API_BASE || 'http://localhost:7101/api/v1';
const USER = process.env.CLPM_E2E_USER || 'admin';
const PASS = process.env.CLPM_E2E_PASS || 'admin123';

const outArg = process.argv.find((a) => a.startsWith('--out=')) || (process.argv.includes('--out') ? process.argv[process.argv.indexOf('--out') + 1] : null);
const OUT_NAME = (outArg && outArg.replace('--out=', '')) || 'baseline';
const OUT_DIR = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'visual-baseline', OUT_NAME);

/** BL-8：--dark 开启暗色模式采集（写用户主题偏好缓存，main.ts E2 补丁会读取应用） */
const DARK = process.argv.includes('--dark');
const THEME_CACHE_KEY = 'undefined-5.7.0-dev-preferences-theme';

/** 21 个关键页面（对应审查报告附录 B；[LOOP] 运行时替换为真实 loopId） */
const PAGES = [
  ['dashboard-workbench', '/dashboard/workbench'],
  ['loop-monitor', '/loop/monitor'],
  ['loop-config', '/config/loop'],
  ['loop-data', '/config/datasource'],
  ['loop-workbench', '/loop/workbench?loopId=[LOOP]'],
  ['metric-pid-dashboard', '/metric/pid-dashboard'],
  ['metric-loop-performance', '/metric/loop-performance'],
  ['metric-kpi-report', '/metric/kpi-report'],
  ['metric-config', '/config/metric'],
  ['diagnosis-overview', '/diagnosis/overview'],
  ['diagnosis-tasks', '/diagnosis/tasks'],
  ['diagnosis-tracker', '/diagnosis/tracker'],
  ['diagnosis-loop-analysis', '/diagnosis/loop-analysis'],
  ['diagnosis-detail', '/diagnosis/detail/[LOOP]'],
  ['tuning-workbench', '/tuning/workbench'],
  ['tuning-detail', '/tuning/detail?loopId=[LOOP]'],
  ['alert-rules', '/alert/rules'],
  ['alert-events', '/alert/events'],
  ['system-users', '/system/users'],
  ['system-audit', '/system/audit'],
  ['system-permissions', '/system/permissions'],
];

async function resolveLoopId() {
  const loginResp = await fetch(`${API}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: USER, password: PASS, rememberMe: false }),
  });
  const loginBody = await loginResp.json();
  const token = loginBody?.data?.accessToken;
  if (!token) throw new Error('API 登录失败，无法解析 loopId');
  const loopsResp = await fetch(`${API}/loops?page=1&pageSize=1`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  const loopsBody = await loopsResp.json();
  const loopId = loopsBody?.data?.items?.[0]?.loopId;
  if (!loopId) throw new Error('未获取到回路列表，无法解析 loopId');
  return loopId;
}

async function main() {
  fs.mkdirSync(OUT_DIR, { recursive: true });
  const loopId = await resolveLoopId();
  console.log(`loopId = ${loopId}`);

  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    locale: 'zh-CN',
    timezoneId: 'Asia/Shanghai',
  });
  const page = await ctx.newPage();
  await page.addInitScript(
    ({ DARK: dark, THEME_CACHE_KEY: themeKey }) => {
      localStorage.setItem('clpm-onboarding-completed', 'true');
      if (dark) {
        localStorage.setItem(themeKey, JSON.stringify({ value: 'dark' }));
      }
    },
    { DARK, THEME_CACHE_KEY },
  );

  // 登录（UI 流程，前端自行持久化 token）
  // 登录页：等待表单真正渲染（早期截图曾只抓到启动闪屏）
  await page.goto(`${WEB}/auth/login`, { waitUntil: 'domcontentloaded' });
  await page.getByPlaceholder('请输入用户名').waitFor({ state: 'visible', timeout: 20_000 });
  await page.waitForTimeout(1500);
  await page.screenshot({ path: path.join(OUT_DIR, 'auth-login.png') });
  await page.getByPlaceholder('请输入用户名').fill(USER);
  await page.getByPlaceholder('请输入密码').fill(PASS);
  await page.getByText('登录', { exact: true }).click();
  await page.waitForURL((url) => !url.pathname.includes('/auth/login'), { timeout: 30_000 });
  await page.waitForTimeout(2500);

  let ok = 0;
  const failed = [];
  for (const [name, route] of PAGES) {
    try {
      await page.goto(`${WEB}${route.replace('[LOOP]', loopId)}`, { waitUntil: 'domcontentloaded' });
      await page.waitForTimeout(3200);
      await page.screenshot({ path: path.join(OUT_DIR, `${name}.png`) });
      ok += 1;
      console.log(`ok ${name}`);
    } catch (error) {
      failed.push(name);
      console.log(`FAIL ${name}: ${String(error).split('\n')[0]}`);
    }
  }

  await browser.close();
  console.log(`done: ${ok}/${PAGES.length} → ${OUT_DIR}`);
  if (failed.length > 0) {
    console.log(`failed: ${failed.join(', ')}`);
    process.exitCode = 2;
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
