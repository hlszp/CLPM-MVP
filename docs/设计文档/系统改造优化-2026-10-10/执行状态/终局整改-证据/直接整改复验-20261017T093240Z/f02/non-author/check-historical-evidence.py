"""Independent F02 historical evidence reconciliation. Reads only saved DB/TD
responses, original report files and fixed byte ranges of the live worker log.
No DB/TD connections or task submissions.
"""
from collections import Counter
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path

OUT=Path(__file__).resolve().parent
SOURCE=OUT.parent
REPORTS=Path('/Users/zhangping/DEV/CLPM-MVP/docs/设计文档/系统改造优化-2026-10-10/执行状态/G5-D01-04-实证-20261015/D02-batches')
sha=lambda value:hashlib.sha256(value).hexdigest()
selected=['1','2','3','4','5','6','7','9pp','10']
excluded=['8','8p','9','9p']
matrix=[]
for batch in selected+excluded:
    path=REPORTS/f'batch{batch}-report.json'
    report=json.loads(path.read_text())
    matrix.append(dict(batch=batch,selected=batch in selected,path=str(path),sha256=sha(path.read_bytes()),
                       parentTaskId=report['parentTaskId'],window=report['window'],
                       completionReason=report['completionReason'],gateOk=report['gate']['ok'],
                       frozenEligible=report['gate']['denominators']['frozenEligible'],
                       violations={key:value['count'] for key,value in report['gate']['violations'].items()}))
counts=Counter(row['completionReason'] for row in matrix if row['selected'])
assert counts=={'BARRIER_PASSED':6,'TERMINAL_MISMATCH':3}
assert all(row['frozenEligible']==1000 for row in matrix if row['selected'])
db=json.loads((SOURCE/'readonly-db-recovery.json').read_text())
assert db['transaction']=='REPEATABLE READ READ ONLY' and db['readOnlyConfirmed']=='on'
warnings=json.loads((SOURCE/'worker-selected-lines.json').read_text())
root_rows=[]
for batch in db['batches']:
    number=batch['batch']
    report=next(row for row in matrix if row['batch']==str(number))
    assert report['parentTaskId']==batch['parentTaskId'] and report['sha256']==batch['reportSha256']
    assert sum(row['count'] for row in batch['exactRunCounts'])==1000
    freeze=SOURCE/f'batch{number}-freeze.json'; manifest=SOURCE/f'batch{number}-manifest.json'
    assert sha(freeze.read_bytes())==batch['freezeSha256']
    assert sha(manifest.read_bytes())==batch['manifestSha256']==batch['manifestFrozenSha256']
    for failure in batch['exactRunFailures']:
        assert failure['logical_run_id']==batch['parentTaskId']
        assert failure['fitness_detail']=={'reason':'DataPlanner取数失败','error':'[0x073a]: Query memory exhausted'}
        assert failure['fitness_tags']=={'tags':['DATA_INSUFFICIENT']}
        assert failure['record_status']=='COMPLETED' and failure['snapshot_status']=='INCONCLUSIVE'
        assert failure['payload_algorithm_version'] is None and failure['dataset_snapshot_id'] is None
        record_at=datetime.fromisoformat(failure['created_at'])+timedelta(hours=8)
        candidates=[row for row in warnings if f"回路 {failure['tag_name']}）" in row['text'] and 'DataPlanner 取数失败' in row['text']]
        closest=min(candidates,key=lambda row:abs((datetime.strptime(row['text'][:23],'%Y-%m-%d %H:%M:%S,%f')-record_at).total_seconds()))
        delta=(record_at-datetime.strptime(closest['text'][:23],'%Y-%m-%d %H:%M:%S,%f')).total_seconds()
        assert abs(delta)<2
        root_rows.append(dict(batch=number,parentTaskId=batch['parentTaskId'],recordId=failure['record_id'],
                              loopId=failure['loop_id'],loopTag=failure['tag_name'],
                              detail=failure['fitness_detail'],recordAlgorithmVersion=failure['record_algorithm_version'],
                              payloadAlgorithmVersion=failure['payload_algorithm_version'],inputSnapshotId=None,
                              warningLine=closest['sourceLine'],warningOffset=closest['sourceOffset'],
                              logToRecordSeconds=delta))
assert Counter(row['batch'] for row in root_rows)=={4:1,5:22,7:1}
assert len({row['recordId'] for row in root_rows})==24
td=json.loads((SOURCE/'readonly-td-source-recovery.json').read_text())
points=[row for batch in td['batches'] for row in batch['points']]
assert len(points)==96
unreadable=[row for row in points if row['summary']['response']['code']!=0 or row['quality']['response']['code']!=0]
for row in unreadable:
    errors=[part['response'] for part in [row['summary'],row['quality']] if part['response']['code']!=0]
    assert all(response['code']==1850 and response['desc']=='Query memory exhausted' for response in errors)
assert len(unreadable)==6
extraction=json.loads((SOURCE/'worker-log-extraction.json').read_text())
raw_source=Path(extraction['sourcePath'])
restored=OUT/'log-restoration';restored.mkdir(exist_ok=True)
log_checks=[]
with raw_source.open('rb') as stream:
    for item in extraction['exceptionTraces']:
        stream.seek(item['sourceStartOffset'])
        raw=stream.read(item['sourceEndOffsetExclusive']-item['sourceStartOffset'])
        same=sha(raw)==item['sha256'] and len(raw)==item['bytes']
        assert same and (SOURCE/item['file']).read_bytes()==raw
        (restored/item['file']).write_bytes(raw)
        log_checks.append(dict(file=item['file'],sourceStartOffset=item['sourceStartOffset'],bytes=len(raw),sha256=sha(raw),matched=same))
assert len(log_checks)==44
node=json.loads((SOURCE/'node-arrival-reconciliation.json').read_text())
node4=next(row for row in node if row['batch']==4)
assert node4['reportNodeMissing']==16 and node4['firstNodeRecords']==20
at=datetime.fromisoformat(node4['reportObservedAt']).replace(tzinfo=None)
assert datetime.fromisoformat(node4['firstMin'])<at<datetime.fromisoformat(node4['firstMax'])
lines=json.loads((SOURCE/'node-child-task-lines.json').read_text())
child='b0bf0d04-2f2f-4176-953b-a15aec90b306'
related=[row for row in lines if child in row['text']]
assert any("'root_id': '4886b5f5-d41f-4269-bb2c-9e1ab0ccc62a'" in row['text'] and "'parent_id': '4886b5f5-d41f-4269-bb2c-9e1ab0ccc62a'" in row['text'] for row in related)
assert any("'success': 26" in row['text'] and "'failed': 0" in row['text'] and "'cancelled': 0" in row['text'] for row in related)
result=dict(historicalSelectedCount=9,historicalSelected=dict(counts),history=matrix,rootCauseCount=24,
            rootCauseRows=root_rows,sourcePoints=96,sourceReadable=90,sourceResourceFailures=6,
            originalLogChecks=log_checks,node4=dict(reportObservedAt=node4['reportObservedAt'],nodeMissingAtReport=16,
            firstNodeRecords=20,firstMin=node4['firstMin'],firstMax=node4['firstMax'],childId=child,childRawLines=related),
            newCapacityRunExecuted=False,boundary='Historical recovery only; current TD source cannot establish original as-of-run input coverage; later node completion does not change historical gate.')
(OUT/'historical-independent-check.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(dict(selected=dict(counts),rootFailures=len(root_rows),logRanges=len(log_checks),sourcePoints=len(points),resourceErrors=len(unreadable),newCapacityRunExecuted=False)))
