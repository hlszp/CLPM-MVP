from pathlib import Path
import datetime,hashlib,json
E=Path("/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/终局整改-证据/BLOCKED闭环-20261017T103039Z")
for relative in ('f02/synthetic-capacity-binding-actual-root-v1','f02/synthetic-capacity-binding-root-invocation-v1','f02/synthetic-capacity-dump-structure-root-v1','f02/synthetic-capacity-dump-adapter-root'):
 root=E/relative;target=root/'evidence-manifest.json';assert not target.exists()
 items=[]
 for p in sorted(root.rglob('*')):
  if not p.is_file():continue
  assert not p.is_symlink()
  st=p.stat();data=p.read_bytes();st2=p.stat()
  assert (st.st_size,st.st_mtime_ns)==(st2.st_size,st2.st_mtime_ns)
  items.append({'path':str(p.relative_to(root)),'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
 for row in items:assert hashlib.sha256((root/row['path']).read_bytes()).hexdigest()==row['sha256']
 value={'schemaVersion':1,'createdAtUtc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'scope':'completed root-owned S01 INPUT preparation only; not formal capacity evidence','items':items}
 with target.open('x') as f:json.dump(value,f,ensure_ascii=False,indent=2);f.write('\n')
 print(json.dumps({'relativeRoot':relative,'items':len(items),'bytes':sum(i['bytes'] for i in items),'manifestSha256':hashlib.sha256(target.read_bytes()).hexdigest()}))

