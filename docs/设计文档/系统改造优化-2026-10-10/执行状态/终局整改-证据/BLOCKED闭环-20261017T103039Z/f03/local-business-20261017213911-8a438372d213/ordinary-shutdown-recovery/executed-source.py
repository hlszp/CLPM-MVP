"""Root ordinary shutdown of the exact owned API; old seed/stop failures stay failures."""
import asyncio,datetime,hashlib,importlib.util,json,os,shutil,signal,socket,subprocess,time
from pathlib import Path
from types import SimpleNamespace
import psutil
ROOT=Path(__file__).resolve().parent
OUT=ROOT/'local-business-20261017213911-8a438372d213'
REC=OUT/'ordinary-shutdown-recovery'
PY='/Users/zhangping/DEV/clpm-g5-runtime-20261015/backend/.venv/bin/python'
NATIVE=Path('/Users/zhangping/DEV/clpm-g5-runtime-20261015/backend/.env')
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def write(name,obj):(REC/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n')
REC.mkdir(exist_ok=False)
for p in OUT.iterdir():
    if p.is_file():shutil.copy2(p,REC/p.name)
state=json.loads((OUT/'current-f03-local-api.json').read_text());api=psutil.Process(state['apiPid'])
assert abs(api.create_time()-state['apiCreated'])<.01 and api.ppid()==state['supervisorPid'] and api.cmdline()==state['apiArgv']
children=api.children(recursive=True)
beats=[p for p in children if p.ppid()==api.pid and 'celery' in p.cmdline() and 'beat' in p.cmdline()]
assert len(beats)==1 and beats[0].pid==72277
observed=[dict(pid=p.pid,ppid=p.ppid(),created=p.create_time(),argv=p.cmdline(),status=p.status()) for p in [api,*children]]
raw=subprocess.run(['ps','-ax','-o','pid=,ppid=,lstart=,stat=,command='],capture_output=True);assert raw.returncode==0
(REC/'before.ps.txt').write_bytes(raw.stdout)
log=OUT/'backend-api.stdout.log';offset=log.stat().st_size
write('action.json',dict(at=now(),signal='SIGTERM',targetPid=api.pid,logOffsetBeforeSignal=offset,observed=observed,scope='ordinary API lifespan shutdown; no manual Worker/Beat start; old seed/stop not accepted'))
os.kill(api.pid,signal.SIGTERM)
deadline=time.monotonic()+90
while time.monotonic()<deadline and not (OUT/'api-exit.json').exists():time.sleep(.2)
assert (OUT/'api-exit.json').exists(),'API supervisor has no real exit; no force kill sent'
actual=json.loads((OUT/'api-exit.json').read_text());write('actual-api-exit.json',actual)
assert actual['pid']==api.pid and actual['rc'] in (0,-15)
remaining=[]
for item in observed:
    try:
        p=psutil.Process(item['pid'])
        if abs(p.create_time()-item['created'])<.01:remaining.append(dict(item,currentStatus=p.status()))
    except psutil.NoSuchProcess:pass
write('remaining-processes.json',remaining);assert not remaining
newlog=log.read_bytes()[offset:];(REC/'actual-shutdown-log.raw').write_bytes(newlog)
assert b'Application shutdown complete.' in newlog and ('Finished server process ['+str(api.pid)+']').encode() in newlog
spec=importlib.util.spec_from_file_location('local_recovery_driver',ROOT/'local-term-v5/keeper-local-f03-v5b.py');driver=importlib.util.module_from_spec(spec);spec.loader.exec_module(driver)
driver.ARGS=SimpleNamespace(native_env_file=NATIVE,expected_sha=state['sourceSha'],run_id=state['localRunId']);driver.CHECKOUT=Path(state['checkout']);driver.OUT=OUT;driver.PY=PY;driver.ENV=driver.environment()
broker=driver.queue_observation('after-ordinary-shutdown-recovery',require_worker=False);write('actual-broker-after.json',broker)
assert not any(broker['redis']['brokerLists'].values()) and not broker['redis']['unacked'] and not broker['redis']['unackedIndex']
asyncio.run(driver.capacity_observation('after-ordinary-shutdown-recovery'))
for port in (17261,17266):
    with socket.socket() as sock:assert sock.connect_ex(('127.0.0.1',port))!=0
write('complete.json',dict(at=now(),cleanupOnly=True,originalSeedRc=1,originalStopRc=1,actualApiExit=actual,ownedProductProcessesRemaining=0,brokerEmpty=True,businessGateExecuted=False))
(REC/'executed-source.py').write_bytes(Path(__file__).read_bytes())
files=[dict(path=str(p.relative_to(REC)),size=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in sorted(REC.rglob('*')) if p.is_file()]
write('evidence-manifest.json',dict(files=files));print(json.dumps(dict(rc=0,archive=str(REC),actualApiRc=actual['rc'],originalRunPassed=False)))
