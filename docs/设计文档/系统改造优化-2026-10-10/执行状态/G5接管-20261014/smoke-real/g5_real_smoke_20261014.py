"""Keeper-only real G5 smoke; this author never executes shared IO.

prepare -> write -> submit-loops -> wait-loops -> submit-node -> wait-node -> export
All commands need --source-root, --freeze, and mutation commands are explicit.
Existing candidate helpers are reused; no main/lifespan/Worker/Beat is started.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
import subprocess

sys.dont_write_bytecode = True
SHA = '6c92cf6944d40bc2c92c9296110711933be28ec7'
SPECS = [(1,'normal'),(801,'insufficient'),(901,'manual'),(953,'gap'),(949,'normal')]
ROLES = ('PV','SP','OP','MODE','PID_P','PID_I','PID_D')

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,default=str,separators=(',',':')).encode()).hexdigest()

def save_new(path, value):
    path = Path(path)
    if not path.is_absolute() or not path.parent.is_dir() or path.exists():
        raise SystemExit(f'Output must be new absolute file with existing parent: {path}')
    with path.open('x',encoding='utf-8') as f:
        json.dump(value,f,ensure_ascii=False,indent=2,default=str)
        f.write('\n')

def load(path):
    return json.loads(Path(path).read_text())

def window(start):
    t = datetime.fromisoformat(start.replace('Z','+00:00'))
    if t.tzinfo is not None:
        t = t.astimezone(UTC).replace(tzinfo=None)
    if (t.minute,t.second,t.microsecond)!=(0,0,0):
        raise SystemExit('Window must be UTC whole-hour start')
    return t,t+timedelta(hours=1)

def check_source(root):
    actual = subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
    if actual!=SHA:
        raise SystemExit(f'Candidate SHA mismatch: {actual} != {SHA}')
    tracked = subprocess.check_output(['git','-C',str(root),'status','--porcelain','--untracked-files=no'],text=True)
    if tracked.strip():
        raise SystemExit('Tracked candidate files changed; refreeze before execution')

def normalized_series(series):
    return [[int(off),float(value),int(q),int(raw)] for off,value,q,raw in series]

async def metadata(t0,t1,require_empty=True):
    from sqlalchemy import select
    from app.core.db import AsyncSessionLocal
    from app.models.loop import LoopLedger
    from app.models.metric import LoopConfidenceLatest,KpiSnapshotHourly,MetricConfig
    from app.models.node_kpi import KpiNodeSnapshotHourly
    from app.models.plant_node import PlantNode
    from app.models.sys_config import SysConfig
    from app.models.calculation_result import CalculationResultRecord
    from app.services.data_source import logical_wide_builder as lwb
    from app.services.loop_config import get_loop_type_weights_map
    from app.services.node_performance import collect_descendant_loop_ids,_load_roster_inputs
    from app.tasks.kpi_calc import _load_batch_runtime_overrides,_batch_load_loop_configs
    from scripts.generate_capacity_dataset import load_role_points
    from app.core.config import settings

    tags = [f'PERF-T{i:04d}' for i,_ in SPECS]
    async with AsyncSessionLocal() as db:
        loops = list((await db.execute(select(LoopLedger).where(LoopLedger.tag_name.in_(tags)))).scalars().all())
        if len(loops)!=5:
            raise SystemExit('Expected all five existing distinct PERF-T loops')
        by_tag={x.tag_name:x for x in loops}
        for x in loops:
            if not x.is_active or x.status!='READY' or not x.include_in_evaluation:
                raise SystemExit(f'Eligibility mismatch: {x.tag_name}')
            if x.loop_type!='TEMPERATURE' or x.control_type!='STABLE' or x.complex_loop_group_id or x.dcs_model_id:
                raise SystemExit(f'Selected TC/simple/default-MODE specification differs: {x.tag_name}')
        ids=[str(x.id) for x in loops]
        old=list((await db.execute(select(KpiSnapshotHourly).where(KpiSnapshotHourly.loop_id.in_(ids),KpiSnapshotHourly.ts_start==t0))).scalars().all())
        records=list((await db.execute(select(CalculationResultRecord).where(CalculationResultRecord.object_kind=='LOOP',CalculationResultRecord.object_id.in_(ids),CalculationResultRecord.ts_start==t0))).scalars().all())
        if require_empty and (old or records):
            raise SystemExit('Window already has loop snapshot/ledger evidence; choose independent new window')
        latest=list((await db.execute(select(LoopConfidenceLatest).where(LoopConfidenceLatest.loop_id.in_(ids)))).scalars().all())
        if any(x.data_ts_end is not None and x.data_ts_end>t1 for x in latest):
            raise SystemExit('Future latest makes incoming window late; never delete latest to bypass')
        config_rows=list((await db.execute(select(SysConfig))).scalars().all())
        selected_config={x.key:x.value for x in config_rows if x.key.startswith(('fitness.','data.held_too_long_','kpi.min_observed_ratio','outlier_params','algorithm_params','confidence.'))}
        runtime=await _load_batch_runtime_overrides(db)
        configs=await _batch_load_loop_configs(db,ids)
        metric_rows=list((await db.execute(select(MetricConfig))).scalars().all())
        metric_configs={x.metric_code:{col.key:getattr(x,col.key) for col in x.__table__.columns} for x in metric_rows}
        weights=await get_loop_type_weights_map(db)
        nodes=list((await db.execute(select(PlantNode).where(PlantNode.name=='PERF-T-FACTORY'))).scalars().all())
        if len(nodes)!=1:
            raise SystemExit('Expected existing unique PERF-T-FACTORY for node denominator audit')
        node=nodes[0]
        descendant_ids=await collect_descendant_loop_ids(db,str(node.id))
        eligible,excluded,existing_snaps,failed_ids,_,roster_weights=await _load_roster_inputs(db,descendant_ids,t0,t1)
        existing_nodes=list((await db.execute(select(KpiNodeSnapshotHourly).where(KpiNodeSnapshotHourly.plant_node_id==node.id,KpiNodeSnapshotHourly.ts_start==t0))).scalars().all())
        if require_empty and (existing_snaps or failed_ids or existing_nodes):
            raise SystemExit('Factory window already has member/node evidence; choose independent new window')
        if len(eligible)!=1000:
            raise SystemExit(f'Factory complete unique denominator differs from frozen 1000: {len(eligible)}')
        if not set(ids)<=set(eligible):
            raise SystemExit('Factory roster does not contain all five frozen samples')
        payload={'loopIds':{tag:str(by_tag[tag].id) for tag in tags},'loopTypes':{tag:by_tag[tag].loop_type for tag in tags},
                 'runtimeOverrides':runtime,'loopConfigs':configs,'metricConfigs':metric_configs,'typeWeights':weights,'sysConfig':selected_config,
                 'selectedLoopContext':{str(x.id):{'importanceLevel':x.importance_level,'idealSettlingTime':x.ideal_settling_time,'controlType':x.control_type,'dcsModelId':x.dcs_model_id,'complexGroupId':x.complex_loop_group_id} for x in loops},
                 'latestBefore':{str(x.loop_id):{'end':x.data_ts_end.isoformat() if x.data_ts_end else None,'recordId':x.result_record_id} for x in latest},
                 'node':{'id':str(node.id),'name':node.name,'isKpiEnabled':bool(node.is_kpi_enabled),'eligibleLoopIds':sorted(str(i) for i in eligible),'excludedCount':excluded,'weights':roster_weights}}
    mappings=await load_role_points('PERF-T')
    payload['rolePoints']={lid:{r:list(mappings[lid][r]) for r in ROLES} for lid in payload['loopIds'].values()}
    for lid in payload['loopIds'].values():
        segments=await lwb._resolve_role_segments(None,lid,list(ROLES),t0.replace(tzinfo=UTC),t1.replace(tzinfo=UTC))
        for role in ROLES:
            segs=[s for s in segments.get(role,[]) if s.start<=t1.replace(tzinfo=UTC) and (s.end is None or s.end>t0.replace(tzinfo=UTC))]
            if len(segs)!=1 or segs[0].start>t0.replace(tzinfo=UTC) or (segs[0].end is not None and segs[0].end<=t1.replace(tzinfo=UTC)):
                raise SystemExit(f'Incomplete/rebound historical binding: {lid}/{role}')
            if segs[0].point_id!=payload['rolePoints'][lid][role][1]:
                raise SystemExit(f'Historical binding differs from frozen writer point: {lid}/{role}')
    gaps=await lwb._load_gap_windows(None)
    overlaps=[(a.isoformat(),b.isoformat()) for a,b in gaps if a<t1.replace(tzinfo=UTC) and b>t0.replace(tzinfo=UTC)]
    if overlaps:
        raise SystemExit(f'Global source gap intersects smoke window: {overlaps}')
    payload['gapOverlaps']=[]
    held=await lwb._held_limits_s(5)
    if held!={'fast':60.0,'slow':7200.0} or runtime.get('kpiMinObservedRatio') is not None or runtime.get('fitness') or selected_config:
        raise SystemExit('Current configuration differs from independent default oracle; stop before writes and report')
    payload['effectiveHeldSeconds']=held
    payload['baseSamplingSeconds']=5
    payload['unknownPolicy']='UNKNOWN excluded from core confidence validity; legal HELD retained; raw QC/values retained'
    payload['environment']={k:getattr(settings,k) for k in ('POSTGRES_HOST','POSTGRES_PORT','POSTGRES_DB','TDENGINE_HOST','TDENGINE_PORT','TDENGINE_DB','REDIS_HOST','REDIS_PORT','REDIS_DB')}
    payload['inputConfigHash']=digest({k:payload[k] for k in ('runtimeOverrides','loopConfigs','metricConfigs','selectedLoopContext','typeWeights','sysConfig','rolePoints','loopTypes','node','effectiveHeldSeconds','environment')})
    return payload

async def td_empty(f):
    from app.services.data_source.point_history_repository import read_events
    t0,t1=window(f['windowStart'])
    points=[entry[1] for roles in f['metadata']['rolePoints'].values() for entry in roles.values()]
    if len(set(points))!=35:
        raise SystemExit('Expected 35 distinct frozen role points')
    rows=await read_events(points,t0,t1,full_columns=False)
    if any(rows.values()):
        raise SystemExit('Selected TD points already contain rows in new window; refusing overwrite/relabel')

async def prepare(args):
    from scripts.generate_capacity_dataset import DATASET_VERSION,synth_series,_response_category,RANGES,LOOP_TYPES
    from app.services.result_ledger import derive_loop_logical_run_id,config_revision_digest
    t0,t1=window(args.ts_start)
    meta=await metadata(t0,t1)
    cases=[]
    for i,composition in SPECS:
        tag=f'PERF-T{i:04d}'
        category=_response_category(i)
        span=RANGES[LOOP_TYPES[(i-1)%4]][1]
        series=synth_series(i,composition,category,span)
        task_id=str(uuid.uuid4())
        cases.append({'seed':i,'loopTag':tag,'loopId':meta['loopIds'][tag],'composition':composition,'category':category,
                      'expectedSnapshotStatus':'INCONCLUSIVE' if composition in ('insufficient','gap') else 'SUCCESS',
                      'expectedScore':'NULL' if composition in ('insufficient','gap') else ('ZERO' if composition=='manual' else 'FINITE'),
                      'expectedConfidence':'E' if composition in ('insufficient','gap') else 'A',
                      'expectedValidRate':{801:0.2940,953:0.2663}.get(i,1.0),
                      'expectedBaseSlots':721,'expectedKnownSlots':192 if i==953 else 721,
                      'expectedCoreValidSlots':{801:212,953:192}.get(i,721),
                      'expectedUnknownSlots':529 if i==953 else 0,'expectedRawQualityInvalidSlots':509 if i==801 else 0,
                      'expectedSource':'MANUAL_STANDARD','expectedSourceTaskId':None,'celeryTaskId':task_id,
                      'logicalRunId':derive_loop_logical_run_id(t0,celery_task_id=task_id),
                      'roleCounts':{r:len(s) for r,s in series.items()},'qualityCounts':{str(q):sum(x[2]==q for x in series['PV']) for q in (0,1,-1)},'seriesSha256':digest(series),
                      'rawRoleHash':{r:digest(normalized_series(s)) for r,s in series.items()}})
    scorable=[c['loopId'] for c in cases if c['expectedSnapshotStatus']=='SUCCESS']
    f={'format':'g5-real-smoke-v1','candidateSha':SHA,'datasetVersion':DATASET_VERSION,'synthetic':True,
       'scenario':'four_classes_plus_A949_noisy_mixed_smoke_not_full_A_or_B','preparedAt':datetime.now(UTC).isoformat(),
       'windowStart':t0.isoformat(),'windowEnd':t1.isoformat(),'futureAtPrepare':t1>datetime.now(UTC).replace(tzinfo=None),
       'scope':'5 explicit calculate_loop_kpi tasks; no standard-hourly/SLO claim','metadata':meta,'cases':cases,
       'expectedConfigRevision':config_revision_digest({'typeWeights':meta['typeWeights'] or {},'fitness':meta['runtimeOverrides'].get('fitness') or {}}),
       'nodeCeleryTaskId':str(uuid.uuid4()),'nodeExpected':{'eligible':len(meta['node']['eligibleLoopIds']),'scorable':3,'incomplete':2,'failed':0,'cancelled':0,'pending':0,'missing':len(meta['node']['eligibleLoopIds'])-5,'scorableLoopIds':scorable},
       'taskRecordExpectedCount':0,'loopTaskCount':5,'nodeTaskCount':1,'ledgerExpectedStatus':'COMPLETED','algorithmVersion':'KPI_CALC_v2.0','preprocessVersion':'pre_v2'}
    await td_empty(f)
    save_new(args.freeze,f) # independently fixed expectations and task IDs before any TD write/submission
    print(json.dumps({'freeze':args.freeze,'freezeSha256':hashlib.sha256(Path(args.freeze).read_bytes()).hexdigest(),'inputRows':sum(sum(c['roleCounts'].values()) for c in cases),'futureAtPrepare':f['futureAtPrepare'],'eligibleSamples':5,'nodeDenominator':f['nodeExpected']},ensure_ascii=False))

async def write_data(args,f):
    from scripts.generate_capacity_dataset import write_timeseries
    t0,t1=window(f['windowStart'])
    current=await metadata(t0,t1)
    if current['inputConfigHash']!=f['metadata']['inputConfigHash']:
        raise SystemExit('Metadata/config changed after freeze')
    await td_empty(f)
    selected=f['metadata']['rolePoints']
    role_points={lid:{r:tuple(v) for r,v in roles.items()} for lid,roles in selected.items()}
    loop_meta={c['loopId']:(c['loopTag'],c['composition'],c['category']) for c in f['cases']}
    counts=await write_timeseries(role_points,loop_meta,t0)
    expected={r:sum(c['roleCounts'][r] for c in f['cases']) for r in ROLES}
    assert {r:counts.get(r,0) for r in ROLES}==expected
    save_new(args.out,{'phase':'write','freezeSha256':hashlib.sha256(Path(args.freeze).read_bytes()).hexdigest(),'counts':counts,'expectedCounts':expected,'window':[t0.isoformat(),t1.isoformat()]})

def submit(args,f,node=False):
    from app.tasks.kpi_calc import calculate_loop_kpi,calculate_node_kpi
    from app.tasks.celery_app import PRIORITY_MANUAL,celery_app
    t0,t1=window(f['windowStart'])
    current=asyncio.run(metadata(t0,t1,require_empty=not node))
    if current['inputConfigHash']!=f['metadata']['inputConfigHash']:
        raise SystemExit('Metadata/config/environment changed after freeze; refuse publication')
    ids=[f['nodeCeleryTaskId']] if node else [c['celeryTaskId'] for c in f['cases']]
    if any(celery_app.AsyncResult(i).state!='PENDING' for i in ids):
        raise SystemExit('Task identity already has a backend state; refuse duplicate publication')
    evidence={'phase':'submit-node' if node else 'submit-loops','submittedAt':datetime.now(UTC).isoformat(),'receipts':[],'complete':False,'freezeSha256':hashlib.sha256(Path(args.freeze).read_bytes()).hexdigest()}
    # Reserve evidence before publication; preserve every successful publish if a later publish fails.
    handle=Path(args.out).open('x',encoding='utf-8')
    def checkpoint():
        handle.seek(0);handle.truncate();json.dump(evidence,handle,ensure_ascii=False,indent=2,default=str);handle.write('\n');handle.flush()
    checkpoint()
    try:
        if node:
            if any(celery_app.AsyncResult(c['celeryTaskId']).state!='SUCCESS' for c in f['cases']):
                raise SystemExit('All five real loop tasks must succeed before node submission')
            result=calculate_node_kpi.apply_async(kwargs={'plant_node_id':f['metadata']['node']['id'],'ts_start':f['windowStart'],'ts_end':f['windowEnd']},task_id=f['nodeCeleryTaskId'],priority=PRIORITY_MANUAL)
            evidence['receipts'].append({'taskId':result.id,'kind':'NODE'});checkpoint()
        else:
            for c in f['cases']:
                result=calculate_loop_kpi.apply_async(kwargs={'loop_id':c['loopId'],'ts_start':f['windowStart']},task_id=c['celeryTaskId'],priority=PRIORITY_MANUAL)
                evidence['receipts'].append({'taskId':result.id,'loopId':c['loopId'],'kind':'LOOP'});checkpoint()
        evidence['complete']=True;checkpoint()
    finally:
        handle.close()

def wait_tasks(args,f,node=False):
    from app.tasks.celery_app import celery_app
    ids=[f['nodeCeleryTaskId']] if node else [c['celeryTaskId'] for c in f['cases']]
    start=time.monotonic()
    while True:
        states={i:celery_app.AsyncResult(i).state for i in ids}
        if all(v in ('SUCCESS','FAILURE','REVOKED') for v in states.values()) or time.monotonic()-start>=args.timeout:
            save_new(args.out,{'phase':'wait-node' if node else 'wait-loops','states':states,'elapsedSeconds':time.monotonic()-start,'complete':all(v=='SUCCESS' for v in states.values()),'isCapacitySlo':False,'freezeSha256':hashlib.sha256(Path(args.freeze).read_bytes()).hexdigest()})
            if not all(v=='SUCCESS' for v in states.values()):
                raise SystemExit(2)
            return
        time.sleep(args.poll)

async def export(args,f):
    from sqlalchemy import select
    from app.core.db import AsyncSessionLocal
    from app.models.metric import KpiSnapshotHourly,LoopConfidenceLatest
    from app.models.calculation_result import CalculationResultRecord
    from app.models.node_kpi import KpiNodeSnapshotHourly
    from app.tasks.celery_app import celery_app
    from app.services.data_source.point_history_repository import read_events
    t0,t1=window(f['windowStart'])
    out={'freezeSha256':hashlib.sha256(Path(args.freeze).read_bytes()).hexdigest(),'exportedAt':datetime.now(UTC).isoformat(),'loops':[]}
    current=await metadata(t0,t1,require_empty=False)
    out['currentInputConfigHash']=current['inputConfigHash']
    out['clientSourceSha']=SHA
    async with AsyncSessionLocal() as db:
        for c in f['cases']:
            records=list((await db.execute(select(CalculationResultRecord).where(CalculationResultRecord.object_kind=='LOOP',CalculationResultRecord.object_id==c['loopId'],CalculationResultRecord.ts_start==t0))).scalars().all())
            snaps=list((await db.execute(select(KpiSnapshotHourly).where(KpiSnapshotHourly.loop_id==c['loopId'],KpiSnapshotHourly.ts_start==t0))).scalars().all())
            latest=(await db.execute(select(LoopConfidenceLatest).where(LoopConfidenceLatest.loop_id==c['loopId']))).scalar_one_or_none()
            def columns(row):
                return {col.key:getattr(row,col.key) for col in row.__table__.columns} if row is not None else None
            out['loops'].append({'loopId':c['loopId'],'taskId':c['celeryTaskId'],'taskState':celery_app.AsyncResult(c['celeryTaskId']).state,'records':[columns(x) for x in records],'snapshots':[columns(x) for x in snaps],'latest':columns(latest)})
        snap=(await db.execute(select(KpiNodeSnapshotHourly).where(KpiNodeSnapshotHourly.plant_node_id==f['metadata']['node']['id'],KpiNodeSnapshotHourly.ts_start==t0))).scalar_one_or_none()
        rec=(await db.execute(select(CalculationResultRecord).where(CalculationResultRecord.id==snap.result_record_id))).scalar_one_or_none() if snap is not None and snap.result_record_id else None
        node_records=list((await db.execute(select(CalculationResultRecord).where(CalculationResultRecord.object_kind=='NODE',CalculationResultRecord.object_id==f['metadata']['node']['id'],CalculationResultRecord.ts_start==t0))).scalars().all())
        out['node']={'taskId':f['nodeCeleryTaskId'],'taskState':celery_app.AsyncResult(f['nodeCeleryTaskId']).state,'snapshot':columns(snap),'record':columns(rec),'allWindowRecords':[columns(x) for x in node_records]}
    points=[v[1] for roles in f['metadata']['rolePoints'].values() for v in roles.values()]
    events=await read_events(points,t0,t1,full_columns=True)
    out['tdInputs']={}
    for c in f['cases']:
        summaries={}
        for role,(_,point_id) in f['metadata']['rolePoints'][c['loopId']].items():
            rows=events.get(point_id,[])
            normalized=[[int((x['ts'].replace(tzinfo=UTC)-t0.replace(tzinfo=UTC)).total_seconds()),float(x['value']),int(x['quality_class']),int(x['quality_raw'])] for x in rows]
            summaries[role]={'pointId':point_id,'count':len(rows),'normalizedHash':digest(normalized),'sourceKinds':sorted(set(x['source_kind'] for x in rows)),'qualitySchemas':sorted(set(x['quality_schema'] for x in rows))}
        out['tdInputs'][c['loopId']]=summaries
    save_new(args.out,out)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--action',required=True,choices=('prepare','write','submit-loops','wait-loops','submit-node','wait-node','export'))
    p.add_argument('--source-root',required=True)
    p.add_argument('--freeze',required=True)
    p.add_argument('--ts-start')
    p.add_argument('--out')
    p.add_argument('--receipt',help='Previous phase receipt required before publication/wait/export')
    p.add_argument('--timeout',type=float,default=300)
    p.add_argument('--poll',type=float,default=2)
    args=p.parse_args()
    root=Path(args.source_root).resolve()
    check_source(root)
    sys.path.insert(0,str(root/'backend'))
    if args.action=='prepare':
        if not args.ts_start:
            p.error('prepare requires --ts-start')
        asyncio.run(prepare(args));return
    if not args.out:
        p.error('all other actions require new absolute --out')
    if not Path(args.out).is_absolute() or not Path(args.out).parent.is_dir() or Path(args.out).exists():
        p.error('--out must be new absolute file with existing parent')
    f=load(args.freeze)
    if f['candidateSha']!=SHA or len(f['cases'])!=5 or {(c['seed'],c['composition']) for c in f['cases']}!=set(SPECS):
        raise SystemExit('Frozen identity/independent input specification differs from this smoke')
    previous={'submit-loops':'write','wait-loops':'submit-loops','submit-node':'wait-loops','wait-node':'submit-node','export':'wait-node'}
    if args.action in previous:
        if not args.receipt:p.error(f'{args.action} requires --receipt from {previous[args.action]}')
        receipt=load(args.receipt)
        if receipt.get('phase')!=previous[args.action] or receipt.get('freezeSha256')!=hashlib.sha256(Path(args.freeze).read_bytes()).hexdigest() or receipt.get('complete',True) is not True:
            raise SystemExit('Previous receipt phase/freeze/completion mismatch; do not republish partial work')
    if args.action=='write':asyncio.run(write_data(args,f))
    elif args.action=='submit-loops':submit(args,f)
    elif args.action=='submit-node':submit(args,f,True)
    elif args.action=='wait-loops':wait_tasks(args,f)
    elif args.action=='wait-node':wait_tasks(args,f,True)
    else:asyncio.run(export(args,f))

if __name__=='__main__':
    main()
