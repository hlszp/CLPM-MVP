"""Only the nested log reader is extracted; no app/process/signal operations."""
import ast
import datetime
import hashlib
import json
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parent
source=ast.parse((ROOT/"driver.v3.py").read_text())
stop=next(n for n in source.body if isinstance(n,ast.FunctionDef) and n.name=="stop")
reader=next(n for n in stop.body if isinstance(n,ast.FunctionDef) and n.name=="read_barrier")


def call(tmp_path, messages):
    at=datetime.datetime(2026,10,17,12,tzinfo=datetime.timezone.utc)
    rows=[{"timestamp":(at+datetime.timedelta(seconds=i+1)).isoformat(),"logger":"app.main","message":m} for i,m in enumerate(messages)]
    (tmp_path/"backend-api.stdout.log").write_bytes(b"".join((json.dumps(r)+"\n").encode() for r in rows))
    def write(path,body):path.write_text(json.dumps(body))
    ns={"OUT":tmp_path,"json":json,"datetime":datetime,"sha":lambda raw:hashlib.sha256(raw).hexdigest(),"now":lambda:at.isoformat(),"write":write}
    exec(compile(ast.Module(body=[reader],type_ignores=[]),"extracted-log-reader.py","exec"),ns)
    return ns["read_barrier"](0,at)[1]


def test_ordered_lifespan_then_worker_stop_can_be_reviewed(tmp_path):
    assert call(tmp_path,["Shutting down CLPM-MVP","停止 Celery Worker 进程..."]) is not None


def test_generic_worker_stop_without_lifespan_entry_cannot_prove_watchdog_stopped(tmp_path):
    # _shutdown_celery_once also invokes _stop_celery_worker without watchdog_stop.
    assert call(tmp_path,["停止 Celery Worker 进程..."]) is None
