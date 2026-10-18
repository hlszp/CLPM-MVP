"""Only frozen log readers are called; no services, processes or signals."""
import ast
import datetime
import hashlib
import json
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parent
module=ast.parse((ROOT/"driver.v4.py").read_text())
outer=next(n for n in module.body if isinstance(n,ast.FunctionDef) and n.name=="stop")
reader=next(n for n in outer.body if isinstance(n,ast.FunctionDef) and n.name=="read_barrier")
AT=datetime.datetime(2026,10,17,12,tzinfo=datetime.timezone.utc)
WAIT=b"INFO:     Waiting for application shutdown.\n"

def row(logger="app.main"):
    return (json.dumps({"timestamp":(AT+datetime.timedelta(seconds=1)).isoformat(),"logger":logger,"message":"停止 Celery Worker 进程..."})+"\n").encode()

def get_reader(out):
    def write(path,body):path.write_text(json.dumps(body))
    ns={"OUT":out,"json":json,"datetime":datetime,"sha":lambda raw:hashlib.sha256(raw).hexdigest(),"now":lambda:AT.isoformat(),"write":write}
    exec(compile(ast.Module(body=[reader],type_ignores=[]),"frozen-reader.py","exec"),ns)
    return ns["read_barrier"]


def test_positive_order_and_both_raw_hashes(tmp_path):
    (tmp_path/"backend-api.stdout.log").write_bytes(WAIT+row())
    _,barrier=get_reader(tmp_path)(0,AT)
    assert barrier and barrier["offsetStart"]==len(WAIT)
    assert barrier["rawSha256"]==hashlib.sha256(row()).hexdigest()
    assert barrier["uvicornWaitingForApplicationShutdown"]["rawSha256"]==hashlib.sha256(WAIT).hexdigest()


def test_atexit_only_is_not_lifespan_barrier(tmp_path):
    (tmp_path/"backend-api.stdout.log").write_bytes(b"INFO:     Finished server process [17116]\n"+row())
    assert get_reader(tmp_path)(0,AT)[1] is None


def test_waiting_before_signal_offset_cannot_be_reused(tmp_path):
    (tmp_path/"backend-api.stdout.log").write_bytes(WAIT+row())
    assert get_reader(tmp_path)(len(WAIT),AT)[1] is None


def test_previous_signal_waiting_file_cannot_be_reused(tmp_path):
    (tmp_path/"backend-api.stdout.log").write_bytes(row())
    (tmp_path/"lifespan-waiting.json").write_text(json.dumps({"signalAt":(AT-datetime.timedelta(seconds=1)).isoformat(),"offsetEnd":0}))
    assert get_reader(tmp_path)(0,AT)[1] is None


def test_same_signal_split_poll_preserves_first_raw_entry(tmp_path):
    log=tmp_path/"backend-api.stdout.log";log.write_bytes(WAIT)
    read=get_reader(tmp_path);offset,proof=read(0,AT);assert proof is None
    log.write_bytes(WAIT+row());_,proof=read(offset,AT)
    assert proof and proof["uvicornWaitingForApplicationShutdown"]["offsetStart"]==0


def test_wrong_logger_after_waiting_not_accepted(tmp_path):
    (tmp_path/"backend-api.stdout.log").write_bytes(WAIT+row("other"))
    assert get_reader(tmp_path)(0,AT)[1] is None
