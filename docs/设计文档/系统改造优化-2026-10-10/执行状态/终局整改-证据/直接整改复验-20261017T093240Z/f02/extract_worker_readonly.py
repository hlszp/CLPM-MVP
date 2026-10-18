"""Recover bounded original worker-log ranges without treating a live file as immutable."""
from __future__ import annotations

import argparse
import hashlib
import json
import mmap
from pathlib import Path
import re
import subprocess

SOURCE = Path('/Users/zhangping/DEV/clpm-g5-runtime-20261015/backend/logs/celery-worker.log')
PARENTS = ('4886b5f5-d41f-4269-bb2c-9e1ab0ccc62a', '61a81336-3260-4033-b347-7284eee89171', '905f70a6-665e-4761-92a1-4d082331c075')
WINDOWS = (('2026-10-16 06:52:00', '2026-10-16 07:05:10'), ('2026-10-16 07:06:45', '2026-10-16 07:20:00'), ('2026-10-16 10:33:50', '2026-10-16 10:47:10'))


def select_matches(patterns):
    command = ['rg', '--json', '--fixed-strings', *[arg for text in patterns for arg in ('-e', text)], str(SOURCE)]
    process = subprocess.run(command, capture_output=True, check=False)
    if process.returncode not in (0, 1):
        raise RuntimeError(process.stderr.decode())
    rows = []
    for raw in process.stdout.splitlines():
        item = json.loads(raw)
        if item['type'] == 'match':
            data = item['data']
            rows.append({'sourceLine': data['line_number'], 'sourceOffset': data['absolute_offset'], 'text': data['lines']['text'].rstrip()})
    return rows, {'command': command, 'rc': process.returncode, 'stdoutSha256': hashlib.sha256(process.stdout).hexdigest(), 'stderrSha256': hashlib.sha256(process.stderr).hexdigest()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out-dir', type=Path, required=True)
    destination = parser.parse_args().out_dir
    destination.mkdir(parents=True, exist_ok=False)
    stat = SOURCE.stat()
    rows, command = select_matches(['DataPlanner 取数失败', *PARENTS, 'Task app.tasks.kpi_calc.calculate_hourly_kpi['])
    selected = [r for r in rows if any(start <= r['text'][:19] <= end for start, end in WINDOWS)]
    context = []
    with SOURCE.open('rb') as stream, mmap.mmap(stream.fileno(),0,access=mmap.ACCESS_READ) as content:
        for index, row in enumerate((r for r in selected if 'DataPlanner 取数失败' in r['text']),1):
            start = row['sourceOffset']
            end = start
            for _ in range(100):
                position = content.find(b'\n',end)
                if position < 0:
                    break
                end = position + 1
            raw = content[start:end]
            filename = f'worker-exception-{index:02d}.log'
            (destination / filename).write_bytes(raw)
            context.append({'file': filename,'sourceStartOffset':start,'sourceEndOffsetExclusive':end,'sourceStartLine':row['sourceLine'],'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
    children = []
    for row in selected:
        if 'calculate_node_kpi_hourly' in row['text'] and 'TaskPool:' in row['text']:
            found = re.search(r"calculate_node_kpi_hourly', '([0-9a-f-]+)'", row['text'])
            if found:
                children.append(found.group(1))
    nodes, child_command = select_matches(children)
    output = {'sourcePath':str(SOURCE),'sourceSizeBefore':stat.st_size,'sourceMtimeNsBefore':stat.st_mtime_ns,'commands':[command,child_command],'selectedLines':selected,'nodeChildLines':nodes,'exceptionTraces':context,'boundary':'Live source may append; only copied byte ranges and their SHA-256 are fixed. Concurrent warnings are not all assigned to the selected parent.'}
    (destination/'extraction.json').write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'exceptionRanges':len(context),'childIds':children,'sourceBytesBefore':stat.st_size}))


if __name__ == '__main__':
    main()
