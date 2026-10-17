"""Offline audit of Keeper exports. Reads local JSON only; no application import or IO client."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime
from decimal import Decimal
from pathlib import Path
import uuid

SHA='6c92cf6944d40bc2c92c9296110711933be28ec7'
SPECS={(1,'normal'),(801,'insufficient'),(901,'manual'),(953,'gap'),(949,'normal')}

def iso(value):
    try:return datetime.fromisoformat(str(value).replace('Z','+00:00')).replace(tzinfo=None)
    except (TypeError,ValueError):return None

def number(value):
    if value is None:return None
    try:x=float(value)
    except (TypeError,ValueError):return None
    return x if math.isfinite(x) else None

def audit(f,o,freeze_sha):
    checks=[]
    def check(label,ok,actual=None,expected=None):
        checks.append({'check':label,'pass':bool(ok),'actual':actual,'expected':expected})
    check('candidate identity',f.get('candidateSha')==SHA and o.get('clientSourceSha')==SHA)
    check('export bound to unchanged freeze',o.get('freezeSha256')==freeze_sha)
    check('metadata/config/environment stable',o.get('currentInputConfigHash')==f['metadata']['inputConfigHash'])
    check('five distinct independent cases',len(f['cases'])==5 and {(c['seed'],c['composition']) for c in f['cases']}==SPECS and len({c['loopId'] for c in f['cases']})==5)
    check('default held and observation policy',f['metadata'].get('effectiveHeldSeconds')=={'fast':60.0,'slow':7200.0} and f['metadata']['runtimeOverrides'].get('kpiMinObservedRatio') is None)
    expected_ids={c['loopId'] for c in f['cases']}
    check('complete exported loop denominator',len(o.get('loops',[]))==5 and {x['loopId'] for x in o.get('loops',[])}==expected_ids)
    observed={x['loopId']:x for x in o.get('loops',[])}
    record_ids={}
    scored=[]
    lineage=[]
    ns=uuid.uuid5(uuid.NAMESPACE_URL,'clpm:calculation-result-ledger')
    for c in f['cases']:
        lid=c['loopId'];prefix=c['loopTag'];x=observed.get(lid,{})
        task=x.get('taskId');check(prefix+' task',task==c['celeryTaskId'] and x.get('taskState')=='SUCCESS')
        expected_run=str(uuid.uuid5(ns,f"celery:{c['celeryTaskId']}:{f['windowStart']}"))
        check(prefix+' independent logicalRunId',c['logicalRunId']==expected_run)
        rr=x.get('records',[]);ss=x.get('snapshots',[])
        check(prefix+' exact ledger/snapshot cardinality',len(rr)==1 and len(ss)==1,{'records':len(rr),'snapshots':len(ss)},{'records':1,'snapshots':1})
        if len(rr)!=1 or len(ss)!=1:continue
        r=rr[0];s=ss[0];latest=x.get('latest') or {};payload=r.get('payload') or {}
        record_ids[lid]=r['id']
        check(prefix+' append-only identity/status/version',r.get('logical_run_id')==expected_run and r.get('object_kind')=='LOOP' and r.get('object_id')==lid and r.get('status')=='COMPLETED' and r.get('algorithm_version')==f['algorithmVersion'] and r.get('config_revision')==f['expectedConfigRevision'])
        check(prefix+' exact ledger window',iso(r.get('ts_start'))==iso(f['windowStart']) and iso(r.get('ts_end'))==iso(f['windowEnd']))
        check(prefix+' snapshot/result pointer',s.get('result_record_id')==r['id'] and s.get('loop_id')==lid)
        check(prefix+' actual status source algorithm',s.get('status')==c['expectedSnapshotStatus'] and payload.get('snapshotStatus')==c['expectedSnapshotStatus'] and s.get('source')==c['expectedSource'] and s.get('source_task_id')==c['expectedSourceTaskId'] and s.get('algorithm_version')==f['algorithmVersion'])
        check(prefix+' confidence',s.get('confidence_level')==c['expectedConfidence'],s.get('confidence_level'),c['expectedConfidence'])
        vr=number(s.get('valid_rate'))
        check(prefix+' effective core valid rate',vr is not None and abs(vr-c['expectedValidRate'])<=0.00005,vr,c['expectedValidRate'])
        score=number(s.get('score'))
        ok=(s.get('score') is None if c['expectedScore']=='NULL' else score==0 if c['expectedScore']=='ZERO' else score is not None and 0<=score<=100)
        check(prefix+' independently frozen score class',ok,s.get('score'),c['expectedScore'])
        check(prefix+' snapshot window',iso(s.get('ts_start'))==iso(f['windowStart']) and iso(s.get('ts_end'))==iso(f['windowEnd']))
        check(prefix+' latest points at this business window/record',latest.get('result_record_id')==r['id'] and latest.get('status')==s.get('status') and iso(latest.get('data_ts_start'))==iso(f['windowStart']) and iso(latest.get('data_ts_end'))==iso(f['windowEnd']) and number(latest.get('score'))==score)
        lv=number(latest.get('valid_rate'))
        check(prefix+' latest valid rate',lv is not None and vr is not None and abs(lv-vr)<=0.00005)
        dl=s.get('data_lineage') or {}
        lineage.append({'loopId':lid,'dataPolicyVersion':dl.get('data_policy_version'),'algorithmVersion':dl.get('algorithm_version'),'samplingFrequency':dl.get('sampling_freq')})
        check(prefix+' persisted actual preprocess/algorithm lineage',dl.get('data_policy_version')==f['preprocessVersion'] and dl.get('algorithm_version')==f['algorithmVersion'])
        if c['expectedSnapshotStatus']=='SUCCESS':scored.append((lid,Decimal(str(s['score']))))
        if c['composition']=='manual':
            check(prefix+' legal manual remains evaluated zero',s.get('auto_mode_rate') is not None and number(s['auto_mode_rate'])==0 and number(s.get('fast_rate'))==0 and s.get('assess_level')=='L4')
        if c['composition']=='gap':
            detail=s.get('fitness_detail') or {};gate=detail.get('gate') or {}
            check(prefix+' gap gate uses effective slots',detail.get('pointCount')==192 and gate.get('expectedPoints')==721 and gate.get('passed') is False and abs(float(detail.get('gapRatio',-1))-529/721)<=0.00005)
        td=o.get('tdInputs',{}).get(lid,{})
        check(prefix+' all seven raw input roles',set(td)==set(c['roleCounts']))
        for role,count in c['roleCounts'].items():
            row=td.get(role,{})
            check(prefix+'/'+role+' TD exact identity/count/hash/source',row.get('pointId')==f['metadata']['rolePoints'][lid][role][1] and row.get('count')==count and row.get('normalizedHash')==c['rawRoleHash'][role] and row.get('sourceKinds')==[4] and row.get('qualitySchemas')==[1])
    node=o.get('node') or {};s=node.get('snapshot') or {};r=node.get('record') or {};payload=r.get('payload') or {}
    check('node exact ledger cardinality',len(node.get('allWindowRecords',[]))==1)
    check('node task terminal',node.get('taskId')==f['nodeCeleryTaskId'] and node.get('taskState')=='SUCCESS')
    check('node real snapshot+ledger identity',bool(s) and bool(r) and s.get('plant_node_id')==f['metadata']['node']['id'] and s.get('result_record_id')==r.get('id') and r.get('object_kind')=='NODE' and r.get('object_id')==f['metadata']['node']['id'] and r.get('status')=='COMPLETED' and r.get('algorithm_version')==f['algorithmVersion'])
    if s and r:
        check('node exact business window',iso(s.get('ts_start'))==iso(f['windowStart']) and iso(s.get('ts_end'))==iso(f['windowEnd']) and iso(r.get('ts_start'))==iso(f['windowStart']) and iso(r.get('ts_end'))==iso(f['windowEnd']))
        stats=s.get('assessment_stats') or {};roster=stats.get('roster') or {};ne=f['nodeExpected']
        check('node complete eligible/scorable denominator',roster.get('eligibleLoopCount')==ne['eligible'] and roster.get('scorableLoopCount')==ne['scorable'] and abs(float(roster.get('scorableLoopRatio',-1))-100*ne['scorable']/ne['eligible'])<=0.00005)
        expected_states={k:ne[k] for k in ('scorable','incomplete','failed','cancelled','pending','missing')}
        check('node six mutually exclusive state counts',roster.get('states')==expected_states,roster.get('states'),expected_states)
        check('node excluded remains separate',roster.get('excludedLoopCount')==f['metadata']['node']['excludedCount'])
        members=roster.get('members') or []
        check('node exact full member identity set',len(members)==ne['eligible'] and {m['loopId'] for m in members}==set(f['metadata']['node']['eligibleLoopIds']))
        selected_states={m['loopId']:m['state'] for m in members if m['loopId'] in expected_ids}
        expected_selected={c['loopId']:'scorable' if c['expectedSnapshotStatus']=='SUCCESS' else 'incomplete' for c in f['cases']}
        check('node manual zero and two insufficient members',selected_states==expected_selected)
        refs=payload.get('loopRecordIds') or []
        actual_refs={(x.get('loopId'),x.get('recordId')) for x in refs}
        expected_refs={(lid,record_ids.get(lid)) for lid in ne['scorableLoopIds']}
        check('node exact three contributing record identities',len(refs)==3 and actual_refs==expected_refs)
        check('node stats immutable payload equals projection',payload.get('assessment_stats')==stats)
        denominator=(payload.get('metricDenominators') or {}).get('score') or {}
        weights=f['metadata']['node']['weights'];ws=sum(Decimal(str(weights[lid])) for lid,_ in scored)
        expected_score=sum(value*Decimal(str(weights[lid])) for lid,value in scored)/ws if ws>0 else None
        check('node actual zero included in score denominator',len(scored)==3 and denominator.get('validCount')==3 and abs(float(denominator.get('validWeight',-1))-float(ws))<=0.000001)
        check('node independent weighted score oracle',expected_score is not None and number(s.get('score')) is not None and abs(number(s['score'])-float(expected_score))<=0.011,s.get('score'),str(expected_score))
    return {'pass':all(c['pass'] for c in checks),'checkCount':len(checks),'failed':[c for c in checks if not c['pass']],'checks':checks,'lineage':lineage,
            'boundary':'local JSON only; authentic shared execution supplied by Keeper; full standard-hourly/capacity/two real-clock windows/worker SHA not certified; actual masks not persisted by single-loop task, covered only by prior independent offline oracle'}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--freeze',required=True);p.add_argument('--observed',required=True);p.add_argument('--report',required=True);args=p.parse_args()
    target=Path(args.report)
    if not target.is_absolute() or not target.parent.is_dir() or target.exists():p.error('--report must be a new absolute local file')
    f=json.loads(Path(args.freeze).read_text());o=json.loads(Path(args.observed).read_text())
    result=audit(f,o,hashlib.sha256(Path(args.freeze).read_bytes()).hexdigest())
    result['files']={'freeze':args.freeze,'observed':args.observed,'observedSha256':hashlib.sha256(Path(args.observed).read_bytes()).hexdigest()}
    with target.open('x') as handle:json.dump(result,handle,ensure_ascii=False,indent=2);handle.write('\n')
    print(json.dumps({'pass':result['pass'],'checkCount':result['checkCount'],'failedCount':len(result['failed']),'report':str(target)},ensure_ascii=False))
    raise SystemExit(0 if result['pass'] else 1)

if __name__=='__main__':main()
