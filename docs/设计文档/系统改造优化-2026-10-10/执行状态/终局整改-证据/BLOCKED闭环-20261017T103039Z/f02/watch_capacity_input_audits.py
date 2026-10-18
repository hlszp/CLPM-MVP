"""Read-only input-audit watch. Audit only closed rc0/hash-verified generations.
This orchestrates no product tasks and performs no application data writes.
"""
import argparse
from datetime import datetime,timezone
import hashlib,json,os
from pathlib import Path
import subprocess,sys,time

def now():return datetime.now(timezone.utc).isoformat()
def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(path,value):
    with Path(path).open('x') as stream:json.dump(value,stream,ensure_ascii=False,indent=2);stream.write('\n')
def main(args):
    root=Path(args.evidence_dir).resolve();tools=root/'f02';series=tools/'series-s01';out=Path(args.out).resolve();out.mkdir(exist_ok=False)
    source=tools/'audit_capacity_inputs.py';source_sha=digest(source)
    if source_sha!='8bd8cb604032d85948cc6bc1f429974b93424e3ab1f7648af62cebcc2e1a3b6d':raise ValueError('Unreviewed audit tool source')
    plans=json.loads((series/'planned-batches.json').read_text())
    if isinstance(plans,dict):plans=plans['batches']
    if len(plans)!=20 or {p['batch'] for p in plans}!=set(range(1,21)):raise ValueError('Frozen complete 20-batch plan required')
    results={};started=now();deadline=time.monotonic()+args.max_seconds
    def progress(status,error=None):
        body={'status':status,'startedAt':started,'observedAt':now(),'auditSourceSha256':source_sha,'passed':sum(r.get('status')=='PASS' for r in results.values()),'required':20,'results':list(results.values()),'boundary':'Input identity/count/quality checks only; not true capacity nor whole-window raw-value hash'}
        if error:body['error']=error
        (out/'progress.json').write_text(json.dumps(body,ensure_ascii=False,indent=2)+'\n')
        return body
    for index,previous in ((1,tools/'input-a1512-v2'),(2,tools/'input-a1513-v2')):
        report=json.loads((previous/'input-audit.json').read_text())
        command=json.loads((tools/f'input-a151{index+1}-v2.command.json').read_text())
        if report['status']!='PASS' or command['rc']!=0:raise ValueError('Prior first-two audit not complete')
        results[index]={'batch':index,'status':'PASS','existingOutput':str(previous),'command':str(tools/f'input-a151{index+1}-v2.command.json'),'manifestSha256':report['manifestSha256'],'reportSha256':digest(previous/'input-audit.json'),'rc':command['rc']}
    write(out/'watch-command.json',{'argv':sys.argv,'startedAt':started,'auditSourceSha256':source_sha,'plannedBatchesSha256':digest(series/'planned-batches.json'),'sourceSha256':digest(__file__),'readOnly':True,'existingFirstTwoReused':True})
    progress('WAITING_GENERATION')
    try:
        while time.monotonic()<deadline:
            try:generated=json.loads((series/'input-generation-results.json').read_text())
            except (FileNotFoundError,json.JSONDecodeError):time.sleep(args.poll_seconds);continue
            for entry in generated:
                index=entry['batch']['batch']
                if index not in {p['batch'] for p in plans}:raise ValueError('Unexpected batch index')
                if index in results:
                    if entry.get('manifest_sha256') and results[index]['manifestSha256']!=entry['manifest_sha256']:raise ValueError('Completed manifest identity changed')
                    continue
                if entry.get('finished_at') is None:continue
                if entry.get('exit_code')!=0:raise ValueError(f'Generation batch {index} failed rc={entry.get("exit_code")}')
                if not entry.get('manifest_sha256'):continue
                manifest=Path(entry['manifest']).resolve();manifest.relative_to(series/'input-manifests')
                if digest(manifest)!=entry['manifest_sha256']:raise ValueError(f'Batch {index} manifest hash mismatch')
                for field in ('stdout','stderr'):
                    if digest(entry[field])!=entry[field+'_sha256']:raise ValueError(f'Batch {index} generation {field} not closed/hash mismatch')
                if digest(source)!=source_sha:raise ValueError('Audit source changed during watch')
                planned=next(p for p in plans if p['batch']==index)
                if entry['batch']!=planned:raise ValueError(f'Batch {index} differs from complete frozen plan')
                batch=out/f'{index:02d}-{planned["scenario"]}-{planned["cache"]}';batch.mkdir()
                command=[sys.executable,str(source),'--env-file',args.env_file,'--manifest',str(manifest),'--out',str(batch/'audit'),'--raw-samples-per-composition','1']
                began=now();clock=time.monotonic();env=dict(os.environ,NO_PROXY='localhost,127.0.0.1,::1',PYTHONDONTWRITEBYTECODE='1')
                try:
                    run=subprocess.run(command,env=env,capture_output=True,timeout=180);rc,stdout,stderr=run.returncode,run.stdout,run.stderr
                except subprocess.TimeoutExpired as exc:rc,stdout,stderr=124,exc.stdout or b'',(exc.stderr or b'')+b'\nAUDIT_TIMEOUT_180_SECONDS\n'
                (batch/'stdout.log').write_bytes(stdout);(batch/'stderr.log').write_bytes(stderr)
                metadata={'argv':command,'cwd':str(Path.cwd()),'startedAt':began,'finishedAt':now(),'elapsedSeconds':time.monotonic()-clock,'rc':rc,'stdoutSha256':hashlib.sha256(stdout).hexdigest(),'stderrSha256':hashlib.sha256(stderr).hexdigest(),'manifestSha256Before':entry['manifest_sha256'],'manifestSha256After':digest(manifest),'auditSourceSha256Before':source_sha,'auditSourceSha256After':digest(source),'generation':entry}
                write(batch/'command.json',metadata)
                report_path=batch/'audit'/'input-audit.json';report=json.loads(report_path.read_text()) if report_path.exists() else {}
                item={'batch':index,'scenario':planned['scenario'],'cache':planned['cache'],'windowStart':planned['ts_start'],'status':report.get('status','BLOCKED'),'output':str(batch),'manifestSha256':entry['manifest_sha256'],'reportSha256':digest(report_path) if report_path.exists() else None,'rc':rc}
                results[index]=item
                if rc!=0 or report.get('status')!='PASS' or digest(source)!=source_sha or digest(manifest)!=entry['manifest_sha256']:
                    progress('AUDIT_FAILED',item);print(json.dumps({'status':'AUDIT_FAILED','batch':index,'out':str(batch)}),flush=True);return 1
                progress('WAITING_GENERATION');print(json.dumps({'status':'INPUT_PASS','batch':index,'passed':len(results),'out':str(batch)}),flush=True)
            if len(results)==20:
                final=progress('ALL_INPUTS_PASS');write(out/'summary.json',final);print(json.dumps({'status':'ALL_INPUTS_PASS','passed':20,'out':str(out)}),flush=True);return 0
            time.sleep(args.poll_seconds)
        progress('INPUT_WATCH_TIMEOUT');return 2
    except BaseException as exc:
        progress('INPUT_WATCH_BLOCKED',{'type':type(exc).__name__,'message':str(exc)});print(json.dumps({'status':'INPUT_WATCH_BLOCKED','error':str(exc),'out':str(out)}),flush=True);return 2

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--evidence-dir',required=True);p.add_argument('--env-file',required=True);p.add_argument('--out',required=True);p.add_argument('--max-seconds',type=float,default=7200);p.add_argument('--poll-seconds',type=float,default=5)
    raise SystemExit(main(p.parse_args()))
