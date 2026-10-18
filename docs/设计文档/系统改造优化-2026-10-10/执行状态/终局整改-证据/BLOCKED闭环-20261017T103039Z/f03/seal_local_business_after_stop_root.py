from pathlib import Path
import hashlib,json,datetime
E=Path("/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/终局整改-证据/BLOCKED闭环-20261017T103039Z")
r=json.loads((E/'f03/local-decimal-fix-root-run.json').read_text())
root=Path(r['out'])
target=root/'evidence-manifest.json'
assert not target.exists()
items=[]
for p in sorted(root.rglob('*')):
    if not p.is_file(): continue
    assert not p.is_symlink(),str(p)
    st=p.stat(); data=p.read_bytes(); st2=p.stat()
    assert (st.st_mtime_ns,st.st_size)==(st2.st_mtime_ns,st2.st_size),str(p)
    items.append({'path':str(p.relative_to(root)),'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
for row in items:
    p=root/row['path']; assert hashlib.sha256(p.read_bytes()).hexdigest()==row['sha256']
manifest={'schemaVersion':1,'createdAtUtc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'candidateSha':r['candidateSha'],'scope':'root-owned stopped local F03 business run; independent receipt stored separately','items':items}
with target.open('x') as f: json.dump(manifest,f,ensure_ascii=False,indent=2);f.write('\n')
print(json.dumps({'path':str(target),'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'items':len(items),'bytes':sum(i['bytes'] for i in items)}))

