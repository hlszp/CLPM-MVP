import json
import math
from pathlib import Path

b=json.loads(Path('/private/tmp/g5-gap-nonauthor-baseline-full-r2-20261014.json').read_text())
c=json.loads(Path('/private/tmp/g5-gap-nonauthor-final-20261014.json').read_text())
t=json.loads(Path('/private/tmp/g5-gap-nonauthor-tests-r2-20261014.json').read_text())
z=json.loads(Path('/private/tmp/g5-gap-zero-nonauthor-20261014.json').read_text())
checks=[]
for old,new in zip(b['cases'],c['cases'],strict=True):
    assert (old['i'],old['composition'])==(new['i'],new['composition'])
    for key in ('series_sha256','base_mask_summary','bundle_mask_summary','config_revision'):
        assert old[key]==new[key], (new['i'],key)
    for key in ('source','algorithm_version'):
        assert old['persisted'][key]==new['persisted'][key]
    if new['composition']!='gap':
        for key in ('persisted','metrics_detail','base'):
            assert old[key]==new[key], (new['i'],key)
    else:
        assert new['result']['status']=='INCONCLUSIVE' and new['result']['score'] is None
        assert new['persisted']['confidence_level']=='E'
        expected_known={1:960,2:480,5:192}[new['base_interval_s']]
        if new['registered_gap']:
            expected_known={1:900,2:450,5:180}[new['base_interval_s']]
        q=next(q for q in new['queries'] if q['interval_s']==new['base_interval_s'] and 'sp' in q['roles'])
        assert q['ctx_period']==new['base_interval_s']
        assert q['pv_known_slots']==expected_known
        assert q['pv_known_slots']+q['pv_unknown_slots']==q['expected_slots']
        assert math.isclose(q['pv_source_coverage_ratio'],expected_known/q['expected_slots'])
        assert math.isclose(new['base']['valid_rate'],expected_known/q['expected_slots'])
    checks.append({'i':new['i'],'class':new['composition'],'min_ratio':new['min_observed_ratio'],'registered_gap':new['registered_gap'],'matches':new['matches_independent_expected'],'status':new['result']['status'],'score':new['result']['score'],'valid_rate':new['base']['valid_rate']})
assert b['source_stable_during_probe'] and c['source_stable_during_probe']
assert len(checks)==22 and all(x['matches'] for x in checks)
assert t['source_stable'] and t['return_code']==0 and not t['network_attempts']
assert z['source_stable'] and z['status']=='PASS'
for f,h in c['source_files_sha256'].items():
    assert t['sha256_after'][f]==h, f
    assert z['source_sha256'][f]==h, f
r={'status':'PASS','main_samples':17,'counterfactuals':5,'same_input_hashes':22,'same_base_mask_hashes':22,'same_bundle_mask_hashes':22,'same_config_revision':22,'source_algorithm_identity_same':22,'normal_bad_manual_persist_and_metrics_equivalent':13,'source_stable_baseline':True,'source_stable_final_candidate':True,'tests_and_zero_oracle_use_same_source_hashes':True,'source_head':c['source_head'],'candidate_hashes':c['source_files_sha256'],'generator_versions':{'baseline':b['cases'][0]['generator_version'],'candidate':c['cases'][0]['generator_version']},'checks':checks}
Path('/private/tmp/g5-gap-nonauthor-final-comparison-20261014.json').write_text(json.dumps(r,ensure_ascii=False,indent=2))
print(json.dumps({k:v for k,v in r.items() if k not in ('checks','candidate_hashes')},ensure_ascii=False))
print(json.dumps(r['candidate_hashes'],indent=2))
