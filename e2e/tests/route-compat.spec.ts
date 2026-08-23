/**
 * E2E 旧路由兼容性基线（V62-P0-037 + IA 重构 Phase A + Phase D 单页整合）
 *
 * 覆盖旧路由 redirect：
 * - 整定三页式现行路由防白屏基线（/tuning/workbench|records|verification）
 *   注：原 18 个 /tuning/{model,algorithm,simulation,flow/*} → /tuning/detail
 *   的 legacy redirect 用例已于 2026-08-23 删除——现行 tuning.ts 为三页式 IA，
 *   从未存在这些 redirect（/tuning/detail 仅存在于已屏蔽的旧提交），属旧 IA 遗物。
 *   改为对现行三页路由做等价的防白屏/刷新/前进后退基线验证。
 * - /diagnosis/records   → MVP 两页式直链页（原 /diagnosis/tasks 已下线，无 redirect）
 * - IA Phase A 配置集中化迁移：
 *   /loop/aas-sync       → /config/link
 *   /metric/config       → /config/metric
 *   /diagnosis/config    → /config/diagnosis
 *   /system/pid-template → /config/link
 *   /loop/data           → /config/datasource
 * - IA v2.9：
 *   /loop/workbench      → /monitor/loop-workbench
 *   /loop/detail/:id     → /monitor/loop-workbench?loopId=:id
 *   /alert/events        → /monitor/alerts
 *   /alert/rules         → /config/alert-rules
 *
 * 验证维度（tuning/diagnosis 每路由 3 个用例，config 每路由 1 个直链用例）：
 * - 直链访问旧路由 → URL 正确 redirect 到新路由，页面不白屏
 * - 硬刷新（page.reload）后 URL 保持，页面不白屏
 * - 前进后退导航正常（历史栈未断裂）
 *
 * 依据：UI/UX v6.1「稳定元素根」防白屏（vben v-show + Transition + KeepAlive）
 *       P1-020 旧路由 redirect + hideInMenu 兼容书签
 *       IA 重构 Phase A §3.3 配置集中化（config.ts legacy redirect 段）
 *       整定三页式（09 设计方案 §6.1，tuning.ts）
 */
import { test, expect } from '../fixtures/auth.js';

/**
 * 整定模块现行三页式路由（09 设计方案 §6.1：工作台/记录/效果验证）。
 * 旧 /tuning/{model,algorithm,simulation,flow/*} 路由在现行代码中不存在
 * （无 legacy redirect 段），原 18 个 redirect 用例属旧 IA 遗物已删除，
 * 此处改为对现行路由做等价的防白屏基线验证。
 */
const TUNING_CURRENT_ROUTES: Array<{ path: string; target: RegExp }> = [
  { path: '/tuning/workbench', target: /\/tuning\/workbench/ },
  { path: '/tuning/records', target: /\/tuning\/records/ },
  { path: '/tuning/verification', target: /\/tuning\/verification/ },
];

/** 诊断模块旧路由兼容（MVP 两页式：/diagnosis/tasks 已下线，
 * /diagnosis/records 为现存直链页，断言 URL 保持且页面可渲染） */
const DIAGNOSIS_LEGACY_ROUTES: Array<{ legacy: string; target: RegExp }> = [
  { legacy: '/diagnosis/records', target: /\/diagnosis\/records/ },
];

const IA_V29_LEGACY_ROUTES: Array<{ legacy: string; target: RegExp }> = [
  { legacy: '/loop', target: /\/monitor\/loop-workbench/ },
  { legacy: '/loop/workbench', target: /\/monitor\/loop-workbench/ },
  {
    legacy: '/loop/detail/00000000-0000-0000-0000-000000000201',
    target: /\/monitor\/loop-workbench\?loopId=/,
  },
  // 面点分离（monitor.ts）：旧 /loop/monitor 重定向到独立回路列表页，query 透传
  {
    legacy: '/loop/monitor',
    target: /\/monitor\/loops/,
  },
  { legacy: '/alert/events', target: /\/monitor\/alerts/ },
  { legacy: '/alert/rules', target: /\/config\/alert-rules/ },
];

