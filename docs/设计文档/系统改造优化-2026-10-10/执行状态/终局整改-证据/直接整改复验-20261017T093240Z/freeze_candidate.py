from pathlib import Path
import datetime,difflib,hashlib,json,os,shutil,subprocess,tempfile
ROOT=Path('/Users/zhangping/DEV/clpm-remediation-20261017');OUT=Path(__file__).parent/'candidate';OUT.mkdir(exist_ok=True)
def run(args,cwd=ROOT):return subprocess.run(args,cwd=cwd,capture_output=True,check=False)
def git(*args):
 r=run(['git',*args]);assert r.returncode==0;return r.stdout
head=git('rev-parse','HEAD').decode().strip();assert head=='dbb1de314ae50e7373c6559ab746b690909bf1b0'
tracked=git('diff','--name-only','HEAD','-z').decode().strip('\0').split('\0')
untracked=git('ls-files','--others','--exclude-standard','-z').decode().strip('\0').split('\0')
tracked=[p for p in tracked if p];untracked=[p for p in untracked if p]
patch=git('diff','--binary','--no-ext-diff','HEAD','--')
for rel in untracked:
 data=(ROOT/rel).read_bytes();assert b'\0' not in data and data.endswith(b'\n')
 mode='100755' if (ROOT/rel).stat().st_mode&0o111 else '100644'
 patch+=f'diff --git a/{rel} b/{rel}\nnew file mode {mode}\n'.encode()
 patch+=''.join(difflib.unified_diff([],data.decode().splitlines(keepends=True),fromfile='/dev/null',tofile='b/'+rel)).encode()
patchfile=OUT/'candidate.patch';patchfile.write_bytes(patch)
rows=[]
for rel in tracked+untracked:
 p=ROOT/rel;data=p.read_bytes();original=git('show',head+':'+rel) if rel in tracked else None
 rows.append({'path':rel,'kind':'modified' if original is not None else 'new','bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'base_sha256':hashlib.sha256(original).hexdigest() if original is not None else None})
tmp=Path(tempfile.mkdtemp(prefix='clpm-patch-recover-check-'))
try:
 for rel in tracked:
  p=tmp/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(git('show',head+':'+rel));p.chmod((ROOT/rel).stat().st_mode&0o777)
 check=run(['git','apply','--check',str(patchfile)],tmp);apply=run(['git','apply',str(patchfile)],tmp) if check.returncode==0 else None
 results=[]
 if apply and apply.returncode==0:
  for row in rows:
   p=tmp/row['path'];results.append({'path':row['path'],'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'matches':hashlib.sha256(p.read_bytes()).hexdigest()==row['sha256']})
 restore={'scratch':str(tmp),'check_exit_code':check.returncode,'check_stdout':check.stdout.decode(),'check_stderr':check.stderr.decode(),'apply_exit_code':apply.returncode if apply else None,'files':results,'all_match':len(results)==len(rows) and all(r['matches'] for r in results)}
 assert restore['all_match'],restore
finally:
 shutil.rmtree(tmp)
restore['scratch_removed']=not tmp.exists()
(OUT/'patch-restore-check.json').write_text(json.dumps(restore,ensure_ascii=False,indent=2)+'\n')
manifest={'timestamp':datetime.datetime.now(datetime.timezone.utc).isoformat(),'root':str(ROOT),'base_commit':head,'branch':git('branch','--show-current').decode().strip(),'candidate_is_uncommitted':True,'patch_path':str(patchfile),'patch_sha256':hashlib.sha256(patch).hexdigest(),'patch_bytes':len(patch),'files':rows,'recovery_test':'git apply --check/apply to base file contents in new scratch; all hashes match','no_commit_push_merge_deploy':True}
(OUT/'source-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
(OUT/'git-status.txt').write_bytes(git('status','--short'))
print(json.dumps({'files':len(rows),'modified':len(tracked),'new':len(untracked),'patch_sha256':manifest['patch_sha256'],'recovery':restore['all_match']}))
