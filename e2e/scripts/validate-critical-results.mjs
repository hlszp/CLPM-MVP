import { readFileSync, writeFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

export function validate(mode, manifest, report) {
  const violations = [];
  const rows = [];
  const visit = (suite) => {
    for (const spec of suite.specs ?? []) {
      for (const test of spec.tests ?? []) {
        rows.push({
          id: spec.title.match(/^(CRITICAL-[A-Z]+-\d{3})(?=:|\s|$)/)?.[1],
          file: spec.file.replaceAll('\\', '/').split('/').at(-1),
          project: test.projectName,
          expectedStatus: test.expectedStatus,
          status: test.status,
          results: test.results ?? [],
        });
      }
    }
    for (const child of suite.suites ?? []) visit(child);
  };
  if (!['collect', 'run'].includes(mode)) violations.push('unknown validation mode');
  if (manifest.schemaVersion !== 1 || !manifest.project || !manifest.cases?.length) {
    violations.push('invalid or empty manifest');
  }
  for (const suite of report.suites ?? []) visit(suite);
  if (report.errors?.length) violations.push(`report errors: ${report.errors.length}`);
  const required = new Map();
  for (const item of manifest.cases ?? []) {
    if (required.has(item.id)) violations.push(`duplicate manifest id: ${item.id}`);
    required.set(item.id, item.file);
  }
  const seen = new Set();
  for (const row of rows) {
    if (!row.id || !required.has(row.id))
      violations.push(`unexpected case: ${row.id ?? '(no id)'}`);
    if (seen.has(row.id)) violations.push(`duplicate execution: ${row.id}`);
    seen.add(row.id);
    if (row.project !== manifest.project)
      violations.push(`${row.id}: unexpected project ${row.project}`);
    if (required.get(row.id) !== row.file)
      violations.push(`${row.id}: unexpected file ${row.file}`);
    if (row.expectedStatus !== 'passed')
      violations.push(`${row.id}: expectedStatus=${row.expectedStatus}`);
    if (mode === 'run') {
      if (row.results.length !== 1)
        violations.push(`${row.id}: attempts=${row.results.length}, require exactly one`);
      if (row.status !== 'expected') violations.push(`${row.id}: outcome=${row.status}`);
      for (const result of row.results) {
        if (result.status !== 'passed') violations.push(`${row.id}: status=${result.status}`);
        if (result.retry !== 0) violations.push(`${row.id}: retry=${result.retry}`);
      }
    }
  }
  for (const id of required.keys()) if (!seen.has(id)) violations.push(`missing case: ${id}`);
  if (rows.length !== required.size)
    violations.push(`denominator=${rows.length}, required=${required.size}`);
  const stats = report.stats ?? {};
  if (mode === 'run') {
    for (const key of ['skipped', 'flaky', 'unexpected']) {
      if (stats[key] !== 0) violations.push(`${key}=${stats[key] ?? '(missing)'}`);
    }
    if (stats.expected !== required.size)
      violations.push(`passed=${stats.expected}, required=${required.size}`);
  }
  const failed = rows.some((row) =>
    row.results.some((result) => ['failed', 'timedOut', 'interrupted'].includes(result.status)),
  );
  return {
    mode,
    passed: violations.length === 0,
    exitCode: violations.length === 0 ? 0 : failed ? 1 : 3,
    required: required.size,
    collected: rows.length,
    stats,
    rows,
    violations,
  };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [mode, manifestPath, reportPath, summaryPath] = process.argv.slice(2);
  let summary;
  try {
    summary = validate(
      mode,
      JSON.parse(readFileSync(manifestPath, 'utf8')),
      JSON.parse(readFileSync(reportPath, 'utf8')),
    );
  } catch (error) {
    summary = { mode, passed: false, exitCode: 3, violations: [String(error)] };
  }
  if (summaryPath) writeFileSync(summaryPath, `${JSON.stringify(summary, null, 2)}\n`);
  process.stdout.write(`${JSON.stringify(summary, null, 2)}\n`);
  process.exitCode = summary.exitCode;
}
