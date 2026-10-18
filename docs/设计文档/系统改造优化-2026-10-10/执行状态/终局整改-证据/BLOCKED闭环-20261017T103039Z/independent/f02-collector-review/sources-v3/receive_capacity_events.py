"""Passive Celery event receiver on the designated private broker.

Keeper enables events on its sole Worker. This receiver never sends enable_events,
wakeup, task, revoke, ACK to the business queue, or service lifecycle commands.
It acknowledges only its observer event subscription in the normal Celery protocol.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import signal
import socket
import sys
import time
from urllib.parse import quote

from celery import Celery
from dotenv import dotenv_values

from audit_capacity_inputs import encode, private_config


def main(args):
    cfg=dotenv_values(args.env_file);private_config(cfg)
    out=Path(args.out);out.mkdir(parents=True,exist_ok=False)
    password=cfg.get('REDIS_PASSWORD')
    authority=(':'+quote(password,safe='')+'@') if password else ''
    host=cfg.get('REDIS_HOST','localhost');port=int(cfg.get('REDIS_PORT',17103))
    app=Celery('capacity_evidence_receiver',broker=f'redis://{authority}{host}:{port}/1')
    deadline=time.monotonic()+args.max_seconds;count=0;counts={};status='COLLECTED'

    def stop(*_):raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)

    def on_event(event):
        nonlocal count
        envelope={'observerUtc':datetime.now(timezone.utc).isoformat(),'event':event}
        with (out/'events.jsonl').open('ab') as stream:stream.write(encode(envelope)+b'\n')
        count+=1;kind=event.get('type','unknown');counts[kind]=counts.get(kind,0)+1
        if time.monotonic()>=deadline or (args.stop_file and Path(args.stop_file).exists()):raise KeyboardInterrupt

    metadata={'startedAt':datetime.now(timezone.utc).isoformat(),'argv':sys.argv,'brokerHost':host,'brokerPort':port,'brokerDb':1,'postgresDatabase':cfg['POSTGRES_DB'],'noWakeup':True,'envFileSha256':hashlib.sha256(Path(args.env_file).read_bytes()).hexdigest()}
    (out/'receiver-command.json').write_bytes(encode(metadata))
    print(json.dumps({'status':'CONNECTING','brokerHost':host,'brokerPort':port,'out':str(out)}),flush=True)
    try:
        with app.connection() as connection:
            receiver=app.events.Receiver(connection,handlers={'*':on_event},app=app)
            print('Receiver ready; Keeper must independently confirm worker events enabled.',flush=True)
            receiver.capture(limit=None,timeout=args.max_seconds,wakeup=False)
    except (KeyboardInterrupt,socket.timeout):pass
    except BaseException as exc:status='COLLECTION_FAILED';metadata['error']={'type':type(exc).__name__,'message':str(exc)}
    metadata.update(status=status,finishedAt=datetime.now(timezone.utc).isoformat(),eventCount=count,eventTypes=counts)
    if count==0:metadata['status']='NO_EVENTS';status='NO_EVENTS'
    path=out/'events.jsonl'
    if path.exists():metadata['eventsSha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    (out/'receiver-summary.json').write_bytes(encode(metadata));print(json.dumps(metadata));return 0 if status=='COLLECTED' else 1


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--env-file',required=True);p.add_argument('--out',required=True);p.add_argument('--stop-file');p.add_argument('--max-seconds',type=float,default=3600)
    raise SystemExit(main(p.parse_args()))
