"""Independent extra negative cases: only/static skip/fixme/expected-failure.
Copies exact candidate gate/validator/manifest to an owned temporary fixture.
No product services or business API requests.
"""
from pathlib import Path
import functools
import hashlib
import http.server
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time

ROOT=Path('/Users/zhangping/DEV/clpm-remediation-20261017')
OUT=Path(__file__).resolve().parent
manifest=json.loads((ROOT/'e2e/critical-cases.json').read_text())
class Health(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b'independent-health-only')
    def log_message(self,*args):
        pass

server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Health)
thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
origin=f'http://127.0.0.1:{server.server_port}'
results=[]
try:
    with tempfile.TemporaryDirectory(prefix='clpm-f03-nonauthor-') as temporary:
        fixture=Path(temporary)
        for relative in ['scripts/run-critical-e2e.sh','e2e/critical-cases.json','e2e/scripts/validate-critical-results.mjs']:
            target=fixture/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/relative,target)
        (fixture/'e2e/tests').mkdir()
        (fixture/'e2e/node_modules').symlink_to(ROOT/'e2e/node_modules',target_is_directory=True)
        (fixture/'e2e/package.json').write_text('{"type":"module"}')
        (fixture/'e2e/playwright.critical.config.ts').write_text("import { defineConfig } from '@playwright/test'; export default defineConfig({testDir:'./tests',testMatch:'critical-business.spec.ts',projects:[{name:'chromium'}],forbidOnly:true,retries:0,workers:1});")
        for scenario in ['static-describe-skip','file-fixme','file-expected-failure','only']:
            lines=["import {test,expect} from '@playwright/test';"]
            if scenario=='static-describe-skip':lines.append("test.describe.skip('all static skipped',()=>{")
            if scenario=='file-fixme':lines.append("test.fixme(true,'whole file disabled');")
            if scenario=='file-expected-failure':lines.append("test.fail(true,'whole file expected failure');")
            for index,item in enumerate(manifest['cases']):
                modifier='.only' if scenario=='only' and index==0 else ''
                lines.append(f"test{modifier}('{item['id']}: independent marker negative',async()=>{{expect(true).toBe(true);}});")
            if scenario=='static-describe-skip':lines.append('});')
            (fixture/'e2e/tests/critical-business.spec.ts').write_text('\n'.join(lines))
            output=OUT/scenario;output.mkdir(exist_ok=True)
            shutil.copyfile(fixture/'e2e/tests/critical-business.spec.ts',output/'fixture.spec.ts')
            command=['bash',str(fixture/'scripts/run-critical-e2e.sh')]
            env=dict(os.environ,E2E_BASE_URL=origin,E2E_API_BASE_URL=origin+'/api/v1',E2E_ARTIFACT_DIR=str(output))
            before=time.monotonic()
            with (output/'gate.stdout.log').open('wb') as stdout,(output/'gate.stderr.log').open('wb') as stderr:
                proc=subprocess.run(command,cwd=fixture,env=env,stdout=stdout,stderr=stderr,timeout=30)
            result=dict(scenario=scenario,command=command,exitCode=proc.returncode,expectedExit=3,elapsedSeconds=time.monotonic()-before)
            result['hasPassMarker']='CRITICAL_E2E_GATE=PASS' in (output/'gate.stdout.log').read_text()
            (output/'command.json').write_text(json.dumps(result,indent=2)+'\n');results.append(result)
finally:
    server.shutdown();server.server_close();thread.join(timeout=5)
(OUT/'static-markers-results.json').write_text(json.dumps(results,indent=2)+'\n')
print(json.dumps(results,indent=2))
raise SystemExit(any(row['exitCode']!=row['expectedExit'] or row['hasPassMarker'] for row in results))
