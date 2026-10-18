// Gate-contract tests, deliberately independent of the product and its DB.
// These pure assertion fixtures validate fail-closed orchestration only. They
// are never counted as the real authentication/calculation/evidence E2E cases.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import {
  copyFileSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from 'node:fs';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { after, before, test } from 'node:test';
import { validate } from './validate-critical-results.mjs';

const source = resolve(fileURLToPath(new URL('../..', import.meta.url)));
const temporary = mkdtempSync(join(tmpdir(), 'clpm-gate-contract-'));
const fixture = join(temporary, 'fixture');
const artifacts = process.env.GATE_CONTRACT_ARTIFACT_DIR
  ? resolve(process.env.GATE_CONTRACT_ARTIFACT_DIR)
  : join(temporary, 'artifacts');
const manifest = JSON.parse(readFileSync(join(source, 'e2e/critical-cases.json'), 'utf8'));
let origin;
let server;

function command(args, options = {}) {
  return new Promise((accept, reject) => {
    const processHandle = spawn(args[0], args.slice(1), options);
    let stdout = '';
    let stderr = '';
    processHandle.stdout.on('data', (data) => {
      stdout += data;
    });
    processHandle.stderr.on('data', (data) => {
      stderr += data;
    });
    processHandle.once('error', reject);
    processHandle.once('exit', (code, signal) => accept({ args, code, signal, stdout, stderr }));
  });
}

before(async () => {
  for (const directory of ['scripts', 'e2e/scripts', 'e2e/tests'])
    mkdirSync(join(fixture, directory), { recursive: true });
  mkdirSync(artifacts, { recursive: true });
  for (const name of [
    'scripts/run-critical-e2e.sh',
    'e2e/critical-cases.json',
    'e2e/scripts/validate-critical-results.mjs',
  ]) {
    copyFileSync(join(source, name), join(fixture, name));
  }
  symlinkSync(join(source, 'e2e/node_modules'), join(fixture, 'e2e/node_modules'), 'dir');
  writeFileSync(join(fixture, 'e2e/package.json'), '{"type":"module"}\n');
  writeFileSync(
    join(fixture, 'e2e/playwright.critical.config.ts'),
    `
    import { defineConfig } from '@playwright/test';
    export default defineConfig({ testDir: './tests', testMatch: 'critical-business.spec.ts',
      projects: [{ name: 'chromium' }], retries: 0, workers: 1, forbidOnly: true });
  `,
  );
  server = createServer((_request, response) => {
    response.writeHead(200);
    response.end('gate-contract-fixture');
  });
  await new Promise((accept) => server.listen(0, '127.0.0.1', accept));
  origin = `http://127.0.0.1:${server.address().port}`;
});

after(async () => {
  await new Promise((accept) => server.close(accept));
  // Do not follow the node_modules symlink or remove a user-supplied artifact path.
  rmSync(temporary, { recursive: true, force: true });
});

function spec(scenario) {
  const cases = scenario === 'missing' ? manifest.cases.slice(0, -1) : manifest.cases;
  return `import { test, expect } from '@playwright/test';\n${cases
    .map(
      (item, index) => `
    test('${scenario === 'wrong-id' && index === 0 ? 'CRITICAL-OTHER-001' : item.id}: gate-contract fixture', async ({}, testInfo) => {
      ${scenario === 'all-skipped' || (scenario === 'part-skipped' && index === 0) ? 'test.skip(true, "controlled runtime skip");' : ''}
      expect(${scenario === 'failed' && index === 0 ? 'false' : scenario === 'flaky' && index === 0 ? 'testInfo.retry > 0' : 'true'}).toBe(true);
    });
  `,
    )
    .join('\n')}`;
}

for (const [scenario, expected] of [
  ['passed', 0],
  ['unreachable', 2],
  ['failed', 1],
  ['all-skipped', 3],
  ['part-skipped', 3],
  ['missing', 3],
  ['wrong-id', 3],
]) {
  test(`real Playwright gate: ${scenario} -> rc=${expected}`, { timeout: 40_000 }, async () => {
    writeFileSync(join(fixture, 'e2e/tests/critical-business.spec.ts'), spec(scenario));
    const directory = join(artifacts, scenario);
    mkdirSync(directory, { recursive: true });
    const result = await command(['bash', join(fixture, 'scripts/run-critical-e2e.sh')], {
      cwd: fixture,
      env: {
        ...process.env,
        E2E_BASE_URL: scenario === 'unreachable' ? 'http://127.0.0.1:1' : origin,
        E2E_API_BASE_URL: `${origin}/api/v1`,
        E2E_ARTIFACT_DIR: directory,
      },
    });
    writeFileSync(join(directory, 'gate.stdout.log'), result.stdout);
    writeFileSync(join(directory, 'gate.stderr.log'), result.stderr);
    writeFileSync(
      join(directory, 'command.json'),
      `${JSON.stringify({ ...result, stdout: undefined, stderr: undefined }, null, 2)}\n`,
    );
    assert.equal(result.code, expected, result.stderr || result.stdout);
    assert.equal(result.stdout.includes('CRITICAL_E2E_GATE=PASS'), expected === 0);
    if (scenario === 'all-skipped' || scenario === 'part-skipped') {
      assert.equal(
        JSON.parse(readFileSync(join(directory, 'executed.json'), 'utf8')).stats.skipped,
        scenario === 'all-skipped' ? 4 : 1,
      );
    }
  });
}

test(
  'real Playwright flaky retry is rejected even when Playwright exits zero',
  { timeout: 40_000 },
  async () => {
    writeFileSync(join(fixture, 'e2e/tests/critical-business.spec.ts'), spec('flaky'));
    const directory = join(artifacts, 'flaky');
    mkdirSync(directory, { recursive: true });
    const reportPath = join(directory, 'executed.json');
    const result = await command(
      [
        join(fixture, 'e2e/node_modules/.bin/playwright'),
        'test',
        '--config=playwright.critical.config.ts',
        '--retries=1',
        '--reporter=json',
      ],
      {
        cwd: join(fixture, 'e2e'),
        env: { ...process.env, PLAYWRIGHT_JSON_OUTPUT_FILE: reportPath },
      },
    );
    writeFileSync(join(directory, 'playwright.stdout.log'), result.stdout);
    writeFileSync(join(directory, 'playwright.stderr.log'), result.stderr);
    writeFileSync(
      join(directory, 'command.json'),
      `${JSON.stringify({ ...result, stdout: undefined, stderr: undefined }, null, 2)}\n`,
    );
    assert.equal(result.code, 0);
    const summary = validate('run', manifest, JSON.parse(readFileSync(reportPath, 'utf8')));
    writeFileSync(
      join(directory, 'execution-summary.json'),
      `${JSON.stringify(summary, null, 2)}\n`,
    );
    assert.equal(summary.passed, false);
    assert.ok(summary.violations.some((message) => message.includes('attempts=2')));
    assert.ok(summary.violations.some((message) => message.includes('flaky=1')));
  },
);
