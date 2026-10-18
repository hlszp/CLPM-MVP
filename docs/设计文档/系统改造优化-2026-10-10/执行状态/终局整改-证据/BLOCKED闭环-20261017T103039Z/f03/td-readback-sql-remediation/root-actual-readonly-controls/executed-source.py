"""Keeper read-only TD SQL negative/positive wire controls; no input/result writes."""
import base64,datetime,hashlib,json,os,runpy
from pathlib import Path
from urllib.request import Request,urlopen
from dotenv import dotenv_values
ROOT=Path(__file__).resolve().parent
PLAN=ROOT/'td-readback-sql-remediation/root-readonly-sql-controls.json'
OUT=ROOT/'td-readback-sql-remediation/root-actual-readonly-controls'
NATIVE=Path('/Users/zhangping/DEV/clpm-g5-runtime-20261015/backend/.env')
OUT.mkdir(exist_ok=False)
plan=json.loads(PLAN.read_text());cfg=dotenv_values(NATIVE)
assert plan['database']=='clpm_critical_20261017_local_ts' and plan['writes'] is False
os.environ['NO_PROXY']='127.0.0.1,localhost,::1';os.environ['no_proxy']=os.environ['NO_PROXY']
auth=base64.b64encode((cfg['TDENGINE_USER']+':'+cfg['TDENGINE_PASSWORD']).encode()).decode()
results=[]
def execute(label,sql):
    assert sql.startswith('SELECT ') and plan['database']+'.p_020e5d339098450aa14f3763e3dfdc03' in sql and ';' not in sql
    began=datetime.datetime.now(datetime.timezone.utc).isoformat()
    with urlopen(Request('http://127.0.0.1:17115/rest/sql',data=sql.encode(),headers={'Authorization':'Basic '+auth}),timeout=30) as response:raw=response.read();status=response.status
    (OUT/(label+'.raw.json')).write_bytes(raw);value=json.loads(raw)
    record=dict(label=label,sql=sql,startedAt=began,finishedAt=datetime.datetime.now(datetime.timezone.utc).isoformat(),httpStatus=status,tdCode=value.get('code'),rows=value.get('rows'),responseSha256=hashlib.sha256(raw).hexdigest(),responseBytes=len(raw))
    (OUT/(label+'.command.json')).write_text(json.dumps(record,indent=2)+'\n');results.append(record)
    return value
for item in plan['queries']:
    value=execute(item['label'],item['sql'])
    assert value['code']==(9728 if item['label'].startswith('unquoted') else 0)
full=execute('quoted-full-window',plan['queries'][2]['sql'].removesuffix(' LIMIT 0'))
assert full['code']==0 and full['rows']==3600
inputs=Path(plan['sourceInputPath']);assert hashlib.sha256(inputs.read_bytes()).hexdigest()==plan['sourceInputSha256']
fixture=json.loads(inputs.read_text());names=[r[0] for r in full['column_meta']];rows=[dict(zip(names,r)) for r in full['data']]
tools=runpy.run_path('/Users/zhangping/DEV/clpm-remediation-20261017/e2e/scripts/prepare-critical-fixture.py')
canonical=tools['validate_td_readback'](rows,plan['pointId'],fixture['roles']['PV']['samples'],datetime.datetime.fromisoformat(fixture['windowStart']))
summary=dict(at=datetime.datetime.now(datetime.timezone.utc).isoformat(),readOnly=True,planSha256=hashlib.sha256(PLAN.read_bytes()).hexdigest(),results=results,fullPvSamplesExactlyMatch= len(canonical),numericToleranceUsed=0,sourceSha256=hashlib.sha256(Path('/Users/zhangping/DEV/clpm-remediation-20261017/e2e/scripts/prepare-critical-fixture.py').read_bytes()).hexdigest())
(OUT/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');(OUT/'executed-source.py').write_bytes(Path(__file__).read_bytes());print(json.dumps(summary))
