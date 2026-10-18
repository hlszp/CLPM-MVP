"""Offline subprocess supervision regressions; no real services or native ps/git."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

SUPERVISOR = Path(__file__).resolve().parents[1] / "scripts/capacity_series_supervisor.py"
RUNNER = """import argparse, hashlib, json, signal, time
from pathlib import Path
from types import SimpleNamespace
def write(path, value):
    path.write_text(json.dumps(value))
def load_contract(path):
    p=json.loads(Path(path).read_text()); e=p['execution']
    for item in p['tools'].values():
        assert hashlib.sha256(Path(item['path']).read_bytes()).hexdigest()==item['sha256']
    return SimpleNamespace(protocol=p,protocol_path=Path(path),tools=p['tools'],
        output_parent=Path(e['outputParent']),python=Path(e['python']),
        runtime=Path(p['runtimeDir']),evidence=Path(e['evidenceRoot']),cwd=Path(e['cwd']))
def require_ready(ctx):
    assert ctx.protocol['formalReady'] and not ctx.protocol['readinessPending']
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--protocol');parser.add_argument('--evidence-root')
    parser.add_argument('--output-dir');args=parser.parse_args()
    p=json.loads(Path(args.protocol).read_text());out=Path(args.output_dir);out.mkdir()
    rows=[];write(out/'runner-clock.json',{'startedMonotonic':time.monotonic()})
    try:
        for batch in range(1,21):
            seconds=601 if p['runnerFault']=='seconds' else 100
            slo=p['runnerFault']!='slo'
            rows.append({'batch':batch,'seconds':seconds,'sloPassed':slo,'fixtureOnly':True})
            write(out/'outcomes.json',rows)
            if p['runnerFault']=='failure': return 7
            time.sleep(20 if p['runnerFault']=='hold' else .03)
        write(out/'final-recovery.json',{'finishedMonotonic':time.monotonic(),'fixtureOnly':True})
        write(out/'complete.json',{'status':'COMPLETE_AWAITING_INDEPENDENT_ACCEPTANCE',
            'required':20,'executed':20,'businessReportsPassed':20,'resourceReportsVerified':20,
            'sloPassed':sum(r['sloPassed'] and r['seconds']<=600 for r in rows),
            'independentAcceptance':'PENDING','evidenceLevel':'OFFLINE_FIXTURE'})
        return 0
    except KeyboardInterrupt:
        return 1
    finally:
        write(out/'runner-cleanup.json',{'fixtureOnly':True,'at':time.time()})
if __name__=='__main__': raise SystemExit(main())
"""
NATIVE = """import argparse,hashlib,json,sys,time
from datetime import datetime,timezone
from pathlib import Path
P=Path(PROTOCOL_PATH)
def stamp(): return datetime.now(timezone.utc).isoformat()
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def write(path,obj): path.write_text(json.dumps(obj))
parser=argparse.ArgumentParser();parser.add_argument('--expected-sha');parser.add_argument('--out')
parser.add_argument('--interval',type=float);parser.add_argument('--max-seconds',type=float)
args=parser.parse_args();p=json.loads(P.read_text());fault=p['nativeFault'];cfg=p['execution']['supervision']
if fault in ('early0','early7'): raise SystemExit(7 if fault=='early7' else 0)
out=Path(args.out);out.mkdir();(out/'first-seen-lsof').mkdir()
plan={'expectedSha':args.expected_sha,'runtime':p['runtimeDir'],
    'toolSha256':sha(Path(__file__)),'nativeEnvSha256':p['configFreeze']['envSha256'],
    'taosConfigSha256':cfg['taosConfigSha256'],'intervalSeconds':args.interval,
    'maximumAcceptedIntervalSeconds':5,'maxSeconds':args.max_seconds,
    'stopFile':str(out/'STOP'),'fixtureOnly':True}
