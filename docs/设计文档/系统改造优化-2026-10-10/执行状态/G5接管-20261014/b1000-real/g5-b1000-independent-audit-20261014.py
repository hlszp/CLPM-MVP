"""Independent offline B1000 audit. No application imports or shared IO."""
import argparse,hashlib,json,math,uuid
from collections import Counter
from datetime import datetime,UTC
from decimal import Decimal
from pathlib import Path

p=argparse.ArgumentParser();p.add_argument('--manifest',required=True);p.add_argument('--observed',required=True);p.add_argument('--barrier',required=True);p.add_argument('--prior-weights',required=True);p.add_argument('--enabled26',required=True);p.add_argument('--report',required=True);a=p.parse_args()
out=Path(a.report);assert out.is_absolute() and out.parent.is_dir()
load=lambda f:json.loads(Path(f).read_text())
m=load(a.manifest);o=load(a.observed);b=load(a.barrier);prior=load(a.prior_weights);enabled26=load(a.enabled26)
checks=[]
def check(name,condition,detail=None):checks.append({'name':name,'pass':bool(condition),'detail':detail})
def norm(t):
    dt=datetime.fromisoformat(t.replace('Z','+00:00'));return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
def near(x,y,tolerance=.0051):
    if x is None or y is None:return x is y
    return abs(Decimal(str(x))-Decimal(str(y)))<=Decimal(str(tolerance))
def json_digest(x):return hashlib.sha256(json.dumps(x,sort_keys=True,default=str,separators=(',',':')).encode()).hexdigest()
manifest_hash=hashlib.sha256(Path(a.manifest).read_bytes()).hexdigest()
check('manifest_hash',o['manifestSha256']==manifest_hash)
check('fixed_sha',m['candidateSha']==o['candidateSha']=='6c92cf6944d40bc2c92c9296110711933be28ec7')
for side in ('source15Before','source15After'):
    s=o[side];check(side,s['sha']==o['candidateSha'] and not s['trackedDirty'] and s['fileCount']==15 and s['matchesFrozen15'])
check('source15_before_after',o['source15Before']==o['source15After'])
frozen=m['metadata'];current=o['pg']['currentMetadata']
for k in current:
    if k in frozen:check('metadata:'+k,current[k]==frozen[k])
