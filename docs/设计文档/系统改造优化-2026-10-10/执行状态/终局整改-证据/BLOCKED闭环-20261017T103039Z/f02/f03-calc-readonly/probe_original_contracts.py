"""AST-load actual frozen pure functions; no application imports or live IO."""
from __future__ import annotations

import argparse
import ast
import asyncio
import dataclasses
import hashlib
import io
import json
import sys
import traceback
import types
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from types import SimpleNamespace

import numpy as np

ROOT = Path('/Users/zhangping/DEV/clpm-remediation-20261017')


def actual_functions(relative, names):
    path = ROOT / relative
    raw = path.read_bytes()
    tree = ast.parse(raw, filename=str(path))
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            found[node.name] = node
    assert set(found) == set(names)
    module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)] + [found[n] for n in names], type_ignores=[])
    ast.fix_missing_locations(module)
    print(json.dumps({'source': str(path), 'sourceSha256': hashlib.sha256(raw).hexdigest(), 'functions': {n: [found[n].lineno, found[n].end_lineno] for n in names}}))
    return compile(module, str(path), 'exec')


def npz_probe(metadata):
    ns = {'np': np, 'datetime': datetime, 'timedelta': timedelta}
    exec(actual_functions('backend/app/services/calc_snapshot.py', ['_ns_int', 'serialize_signal', 'serialize_data_blocks']), ns)
    exec(actual_functions('backend/app/services/data_planner.py', ['_fill_limit_metadata']), ns)

    @dataclasses.dataclass
    class Quality:
        sample_count: int = 3

    block = SimpleNamespace(timestamps=[datetime(2026, 10, 17, 11, 0, i) for i in range(3)], signals={'pv': [55.0, None, 55.5]}, validity={'pv': [True, False, True]}, outlier_reasons={}, tag_group='BASE', sampling_freq='1s', config_version='v1', preprocess_version='pre_v1', point_count=3, control_type='STABLE', loop_confidence_level='A', loop_valid_rate=2/3, consecutive_segments=[(0, 1)], quality_summary=Quality(), series_context=None)
    bundle = SimpleNamespace(data_block=block, metric_code='pv_mean', mask_expression='pv_valid', masked_indices=[0, 2], lineage=None)
    if metadata:
        # Actual producer creates these values, not a hand-written serializer input.
        ns['_fill_limit_metadata']([bundle], 'LOOP_FIELD')
    print(json.dumps({'metadataProduced': metadata, 'signals': block.signals}))
    arrays, metas = ns['serialize_data_blocks']([bundle])
    buffer = io.BytesIO()
    np.savez_compressed(buffer, **arrays)
    with np.load(io.BytesIO(buffer.getvalue()), allow_pickle=False) as restored:
        assert restored['blk0__sig_pv'].tolist()[0] == 55.0
        assert bool(restored['blk0__null_pv'][1]) is True
        print(json.dumps({'npzBytes': len(buffer.getvalue()), 'npzSha256': hashlib.sha256(buffer.getvalue()).hexdigest(), 'keys': restored.files, 'metadata': metas}))


async def status_probe():
    class TaskStatus(str, Enum):
        SUCCESS = 'SUCCESS'
        RUNNING = 'RUNNING'
        FAILED = 'FAILED'

    initial = {'status': 'RUNNING', 'progress': '0', 'current_stage': '取数', 'loops_total': '1', 'loops_done': '0', 'celery_task_id': '92c8ce0d-3401-4a8b-bad4-2e1f6d372ac1'}
    persistent = dict(initial)
    update_calls = []
    release_calls = []

    async def update_status(task_id, status, **fields):
        update_calls.append({'taskId': task_id, 'status': status.value, **fields})
        persistent['status'] = status.value
        persistent.update({k: str(v) for k, v in fields.items() if v is not None})
        return dict(persistent)

    async def release(task_id, data):
        release_calls.append(task_id)

    result_module = types.ModuleType('celery.result')
    result_module.AsyncResult = lambda cid, app: SimpleNamespace(state='SUCCESS', result={'total': 1, 'success': 0, 'inconclusive': 1, 'failed': 0})
    celery_module = types.ModuleType('app.tasks.celery_app')
    celery_module.celery_app = object()
    service_module = types.ModuleType('app.services')
    service_module.task_tracker = SimpleNamespace(update_status=update_status)
    sys.modules.update({'celery.result': result_module, 'app.tasks.celery_app': celery_module, 'app.services': service_module})
    ns = {'json': json, 'TaskStatus': TaskStatus, '_TERMINAL_STATUSES': {'SUCCESS', 'FAILED', 'CANCELLED'}, '_now_iso': lambda: '2026-10-17T13:02:10+00:00', '_release_concurrency_slot': release}
    exec(actual_functions('backend/app/api/v1/endpoints/tasks.py', ['_parse_celery_task_ids', '_sync_task_status']), ns)
    observed = dict(initial)
    await ns['_sync_task_status']('ebfc750e-d99a-4ade-882d-73bc4dea54c2', observed)
    print(json.dumps({'initial': initial, 'actualCeleryResultFromWorker': {'total': 1, 'success': 0, 'inconclusive': 1, 'failed': 0}, 'afterSync': observed, 'statusWriterCalls': update_calls, 'slotReleaseCalls': release_calls}, ensure_ascii=False))
    # A completed INCONCLUSIVE loop counts as processed, not as EVALUATED.
    assert observed['loops_done'] == '1', 'actual custom task result processed 1 loop, but completion leaves loops_done=0'
    assert observed['current_stage'] == '完成'


if __name__ == '__main__':
    mode = argparse.ArgumentParser()
    mode.add_argument('mode', choices=['npz-control', 'npz-produced-metadata', 'status-completion'])
    args = mode.parse_args()
    try:
        if args.mode == 'status-completion':
            asyncio.run(status_probe())
        else:
            npz_probe(args.mode == 'npz-produced-metadata')
    except Exception:
        traceback.print_exc()
        sys.exit(1)
