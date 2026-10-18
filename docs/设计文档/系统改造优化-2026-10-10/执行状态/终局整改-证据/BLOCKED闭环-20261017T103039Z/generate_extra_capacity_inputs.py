"""Prepare separate fault and independent replay inputs before global freeze."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from dotenv import dotenv_values

E = Path(__file__).resolve().parent
R = Path('/Users/zhangping/DEV/clpm-g5-runtime-20261015')
O = E / 'f02/extra-inputs'
O.mkdir(exist_ok=False)
plan = []
for name, scenario, hour in [('fault-recovery', 'a', 8), ('fault-persistent', 'a', 9), ('independent-cold', 'b', 10), ('independent-hot', 'b', 11)]:
    start = datetime(2026, 10, 16, hour)
    plan.append({'purpose': name, 'scenario': scenario, 'ts_start': start.isoformat(), 'ts_end': (start+timedelta(hours=1)).isoformat(), 'formal20Denominator': False})
(O / 'plan.json').write_text(json.dumps({'inputs': plan, 'boundary': 'Additional recovery/persistence/independent cases, never part of required normal20 success denominator'}, indent=2)+'\n')
deadline = time.monotonic()+1800
while True:
    path = E / 'f02/input-series-audit-v2/progress.json'
    status = json.loads(path.read_text())
    if status['status'] == 'ALL_INPUTS_PASS' and status['passed'] == 20 and (E / 'f02/series-s01/input-generation-complete.json').exists(): break
    assert status['status'] == 'WAITING_GENERATION' and time.monotonic() < deadline, status['status']
    time.sleep(2)
cfg = dotenv_values(R / 'backend/.env'); assert cfg['POSTGRES_DB'] == cfg['TDENGINE_DB'] == 'clpm_capacity_20261017_s01' and cfg['REDIS_PORT'] == '17123'
env = os.environ.copy(); env.update({k:v for k,v in cfg.items() if v is not None}); env.update(NO_PROXY='127.0.0.1,localhost,::1',CLPM_TEST_MODE='0',CLPM_SKIP_EXIT_HOOKS='1',PYTHONUNBUFFERED='1')
sha = subprocess.check_output(['git','rev-parse','HEAD'],cwd=R,text=True).strip(); results=[]
for item in plan:
    out = O / item['purpose']; out.mkdir()
    commands = [[str(R/'backend/.venv/bin/python'),'-u','scripts/generate_capacity_dataset.py','--execute','--scenario',item['scenario'],'--ts-start',item['ts_start'],'--base-url','http://127.0.0.1:17101/api/v1']]
    print(json.dumps({'preparingExtraInput':item}),flush=True)
    before=time.time()
    with (out/'generation.stdout.log').open('wb') as stdout,(out/'generation.stderr.log').open('wb') as stderr:
        process=subprocess.run(commands[0],cwd=R/'backend',env=env,stdout=stdout,stderr=stderr)
    record={'input':item,'candidateAtPreparation':sha,'generatorSha256':hashlib.sha256((R/'backend/scripts/generate_capacity_dataset.py').read_bytes()).hexdigest(),'commands':[{'argv':commands[0],'rc':process.returncode,'startedAt':before,'finishedAt':time.time()}]}
    (out/'generation-command.json').write_text(json.dumps(record,indent=2)+'\n');assert process.returncode==0
    start=datetime.fromisoformat(item['ts_start']);manifest=R/'backend/capacity_dataset'/f"manifest_{item['scenario']}_{start:%Y%m%d%H}.json";saved=out/manifest.name;shutil.copy2(manifest,saved)
    command=[str(R/'backend/.venv/bin/python'),str(E/'f02/audit_capacity_inputs.py'),'--env-file',str(R/'backend/.env'),'--manifest',str(saved),'--out',str(out/'audit')]
    before=time.time()
    with (out/'audit.stdout.log').open('wb') as stdout,(out/'audit.stderr.log').open('wb') as stderr:
        audit=subprocess.run(command,cwd=E/'f02',env=env,stdout=stdout,stderr=stderr)
    result={'input':item,'manifest':str(saved),'manifestSha256':hashlib.sha256(saved.read_bytes()).hexdigest(),'auditCommand':command,'auditRc':audit.returncode,'auditStartedAt':before,'finishedAt':time.time()}
    (out/'audit-command.json').write_text(json.dumps(result,indent=2)+'\n');assert audit.returncode==0
    body=json.loads((out/'audit/input-audit.json').read_text());assert body['status']=='PASS';result['auditReportSha256']=hashlib.sha256((out/'audit/input-audit.json').read_bytes()).hexdigest();results.append(result)
    (O/'progress.json').write_text(json.dumps({'preparedAndAudited':len(results),'required':4,'results':results},indent=2)+'\n');print(json.dumps({'extraInputPass':item['purpose'],'prepared':len(results)}),flush=True)
(O/'complete.json').write_text(json.dumps({'finishedAt':datetime.now(timezone.utc).isoformat(),'preparedAndAudited':4,'results':results,'formal20Executed':False},indent=2)+'\n')
