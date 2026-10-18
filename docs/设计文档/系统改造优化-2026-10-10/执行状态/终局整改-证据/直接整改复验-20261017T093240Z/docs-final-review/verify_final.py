"""Read-only final review; restores the patch only in this verifier's own temp."""
import collections
import datetime
import hashlib
import json
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

S = Path('/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态')
E = S / '终局整改-证据/直接整改复验-20261017T093240Z'
C = Path('/Users/zhangping/DEV/clpm-remediation-20261017')
OWN = E / 'docs-final-review'
result = {'observedAt': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'commands': [], 'files': {}}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def register(path):
    path = Path(path)
    result['files'][str(path)] = {'bytes': path.stat().st_size, 'sha256': sha(path)}
    return path


def read_json(path):
    return json.loads(register(path).read_text())


def run(argv, cwd):
    proc = subprocess.run(argv, cwd=cwd, capture_output=True)
    result['commands'].append({'argv': argv, 'cwd': str(cwd), 'rc': proc.returncode,
                               'stdout': proc.stdout.decode(errors='replace'),
                               'stderr': proc.stderr.decode(errors='replace')})
    assert proc.returncode == 0, result['commands'][-1]
    return proc.stdout


docs = ['终局整改-整改后复验报告.md', 'P7汇总与G7结论.md', '任务看板.md', '终局整改台账.md',
        '终局整改-遗留索引.md', '终局整改-运行来源台账.md', 'P6-任务状态/STATE.md',
        'P6P7-对账矩阵与结论修正-20261017.md', '终局整改-独立复验报告.md']
contents = {name: register(S / name).read_text() for name in docs}
review_path = OWN / 'receipt.md'
pending_paths = {str(review_path), str(E / 'evidence-manifest.json')}
result['documentLinks'] = {}
for name, text in contents.items():
    links = re.findall(r'\]\((/[^)]+)\)', text)
    missing = sorted(set(link for link in links if not Path(link).exists()))
    result['documentLinks'][name] = {'count': len(links), 'missing': missing}
    assert all(path in pending_paths for path in missing), (name, missing)
final = contents[docs[0]]
assert '执行包整体仍为 BLOCKED' in final and '不能宣称八项全量关闭' in final
result['itemVerdicts'] = {}
for item in range(1, 9):
    matches = [line for line in final.splitlines() if line.startswith(f'| F0{item}/P')]
    assert len(matches) == 1
    result['itemVerdicts'][f'F0{item}'] = matches[0]
    assert ('整体BLOCKED' in matches[0]) if item in (2, 3) else ('PASS' in matches[0])
assert 'CONDITIONAL_PASS' in final and '未提交补丁' in final
result['waiverRows'] = {}
for name in docs[:5]:
    rows = []
    for token in ['P6-07', 'IAR-A01', '真实岗位走查', 'P7-01']:
        matches = [line for line in contents[name].splitlines() if line.startswith('| ' + token)
                   and ('跨仓' in line if token == 'P6-07' else True)
                   and ('现场校准' in line if token == 'P7-01' else True)]
        assert len(matches) == 1 and '用户豁免' in matches[0], (name, token, matches)
        rows.extend(matches)
    assert all(token in contents[name] for token in ['900s', '600s', 'DEC-05', 'D03', 'D04'])
    result['waiverRows'][name] = rows
state = contents['P6-任务状态/STATE.md']
cp81 = [line for line in state.splitlines() if line.startswith('81. CP-')]
assert len(cp81) == 1 and '6909' in cp81[0] and '1021' in cp81[0] and 'BLOCKED' in cp81[0]
result['CP81'] = cp81[0]
assert 'PRE-RULING SNAPSHOT' in contents['P6P7-对账矩阵与结论修正-20261017.md'][:1800]
assert 'SUPERSEDED' in contents['P6P7-对账矩阵与结论修正-20261017.md'][:1800]
assert '不能将本报告中的PASS作为现行销项' in contents['终局整改-独立复验报告.md'][:1000]

