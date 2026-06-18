import { expect, test } from '@playwright/test';
import { smokeTestStatus } from '../src/data/deliveryStatus';
import { currentBatch, evidencePackage, findings, loopLedgers, reevaluation } from '../src/data/mockData';

const p0Routes = [
  '/',
  '/risk',
  '/todos',
  '/samples',
  '/samples/import',
  '/samples/readiness',
  '/samples/dashboard',
  '/samples/radar',
  '/samples/freeze',
  '/loops',
  '/loops/mapping',
  '/loops/verification',
  '/loops/exclusions',
  '/loops/versions',
  '/performance',
  '/performance/ranking',
  '/performance/lineage',
  '/performance/trends',
  '/diagnosis',
  '/diagnosis/loop/TIC-1115',
  '/closure/review',
  '/closure/multi-review',
  '/closure/implementation',
  '/closure/rollback',
  '/closure/reevaluation',
  '/evidence',
  '/evidence/sample-report',
  '/evidence/export',
  '/sponsor',
  '/delivery',
  '/delivery/acceptance',
  '/system/data-source',
  '/system/rules',
  '/system/safety',
  '/tuning/sample',
];

test.describe('CLPM prototype smoke', () => {
  for (const route of p0Routes) {
    test(`opens ${route}`, async ({ page }) => {
      await page.goto(route);
      await expect(page.getByRole('main')).toBeVisible();
      await expect(page.getByRole('heading').first()).toBeVisible();
    });
  }

  test('has no broken display text on core routes', async ({ page }) => {
    for (const route of p0Routes) {
      await page.goto(route);
      const text = await page.getByRole('main').innerText();
      expect(text).not.toMatch(/NaN|undefined|Invalid Date|\uFFFD/);
      expect(text).not.toMatch(/2026-05/);
    }
  });

  test('does not create page-level horizontal overflow on mobile-critical routes', async ({ page }) => {
    for (const route of ['/', '/samples/radar', '/loops', '/performance/ranking', '/diagnosis/loop/TIC-1115', '/evidence', '/evidence/export', '/system/data-source', '/delivery/acceptance']) {
      await page.goto(route);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 2);
      expect(overflow, `${route} should keep overflow inside panels/tables`).toBe(false);
    }
  });

  test('shows full menu and version labels', async ({ page }) => {
    await page.goto('/');
    for (const label of ['治理总览', '样本验证', '回路台账', '绩效评估', '诊断中心', '闭环治理', '可信整定', '证据报告', '项目交付', '知识资产', '系统管理']) {
      await expect(page.getByText(label, { exact: true })).toBeVisible();
    }
    for (const tag of ['P0', 'P1/P2', 'P2', 'P3']) {
      await expect(page.getByText(tag, { exact: true }).first()).toBeVisible();
    }
    await page.goto('/delivery');
    await expect(page.getByText(/验收清单/)).toBeVisible();
  });

  test('shows sample scale boundary consistently', async ({ page }) => {
    for (const route of ['/samples', '/samples/dashboard', '/samples/radar', '/evidence/sample-report']) {
      await page.goto(route);
      await expect(page.getByText(/当前 24 回路为开发 smoke 数据/).first()).toBeVisible();
      await expect(page.getByText(/50-100\/72 回路口径/).first()).toBeVisible();
    }
  });

  test('walks the P0 evidence chain', async ({ page }) => {
    await page.goto('/');
    const evidenceHref = await page.getByRole('link', { name: /进入证据链/ }).getAttribute('href');
    expect(evidenceHref).toMatch(/\/diagnosis\/loop\/.+/);
    await page.goto(evidenceHref!);
    await expect(page.getByRole('link', { name: /提交建议审核/ })).toHaveAttribute('href', '/closure/review');
    await page.goto('/closure/review');
    await expect(page.getByText(/桌面评审版 \/ 模拟版/)).toBeVisible();
    await expect(page.getByText(/决策样式预览/)).toBeVisible();
    await expect(page.getByRole('button', { name: /通过（模拟）/ })).toHaveAttribute('aria-pressed', 'true');
    await expect(page.getByRole('button', { name: /驳回（模拟）/ })).toHaveAttribute('aria-pressed', 'false');
    await expect(page.getByRole('button', { name: /需补证据（模拟）/ })).toHaveAttribute('aria-pressed', 'false');
    await expect(page.getByRole('link', { name: /查看证据包/ })).toHaveAttribute('href', '/evidence');
    await page.goto('/closure/multi-review');
    await expect(page.getByText(/桌面评审版 \/ 模拟版/)).toBeVisible();
    await page.goto('/closure/implementation');
    await expect(page.getByText(/人工实施记录/)).toBeVisible();
    await expect(page.getByRole('main')).toContainText(/系统不写入/);
    await page.goto('/closure/reevaluation');
    await expect(page.getByText(/不能关闭归档/)).toBeVisible();
    await page.goto('/evidence');
    await expect(page).toHaveURL(/\/evidence/);
    await page.goto('/sponsor');
    await expect(page.getByText(/代表性样例/)).toBeVisible();
  });

  test('opens three distinct evidence chains', async ({ page }) => {
    await page.goto('/performance/ranking');
    await expect(page.getByText(/仅对可评估、可诊断、可整定、需现场核实对象排序/)).toBeVisible();
    await expect(page.getByText(/未参与真实排序对象/)).toBeVisible();
    await expect(page.getByText(/数据不足与不可判定不会被当作真实 0 分/)).toBeVisible();
    const expectations = [
      ['TIC-1115', /PV 围绕 SP 周期振荡/, /控制工程师/],
      ['PIC-1122', /OP 阶梯化变化/, /仪表工程师/],
      ['FIC-1136', /质量码|数据治理|BAD/],
    ] as const;
    for (const [loopId, rule, owner] of expectations) {
      await page.goto(`/diagnosis/loop/${loopId}`);
      await expect(page.getByRole('heading', { name: new RegExp(loopId) })).toBeVisible();
      await expect(page.getByRole('img', { name: new RegExp(`${loopId}.*PV.*SP.*OP`) })).toBeVisible();
      await expect(page.getByText(new RegExp(`${loopId} 趋势摘要`))).toBeVisible();
      await expect(page.getByRole('main')).toContainText(rule);
      if (owner) await expect(page.getByRole('main')).toContainText(owner);
    }
    await page.goto('/diagnosis/loop/UNKNOWN-0000');
    await expect(page.getByText(/未找到该回路证据/)).toBeVisible();
    await expect(page.getByText(/不会伪装为真实证据链/)).toBeVisible();
    await expect(page.getByRole('link', { name: /返回诊断清单/ })).toHaveAttribute('href', '/diagnosis');
  });

  test('exposes safety and evidence boundaries', async ({ page }) => {
    await page.goto('/system/safety');
    await expect(page.getByText(/不直接写 DCS/)).toBeVisible();
    await page.goto('/evidence');
    await expect(page.getByText(/Included refs/)).toBeVisible();
    await expect(page.getByText(/桌面评审版 \/ 模拟版/)).toBeVisible();
    await expect(page.getByText(/查看交付验收/)).toBeVisible();
    await expect(page.getByText('CLPM-DEMO-SECOND-LEVEL-20260616', { exact: true })).toBeVisible();
    await page.goto('/evidence/sample-report');
    await expect(page.getByText(/场景分布/)).toBeVisible();
    await page.goto('/samples/readiness');
    await expect(page.getByText(/批次映射率/)).toBeVisible();
    await expect(page.getByText(/批次好值率/)).toBeVisible();
    await expect(page.getByText(/评审就绪率/)).toBeVisible();
    await expect(page.getByText(/质量可用率/)).toBeVisible();
    await expect(page.getByText(/事件可用性/)).toBeVisible();
    await expect(page.getByText(/partial 原因/)).toBeVisible();
    await expect(page.getByText(/GOOD 进入评价/)).toBeVisible();
    await page.goto('/system/data-source');
    await expect(page.getByRole('main')).toContainText(/control_loop_second_level_24loops_1h.csv/);
    await expect(page.getByText(/关键字段：timestamp/)).toBeVisible();
    await page.goto('/samples/import');
    await expect(page.getByText(/CSV：control_loop_second_level_24loops_1h.csv/)).toBeVisible();
    await expect(page.getByText(/不接真实 DCS、不写 DCS/)).toBeVisible();
    await expect(page.getByText(/样本来源：CSV:/)).toBeVisible();
    await page.goto('/samples/dashboard');
    await expect(page.getByText(/24 回路 \/ 86424 行 \/ 1s 采样/)).toBeVisible();
    await page.goto('/samples/radar');
    for (const state of ['可评估', '可诊断', '可整定', '需现场核实', '数据不足', '不可判定']) {
      await expect(page.getByText(state, { exact: true }).first()).toBeVisible();
    }
    await page.goto('/performance/lineage');
    await expect(page.getByText(/KPI 口径来源/)).toBeVisible();
    await expect(page.getByText(/样本自控率/)).toBeVisible();
    await expect(page.getByText(/采样间隔 1s/)).toBeVisible();
    await expect(page.getByText(/公式、阈值与版本引用/)).toBeVisible();
    await expect(page.getByText('formula.kpi.v0.1', { exact: true })).toBeVisible();
    await expect(page.getByText('threshold.demo.v0.1', { exact: true })).toBeVisible();
    await page.goto('/performance/trends');
    await expect(page.getByText(/趋势分析工作台/)).toBeVisible();
    await expect(page.getByText(/只用于识别证据/)).toBeVisible();
    await expect(page.getByRole('heading', { name: 'PV/SP' })).toBeVisible();
    await expect(page.getByText(/现场实施必须经过人工审核和回退方案/)).toBeVisible();
    await page.goto('/system/rules');
    await expect(page.getByText(/质量码规则/)).toBeVisible();
    await expect(page.getByText(/不写 DCS、不切模式、不主动激励/)).toBeVisible();
    await page.goto('/evidence/export');
    await expect(page.getByText(/JSON Manifest/)).toBeVisible();
    await expect(page.getByText(/不得生成“已完成闭环”结论/)).toBeVisible();
    await page.goto('/tuning/sample');
    await expect(page.getByText(/不代表批量整定/)).toBeVisible();
    await expect(page.getByText(/风险与回退/)).toBeVisible();
    await expect(page.getByText(/授权人员人工恢复/)).toBeVisible();
  });

  test('supports keyboard evidence navigation from ranking table', async ({ page }) => {
    await page.goto('/performance/ranking');
    await page.getByRole('row', { name: /TIC-1115/ }).focus();
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(/\/diagnosis\/loop\/TIC-1115/);
    await expect(page.getByRole('heading', { name: /TIC-1115/ })).toBeVisible();
  });

  test('filters ranking table and keeps selected loop context in sync', async ({ page }) => {
    await page.goto('/performance/ranking');
    await page.getByLabel('风险等级').selectOption('high');
    await expect(page.getByRole('row', { name: /TIC-1115/i })).toBeVisible();
    await expect(page.getByRole('row', { name: /FIC-1101/i })).toHaveCount(0);

    await page.getByRole('checkbox', { name: /选择 TIC-1115/i }).check();
    await expect(page.getByText(/批量已选：1 条/i)).toBeVisible();
  });

  test('updates home workbench context when selecting another priority loop', async ({ page }) => {
    await page.goto('/');
    await page.getByRole('button', { name: /LIC-1143/i }).click();
    await expect(page.getByText(/当前回路：LIC-1143/i)).toBeVisible();
    await expect(page.getByRole('link', { name: /进入证据链/i })).toHaveAttribute('href', '/diagnosis/loop/LIC-1143');
  });

  test('switches sample import method and freezes sample', async ({ page }) => {
    await page.goto('/samples/import');
    await page.getByRole('button', { name: /OPC 只读连接/i }).click();
    await expect(page.getByText(/当前导入方式：OPC 只读连接/i)).toBeVisible();

    await page.goto('/samples/readiness');
    await page.getByRole('button', { name: /冻结样本/i }).click();
    await expect(page.getByText(/当前状态：frozen/i)).toBeVisible();
    await expect(page.getByText(/样本已冻结，字段映射只读/i)).toBeVisible();
  });

  test('keeps display-only loop tables out of empty keyboard actions', async ({ page }) => {
    await page.goto('/loops');
    const rows = page.getByRole('row');
    await expect(rows.nth(1)).not.toHaveAttribute('tabindex', '0');
    await page.goto('/performance/ranking');
    await expect(page.getByRole('row', { name: /TIC-1115/ })).toHaveAttribute('tabindex', '0');
  });

  test('shows an explicit unknown route page', async ({ page }) => {
    await page.goto('/not-a-real-route');
    await expect(page.getByRole('heading', { name: /页面不存在/ })).toBeVisible();
    await expect(page.getByRole('heading', { name: '未知路由' })).toBeVisible();
    await expect(page.getByText(/不会自动回到首页伪装成功/)).toBeVisible();
    await expect(page.getByRole('link', { name: /返回首页/ })).toHaveAttribute('href', '/');
    await expect(page.getByRole('link', { name: /查看交付验收/ })).toHaveAttribute('href', '/delivery/acceptance');
  });

  test('shows sponsor evidence view boundaries', async ({ page }) => {
    await page.goto('/sponsor');
    await expect(page.getByText(/Sponsor verdict/)).toBeVisible();
    await expect(page.getByText(/不能伪装为完整闭环验收/)).toBeVisible();
    await expect(page.getByText(/证据覆盖矩阵/)).toBeVisible();
    await expect(page.getByText(/样本可信/)).toBeVisible();
    await expect(page.getByRole('main')).toContainText(/闭环治理/);
    await expect(page.getByText(/不可证明事项/)).toBeVisible();
    await expect(page.getByText(/不能证明批量整定可自动实施/)).toBeVisible();
  });

  test('shows delivery acceptance package', async ({ page }) => {
    await page.goto('/delivery/acceptance');
    await expect(page.getByText(/验收清单/)).toBeVisible();
    await expect(page.getByText(/构建命令/)).toBeVisible();
    await expect(page.getByRole('main')).toContainText(/npm run build/);
    await expect(page.getByText(/Smoke 命令/)).toBeVisible();
    await expect(page.getByRole('main')).toContainText(/npm run test:smoke/);
    await expect(page.getByText(smokeTestStatus, { exact: true })).toBeVisible();
    await expect(page.getByText(/缺证据不伪装 success/)).toBeVisible();
    await expect(page.getByText(/整改就绪矩阵/)).toBeVisible();
    await expect(page.getByText(/样本口径一致/)).toBeVisible();
    await expect(page.getByText(/页面可维护性/)).toBeVisible();
    await expect(page.getByText('/diagnosis/loop/:loopId')).toBeVisible();
    await page.goto('/sponsor');
    await expect(page.getByText(/查看交付验收/)).toBeVisible();
  });

  test('switches to sponsor role and filters navigation to sponsor routes', async ({ page }) => {
    await page.goto('/');
    await page.getByLabel('当前角色').selectOption('sponsor');
    await expect(page).toHaveURL(/\/sponsor$/);
    await expect(page.getByText('管理首页', { exact: true })).toBeVisible();
    await expect(page.getByText('项目交付', { exact: true })).toBeVisible();
    await expect(page.getByText('样本验证', { exact: true })).toHaveCount(0);
    await expect(page.getByText('实施记录', { exact: true })).toHaveCount(0);
  });

  test('keeps typed contract fields first-class', async () => {
    expect(currentBatch.source).toContain('CSV:');
    expect(loopLedgers.length).toBeGreaterThan(0);
    expect(loopLedgers[0]).toMatchObject({ pvMapping: 'mapped', spMapping: 'mapped', opMapping: 'mapped' });
    expect(loopLedgers.some((ledger) => ledger.modeMapping === 'partial' && ledger.blockingItems.length > 0)).toBe(true);
    expect(findings.find((finding) => finding.loopId === 'TIC-1115')).toMatchObject({ findingType: 'pid_oscillation', severity: 'high', ownerRole: '控制工程师' });
    expect(findings.find((finding) => finding.loopId === 'PIC-1122')).toMatchObject({ findingType: 'valve_instrument', ownerRole: '仪表工程师' });
    expect(findings.find((finding) => finding.loopId === 'FIC-1136')).toMatchObject({ findingType: 'data_condition', ownerRole: '数据治理' });
    expect(evidencePackage).toMatchObject({ packageStatus: 'PACKAGE_PARTIAL', validityStatus: 'VALID_WITH_MISSING_REFS' });
    expect(evidencePackage.manifestHash).toMatch(/^sha256:/);
    expect(evidencePackage.includedRefs.some((ref) => ref.name === 'InstrumentCheckRecord' && ref.status === '缺失')).toBe(true);
    expect(evidencePackage.riskSummary.length).toBeGreaterThanOrEqual(3);
    expect(reevaluation.beforeWindow).toContain('2026-06-16');
    expect(reevaluation.afterWindow).toContain('待现场观察记录');
    expect(reevaluation.status).toBe('partial');
  });

  test('shows ledger governance and partial evidence states', async ({ page }) => {
    await page.goto('/loops/mapping');
    await expect(page.getByText(/字段映射矩阵/)).toBeVisible();
    await expect(page.getByText(/LoopLedger.modeTag/)).toBeVisible();
    await expect(page.getByText(/LoopLedger 契约样例/)).toBeVisible();
    await expect(page.getByText(/ledger.freeze.v0.1/).first()).toBeVisible();
    await expect(page.getByText(/缺失项/)).toBeVisible();
    await expect(page.getByText(/valve_position/)).toBeVisible();
    await page.goto('/loops/verification');
    await expect(page.getByText(/人工修正记录/)).toBeVisible();
    await expect(page.getByText(/冻结前阻断项/)).toBeVisible();
    await expect(page.getByRole('article').filter({ hasText: '冻结前阻断项' }).getByText('需补证据')).toBeVisible();
    await page.goto('/loops/exclusions');
    await expect(page.getByText(/有效窗口/)).toBeVisible();
    await expect(page.getByText(/审批状态/)).toBeVisible();
    await expect(page.getByText(/不计入有效自控率/)).toBeVisible();
    await page.goto('/loops/versions');
    await expect(page.getByText('ledger.freeze.v0.1', { exact: true })).toBeVisible();
    await expect(page.getByRole('cell', { name: 'formula' })).toBeVisible();
    await expect(page.getByRole('cell', { name: 'threshold' })).toBeVisible();
    await expect(page.getByRole('cell', { name: 'quality rule' })).toBeVisible();
    await expect(page.getByRole('cell', { name: 'mode mapping' })).toBeVisible();
    await page.goto('/samples/freeze');
    await expect(page.getByText(/Manifest Hash/)).toBeVisible();
    await expect(page.getByText(/冻结条件/)).toBeVisible();
    await expect(page.getByText(/冻结后不可漂移/)).toBeVisible();
    await expect(page.getByText(/InstrumentCheckRecord 缺失/)).toBeVisible();
    await page.goto('/closure/implementation');
    await expect(page.getByText(/桌面评审版 \/ 模拟版/)).toBeVisible();
    await expect(page.getByText(/不能标记为已完成实施/)).toBeVisible();
    await expect(page.getByRole('link', { name: /进入观察复评/ })).toHaveAttribute('href', '/closure/reevaluation');
    await page.goto('/closure/rollback');
    await expect(page.getByRole('heading', { name: '观察要求' })).toBeVisible();
    await expect(page.getByText(/InstrumentCheckRecord 与 PostImplementationObservation/)).toBeVisible();
    await page.goto('/closure/reevaluation');
    await expect(page.getByText(/桌面评审版 \/ 模拟版/)).toBeVisible();
    await expect(page.getByText(/Reevaluation 契约/)).toBeVisible();
    await expect(page.getByText(/前窗口/)).toBeVisible();
    await expect(page.getByText(/后窗口/)).toBeVisible();
    await expect(page.getByText(/EvidencePackage 保持 partial/)).toBeVisible();
    await page.goto('/evidence');
    await expect(page.getByText(/Included refs/)).toBeVisible();
    await expect(page.getByRole('main')).toContainText(/PACKAGE_PARTIAL/);
    await expect(page.getByText(/VALID_WITH_MISSING_REFS/)).toBeVisible();
    await expect(page.getByText('manifest-first：证据包引用对象与版本，不用截图堆叠替代审计链。')).toBeVisible();
    await expect(page.getByText(/不能证明批量整定可自动实施/)).toBeVisible();
    await expect(page.getByText(/sha256:demo/)).toBeVisible();
    await expect(page.getByText(/闭环审核存在需补证据项/)).toBeVisible();
  });
});