test={r['loopId']:r for r in m['rows']};loops={r['loopId']:r for r in o['pg']['loops']};global_ids=set(frozen['globalEligibleLoopIds'])
check('1000_test_distinct',len(test)==len(m['rows'])==1000)
check('1030_global_distinct',len(loops)==len(o['pg']['loops'])==1030 and set(loops)==global_ids and len(global_ids-test.keys())==30)
check('composition',dict(Counter(r['composition'] for r in test.values()))=={'normal':800,'insufficient':100,'manual':50,'gap':50})
old_weights=prior['metadata']['node']['weights']
check('prior_weights_predate_B',norm(prior['preparedAt'])<norm(m['preparedAt']) and set(old_weights)==set(test))
namespace=uuid.uuid5(uuid.NAMESPACE_URL,'clpm:calculation-result-ledger');parent=o['parent']['taskId']
expected_run=parent  # registered hourly explicitly passes self.request.id; single-loop uuid5 differs
weights_typed={k:{q:float(v) for q,v in w.items()} for k,w in frozen['typeWeights'].items()}
config_revision='digest:'+json_digest({'typeWeights':weights_typed,'fitness':frozen['runtimeOverrides']['fitness'] or {}})[:16]
statuses=Counter();extra_statuses=Counter();records={};snapshots={};manual=[];loop_detail=[]
for lid,row in loops.items():
    tag=test[lid]['loopTag'] if lid in test else lid
    check(tag+':one_record_snapshot',len(row['records'])==len(row['snapshots'])==1)
    if len(row['records'])!=1 or len(row['snapshots'])!=1:continue
    r=row['records'][0];s=row['snapshots'][0];l=row['latest'];payload=r['payload'];records[lid]=r;snapshots[lid]=s
    check(tag+':identity_run',r['object_kind']=='LOOP' and r['object_id']==lid and r['logical_run_id']==expected_run and r['status']=='COMPLETED')
    check(tag+':full_window',all(norm(v)==norm(m['windowStart']) for v in [r['ts_start'],s['ts_start']]) and all(norm(v)==norm(m['windowEnd']) for v in [r['ts_end'],s['ts_end']]))
    check(tag+':same_record_latest',l is not None and s['result_record_id']==l['result_record_id']==r['id']
        and norm(l['data_ts_start'])==norm(m['windowStart']) and norm(l['data_ts_end'])==norm(m['windowEnd']))
    if l is not None:
        check(tag+':latest_values',s['status']==l['status'] and near(s['score'],l['score']) and near(s['valid_rate'],l['valid_rate'],.000001)
            and s['confidence_level']==l['confidence_level'] and s['algorithm_version']==l['algorithm_version'])
    check(tag+':algorithm_source',r['algorithm_version']==s['algorithm_version']==payload['algorithm_version']=='KPI_CALC_v2.0'
        and s['source']==payload['source']=='SCHEDULED' and s['source_task_id']==payload['source_task_id']
        and s['source_task_id'] in o['taskRecords'])
    check(tag+':payload_projection',payload['snapshotStatus']==s['status'] and near(payload.get('score'),s['score'])
        and near(payload['valid_rate'],s['valid_rate'],.000001) and payload['confidence_level']==s['confidence_level'])
    if lid not in test:
        extra_statuses[s['status']]+=1;continue
    inp=test[lid];kind=inp['composition'];statuses[s['status']]+=1
    expected_status='SUCCESS' if kind in ('normal','manual') else 'INCONCLUSIVE'
    check(tag+':status',s['status']==expected_status)
    check(tag+':config_digest_and_pinned_revision',r['config_revision'].startswith('rev:')
        and r['config_revision'].split(':')[-1]==config_revision.removeprefix('digest:'))
    check(tag+':effective_weight',Decimal(str(row['effectiveImportanceWeight']))==Decimal(str(old_weights[lid])))
    check(tag+':simple_loop',row['currentLoopMetadata']['complex_loop_group_id'] is None)
    vr=s['valid_rate'];upper=inp['expected']['coreValidRateUpperBound']
    check(tag+':valid_rate_bound',vr is not None and math.isfinite(float(vr)) and 0<=float(vr)<=float(upper)+.000051)
    lineage=s.get('data_lineage') or {}
    check(tag+':lineage',lineage.get('data_policy_version')=='pre_v2' and lineage.get('algorithm_version')=='KPI_CALC_v2.0')
    if expected_status=='SUCCESS':check(tag+':finite_score',s['score'] is not None and math.isfinite(float(s['score'])) and 0<=float(s['score'])<=100 and s['confidence_level'] in ['A','B','C','D'])
    else:check(tag+':null_score',s['score'] is None and s['confidence_level']=='E')
    if kind=='manual':
        check(tag+':manual_valid_zero',float(s['score'])==float(s['auto_mode_rate'])==float(s['effective_auto_rate'])==0 and s['status']=='SUCCESS')
        manual.append(lid)
    loop_detail.append({'loopId':lid,'tag':tag,'composition':kind,'status':s['status'],'score':s['score'],'validRate':vr,'confidence':s['confidence_level'],'recordId':r['id']})
check('B_expected_histogram',dict(statuses)=={'SUCCESS':850,'INCONCLUSIVE':150})
check('50_manual',len(manual)==50)
check('uniform_batch_pinned_revision',len({r['config_revision'] for r in records.values()})==1)

# Reconstruct membership from frozen parent/unit UUIDs, never use exporter expected states.
node_meta=frozen['nodes'];nodes={n['nodeId']:n for n in o['pg']['nodes']}
check('26_complete_nodes',len(nodes)==len(o['pg']['nodes'])==26 and set(nodes)==set(node_meta))
def descendants(nid):
    result={nid};changed=True
    while changed:
        additions={k for k,v in node_meta.items() if str(v['parent_id']) in result}-result;changed=bool(additions);result.update(additions)
    return result
