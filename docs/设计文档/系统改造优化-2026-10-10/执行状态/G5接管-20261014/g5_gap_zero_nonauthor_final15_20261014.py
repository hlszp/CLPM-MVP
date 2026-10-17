import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

sys.dont_write_bytecode = True
os.environ.update(G5_AUDIT_SOURCE_ROOT='/private/tmp/clpm-g5-followup-20261014',CLPM_G5_TRACE='0')
sys.path.insert(0,'/private/tmp')
import g5_v2_zero_frozen_probe_20261014 as p

async def main():
    branch_results = {}
    for version, expected in [('pre_v2',0.0),('pre_v1',1.0),(None,1.0),('MISSING',1.0)]:
        kwargs = dict(tag_group='BASE',loop_valid_rate=0.0,validity={'pv_valid':[True]*10},point_count=10)
        if version!='MISSING':
            kwargs['preprocess_version'] = version
        value = p.kpi._compute_loop_valid_rate_from_bundles([SimpleNamespace(data_block=SimpleNamespace(**kwargs))])
        assert value==expected
        branch_results[str(version)] = value
    cases=[]
    for i in (952,953):
        c = await p.one_case(i,'gap',registered_gap='whole_window')
        assert c['base']['valid_rate']==0.0 and c['base']['confidence']=='E'
        assert c['base']['pv_invalid']==0 # retained Good numeric values
        assert c['result']['status']=='INCONCLUSIVE' and c['result']['score'] is None
        assert float(c['persisted']['valid_rate'])==0.0
        assert all(q['pv_known_slots']==0 and q['pv_unknown_slots']==q['expected_slots'] for q in c['queries'])
        cases.append(c)
    after={f:hashlib.sha256((p.ROOT/f).read_bytes()).hexdigest() if (p.ROOT/f).exists() else None for f in p.SOURCE_FILES}
    assert p.SOURCE_HASHES_BEFORE==after
    assert p.NETWORK_ATTEMPTS==[]
    result={'status':'PASS','branch_oracle':branch_results,'whole_unknown_kpi_cases':cases,'source_stable':True,'source_sha256':after,'network_attempts':[]}
    Path('/private/tmp/g5-gap-zero-nonauthor-final15-20261014.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str))
    print('PASS: 4 version/missing branches; 2 real Pipeline/full-KPI whole-UNKNOWN cases; no network; stable source')

asyncio.run(main())
