"""Read-only check of the three-file CI/dependency supplement and saved raw output."""

import hashlib
import json
import re
import subprocess
from pathlib import Path

import yaml

CANDIDATE = Path('/Users/zhangping/DEV/clpm-remediation-20261017')
EVIDENCE = Path(__file__).resolve().parent
RESOURCE = EVIDENCE / 'resource-profile'
FRONTEND = EVIDENCE / 'frontend-smoke-dependency'


def read_json(path):
    return json.loads(path.read_text())


def from_head(path):
    return subprocess.check_output(
        ['git', 'show', f'HEAD:{path}'], cwd=CANDIDATE
    ).decode()


def listed_cases(suites):
    return [
        case
        for suite in suites
        for case in [*suite.get('specs', []), *listed_cases(suite.get('suites', []))]
    ]


workflow_path = '.github/workflows/ci.yml'
old_workflow = yaml.safe_load(from_head(workflow_path))
workflow = yaml.safe_load((CANDIDATE / workflow_path).read_text())
jobs = workflow['jobs']
thread_keys = [
    'OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS',
    'NUMEXPR_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS',
]
checks = {}
checks['threeIndependentJobs'] = all(
    'needs' not in jobs[key] for key in ['backend-ci', 'frontend-ci', 'critical-e2e']
)
checks['allJobsBlocking'] = all(
    not job.get('continue-on-error', False) and 'if' not in job
    for job in jobs.values()
)
checks['criticalAndFrontendStepsBlocking'] = all(
    not step.get('continue-on-error', False)
    for key in ['critical-e2e', 'frontend-ci'] for step in jobs[key]['steps']
)
old_nonblocking = [
    step['name'] for step in old_workflow['jobs']['backend-ci']['steps']
    if step.get('continue-on-error', False)
]
current_nonblocking = [
    step['name'] for step in jobs['backend-ci']['steps']
    if step.get('continue-on-error', False)
]
checks['existingMypyExceptionUnchanged'] = (
    current_nonblocking == old_nonblocking == ['Mypy type check']
)
checks['bothNumericProfilesOne'] = all(
    str(jobs[job]['env'].get(key)) == '1'
    for job in ['backend-ci', 'critical-e2e'] for key in thread_keys
)
checks['timeout30Unchanged'] = re.search(
    r'^timeout = 30$', (CANDIDATE / 'backend/pyproject.toml').read_text(), re.M
) is not None
checks['coverage60Unchanged'] = any(
    '--cov-fail-under=60' in step.get('run', '')
    for step in jobs['backend-ci']['steps']
)
checks['fourBusinessCasesUnchanged'] = (
    (CANDIDATE / 'e2e/critical-cases.json').read_text()
    == from_head('e2e/critical-cases.json')
    and len(read_json(CANDIDATE / 'e2e/critical-cases.json')['cases']) == 4
)
for filename in ['playwright.config.ts', '__tests__/e2e/smoke.spec.ts']:
    path = f'frontend/apps/web-antd/{filename}'
    checks[f'unchanged:{filename}'] = (CANDIDATE / path).read_text() == from_head(path)
checks['numericSourcesUnchanged'] = (
    read_json(RESOURCE / 'source-before.json') == read_json(RESOURCE / 'source-after.json')
)
logs = [(RESOURCE / f'{name}.stdout.log').read_text() for name in ['default', 'one-thread']]
rows = [[row for row in log.splitlines() if row.startswith(('loop-', '核心门禁'))] for log in logs]
checks['same22NumericalRows'] = (
    len([row for row in rows[0] if row.startswith('loop-')]) == 22 and rows[0] == rows[1]
)
for name in ['default', 'one-thread', 'diagnostic-backend-ci', 'diagnostic-critical-e2e', 'actionlint-after']:
    checks[f'rc0:{name}'] = read_json(RESOURCE / f'{name}.command.json')['rc'] == 0

old_package = read_json(FRONTEND / 'before-package.json')
package = read_json(CANDIDATE / 'frontend/apps/web-antd/package.json')
checks['directCatalogDependencyAdded'] = package['devDependencies'].pop('@playwright/test') == 'catalog:'
checks['noOtherPackageChange'] = package == old_package
old_lock = yaml.safe_load((FRONTEND / 'before-pnpm-lock.yaml').read_text())
lock = yaml.safe_load((CANDIDATE / 'frontend/pnpm-lock.yaml').read_text())
checks['reuseLocked160'] = lock['importers']['apps/web-antd']['devDependencies'].pop('@playwright/test') == {
    'specifier': 'catalog:', 'version': '1.60.0',
}
checks['noOtherLockChange'] = lock == old_lock
checks['catalogUnchanged'] = (
    (CANDIDATE / 'frontend/pnpm-workspace.yaml').read_bytes()
    == (FRONTEND / 'before-pnpm-workspace.yaml').read_bytes()
)
checks['offlineFrozenInstallRC0'] = read_json(FRONTEND / 'install.command.json')['rc'] == 0
checks['frozenInstallDidNotRewriteDeclarations'] = (
    read_json(FRONTEND / 'install-source-before.json')
    == read_json(FRONTEND / 'install-source-after.json')
)
checks['realBeforeMissingDependencyRC1'] = read_json(FRONTEND / 'before-collect.command.json')['rc'] == 1
checks['installed160'] = read_json(FRONTEND / 'resolve.stdout.log')['version'] == '1.60.0'
listed = read_json(FRONTEND / 'after-collect.stdout.log')
checks['originalTwoSmokeCasesCollected'] = (
    read_json(FRONTEND / 'after-collect.command.json')['rc'] == 0
    and len(listed_cases(listed['suites'])) == 2 and not listed['errors']
)
for name in ['unit', 'type']:
    checks[f'rc0:frontend-{name}'] = read_json(FRONTEND / f'{name}.command.json')['rc'] == 0
unit_stdout = re.sub(r'\x1b\[[0-9;]*m', '', (FRONTEND / 'unit.stdout.log').read_text())
checks['full1021UnitPass'] = re.search(r'Tests\s+1021 passed', unit_stdout) is not None
checks['applicationSourceUnchanged'] = (
    read_json(FRONTEND / 'app-source-before.json') == read_json(FRONTEND / 'app-source-after.json')
)
result = {
    'allPass': all(checks.values()), 'checks': checks,
    'timingSeconds': {'default': 28.75, 'oneThread': 28.47},
    'scope': 'Local macOS Apple Accelerate gives no meaningful speedup; Linux CI remains required. --list collected two cases without executing either smoke or any critical business case.',
    'existingNonblockingException': current_nonblocking,
    'sourceSha256': {
        path: hashlib.sha256((CANDIDATE / path).read_bytes()).hexdigest()
        for path in [workflow_path, 'frontend/apps/web-antd/package.json', 'frontend/pnpm-lock.yaml']
    },
}
(EVIDENCE / 'supplement-check.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
print(json.dumps(result, ensure_ascii=False))
raise SystemExit(0 if result['allPass'] else 1)
