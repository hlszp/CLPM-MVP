"""Local read-only stop gate; independent acceptance is a separate operation.

Consumes actual sampler files. Never imports product gate/calculator or edits raw.
"""
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys


def stamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def validate(batch, profile, recovery=None):
    raw = recovery or batch / 'raw'
    errors = []
    def require(condition, message):
        if not condition: errors.append(message)
    resources = [json.loads(line) for line in (raw / 'resources.jsonl').read_text().splitlines() if line.strip()]
    require(len(resources) >= 2, 'less than two resource samples')
    runtime = json.loads((batch / 'runtime-profile/runtime-profile.json').read_text())
    api = runtime['runtime']['state']['api_pid']
    expected_master = [x['pid'] for x in runtime['runtime']['members'] if x['role'] == 'worker_master'][0]
    expected_ids = {x['pid'] for x in runtime['runtime']['members']}
    peaks = []; samples = []
    for index, sample in enumerate(resources):
        at = stamp(sample['observedAt'])
        if index: require(0 < at - stamp(resources[index-1]['observedAt']) <= profile['sampling']['maximumGapSeconds'], f'poll{sample["poll"]}: resource sampling gap>5s or non-increasing time')
        require(sample['dockerStats']['rc'] == 0, f'poll{sample["poll"]}: docker stats failure')
        require(set(sample['containerCgroups']) == set(profile['containers']), f'poll{sample["poll"]}: incomplete container scope')
        total = 0
        for name in profile['containers']:
            info = sample['containerCgroups'].get(name, {})
            value = info.get('memoryCurrentBytes')
            require(isinstance(value, int) and info.get('raw', {}).get('rc') == 0, f'poll{sample["poll"]}: missing {name} cgroup memory')
            if isinstance(value, int): total += value
            raw_fields = {}
            for line in info.get('raw', {}).get('stdout', '').splitlines()[1:]:
                parts = line.split()
                if len(parts) == 2 and parts[1].isdigit(): raw_fields[parts[0]] = int(parts[1])
            require(all(key in raw_fields and key in info.get('fields', {}) and info['fields'][key] == raw_fields[key] == 0 for key in ('oom', 'oom_kill', 'oom_group_kill')), f'poll{sample["poll"]}: {name} OOM counter missing/nonzero/inconsistent')
        members = []
        for line in sample['ownedProcesses']:
            fields = line.strip().split(maxsplit=5)
            if len(fields) != 6: continue
            pid, ppid, _, rss, state, command = fields
            if not command.startswith('/Users/zhangping/DEV/clpm-g5-runtime-20261015/backend/.venv/bin/python -m '): continue
            if '-m uvicorn app.main:app' in command: role = 'api'
            elif '-m celery ' in command and ' beat ' in command: role = 'beat'
            elif '-m celery ' in command and ' worker ' in command: role = 'worker_master' if int(ppid) == api else 'worker_child'
            else: continue
            members.append({'pid': int(pid), 'ppid': int(ppid), 'rssBytes': int(rss) * 1024, 'role': role, 'state': state})
        require(len({x['pid'] for x in members}) == len(members) == 7, f'poll{sample["poll"]}: native PID missing/duplicate')
        for role, count in [('api', 1), ('worker_master', 1), ('beat', 1), ('worker_child', 4)]:
            require(len([x for x in members if x['role'] == role]) == count, f'poll{sample["poll"]}: {role} identity count')
        require(all(x['ppid'] == expected_master for x in members if x['role'] == 'worker_child'), f'poll{sample["poll"]}: child ancestry')
        require({x['pid'] for x in members} == expected_ids, f'poll{sample["poll"]}: process recycle/change (cache profile no longer proven)')
        total += sum(x['rssBytes'] for x in members)
        require(total <= profile['maximumAccountingBytes'], f'poll{sample["poll"]}: memory conservative upper bound {total}>frozen 80%')
        meminfo = sample['tdProcMeminfo']
        match = re.search(r'^MemAvailable:\s+(\d+)\s+kB$', meminfo.get('stdout', ''), re.M)
        available = int(match.group(1)) * 1024 if match else None
        require(meminfo['rc'] == 0 and available is not None and available >= profile['minimumVmMemAvailableBytes'], f'poll{sample["poll"]}: VM available memory missing/below floor')
        queries = sample.get('tdActiveQueries', {})
        require(queries.get('httpStatus') == 200 and queries.get('response', {}).get('code') == 0, f'poll{sample["poll"]}: TD observer error')
        peaks.append(total); samples.append({'at': sample['observedAt'], 'memoryUpperBoundBytes': total, 'vmAvailableBytes': available, 'members': members})
    report = json.loads((batch / 'product-report.json').read_text())
    observations = [json.loads(line) for line in (raw / 'observations.jsonl').read_text().splitlines() if line.strip()]
    logs = []; seen = set(); previous_log = None
    for observation in observations:
        info = observation.get('workerLog') or observation.get('finalLog') or {}
        require(info.get('available') is not False and all(key in info for key in ('source','inode','offsetStart','offsetEnd','bytes','sha256','pendingBytes','rotated')), 'worker log unavailable/incomplete metadata')
        require(info.get('pendingBytes', 0) == 0 and not info.get('rotated', False), 'worker log gap/rotation requires explicit recovery')
        name = info.get('file')
        content = (raw / name).read_bytes() if name else b''
        require(hashlib.sha256(content).hexdigest() == info.get('sha256') and len(content) == info.get('bytes') and info.get('offsetEnd',0)-info.get('offsetStart',0) == len(content), 'worker log raw hash/byte range mismatch')
        if previous_log:
            require(info.get('source') == previous_log.get('source') and info.get('inode') == previous_log.get('inode') and info.get('offsetStart') == previous_log.get('offsetEnd'), 'worker log missing segment/source changed')
        previous_log = info
        if name and name not in seen:
            seen.add(name); logs.append(content.decode('utf-8','replace'))
    text = ''.join(logs)
    bad = re.findall(r'.*(?:out of memory|query memory exhausted|query buffer exhausted|cannot allocate memory|too many connections|connection pool exhausted|0x0*73a|0x8000073a).*', text, re.I)
    require(not bad, 'resource-exhaustion signature in preserved worker log')
    if recovery:
        require(stamp(resources[-1]['observedAt']) - stamp(resources[0]['observedAt']) >= 300, 'final recovery duration below300s')
    else:
        require(bool(logs), 'formal worker log empty/unavailable')
        timed = [json.loads(line) for line in (batch / 'benchmark-timestamped.jsonl').read_text().splitlines() if line.strip()]
        submitted = next((x['observedAt'] for x in timed if x['line'].startswith('[submit] calculate_hourly_kpi.delay')), None)
        require(submitted is not None, 'actual submission timestamp missing')
        if submitted: require(stamp(submitted) - stamp(resources[0]['observedAt']) >= 60, 'pre-submission resources below60s')
        require(stamp(resources[-1]['observedAt']) - stamp(report['observedAt']) >= 30, 'post-terminal resources below30s')
        cache = json.loads((batch / 'cache-before/cache-state.json').read_text())
        freeze = json.loads(next((batch / 'freeze').glob('freeze_*.json')).read_text())
        if '-cold' in batch.name:
            require(not cache['keys'] and cache['l2PresentLoopCount'] == 0, 'cold target keys exist')
        else:
            warm = json.loads((batch / 'prewarm-task.json').read_text())
            require(warm['state'] == 'SUCCESS' and warm['result']['total'] == warm['result']['succeeded'] == 1000 and warm['result']['failed'] == 0, 'real warmup full denominator failure')
            scored = {x['loopId'] for x in freeze['eligible'] if x['expectedSnapshotStatus'] == 'SUCCESS'}
            present = {x['loopId'] for x in cache['keys'] if x['layer'] == 'pdb_l2' and x['exists'] and x['ttlMs'] > 0}
            parent = report['parentTaskId']
            begin = text.find(f'calculate_hourly_kpi[{parent}] received')
            require(begin >= 0, 'formal parent received log missing')
            hits = set(re.findall(r'DataPlanner L2 命中，跳过查询\+组装: loop=([0-9a-f-]{36})', text[begin:] if begin >= 0 else ''))
            require(scored <= present, f'hot scored input missing valid L2 keys: {len(scored-present)}')
            require(scored <= hits, f'hot formal scored input missing actual L2 consumption: {len(scored-hits)}')
    return {'status': 'LOCAL_CHECKS_PASS' if not errors else 'REWORK', 'independentAcceptance': 'PENDING', 'batch': str(batch), 'recovery': str(recovery) if recovery else None, 'errors': errors, 'peakMemoryUpperBoundBytes': max(peaks) if peaks else None, 'samples': samples, 'resourceFailureLines': bad, 'boundary': 'No product gate/algorithm reuse; local resource/cache stop check only. Full object/record lineage and independent numerical acceptance remain separate.'}


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--batch', type=Path, required=True); p.add_argument('--profile', type=Path, required=True); p.add_argument('--recovery', type=Path)
    args = p.parse_args()
    result = validate(args.batch, json.loads(args.profile.read_text()), args.recovery)
    path = (args.recovery or args.batch) / 'local-resource-check.json'
    with path.open('x') as stream: json.dump(result, stream, ensure_ascii=False, indent=2); stream.write('\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'samples'}, ensure_ascii=False))
    raise SystemExit(0 if result['status'] == 'LOCAL_CHECKS_PASS' else 1)