percent={'score','good_value_rate','auto_mode_rate','effective_auto_rate','steady_rate','accuracy_rate','fast_rate','oscillation_rate','saturation_rate','instrument_fault_rate'}
node_detail=[]
disabled_nodes=[]
for nid,row in nodes.items():
    if not node_meta[nid]['is_kpi_enabled']:
        check(nid+':frozen_disabled_no_result',row['currentNodeMetadata']['is_kpi_enabled'] is False and row['records']==row['snapshots']==[])
        disabled_nodes.append(nid)
        continue
    member_ids={lid for lid,v in frozen['loopRows'].items() if str(v['unit_id']) in descendants(nid)}
    expected_states=Counter('scorable' if test[lid]['composition'] in ('normal','manual') else 'incomplete' for lid in member_ids)
    check(nid+':unique_node_rows',len(row['records'])==len(row['snapshots'])==1)
    if len(row['records'])!=1 or len(row['snapshots'])!=1:continue
    r=row['records'][0];s=row['snapshots'][0];payload=r['payload'];stats=s['assessment_stats'];roster=stats['roster'];members=roster['members']
    check(nid+':membership',len(members)==len(member_ids)==len({x['loopId'] for x in members})
        and {x['loopId'] for x in members}==member_ids==set(row['currentEligibleMemberLoopIds']) and row['currentExcludedLoopIds']==[])
    states=Counter(x['state'] for x in members)
    expected_full={k:expected_states[k] for k in ['scorable','incomplete','missing','failed','cancelled','pending']}
    check(nid+':closed_six_states',{k:states[k] for k in expected_full}==expected_full==roster['states'])
    for item in members:
        expected='scorable' if test[item['loopId']]['composition'] in ('normal','manual') else 'incomplete'
        check(nid+':state:'+item['loopId'],item['state']==expected)
    eligible=len(member_ids);scorable=[lid for lid in member_ids if test[lid]['composition'] in ('normal','manual')]
    check(nid+':E_N_ratio',roster['eligibleLoopCount']==eligible and roster['scorableLoopCount']==len(scorable)
        and near(roster['scorableLoopRatio'],100*len(scorable)/eligible,.000051))
    refs={x['loopId']:x['recordId'] for x in payload['loopRecordIds']}
    check(nid+':exact_record_refs',len(payload['loopRecordIds'])==len(scorable) and refs=={lid:records[lid]['id'] for lid in scorable})
    check(nid+':record_projection_version',s['result_record_id']==r['id'] and r['object_id']==nid and r['object_kind']=='NODE' and r['status']=='COMPLETED'
        and s['algorithm_version']==r['algorithm_version']=='KPI_CALC_v2.0' and payload['assessment_stats']==stats)
    check(nid+':window',norm(s['ts_start'])==norm(r['ts_start'])==norm(m['windowStart']) and norm(s['ts_end'])==norm(r['ts_end'])==norm(m['windowEnd']))
    weight_total=sum(Decimal(str(old_weights[lid])) for lid in scorable)
    for field,den in payload['metricDenominators'].items():
        valid=[(Decimal(str(snapshots[lid][field])),Decimal(str(old_weights[lid]))) for lid in scorable if snapshots[lid].get(field) is not None]
        valid_weight=sum(w for value,w in valid);numerator=sum(value*w for value,w in valid);mean=numerator/valid_weight if valid_weight else None
        check(nid+':metric:'+field,den['validCount']==len(valid) and near(den['totalWeight'],weight_total,.000001)
            and near(den['validWeight'],valid_weight,.000001) and near(s.get(field),mean) and near(payload.get(field),s.get(field)))
        ms=stats['metricStats'].get(field)
        if ms is not None:check(nid+':metricStats:'+field,ms['numericCount']==len(valid) and near(ms['numericWeightSum'],valid_weight,.000001)
            and near(ms['weightedMean'],mean,.00011) and near(ms['weightedNumerator'],numerator/100 if field in percent else numerator,.000001))
    check(nid+':manual_kept',all(lid in refs for lid in set(manual)&member_ids))
    check(nid+':node_score_status',s['status']==('INCONCLUSIVE' if s['score'] is None else 'EXCELLENT' if float(s['score'])>=90 else 'GOOD' if float(s['score'])>=80 else 'FAIR' if float(s['score'])>=70 else 'WARNING' if float(s['score'])>=60 else 'POOR'))
    node_detail.append({'nodeId':nid,'name':node_meta[nid]['name'],'E':eligible,'N':len(scorable),'states':expected_full,'score':s['score'],'status':s['status'],'configRevision':r['config_revision'],'manualMembers':len(set(manual)&member_ids)})

td=o['tdCounts'];count_rows={r['loopId']:r for r in td['perLoopRoles']};points=set();role_totals=Counter()
check('1000_TD_scope',len(count_rows)==len(td['perLoopRoles'])==1000 and set(count_rows)==set(test))
for lid,row in count_rows.items():
    check('TD7roles:'+lid,set(row['roles'])=={'PV','SP','OP','MODE','PID_P','PID_I','PID_D'})
    for role,values in row['roles'].items():
        points.add(values['pointId']);n=900 if test[lid]['composition']=='gap' and role in ['PV','OP'] else 3600 if role in ['PV','OP'] else 1 if test[lid]['composition']=='gap' or role!='SP' else 3
        check('TD:'+lid+':'+role,values['pointId']==test[lid]['pointIds'][role] and values['closedCount']==values['halfOpenCount']==n and values['atEndCount']==0)
        role_totals[role]+=values['closedCount']
