"""Independent input IO contract; no product calculator or actual database/network."""

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

BASE = Path(__file__).resolve().parent.parent
CURRENT = Path('/Users/zhangping/DEV/clpm-remediation-20261017/backend/app/services/calc_snapshot.py')


@pytest.fixture
def subject(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError('No network or real DB is allowed')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)
    path = Path(os.environ.get('INDEPENDENT_DECIMAL_SOURCE', CURRENT))
    spec = importlib.util.spec_from_file_location('independent_decimal_subject', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'snapshot_root', lambda: tmp_path / 'snapshots')
    monkeypatch.setattr(module, 'executable_build_digest', lambda: 'independent-fixed-build')
    return module


class MemoryDB:
    def __init__(self):
        self.calls, self.row = [], None
    async def execute(self, statement):
        self.calls.append(type(statement).__name__)
        if type(statement).__name__ == 'Insert':
            params = statement.compile().params
            self.row = SimpleNamespace(**params, created_at=datetime.now(timezone.utc).replace(tzinfo=None), cleaned_at=None)
        return SimpleNamespace(scalar_one_or_none=lambda: self.row)


async def save(subject, db, extra):
    return await subject.save_calculation_snapshot(db, kind='KPI_GRID', ts_start=datetime(2020, 1, 1), ts_end=datetime(2020, 1, 1, 1), dataset_ref='independent:decimal', arrays={'samples': np.array([1.0, 2.0]), 'valid': np.array([True, False])}, manifest_extra=extra, loop_id='independent-loop')


def independent_hash(manifest, arrays):
    core = {k: v for k, v in manifest.items() if k not in {'createdAt', 'createdByRecordId'}}
    h = hashlib.sha256(b'clpm-calc-snapshot-v1\0')
    h.update(json.dumps(core, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode())
    for key in sorted(arrays):
        array = np.ascontiguousarray(arrays[key])
        h.update(b'\0' + key.encode())
        h.update(f'|{array.dtype.str}|{array.shape}|'.encode())
        h.update(array.tobytes())
    return h.hexdigest()


@pytest.mark.asyncio
async def test_actual_four_type_producer_whole_manifest_roundtrip(subject):
    from app.models.loop_config import LoopTypeWeight
    from app.services.loop_config import get_loop_type_weights_map
    raw = BASE / 'f03/decimal-snapshot-remediation/baseline/input-policy-before.stdout.log'
    policies = [json.loads(line, parse_float=Decimal) for line in raw.read_text().splitlines() if line.startswith('{')]
    originals = next(x['rows'] for x in policies if x['section'] == 'loop-type-weights-input')
    assert len(originals) == 4
    orm = [LoopTypeWeight(loop_type=r['loop_type'], weight_a=r['weight_a'], weight_f=r['weight_f'], weight_s=r['weight_s']) for r in originals]
    producer_db = MagicMock()
    producer_db.execute = AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: orm)))
    produced = await get_loop_type_weights_map(producer_db)
    expected = {r['loop_type']: {k: float(r[k]) for k in ('weight_a', 'weight_f', 'weight_s')} for r in originals}
    assert all(type(v) is Decimal for weights in produced.values() for v in weights.values())
    extra = {'loopId': 'independent-loop', 'source': {'effectiveParams': {'typeWeights': produced, 'zero': Decimal('-0.00')}}, 'replayContext': {'weights': {'a': Decimal('0.20')}, 'nested': (Decimal('0.5'), None)}}
    db = MemoryDB()
    row = await save(subject, db, extra)
    loaded = await subject.load_snapshot_for_replay(db, row.id)
    assert loaded.manifest['source']['effectiveParams']['typeWeights'] == expected
    assert all(type(v) is float for weights in loaded.manifest['source']['effectiveParams']['typeWeights'].values() for v in weights.values())
    assert loaded.manifest['replayContext'] == {'weights': {'a': 0.2}, 'nested': [0.5, None]}
    assert loaded.manifest['source']['effectiveParams']['zero'].hex() == (-0.0).hex()
    assert independent_hash(loaded.manifest, loaded.arrays) == row.input_hash
    assert all(type(v) is Decimal for weights in produced.values() for v in weights.values())


@pytest.mark.asyncio
@pytest.mark.parametrize('value', [Decimal('NaN'), Decimal('sNaN'), Decimal('Infinity'), Decimal('-Infinity'), Decimal('1e9999'), float('nan'), float('inf'), float('-inf'), object()])
async def test_rejected_metadata_precedes_db_and_any_npz(subject, value):
    db = MemoryDB()
    with pytest.raises((ValueError, TypeError)):
        await save(subject, db, {'source': {'effectiveParams': {'nested': [{'number': value}]}}})
    assert db.calls == []
    assert not subject.snapshot_root().exists()


def test_complete_valid_native_json_identity_unchanged(subject):
    source = {'createdAt': 'volatile', 'createdByRecordId': 'volatile', 'arrays': {}, 'a': [None, True, False, 0, 9007199254740993, -0.0, 1e-100, '中文'], 'b': {'empty': {}, 'x': [1, 2]}}
    expected = json.dumps(source, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    assert subject._canonical_json(source) == expected
    assert subject.compute_input_hash(source, {}) == independent_hash(source, {})


def test_decimal_identity_is_explicit_numeric_fix_not_old_string_equivalence(subject):
    manifest = {'nested': [{'a': Decimal('0.20'), 'zero': Decimal('-0.00')}]}
    numeric = {'nested': [{'a': 0.2, 'zero': -0.0}]}
    old_string = {'nested': [{'a': '0.20', 'zero': '-0.00'}]}
    assert subject.compute_input_hash(manifest, {}) == independent_hash(numeric, {})
    assert subject.compute_input_hash(manifest, {}) != subject.compute_input_hash(old_string, {})