write(out/'plan.json',plan);path=out/'observations.jsonl';count=0
with path.open('x',buffering=1) as stream:
    while not (out/'STOP').exists():
        when=stamp();tick=time.monotonic()
        if fault=='gap' and count: tick+=6
        cmd={'startedAt':when,'finishedAt':when,'rc':0,'fixtureOnly':True}
        row={'sequence':count,'monotonicSeconds':tick,'observationValid':True,
            'gitCommand':{**cmd,'stdout':args.expected_sha+'\\n'+cfg['backendTreeSha']+'\\n'},
            'psCommand':cmd,'sourceShaObserved':args.expected_sha,'backendTreeObserved':cfg['backendTreeSha'],
            'nativeEnvSha256Observed':p['configFreeze']['envSha256'],
            'taosConfigSha256Observed':cfg['taosConfigSha256'],'firstSeenLsofRefs':[],
            'members':[],'selectedRawPsLines':[],'fixtureOnly':True}
        if fault=='invalid': row['observationValid']=False
        if fault=='ps_rc': row['psCommand']={**cmd,'rc':7}
        if fault=='env': row['nativeEnvSha256Observed']='wrong'
        if count==0:
            ref=out/'first-seen-lsof/first.json'
            write(ref,{'actualLsof':{**cmd,'rc':7 if fault=='lsof_rc' else 0,'stdout':'OFFLINE'}})
            row['firstSeenLsofRefs']=[{'path':str(ref),
                'sha256':'0'*64 if fault=='lsof' else sha(ref)}]
        stream.write(json.dumps(row)+'\\n');count+=1
        if count==5 and fault=='late7': raise SystemExit(7)
        if count==5 and fault=='drift':
            target=Path(p['tools']['runner']['path'])
            target.write_text(target.read_text()+'\\n# drift\\n')
        if count==5 and fault=='protocol_drift': P.write_text(P.read_text()+' ')
        if fault=='stall': time.sleep(60)
        time.sleep(.025)
if fault=='no_summary': raise SystemExit(0)
if fault=='prefix': path.write_text(path.read_text().replace('"sequence": 0','"sequence": 9',1))
summary={'stopReason':'requested-stop','errors':[],'expectedSha':args.expected_sha,
    'pollCount':count,'planSha256':sha(out/'plan.json'),'fixtureOnly':True,
    'observations':{'path':str(path),'bytes':path.stat().st_size,'sha256':sha(path)}}
