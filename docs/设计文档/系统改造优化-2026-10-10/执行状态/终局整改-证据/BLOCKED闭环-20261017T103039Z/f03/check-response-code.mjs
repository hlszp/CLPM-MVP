// Read-only regression: execute the actual business-spec assertion against the
// actual backend success()/ApiResponse output; no product services or mocks.
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
const root = '/Users/zhangping/DEV/clpm-remediation-20261017';
const require = createRequire(`${root}/e2e/package.json`);
const { expect } = require('@playwright/test');
const source = readFileSync(`${root}/e2e/tests/critical-business.spec.ts`, 'utf8');
const envelope = JSON.parse(readFileSync(new URL('real-success-envelope.json', import.meta.url), 'utf8'));
const assertion = source.match(/expect\(body\.code\)\.toBe\([^)]*\);/)?.[0];
if (!assertion) throw new Error('actual strict success-code assertion missing');
console.log(JSON.stringify({ assertion, actualCode: envelope.code, actualType: typeof envelope.code }));
const check = new Function('expect', 'body', assertion);
check(expect, envelope);
for (const code of [0, 'ERR_INTERNAL_SERVER_ERROR', 'ERR_REVISION_CONFLICT', undefined, null]) {
  expect(() => check(expect, { ...envelope, code })).toThrow();
}
console.log('real success accepted; non-string-success and error codes rejected');