check('7000_points_6936900',len(points)==7000 and sum(role_totals.values())==6936900 and dict(role_totals)==m['rawRoleCounts']==td['closedRoleTotals']==td['halfOpenRoleTotals'])
check('parent_and_TaskRecord_terminal',o['parent']['backendBefore']['status']==o['parent']['backendAfter']['status']=='SUCCESS'
    and len(o['taskRecords'])>0 and all(r is not None and r.get('status')=='SUCCESS' for r in o['taskRecords'].values()))
check('TaskRecord_1030_completed',len(o['taskRecords'])==1 and all(int(r['loops_total'])==int(r['loops_done'])==int(r['work_items_total'])==int(r['work_items_done'])==1030
    and float(r['progress'])==1 and r['task_type']=='STANDARD' and norm(r['ts_start'])==norm(m['windowStart']) and norm(r['ts_end'])==norm(m['windowEnd']) for r in o['taskRecords'].values()))
check('child_terminal',len(o['nodeChildTasks'])>0 and all(r['status']=='SUCCESS' for r in o['nodeChildTasks'].values()))
check('same_round_nine_conditions',b['parentTaskId']==parent and b['gate']['ok'] and len(b['gate']['conditions'])==9 and all(b['gate']['conditions'].values())
    and b['completionReason']!='RUNNING' and b['sloSeconds']==900 and b['totalSeconds']<=900 and b['sloPassed'])
check('20_enabled_6_disabled_test_tree',len(node_detail)==20 and len(disabled_nodes)==6)

all_node_meta={**node_meta,**{n['id']:n for n in enabled26['enabledNodes']}}
enabled_snaps={s['plant_node_id']:s for s in enabled26['snapshots']};enabled_records={r['object_id']:r for r in enabled26['records']}
enabled_ids={n['id'] for n in enabled26['enabledNodes']}
check('actual_enabled26_unique',len(enabled_ids)==len(enabled26['enabledNodes'])==len(enabled_snaps)==len(enabled26['snapshots'])==len(enabled_records)==len(enabled26['records'])==26
    and set(enabled_snaps)==set(enabled_records)==enabled_ids)
global_extra_detail=[]
for nid in enabled_ids:
    s=enabled_snaps[nid];r=enabled_records[nid];payload=r['payload'];stats=s['assessment_stats'];roster=stats['roster']
    descendants_set={nid};changed=True
    while changed:
        more={k for k,v in all_node_meta.items() if str(v['parent_id']) in descendants_set}-descendants_set;changed=bool(more);descendants_set.update(more)
    members={lid for lid,row in loops.items() if str(row['currentLoopMetadata']['unit_id']) in descendants_set}
    roster_rows=roster['members'];scorable={lid for lid in members if snapshots[lid]['status']=='SUCCESS' and snapshots[lid]['score'] is not None}
    expected_states=Counter('scorable' if lid in scorable else 'incomplete' for lid in members)
    check(nid+':global26_members',len(roster_rows)==len(members)==len({x['loopId'] for x in roster_rows}) and {x['loopId'] for x in roster_rows}==members)
    check(nid+':global26_states',all(roster['states'][k]==expected_states[k] for k in ['scorable','incomplete','failed','missing','cancelled','pending'])
        and roster['eligibleLoopCount']==len(members) and roster['scorableLoopCount']==len(scorable))
    check(nid+':global26_each_member_state',all(item['state']==('scorable' if item['loopId'] in scorable else 'incomplete') for item in roster_rows))
    refs={x['loopId']:x['recordId'] for x in payload['loopRecordIds']}
    check(nid+':global26_refs_record',refs=={lid:records[lid]['id'] for lid in scorable} and len(payload['loopRecordIds'])==len(scorable)
        and s['result_record_id']==r['id'] and r['status']=='COMPLETED' and r['object_kind']=='NODE' and r['object_id']==nid
        and s['algorithm_version']==r['algorithm_version']=='KPI_CALC_v2.0' and stats==payload['assessment_stats']
        and norm(s['ts_start'])==norm(r['ts_start'])==norm(m['windowStart']) and norm(s['ts_end'])==norm(r['ts_end'])==norm(m['windowEnd']))
    if nid in node_meta:
        check(nid+':supplement_equal_original_test',s==nodes[nid]['snapshots'][0] and r==nodes[nid]['records'][0])
    else:
        check(nid+':real_N0_honest',len(scorable)==0 and s['score'] is None and s['status']=='INCONCLUSIVE')
        check(nid+':real_N0_metric_denominators',all(v['validCount']==0 and near(v['validWeight'],0,.000001) for v in payload['metricDenominators'].values()))
        global_extra_detail.append({'nodeId':nid,'name':all_node_meta[nid]['name'],'E':len(members),'N':len(scorable),'states':roster['states'],'score':s['score'],'status':s['status']})
