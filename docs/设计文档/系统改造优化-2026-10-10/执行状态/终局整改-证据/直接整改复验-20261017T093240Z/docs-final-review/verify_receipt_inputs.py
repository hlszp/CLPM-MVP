import collections
import datetime
import hashlib
import json
from pathlib import Path

E = Path('/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/终局整改-证据/直接整改复验-20261017T093240Z')
OWN = E / 'docs-final-review'
result = {'observedAt': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'files': {}}


def read(relative):
    path = E / relative
    result['files'][relative] = {'bytes': path.stat().st_size, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    return path.read_text()


for relative in ['f02/non-author/receipt.md', 'f02/non-author-v3/receipt.md',
                 'f03/non-author/receipt.md', 'f04/non-author/receipt.md',
                 'docs-non-author/receipt.md', 'f07/phase2/scratch-cleanup.json']:
    read(relative)
f01 = json.loads(read('f01/f01-run-results.json'))
assert [item['exit_code'] for item in f01] == [0, 4, 0, 1]
for item in f01:
    for stream in ['stdout', 'stderr']:
        path = 'f01/' + item['name'] + '.' + stream + '.log'
        read(path)
        assert result['files'][path]['sha256'] == item[stream + '_sha256']
assert '19 passed' in read('f01/f01-targeted-19.stdout.log')
assert '3 passed' in read('f01/f01-forward-restored.stdout.log')
assert '1 failed' in read('f01/f01-reverse-old-behavior.stdout.log')
mutation = json.loads(read('f01/f01-mutation-restoration.json'))
assert mutation['original_sha256'] == mutation['restored_sha256'] != mutation['removed_sha256']
result['F01'] = {'recordedRCs': [0, 4, 0, 1], 'forward19': True, 'restored3': True, 'oldBehaviorFails': True}

history = json.loads(read('f02/non-author/historical-independent-check.json'))
assert history['historicalSelected'] == {'BARRIER_PASSED': 6, 'TERMINAL_MISMATCH': 3}
assert history['rootCauseCount'] == 24 and not history['newCapacityRunExecuted']
assert len(history['rootCauseRows']) == 24
result['F02'] = {'historicalSelected': history['historicalSelected'], 'rootCauseCount': 24,
                 'capacityUnverified': True, 'evidenceScope': 'Cross-check of prior independent receipt and recorded result, not new capacity execution.'}

contract = json.loads(read('f03/non-author/contract.command.json'))
assert contract['exitCode'] == 0
output = read('f03/non-author/contract.stdout.log')
assert '# tests 8' in output and '# pass 8' in output and '# fail 0' in output and '# skipped 0' in output
negative = {}
for name, expected in [('unreachable', 2), ('failed', 1), ('all-skipped', 3), ('part-skipped', 3)]:
    record = json.loads(read('f03/non-author/contract-independent/' + name + '/command.json'))
    assert record['code'] == expected
    read('f03/non-author/contract-independent/' + name + '/gate.stdout.log')
    read('f03/non-author/contract-independent/' + name + '/gate.stderr.log')
    negative[name] = expected
structured = json.loads(read('f03/non-author/structured-reports-results.json'))
assert len(structured) == 11 and all(item['passed'] == item['expected'] for item in structured)
result['F03'] = {'recordedNegativeRCs': negative, 'structuredCases': 11,
                 'scope': 'Checks independently recorded gate-contract results; reviewer authored F03 and does not re-sign independent code acceptance. Business and remote CI remain BLOCKED.'}

core = json.loads(read('f04/non-author/api-pg-results.json'))
explicit = json.loads(read('f04/non-author/explicit-template-api-results.json'))
assert len(core['requests']) == 62 and len(core['checks']) == 43
assert len(explicit['requests']) == 11 and len(explicit['checks']) == 6
assert core['status'] == explicit['status'] == 'PASS'
assert all(item['passed'] for item in core['checks'] + explicit['checks'])
result['F04PriorReceipt'] = {'requests': 73, 'checks': 49, 'allChecksPassed': True}

first = json.loads((OWN / 'review-results.json').read_text())
changed = []
for path, old in first['files'].items():
    current = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    if current != old['sha256']:
        changed.append(path)
assert not changed, changed
result['firstReviewedInputsStillStable'] = True
(OWN / 'additional-input-results.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({'additionalInputs': len(result['files']), 'F01RecordedRCs': [0, 4, 0, 1],
                  'F02History': '6+3/24', 'F03Contract': '8/8+11 variants',
                  'F04RecordedHTTPChecks': '73/49', 'priorInputStability': True}, ensure_ascii=False))
