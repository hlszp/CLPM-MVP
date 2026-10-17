"""Keeper-only post-export: read-only PG/Redis and TD COUNT aggregates.

Author validates syntax/help only. No publication, service startup, DB/cache writes,
control.inspect RPC, raw TD series reads, or output overwrite. Captures all frozen
1030 global loops, with 1000 synthetic test identities explicitly separated.
"""
from __future__ import annotations

import argparse
from collections import Counter
import asyncio
from datetime import UTC,datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import uuid

sys.dont_write_bytecode=True
SHA='6c92cf6944d40bc2c92c9296110711933be28ec7'
ROLES=('PV','SP','OP','MODE','PID_P','PID_I','PID_D')

def load(path):return json.loads(Path(path).read_text())
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,default=str,separators=(',',':')).encode()).hexdigest()
def safe(value):return json.loads(json.dumps(value,default=str))
def columns(row):return {c.key:getattr(row,c.key) for c in row.__table__.columns} if row is not None else None
def aware(value):return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

def source_state(root,freeze):
    actual=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
    dirty=subprocess.check_output(['git','-C',str(root),'status','--porcelain','--untracked-files=no'],text=True)
    files={rel:hashlib.sha256((root/rel).read_bytes()).hexdigest() for rel in freeze['files']}
    return {'sha':actual,'trackedDirty':bool(dirty.strip()),'files':files,'matchesFrozen15':files==freeze['files'],'freezeBaseline':freeze.get('baseline'),'fileCount':len(files)}

def uuids(value):
    out=set()
    if isinstance(value,str):
        try:out.add(str(uuid.UUID(value)))
        except ValueError:pass
    elif isinstance(value,(list,tuple)):
        for item in value:out.update(uuids(item))
    elif isinstance(value,dict):
        for key in ('id','task_id','taskId','children'):
            if key in value:out.update(uuids(value[key]))
    return out

def compare(old,current):
    old=safe(old);current=safe(current)
    result={'equal':old==current,'frozenHash':digest(old),'observedHash':digest(current)}
    if isinstance(old,dict) and isinstance(current,dict):
        result['changedKeys']=sorted(k for k in set(old)|set(current) if old.get(k)!=current.get(k))
    return result

def descendants(node_id,nodes):
    result={node_id};todo=[node_id]
    while todo:
        pid=todo.pop()
        for nid,n in nodes.items():
            if str(n.get('parent_id'))==pid and nid not in result:result.add(nid);todo.append(nid)
    return result

