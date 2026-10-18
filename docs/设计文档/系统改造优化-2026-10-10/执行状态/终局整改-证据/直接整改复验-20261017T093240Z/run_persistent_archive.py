from pathlib import Path
import datetime,hashlib,json,os,pwd,shutil,tarfile,tempfile,time
root=Path('/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态')
evidence=Path('/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/终局整改-证据/直接整改复验-20261017T093240Z')/'f08'
evidence.mkdir(parents=True,exist_ok=True)
old=json.loads((root/'终局整改-证据/非作者复验-20261017T085542Z/f08-manifest-independent-check.json').read_text())
rows=[r for r in old['rows'] if not r['tracked']]
assert len(rows)==3
archive_dir=Path('/Users/zhangping/DEV/CLPM-evidence-archive/2026-10-17-final-audit')
archive_dir.mkdir(parents=True,exist_ok=True)
archive=archive_dir/'large-original-evidence.tar.gz'
assert not archive.exists(),'Do not overwrite persistent archive'
def digest(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
entries=[]
for row in rows:
 source=Path(row['resolved_path'])
 actual=digest(source)
 assert actual==row['expected_sha256'] and source.stat().st_size==row['expected_bytes']
 entries.append({'source':str(source),'member':str(source.relative_to(root)),'bytes':source.stat().st_size,'sha256':actual})
start=time.monotonic()
with tarfile.open(archive,'w:gz',compresslevel=5) as tf:
 for entry in entries:tf.add(entry['source'],arcname=entry['member'],recursive=False)
 with archive.open('rb') as f:os.fsync(f.fileno())
manifest={'created_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'archive':str(archive),'archive_bytes':archive.stat().st_size,'archive_sha256':digest(archive),'owner_role':'CLPM 项目证据保管负责人','filesystem_owner':pwd.getpwuid(archive.stat().st_uid).pw_name,'retention':'无自动过期或清理任务；后续按项目负责人指定规则维护','storage_scope':'本机持久目录；未配置异机或云副本','entries':entries}
(archive_dir/'archive-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
(archive_dir/'README.md').write_text('# CLPM 终局审计大文件持久归档\n\n位置与每个源文件、大小、SHA-256 见 archive-manifest.json。归档由本轮整改创建，Owner 为 CLPM 项目证据保管负责人，文件所有者 zhangping。未设置自动过期/清理任务；后续按项目负责人指定规则维护。\n\n这是本机持久归档，位于项目工作树和临时目录之外；无异机或云存储配置。恢复时先核归档 SHA-256，再按 manifest 指定 member 读取并核每份原始文件大小和 SHA-256；不得覆盖原审计源证据。\n')
restore_dir=Path(tempfile.mkdtemp(prefix='clpm-f08-persistent-restore-'))
restored=[]
try:
 with tarfile.open(archive,'r:gz') as tf:
  for entry in entries:
   destination=restore_dir/entry['member'];destination.parent.mkdir(parents=True,exist_ok=True)
   source=tf.extractfile(entry['member']);assert source is not None
   with destination.open('wb') as dst:shutil.copyfileobj(source,dst,1024*1024)
   actual=digest(destination)
   item={'archive':str(archive),'member':entry['member'],'restored_bytes':destination.stat().st_size,'restored_sha256':actual,'expected_bytes':entry['bytes'],'expected_sha256':entry['sha256'],'match':actual==entry['sha256'] and destination.stat().st_size==entry['bytes']}
   restored.append(item);assert item['match']
finally:shutil.rmtree(restore_dir)
(evidence/'archive-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
(evidence/'restore-from-persistent-archive.json').write_text(json.dumps({'completed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'command':'python run_persistent_archive.py','exit_code':0,'elapsed_seconds':time.monotonic()-start,'source':'persistent archive, not live source copy','restored':restored,'temporary_restore_cleaned':not restore_dir.exists()},ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'archive':str(archive),'bytes':archive.stat().st_size,'restored':len(restored),'sha256':manifest['archive_sha256'],'exit_code':0},ensure_ascii=False),flush=True)

