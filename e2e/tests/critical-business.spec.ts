/** P7-02 real services only. No route mocks or conditional business assertions.
 * Inputs come from prepare-critical-fixture.py in disposable CI services. No
 * product results are seeded; CALC submits the actual selected-loop task.
 */
import { readFileSync } from 'node:fs';
import { test, expect, type APIRequestContext, type Page } from '@playwright/test';

interface InputFixture {
  schemaVersion: number;
  synthetic: boolean;
  inputOnly: boolean;
  environment: string;
  candidateSha: string;
  loopId: string;
  loopTagName: string;
  windowStart: string;
  windowEnd: string;
}

const api = (process.env.E2E_API_BASE_URL ?? 'http://localhost:17101/api/v1').replace(/\/$/, '');
let fixture: InputFixture;
let taskId: string;
let snapshot: Record<string, any>;
const tokens = new Map<string, string>();

function applicationRoute(url: URL): URL {
  if (!url.hash) return url;
  const path = url.hash.slice(1).split(/[?#]/, 1)[0];
  if (!url.hash.startsWith('#/') || path.startsWith('//') || url.hash.includes('\\')) {
    throw new Error('Invalid application hash route');
  }
  const route = new URL(url.hash.slice(1), url.origin);
  if (route.origin !== url.origin || route.pathname !== path) {
    throw new Error('Application hash route must preserve its local path');
  }
  return route;
}

function applicationRouteUrl(current: URL, path: string): string {
  if (!path.startsWith('/') || path.startsWith('//') || path.includes('\\')) {
    throw new Error('Application target must be a local absolute path');
  }
  const route = new URL(path, current.origin);
  if (route.origin !== current.origin || route.hash ||
    route.pathname !== path.split(/[?#]/, 1)[0] ||
    !['/config/loop', '/loop/workbench360', '/metric/loop-evaluation'].includes(route.pathname)) {
    throw new Error('Unexpected critical E2E target');
  }
  if (!current.hash) return route.toString();
  applicationRoute(current);
  const target = new URL(current);
  target.hash = `${route.pathname}${route.search}${route.hash}`;
  return target.toString();
}

function isAuthenticatedLanding(url: URL, origin: string): boolean {
  try {
    const route = applicationRoute(url);
    return url.origin === origin && route.origin === origin &&
      (route.pathname === '/cockpit' || route.pathname.startsWith('/cockpit/'));
  } catch {
    return false;
  }
}

async function gotoApplicationRoute(page: Page, path: string) {
  await page.goto(applicationRouteUrl(new URL(page.url()), path));
}

async function token(request: APIRequestContext, username = 'admin'): Promise<string> {
  const cached = tokens.get(username);
  if (cached) return cached;
  const response = await request.post(`${api}/auth/login`, {
    data: { username, password: 'admin123' },
  });
  expect(response.status()).toBe(200);
  const body = await response.json();
  expect(body.code).toBe('0');
  expect(body.data.user.username).toBe(username);
  expect(body.data.accessToken).toEqual(expect.any(String));
  tokens.set(username, body.data.accessToken);
  return body.data.accessToken;
}

async function read(request: APIRequestContext, path: string) {
  const response = await request.get(`${api}${path}`, {
    headers: { Authorization: `Bearer ${await token(request)}` },
  });
  expect(response.status(), path).toBe(200);
  const body = await response.json();
  expect(body.code, path).toBe('0');
  return body.data;
}

async function login(page: Page, username: string) {
  await page.addInitScript(() => localStorage.setItem('clpm-onboarding-completed', 'true'));
  await page.goto('/');
  await page.getByPlaceholder('请输入用户名').fill(username);
  await page.getByPlaceholder('请输入密码').fill('admin123');
  const responsePromise = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === '/api/v1/auth/login' &&
      response.request().method() === 'POST',
  );
  const origin = new URL(page.url()).origin;
  await page.getByText('登录', { exact: true }).click();
  const response = await responsePromise;
  expect(response.status()).toBe(200);
  expect((await response.json()).data.user.username).toBe(username);
  // The isolated admin/sponsor seeds both land at /cockpit. Hash fragments
  // carry the actual route in the production build; arbitrary hashes cannot pass.
  await page.waitForURL((url) => isAuthenticatedLanding(url, origin));
  await expect(page.locator('body')).not.toContainText('访问被拒绝');
}

test.describe.configure({ mode: 'serial' });
test.beforeAll(() => {
  const local = process.env.E2E_EXECUTION_ENV === 'disposable-local';
  expect(['disposable-github-ci', 'disposable-local']).toContain(
    process.env.E2E_EXECUTION_ENV ?? 'disposable-github-ci',
  );
  if (local) {
    expect(process.env.GITHUB_ACTIONS).not.toBe('true');
    expect(process.env.POSTGRES_DB).toBe('clpm_critical_20261017_local');
    expect(process.env.TDENGINE_DB).toBe('clpm_critical_20261017_local_ts');
    expect(process.env.REDIS_PORT).toBe('17124');
    expect(process.env.POSTGRES_PORT).toBe('17102');
    expect(process.env.TDENGINE_PORT).toBe('17104');
    for (const key of ['POSTGRES_HOST', 'REDIS_HOST', 'TDENGINE_HOST']) {
      expect(process.env[key], key).toBe('127.0.0.1');
    }
    expect(api).toBe('http://127.0.0.1:17261/api/v1');
    expect(process.env.E2E_BASE_URL).toBe('http://127.0.0.1:17266');
  } else {
    expect(process.env.GITHUB_ACTIONS, 'business gate only writes in disposable CI').toBe('true');
    expect(process.env.POSTGRES_DB).toBe('clpm_critical_e2e');
    expect(process.env.TDENGINE_DB).toBe('clpm_critical_e2e_ts');
  }
  const path = process.env.E2E_CRITICAL_FIXTURE;
  expect(path, 'required synthetic-input manifest from isolated CI preparation').toBeTruthy();
  fixture = JSON.parse(readFileSync(path!, 'utf8'));
  expect(fixture.schemaVersion).toBe(1);
  expect(fixture.synthetic).toBe(true);
  expect(fixture.inputOnly).toBe(true);
  expect(fixture.candidateSha).toMatch(/^[0-9a-f]{40}$/);
  expect(fixture.environment).toBe(local ? 'disposable-local' : 'disposable-github-ci');
  expect(fixture.loopTagName).toMatch(local ? /^CRITICAL-LOCAL-\d+-\d+-FLOW001$/ : /^CRITICAL-E2E-\d+-\d+-FLOW001$/);
  expect(fixture.candidateSha).toBe(local ? process.env.CLPM_BUILD_CODE_SHA : process.env.GITHUB_SHA);
});

test('CRITICAL-AUTH-001: real login and authenticated identity', async ({ page }) => {
  await login(page, 'admin');
  const responsePromise = page.waitForResponse(
    (response) => response.url().includes('/api/v1/loops') && response.request().method() === 'GET',
  );
  await gotoApplicationRoute(page, '/config/loop');
  const response = await responsePromise;
  expect(response.status()).toBe(200);
  await expect(page.locator('.ant-table').first()).toBeVisible();
  expect((await response.json()).code).toBe('0');
});

test('CRITICAL-PERM-001: sponsor cannot run calculation or open workbench', async ({
  page,
  request,
  browser,
  baseURL,
}, info) => {
  const headers = { Authorization: `Bearer ${await token(request, 'sponsor')}` };
  const me = await request.get(`${api}/auth/me`, { headers });
  expect(me.status()).toBe(200);
  const identity = await me.json();
  expect(identity.code).toBe('0');
  expect(identity.data.username).toBe('sponsor');
  expect(identity.data.role).toBe('SPONSOR');
  expect(Array.isArray(identity.data.pageCodes)).toBe(true);
  expect(Array.isArray(identity.data.permissions)).toBe(true);
  expect(identity.data.pageCodes).not.toContain('page:workbench.main');
  expect(identity.data.permissions).not.toContain('metric:task-run');
  await info.attach('sponsor-identity.json', {
    body: JSON.stringify(identity), contentType: 'application/json',
  });
  const response = await request.post(`${api}/tasks/custom/evaluate`, {
    headers,
    data: {
      loopIds: [fixture.loopId],
      metrics: ['GOOD_VALUE_RATE'],
      tsStart: fixture.windowStart,
      tsEnd: fixture.windowEnd,
    },
  });
  expect(response.status()).toBe(403);
  const denied = await response.json();
  expect(denied.code).toBe('ERR_PERMISSION_DENIED');
  expect(denied.data).toBeNull();
  await info.attach('sponsor-write-denied.json', {
    body: JSON.stringify(denied), contentType: 'application/json',
  });
  await login(page, 'sponsor');
  const target = `/loop/workbench360?loopId=${fixture.loopId}`;
  await gotoApplicationRoute(page, target);
  await expect.poll(() => {
    const route = applicationRoute(new URL(page.url()));
    return `${route.pathname}${route.search}`;
  }).toBe(target);
  // Page-code filtering removes this route AND its known-path pattern. The
  // current router therefore renders its exact 404 fallback for this denial.
  await expect(page.getByText('哎呀！未找到页面', { exact: true })).toBeVisible();
  await expect(page.getByText('抱歉，我们无法找到您要找的页面。', { exact: true })).toBeVisible();
  await expect(page.locator('.wb360-adapter')).toHaveCount(0);
  // An independent authenticated context proves the target actually exists.
  // Reusing the sponsor context would preserve its localStorage credentials.
  const adminContext = await browser.newContext({ baseURL });
  try {
    const adminPage = await adminContext.newPage();
    await login(adminPage, 'admin');
    await gotoApplicationRoute(adminPage, target);
    await expect.poll(() => {
      const route = applicationRoute(new URL(adminPage.url()));
      return `${route.pathname}${route.search}`;
    }).toBe(target);
    await expect(adminPage.locator('.wb360-adapter')).toBeVisible();
    await expect(adminPage.locator('.wb360-adapter-state')).toHaveCount(0);
    await expect(adminPage.getByText('哎呀！未找到页面', { exact: true })).toHaveCount(0);
  } finally {
    await adminContext.close();
  }
});

test('CRITICAL-CALC-001: real task produces one successful synthetic-input result', async ({
  request,
}, info) => {
  test.setTimeout(240_000);
  const response = await request.post(`${api}/tasks/custom/evaluate`, {
    headers: { Authorization: `Bearer ${await token(request)}` },
    data: {
      loopIds: [fixture.loopId],
      metrics: ['GOOD_VALUE_RATE', 'AUTO_MODE_RATE', 'STEADY_RATE'],
      tsStart: fixture.windowStart,
      tsEnd: fixture.windowEnd,
    },
  });
  expect(response.status()).toBe(200);
  const created = await response.json();
  expect(created.code).toBe('0');
  taskId = created.data.taskId;
  expect(taskId).toMatch(/^[\da-f-]{36}$/);
  let state: Record<string, any>;
  await expect
    .poll(
      async () => {
        state = await read(request, `/tasks/${taskId}`);
        return ['SUCCESS', 'FAILED', 'CANCELLED'].includes(state.status);
      },
      { timeout: 180_000, intervals: [1000, 2000] },
    )
    .toBe(true);
  await info.attach('task-terminal.json', {
    body: JSON.stringify(state!),
    contentType: 'application/json',
  });
  expect(state!.status).toBe('SUCCESS');
  expect(state!.loopsTotal).toBe(1);
  expect(state!.loopsDone).toBe(1);
  const results = await read(
    request,
    `/performance/loops/snapshots?latestOnly=false&taskId=${taskId}&loopId=${fixture.loopId}`,
  );
  expect(results.total).toBe(1);
  expect(results.items).toHaveLength(1);
  snapshot = results.items[0];
  await info.attach('snapshot.json', {
    body: JSON.stringify(snapshot),
    contentType: 'application/json',
  });
  expect(snapshot.loopId).toBe(fixture.loopId);
  expect(snapshot.sourceTaskId).toBe(taskId);
  expect(snapshot.status).toBe('SUCCESS');
  expect(snapshot.algorithmVersion).toMatch(/^KPI_CALC_/);
  expect(snapshot.resultRecordId).toMatch(/^[\da-f-]{36}$/);
  expect(snapshot.dataLineage.dataBlockIds.length).toBeGreaterThan(0);
  for (const field of ['goodValueRate', 'autoModeRate', 'steadyRate']) {
    expect(typeof snapshot[field], field).toBe('number');
    expect(snapshot[field], field).toBeGreaterThanOrEqual(0);
    expect(snapshot[field], field).toBeLessThanOrEqual(100);
  }
});

test('CRITICAL-EVIDENCE-001: task projection links immutable result and real history UI', async ({
  request,
  page,
}, info) => {
  expect(taskId, 'CALC must finish before evidence checks').toBeTruthy();
  const record = await read(
    request,
    `/performance/loops/result-records/${snapshot.resultRecordId}`,
  );
  await info.attach('result-record.json', {
    body: JSON.stringify(record),
    contentType: 'application/json',
  });
  expect(record.recordId).toBe(snapshot.resultRecordId);
  expect(record.objectId).toBe(fixture.loopId);
  expect(record.status).toBe('COMPLETED');
  expect(record.algorithmVersion).toBe(snapshot.algorithmVersion);
  expect(record.payload.customTaskId).toBe(taskId);
  expect(record.payload.snapshotStatus).toBe('SUCCESS');
  expect(record.datasetSnapshotId).toMatch(/^[\da-f-]{36}$/);
  expect(record.configRevision).toMatch(/^(digest:[\da-f]{16}|rev:[^:]+:[\da-f]{16})$/);
  await login(page, 'admin');
  const historyResponse = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname === '/api/v1/performance/loops/snapshots' &&
      url.searchParams.get('taskId') === taskId && response.request().method() === 'GET';
  });
  await gotoApplicationRoute(page, `/metric/loop-evaluation?view=history&taskId=${taskId}`);
  await expect.poll(() => {
    const route = applicationRoute(new URL(page.url()));
    return { path: route.pathname, view: route.searchParams.get('view'), taskId: route.searchParams.get('taskId') };
  }).toEqual({ path: '/metric/loop-evaluation', view: 'history', taskId });
  const history = await historyResponse;
  expect(history.status()).toBe(200);
  const projected = await history.json();
  expect(projected.code).toBe('0');
  expect(projected.data.total).toBe(1);
  expect(projected.data.items).toHaveLength(1);
  expect(projected.data.items[0].sourceTaskId).toBe(taskId);
  expect(projected.data.items[0].resultRecordId).toBe(snapshot.resultRecordId);
  const row = page
    .locator('.ant-table-tbody tr.ant-table-row')
    .filter({ hasText: fixture.loopTagName });
  await expect(row).toHaveCount(1);
  await row.getByRole('button', { name: /详情/ }).click();
  const drawer = page.locator('.ant-drawer:visible');
  await expect(drawer).toContainText(snapshot.algorithmVersion);
  await expect(drawer).toContainText('数据血缘');
  for (const id of snapshot.dataLineage.dataBlockIds) await expect(drawer).toContainText(id);
});