async def read_pg(f):
    from sqlalchemy import select,text
    from app.core.db import AsyncSessionLocal
    from app.models.loop import LoopLedger
    from app.models.tag import TagRegistry
    from app.models.metric import KpiSnapshotHourly,LoopConfidenceLatest,MetricConfig
    from app.models.node_kpi import KpiNodeSnapshotHourly
    from app.models.calculation_result import CalculationResultRecord,CalculationResultAttempt
    from app.models.plant_node import PlantNode
    from app.models.point_history import LoopTagBindingHistory,HistoryCoverageSegment
    from app.models.sys_config import SysConfig
    from app.models.metric_data_requirement import ClpmMetricDataRequirement
    from app.models.loop_config import LoopLevelWeight
    from app.services.loop_config import get_loop_type_weights_map
    from app.tasks.kpi_calc import _load_batch_runtime_overrides,_batch_load_loop_configs
    from scripts.generate_capacity_dataset import DECLARATION_KEY,load_role_points,_load_loop_meta
    start=datetime.fromisoformat(f['windowStart']);end=datetime.fromisoformat(f['windowEnd'])
    test_ids=sorted(r['loopId'] for r in f['rows']);global_ids=f['metadata']['globalEligibleLoopIds'];node_ids=sorted(f['metadata']['nodes'])
    if len(test_ids)!=1000 or len(set(test_ids))!=1000 or len(global_ids)!=1030 or len(set(global_ids))!=1030 or len(node_ids)!=26:
        raise SystemExit('Frozen complete denominators differ from 1000 test /1030 global /26 test nodes')
    async with AsyncSessionLocal() as db:
        await db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
        all_loops=list((await db.execute(select(LoopLedger))).scalars().all())
        loop_by_id={str(x.id):x for x in all_loops}
        selected_loops={lid:columns(loop_by_id.get(lid)) for lid in global_ids}
        records=list((await db.execute(select(CalculationResultRecord).where(CalculationResultRecord.object_kind=='LOOP',CalculationResultRecord.object_id.in_(global_ids),CalculationResultRecord.ts_start>=start,CalculationResultRecord.ts_start<end))).scalars().all())
        snaps=list((await db.execute(select(KpiSnapshotHourly).where(KpiSnapshotHourly.loop_id.in_(global_ids),KpiSnapshotHourly.ts_start>=start,KpiSnapshotHourly.ts_start<end))).scalars().all())
        latest=list((await db.execute(select(LoopConfidenceLatest).where(LoopConfidenceLatest.loop_id.in_(global_ids)))).scalars().all())
        node_snaps=list((await db.execute(select(KpiNodeSnapshotHourly).where(KpiNodeSnapshotHourly.plant_node_id.in_(node_ids),KpiNodeSnapshotHourly.ts_start>=start,KpiNodeSnapshotHourly.ts_start<end))).scalars().all())
        node_records=list((await db.execute(select(CalculationResultRecord).where(CalculationResultRecord.object_kind=='NODE',CalculationResultRecord.object_id.in_(node_ids),CalculationResultRecord.ts_start>=start,CalculationResultRecord.ts_start<end))).scalars().all())
        runs={str(x.logical_run_id) for x in records+node_records}
        attempts=list((await db.execute(select(CalculationResultAttempt).where(CalculationResultAttempt.logical_run_id.in_(runs)))).scalars().all()) if runs else []
        level_rows=list((await db.execute(select(LoopLevelWeight))).scalars().all())
        levels={x.level:x.weight for x in level_rows}
        effective_weights={lid:levels.get(loop_by_id[lid].importance_level,1) for lid in global_ids if lid in loop_by_id}
        nodes=list((await db.execute(select(PlantNode).where(PlantNode.id.in_(node_ids)))).scalars().all())
        current_nodes={str(x.id):columns(x) for x in nodes}
        config_rows=list((await db.execute(select(SysConfig))).scalars().all())
        relevant={x.key:x.value for x in config_rows if x.key.startswith(('fitness.','data.held_too_long_','kpi.min_observed_ratio','outlier_params','algorithm_params','confidence.'))}
        runtime=await _load_batch_runtime_overrides(db);configs=await _batch_load_loop_configs(db,test_ids);weights=await get_loop_type_weights_map(db)
        metrics=list((await db.execute(select(MetricConfig))).scalars().all());requirements=list((await db.execute(select(ClpmMetricDataRequirement))).scalars().all())
        tag_ids=set(f['metadata']['tagRows']);tags=list((await db.execute(select(TagRegistry).where(TagRegistry.id.in_(tag_ids)))).scalars().all())
        history=list((await db.execute(select(LoopTagBindingHistory).where(LoopTagBindingHistory.loop_id.in_(test_ids)))).scalars().all())
        history.sort(key=lambda h:(str(h.loop_id),h.tag_role,aware(h.valid_from)))
        gaps=list((await db.execute(select(HistoryCoverageSegment).where(HistoryCoverageSegment.status=='gap',HistoryCoverageSegment.seg_start<aware(end),HistoryCoverageSegment.seg_end>aware(start)))).scalars().all())
        declaration=next((x.value for x in config_rows if x.key==DECLARATION_KEY),None)
        current={'loopRows':{lid:columns(loop_by_id.get(lid)) for lid in test_ids},'tagRows':{str(x.id):columns(x) for x in tags},'bindingHistory':[columns(x) for x in history],
                 'runtimeOverrides':runtime,'sysConfig':relevant,'loopConfigs':configs,'metricConfigs':{x.metric_code:columns(x) for x in metrics},'dataRequirements':{str(x.id):columns(x) for x in requirements},
                 'typeWeights':weights,'nodes':current_nodes,'globalEligibleLoopIds':sorted(str(x.id) for x in all_loops if x.is_active and x.status=='READY' and x.include_in_evaluation),
                 'oldDeclarationSha256':hashlib.sha256(str(declaration).encode()).hexdigest(),'gapOverlaps':[columns(x) for x in gaps]}
    # These existing helpers only SELECT metadata; no preprocessing/cache warming.
    current['rolePoints']=await load_role_points('PERF-T');current['loopMeta']=await _load_loop_meta('b')
    grouped_records={lid:[] for lid in global_ids};grouped_snaps={lid:[] for lid in global_ids};latest_map={str(x.loop_id):columns(x) for x in latest}
    for x in records:grouped_records[str(x.object_id)].append(columns(x))
    for x in snaps:grouped_snaps[str(x.loop_id)].append(columns(x))
    test_map={r['loopId']:r for r in f['rows']}
    loops=[{'loopId':lid,'scope':'TEST_SYNTHETIC_1000' if lid in test_map else 'REAL_EXTRA_30','frozenInput':test_map.get(lid),
            'currentLoopMetadata':selected_loops[lid],'effectiveImportanceWeight':effective_weights.get(lid),'records':grouped_records[lid],'snapshots':grouped_snaps[lid],'latest':latest_map.get(lid)} for lid in global_ids]
    nodes_out=[]
    for nid in node_ids:
        unit_ids=descendants(nid,current_nodes)
        eligible=sorted(str(x.id) for x in all_loops if x.is_active and x.status=='READY' and x.include_in_evaluation and str(x.unit_id) in unit_ids)
        excluded=sorted(str(x.id) for x in all_loops if x.is_active and str(x.unit_id) in unit_ids and not(x.status=='READY' and x.include_in_evaluation))
        frozen_members=[lid for lid,r in f['metadata']['loopRows'].items() if str(r['unit_id']) in descendants(nid,f['metadata']['nodes'])]
        expected_states=Counter('scorable' if test_map[lid]['expected']['snapshotStatus']=='SUCCESS' else 'incomplete' for lid in frozen_members)
        nodes_out.append({'nodeId':nid,'frozenNodeMetadata':f['metadata']['nodes'][nid],'currentNodeMetadata':current_nodes.get(nid),
                          'frozenTestMemberLoopIds':sorted(frozen_members),'currentEligibleMemberLoopIds':eligible,'currentExcludedLoopIds':excluded,
                          'currentEffectiveMemberWeights':{lid:effective_weights.get(lid) for lid in eligible},'expectedTestStates':dict(expected_states),
                          'records':[columns(x) for x in node_records if str(x.object_id)==nid],'snapshots':[columns(x) for x in node_snaps if str(x.plant_node_id)==nid]})
    task_record_ids={str((x.payload or {}).get('source_task_id')) for x in records if (x.payload or {}).get('source_task_id')}
    task_record_ids.update(str(x.source_task_id) for x in snaps if x.source_task_id)
    return {'loops':loops,'nodes':nodes_out,'attempts':[columns(x) for x in attempts],'currentMetadata':current,
            'taskRecordIdsFromActualResults':sorted(task_record_ids),'levelWeightRows':[columns(x) for x in level_rows],
            'unfrozenConfigurationBoundary':'Input manifest did not freeze LoopLevelWeight rows or full metadata/config of 30 real extra loops; current effective weights exported, prior equality of these sections is not certified.'}

