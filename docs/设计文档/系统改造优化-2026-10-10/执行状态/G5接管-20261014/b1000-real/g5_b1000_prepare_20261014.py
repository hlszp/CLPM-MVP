"""Keeper-only B1000 input preparation. Author runs only local syntax/help.

prepare: PG/TD read-only preflight, then new durable manifest before any TD write.
write: revalidate frozen identity/config/empty window, then generator.write_timeseries.
Never imports app.main, starts services, calls API imports, publishes tasks,
updates metadata/declaration, or overwrites old manifests/data/results.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import UTC,datetime,timedelta
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

sys.dont_write_bytecode=True
SHA='6c92cf6944d40bc2c92c9296110711933be28ec7'
START=datetime(2026,10,14,5);END=START+timedelta(hours=1)
ROLES=('PV','SP','OP','MODE','PID_P','PID_I','PID_D')
COUNTS={'PV':3465000,'OP':3465000,'SP':2900,'MODE':1000,'PID_P':1000,'PID_I':1000,'PID_D':1000}
STEPS={'TEMPERATURE':5,'PRESSURE':2,'LEVEL':5,'FLOW':1}

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,default=str,separators=(',',':')).encode()).hexdigest()

def save_new(path,value):
    path=Path(path)
    if not path.is_absolute() or not path.parent.is_dir() or path.exists():
        raise SystemExit(f'Output must be new absolute file with existing parent: {path}')
    with path.open('x',encoding='utf-8') as f:json.dump(value,f,ensure_ascii=False,indent=2,default=str);f.write('\n')

def check_source(root):
    actual=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
    dirty=subprocess.check_output(['git','-C',str(root),'status','--porcelain','--untracked-files=no'],text=True)
    if actual!=SHA or dirty.strip():raise SystemExit(f'Candidate changed: SHA={actual}, tracked dirty={bool(dirty.strip())}')

def normalize_series(series):
    return [[int(t),float(v),int(q),int(raw)] for t,v,q,raw in series]

def row_values(row):
    return {col.key:getattr(row,col.key) for col in row.__table__.columns}

def aware(t):
    return t.replace(tzinfo=UTC) if t.tzinfo is None else t.astimezone(UTC)

async def preflight():
    from sqlalchemy import select
    from app.core.db import AsyncSessionLocal
    from app.core.config import settings
    from app.models.loop import LoopLedger,LoopTagMapping
    from app.models.tag import TagRegistry
    from app.models.metric import KpiSnapshotHourly,LoopConfidenceLatest,MetricConfig
    from app.models.node_kpi import KpiNodeSnapshotHourly
    from app.models.calculation_result import CalculationResultRecord
    from app.models.plant_node import PlantNode
    from app.models.point_history import LoopTagBindingHistory,HistoryCoverageSegment
    from app.models.sys_config import SysConfig
    from app.models.metric_data_requirement import ClpmMetricDataRequirement
    from app.services.loop_config import get_loop_type_weights_map
    from app.services.data_source import logical_wide_builder as lwb
    from app.tasks.kpi_calc import _load_batch_runtime_overrides,_batch_load_loop_configs,_eval_loop_selection_stmt,_loop_type_to_control_type
    from app.services.preprocessing.thresholds import get_threshold
    from scripts.generate_capacity_dataset import load_role_points,_load_loop_meta,build_manifest_rows,LOOP_TYPES,CONTROL_TYPES,DECLARATION_KEY

    tags={f'PERF-T{i:04d}' for i in range(1,1001)}
    async with AsyncSessionLocal() as db:
        loops=list((await db.execute(select(LoopLedger).where(LoopLedger.tag_name.like('PERF-T%')))).scalars().all())
        if len(loops)!=1000 or {x.tag_name for x in loops}!=tags or len({str(x.id) for x in loops})!=1000:
            raise SystemExit('Existing PERF-T identity set is not exact distinct 0001..1000')
        for x in loops:
            i=int(x.tag_name.removeprefix('PERF-T'))
            if not x.is_active or x.status!='READY' or not x.include_in_evaluation:
                raise SystemExit(f'Three eligibility conditions violated: {x.tag_name}')
            if x.loop_type!=LOOP_TYPES[(i-1)%4] or x.control_type!=CONTROL_TYPES[(i-1)%4] or x.complex_loop_group_id or x.dcs_model_id:
                raise SystemExit(f'Frozen generator type/control/simple/default-MODE differs: {x.tag_name}')
        ids=sorted(str(x.id) for x in loops)
        maps=list((await db.execute(select(LoopTagMapping).where(LoopTagMapping.loop_id.in_(ids)))).scalars().all())
        if len(maps)!=7000 or Counter(str(x.loop_id) for x in maps)!=Counter({lid:7 for lid in ids}):
            raise SystemExit('Expected exactly seven existing role mappings per loop')
        point_ids={str(x.tag_id) for x in maps}
        if len(point_ids)!=7000:raise SystemExit('Expected 7000 distinct point UUIDs')
        tag_rows=list((await db.execute(select(TagRegistry).where(TagRegistry.id.in_(point_ids)))).scalars().all())
        if len(tag_rows)!=7000:raise SystemExit('Missing existing point metadata')
        history=list((await db.execute(select(LoopTagBindingHistory).where(LoopTagBindingHistory.loop_id.in_(ids)))).scalars().all())
        by_role={}
        for h in history:by_role.setdefault((str(h.loop_id),h.tag_role),[]).append(h)
        for m in maps:
            all_rows=by_role.get((str(m.loop_id),m.tag_role),[])
            active=[h for h in all_rows if aware(h.valid_from)<=aware(END) and (h.valid_to is None or aware(h.valid_to)>aware(START))]
            if len(active)!=1 or aware(active[0].valid_from)>aware(START) or (active[0].valid_to is not None and aware(active[0].valid_to)<=aware(END)) or str(active[0].tag_id)!=str(m.tag_id):
                raise SystemExit(f'Historical binding fails full closed input window/point identity: {m.loop_id}/{m.tag_role}')
        snapshots=list((await db.execute(select(KpiSnapshotHourly.id).where(KpiSnapshotHourly.loop_id.in_(ids),KpiSnapshotHourly.ts_start>=START,KpiSnapshotHourly.ts_start<END))).all())
        records=list((await db.execute(select(CalculationResultRecord.id).where(CalculationResultRecord.object_kind=='LOOP',CalculationResultRecord.object_id.in_(ids),CalculationResultRecord.ts_start>=START,CalculationResultRecord.ts_start<END))).all())
        if snapshots or records:raise SystemExit('Existing B1000 window PG snapshot/ledger evidence; refuse relabel/overwrite')
        latest=list((await db.execute(select(LoopConfidenceLatest).where(LoopConfidenceLatest.loop_id.in_(ids)))).scalars().all())
        if any(x.data_ts_end>START for x in latest):raise SystemExit('Latest business end is inside/later than new window; never delete latest')
        gaps=list((await db.execute(select(HistoryCoverageSegment).where(HistoryCoverageSegment.status=='gap',HistoryCoverageSegment.seg_start<aware(END),HistoryCoverageSegment.seg_end>aware(START)))).scalars().all())
        if gaps:raise SystemExit('Existing registered global gap intersects this window')
        sys_rows=list((await db.execute(select(SysConfig))).scalars().all())
        relevant={x.key:x.value for x in sys_rows if x.key.startswith(('fitness.','data.held_too_long_','kpi.min_observed_ratio','outlier_params','algorithm_params','confidence.'))}
        runtime=await _load_batch_runtime_overrides(db)
        if relevant or runtime.get('fitness') or runtime.get('kpiMinObservedRatio') is not None:
            raise SystemExit('Actual configuration differs from predeclared default four-class oracle')
        configs=await _batch_load_loop_configs(db,ids)
        metrics=list((await db.execute(select(MetricConfig))).scalars().all())
        requirements=list((await db.execute(select(ClpmMetricDataRequirement))).scalars().all())
        weights=await get_loop_type_weights_map(db)
        all_eligible=list((await db.execute(_eval_loop_selection_stmt(None))).scalars().all())
        nodes=list((await db.execute(select(PlantNode).where(PlantNode.name.like('PERF-T%')))).scalars().all())
        if len(nodes)!=26 or len({n.name for n in nodes})!=26 or sum(n.type=='UNIT' for n in nodes)!=20:
            raise SystemExit('Existing PERF-T tree differs from 26 nodes /20 units')
        units={n.name:str(n.id) for n in nodes if n.type=='UNIT'}
        manifest_rows=build_manifest_rows(units,'b',START)
        actual_by_tag={x.tag_name:x for x in loops}
        if any(str(actual_by_tag[r['loopTag']].unit_id)!=r['unitNodeId'] for r in manifest_rows):
            raise SystemExit('Actual unit membership differs from deterministic B manifest')
        node_snaps=list((await db.execute(select(KpiNodeSnapshotHourly.id).where(KpiNodeSnapshotHourly.plant_node_id.in_([str(x.id) for x in nodes]),KpiNodeSnapshotHourly.ts_start>=START,KpiNodeSnapshotHourly.ts_start<END))).all())
        node_records=list((await db.execute(select(CalculationResultRecord.id).where(CalculationResultRecord.object_kind=='NODE',CalculationResultRecord.object_id.in_([str(x.id) for x in nodes]),CalculationResultRecord.ts_start>=START,CalculationResultRecord.ts_start<END))).all())
        if node_snaps or node_records:raise SystemExit('Existing PERF-T node result evidence in this window')
        declaration=next((x.value for x in sys_rows if x.key==DECLARATION_KEY),None)
        payload={'loopRows':{str(x.id):row_values(x) for x in loops},'tagRows':{str(x.id):row_values(x) for x in tag_rows},
                 'bindingHistory':[row_values(x) for x in sorted(history,key=lambda h:(str(h.loop_id),h.tag_role,aware(h.valid_from)))],
                 'runtimeOverrides':runtime,'sysConfig':relevant,'loopConfigs':configs,'metricConfigs':{x.metric_code:row_values(x) for x in metrics},
                 'dataRequirements':{str(x.id):row_values(x) for x in requirements},'typeWeights':weights,'nodes':{str(x.id):row_values(x) for x in nodes},
                 'latestBefore':{str(x.loop_id):{'dataTsStart':x.data_ts_start,'dataTsEnd':x.data_ts_end,'recordId':x.result_record_id} for x in latest},
                 'globalEligibleLoopIds':sorted(str(x.id) for x in all_eligible),'extraEligibleLoopIds':sorted(str(x.id) for x in all_eligible if str(x.id) not in ids),
                 'oldDeclarationSha256':hashlib.sha256(str(declaration).encode()).hexdigest(),'gapOverlaps':[]}
    points=await load_role_points('PERF-T');loop_meta=await _load_loop_meta('b')
    if set(points)!=set(ids) or set(loop_meta)!=set(ids) or any(set(points[lid])!=set(ROLES) for lid in ids):
        raise SystemExit('Generator helper identity/roles differ from frozen PG evidence')
    payload['rolePoints']=points;payload['loopMeta']=loop_meta
    held=await lwb._held_limits_s(5)
    actual_steps={lt:get_threshold(_loop_type_to_control_type(lt)).base_sampling_freq for lt in STEPS}
    if held!={'fast':60.0,'slow':7200.0} or actual_steps!=STEPS:raise SystemExit('Actual default held/base sampling differs from independent oracle')
    payload['heldSeconds']=held;payload['baseSamplingSeconds']=actual_steps
    payload['environment']={k:getattr(settings,k) for k in ('POSTGRES_HOST','POSTGRES_PORT','POSTGRES_DB','TDENGINE_HOST','TDENGINE_PORT','TDENGINE_DB','REDIS_HOST','REDIS_PORT','REDIS_DB')}
    payload['identityConfigHash']=digest(payload)
    return payload,manifest_rows

async def td_empty(metadata):
    from app.core.config import settings
    from app.core.tdengine_native import execute_native
    from app.services.data_source.point_history_repository import POINT_STABLE,format_ts_utc
    db=settings.TDENGINE_DB
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',db):raise SystemExit('Invalid TD database identifier')
    points={entry[1] for roles in metadata['rolePoints'].values() for entry in roles.values()}
    if len(points)!=7000:raise SystemExit('TD preflight denominator is not all 7000 distinct points')
    sql=f"SELECT point_id, COUNT(*) AS cnt FROM {db}.{POINT_STABLE} WHERE ts >= '{format_ts_utc(START)}' AND ts <= '{format_ts_utc(END)}' GROUP BY point_id"
    rows=await execute_native(sql) # one complete aggregation, no LIMIT/sampling/7000 small calls
    present={str(row['point_id']):int(row['cnt']) for row in rows if int(row['cnt'])>0}
    touched={pid:present[pid] for pid in points if pid in present}
    if touched:raise SystemExit(f'New window already has selected TD data: {len(touched)} points; refuse overwrite')
    return {'selectedDistinctPointCount':7000,'selectedRows':0,'otherPointGroupsInWindow':len(set(present)-points),'queryShape':'one super-table GROUP BY point_id, closed exact input window; no LIMIT'}

def build_raw_expectations(metadata,rows):
    from scripts.generate_capacity_dataset import synth_series,RANGES,DATASET_VERSION
    out=[];counts=Counter()
    for row in rows:
        i=int(row['loopTag'].removeprefix('PERF-T'));comp=row['composition'];lt=row['loopType'];step=STEPS[lt]
        series=synth_series(i,comp,row['responseCategory'],RANGES[lt][1]);role_counts={r:len(s) for r,s in series.items()};counts.update(role_counts)
        lid=next(lid for lid,entry in metadata['loopMeta'].items() if entry[0]==row['loopTag'])
        n=3600//step+1;known=(960+step-1)//step if comp=='gap' else n
        # QC-only bound is independent of preprocessing outlier rejection; never claim masks equal QC.
        qc_good=sum(series['PV'][min(slot*step,len(series['PV'])-1)][2]==1 for slot in range(known))
        expected={'snapshotStatus':'INCONCLUSIVE' if comp in ('insufficient','gap') else 'SUCCESS',
                  'scoreClass':'NULL' if comp in ('insufficient','gap') else 'ZERO' if comp=='manual' else 'FINITE',
                  'confidence':'E' if comp in ('insufficient','gap') else 'valid A-D','expectedBaseSlots':n,'sourceKnownSlots':known,'sourceUnknownSlots':n-known,
                  'coreValidSlotsUpperBoundFromSourceQc':qc_good,'coreValidRateUpperBound':qc_good/n,
                  'rawModeValue':series['MODE'][0][1],'ledgerStatus':'COMPLETED','algorithmVersion':'KPI_CALC_v2.0','preprocessVersion':'pre_v2'}
        out.append({**row,'loopId':lid,'seed':i,'pointIds':{r:entry[1] for r,entry in metadata['rolePoints'][lid].items()},'rawRoleCounts':role_counts,
                    'rawQualityCounts':dict(Counter(str(x[2]) for x in series['PV'])),'rawRoleHash':{r:digest(normalize_series(s)) for r,s in series.items()},'rawSeriesHash':digest(series),'expected':expected})
        if i%100==0:print(f'[freeze raw expectations] {i}/1000',flush=True)
    if dict(counts)!=COUNTS:raise SystemExit(f'Raw B counts differ from predeclared composition: {dict(counts)}')
    return out,dict(counts),DATASET_VERSION

async def prepare(args):
    metadata,base_rows=await preflight();empty=await td_empty(metadata)
    rows,counts,version=build_raw_expectations(metadata,base_rows)
    check_source(Path(args.source_root))
    f={'format':'g5-b1000-input-v1','candidateSha':SHA,'scenario':'b','synthetic':True,'generatorVersion':version,'windowStart':START.isoformat(),'windowEnd':END.isoformat(),
       'futureAtPrepare':END>datetime.now(UTC).replace(tzinfo=None),'preparedAt':datetime.now(UTC).isoformat(),'seed':'numpy default_rng(loop_index), unchanged1..1000',
       'metadata':metadata,'tdEmptyBefore':empty,'rows':rows,'rawRoleCounts':counts,'rawTotalRows':sum(counts.values()),'composition':{'normal':800,'insufficient':100,'manual':50,'gap':50},
       'expectedDatasetResults':{'eligible':1000,'snapshotSuccess':850,'snapshotInconclusive':150,'manualEvaluatedZero':50,'scorable':850,'missing':0,'failed':0},
       'newG5Advancement':{'userAuthorized':True,'oneBatchLimitSeconds':900,'old600sTenBatchTwoRealClockWindowsAndFullPerformance':'P6 backlog, not proved by this future input preparation'},
       'channel':{'metadata':'reuse existing PG identities/bindings; zero metadata/declaration updates','timeseries':'generator.write_timeseries, real TD point stable, source_kind4, new window only'},
       'boundary':'input preparation only; no task publication/result/SLO certification; future synthetic window explicitly recorded'}
    save_new(args.manifest,f)
    print(json.dumps({'manifest':args.manifest,'manifestSha256':hashlib.sha256(Path(args.manifest).read_bytes()).hexdigest(),'rawTotalRows':f['rawTotalRows'],'futureAtPrepare':f['futureAtPrepare'],'standardHourlyGlobalEligibleCount':len(metadata['globalEligibleLoopIds']),'extraEligibleCount':len(metadata['extraEligibleLoopIds'])},ensure_ascii=False))

async def write(args):
    from scripts.generate_capacity_dataset import write_timeseries,DATASET_VERSION
    path=Path(args.manifest);actual_hash=hashlib.sha256(path.read_bytes()).hexdigest()
    if actual_hash!=args.expected_manifest_sha256:raise SystemExit('Manifest hash differs from pre-write freeze')
    f=json.loads(path.read_text())
    if f.get('candidateSha')!=SHA or f.get('format')!='g5-b1000-input-v1' or f.get('generatorVersion')!=DATASET_VERSION or f.get('windowStart')!=START.isoformat() or f.get('windowEnd')!=END.isoformat() or len(f.get('rows',[]))!=1000 or f.get('rawRoleCounts')!=COUNTS:
        raise SystemExit('Frozen scenario/version/window/full denominator differs')
    current,_=await preflight()
    if current['identityConfigHash']!=f['metadata']['identityConfigHash']:raise SystemExit('Identity/config/history/latest/environment changed after prepare')
    await td_empty(current)
    # Reserve this new receipt before any write; preserve failure/partial state and never silently rerun.
    receipt={'phase':'write','manifest':str(path),'manifestSha256':actual_hash,'startedAt':datetime.now(UTC).isoformat(),'complete':False,'expectedRoleCounts':COUNTS}
    handle=Path(args.receipt).open('x',encoding='utf-8')
    def checkpoint():
        handle.seek(0);handle.truncate();json.dump(receipt,handle,ensure_ascii=False,indent=2,default=str);handle.write('\n');handle.flush()
    checkpoint()
    try:
        points={lid:{r:tuple(entry) for r,entry in roles.items()} for lid,roles in current['rolePoints'].items()}
        loop_meta={lid:tuple(entry) for lid,entry in current['loopMeta'].items()}
        counts=await write_timeseries(points,loop_meta,START)
        if {r:counts.get(r,0) for r in ROLES}!=COUNTS or counts.get('__total__')!=sum(COUNTS.values()):raise RuntimeError('Actual TD write counts differ from frozen input')
        check_source(Path(args.source_root));receipt.update(complete=True,actualWritten=counts,finishedAt=datetime.now(UTC).isoformat());checkpoint()
    except BaseException as exc:
        receipt.update(errorClass=type(exc).__name__,error=str(exc),partialTdPossible=True,finishedAt=datetime.now(UTC).isoformat());checkpoint();raise
    finally:handle.close()

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--action',required=True,choices=('prepare','write'))
    p.add_argument('--source-root',required=True);p.add_argument('--manifest',required=True,help='New absolute path in Keeper-selected existing durable directory')
    p.add_argument('--expected-manifest-sha256',help='Required for write; exact prepare receipt hash')
    p.add_argument('--receipt',help='Required for write; new absolute receipt path')
    args=p.parse_args();root=Path(args.source_root).resolve();manifest=Path(args.manifest)
    if not manifest.is_absolute() or not manifest.parent.is_dir():p.error('--manifest must be absolute with an existing durable parent')
    if args.action=='prepare' and manifest.exists():p.error('prepare refuses existing manifest')
    if args.action=='write':
        if not args.expected_manifest_sha256 or not re.fullmatch('[0-9a-f]{64}',args.expected_manifest_sha256) or not args.receipt:p.error('write requires exact --expected-manifest-sha256 and --receipt')
        receipt=Path(args.receipt)
        if not receipt.is_absolute() or not receipt.parent.is_dir() or receipt.exists():p.error('--receipt must be new absolute path with existing parent')
    check_source(root);sys.path.insert(0,str(root/'backend'))
    asyncio.run(prepare(args) if args.action=='prepare' else write(args))

if __name__=='__main__':main()