protected = {'终局对标检查报告.md': '517e91b57f2e2ff3c182be30d32ffb329d9c7b9397e2d3395188f190fe167016',
             '终局整改-非作者复验回执.md': '54cc82ca97e64fb7fd5314b14b41d5444ccaaf719b8478651d919e7a50f853e6'}
result['protected'] = {name: {'expected': expected, 'actual': sha(register(S / name))}
                       for name, expected in protected.items()}
assert all(item['expected'] == item['actual'] for item in result['protected'].values())
result['protectedExpectedProvenance'] = '主协调本轮开工前交接记录；本次独立重算当前文件，不能凭当前单份文件自证过去。'

manifest = read_json(E / 'candidate/source-manifest.json')
parent_restore = read_json(E / 'candidate/patch-restore-check.json')
patch = register(manifest['patch_path'])
assert sha(patch) == manifest['patch_sha256'] == 'c5baf0fd18a4b109e221fded2c131fdb4be097a7671a87fb3695762b70843fc4'
assert patch.stat().st_size == manifest['patch_bytes']
assert len(manifest['files']) == 36
assert collections.Counter(item['kind'] for item in manifest['files']) == {'modified': 23, 'new': 13}
assert run(['git', 'rev-parse', 'HEAD'], C).decode().strip() == manifest['base_commit']
assert run(['git', 'branch', '--show-current'], C).decode().strip() == manifest['branch']
changed = run(['git', 'diff', '--name-only', '-z', 'HEAD'], C).decode().split('\0')
new = run(['git', 'ls-files', '--others', '--exclude-standard', '-z'], C).decode().split('\0')
assert set(filter(None, changed + new)) == {item['path'] for item in manifest['files']}
result['candidateFiles'] = []
with tempfile.TemporaryDirectory(prefix='clpm-independent-final-restore-') as temporary:
    temp = Path(temporary)
    for item in manifest['files']:
        relative = Path(item['path'])
        assert not relative.is_absolute() and '..' not in relative.parts
        actual_path = C / relative
        assert actual_path.stat().st_size == item['bytes'] and sha(actual_path) == item['sha256']
        if item['kind'] == 'modified':
            base = run(['git', 'show', manifest['base_commit'] + ':' + item['path']], C)
            assert hashlib.sha256(base).hexdigest() == item['base_sha256']
            destination = temp / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(base)
    run(['git', 'apply', '--check', str(patch)], temp)
    run(['git', 'apply', str(patch)], temp)
    for item in manifest['files']:
        actual = sha(temp / item['path'])
        result['candidateFiles'].append({'path': item['path'], 'sha256': actual, 'matches': actual == item['sha256']})
    assert all(item['matches'] for item in result['candidateFiles'])
result['independentPatchRecovery'] = {'count': 36, 'matches': 36, 'temporaryRemoved': not Path(temporary).exists()}
assert parent_restore['all_match'] and parent_restore['check_exit_code'] == parent_restore['apply_exit_code'] == 0
assert len(parent_restore['files']) == 36

def identity(scopes):
    data = {str(path.relative_to(C)): sha(path) for scope in scopes for path in (C / scope).rglob('*')
            if path.is_file() and path.suffix in {'.py', '.ts', '.vue', '.json'}}
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()

ids = {'backend': identity(['backend/app', 'backend/tests']), 'frontend': identity(['frontend/apps/web-antd/src'])}
result['currentSourceIdentities'] = ids
checks = ['backend-full-v3', 'backend-collect-final-v3', 'ruff-check-v3', 'ruff-format-v3',
          'frontend-full-rowkey-final', 'frontend-eslint-rowkey-final',
          'frontend-typecheck-rowkey-final', 'frontend-build-rowkey-final', 'alembic-check']