async def read_td_counts(f):
    from app.core.config import settings
    from app.core.tdengine_native import execute_native
    from app.services.data_source.point_history_repository import POINT_STABLE,format_ts_utc
    db=settings.TDENGINE_DB
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',db):raise SystemExit('Invalid TD DB identifier')
    mapping=f['metadata']['rolePoints'];points=sorted({v[1] for roles in mapping.values() for v in roles.values()})
    if len(points)!=7000:raise SystemExit('TD aggregation scope must be exactly7000 distinct frozen points')
    for pid in points:uuid.UUID(pid)
    start=datetime.fromisoformat(f['windowStart']);end=datetime.fromisoformat(f['windowEnd']);in_clause=','.join("'"+pid+"'" for pid in points)
    counts={}
    for label,op in [('closed','<='),('halfOpen','<')]:
        sql=f"SELECT point_id, COUNT(*) AS cnt FROM {db}.{POINT_STABLE} WHERE point_id IN ({in_clause}) AND ts >= '{format_ts_utc(start)}' AND ts {op} '{format_ts_utc(end)}' GROUP BY point_id"
        result=await execute_native(sql)
        counts[label]={str(row['point_id']):int(row['cnt']) for row in result}
        if not set(counts[label])<=set(points):raise RuntimeError('TD returned foreign point outside explicit7000 scope')
    rows=[];role_totals={r:0 for r in ROLES};open_totals={r:0 for r in ROLES}
    for lid,roles in mapping.items():
        by_role={}
        for role,entry in roles.items():
            pid=entry[1];closed=counts['closed'].get(pid,0);opened=counts['halfOpen'].get(pid,0)
            by_role[role]={'pointId':pid,'closedCount':closed,'halfOpenCount':opened,'atEndCount':closed-opened}
            role_totals[role]+=closed;open_totals[role]+=opened
        rows.append({'loopId':lid,'roles':by_role})
    return {'scope':'exact frozen7000 points, explicit UUID IN, grouped COUNT only; no raw TD reads/no LIMIT','perLoopRoles':rows,
            'closedRoleTotals':role_totals,'halfOpenRoleTotals':open_totals,'closedTotal':sum(role_totals.values()),'halfOpenTotal':sum(open_totals.values()),
            'expectedRoleTotals':f['rawRoleCounts'],'writerOffsetBoundary':'raw dense offsets0..3599, gap0..899; COV0/900/2400; no source event at END',
            'matchesFrozenCounts':role_totals==f['rawRoleCounts'] and open_totals==f['rawRoleCounts'] and sum(role_totals.values())==6936900}

