from pathlib import Path
import hashlib,json,runpy,shutil,sys,tempfile
from unittest.mock import patch
BASE=Path(__file__).resolve().parents[2]
SOURCE=BASE/'f03/freeze_local_f03_input_manifest_root.py'
ORIGINALS=BASE/'independent/f03-local-numeric-review/run-20261017215608-9f356782ef40/observed-pre-input-raw'
SHA='9f356782ef4076dabb91a86f56e054e514e341ea'
assert hashlib.sha256(SOURCE.read_bytes()).hexdigest()=='0de792ae43818fb041915c5fc3a5cd8aa181230de230b1a71165460d085ff985'
def git(args,**kwargs):
 assert args in [['git','rev-parse','HEAD'],['git','status','--porcelain','--untracked-files=no']]
 return SHA+'\n' if kwargs.get('text') else b''
def fixture(root):
 out=root/'scope';shutil.copytree(ORIGINALS,out)
 shutil.rmtree(out/'input-only-before-gate')
 (out/'current-f03-local-api.json').write_bytes((ORIGINALS/'input-only-before-gate/started-state.json').read_bytes())
 (out/'input-only-manifest.original.json').unlink()
 (out/'observed-raw-manifest.json').unlink()
 run=root/'run.json';run.write_text(json.dumps({'out':str(out),'candidateSha':SHA,'runId':'unit-immutable-only'}))
 return out,run
with tempfile.TemporaryDirectory(prefix='independent-input-freeze-') as temp:
 root=Path(temp);out,run=fixture(root)
 with patch('sys.argv',[str(SOURCE),str(run)]),patch('subprocess.check_output',git):runpy.run_path(str(SOURCE),run_name='__main__')
 manifest_path=out/'input-only-before-gate/input-only-manifest.json';before=manifest_path.read_bytes();m=json.loads(before)
 assert len(m['files'])==21 and m['mutablePhaseFilesCopiedBeforeHashing'] is True
 for name in ('environment-safe.json','current-f03-local-api.json'):(out/name).write_text('actual later phase replaced this unit source')
 assert all(Path(r['path']).stat().st_size==r['bytes'] and hashlib.sha256(Path(r['path']).read_bytes()).hexdigest()==r['sha256'] for r in m['files'])
 assert manifest_path.read_bytes()==before
 assert {Path(r['path']).name for r in m['files']} >= {'started-state.json','environment-before-gate.json'}
 assert not any(Path(r['path']).name in {'environment-safe.json','current-f03-local-api.json'} for r in m['files'])
 blocked=root/'after-gate';blocked.mkdir();out2,run2=fixture(blocked);(out2/'gate').mkdir();error=None
 with patch('sys.argv',[str(SOURCE),str(run2)]),patch('subprocess.check_output',git):
  try:runpy.run_path(str(SOURCE),run_name='__main__')
  except AssertionError as caught:error=str(caught)
 assert error=='input freeze must precede actual gate' and not (out2/'input-only-before-gate').exists()
 print(json.dumps({'status':'PASS_TOOL_IMMUTABLE_COPY_ONLY','sourceSha256':hashlib.sha256(SOURCE.read_bytes()).hexdigest(),'original21ReferencesSurviveActualSourceFileOverwrites':True,'manifestBytesUnchanged':True,'afterGateRejectedBeforeWritingArchive':True,'actualServicesOrBusinessOperations':0,'allGitCallsStubbed':True,'boundary':'Tool checks recorded JSON readiness/identity; actual process liveness, PG raw and source identity remain independently required. Gate path absence alone does not prove globally no task dispatch.'}))
