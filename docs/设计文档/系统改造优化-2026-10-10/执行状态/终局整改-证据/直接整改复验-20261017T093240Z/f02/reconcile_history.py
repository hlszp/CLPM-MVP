"""Rebuild historical selected 6+3 matrix and join recoverable root evidence.

Reads saved reports/SELECT output only. No application import or task submission.
An historical failure returns rc=1; this is not a new capacity benchmark.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
REPORTS = Path('/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/G5-D01-04-实证-20261015/D02-batches')
SELECTED = ('1', '2', '3', '4', '5', '6', '7', '9pp', '10')
EXCLUDED = ('8', '8p', '9', '9p')


def main():
    reports = []
    for batch in (*SELECTED, *EXCLUDED):
        path = REPORTS / f'batch{batch}-report.json'
        report = json.loads(path.read_text())
        reports.append({
            'batch': batch, 'selected': batch in SELECTED, 'source': str(path),
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'parentTaskId': report['parentTaskId'], 'window': report['window'],
            'completionReason': report['completionReason'], 'gateOk': report['gate']['ok'],
            'denominators': report['gate']['denominators'],
            'conditions': report['gate']['conditions'],
            'violations': {k: v['count'] for k, v in report['gate']['violations'].items()},
        })
    selected = [r for r in reports if r['selected']]
    historical = Counter(r['completionReason'] for r in selected)
    assert len(selected) == 9 and all(r['denominators']['frozenEligible'] == 1000 for r in selected)
    assert historical == {'BARRIER_PASSED': 6, 'TERMINAL_MISMATCH': 3}
    recovered = json.loads((HERE / 'readonly-db-recovery.json').read_text())
    warnings = json.loads((HERE / 'worker-selected-lines.json').read_text())
    td = json.loads((HERE / 'readonly-td-source-recovery.json').read_text())
    node = json.loads((HERE / 'node-arrival-reconciliation.json').read_text())
    root_rows = []
    for batch in recovered['batches']:
        number = batch['batch']
        assert batch['manifestSha256'] == batch['manifestFrozenSha256']
        assert sum(r['count'] for r in batch['exactRunCounts']) == 1000
        inputs = {r['pointId']: r for b in td['batches'] if b['batch'] == number for r in b['points']}
        for failure in batch['exactRunFailures']:
            detail = failure['fitness_detail']
            assert detail['reason'] == 'DataPlanner取数失败'
            assert detail['error'] == '[0x073a]: Query memory exhausted'
            assert failure['dataset_snapshot_id'] is None
            # Original immutable record time is naive UTC; worker file uses UTC+08.
            record_at = datetime.fromisoformat(failure['created_at']) + timedelta(hours=8)
            candidates = [r for r in warnings if f"回路 {failure['tag_name']}）" in r['text'] and '取数失败' in r['text']]
            matched = min(candidates, key=lambda r: abs((datetime.strptime(r['text'][:23], '%Y-%m-%d %H:%M:%S,%f') - record_at).total_seconds()))
            log_at = datetime.strptime(matched['text'][:23], '%Y-%m-%d %H:%M:%S,%f')
            assert abs((log_at-record_at).total_seconds()) < 2
            point_rows = []
            for binding in batch['historicalBindingsForWindow']:
                if binding['loop_id'] != failure['loop_id'] or binding['tag_role'] not in ('PV','OP','SP','MODE'):
                    continue
                current = inputs[binding['tag_id']]
                point_rows.append({
                    'role': binding['tag_role'], 'pointId': binding['tag_id'],
                    'currentSummary': current['summary']['response'],
                    'currentQuality': current['quality']['response'],
                })
            root_rows.append({
                'batch': number, 'parentTaskId': batch['parentTaskId'], 'window': batch['window'],
                'loopTag': failure['tag_name'], 'loopId': failure['loop_id'],
                'recordId': failure['record_id'], 'recordCreatedAtUtc': failure['created_at'],
                'recordAlgorithmVersion': failure['record_algorithm_version'],
                'payloadAlgorithmVersion': failure['payload_algorithm_version'],
                'configRevision': failure['config_revision'], 'inputSnapshotId': failure['dataset_snapshot_id'],
                'fitnessDetail': detail, 'log': matched,
                'logToRecordSeconds': (record_at-log_at).total_seconds(),
                'provenCause': 'TDengine query resource failure mislabeled DATA_INSUFFICIENT',
                'currentSourcePoints': point_rows,
                'coverageBoundary': 'Current source query cannot reconstruct original as-of-run input. Source SELECT errors remain errors, never inferred empty data.',
            })
    assert Counter(r['batch'] for r in root_rows) == {4: 1, 5: 22, 7: 1}
    out = {
        'generatedAt': datetime.now(timezone.utc).isoformat(),
        'mode': 'historical reconciliation and immutable evidence recovery only',
        'newCapacityRunExecuted': False,
        'historicalSelectedCount': len(selected), 'historicalStatusCounts': dict(historical),
        'history': reports, 'rootCauseRows': root_rows, 'nodeArrival': node,
        'sourceWarningCountInSampledTimeRanges': sum('DataPlanner 取数失败' in r['text'] for r in warnings),
        'exactRunResourceFailureCount': len(root_rows),
        'concurrencyEvidence': 'concurrent-hourly-task-lines.json: scheduled root 8f04f04c overlaps batch4 and batch5; temporal overlap is not a proof of exclusive cause',
        'rootCauseExcludedClaims': ['data coverage proven insufficient', 'calculation/query code excluded as a cause', 'all nine selected batches passed', 'nodeMissing=16 means persistent node loss'],
        'remaining': ['actual TD query memory pressure cause/budget remediation under Keeper ownership', 'fixed source identity plus candidate Celery standard-hourly 1000-loop full nine-condition benchmark', 'non-author verification of candidate implementation and later capacity raw evidence'],
    }
    path = HERE / 'root-cause-matrix.json'
    path.write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'selected': len(selected), 'historical': dict(historical), 'exactRunResourceFailureCount': len(root_rows), 'newCapacityRunExecuted': False},ensure_ascii=False))
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
