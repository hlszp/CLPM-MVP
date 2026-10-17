"""Independent local evidence audit; no app imports, services or author audit calls."""
from __future__ import annotations
import ast
import hashlib
import json
import math
import uuid
from collections import Counter
from datetime import datetime
from decimal import Decimal
from pathlib import Path
import numpy as np

ROOT = Path('/private/tmp/clpm-g5-followup-20261014')
INPUT = Path('/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/G5接管-20261014/smoke-real')
OUT = Path('/private/tmp/g5-real-smoke-independent-20261014.json')
assert OUT.is_absolute() and OUT.parent.is_dir()
f = json.loads((INPUT/'freeze.json').read_text())
o = json.loads((INPUT/'observed.json').read_text())
checks = []
def check(name, condition, details=None):
    checks.append({'name':name, 'pass':bool(condition), 'details':details})
def digest(value, *, unicode=False):
    return hashlib.sha256(json.dumps(value,sort_keys=True,default=str,
        ensure_ascii=not unicode,separators=(',',':')).encode()).hexdigest()
def time_norm(value):
    return datetime.fromisoformat(value).isoformat()
def near(a,b,tol=1e-6):
    return a is not None and b is not None and abs(float(a)-float(b)) <= tol
def same_value(a,b):
    if a is None or b is None: return a is b
    if isinstance(a,(int,float,Decimal)) or isinstance(b,(int,float,Decimal)):
        try: return near(a,b)
        except (ValueError,TypeError): return a == b
    return a == b

cert = json.loads((INPUT.parent/'runtime-deployment-20261014.json').read_text())
hashes = {name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
          for name in cert['stages'][0]['sourceFiles']}
check('candidate_source_hashes_before',hashes == cert['stages'][0]['sourceFiles'])
check('freeze_file_hash',o['freezeSha256'] == hashlib.sha256((INPUT/'freeze.json').read_bytes()).hexdigest())
check('candidate_sha',f['candidateSha'] == o['clientSourceSha'] == cert['candidateSHA'])
config_keys = ('runtimeOverrides','loopConfigs','metricConfigs','selectedLoopContext',
    'typeWeights','sysConfig','rolePoints','loopTypes','node','effectiveHeldSeconds','environment')
config_hash = digest({k:f['metadata'][k] for k in config_keys})
check('frozen_config_integrity',config_hash == f['metadata']['inputConfigHash'])
check('export_config_drift',config_hash == o['currentInputConfigHash'])
weights_typed = {k:{sub:float(val) for sub,val in values.items()}
                for k,values in f['metadata']['typeWeights'].items()}
revision = 'digest:'+digest({'typeWeights':weights_typed,
    'fitness':f['metadata']['runtimeOverrides']['fitness'] or {}},unicode=True)[:16]
check('loop_config_revision_recomputed',revision == f['expectedConfigRevision'],revision)
check('held_thresholds',f['metadata']['effectiveHeldSeconds'] == {'fast':60.0,'slow':7200.0})
check('base_sampling',f['metadata']['baseSamplingSeconds'] == 5)
check('min_observed_disabled',f['metadata']['runtimeOverrides']['kpiMinObservedRatio'] is None)
check('no_declared_gap_overlap',f['metadata']['gapOverlaps'] == [])
check('future_synthetic_scope',f['synthetic'] and f['futureAtPrepare'] and
    time_norm(f['windowEnd']) > time_norm(f['preparedAt'].split('+')[0]))

