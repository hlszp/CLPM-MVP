"""Root-only preserve the never-started f29b schema before a new fixed candidate."""
import asyncio,base64,datetime,hashlib,json,os,re,socket,subprocess
from pathlib import Path
from urllib.request import Request,urlopen
import asyncpg,redis
from dotenv import dotenv_values
ROOT=Path(__file__).resolve().parent
OUT=ROOT/'local-f29b-bootstrap-preservation-v2'
NATIVE=Path('/Users/zhangping/DEV/clpm-g5-runtime-20261015/backend/.env')
DB='clpm_critical_20261017_local'
OLD=DB+'_prep_f29b'
PY='/Users/zhangping/DEV/clpm-g5-runtime-20261015/backend/.venv/bin/python'
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def write(name,data):(OUT/name).write_text(json.dumps(data,ensure_ascii=False,indent=2,default=str)+'\n')
def command(name,argv):
    start=now();p=subprocess.run(argv,capture_output=True)
    (OUT/(name+'.stdout')).write_bytes(p.stdout);(OUT/(name+'.stderr')).write_bytes(p.stderr)
    write(name+'.json',dict(argv=argv,startedAt=start,finishedAt=now(),rc=p.returncode,stdoutSha256=hashlib.sha256(p.stdout).hexdigest(),stderrSha256=hashlib.sha256(p.stderr).hexdigest()))
    if p.returncode:raise RuntimeError(name+' failed')
    return p.stdout
async def run(cfg):
    c=await asyncpg.connect(host='127.0.0.1',port=17102,user=cfg['POSTGRES_USER'],password=cfg['POSTGRES_PASSWORD'],database=DB)
    try:
        async with c.transaction(isolation='repeatable_read',readonly=True):
            actual=await c.fetchval('SELECT current_database()')
            loops=await c.fetchval('SELECT count(*) FROM loop_ledger')
            schema=await c.fetchval('SELECT version_num FROM alembic_version')
            write('prepared-pg-readonly.json',dict(at=now(),actualDatabase=actual,loopCount=loops,schema=schema,readOnly=await c.fetchval('SHOW transaction_read_only')))
            rows=[dict(r) for r in await c.fetch('SELECT id::text,tag_name FROM loop_ledger ORDER BY tag_name')]
            write('bootstrap-loop-identities.json',rows)
            assert actual==DB and loops==27 and not any(r['tag_name'].startswith('CRITICAL-') for r in rows) and schema=='p702perm01'
            assert not (ROOT/'local-business-20261017213202-f29b57338688/current-f03-local-api.json').exists()
    finally:await c.close()
    command('pg-dump-prepared',['docker','exec','clpm-mvp-postgres','pg_dump','-Fc','-U',cfg['POSTGRES_USER'],DB])
    c=await asyncpg.connect(host='127.0.0.1',port=17102,user=cfg['POSTGRES_USER'],password=cfg['POSTGRES_PASSWORD'],database='postgres')
    try:
        active=[dict(x) for x in await c.fetch('SELECT pid,datname,state,application_name FROM pg_stat_activity WHERE datname=$1',DB)]
        assert not active and not await c.fetchval('SELECT 1 FROM pg_database WHERE datname=$1',OLD)
        assert re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',cfg['POSTGRES_USER'])
        for name,sql in [('rename-prepared',f'ALTER DATABASE "{DB}" RENAME TO "{OLD}"'),('create-fresh',f'CREATE DATABASE "{DB}" OWNER "{cfg["POSTGRES_USER"]}" TEMPLATE template0')]:
            start=now();result=await c.execute(sql);write(name+'.json',dict(sql=sql,startedAt=start,finishedAt=now(),result=result))
    finally:await c.close()
if __name__=='__main__':
    OUT.mkdir(exist_ok=False);os.environ['NO_PROXY']='127.0.0.1,localhost,::1';os.environ['no_proxy']=os.environ['NO_PROXY']
    assert hashlib.sha256(NATIVE.read_bytes()).hexdigest()=='27d6fc10cc68376b056d1c7803f1b6361e48c11bc3de46c4e8a46cc4b2abe04b'
    cfg=dotenv_values(NATIVE)
    raw=command('process-before',['ps','-ax','-o','pid=,ppid=,lstart=,stat=,command=']).decode()
    assert not [line for line in raw.splitlines() if PY in line and (' -m uvicorn ' in line or ' -m celery ' in line)]
    for port in (17261,17266):
        with socket.socket() as sock:assert sock.connect_ex(('127.0.0.1',port))!=0
    sizes={str(db):redis.Redis(host='127.0.0.1',port=17124,db=db).dbsize() for db in (0,1,2)}
    assert not any(sizes.values());write('unchanged-empty-redis.json',dict(at=now(),dbsize=sizes,port=17124,mutations=False))
    token=base64.b64encode((cfg['TDENGINE_USER']+':'+cfg['TDENGINE_PASSWORD']).encode()).decode()
    sql='SHOW clpm_critical_20261017_local_ts.TABLES'
    with urlopen(Request('http://127.0.0.1:17115/rest/sql',data=sql.encode(),headers={'Authorization':'Basic '+token}),timeout=30) as response:body=response.read()
    (OUT/'empty-td-response.json').write_bytes(body)
    value=json.loads(body);assert value['code']==0 and value['rows']==0
    asyncio.run(run(cfg))
    write('complete.json',dict(at=now(),archivedDatabase=OLD,freshDatabase=DB,bootstrapOnly=True,sourceSha='f29b57338688ca9f9c0900f4c10338d3ad54cc2f',actualApiStarts=0,actualTasks=0,redisAndTdMutated=False))
    (OUT/'executed-source.py').write_bytes(Path(__file__).read_bytes())
    files=[dict(path=str(f.relative_to(OUT)),size=f.stat().st_size,sha256=hashlib.sha256(f.read_bytes()).hexdigest()) for f in sorted(OUT.rglob('*')) if f.is_file()]
    write('evidence-manifest.json',dict(files=files));print(json.dumps(dict(rc=0,archive=str(OUT),manifestSha256=hashlib.sha256((OUT/'evidence-manifest.json').read_bytes()).hexdigest())))