if fault=='summary_hash': summary['observations']['sha256']='0'*64
write(out/'summary.json',summary)
"""


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def case(tmp_path):
    runtime = tmp_path / "runtime"
    (runtime / "backend").mkdir(parents=True)
    protocol, output = tmp_path / "protocol.json", tmp_path / "new/supervised-run"
    runner, native = tmp_path / "runner.py", tmp_path / "native.py"
    runner.write_text(RUNNER)
    native.write_text(NATIVE.replace("PROTOCOL_PATH", repr(str(protocol))))
    base = [
        sys.executable,
        "-B",
        str(SUPERVISOR),
        "--protocol",
        str(protocol),
        "--output-dir",
        str(output),
    ]
    p = {
        "candidateSha": "a" * 40,
        "runtimeDir": str(runtime),
        "formalReady": True,
        "readinessPending": [],
        "methodBindings": {},
        "configFreeze": {"status": "FROZEN", "envSha256": "b" * 64},
        "tools": {
            name: {"path": str(path), "sha256": sha(path)}
            for name, path in (
                ("supervisor", SUPERVISOR),
                ("runner", runner),
                ("nativeObserver", native),
            )
        },
        "runnerFault": None,
        "nativeFault": None,
        "execution": {
            "python": sys.executable,
            "cwd": str(runtime / "backend"),
            "evidenceRoot": str(tmp_path),
            "outputParent": str(output),
            "evidenceLevel": "OFFLINE_FIXTURE",
            "invocation": [
                sys.executable,
                "-B",
                str(runner),
                "--protocol",
                str(protocol),
                "--evidence-root",
                str(tmp_path),
            ],
            "supervision": {
                "outputParent": str(output.parent),
                "approvedInvocation": base,
                "nativeIntervalSeconds": 2,
                "maximumGapSeconds": 5,
                "requireSloPass": True,
                "pollSeconds": 0.01,
                "startupTimeoutSeconds": 2,
                "stopTimeoutSeconds": 0.3,
                "nativeMaxSeconds": 43200,
                "backendTreeSha": "c" * 40,
                "taosConfigSha256": "d" * 64,
            },
        },
    }

    def seal():
        protocol.write_text(json.dumps(p))

    seal()
    return p, output, base, seal


def execute(case, *extra):
    _, _, argv, seal = case
    seal()
    return subprocess.run(
        [*argv, *extra],
        capture_output=True,
        text=True,
        timeout=15,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )


def test_full_supervisor_main_preserves_raw_native_and_pending_acceptance(case):
    p, output, _, _ = case
    result = execute(case)
    assert result.returncode == 0, result.stderr
    complete = json.loads((output / "supervision-complete.json").read_text())
    assert complete["evidenceLevel"] == "OFFLINE_FIXTURE"
    assert complete["sevenIdentityAcceptance"] == complete["independentAcceptance"] == "PENDING"
    assert complete["native"]["observations"]["sha256"] == sha(output / "native/observations.jsonl")
    assert (output / "native/STOP").exists()
    for name in ("runner", "native-producer"):
        record = json.loads((output / (name + ".command.json")).read_text())
        assert record["rc"] == 0
        assert record["stdout"]["sha256"] == sha(output / (name + ".stdout.log"))
    assert len(json.loads((output / "series/outcomes.json").read_text())) == 20
    assert p["execution"]["supervision"]["requireSloPass"]
    rows = [
        json.loads(line) for line in (output / "native/observations.jsonl").read_text().splitlines()
    ]
    assert (
        rows[0]["monotonicSeconds"]
        < json.loads((output / "series/runner-clock.json").read_text())["startedMonotonic"]
    )
    assert (
        rows[-1]["monotonicSeconds"]
        > json.loads((output / "series/final-recovery.json").read_text())["finishedMonotonic"]
    )


@pytest.mark.parametrize(
    "fault",
    [
        "early0",
        "early7",
        "late7",
        "invalid",
        "gap",
        "ps_rc",
        "env",
        "lsof",
        "lsof_rc",
        "drift",
        "protocol_drift",
        "no_summary",
        "summary_hash",
        "prefix",
    ],
)
def test_native_failure_or_byte_drift_never_completes_supervision(case, fault):
    p, output, _, _ = case
    p["nativeFault"] = fault
    result = execute(case)
    assert result.returncode == 1, (fault, result.stderr)
    assert not (output / "supervision-complete.json").exists()
    assert (output / "supervision-failure.json").exists()
    record = json.loads((output / "native-producer.command.json").read_text())
    assert record["rc"] == (7 if fault in ("early7", "late7") else 0)
    if fault in ("early0", "early7", "invalid", "lsof", "lsof_rc", "env", "ps_rc"):
        assert not (output / "series").exists()


@pytest.mark.parametrize("fault", ["slo", "seconds", "failure"])
def test_runner_failure_or_strict_slo_stops_later_batches(case, fault):
    p, output, _, _ = case
    p["runnerFault"] = fault
    result = execute(case)
    assert result.returncode == 1, result.stderr
    assert not (output / "supervision-complete.json").exists()
    outcomes = json.loads((output / "series/outcomes.json").read_text())
    assert len(outcomes) < 20
    if fault != "failure":
        assert "600s SLO failed" in result.stderr
    assert (output / "series/runner-cleanup.json").exists()


@pytest.mark.parametrize(
    "mutation",
    [
        "existing",
        "self",
        "hash",
        "invocation",
        "parent",
        "runner_parent",
        "interval",
        "slo_contract",
        "not_ready",
    ],
)
def test_invalid_preflight_never_starts_native_or_runner(case, mutation):
    p, output, _, _ = case
    config = p["execution"]["supervision"]
    if mutation == "existing":
        output.mkdir(parents=True)
        (output / "old.json").write_text("OLD SIX BATCHES")
    elif mutation == "self":
        p["tools"]["supervisor"]["path"] = p["tools"]["runner"]["path"]
        p["tools"]["supervisor"]["sha256"] = p["tools"]["runner"]["sha256"]
    elif mutation == "hash":
        p["tools"]["nativeObserver"]["sha256"] = "0" * 64
    elif mutation == "invocation":
        config["approvedInvocation"] = ["wrong"]
    elif mutation == "parent":
        config["outputParent"] = str(output)
    elif mutation == "runner_parent":
        p["execution"]["outputParent"] = str(output.parent)
    elif mutation == "interval":
        config["maximumGapSeconds"] = 6
    elif mutation == "slo_contract":
        config["requireSloPass"] = False
    else:
        p["formalReady"] = False
    result = execute(case)
    assert result.returncode == 2, result.stderr
    assert not (output / "native-producer.stdout.log").exists()
    if mutation == "existing":
        assert (output / "old.json").read_text() == "OLD SIX BATCHES"


def test_check_only_does_not_admit_false_formal_ready(case):
    p, output, _, _ = case
    p["formalReady"] = False
    result = execute(case, "--check-only")
    assert result.returncode == 0 and not output.exists()
    assert json.loads(result.stdout)["formalReady"] is False


@pytest.mark.parametrize("number", [signal.SIGINT, signal.SIGTERM])
def test_signal_stops_owned_runner_and_native_without_claiming_acceptance(case, number):
    p, output, argv, seal = case
    p["runnerFault"] = "hold"
    seal()
    child = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 5
        while not (output / "series/outcomes.json").exists():
            assert time.monotonic() < deadline
            time.sleep(0.01)
        child.send_signal(number)
        stdout, stderr = child.communicate(timeout=5)
        assert child.returncode == 1, (stdout, stderr)
        assert (output / "series/runner-cleanup.json").exists()
        assert not (output / "supervision-complete.json").exists()
        assert json.loads((output / "supervision-failure.json").read_text())["signals"] == [number]
        for name in ("runner", "native-producer"):
            assert json.loads((output / (name + ".command.json")).read_text())["rc"] is not None
        assert json.loads((output / "runner.command.json").read_text())["rc"] == 1
        assert json.loads((output / "native-producer.command.json").read_text())["rc"] == 0
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def test_stalled_native_observer_aborts_runner_and_records_forced_owned_cleanup(case):
    p, output, _, _ = case
    p["nativeFault"], p["runnerFault"] = "stall", "hold"
    result = execute(case)
    assert result.returncode == 1 and "stalled" in result.stderr
    assert not (output / "supervision-complete.json").exists()
    assert (
        json.loads((output / "native-producer.command.json").read_text())["rc"] == -signal.SIGKILL
    )


def test_actual_invocation_cannot_add_unapproved_interpreter_flags(case):
    _, output, argv, _ = case
    result = subprocess.run([argv[0], "-u", *argv[1:]], capture_output=True, text=True, timeout=5)
    assert result.returncode == 2 and "actual argv differs" in result.stderr
    assert not output.exists()


@pytest.mark.parametrize(
    "mutation",
    [
        None,
        "foreign_launcher",
        "wrong_venv",
        "wrong_base",
        "foreign_framework",
        "flags",
        "script",
        "protocol",
    ],
)
def test_framework_argv_alias_requires_the_bound_interpreter_chain(tmp_path, monkeypatch, mutation):
    spec = importlib.util.spec_from_file_location("supervisor_argv_test", SUPERVISOR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    framework = tmp_path / "Python.framework/Versions/3.12"
    native = framework / "Resources/Python.app/Contents/MacOS/Python"
    base = framework / "bin/python3.12"
    venv = tmp_path / ".venv/bin/python"
    for path in (native, base):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("OFFLINE identity fixture")
    venv.parent.mkdir(parents=True)
    venv.symlink_to(base)
    monkeypatch.setattr(mod.sys, "platform", "darwin")
    monkeypatch.setattr(mod.sys, "base_prefix", str(framework))
    monkeypatch.setattr(mod.sys, "_base_executable", str(base))
    monkeypatch.setattr(mod.sys, "executable", str(venv))
    expected = [str(venv), "-B", str(SUPERVISOR), "--protocol", "/bound/protocol.json"]
    actual = [str(native), *expected[1:]]
    if mutation == "foreign_launcher":
        actual[0] = str(tmp_path / "unapproved/Python")
    elif mutation == "wrong_venv":
        monkeypatch.setattr(mod.sys, "executable", str(tmp_path / "other-venv/bin/python"))
    elif mutation == "wrong_base":
        monkeypatch.setattr(mod.sys, "_base_executable", str(native))
    elif mutation == "foreign_framework":
        monkeypatch.setattr(mod.sys, "base_prefix", str(tmp_path / "unapproved-framework"))
    elif mutation == "flags":
        actual.insert(1, "-u")
    elif mutation == "script":
        actual[2] = "/unapproved/capacity_series_supervisor.py"
    elif mutation == "protocol":
        actual[-1] = "/unapproved/protocol.json"
    assert mod.matches_invocation(actual, expected) is (mutation is None)
    if mutation is None:
        assert mod.matches_invocation([*actual, "--check-only"], expected)
