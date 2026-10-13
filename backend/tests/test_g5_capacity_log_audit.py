"""Independent log vectors: ambiguity/failure must never become capacity PASS."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.g5_capacity_log_audit import (
    _stats,
    audit_log,
    main,
    validate_report_path,
    write_report,
)

PARENT = "parent-1"


def _line(second: int, message: str) -> str:
    return f"2026-10-13 23:17:{second:02d},000 | INFO | fixture | {message}\n"


def _request(second=1, window="2026-10-14T01:00:00~2026-10-14T02:00:00"):
    return _line(
        second, f"DataPlanner.request_bundles: loop=L1, metrics=[], window={window}, control=T"
    )


def _phase(group: str, second: int, query=2, preprocess=1):
    return _line(
        second,
        f"DataPlanner 取数+预处理: loop=L1, tagGroup={group}, points=721, "
        f"query={query:.3f}s, preprocess={preprocess:.3f}s",
    )


def _fixture():
    return "".join(
        [
            _line(0, f"Task app.tasks.kpi_calc.calculate_hourly_kpi[{PARENT}] received"),
            _line(
                0,
                f"KPI 计算任务开始, celery_id={PARENT}, ts_start=2026-10-14T01:00:00, task_id=None",
            ),
            _request(),
            _line(1, "DataPlanner 回源查询: loop=L1, tagGroup=BASE, tags=['pv'], interval=5s"),
            _line(1, "DataPlanner 回源查询: loop=L1, tagGroup=OP_HF, tags=['op'], interval=1s"),
            _phase("BASE", 4),
            _phase("OP_HF", 4),
            _line(5, "DataPlanner 完成: loop=L1, bundles=25, cached_blocks=2"),
            _line(5, f"Task kpi[{PARENT}] succeeded in 5.0s: {{}}"),
            _line(5, "Task node[child-1] received"),
            _line(
                5,
                "TaskPool: Apply args ('id': 'child-1', 'task': 'node', "
                f"'root_id': '{PARENT}', 'parent_id': '{PARENT}')",
            ),
            _line(6, "Task node[child-1] succeeded in 1.0s: {}"),
            _line(6, "Task patrol[background-1] received"),
        ]
    )


def _audit(tmp_path, content=None):
    log = tmp_path / "worker.log"
    log.write_text(_fixture() if content is None else content)
    return audit_log(
        log,
        start="2026-10-13T23:17:00",
        end="2026-10-13T23:17:10",
        task_id=PARENT,
        log_timezone="Asia/Shanghai",
    )


def test_parallel_group_work_is_not_summed_as_wall_clock(tmp_path):
    report = _audit(tmp_path)
    assert report["plannerRequestToComplete"]["maxSeconds"] == 4
    assert report["queryTotalIncludingMetadataAndReconstruction"]["sumWorkSeconds"] == 4
    assert report["preprocessTotalIncludingThreadWait"]["sumWorkSeconds"] == 2
    assert report["queryTotalIncludingMetadataAndReconstruction"]["isWallClock"] is False
    assert report["sloEvaluated"] is report["capacityGateEvaluated"] is False
    assert report["boundChildTasks"][0]["parentId"] == PARENT
    assert report["backgroundTaskNameCounts"] == {"patrol": 1}
    assert report["scope"]["startUtc"] == "2026-10-13T15:17:00+00:00"
    assert report["missingProbes"]
    assert report["analysisStatus"] == "LOG_AUDIT_ONLY"


@pytest.mark.parametrize(
    "addition", [_request(2), _request(2, "2026-10-14T02:00:00~2026-10-14T03:00:00")]
)
def test_repeated_loop_is_explicitly_ambiguous(tmp_path, addition):
    report = _audit(tmp_path, _fixture() + addition)
    assert report["analysisStatus"] == "INCONCLUSIVE"
    assert report["perLoop"] == []
    assert report["ambiguousOrIncompleteLoops"][0]["requests"] == 2


def test_concurrent_same_window_parent_is_not_trusted(tmp_path):
    report = _audit(
        tmp_path,
        _fixture()
        + _line(2, "KPI 计算任务开始, celery_id=other, ts_start=2026-10-14T01:00:00, task_id=None"),
    )
    assert "CONCURRENT_HOURLY_TASK_SAME_WINDOW" in report["issues"]
    assert report["capacityGateEvaluated"] is False


def test_missing_log_evidence_is_not_success(tmp_path):
    report = _audit(tmp_path, "unparseable log line\n")
    assert report["analysisStatus"] == "INCONCLUSIVE"
    assert "NO_TIMESTAMPED_LOG_ROWS_IN_RANGE" in report["issues"]
    assert report["plannerRequestToComplete"]["count"] == 0


def test_failed_parent_and_incomplete_child_are_preserved(tmp_path):
    content = _fixture().replace(
        f"Task kpi[{PARENT}] succeeded in 5.0s: {{}}",
        f"Task kpi[{PARENT}] raised unexpected: RuntimeError('failed')",
    )
    content = content.replace(_line(6, "Task node[child-1] succeeded in 1.0s: {}"), "")
    report = _audit(tmp_path, content)
    assert "PARENT_FAILURE" in report["issues"]
    assert "CHILD_NON_SUCCESS_OR_TERMINAL_MISSING" in report["issues"]


def test_json_timestamped_log_and_partial_preprocess(tmp_path):
    text = _fixture().replace(
        _phase("BASE", 4),
        _line(4, "TDengine 返回空数据: loop=L1, tagGroup=BASE, query_time=2.000s"),
    )
    rows = []
    for line in text.splitlines():
        stamp, _, _, message = line.split(" | ")
        rows.append(
            json.dumps(
                {
                    "timestamp": stamp.replace(" ", "T").replace(",", ".") + "+08:00",
                    "message": message,
                }
            )
        )
    report = _audit(tmp_path, "\n".join(rows))
    assert report["queryTotalIncludingMetadataAndReconstruction"]["count"] == 2
    assert report["preprocessTotalIncludingThreadWait"]["count"] == 1
    assert report["perLoop"][0]["groupPhases"][0]["status"] == "EMPTY"


def test_report_path_is_checked_before_missing_input(capsys, tmp_path):
    with pytest.raises(SystemExit) as caught:
        main(
            [
                "--log",
                str(tmp_path / "absent.log"),
                "--start",
                "2026-10-13T23:17:00",
                "--end",
                "2026-10-13T23:17:10",
                "--task-id",
                PARENT,
                "--report",
                str(tmp_path / "absent" / "audit.json"),
            ]
        )
    assert caught.value.code == 2
    assert "report parent directory must already exist" in capsys.readouterr().err
    with pytest.raises(ValueError, match="absolute"):
        validate_report_path(Path("relative.json"))


def test_streams_instead_of_reading_entire_log(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("whole-file read forbidden")

    monkeypatch.setattr(Path, "read_text", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    report = _audit(tmp_path)
    output = tmp_path / "report.json"
    write_report(output, report)
    with output.open() as source:
        assert json.load(source)["analysisStatus"] == "LOG_AUDIT_ONLY"
    assert set(tmp_path.iterdir()) == {tmp_path / "worker.log", output}


def test_missing_input_and_reversed_range_fail(tmp_path):
    with pytest.raises(FileNotFoundError):
        audit_log(
            tmp_path / "absent.log",
            start="2026-10-13T23:17:00",
            end="2026-10-13T23:17:10",
            task_id=PARENT,
            log_timezone="Asia/Shanghai",
        )
    with pytest.raises(ValueError, match="end"):
        audit_log(
            tmp_path / "absent.log",
            start="2026-10-13T23:17:10",
            end="2026-10-13T23:17:00",
            task_id=PARENT,
            log_timezone="Asia/Shanghai",
        )


def test_cache_hit_has_explicit_skipped_stages(tmp_path):
    content = _fixture().replace(
        _line(5, "DataPlanner 完成: loop=L1, bundles=25, cached_blocks=2"),
        _line(5, "DataPlanner L2 命中，跳过查询+组装: loop=L1, bundles=25"),
    )
    content = "\n".join(
        line
        for line in content.splitlines()
        if "回源查询" not in line and "取数+预处理" not in line
    )
    report = _audit(tmp_path, content)
    assert report["perLoop"][0]["skippedStages"] == ["query", "preprocess", "assemble"]
    assert report["queryTotalIncludingMetadataAndReconstruction"]["count"] == 0


def test_beat_none_window_is_unknown_not_invented(tmp_path):
    report = _audit(
        tmp_path,
        _fixture().replace(
            "ts_start=2026-10-14T01:00:00, task_id=None", "ts_start=None, task_id=None"
        ),
    )
    assert report["scope"]["businessWindowUtc"] is None
    assert report["analysisStatus"] == "INCONCLUSIVE"


def test_uncached_complete_without_phases_is_incomplete(tmp_path):
    content = "\n".join(line for line in _fixture().splitlines() if "取数+预处理" not in line)
    report = _audit(tmp_path, content)
    assert "MISSING_PHASE_EVIDENCE_FOR_UNCACHED_LOOP" in report["issues"]
    assert report["analysisStatus"] == "INCONCLUSIVE"


def test_output_cannot_overwrite_log(tmp_path):
    log = tmp_path / "worker.log"
    log.write_text(_fixture())
    with pytest.raises(SystemExit) as caught:
        main(
            [
                "--log",
                str(log),
                "--start",
                "2026-10-13T23:17:00",
                "--end",
                "2026-10-13T23:17:10",
                "--task-id",
                PARENT,
                "--report",
                str(log),
            ]
        )
    assert caught.value.code == 2
    assert log.read_text() == _fixture()


def test_nearest_rank_p95_31_sample_counterexample():
    # 31 independently ordered samples: nearest rank 30 is value 29, not 28.
    stats = _stats(list(range(31)))
    assert stats["p95Seconds"] == 29
    assert stats["percentileMethod"] == "nearest-rank"


def _trace_line(second=7, **changes):
    trace = {
        "taskId": PARENT,
        "loopId": "L1",
        "windowStart": "2026-10-14T01:00:00",
        "windowEnd": "2026-10-14T02:00:00",
        "stage": "td.executor",
        "spanId": "job-1",
        "pid": 100,
        "outcome": "ok",
        "submitMonoNs": 10,
        "threadStartMonoNs": 20,
        "threadEndMonoNs": 30,
        "awaitReturnMonoNs": 40,
    }
    trace.update(changes)
    return _line(second, "G5_TRACE " + json.dumps(trace))


def test_trace_target_filter_identity_and_interval_clocks(tmp_path):
    log = _fixture() + _trace_line() + _trace_line(8, taskId="other") + _trace_line(9, taskId=None)
    report = _audit(tmp_path, log)
    trace = report["traceAudit"]
    assert trace["targetTraceCount"] == 1 and trace["otherTaskTraceCount"] == 1
    assert len(trace["unboundTraceRecords"]) == 1
    row = trace["targetTraceIntervalsAndThreadClocks"][0]
    assert row["clockOrder"] == "ORDERED" and row["clockKind"] == "thread-four-points"
    assert row["missingFields"] == []
    assert report["sloEvaluated"] is False


def test_cancelled_trace_missing_end_remains_incomplete(tmp_path):
    report = _audit(
        tmp_path, _fixture() + _trace_line(outcome="CancelledError", threadEndMonoNs=None)
    )
    row = report["traceAudit"]["targetTraceIntervalsAndThreadClocks"][0]
    assert row["outcome"] == "CancelledError" and row["clockOrder"] == "INCOMPLETE"
    assert "threadEndMonoNs" in row["missingFields"]


def test_cancelled_trace_can_finish_after_caller_return(tmp_path):
    report = _audit(
        tmp_path, _fixture() + _trace_line(outcome="CancelledError", threadEndMonoNs=50)
    )
    row = report["traceAudit"]["targetTraceIntervalsAndThreadClocks"][0]
    assert row["clockOrder"] == "CALLER_CANCELLED_THREAD_FINISHED_LATER"


def test_legacy_log_cannot_claim_new_probe_observation(tmp_path):
    trace = _audit(tmp_path)["traceAudit"]
    assert trace["newProbeObservedForTargetTask"] is False
    assert trace["status"] == "NO_TARGET_TRACE_RECORDS" and trace["targetTraceCount"] == 0


def test_trace_interval_and_malformed_json_are_explicit(tmp_path):
    content = (
        _fixture()
        + _line(7, "G5_TRACE {broken-json}")
        + _line(
            8,
            "G5_TRACE "
            + json.dumps(
                {
                    "taskId": PARENT,
                    "spanId": "interval-1",
                    "stage": "builder.roles",
                    "pid": 100,
                    "outcome": "ValueError",
                    "startMonoNs": 50,
                    "endMonoNs": 60,
                }
            ),
        )
    )
    trace = _audit(tmp_path, content)["traceAudit"]
    assert len(trace["parseErrors"]) == 1
    row = trace["targetTraceIntervalsAndThreadClocks"][0]
    assert row["clockKind"] == "interval" and row["clockOrder"] == "ORDERED"
    assert set(row["missingFields"]) == {"loopId", "windowStart", "windowEnd"}
