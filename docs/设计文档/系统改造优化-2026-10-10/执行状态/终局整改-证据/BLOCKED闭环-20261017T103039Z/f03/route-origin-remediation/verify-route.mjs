/** Real browser URL-contract fixture. No CLPM API, product data, or route mocks. */
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFileSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = dirname(fileURLToPath(import.meta.url));
const candidate = '/Users/zhangping/DEV/clpm-remediation-20261017';
const require = createRequire(join(candidate, 'e2e/package.json'));
const { chromium } = require('@playwright/test');
const ts = require('typescript');
const before = process.argv.includes('--before');
const source = readFileSync(before ? join(root, 'spec.before.ts') : join(candidate, 'e2e/tests/critical-business.spec.ts'), 'utf8');
const ast = ts.createSourceFile('critical-business.spec.ts', source, ts.ScriptTarget.ES2022, true);
const names = ['applicationRoute', 'applicationRouteUrl', 'isAuthenticatedLanding', 'gotoApplicationRoute'];
const declarations = ast.statements.filter((statement) => ts.isFunctionDeclaration(statement) && names.includes(statement.name?.text));
assert.equal(declarations.length, names.length);
const script = declarations.map((statement) => statement.getText(ast)).join('\n');
const emitted = ts.transpile(`${script}\nexport { ${names.join(', ')} };`, {
  target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022,
});
const helpers = await import(`data:text/javascript;base64,${Buffer.from(emitted).toString('base64')}`);
const legacy = process.argv.includes('--legacy');
const result = { before, fixtureOnly: true, productApiUsed: false, mode: legacy ? 'old-predicate' : 'actual-source-helpers', checks: [] };
const server = createServer((request, response) => {
  const mode = /route-mode=hash/.test(request.headers.cookie ?? '') ? 'hash' : 'history';
  response.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
  response.end(`<!doctype html><body><script>
    const mode = ${JSON.stringify(mode)};
    function render() {
      const route = mode === 'hash'
        ? new URL(location.hash.startsWith('#/') ? location.hash.slice(1) : '/auth/login', location.origin)
        : new URL(location.href);
      document.body.dataset.route = route.pathname;
      document.body.textContent = JSON.stringify({path: route.pathname, query: [...route.searchParams]});
    }
    addEventListener('hashchange', render); addEventListener('popstate', render); render();
  </script></body>`);
});
await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
let browser;
let rc = 0;
try {
  browser = await chromium.launch({ headless: true });
  for (const mode of legacy ? ['hash'] : ['hash', 'history']) {
    const context = await browser.newContext({ baseURL: origin });
    await context.addCookies([{ name: 'route-mode', value: mode, url: origin }]);
    const page = await context.newPage();
    await page.goto(`${origin}/auth/login${mode === 'hash' ? '#/auth/login' : ''}`);
    await page.evaluate((mode) => {
      if (mode === 'hash') location.hash = '/cockpit';
      else { history.pushState({}, '', '/cockpit'); dispatchEvent(new Event('popstate')); }
    }, mode);
    await page.waitForFunction(() => document.body.dataset.route === '/cockpit');
    if (legacy) {
      result.checks.push({ mode, url: page.url(), oldPredicate: !new URL(page.url()).pathname.includes('/auth/login'), actualLogicalRoute: await page.locator('body').getAttribute('data-route') });
      await page.waitForURL((url) => !url.pathname.includes('/auth/login'), { timeout: 350 });
    } else {
      await page.waitForURL((url) => helpers.isAuthenticatedLanding(url, origin), { timeout: 1000 });
      result.checks.push({ mode, loginLanding: page.url(), accepted: true });
      const targets = [
        '/config/loop',
        '/loop/workbench360?loopId=11111111-2222-4333-8444-555555555555',
        '/metric/loop-evaluation?view=history&taskId=aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee',
      ];
      for (const target of targets) {
        await helpers.gotoApplicationRoute(page, target);
        const expected = new URL(target, origin);
        await page.waitForFunction((path) => document.body.dataset.route === path, expected.pathname);
        const rendered = JSON.parse(await page.locator('body').innerText());
        assert.deepEqual(rendered, { path: expected.pathname, query: [...expected.searchParams] });
        result.checks.push({ mode, target, url: page.url(), rendered });
      }
      await context.close();
    }
  }
  if (!legacy) {
    for (const path of ['/auth/login#/auth/login', '/auth/login#arbitrary', '/auth/login#/arbitrary', '/auth/login#/auth/login?next=%2Fcockpit']) {
      assert.equal(helpers.isAuthenticatedLanding(new URL(path, origin), origin), false);
      result.checks.push({ rejected: path });
    }
    assert.equal(helpers.isAuthenticatedLanding(new URL('http://example.invalid/cockpit'), origin), false);
    result.checks.push({ rejected: 'different-origin/cockpit' });
    const invalidContext = await browser.newContext({ baseURL: origin });
    await invalidContext.addCookies([{ name: 'route-mode', value: 'hash', url: origin }]);
    const invalidPage = await invalidContext.newPage();
    for (const raw of ["//foreign.invalid/cockpit", "//__HOST__/cockpit", "/\\foreign.invalid/cockpit", "/\\__HOST__/cockpit", "/config/../cockpit", "/config/%2e%2e/cockpit", "arbitrary"]) {
      const fragment = raw.replace('__HOST__', new URL(origin).host);
      await invalidPage.goto(`${origin}/auth/login#${fragment}`);
      const accepted = helpers.isAuthenticatedLanding(new URL(invalidPage.url()), origin);
      result.checks.push({ authorityHash: fragment, actualBrowserUrl: invalidPage.url(), accepted });
      assert.equal(accepted, false, `invalid hash ${fragment} must not be an authenticated landing`);
    }
    await invalidPage.goto(`${origin}/cockpit#arbitrary`);
    assert.equal(helpers.isAuthenticatedLanding(new URL(invalidPage.url()), origin), false);
    result.checks.push({ rejected: '/cockpit#arbitrary' });
    await invalidContext.close();
    for (const raw of ["//foreign.invalid/config/loop", "//__HOST__/config/loop", "/\\foreign.invalid/config/loop", "http://foreign.invalid/config/loop", "/config/other/../loop", "/cockpit"]) {
      const target = raw.replace('__HOST__', new URL(origin).host);
      assert.throws(() => helpers.applicationRouteUrl(new URL(`${origin}/auth/login#/cockpit`), target));
      result.checks.push({ rejectedTarget: target });
    }

  }
} catch (error) {
  rc = 1;
  result.error = { name: error.name, message: error.message, stack: error.stack };
  console.error(error);
} finally {
  await browser?.close();
  await new Promise((resolve) => server.close(resolve));
  result.rc = rc;
  writeFileSync(join(root, before ? 'before-browser.json' : (legacy ? 'legacy-browser.json' : 'fixed-browser.json')), JSON.stringify(result, null, 2) + '\n');
  console.log(JSON.stringify(result));
  process.exitCode = rc;
}