result['verificationCommands'] = {}
for label in checks:
    record = read_json(E / ('verification/' + label + '.json'))
    assert record['exit_code'] == 0
    for stream in ['stdout', 'stderr']:
        path = register(E / ('verification/' + label + '.' + stream + '.log'))
        assert sha(path) == record[stream + '_sha256']
    if label != 'alembic-check':
        expected = ids['frontend' if label.startswith('frontend') else 'backend']
        assert record['source_stable'] and record['source_identity_before'] == record['source_identity_after'] == expected
    result['verificationCommands'][label] = record
backend_text = (E / 'verification/backend-full-v3.stdout.log').read_text()
match = re.search(r'(6909 passed, 340 skipped, 166 deselected, 12 xfailed, 4 xpassed[^\n]*)', backend_text)
assert match, 'backend full denominator not present in raw stdout'
assert '7265/7431 tests collected (166 deselected)' in (E / 'verification/backend-collect-final-v3.stdout.log').read_text()
root = ET.parse(register(E / 'verification/backend-full-v3.junit.xml')).getroot()
suite = root.find('testsuite')
assert suite is not None and suite.attrib['tests'] == '7265' and suite.attrib['failures'] == suite.attrib['errors'] == '0' and suite.attrib['skipped'] == '352'
front = read_json(E / 'verification/frontend-full-rowkey-final.results.json')
assert front['success'] and front['numTotalTests'] == front['numPassedTests'] == 1021
assert front['numFailedTests'] == front['numPendingTests'] == front['numTodoTests'] == 0 and len(front['testResults']) == 106
assert sum(len(item['assertionResults']) for item in front['testResults']) == 1021
assert all(item['status'] == 'passed' for file in front['testResults'] for item in file['assertionResults'])
result['testDenominators'] = {'backendRaw': match.group(1), 'backendTotal': 6909 + 340 + 166 + 12 + 4,
                             'backendJUnit': suite.attrib, 'frontendTests': 1021, 'frontendFiles': 106}

def cli(relative):
    raw = read_json(E / relative)
    assert raw['exit_code'] == 0, relative
    decoded, _ = json.JSONDecoder().raw_decode(raw['stdout'].split('### Result\n', 1)[1])
    return decoded

register(E / 'f04/ui-receipt.md')
register(E / 'f07/ui-receipt.md')
apply_result = cli('f04/cli/13-final-crosspage-preview-apply.json')
records = apply_result['results']
assert [len(row['request']['loopIds']) for row in records] == [200, 51, 200, 51]
assert [row['request']['dryRun'] for row in records] == [True, True, False, False]
assert all(row['status'] == 200 and len(row['body']['data']['results']) == len(row['request']['loopIds'])
           and all(item['status'] == 'OK' for item in row['body']['data']['results']) for row in records)
assert '成功 251' in apply_result['summary'] and '失败 0' in apply_result['summary']
for relative, dry in [('f04/cli/15-preview-restore.json', True), ('f04/cli/16-confirm-restore.json', False)]:
    data = cli(relative)
    assert [len(row['request']['loopIds']) for row in data['results']] == [200, 51]
    assert all(row['request']['dryRun'] == dry and row['status'] == 200 for row in data['results'])
    assert sum(row['body']['data']['affected'] for row in data['results']) == 251
baseline = read_json(E / 'f04/ui-pg-dryrun.json')
applied = read_json(E / 'f04/ui-pg-applied.json')
restore_preview = read_json(E / 'f04/ui-pg-restore-preview.json')
restored = read_json(E / 'f04/ui-pg-restored.json')
assert [row['target_template_count'] for row in [baseline, applied, restore_preview, restored]] == [0, 251, 251, 0]
fields = ['control_action_direction', 'op_output_lower_limit', 'op_output_upper_limit', 'dcs_model_id',
          'engineering_template_id', 'engineering_template_version']
