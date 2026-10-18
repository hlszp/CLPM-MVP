from pathlib import Path
import datetime,hashlib,json
E=Path(__file__).parent;S=E.parent.parent
EXCLUDED={'evidence-manifest.json','evidence-manifest.sha256','manifest-verification.json'}
def entry(path):return {'path':str(path),'bytes':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
assert (E/'docs-final-review/receipt.md').is_file(),'wait for final non-author receipt'
assert json.loads((E/'final-hygiene.json').read_text())['all_checks_passed']
rows=[]
for p in sorted(E.rglob('*')):
 if p.is_file() and not (p.parent==E and p.name in EXCLUDED):
  assert not p.is_symlink(),p
  row=entry(p);row['path']=str(p.relative_to(E));rows.append(row)
files=['终局整改-整改后复验报告.md','P7汇总与G7结论.md','任务看板.md','终局整改台账.md','终局整改-独立复验报告.md','终局整改-遗留索引.md','终局整改-运行来源台账.md','终局整改-直接整改执行.md','P6-任务状态/STATE.md','终局对标检查报告.md','终局整改-非作者复验回执.md']
docs=[entry(S/n) for n in files]
source=entry(S/'终局整改-证据/evidence-manifest.json')
archive=entry(Path('/Users/zhangping/DEV/CLPM-evidence-archive/2026-10-17-final-audit/large-original-evidence.tar.gz'))
assert archive['sha256']=='241e671130b9aad1d3f0b1bb3b19befcac185411620f4c711ecf8e315fe2bbf1'
obj={'schema_version':1,'created_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'root':str(E),'scope':'本轮新证据完整清单；不覆盖原24项源manifest/原审计/上一轮回执','excluded_self_and_verification':sorted(EXCLUDED),'entries':rows,'entry_count':len(rows),'document_snapshot':docs,'prior_source_manifest':source,'external_archives':[archive],'candidate_patch':json.loads((E/'candidate/source-manifest.json').read_text())['patch_sha256'],'local_retention':'project evidence directory, plus separately declared large original archive; no cloud/disaster recovery claim'}
p=E/'evidence-manifest.json';p.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n');mh=hashlib.sha256(p.read_bytes()).hexdigest();(E/'evidence-manifest.sha256').write_text(mh+'  evidence-manifest.json\n')
bad=[]
for row in rows:
 q=E/row['path'];data=entry(q)
 if row['bytes']!=data['bytes'] or row['sha256']!=data['sha256']:bad.append(row['path'])
for row in docs+[source,archive]:
 data=entry(Path(row['path']))
 if row['bytes']!=data['bytes'] or row['sha256']!=data['sha256']:bad.append(row['path'])
verify={'timestamp':datetime.datetime.now(datetime.timezone.utc).isoformat(),'manifest_sha256':mh,'checked_evidence_entries':len(rows),'checked_documents':len(docs),'checked_source_manifest':True,'checked_external_archives':1,'mismatches':bad,'pass':not bad}
(E/'manifest-verification.json').write_text(json.dumps(verify,ensure_ascii=False,indent=2)+'\n');print(json.dumps(verify,ensure_ascii=False));assert not bad
