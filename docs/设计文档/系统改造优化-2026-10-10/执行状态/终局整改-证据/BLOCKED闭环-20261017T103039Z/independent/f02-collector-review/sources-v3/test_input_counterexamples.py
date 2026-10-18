"""Author checks for fixed independent input-audit counterexamples, before and after."""
from copy import deepcopy
from audit_capacity_inputs import evaluate
from test_evidence_tools import fixture_rows

def test_1001_manifest_rows_is_rejected_even_with_1000_unique_tags():
    manifest,loops,mappings,summary=fixture_rows()
    manifest['rows'].append(deepcopy(manifest['rows'][0]))
    failures,_=evaluate(manifest,loops,mappings,summary)
    assert any('Manifest row' in failure for failure in failures)

def test_insufficient_pv_all_good_is_rejected_not_promoted_to_input_pass():
    manifest,loops,mappings,summary=fixture_rows()
    manifest['rows'][0]['composition']='insufficient'
    failures,_=evaluate(manifest,loops,mappings,summary)
    assert any('quality histogram' in failure for failure in failures)

def test_bounded_count_cannot_be_lowered_in_manifest_to_suit_actual():
    manifest,loops,mappings,summary=fixture_rows()
    manifest['rows'][0]['pointsPerTag']['PV']=10
    summary[0]['row_count']=10
    failures,_=evaluate(manifest,loops,mappings,summary)
    assert any('manifest count differs' in failure for failure in failures)

def test_duplicate_loop_role_bindings_cannot_overwrite():
    manifest,loops,mappings,summary=fixture_rows()
    mappings.append(deepcopy(mappings[0]))
    failures,_=evaluate(manifest,loops,mappings,summary)
    assert any('Duplicate loop/role binding' in failure for failure in failures)

def test_explicit_utc_window_end_is_accepted_without_removing_upper_bound():
    from audit_capacity_inputs import window
    from datetime import datetime
    start,end=window({'windowStart':'2026-10-15T12:00:00Z','windowEnd':'2026-10-15T13:00:00+00:00'})
    assert start==datetime(2026,10,15,12) and end==datetime(2026,10,15,13)
