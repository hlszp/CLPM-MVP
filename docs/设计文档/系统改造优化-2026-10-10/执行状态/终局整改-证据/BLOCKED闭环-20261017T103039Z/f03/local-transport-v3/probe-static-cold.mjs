// Root executes this transport-only probe. It never contacts a CLPM API.
import fs from 'node:fs/promises';
import http from 'node:http';
import path from 'node:path';
import crypto from 'node:crypto';
import { createRequire } from 'node:module';

const [checkout, fixturePath, output] = process.argv.slice(2);
const fixture = JSON.parse(await fs.readFile(fixturePath, 'utf8'));
const base = 'http://127.0.0.1:17267';
const require = createRequire(path.join(checkout, 'e2e/package.json'));
const { chromium } = require('@playwright/test');
const digest = (body) => crypto.createHash('sha256').update(body).digest('hex');
const report = { purpose: 'TRANSPORT_ONLY_NO_CLPM_API', startedAt: new Date().toISOString(), http: [], browsers: [] };
const paths = Object.keys(fixture.files).filter((name) => name.startsWith('css/'));
if (paths.length !== 96) throw new Error('expected exactly 96 frozen CSS resources');

function get(name) {
  return new Promise((resolve) => {
    const startedAt = new Date().toISOString();
    const request = http.get(`${base}/${name}`, { agent: false, headers: { Connection: 'close' } }, (response) => {
      const chunks = [];
      response.on('data', (chunk) => chunks.push(chunk));
      response.on('end', () => {
        const body = Buffer.concat(chunks);
        resolve({ name, startedAt, rc: 0, status: response.statusCode, bytes: body.length, sha256: digest(body) });
      });
      response.on('error', (error) => resolve({ name, startedAt, rc: 1, error: String(error) }));
    });
    request.setTimeout(10000, () => request.destroy(new Error('transport timeout 10000ms; no retry')));
    request.on('error', (error) => resolve({ name, startedAt, rc: 1, error: String(error) }));
  });
}

let browser;
try {
  // Four bursts, 64 new TCP connections per burst. Preserve every outcome.
  for (let burst = 0; burst < 4; burst++) {
    const rows = await Promise.all(Array.from({ length: 64 }, (_, i) => get(paths[(burst * 64 + i) % paths.length])));
    report.http.push(...rows.map((row) => ({ burst, ...row })));
  }
  browser = await chromium.launch({ headless: true, args: ['--no-proxy-server'] });
  await Promise.all(Array.from({ length: 4 }, async (_, index) => {
    const context = await browser.newContext({ serviceWorkers: 'block' });
    await context.tracing.start({ screenshots: true, snapshots: true, sources: false });
    const page = await context.newPage();
    const row = { index, responses: [], failed: [], consoleErrors: [], pageErrors: [], foreignRequests: [] };
    const bodyReads = [];
    page.on('request', (request) => {
      if (request.url().startsWith('http') && new URL(request.url()).origin !== base) row.foreignRequests.push(request.url());
    });
    page.on('requestfailed', (request) => row.failed.push({ url: request.url(), error: request.failure() }));
    page.on('console', (message) => { if (message.type() === 'error') row.consoleErrors.push(message.text()); });
    page.on('pageerror', (error) => row.pageErrors.push(String(error)));
    page.on('response', (response) => {
      const name = new URL(response.url()).pathname.slice(1);
      if (fixture.files[name]) bodyReads.push((async () => {
        try {
          const body = await response.body();
          row.responses.push({ name, status: response.status(), bytes: body.length, sha256: digest(body) });
        } catch (error) { row.responses.push({ name, error: String(error) }); }
      })());
    });
    try {
      await page.goto(`${base}/index.html`, { waitUntil: 'load', timeout: 20000 });
      await page.waitForFunction(() => document.documentElement.dataset.transportReady === '1', undefined, { timeout: 1000 });
      await Promise.all(bodyReads);
      row.cssCount = new Set(row.responses.filter((item) => item.name.startsWith('css/')).map((item) => item.name)).size;
      row.finalUrl = page.url();
      await page.screenshot({ path: path.join(output, `cold-${index}.png`) });
    } catch (error) { row.navigationError = String(error); }
    finally {
      await context.tracing.stop({ path: path.join(output, `cold-${index}.trace.zip`) });
      await context.close();
      report.browsers.push(row);
    }
  }));
} catch (error) { report.error = String(error); }
finally {
  if (browser) await browser.close();
  const equal = (row) => row.status === 200 && row.bytes === fixture.files[row.name]?.bytes && row.sha256 === fixture.files[row.name]?.sha256;
  report.passed = !report.error && report.http.length === 256 && report.http.every(equal)
    && report.browsers.length === 4 && report.browsers.every((row) => !row.navigationError && row.cssCount === 96
      && row.responses.every(equal) && !row.failed.length && !row.consoleErrors.length
      && !row.pageErrors.length && !row.foreignRequests.length);
  report.finishedAt = new Date().toISOString();
  await fs.writeFile(path.join(output, 'result.json'), `${JSON.stringify(report, null, 2)}\n`);
  console.log(JSON.stringify({ passed: report.passed, httpCollected: report.http.length, coldContexts: report.browsers.length, businessCasesExecuted: 0 }));
  process.exitCode = report.passed ? 0 : 1;
}
