"""F04 author selfcheck. No product services, shared DB writes or package installation."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from datetime import datetime, timezone

BASE = Path('/Users/zhangping/DEV/clpm-remediation-20261017')
OUT = Path(__file__).resolve().parent
PYTHON = '/Users/zhangping/DEV/CLPM-MVP/backend/.venv/bin/python'
RUFF = '/Users/zhangping/DEV/CLPM-MVP/backend/.venv/bin/ruff'
FILES = ['app/services/loop_batch_restore.py', 'app/services/loop_batch.py',
         'app/schemas/loop_batch.py', 'app/api/v1/endpoints/loops.py', 'tests/test_loop_batch_restore.py']
FRONT = ['apps/web-antd/src/api/engineering-template.ts',
         'apps/web-antd/src/views/config/engineering-templates/batch-apply-modal.vue',
         'apps/web-antd/src/views/config/engineering-templates/batch-restore-modal.vue',
         'apps/web-antd/src/views/config/engineering-templates/index.vue',
         'apps/web-antd/src/views/config/engineering-templates/template-utils.ts',
         'apps/web-antd/src/__tests__/p6-eng-templates.test.ts']
JOBS = [
    ('backend-ruff', BASE/'backend', [RUFF, 'check', *FILES], 120),
    ('backend-format', BASE/'backend', [RUFF, 'format', '--check', *FILES], 120),
    ('backend-targeted', BASE/'backend', [PYTHON, '-m', 'pytest', '-q',
        'tests/test_loop_batch_restore.py', 'tests/test_loop_batch.py',
        'tests/test_p2_04_engineering_templates.py', 'tests/test_p6_04_e2e_defects.py',
        '--junitxml='+str(OUT/'backend-targeted.junit.xml')], 180),
    ('frontend-eslint', BASE/'frontend', ['pnpm', 'exec', 'eslint', *FRONT], 180),
    ('frontend-format', BASE/'frontend', ['pnpm', 'exec', 'oxfmt', '--check', *FRONT], 180),
    ('frontend-targeted', BASE/'frontend', ['pnpm', 'exec', 'vitest', 'run', '--dom', '--no-cache',
        'apps/web-antd/src/__tests__/p6-eng-templates.test.ts', '--reporter=default',
        '--reporter=json', '--outputFile.json='+str(OUT/'frontend-targeted.results.json')], 180),
    ('frontend-types', BASE/'frontend', ['pnpm', 'run', 'check:type'], 240),
    ('frontend-build', BASE/'frontend', ['pnpm', 'run', 'build:antd'], 360),
]

env = dict(os.environ, CLPM_TEST_MODE='1', CLPM_SKIP_EXIT_HOOKS='1',
           PYTHONDONTWRITEBYTECODE='1', POSTGRES_HOST='127.0.0.1', POSTGRES_PORT='1',
           REDIS_HOST='127.0.0.1', REDIS_PORT='1')
results=[]
for name, cwd, command, timeout in JOBS:
    started=datetime.now(timezone.utc).isoformat()
    before=time.monotonic()
    with (OUT/f'{name}.stdout.log').open('wb') as stdout, (OUT/f'{name}.stderr.log').open('wb') as stderr:
        try:
            process=subprocess.run(command,cwd=cwd,env=env,stdout=stdout,stderr=stderr,timeout=timeout)
            rc=process.returncode
        except subprocess.TimeoutExpired:
            rc=124
    result=dict(name=name,command=command,cwd=str(cwd),startedAt=started,
                elapsedSeconds=round(time.monotonic()-before,3),exitCode=rc)
    results.append(result)
    (OUT/f'{name}.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    (OUT/'selfcheck-results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2)+'\n')
    print(f'{name}: rc={rc} elapsed={result["elapsedSeconds"]}',flush=True)

sources=[BASE/'backend'/path for path in FILES]+[BASE/'frontend'/path for path in FRONT]
(OUT/'source-sha256.json').write_text(json.dumps([
    dict(file=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),size=path.stat().st_size)
    for path in sources],ensure_ascii=False,indent=2)+'\n')
raise SystemExit(any(result['exitCode'] for result in results))