check('actual_global6_plus_test20',len(global_extra_detail)==6 and enabled26['nodeChildTask']['status']=='SUCCESS'
    and enabled26['nodeChildTask']['result']['total']==enabled26['nodeChildTask']['result']['success']==26
    and enabled26['nodeChildTask']['result']['skipped']==enabled26['nodeChildTask']['result']['failed']==0)
submit_epoch=norm(b['observedAt']).timestamp()-b['totalSeconds']
child_elapsed=max(norm(c['date_done']).timestamp()-submit_epoch for c in o['nodeChildTasks'].values())
direct26_elapsed=norm(enabled26['observedAt']).timestamp()-submit_epoch
main_export_elapsed=norm(o['exportFinishedAt']).timestamp()-submit_epoch
check('actual_child_terminal_within900',child_elapsed<=900)

report={'schema':'g5-b1000-independent-v1','status':'PASS_USER_APPROVED_SINGLE_BATCH_GATE_LIMITED' if all(c['pass'] for c in checks) else 'FAIL_OR_INCOMPLETE',
    'checksCount':len(checks),'failed':[c for c in checks if not c['pass']],'checks':checks,'parentId':parent,'expectedRunIndependent':expected_run,
    'testStatuses':dict(statuses),'extraStatuses':dict(extra_statuses),'manualZeroCount':len(manual),'loops':loop_detail,'nodes':node_detail,'disabledTestNodes':disabled_nodes,'actualEnabledGlobalExtra6':global_extra_detail,
    'tdRoleTotalsIndependent':dict(role_totals),'tdDistinctPoints':len(points),'barrierTotalSeconds':b['totalSeconds'],
    'barrierOnly20Units':True,'complete26TimingNotInferredFromCreatedAt':True,'nodeChildStates':{k:v['status'] for k,v in o['nodeChildTasks'].items()},
    'timing':{'approvedNineConditionBarrierSeconds':b['totalSeconds'],'childTerminalSeconds':child_elapsed,'directTest20ExportUpperBoundSeconds':main_export_elapsed,'directGlobal26ObservationSeconds':direct26_elapsed,
        'global26DirectObservationWithin900':direct26_elapsed<=900,'global26CommitBeforeReturnSupport':'Normal dict return follows commit; cancellation BaseException may inflate success_count, so this is strong conditional support, not unconditional per-node commit instrumentation'},
    'correctedExporterLabel':{'singleLoopHelperLabel':o['expectedParentLogicalRunId'],'actualRegisteredHourlyRun':expected_run},
    'authorExporterNotExecuted':True,'sharedIO':0,'boundaries':['Future synthetic single B batch; original600/ten batches/two real-date windows/full performance remain P6 backlog',
        'TD counts validate identity/cardinality, not raw value/quality equality; exporter did not read raw series',
        'Prior smoke weights certify1000 effective weights; full LoopLevelWeight table and30 extra-loop full metadata/config were not pre-frozen',
        'NODE configRevision may remain UNVERSIONED; preserve actual values',
        'All26 post hoc observation was after900; user-approved nine-condition gate covers20 frozen enabled test units, not6 frozen disabled synthetic ancestors',
        'gather success_count treats non-Exception BaseException including CancelledError as success; no actual cancellation evidence in this batch'],
    'files':{k:{'path':v,'sha256':hashlib.sha256(Path(v).read_bytes()).hexdigest()} for k,v in [('manifest',a.manifest),('observed',a.observed),('barrier',a.barrier),('priorWeights',a.prior_weights),('enabled26',a.enabled26)]}}
root=Path('/private/tmp/clpm-g5-followup-20261014')
guard={name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in o['source15After']['files']}
report['independentSourceHashGuardAfter']=guard
report['independentSource15Stable']=guard==o['source15Before']['files']==o['source15After']['files']
log=root/'backend/logs/celery-worker.log';ids={parent,*o['nodeChildTasks']};events=[]
with log.open() as stream:
    for line_no,line in enumerate(stream,1):
        if 'G5_TRACE' not in line and any(task_id in line for task_id in ids):events.append({'line':line_no,'raw':line.rstrip()})
report['actualRawLifecycle']=events
out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'status':report['status'],'checks':len(checks),'failed':report['failed'][:12],'failedCount':len(report['failed']),'testStatuses':dict(statuses),'manual50':len(manual),'nodes':len(node_detail),'report':str(out)},ensure_ascii=False))
