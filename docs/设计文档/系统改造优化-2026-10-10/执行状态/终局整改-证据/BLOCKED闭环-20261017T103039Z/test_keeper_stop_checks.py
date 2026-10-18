"""Synthetic disk-only checks of the local stop gate, never business evidence."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import hashlib
from pathlib import Path
import tempfile
import unittest
from validate_capacity_batch import validate

E = Path(__file__).resolve().parent
PROFILE = json.loads((E / 'f02/series-s01/resource-profile-plan.json').read_text())


class StopChecks(unittest.TestCase):
    def run_case(self, mutation=None, recovery=False):
        with tempfile.TemporaryDirectory(prefix='keeper-disk-only-') as folder:
            batch = Path(folder) / '01-a-cold'
            for name in ('raw', 'runtime-profile', 'cache-before', 'freeze'): (batch / name).mkdir(parents=True)
            start = datetime(2026, 10, 1, tzinfo=timezone.utc)
            at = lambda seconds: (start + timedelta(seconds=seconds)).isoformat()
            processes = []
            members = []
            for pid, ppid, role in [(101, 1, 'api'), (102, 101, 'beat'), (103, 101, 'worker_master'), *[(i, 103, 'worker_child') for i in range(104, 108)]]:
                command = '-m uvicorn app.main:app' if role == 'api' else '-m celery -A app.tasks.celery_app ' + ('beat' if role == 'beat' else 'worker') + ' -l info'
                processes.append(f'{pid} {ppid} 0.0 1024 S /Users/zhangping/DEV/clpm-g5-runtime-20261015/backend/.venv/bin/python {command}')
                members.append({'pid': pid, 'role': role})
            resources = [{'poll': i+1, 'observedAt': at(i), 'dockerStats': {'rc': 0}, 'containerCgroups': {name: {'memoryCurrentBytes': 32*1024**2, 'raw': {'rc': 0,'stdout':str(32*1024**2)+'\noom 0\noom_kill 0\noom_group_kill 0\n'}, 'fields': {'oom': 0, 'oom_kill': 0,'oom_group_kill':0}} for name in PROFILE['containers']}, 'ownedProcesses': processes[:], 'tdProcMeminfo': {'rc': 0, 'stdout': 'MemAvailable: 3145728 kB\n'}, 'tdActiveQueries': {'httpStatus': 200, 'response': {'code': 0}}} for i in range(106)]
            cache = {'keys': [], 'l2PresentLoopCount': 0}
            log = 'Synthetic quiet worker log\n'
            if mutation: resources, cache, log = mutation(resources, cache, log)
            def write(relative, value): (batch / relative).write_text(json.dumps(value))
            write('runtime-profile/runtime-profile.json', {'runtime': {'state': {'api_pid': 101}, 'members': members}})
            write('product-report.json', {'observedAt': at(70), 'parentTaskId': '00000000-0000-0000-0000-000000000001'})
            write('cache-before/cache-state.json', cache); write('freeze/freeze_fake.json', {'eligible': []})
            (batch / 'benchmark-timestamped.jsonl').write_text(json.dumps({'observedAt': at(65), 'line': '[submit] calculate_hourly_kpi.delay synthetic-only'})+'\n')
            (batch / 'raw/resources.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in resources))
            (batch / 'raw/worker.log').write_text(log)
            (batch / 'raw/observations.jsonl').write_text(json.dumps({'workerLog': {'source':'synthetic-only','inode':1,'offsetStart':0,'offsetEnd':len(log.encode()),'bytes':len(log.encode()),'sha256':hashlib.sha256(log.encode()).hexdigest(),'pendingBytes': 0, 'rotated': False, 'file': 'worker.log'}})+'\n')
            return validate(batch, PROFILE, batch / 'raw' if recovery else None)

    def test_valid_quiet_shape(self): self.assertEqual(self.run_case()['status'], 'LOCAL_CHECKS_PASS')
    def test_sampling_gap_rejected(self):
        result = self.run_case(lambda r,c,l: (r[:20]+r[30:],c,l)); self.assertTrue(any('gap>5s' in x for x in result['errors']))
    def test_memory_charge_rejected(self):
        def mutate(r,c,l): r[40]['containerCgroups']['clpm-mvp-tdengine']['memoryCurrentBytes']=6*1024**3; return r,c,l
        self.assertEqual(self.run_case(mutate)['status'], 'REWORK')
    def test_memory_raw_summary_disagreement_rejected(self):
        def mutate(r,c,l): r[40]['containerCgroups']['clpm-mvp-tdengine']['raw']['stdout']='6000000000\noom 0\noom_kill 0\noom_group_kill 0\n'; return r,c,l
        self.assertEqual(self.run_case(mutate)['status'], 'REWORK')
    def test_missing_child_rejected(self):
        def mutate(r,c,l): r[40]['ownedProcesses']=r[40]['ownedProcesses'][:-1]; return r,c,l
        self.assertEqual(self.run_case(mutate)['status'], 'REWORK')
    def test_vm_floor_rejected(self):
        def mutate(r,c,l): r[40]['tdProcMeminfo']['stdout']='MemAvailable: 1024 kB\n'; return r,c,l
        self.assertEqual(self.run_case(mutate)['status'], 'REWORK')
    def test_oom_rejected(self):
        def mutate(r,c,l): r[40]['containerCgroups']['clpm-mvp-tdengine']['fields']['oom_kill']=1; return r,c,l
        self.assertEqual(self.run_case(mutate)['status'], 'REWORK')
    def test_cold_cache_rejected(self): self.assertEqual(self.run_case(lambda r,c,l:(r,{'keys':[{'fake':'present'}],'l2PresentLoopCount':1},l))['status'], 'REWORK')
    def test_resource_error_rejected(self): self.assertEqual(self.run_case(lambda r,c,l:(r,c,'TDengine query memory exhausted 0x073a\n'))['status'], 'REWORK')
    def test_redis_maxmemory_rejected(self): self.assertEqual(self.run_case(lambda r,c,l:(r,c,'redis.exceptions.OutOfMemoryError: command not allowed when used memory > maxmemory\n'))['status'], 'REWORK')
    def test_short_final_recovery_rejected(self): self.assertTrue(any('below300s' in x for x in self.run_case(recovery=True)['errors']))


if __name__ == '__main__': unittest.main(verbosity=2)
