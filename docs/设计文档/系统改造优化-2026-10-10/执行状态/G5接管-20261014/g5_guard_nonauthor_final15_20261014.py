"""Protected manifest: independent base comparison plus ordinary guard red/green."""
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode=True
ROOT=Path('/private/tmp/clpm-g5-followup-20261014')
BACKEND=ROOT/'backend'
BASE='e07dcf8019ea0cb65cfbe1ea0f9c1e108bc6d252'
FREEZE_PATH=Path('/private/tmp/g5-final-candidate-freeze-v2-20261014.json')
freeze=json.loads(FREEZE_PATH.read_text())
def hash_files():
    return {f:hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in freeze['files']}
before=hash_files()
assert before==freeze['files']
def base_bytes(relative):
    return subprocess.check_output(['git','-C',str(ROOT),'show',BASE+':'+relative])
golden=BACKEND/'tests/golden/refactor_protected_manifest.json'
current=json.loads(golden.read_text())
old_raw=base_bytes('backend/tests/golden/refactor_protected_manifest.json')
old=json.loads(old_raw)
assert current['baseline_head']==BASE
assert set(current['files'])==set(old['files'])
assert sorted(k for k in current['files'] if current['files'][k]!=old['files'][k])==['app/services/preprocessing/pipeline.py']
registry=BACKEND/'tests/refactor/protected_manifest.py'
assert registry.read_bytes()==base_bytes('backend/tests/refactor/protected_manifest.py')
guard=BACKEND/'tests/test_refactor_protected_manifest.py'
assert guard.read_bytes()==base_bytes('backend/tests/test_refactor_protected_manifest.py')
sys.path.insert(0,str(BACKEND))
from tests.refactor.protected_manifest import collect_protected
actual=collect_protected()
assert actual==current['files']
changes=[]
rows=[]
for f,h in sorted(current['files'].items()):
    baseline_hash=hashlib.sha256(base_bytes('backend/'+f)).hexdigest()
    actual_hash=hashlib.sha256((BACKEND/f).read_bytes()).hexdigest()
    assert actual_hash==h
    if actual_hash!=baseline_hash:
        changes.append(f)
    rows.append({'path':f,'base_sha256':baseline_hash,'candidate_sha256':actual_hash,'same':actual_hash==baseline_hash})
assert changes==['app/services/preprocessing/pipeline.py']
spec=importlib.util.spec_from_file_location('g5_guard_oracle_test',guard)
test=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=test
spec.loader.exec_module(test)
os.environ.pop('REFACTOR_UPDATE_GOLDEN',None)
test.test_protected_files_unchanged() # ordinary mode; does not write/update golden
old_path=Path('/private/tmp/g5-old-protected-manifest-e07-20261014.json')
old_path.write_bytes(old_raw)
test._GOLDEN=old_path
old_failure=None
try:
    test.test_protected_files_unchanged()
except AssertionError as exc:
    old_failure=str(exc)
assert old_failure and 'app/services/preprocessing/pipeline.py' in old_failure
old_path_after=old_path.read_bytes()
assert old_path_after==old_raw
assert hash_files()==before
env_bypass={name:os.environ.get(name) for name in ('REFACTOR_UPDATE_GOLDEN','LEFTHOOK','LEFTHOOK_EXCLUDE','HUSKY','SKIP','GIT_CONFIG_COUNT')}
result={
 'status':'PASS','formal_freeze_sha256':hashlib.sha256(FREEZE_PATH.read_bytes()).hexdigest(),
 'formal_15_files_stable':True,'protected_count':len(rows),'protected_set_same_as_e07':True,
 'only_change_from_base':changes,'golden_only_file_hash_change':changes,'golden_base_updated_to':current['baseline_head'],
 'registry_and_guard_unchanged_from_base':True,'ordinary_guard_current_pass':True,
 'ordinary_guard_old_manifest_fails':True,'ordinary_guard_old_error':old_failure,
 'update_mode':'UNSET during independent ordinary guard','bypass_env_snapshot':env_bypass,
 'protected_rows':rows,
 'scope':'authorized UNKNOWN pipeline predicate/pre_v2 only; actual algorithm/threshold files byte-identical to e07; no commit/push/hooks invoked by this auditor'}
Path('/private/tmp/g5-guard-nonauthor-final15-20261014.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
print(json.dumps({k:v for k,v in result.items() if k not in ('protected_rows','ordinary_guard_old_error')},ensure_ascii=False))