test.describe('回路整定三页式路由防白屏基线（09 方案）', () => {
  test.beforeEach(async ({ loginAs }) => {
    // 整定模块需要 ADMIN / IC_ENGINEER / EXPERT 权限
    await loginAs('ADMIN');
  });

  for (const { path, target } of TUNING_CURRENT_ROUTES) {
    test(`E2E-ROUTE-TUNE: ${path} 直链访问不白屏`, async ({ page }) => {
      await page.goto(path, { waitUntil: 'domcontentloaded' });
      await expect(page).toHaveURL(target, { timeout: 15_000 });
      // 防白屏：body 必须有非空可见内容
      await expect(page.locator('body')).not.toBeEmpty();
      const text = await page.locator('body').innerText();
      expect(text.trim().length).toBeGreaterThan(0);
    });

    test(`E2E-ROUTE-TUNE: ${path} 硬刷新后不白屏`, async ({ page }) => {
      await page.goto(path, { waitUntil: 'domcontentloaded' });
      await expect(page).toHaveURL(target, { timeout: 15_000 });
      // 硬刷新：模拟用户按 F5 / 点击刷新按钮
      await page.reload({ waitUntil: 'domcontentloaded' });
      await expect(page).toHaveURL(target, { timeout: 15_000 });
      await expect(page.locator('body')).not.toBeEmpty();
      const text = await page.locator('body').innerText();
      expect(text.trim().length).toBeGreaterThan(0);
    });

    test(`E2E-ROUTE-TUNE: ${path} 前进后退导航正常`, async ({ page }) => {
      // 先建立历史栈：访问 records → 访问目标路由 → 回退 → 前进
      // （目标本身为 records 时改用 workbench 作为对照页）
      const other =
        path === '/tuning/records' ? '/tuning/workbench' : '/tuning/records';
      await page.goto(other, { waitUntil: 'domcontentloaded' });
      await page.goto(path, { waitUntil: 'domcontentloaded' });
      await expect(page).toHaveURL(target, { timeout: 15_000 });

      // 回退到对照页
      await page.goBack({ waitUntil: 'domcontentloaded' });
      await expect(page).toHaveURL(new RegExp(other.replace(/\//g, '\\/')), {
        timeout: 15_000,
      });

      // 前进回目标路由
      await page.goForward({ waitUntil: 'domcontentloaded' });
      await expect(page).toHaveURL(target, { timeout: 15_000 });
      await expect(page.locator('body')).not.toBeEmpty();
    });
  }
});

test.describe('旧路由兼容 - 诊断中心（V62-P0-037）', () => {
  test.beforeEach(async ({ loginAs }) => {
    // 诊断任务页 IC_ENGINEER 可访问
    await loginAs('IC_ENGINEER');
  });

  for (const { legacy, target } of DIAGNOSIS_LEGACY_ROUTES) {
    test(`E2E-ROUTE-DIAG: ${legacy} 直链 redirect 不白屏`, async ({ page }) => {
      await page.goto(legacy, { waitUntil: 'domcontentloaded' });
      await expect(page).toHaveURL(target, { timeout: 15_000 });
      await expect(page.locator('body')).not.toBeEmpty();
      const text = await page.locator('body').innerText();
      expect(text.trim().length).toBeGreaterThan(0);
    });

    test(`E2E-ROUTE-DIAG: ${legacy} 硬刷新后不白屏`, async ({ page }) => {
      await page.goto(legacy, { waitUntil: 'domcontentloaded' });
      await expect(page).toHaveURL(target, { timeout: 15_000 });
      await page.reload({ waitUntil: 'domcontentloaded' });
      await expect(page).toHaveURL(target, { timeout: 15_000 });
      await expect(page.locator('body')).not.toBeEmpty();
      const text = await page.locator('body').innerText();
      expect(text.trim().length).toBeGreaterThan(0);
    });

    test(`E2E-ROUTE-DIAG: ${legacy} 前进后退导航正常`, async ({ page }) => {
      // 先建立历史栈：访问 workbench → 访问旧路由 → 回退 → 前进
      await page.goto('/diagnosis/workbench', { waitUntil: 'domcontentloaded' });
      await page.goto(legacy, { waitUntil: 'domcontentloaded' });
      await expect(page).toHaveURL(target, { timeout: 15_000 });

      // 回退到 workbench
      await page.goBack({ waitUntil: 'domcontentloaded' });
      await expect(page).toHaveURL(/\/diagnosis\/workbench/, { timeout: 15_000 });

      // 前进回新路由
      await page.goForward({ waitUntil: 'domcontentloaded' });
      await expect(page).toHaveURL(target, { timeout: 15_000 });
      await expect(page.locator('body')).not.toBeEmpty();
    });
  }
});

/**
 * IA 重构 Phase A 配置集中化迁移的 legacy redirect。
 * 旧路径 → /config/* 新路径，对齐 config.ts legacy redirect 段。
 */
const CONFIG_LEGACY_ROUTES: Array<{ legacy: string; target: RegExp }> = [
  { legacy: '/loop/aas-sync', target: /\/config\/link/ },
  { legacy: '/metric/config', target: /\/config\/metric/ },
  { legacy: '/diagnosis/config', target: /\/config\/diagnosis/ },
  { legacy: '/system/pid-template', target: /\/config\/link/ },
  { legacy: '/loop/data', target: /\/config\/datasource/ },
];

test.describe('旧路由兼容 - 配置集中化迁移（IA 重构 Phase A）', () => {
  test.beforeEach(async ({ loginAs }) => {
    // 配置模块仅 ADMIN 可访问
    await loginAs('ADMIN');
  });

  for (const { legacy, target } of CONFIG_LEGACY_ROUTES) {
    test(`E2E-ROUTE-CONFIG: ${legacy} 直链 redirect 不白屏`, async ({ page }) => {
      await page.goto(legacy, { waitUntil: 'domcontentloaded' });
      await expect(page).toHaveURL(target, { timeout: 15_000 });
      // 防白屏：body 必须有非空可见内容
      await expect(page.locator('body')).not.toBeEmpty();
      const text = await page.locator('body').innerText();
      expect(text.trim().length).toBeGreaterThan(0);
    });
  }
});

test.describe('旧路由兼容 - 监控/工作台/预警 IA 收敛（v2.9）', () => {
  test.beforeEach(async ({ loginAs }) => {
    await loginAs('ADMIN');
  });

  for (const { legacy, target } of IA_V29_LEGACY_ROUTES) {
    test(`E2E-ROUTE-IA29: ${legacy} redirect 不白屏`, async ({ page }) => {
      await page.goto(legacy, { waitUntil: 'domcontentloaded' });
      await expect(page).toHaveURL(target, { timeout: 15_000 });
      await expect(page.locator('body')).not.toBeEmpty();
    });
  }
});

/**
 * 处置五段式五入口可访问性（批次 C：路由别名 + meta 预设）
 *
 * - /handling/suggestions  诊断建议（预设 Tab=建议审核）
 * - /handling/tasks        处置任务（预设工单状态 PENDING,REOPENED）
 * - /handling/orders       处置工单（预设工单状态 EXECUTING,VERIFYING）
 * - /handling/workbench    旧入口 redirect（按 tab query 分流，缺省 → suggestions）
 */
const HANDLING_V2_ROUTES: Array<{ path: string; target: RegExp }> = [
  { path: '/handling/suggestions', target: /\/handling\/suggestions/ },
  { path: '/handling/tasks', target: /\/handling\/tasks/ },
  { path: '/handling/orders', target: /\/handling\/orders/ },
  { path: '/handling/workbench', target: /\/handling\/suggestions/ },
  { path: '/handling', target: /\/handling\/suggestions/ },
];

test.describe('处置五段式 - 五入口可访问兼容（批次 C）', () => {
  test.beforeEach(async ({ loginAs }) => {
    await loginAs('ADMIN');
  });

  for (const { path, target } of HANDLING_V2_ROUTES) {
    test(`E2E-ROUTE-HANDLING: ${path} 可访问不白屏`, async ({ page }) => {
      await page.goto(path, { waitUntil: 'domcontentloaded' });
      await expect(page).toHaveURL(target, { timeout: 15_000 });
      await expect(page.locator('body')).not.toBeEmpty();
      const text = await page.locator('body').innerText();
      expect(text.trim().length).toBeGreaterThan(0);
    });
  }

  test('E2E-ROUTE-HANDLING: 旧 workbench 按 tab query 分流且透传 focus', async ({
    page,
  }) => {
    await page.goto('/handling/workbench?tab=orders&focus=abc', {
      waitUntil: 'domcontentloaded',
    });
    await expect(page).toHaveURL(/\/handling\/orders\?.*focus=abc/, {
      timeout: 15_000,
    });
  });
});

/**
 * 回路工作台内嵌趋势图区（2026-08-23 对齐 Grid 布局重构）
 *
 * 背景：概览区"历史"按钮曾跳转不存在的 /loop/history（必 404），整改 B1
 * 一度改为概览区"趋势"按钮 + LoopTrendModal 弹窗；此后工作台重构为统一
 * CSS Grid 布局（workbench.vue：左脊柱通高 + 上部(趋势+决策) + 下部(4卡片)），
 * 趋势改为选中回路后的内嵌主画布图区（.wb-r4：过程趋势/KPI 历史双模式），
 * 概览区按钮整体下线。原"趋势按钮/弹窗"两个用例据此重写为内嵌图区等价断言。
 */
test.describe('回路工作台内嵌趋势图区（Grid 布局重构后）', () => {
  test.beforeEach(async ({ loginAs }) => {
    await loginAs('ADMIN');
  });

  test('E2E-ROUTE-WB: 概览区趋势/历史按钮已下线，趋势为内嵌图区', async ({
    page,
  }) => {
    await page.goto('/monitor/loop-workbench', { waitUntil: 'domcontentloaded' });
    // 概览区不再有"趋势/历史"按钮（antd 双汉字按钮可访问名带空格，正则兼容）
    await expect(
      page.getByRole('button', { name: /趋\s*势/ }),
    ).toHaveCount(0);
    await expect(
      page.getByRole('button', { name: /历\s*史/ }),
    ).toHaveCount(0);

    // 选中左脊柱第一个回路 → 内嵌趋势图区（.wb-r4）渲染
    const firstLoopItem = page.locator('.wb-loop-item').first();
    await expect(firstLoopItem).toBeVisible({ timeout: 20_000 });
    await firstLoopItem.click();
    await expect(page.locator('section.wb-r4')).toBeVisible({
      timeout: 20_000,
    });
  });

  test('E2E-ROUTE-WB: 内嵌趋势图区渲染图表且不跳路由', async ({ page }) => {
    await page.goto('/monitor/loop-workbench', { waitUntil: 'domcontentloaded' });
    const firstLoopItem = page.locator('.wb-loop-item').first();
    await expect(firstLoopItem).toBeVisible({ timeout: 20_000 });
    await firstLoopItem.click();

    // 图区容器与图表画布渲染（过程趋势默认模式：WorkbenchProcessTrend）
    await expect(page.locator('section.wb-r4')).toBeVisible({ timeout: 20_000 });
    await expect(
      page.locator('.wb-r4__chart').first(),
    ).toBeVisible({ timeout: 20_000 });
    await expect(page.locator('.wb-r4 canvas').first()).toBeVisible({
      timeout: 20_000,
    });

    // 不跳转路由：URL 仍停留在工作台
    await expect(page).toHaveURL(/\/monitor\/loop-workbench/);
  });
});
