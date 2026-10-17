import hashlib
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, '/private/tmp')
os.environ['G5_AUDIT_SOURCE_ROOT'] = '/private/tmp/clpm-g5-followup-20261014'
os.environ['CLPM_G5_TRACE'] = '0'
import g5_v2_candidate_probe_20261014 as probe
import pytest

async def held_defaults(step):
    return {'fast': max(3.0*step,60.0), 'slow': max(3.0*step,7200.0)}

tests = [
    'tests/test_g5_gap_coverage_regression.py',
    'tests/test_g5_kpi_zero_coverage.py',
    'tests/test_g5_trace.py',
    'tests/test_generate_capacity_dataset.py',
    'tests/test_kpi_r14_sparse_admission.py',
    'tests/test_held_mask_r2r3.py',
    'tests/test_held_tiered_r1.py',
    'tests/test_p3_08bc_time_quality.py',
    'tests/test_preprocessing/test_pipeline.py',
    'tests/test_preprocessing/test_quality_summary.py',
]
files = probe.SOURCE_FILES + ['backend/'+x for x in tests]
def hashes():
    return {f:hashlib.sha256((probe.ROOT/f).read_bytes()).hexdigest() for f in files}
before = hashes()
os.chdir(str(probe.ROOT/'backend'))
class EmptyRows:
    def all(self):
        return []
async def metadata_defaults(_stmt):
    return EmptyRows()
probe.lwb._held_cfg_cache.clear()
probe.lwb._held_cfg_read_at = 0.0
with patch.object(probe.lwb,'_metadata_execute',metadata_defaults):
    rc = pytest.main(tests + ['-q','-p','no:cacheprovider','--basetemp=/private/tmp/g5-nonauthor-pytest-frozen-20261014'])
after = hashes()
record = {'return_code':int(rc),'network_attempts':probe.NETWORK_ATTEMPTS,'source_stable':before==after,'sha256_before':before,'sha256_after':after}
Path('/private/tmp/g5-gap-nonauthor-tests-frozen-20261014.json').write_text(json.dumps(record,indent=2))
print('OUTBOUND_NETWORK_ATTEMPTS='+str(len(probe.NETWORK_ATTEMPTS)))
print('SOURCE_AND_TESTS_STABLE='+str(before==after))
sys.exit(3 if probe.NETWORK_ATTEMPTS or before!=after else int(rc))
