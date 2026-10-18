"""Offline checks of independent collector/auditor safety and counterexamples."""
from datetime import datetime
import gzip
import hashlib
import json

import pytest

from audit_capacity_inputs import evaluate, expected_composition, expected_quality_histogram, point_summary_sql, private_config, rows_of
from collect_capacity_evidence import capture_log, store, task_children


def fixture_rows():
    manifest={'scenario':'a','rows':[]};loops=[];mappings=[];summary=[]
    for index in range(1000):
        lid=f'loop-{index}';tag=f'PERF-T{index+1:04}';composition='normal'
        manifest['rows'].append({'loopTag':tag,'composition':composition,'pointsPerTag':{'PV':3600,'OP':3600,'SP':'COV_sparse','MODE':1}})
        loops.append({'id':lid,'tag_name':tag,'is_active':True,'status':'READY','include_in_evaluation':True})
        for role in ('PV','SP','OP','MODE','PID_P','PID_I','PID_D'):
            point=f'{lid}-{role}';count=3600 if role in ('PV','OP') else 3 if role=='SP' else 1
            mappings.append({'loop_id':lid,'tag_role':role,'tag_id':point})
            summary.append({'point_id':point,'quality_class':1,'row_count':count,'min_value':1,'max_value':1})
    return manifest,loops,mappings,summary


def test_real_private_db_and_both_half_open_bounds_are_required():
    sql=point_summary_sql('clpm_capacity_test',datetime(2026,10,15,12),datetime(2026,10,15,13))
    assert 'FROM clpm_capacity_test.st_point_data_v1' in sql
    assert "ts >= '2026-10-15T12:00:00.000Z'" in sql
    assert "ts < '2026-10-15T13:00:00.000Z'" in sql
    with pytest.raises(ValueError):point_summary_sql('clpm_ts;DROP',datetime(2026,10,15),datetime(2026,10,16))


def test_shared_database_is_refused_before_network():
    with pytest.raises(ValueError):private_config({'POSTGRES_DB':'clpm','TDENGINE_DB':'clpm_ts'})
    private_config({'POSTGRES_DB':'clpm_capacity_test','TDENGINE_DB':'clpm_capacity_test'})


def test_full_7000_point_denominator_accepts_complete_real_shape():
    failures,details=evaluate(*fixture_rows())
    assert failures==[] and len(details)==7000


def test_one_missing_point_fails_even_when_6999_exist():
    manifest,loops,mappings,summary=fixture_rows();summary.pop()
    failures,_=evaluate(manifest,loops,mappings,summary)
    assert any('point set mismatch' in failure for failure in failures)


def test_later_window_data_cannot_fill_this_windows_empty_summary():
    manifest,loops,mappings,_=fixture_rows()
    failures,_=evaluate(manifest,loops,mappings,[])
    assert any('missing=7000' in failure for failure in failures)
    assert len([failure for failure in failures if 'bounded count' in failure])==7000


def test_insufficient_bad_quality_is_distinct_from_query_failure():
    manifest,loops,mappings,summary=fixture_rows();manifest['scenario']='b';new_summary=[]
    for index,row in enumerate(manifest['rows'],1):
        composition=expected_composition('b',index);row['composition']=composition
        row['pointsPerTag']['PV']=row['pointsPerTag']['OP']=900 if composition=='gap' else 3600
    for row in summary:
        index=int(row['point_id'].split('-')[1])+1;role=row['point_id'].split('-',2)[2]
        composition=expected_composition('b',index)
        for quality,count in expected_quality_histogram(index,composition,role).items():
            point={**row,'quality_class':quality,'row_count':count}
            if role=='MODE' and composition=='manual':point['min_value']=point['max_value']=0
            new_summary.append(point)
    summary=new_summary
    failures,_=evaluate(manifest,loops,mappings,summary)
    assert failures==[]
    with pytest.raises(RuntimeError,match='1850'):rows_of({'code':1850,'desc':'Query memory exhausted'})


def test_ineligible_loop_cannot_be_silently_excluded():
    manifest,loops,mappings,summary=fixture_rows();loops[-1]['include_in_evaluation']=False
    failures,_=evaluate(manifest,loops,mappings,summary)
    assert 'Ineligible object in frozen 1000' in failures


def test_normal_quality_failure_is_not_relabelled_insufficient():
    manifest,loops,mappings,summary=fixture_rows();summary[0]['quality_class']=0
    failures,_=evaluate(manifest,loops,mappings,summary)
    assert any('unexpected non-GOOD' in failure for failure in failures)


def test_content_addressed_raw_objects_are_restorable_and_reused(tmp_path):
    (tmp_path/'objects').mkdir();raw=[{'id':'x','status':'FAILED','payload':{'error':'original'}}]
    first=store(tmp_path,'records',raw);second=store(tmp_path,'records',raw)
    assert first==second and len(list((tmp_path/'objects').iterdir()))==1
    restored=gzip.decompress((tmp_path/first['file']).read_bytes())
    assert hashlib.sha256(restored).hexdigest()==first['rawSha256']
    assert json.loads(restored)==raw


def test_children_use_backend_trail_not_unrelated_result_uuid():
    child='11111111-1111-4111-8111-111111111111';other='22222222-2222-4222-8222-222222222222'
    assert task_children({'children':[[[child,None],None]],'result':{'loopId':other}})=={child}


def test_fast_node_request_survives_log_interval_capture(tmp_path):
    parent='11111111-1111-4111-8111-111111111111';child='22222222-2222-4222-8222-222222222222'
    log=tmp_path/'worker.log';out=tmp_path/'evidence';out.mkdir()
    log.write_text("TaskPool: Apply app.tasks.kpi_calc.calculate_node_kpi_hourly {'id': '"+child+"', 'root_id': '"+parent+"', 'parent_id': '"+parent+"', 'kwargsrepr': 'same window'}\n")
    children=set();position,record=capture_log(log,out,{'inode':log.stat().st_ino,'offset':0},parent,children)
    assert children=={child} and position['offset']==log.stat().st_size
    assert (out/record['file']).read_bytes()==log.read_bytes()
    assert 'node-request' in (out/'task-evidence.jsonl').read_text()
