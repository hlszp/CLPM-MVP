"""Non-author F02 forward/reverse checks in fresh processes.
Negative controls mutate only imported functions inside an owned temp plugin.
Candidate source bytes are checked before/after and never rewritten.
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

ROOT=Path('/Users/zhangping/DEV/clpm-remediation-20261017')
OUT=Path(__file__).resolve().parent
PYTHON='/Users/zhangping/DEV/CLPM-MVP/backend/.venv/bin/python'
identity=json.loads((OUT.parent/'f02-source-identity.json').read_text())
sha=lambda value:hashlib.sha256(value).hexdigest()
before={name:sha((ROOT/name).read_bytes()) for name in identity['sourceFiles']}
assert before==identity['sourceFiles'], 'candidate source differs from frozen author identity'
(OUT/'source-before.json').write_text(json.dumps(dict(identityVersion=identity.get('version'),sourceFiles=before),indent=2)+'\n')
old_result=subprocess.run(['git','show',identity['base']+':backend/app/tasks/kpi_calc.py'],cwd=ROOT,capture_output=True,check=True)
old=old_result.stdout.decode()
function=next(node for node in ast.parse(old).body if isinstance(node,ast.AsyncFunctionDef) and node.name=='_calculate_loop_kpi')
old_function='from __future__ import annotations\n'+ast.get_source_segment(old,function)+'\n'
(OUT/'old-candidate-calculate-loop-kpi.py').write_text(old_function)
(OUT/'old-behavior-source.json').write_text(json.dumps(dict(base=identity['base'],gitCommand=old_result.args,
    fullSourceSha256=sha(old_result.stdout),functionSha256=sha(old_function.encode())),indent=2)+'\n')
plugins={
 'negative_old_conversion':f'''def pytest_configure(config):
    from pathlib import Path
    from app.tasks import kpi_calc
    source=Path({str(OUT/'old-candidate-calculate-loop-kpi.py')!r}).read_text()
    exec(compile(source,"old-candidate-calculate-loop-kpi.py","exec"),kpi_calc.__dict__)
''',
 'negative_default_gather':'''def pytest_configure(config):
    import asyncio
    from app.services import data_planner
    from app.services.data_source import logical_wide_builder
    data_planner.gather_settled=asyncio.gather
    logical_wide_builder.gather_settled=asyncio.gather
''',
 'negative_resource_boundary':'''def pytest_configure(config):
    from app.tasks import kpi_calc
    kpi_calc._assert_batch_resources=lambda results:None
''',
}
env=dict(os.environ,CLPM_TEST_MODE='1',CLPM_SKIP_EXIT_HOOKS='1',PYTHONDONTWRITEBYTECODE='1',
         POSTGRES_HOST='127.0.0.1',POSTGRES_PORT='1',REDIS_HOST='127.0.0.1',REDIS_PORT='1')
for name in ['DATABASE_URL','POSTGRES_URL','POSTGRES_DSN','REDIS_URL']:
    env.pop(name,None)
commands=[
 ('forward-regression',['tests/test_g5_data_acquisition_failure.py','tests/test_kpi_calc.py',
   'tests/test_g5_d01_opt_batch.py','tests/test_data_planner','tests/test_kpi_capacity_benchmark.py',
   'tests/test_g5_pre_card_fixes.py','tests/test_p5_storm_rework.py','tests/test_batch_failure_breaker_g29.py'],None,0),
 ('forward-additional',[str(OUT/'test_independent_resource_contract.py')],None,0),
 ('reverse-old-conversion',['tests/test_g5_data_acquisition_failure.py::test_provider_failure_propagates_without_inconclusive_snapshot'],'negative_old_conversion',1),
 ('reverse-default-gather',['tests/test_g5_data_acquisition_failure.py::test_sibling_queries_settle_before_local_retry'],'negative_default_gather',1),
 ('reverse-resource-boundary',['tests/test_g5_data_acquisition_failure.py::test_one_resource_failure_in_thousand_cannot_be_success',
   'tests/test_g5_data_acquisition_failure.py::test_minority_resource_failure_propagates_to_parent_and_terminal_record'],'negative_resource_boundary',1),
 ('forward-restored-boundary',['tests/test_g5_data_acquisition_failure.py',str(OUT/'test_independent_resource_contract.py')],None,0),
]
results=[]
with tempfile.TemporaryDirectory(prefix='clpm-f02-independent-reverse-') as temporary:
    for name,source in plugins.items():
        path=Path(temporary)/(name+'.py');path.write_text(source)
        (OUT/(name+'.py')).write_text(source)
    env['PYTHONPATH']=temporary+os.pathsep+str(ROOT/'backend')
    for name,selectors,plugin,expected in commands:
        command=[PYTHON,'-m','pytest','-q',*selectors,'-p','no:cacheprovider',
            '--junitxml='+str(OUT/(name+'.junit.xml'))]
        if plugin:command.extend(['-p',plugin])
        start=time.monotonic();started=datetime.now(timezone.utc).isoformat()
        with (OUT/(name+'.stdout.log')).open('wb') as stdout,(OUT/(name+'.stderr.log')).open('wb') as stderr:
            process=subprocess.run(command,cwd=ROOT/'backend',env=env,stdout=stdout,stderr=stderr,timeout=180)
        result=dict(name=name,command=command,cwd=str(ROOT/'backend'),startedAt=started,
                    elapsedSeconds=time.monotonic()-start,exitCode=process.returncode,expectedExit=expected)
        (OUT/(name+'.command.json')).write_text(json.dumps(result,indent=2)+'\n');results.append(result)
        print(f'{name}: actual={process.returncode} expected={expected}',flush=True)
after={name:sha((ROOT/name).read_bytes()) for name in identity['sourceFiles']}
(OUT/'source-restoration-check.json').write_text(json.dumps(dict(before=before,after=after,unchanged=before==after),indent=2)+'\n')
(OUT/'code-contract-results.json').write_text(json.dumps(results,indent=2)+'\n')
assert before==after, 'candidate source changed during independent checks'
raise SystemExit(any(row['exitCode']!=row['expectedExit'] for row in results))