# Execute only the immutable pure generator function AST, never its IO module.
source = (ROOT/'backend/scripts/generate_capacity_dataset.py').read_text()
tree = ast.parse(source)
fn = next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='synth_series')
ns = {'np':np,'POINTS':3600}
exec(compile(ast.Module(body=[fn],type_ignores=[]),str(ROOT/'backend/scripts/generate_capacity_dataset.py'),'exec'),ns)
generate = ns['synth_series']
roles = {'PV','SP','OP','MODE','PID_P','PID_I','PID_D'}
loop_rows = {row['loopId']:row for row in o['loops']}
check('five_distinct_loops',len(loop_rows)==len(o['loops'])==5 and set(loop_rows)=={c['loopId'] for c in f['cases']})
case_reports = []
namespace = uuid.uuid5(uuid.NAMESPACE_URL,'clpm:calculation-result-ledger')
row_total = 0
for c in f['cases']:
    label = c['loopTag']; lid = c['loopId']; row = loop_rows[lid]
    series = generate(c['seed'],c['composition'],c['category'],400.0)
    expected_mode = 0 if c['composition']=='manual' else 1
    check(label+':raw_MODE',series['MODE']==[(0,float(expected_mode),1,1)])
    check(label+':generator_series_hash',digest(series)==c['seriesSha256'])
    td = o['tdInputs'][lid]
    check(label+':seven_roles',set(td)==roles and set(c['rawRoleHash'])==roles)
    for role,values in series.items():
        normalized = [[int(t),float(v),int(q),int(raw)] for t,v,q,raw in values]
        regenerated = digest(normalized)
        actual = td[role]
        check(label+':TD_'+role, actual['count']==len(values)==c['roleCounts'][role]
              and actual['normalizedHash']==regenerated==c['rawRoleHash'][role]
              and actual['pointId']==f['metadata']['rolePoints'][lid][role][1]
              and actual['sourceKinds']==[4] and actual['qualitySchemas']==[1])
        row_total += actual['count']
    # Direct inclusive 5s grid oracle from events/60s held policy, independent of Pipeline.
    valid = known = bad = 0
    for t in range(0,3601,5):
        event = series['PV'][min(t,len(series['PV'])-1)]
        is_known = t-event[0] <= 60
        known += int(is_known)
        valid += int(is_known and event[2]==1)
        bad += int(is_known and event[2]!=1)
    oracle_rate = Decimal(valid)/Decimal(721)
    quantized_rate = oracle_rate.quantize(Decimal('0.0001'))
    expected_status = 'INCONCLUSIVE' if c['composition'] in ('insufficient','gap') else 'SUCCESS'
    expected_confidence = 'E' if expected_status=='INCONCLUSIVE' else 'A'
    check(label+':independent_grid_expectation',known==c['expectedKnownSlots'] and valid==c['expectedCoreValidSlots']
          and 721-known==c['expectedUnknownSlots'] and bad==c['expectedRawQualityInvalidSlots'])
    check(label+':unique_record_snapshot',len(row['records'])==len(row['snapshots'])==1)
    r = row['records'][0]; s = row['snapshots'][0]; latest = row['latest']; payload=r['payload']
    logical = str(uuid.uuid5(namespace,'celery:'+c['celeryTaskId']+':'+f['windowStart']))
    check(label+':task_identity_terminal',row['taskId']==c['celeryTaskId'] and row['taskState']=='SUCCESS')
    check(label+':record_identity',r['logical_run_id']==logical==c['logicalRunId'] and r['object_kind']=='LOOP'
          and r['object_id']==lid and r['status']=='COMPLETED')
    check(label+':record_versions',r['config_revision']==revision and r['algorithm_version']=='KPI_CALC_v2.0'
          and r['dataset_snapshot_id'] is None and r['source_record_id'] is None)
    check(label+':shared_record_pointer',s['result_record_id']==latest['result_record_id']==r['id'])
    check(label+':full_window',time_norm(r['ts_start'])==time_norm(s['ts_start'])==time_norm(latest['data_ts_start'])==f['windowStart']
          and time_norm(r['ts_end'])==time_norm(s['ts_end'])==time_norm(latest['data_ts_end'])==f['windowEnd'])
    check(label+':business_status',s['status']==latest['status']==payload['snapshotStatus']==expected_status)
    check(label+':valid_rate',near(s['valid_rate'],quantized_rate,1e-8) and near(latest['valid_rate'],quantized_rate,1e-8)
          and near(payload['valid_rate'],quantized_rate,1e-8))
    check(label+':confidence',s['confidence_level']==latest['confidence_level']==payload['confidence_level']==expected_confidence)
    check(label+':source',s['source']==payload['source']=='MANUAL_STANDARD'
          and s['source_task_id'] is None and payload['source_task_id'] is None)
    for field in ('score','auto_mode_rate','effective_auto_rate','algorithm_version'):
        check(label+':projection_'+field, same_value(s.get(field),payload.get(field)))
    check(label+':latest_core',same_value(s.get('score'),latest.get('score')) and s['algorithm_version']==latest['algorithm_version'])
    lineage=s['data_lineage']
    check(label+':lineage_identity',lineage['tag_group']=='BASE' and lineage['sampling_freq']=='5s'
          and lineage['data_policy_version']=='pre_v2' and lineage['algorithm_version']=='KPI_CALC_v2.0'
          and lineage['quality_policy']=='KEEP_ALL_WITH_VALIDITY'
          and lineage['data_block_ids']==['db_'+lid+'_BASE_5s'])
    if expected_status=='INCONCLUSIVE':
        check(label+':score_is_null',s['score'] is None and payload.get('score') is None and latest['score'] is None)
    else:
        score=float(s['score'])
        check(label+':finite_score',math.isfinite(score) and 0<=score<=100)
        details=payload['composite']['details']
        recalculated=round(float(details['base_score'])*float(s['effective_auto_rate'])/100,2)
        check(label+':composite_multiplier',near(score,recalculated,.005),recalculated)
    if c['composition']=='manual':
        check(label+':manual_valid_negative',float(s['score'])==0 and float(s['auto_mode_rate'])==0
              and float(s['effective_auto_rate'])==0 and s['assess_level']=='L4')
    if c['composition']=='gap':
        check(label+':gap_gate',s['fitness_detail']['gate']['passed'] is False
              and near(s['fitness_detail']['gate']['gapRatio'],529/721,.00005))
    check(label+':latest_monotonic',time_norm(latest['data_ts_end'])>time_norm(f['metadata']['latestBefore'][lid]['end']))
    case_reports.append({'tag':label,'loopId':lid,'taskId':row['taskId'],'rawMode':expected_mode,'score':s['score'],
        'status':s['status'],'validRate':s['valid_rate'],'confidence':s['confidence_level'],'recordId':r['id'],
        'gridOracle':{'slots':721,'known':known,'coreValid':valid,'unknown':721-known,'rawQcBad':bad},
        'lineageRawValidRate':lineage.get('valid_rate'),'compositeValueBeforeGate':payload['composite']['value']})