before = {row['id']: [row[key] for key in fields] for row in baseline['rows']}
after = {row['id']: [row[key] for key in fields] for row in restored['rows']}
assert len(before) == len(after) == 251 and before == after
pin = cli('f04/cli/24-restore-rebind-pin.json')
entry = pin['snapshotRead']['body']['data']['items'][0]
assert pin['snapshotRead']['status'] == 200 and entry['before']['engineering_template_version'] == 1
assert entry['after']['engineering_template_version'] is None
assert entry['before']['engineering_template_id'] != entry['after']['engineering_template_id']
assert [row['request']['dryRun'] for row in pin['results']] == [True, False]
assert all(row['status'] == 200 and row['body']['data']['affected'] == 1 for row in pin['results'])
selection = cli('f04/cli/26-history-selection-invalidation.json')
assert selection['counts'] == {'a': '1', 'b': '0'} and selection['previewDisabled'] and '0 条' in selection['denominator']
multi = cli('f04/cli/27-final-multiple-snapshot-rows.json')
assert multi['consoleErrors'] == []
post = [row for row in multi['results'] if row['method'] == 'POST']
assert len(post) == 2 and all(row['status'] == 200 and row['request']['dryRun'] for row in post)
items = [item for row in post for item in row['body']['data']['results']]
assert len(items) == 2 and items[0]['loopId'] == items[1]['loopId']
assert collections.Counter(item['status'] for item in items) == {'CONFLICT': 1, 'ALREADY_RESTORED': 1}
register(E / 'f04/final-ui-restore.png')
result['F04RawChecks'] = {'applyBatches': [200, 51, 200, 51], 'restoreExactFields': 6,
                         'restoreExactLoops': 251, 'pinRestored': True, 'selectionInvalidated': True,
                         'sameLoopSnapshotStatuses': [item['status'] for item in items], 'consoleErrors': []}

cas = cli('f07/cli/51-fixed-real-409.json')
assert [row['status'] for row in cas['results']] == [409, 200]
assert [row['request']['expectedRevision'] for row in cas['results']] == [0, 2]
assert cas['results'][0]['body']['data']['currentRevision'] == 2 and cas['results'][1]['body']['data']['revision'] == 3
assert cas['results'][0]['request']['requestId'] != cas['results'][1]['request']['requestId']
assert cas['star'] == '取消关注'
accounts = cli('f07/cli/53-final-spa-account-boundary.json')
assert accounts == {'oldAccountState': '关注该回路', 'newAccountState': '取消关注该回路',
                    'boundary': 'same SPA; no reload between logout and new login'}
security = read_json(E / 'f07/real-account-security-final.json')
assert security['count_by_user'] == {'ic_engineer': 1, 'audit_peer': 0, 'audit_restricted': 0}
assert any(row['status'] == 401 and row['path'] == '/me/loop-follows' for row in security['records'])
assert any(row['status'] == 403 and row['method'] == 'PUT' for row in security['records'])
result['F07RawChecks'] = {'httpStatuses': [409, 200], 'expectedRevisions': [0, 2], 'finalRevision': 3,
                         'sameSpaAccounts': accounts, 'counts': security['count_by_user'], 'anonymous401': True,
                         'scope403': True, 'scope': 'Review of independently recorded wire evidence; reviewer authored F07 and does not sign its independent technical acceptance.'}

hygiene = read_json(E / 'final-hygiene.json')
assert hygiene['all_checks_passed'] and all(hygiene['checks'].values())
result['finalHygieneChecks'] = hygiene['checks']
result['overall'] = 'PASS within final documentation/evidence-chain review; execution package remains BLOCKED on F02/F03 stated runtime/CI requirements.'
result['pending'] = ['Top-level new evidence manifest will be generated after this receipt; not claimed verified by this invocation.']
(OWN / 'review-results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({'overall': result['overall'], 'registeredFiles': len(result['files']),
                  'patchRecovery': result['independentPatchRecovery'], 'backendTotal': 7431,
                  'frontendTests': 1021, 'hygieneChecks': len(hygiene['checks'])}, ensure_ascii=False))