async def run(args,root,f,freeze):
    from app.core.config import settings
    from app.services import task_tracker
    from app.tasks.celery_app import celery_app
    from app.services.data_source import logical_wide_builder as lwb
    from app.tasks.kpi_calc import _loop_type_to_control_type
    from app.services.preprocessing.thresholds import get_threshold
    from app.services.result_ledger import derive_loop_logical_run_id
    before=source_state(root,freeze);started=datetime.now(UTC).isoformat()
    parent_before=celery_app.backend.get_task_meta(args.parent_task_id)
    pg=await read_pg(f)
    pg['currentMetadata']['heldSeconds']=await lwb._held_limits_s(5)
    pg['currentMetadata']['baseSamplingSeconds']={lt:get_threshold(_loop_type_to_control_type(lt)).base_sampling_freq for lt in f['metadata']['baseSamplingSeconds']}
    pg['currentMetadata']['environment']={k:getattr(settings,k) for k in f['metadata']['environment']}
    drift={k:compare(f['metadata'][k],v) for k,v in pg['currentMetadata'].items() if k in f['metadata']}
    record_ids=set(pg['taskRecordIdsFromActualResults'])|set(args.task_record_id or [])
    task_records={tid:await task_tracker.get_task(tid) for tid in sorted(record_ids)}
    parent_after=celery_app.backend.get_task_meta(args.parent_task_id)
    child_ids=(uuids(parent_before.get('children',[]))|uuids(parent_after.get('children',[]))|set(args.node_task_id or []))-{args.parent_task_id}
    barrier=None
    if args.barrier_report:
        barrier=load(args.barrier_report)
        child_ids.update(uuids(barrier.get('childTaskObservation',[])))
    child_meta={tid:celery_app.backend.get_task_meta(tid) for tid in sorted(child_ids)}
    td=await read_td_counts(f);after=source_state(root,freeze)
    actual_counts={'globalFrozenEligible':1030,'testFrozenEligible':1000,'realExtraFrozenEligible':30,'testNodes':26,
                   'globalRecordRows':sum(len(x['records']) for x in pg['loops']),'globalSnapshotRows':sum(len(x['snapshots']) for x in pg['loops']),
                   'testRecordRows':sum(len(x['records']) for x in pg['loops'] if x['scope']=='TEST_SYNTHETIC_1000'),
                   'testSnapshotStatuses':dict(Counter(s['status'] for x in pg['loops'] if x['scope']=='TEST_SYNTHETIC_1000' for s in x['snapshots'])),
                   'nodeRecordRows':sum(len(x['records']) for x in pg['nodes']),'nodeSnapshotRows':sum(len(x['snapshots']) for x in pg['nodes'])}
    out={'format':'g5-b1000-complete-export-v1','exportStartedAt':started,'exportFinishedAt':datetime.now(UTC).isoformat(),'candidateSha':SHA,
         'manifestPath':args.manifest,'manifestSha256':hashlib.sha256(Path(args.manifest).read_bytes()).hexdigest(),'sourceFreezePath':args.source_freeze,
         'windowStart':f['windowStart'],'windowEnd':f['windowEnd'],'synthetic':True,'futureAtPrepare':f['futureAtPrepare'],'source15Before':before,'source15After':after,
         'source15Stable':before==after,'configurationMetadataDrift':drift,'allFrozenComparedSectionsStable':all(x['equal'] for x in drift.values()),
         'expectedParentLogicalRunId':derive_loop_logical_run_id(datetime.fromisoformat(f['windowStart']),celery_task_id=args.parent_task_id),
         'actualCounts':actual_counts,'pg':pg,'tdCounts':td,'parent':{'taskId':args.parent_task_id,'backendBefore':parent_before,'backendAfter':parent_after},
         'taskRecords':task_records,'nodeChildTasks':child_meta,'nodeChildIdsSources':{'explicit':args.node_task_id or [],'parentBackendTrail':sorted(uuids(parent_after.get('children',[]))),'localBarrierReport':args.barrier_report},
         'barrierReportReadOnlyCopy':barrier,'certification':'Evidence export only; missing/duplicates/pending/drift preserved. No PASS/SLO assertion. Root/independent timing agent must audit complete900s barrier, 1000+30 global loops,26 nodes and child terminal. Original600s/ten batches/two real-date windows/full performance remain P6 backlog.'}
    path=Path(args.out)
    with path.open('x',encoding='utf-8') as handle:json.dump(out,handle,ensure_ascii=False,indent=2,default=str);handle.write('\n')
    print(json.dumps({'out':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'actualCounts':actual_counts,'source15Stable':out['source15Stable'],'frozenComparedSectionsStable':out['allFrozenComparedSectionsStable'],'parentState':parent_after.get('status'),'nodeChildStates':{i:x.get('status') for i,x in child_meta.items()},'tdCountsMatch':td['matchesFrozenCounts']},ensure_ascii=False))

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source-root',required=True);p.add_argument('--manifest',required=True);p.add_argument('--parent-task-id',required=True)
    p.add_argument('--source-freeze',default='/private/tmp/g5-final-candidate-freeze-v2-20261014.json');p.add_argument('--out',required=True)
    p.add_argument('--task-record-id',action='append',help='Optional explicit TaskRecord ID; actual source_task_id records are also read')
    p.add_argument('--node-task-id',action='append',help='Optional known node child UUID; actual parent backend trail also read')
    p.add_argument('--barrier-report',help='Optional local benchmark report supplies observed child UUIDs; no inspect RPC is issued')
    args=p.parse_args();root=Path(args.source_root).resolve();out=Path(args.out)
    if not out.is_absolute() or not out.parent.is_dir() or out.exists():p.error('--out must be new absolute file with existing parent')
    uuid.UUID(args.parent_task_id)
    for value in (args.task_record_id or [])+(args.node_task_id or []):uuid.UUID(value)
    f=load(args.manifest);freeze=load(args.source_freeze)
    if f.get('candidateSha')!=SHA or f.get('format')!='g5-b1000-input-v1' or len(freeze.get('files',{}))!=15:raise SystemExit('Manifest/source freeze differs from fixed candidate or15-file scope')
    for rel in freeze['files']:
        if not (root/rel).resolve().is_relative_to(root):raise SystemExit('Source freeze file escapes candidate root')
    state=source_state(root,freeze)
    if state['sha']!=SHA or state['trackedDirty'] or not state['matchesFrozen15']:raise SystemExit('Candidate SHA/dirty/15 hashes differ before export')
    sys.path.insert(0,str(root/'backend'));asyncio.run(run(args,root,f,freeze))

if __name__=='__main__':main()