check('TD_total_30633',row_total==30633,row_total)

node=o['node']; s=node['snapshot']; r=node['record']; payload=r['payload']; stats=s['assessment_stats']; roster=stats['roster']
ids=f['metadata']['node']['eligibleLoopIds']; members=roster['members']; states=Counter(m['state'] for m in members)
check('node_1000_unique_members',len(ids)==len(set(ids))==len(members)==len({m['loopId'] for m in members})==1000
      and {m['loopId'] for m in members}==set(ids))
expected_states={'scorable':3,'incomplete':2,'missing':995,'failed':0,'cancelled':0,'pending':0}
check('node_six_state_partition',{k:states[k] for k in expected_states}==expected_states==roster['states'])
for member in members:
    loop=loop_rows.get(member['loopId'])
    expected='missing' if loop is None else ('scorable' if loop['snapshots'][0]['status']=='SUCCESS' and loop['snapshots'][0]['score'] is not None else 'incomplete')
    check('node_member:'+member['loopId'],member['state']==expected)
scorable=[row for row in o['loops'] if row['snapshots'][0]['status']=='SUCCESS' and row['snapshots'][0]['score'] is not None]
expected_refs={row['loopId']:row['records'][0]['id'] for row in scorable}
actual_refs={x['loopId']:x['recordId'] for x in payload['loopRecordIds']}
check('node_exact_three_record_references',len(payload['loopRecordIds'])==3 and actual_refs==expected_refs)
check('node_E_N_ratio_percent_units',roster['eligibleLoopCount']==1000 and roster['scorableLoopCount']==3 and near(roster['scorableLoopRatio'],100*3/1000))
check('node_same_record_and_stats',node['taskState']=='SUCCESS' and node['taskId']==f['nodeCeleryTaskId']
      and len(node['allWindowRecords'])==1 and node['allWindowRecords'][0]['id']==s['result_record_id']==r['id']
      and r['object_kind']=='NODE' and r['status']=='COMPLETED' and r['object_id']==f['metadata']['node']['id']
      and stats==payload['assessment_stats'])
check('node_versions_and_window',s['algorithm_version']==r['algorithm_version']=='KPI_CALC_v2.0'
      and time_norm(s['ts_start'])==time_norm(r['ts_start'])==f['windowStart']
      and time_norm(s['ts_end'])==time_norm(r['ts_end'])==f['windowEnd'])
weight_by_id=f['metadata']['node']['weights']
metric_reports={}
percent_fields={'score','good_value_rate','auto_mode_rate','effective_auto_rate','steady_rate','accuracy_rate','fast_rate','oscillation_rate','saturation_rate','instrument_fault_rate'}
for field,d in payload['metricDenominators'].items():
    values=[(Decimal(str(row['snapshots'][0][field])),Decimal(weight_by_id[row['loopId']])) for row in scorable if row['snapshots'][0].get(field) is not None]
    denominator=sum(w for v,w in values); numerator=sum(v*w for v,w in values); expected=numerator/denominator if denominator else None
    check('node_metric:'+field,d['validCount']==len(values) and near(d['validWeight'],denominator)
          and near(d['totalWeight'],sum(Decimal(weight_by_id[row['loopId']]) for row in scorable))
          and near(s[field],expected,.0051) and near(payload[field],s[field]))
    mst=stats['metricStats'][field]
    stat_numerator=numerator/100 if field in percent_fields else numerator
    check('node_metric_stats:'+field,mst['numericCount']==len(values) and near(mst['weightedMean'],expected,.0001)
          and near(mst['numericWeightSum'],denominator) and near(mst['weightedNumerator'],stat_numerator,.000001))
    metric_reports[field]={'numericCount':len(values),'weight':float(denominator),'numeratorUnscaled':float(numerator),'meanIndependent':float(expected)}
check('node_manual_zero_included',loop_rows[f['metadata']['loopIds']['PERF-T0901']]['records'][0]['id'] in actual_refs.values()
      and metric_reports['score']['numericCount']==3 and metric_reports['score']['weight']==6
      and near(s['score'],(56.12*2+0*2+33.16*2)/6,.00001) and s['status']=='POOR')
membership='roster-'+hashlib.sha256((f['windowStart']+'~'+f['windowEnd']+'~'+'|'.join(sorted(ids))).encode()).hexdigest()[:16]
check('node_membership_digest',roster['membershipRevision']==membership)
hashes_after={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in hashes}
check('candidate_source_hashes_after',hashes_after==hashes)

task_ids={c['celeryTaskId'] for c in f['cases']}|{f['nodeCeleryTaskId']}
lifecycle=[]
with (ROOT/'backend/logs/celery-worker.log').open() as stream:
    for line_no,line in enumerate(stream,1):
        if 'G5_TRACE' in line or 'TaskPool: Apply' in line: continue
        if any(tid in line for tid in task_ids): lifecycle.append({'line':line_no,'raw':line.rstrip()})
for tid in task_ids:
    check('actual_worker_lifecycle:'+tid,any(tid in x['raw'] and '] received' in x['raw'] for x in lifecycle)
          and any(tid in x['raw'] and '] succeeded in' in x['raw'] for x in lifecycle))
report={'schema':'g5-independent-smoke-v1','status':'PASS_LIMITED' if all(x['pass'] for x in checks) else 'FAIL',
    'authorAuditNotInvoked':True,'sharedServiceOperations':0,'checksCount':len(checks),
    'failed':[x for x in checks if not x['pass']],'checks':checks,'cases':case_reports,'node':{
        'E':1000,'N':3,'ratioFraction':.003,'ratioPercent':.3,'states':expected_states,'score':s['score'],'status':s['status'],
        'references':actual_refs,'metricsIndependent':metric_reports,'configRevision':r['config_revision'],
        'fastReferenceSeparate':stats.get('fastRateReference')},'taskLifecycles':lifecycle,
    'inputs':{name:{'path':str(INPUT/name),'sha256':hashlib.sha256((INPUT/name).read_bytes()).hexdigest()}
              for name in ('freeze.json','observed.json')},
    'sourceHashGuard':{'before':hashes,'after':hashes_after},
    'limitations':['future synthetic mixed five-case sample; no full A/B or standard-hourly capacity',
        'TD row hashes are exported by Keeper; raw actual rows and actual grid/masks not exported',
        'config drift checked only across captured config hash; NODE record uses UNVERSIONED',
        'TaskRecord actual count not exported; absence expected from single-loop entry source',
        'gap lineage raw valid_rate=1 and intermediate composite9.49 are references; business score null and effective valid_rate=.2663',
        'plan MODE=3 text is incorrect; regenerated/export-matched manual raw MODE=0',
        'deployment process certificate is separate; this audit makes no new live process/DB assertion'],
    'capacityGateEvaluated':False,'sloEvaluated':False}
OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'status':report['status'],'checks':len(checks),'failed':report['failed'],'cases':case_reports,
    'nodeScore':s['score'],'output':str(OUT)},ensure_ascii=False))
