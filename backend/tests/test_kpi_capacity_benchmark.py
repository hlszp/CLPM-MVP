"""P5 容量工具单测（R05/R06 返修版：预检反例 / 时序编排 / 绑定核验 / 防伪通过）.

覆盖（17 号文 R05/R06 + 18 号文第 3 步验收）：
- R05：latest 迟到冲突预检反例（人为冲突态被拒）；多窗编排单调升序与
  连续闭合的结构性证明；降序/迟到/单窗拒绝；
- R06：投递前冻结（eligible/excluded/manifest 身份/expected 直方图）；
  完成屏障绑定核验（run 绑定/版本/expectedStatus/latest/节点/任务终态）；
- 防伪反例：旧结果污染、错版本、全部 INCONCLUSIVE（正常组）、缺节点、
  missing、父子任务未终结——均不得 PASS；
- dry-run 零投递零落盘；SLO 分母完整输出；
- 既有：custom 重放门禁、pageSize 翻页（P5 批 α 修复不回退）。
"""

from __future__ import annotations

import argparse
import contextlib
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scripts.kpi_capacity_benchmark import (
    _ALLOWED_SOURCES,
    _PREDICTED_NODES_STRUCTURAL_SQL,
    _build_freeze,
    _check_plan_windows,
    _evaluate_run_gate,
    _expected_snapshot_status,
    _frozen_window,
    _load_loops,
    _print_gate_report,
    _replay_windows,
    run_standard_hourly,
)

WINDOW = datetime(2026, 10, 13, 2, 0, 0)
ALGO = "KPI_CALC_v2.0"
PARENT = "parent-celery-id-0001"


def _httpx_page(items: list[dict], total: int) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"data": {"items": items, "total": total}}
    return resp


class TestLoadLoopsPagination:
    """P5 批 α §3.4-1/3 回归：pageSize 参数名 + 逐页拉全量 + 去重。"""

    def test_使用pageSize参数并翻页拉全量(self):
        client = MagicMock()
        page1 = _httpx_page([{"loopId": f"L{i}"} for i in range(100)], total=150)
        page2 = _httpx_page([{"loopId": f"L{i}"} for i in range(100, 150)], total=150)
        client.get.side_effect = [page1, page2]

        ids = _load_loops(client, "http://x/api/v1", {}, page_size=100)

        assert len(ids) == 150
        params_used = [c.kwargs["params"] for c in client.get.call_args_list]
        assert all(p["pageSize"] == 100 for p in params_used)
        assert [p["page"] for p in params_used] == [1, 2]

    def test_total满足即停不再空页探测(self):
        client = MagicMock()
        page1 = _httpx_page([{"loopId": "L1"}, {"loopId": "L2"}], total=2)
        client.get.side_effect = [page1]

        ids = _load_loops(client, "http://x/api/v1", {}, page_size=100)
        assert ids == ["L1", "L2"]
        assert client.get.call_count == 1


class TestFrozenWindow:
    def test_缺省取上一个完整小时(self):
        win = _frozen_window(None)
        assert win.minute == 0 and win.second == 0 and win.microsecond == 0

    def test_显式ISO解析为naiveUTC(self):
        win = _frozen_window("2026-10-13T08:00:00Z")
        assert win == datetime(2026, 10, 13, 8, 0, 0)
        assert win.tzinfo is None

    def test_非整点拒绝(self):
        with pytest.raises(SystemExit, match="整点"):
            _frozen_window("2026-10-13T08:30:00")


def _sh_args(**kw) -> argparse.Namespace:
    base: dict = {
        "mode": "standard-hourly",
        "base_url": "http://localhost:17101/api/v1",
        "username": "admin",
        "password": "admin123",
        "loops": 1000,
        "ts_start": "2026-10-13T02:00:00",
        "manifest": None,
        "plan_windows": None,
        "freeze_dir": "/tmp/never-written-freezes",
        "report_out": None,
        "expect_eligible": 2,
        "barrier_loop_prefix": "PERF-",
        "timeout": 0.6,
        "slo_seconds": 600.0,
        "poll_interval": 0.2,
        "page_size": 100,
        "retry_seconds": 60.0,
        "batch_spacing": 2.0,
        "allow_replay": False,
        "dry_run": True,
    }
    base.update(kw)
    return argparse.Namespace(**base)


def _write_manifest(
    tmp_path: Path, *, window_start: str | None = None, name: str = "manifest_b_test.json"
) -> Path:
    rows = [
        {
            "loopTag": "PERF-T0001",
            "composition": "normal",
            "expectedStatus": "EVALUATED",
            "scenario": "b",
        },
        {
            "loopTag": "PERF-T0002",
            "composition": "insufficient",
            "expectedStatus": "INCONCLUSIVE(DATA_INSUFFICIENT)",
            "scenario": "b",
        },
    ]
    payload = {
        "synthetic": True,
        "scenario": "b",
        "totalLoops": len(rows),
        "windowStart": window_start or "2026-10-13T02:00:00",
        "rows": rows,
    }
    path = tmp_path / name
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _universe_rows(*, extra_tag: str | None = None) -> list[dict]:
    rows = [
        {
            "loopId": "id-1",
            "loopTag": "PERF-T0001",
            "isActive": True,
            "ledgerStatus": "READY",
            "includeInEvaluation": True,
        },
        {
            "loopId": "id-2",
            "loopTag": "PERF-T0002",
            "isActive": True,
            "ledgerStatus": "READY",
            "includeInEvaluation": True,
        },
    ]
    if extra_tag:
        rows.append(
            {
                "loopId": "id-x",
                "loopTag": extra_tag,
                "isActive": True,
                "ledgerStatus": "READY",
                "includeInEvaluation": True,
            }
        )
    return rows


def _empty_barrier() -> dict:
    return {"ledger_rows": [], "latest_rows": [], "node_rows": []}


def _base_patches(
    tmp_path: Path, *, universe=None, latest_conflicts=None, barrier0=None, with_barrier=True
):
    """预检依赖统一 mock（ExitStack 进入 + manifest 路径）。

    用法::

        staged = _base_patches(tmp_path)
        with _stacked(staged):
            await run_standard_hourly(_sh_args(manifest=str(staged["manifest"])))

    with_barrier=False 时不创建 _barrier_snapshot patch，供外层用序列化
    side_effect 覆盖同目标（mock 后进先出，内层 patch 生效）。
    """
    manifest_path = _write_manifest(tmp_path)
    patches = [
        patch(
            "scripts.kpi_capacity_benchmark._preflight_loop_universe_full",
            AsyncMock(return_value=universe if universe is not None else _universe_rows()),
        ),
        patch(
            "scripts.kpi_capacity_benchmark._preflight_latest_conflict",
            AsyncMock(return_value=latest_conflicts or []),
        ),
        patch(
            "scripts.kpi_capacity_benchmark._predict_nodes_structural",
            AsyncMock(return_value=["node-1"]),
        ),
        patch(
            "scripts.kpi_capacity_benchmark._current_algorithm_version",
            MagicMock(return_value=ALGO),
        ),
    ]
    if with_barrier:
        patches.append(
            patch(
                "scripts.kpi_capacity_benchmark._barrier_snapshot",
                AsyncMock(return_value=barrier0 if barrier0 is not None else _empty_barrier()),
            )
        )
    return {"manifest": manifest_path, "patches": patches}


@contextlib.contextmanager
def _stacked(staged: dict):
    """按序进入 staged patches。"""
    with contextlib.ExitStack() as stack:
        for p in staged["patches"]:
            stack.enter_context(p)
        yield


# ---------------------------------------------------------------------------
# R05：latest 冲突预检 + 多窗编排
# ---------------------------------------------------------------------------


class TestLatestPreflightConflict:
    """R05 预检反例：人为造冲突态（既有 latest 晚于被测窗）必须被拒绝。"""

    @pytest.mark.asyncio
    async def test_迟到冲突即拒绝投递并列明(self, tmp_path):
        staged = _base_patches(
            tmp_path,
            latest_conflicts=[{"loopId": "id-1", "latestDataTsEnd": "2026-10-13T00:00:00"}],
        )
        submit_spy = MagicMock()
        with patch("app.tasks.kpi_calc.calculate_hourly_kpi", submit_spy), _stacked(staged):
            with pytest.raises(SystemExit, match="R05 拒绝投递") as exc_info:
                await run_standard_hourly(_sh_args(manifest=str(staged["manifest"])))
        assert "2026-10-13T00:00:00" in str(exc_info.value)
        submit_spy.delay.assert_not_called()

    @pytest.mark.asyncio
    async def test_同窗既有结果行拒绝投递(self, tmp_path):
        polluted = {
            "ledger_rows": [
                {
                    "loopId": "id-1",
                    "status": "SUCCESS",
                    "score": 88.0,
                    "algorithmVersion": ALGO,
                    "source": "MANUAL_STANDARD",
                    "sourceTaskId": "tr-old",
                    "resultRecordId": "rec-old",
                    "recordLogicalRunId": "other-run",
                    "fitnessTags": {},
                }
            ],
            "latest_rows": [],
            "node_rows": [],
        }
        staged = _base_patches(tmp_path, barrier0=polluted)
        with _stacked(staged):
            with pytest.raises(SystemExit, match="同窗重投"):
                await run_standard_hourly(_sh_args(manifest=str(staged["manifest"])))


class TestPlanWindows:
    """R05 时序编排：单调升序 + 现状无冲突 = 连续多窗闭合的结构性证明。"""

    def _windows(self, *hours: int) -> list[datetime]:
        return [datetime(2026, 10, 13, h, 0, 0) for h in hours]

    def test_升序计划通过且构成连续闭合证明(self):
        verdict = _check_plan_windows(self._windows(2, 3, 4, 5, 6, 7, 8, 9, 10, 11), {})
        assert verdict["ok"] is True
        assert len(verdict["windows"]) == 10

    def test_降序计划拒绝_先新后旧永不闭合(self):
        verdict = _check_plan_windows(self._windows(20, 16), {})
        assert verdict["ok"] is False
        assert any("严格单调升序" in p for p in verdict["problems"])

    def test_乱序计划拒绝(self):
        verdict = _check_plan_windows(self._windows(2, 4, 3), {})
        assert verdict["ok"] is False

    def test_现状latest晚于计划窗即冲突_人为冲突态被拒(self):
        # 场景：既有 latest 已推进到 2026-10-13T00:00（Beat 22:00/23:00 窗产物），
        # 计划窗仍是 2026-10-12 的旧窗——全部构成迟到窗，必须逐窗拒绝。
        latest = {"id-1": "2026-10-13T00:00:00", "id-2": "2026-10-12T23:30:00"}
        old_windows = [datetime(2026, 10, 12, h, 0, 0) for h in (12, 13, 14)]
        verdict = _check_plan_windows(old_windows, latest)
        assert verdict["ok"] is False
        assert verdict["conflictByWindow"], "冲突窗必须逐窗列明"
        assert verdict["conflictByWindow"]["2026-10-12T12:00:00"][0]["loopId"] == "id-1"
        assert len(verdict["conflictByWindow"]) == 3, "三个旧窗全部冲突"

    def test_现状latest不晚于计划首窗则全部通过(self):
        latest = {"id-1": "2026-10-12T03:00:00"}  # 首窗 12:00 终点=13:00 > latest，可更新
        verdict = _check_plan_windows(self._windows(2, 3, 4), latest)
        assert verdict["ok"] is True

    def test_少于两窗无法构成跨窗闭合证明(self):
        verdict = _check_plan_windows(self._windows(2), {})
        assert verdict["ok"] is False
        assert any("至少需要两个窗口" in p for p in verdict["problems"])


# ---------------------------------------------------------------------------
# R06：投递前冻结与身份核对
# ---------------------------------------------------------------------------


class TestFreezeIdentity:
    @pytest.mark.asyncio
    async def test_缺manifest拒绝执行(self, tmp_path):
        with pytest.raises(SystemExit, match="必须提供 --manifest"):
            await run_standard_hourly(_sh_args(manifest=None))

    @pytest.mark.asyncio
    async def test_缺前缀拒绝执行(self, tmp_path):
        with pytest.raises(SystemExit, match="barrier-loop-prefix"):
            await run_standard_hourly(_sh_args(barrier_loop_prefix=None))

    @pytest.mark.asyncio
    async def test_manifest窗口与被测窗不一致拒绝(self, tmp_path):
        manifest_path = _write_manifest(
            tmp_path, window_start="2026-10-12T20:00:00", name="manifest_wrong_window.json"
        )
        staged = _base_patches(tmp_path)
        with _stacked(staged):
            with pytest.raises(SystemExit, match="windowStart"):
                await run_standard_hourly(_sh_args(manifest=str(manifest_path), expect_eligible=2))

    @pytest.mark.asyncio
    async def test_manifest与eligible身份不一致拒绝(self, tmp_path):
        staged = _base_patches(tmp_path, universe=_universe_rows(extra_tag="PERF-T0003"))
        with _stacked(staged):
            with pytest.raises(SystemExit, match="测试身份核对失败"):
                await run_standard_hourly(
                    _sh_args(manifest=str(staged["manifest"]), expect_eligible=3)
                )

    @pytest.mark.asyncio
    async def test_三条件计数不足即拒绝_禁止重放凑数(self, tmp_path):
        staged = _base_patches(
            tmp_path,
            universe=[_universe_rows()[0]],  # 仅 1 个 eligible
        )
        with _stacked(staged):
            with pytest.raises(SystemExit, match="禁止重放凑数"):
                await run_standard_hourly(_sh_args(manifest=str(staged["manifest"])))

    def test_expectedStatus语义映射与未知拒绝(self):
        assert _expected_snapshot_status("EVALUATED") == "SUCCESS"
        assert _expected_snapshot_status("EVALUATED(ELIGIBLE内终态; MODE=MANUAL)") == "SUCCESS"
        assert _expected_snapshot_status("INCONCLUSIVE(DATA_INSUFFICIENT)") == "INCONCLUSIVE"
        with pytest.raises(SystemExit, match="语义未知"):
            _expected_snapshot_status("PARTIAL-ish")

    def test_冻结件包含excluded原因与expected直方图(self, tmp_path):
        manifest_path = _write_manifest(tmp_path)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        eligible = _universe_rows()[:2]
        excluded = [
            {"loopId": "id-9", "loopTag": "PERF-T0009", "reasons": ["include_in_evaluation=false"]}
        ]
        freeze = _build_freeze(
            ts_start=WINDOW,
            manifest=manifest,
            manifest_path=str(manifest_path),
            eligible=eligible,
            excluded=excluded,
            predicted_nodes=["node-1"],
            algorithm_version=ALGO,
            prefix="PERF-",
        )
        assert freeze["algorithmVersion"] == ALGO
        assert freeze["predictedNodes"] == ["node-1"]
        assert freeze["excluded"] == excluded
        assert freeze["expectedStatusHistogram"] == {
            "EVALUATED": 1,
            "INCONCLUSIVE(DATA_INSUFFICIENT)": 1,
        }
        by_tag = {row["loopTag"]: row for row in freeze["eligible"]}
        assert by_tag["PERF-T0001"]["loopId"] == "id-1"
        assert by_tag["PERF-T0001"]["expectedSnapshotStatus"] == "SUCCESS"
        assert by_tag["PERF-T0002"]["expectedSnapshotStatus"] == "INCONCLUSIVE"


# ---------------------------------------------------------------------------
# R06：绑定核验 + 防伪通过反例（纯函数级）
# ---------------------------------------------------------------------------


def _freeze_fixture() -> dict:
    eligible_rows = [
        {
            "loopId": "id-1",
            "loopTag": "PERF-T0001",
            "composition": "normal",
            "expectedStatus": "EVALUATED",
            "expectedSnapshotStatus": "SUCCESS",
        },
        {
            "loopId": "id-2",
            "loopTag": "PERF-T0002",
            "composition": "insufficient",
            "expectedStatus": "INCONCLUSIVE(DATA_INSUFFICIENT)",
            "expectedSnapshotStatus": "INCONCLUSIVE",
        },
    ]
    return {
        "window": {"tsStart": WINDOW.isoformat(), "tsEnd": "2026-10-13T03:00:00"},
        "algorithmVersion": ALGO,
        "eligible": eligible_rows,
        "excluded": [],
        "predictedNodes": ["node-1"],
        "expectedStatusHistogram": {
            "EVALUATED": 1,
            "INCONCLUSIVE(DATA_INSUFFICIENT)": 1,
        },
    }


def _ledger_row(loop_id: str, **over) -> dict:
    row = {
        "loopId": loop_id,
        "status": "SUCCESS",
        "score": 88.5,
        "algorithmVersion": ALGO,
        "source": "MANUAL_STANDARD",
        "sourceTaskId": "tr-1",
        "resultRecordId": "rec-1",
        "recordLogicalRunId": PARENT,
        "fitnessTags": {"tags": []},
    }
    row.update(over)
    return row


def _full_pass_barrier() -> dict:
    return {
        "ledger_rows": [
            _ledger_row("id-1", fitnessTags={"tags": []}),
            _ledger_row(
                "id-2",
                status="INCONCLUSIVE",
                score=None,
                fitnessTags={"tags": ["DATA_INSUFFICIENT"]},
            ),
        ],
        "latest_rows": [
            {
                "loopId": "id-1",
                "algorithmVersion": ALGO,
                "resultRecordId": "rec-1",
                "recordLogicalRunId": PARENT,
            },
            {
                "loopId": "id-2",
                "algorithmVersion": ALGO,
                "resultRecordId": "rec-2",
                "recordLogicalRunId": PARENT,
            },
        ],
        "node_rows": [{"plantNodeId": "node-1", "algorithmVersion": ALGO}],
    }


def _gate(barrier: dict, **over) -> dict:
    kwargs = {
        "parent_task_id": PARENT,
        "parent_state": "SUCCESS",
        "task_record_status": "SUCCESS",
    }
    kwargs.update(over)
    return _evaluate_run_gate(_freeze_fixture(), barrier, **kwargs)


class TestRunGateBinding:
    def test_全部条件满足即通过(self):
        gate = _gate(_full_pass_barrier())
        assert gate["ok"] is True
        assert all(gate["conditions"].values())
        d = gate["denominators"]
        assert d["frozenEligible"] == 2
        assert d["success"] == 1 and d["inconclusive"] == 1

    def test_旧结果污染不得通过_绑定其他run的同窗行(self):
        barrier = _full_pass_barrier()
        barrier["ledger_rows"][0]["recordLogicalRunId"] = "other-logical-run"
        barrier["ledger_rows"][0]["resultRecordId"] = "rec-old"
        gate = _gate(barrier)
        assert gate["ok"] is False
        assert gate["conditions"]["run_binding"] is False
        assert gate["violations"]["runBinding"]["count"] == 1

    def test_错算法版本不得通过(self):
        barrier = _full_pass_barrier()
        barrier["ledger_rows"][0]["algorithmVersion"] = "KPI_CALC_v1.0"
        barrier["latest_rows"][0]["algorithmVersion"] = "KPI_CALC_v1.0"
        barrier["node_rows"][0]["algorithmVersion"] = "KPI_CALC_v1.0"
        gate = _gate(barrier)
        assert gate["ok"] is False
        assert gate["conditions"]["algorithm_version"] is False
        assert gate["conditions"]["latest"] is False
        assert gate["conditions"]["nodes"] is False

    def test_全部INCONCLUSIVE正常组不得通过(self):
        barrier = _full_pass_barrier()
        barrier["ledger_rows"][0]["status"] = "INCONCLUSIVE"
        barrier["ledger_rows"][0]["score"] = None
        gate = _gate(barrier)
        assert gate["ok"] is False
        assert gate["conditions"]["expected_status"] is False

    def test_EVALUATED无score不得通过_完整有效评分要求(self):
        barrier = _full_pass_barrier()
        barrier["ledger_rows"][0]["score"] = None
        gate = _gate(barrier)
        assert gate["conditions"]["expected_status"] is False

    def test_DATA_INSUFFICIENT标签缺失不得通过(self):
        barrier = _full_pass_barrier()
        barrier["ledger_rows"][1]["fitnessTags"] = {"tags": []}
        gate = _gate(barrier)
        assert gate["conditions"]["expected_status"] is False

    def test_缺节点不得通过(self):
        barrier = _full_pass_barrier()
        barrier["node_rows"] = []
        gate = _gate(barrier)
        assert gate["ok"] is False
        assert gate["conditions"]["nodes"] is False
        assert gate["violations"]["nodeMissing"]["count"] == 1

    def test_missing回路不得通过_pending_missing非零即未闭合(self):
        barrier = _full_pass_barrier()
        barrier["ledger_rows"] = barrier["ledger_rows"][:1]
        barrier["latest_rows"] = barrier["latest_rows"][:1]
        gate = _gate(barrier)
        assert gate["conditions"]["ledger"] is False
        assert gate["conditions"]["latest"] is False
        assert gate["violations"]["missingLedger"]["count"] == 1

    def test_外来source不得通过(self):
        barrier = _full_pass_barrier()
        barrier["ledger_rows"][0]["source"] = "BACKFILL"
        gate = _gate(barrier)
        assert gate["conditions"]["source"] is False

    def test_父任务与TaskRecord未终结均不得通过(self):
        barrier = _full_pass_barrier()
        gate = _gate(barrier, task_record_status="RUNNING")
        assert gate["conditions"]["task_terminal"] is False
        assert gate["ok"] is False
        gate2 = _gate(barrier, parent_state="RETRY")
        assert gate2["conditions"]["parent_terminal"] is False
        assert gate2["ok"] is False
        gate3 = _gate(barrier, parent_state=None, task_record_status=None)
        assert gate3["ok"] is False

    def test_空结果不得让行级条件vacuous满足_防伪(self):
        # 零行时 source/run_binding/version/expected_status 不因"无违约"而满足
        gate = _gate({"ledger_rows": [], "latest_rows": [], "node_rows": []})
        assert gate["ok"] is False
        for name in ("ledger", "source", "run_binding", "algorithm_version", "expected_status"):
            assert gate["conditions"][name] is False, name

    def test_latest未更新不得通过(self):
        barrier = _full_pass_barrier()
        barrier["latest_rows"] = []
        gate = _gate(barrier)
        assert gate["conditions"]["latest"] is False
        assert gate["violations"]["latestMissing"]["count"] == 2


class TestSloDenominators:
    def test_屏障报告输出完整分母_失败不缩分母(self, capsys):
        barrier = _full_pass_barrier()
        barrier["ledger_rows"][0]["recordLogicalRunId"] = "other-run"
        gate = _gate(barrier)
        _print_gate_report(gate, PARENT, "RUNNING")
        out = capsys.readouterr().out
        assert "冻结 eligible=2" in out
        assert "excluded(跳过)=0" in out
        assert "SUCCESS=1" in out and "INCONCLUSIVE=1" in out
        assert "违约[runBinding]: count=1" in out


class TestBarrierSqlContract:
    def test_允许来源常量不变(self):
        assert set(_ALLOWED_SOURCES) == {"SCHEDULED", "MANUAL_STANDARD"}

    def test_预定节点为结构推导_不依赖运行中快照(self):
        # R06：节点集冻结不得从 kpi_snapshot_hourly 反推（原缺陷：动态推导）
        assert "kpi_snapshot_hourly" not in _PREDICTED_NODES_STRUCTURAL_SQL
        assert "is_kpi_enabled" in _PREDICTED_NODES_STRUCTURAL_SQL
        assert "ANY(:ids)" in _PREDICTED_NODES_STRUCTURAL_SQL
        assert "RECURSIVE" in _PREDICTED_NODES_STRUCTURAL_SQL


# ---------------------------------------------------------------------------
# 端到端（mock 级）：dry-run 与违约批退出码
# ---------------------------------------------------------------------------


class TestStandardHourlyDryRun:
    @pytest.mark.asyncio
    async def test_dry_run通过_零投递_零落盘(self, tmp_path, capsys):
        staged = _base_patches(tmp_path)
        submit_spy = MagicMock()
        freeze_dir = tmp_path / "freezes"
        with patch("app.tasks.kpi_calc.calculate_hourly_kpi", submit_spy), _stacked(staged):
            rc = await run_standard_hourly(
                _sh_args(
                    manifest=str(staged["manifest"]),
                    dry_run=True,
                    freeze_dir=str(freeze_dir),
                )
            )
        assert rc == 0
        out = capsys.readouterr().out
        assert "独立 loopId 断言: 2 unique" in out
        assert "R05 latest 冲突预检: 0 冲突" in out
        assert "冻结算法版本" in out
        assert "[dry-run] 计划" in out
        submit_spy.delay.assert_not_called()
        assert not freeze_dir.exists(), "dry-run 不得落盘冻结件"

    @pytest.mark.asyncio
    async def test_编排预检通过并回显计划(self, tmp_path, capsys):
        staged = _base_patches(tmp_path)
        with (
            patch("scripts.kpi_capacity_benchmark._latest_ends", AsyncMock(return_value={})),
            _stacked(staged),
        ):
            rc = await run_standard_hourly(
                _sh_args(
                    manifest=str(staged["manifest"]),
                    dry_run=True,
                    plan_windows="2026-10-13T02:00,2026-10-13T03:00",
                )
            )
        assert rc == 0
        assert "R05 编排预检" in capsys.readouterr().out

    @pytest.mark.asyncio
    async def test_编排预检冲突即拒绝(self, tmp_path):
        staged = _base_patches(tmp_path)
        latest = {"id-1": "2026-10-13T05:00:00"}  # 晚于计划两窗（终点 03:00/04:00）
        with (
            patch("scripts.kpi_capacity_benchmark._latest_ends", AsyncMock(return_value=latest)),
            _stacked(staged),
        ):
            with pytest.raises(SystemExit, match="R05 编排预检拒绝"):
                await run_standard_hourly(
                    _sh_args(
                        manifest=str(staged["manifest"]),
                        dry_run=True,
                        plan_windows="2026-10-13T02:00,2026-10-13T03:00",
                    )
                )


class TestViolatedBatchExitCode:
    @pytest.mark.asyncio
    async def test_违约批退出码非0_SLO分母完整输出(self, tmp_path, capsys):
        staged = _base_patches(tmp_path, with_barrier=False)
        # 投递前干净；投递后屏障始终违约（旧结果污染 + 任务未终结）
        polluted = _full_pass_barrier()
        polluted["ledger_rows"][0]["recordLogicalRunId"] = "other-run"
        barrier_mock = AsyncMock(side_effect=[_empty_barrier(), polluted, polluted, polluted])

        submit_result = MagicMock()
        submit_result.id = PARENT
        submit_result.state = "RUNNING"
        submit_spy = MagicMock()
        submit_spy.delay.return_value = submit_result

        report_out = tmp_path / "report.json"
        with (
            patch("app.tasks.kpi_calc.calculate_hourly_kpi", submit_spy),
            patch(
                "scripts.kpi_capacity_benchmark._barrier_snapshot",
                barrier_mock,
            ),
            patch(
                "scripts.kpi_capacity_benchmark._task_record_status",
                AsyncMock(return_value="RUNNING"),
            ),
            patch(
                "scripts.kpi_capacity_benchmark._observe_child_task_ids",
                AsyncMock(return_value=[]),
            ),
            _stacked(staged),
        ):
            rc = await run_standard_hourly(
                _sh_args(
                    manifest=str(staged["manifest"]),
                    dry_run=False,
                    freeze_dir=str(tmp_path / "freezes"),
                    report_out=str(report_out),
                )
            )
        assert rc == 1
        out = capsys.readouterr().out
        assert "SLO <= 600s: 失败" in out
        assert "分母" in out and "跳过(excluded)" in out
        assert "违约[runBinding]: count=1" in out
        # 冻结件投递前已落盘（R06：先冻结后投递）
        freezes = list((tmp_path / "freezes").glob("freeze_*.json"))
        assert len(freezes) == 1
        report = json.loads(report_out.read_text(encoding="utf-8"))
        assert report["parentTaskId"] == PARENT
        assert report["gate"]["ok"] is False
        assert report["taskRecordStatus"] == "RUNNING"


class TestCustomReplayGuard:
    def test_replay窗口切分(self):
        batches = _replay_windows(10, ["A", "B", "C"])
        assert [len(b[1]) for b in batches] == [3, 3, 3, 1]

    @pytest.mark.asyncio
    async def test_少ID重放未显式allow即拒绝(self):
        import scripts.kpi_capacity_benchmark as bench

        login_resp = MagicMock()
        login_resp.raise_for_status = MagicMock()
        login_resp.json.return_value = {"data": {"accessToken": "t"}}
        mock_client = MagicMock()
        mock_client.post.return_value = login_resp
        ctx = MagicMock()
        ctx.__enter__.return_value = mock_client
        ctx.__exit__.return_value = False
        with (
            patch.object(bench.httpx, "Client", MagicMock(return_value=ctx)),
            patch.object(bench, "_load_loops", MagicMock(return_value=["A", "B"])),
        ):
            with pytest.raises(SystemExit, match="--allow-replay"):
                bench.run_custom(
                    _sh_args(mode="custom", loops=1000, allow_replay=False, dry_run=False)
                )


class TestTerminalEvidenceCapture:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "outcome", ["mismatch", "pending_then_pass", "parent_failed", "interrupted"]
    )
    async def test_终态失败及时保存_未完成继续等_中断保留最近证据(self, tmp_path, outcome):
        staged = _base_patches(tmp_path, with_barrier=False)
        observed = _full_pass_barrier()
        if outcome == "mismatch":
            observed["ledger_rows"][0].update(status="INCONCLUSIVE", score=None)
        elif outcome in {"pending_then_pass", "interrupted"}:
            observed["node_rows"] = []
        snapshots = [_empty_barrier(), observed]
        if outcome == "pending_then_pass":
            snapshots.append(_full_pass_barrier())
        else:
            # 已确定失败不能继续等到后续批次改写 latest，丢失首份证据。
            snapshots.append(RuntimeError("later poll must not erase evidence"))
        barrier = AsyncMock(side_effect=snapshots)
        submit = MagicMock()
        submit.delay.return_value.id = PARENT
        submit.delay.return_value.state = "FAILURE" if outcome == "parent_failed" else "SUCCESS"
        report_out = tmp_path / "report.json"
        sleep = AsyncMock()
        with (
            patch("app.tasks.kpi_calc.calculate_hourly_kpi", submit),
            patch("scripts.kpi_capacity_benchmark._barrier_snapshot", barrier),
            patch(
                "scripts.kpi_capacity_benchmark._task_record_status",
                AsyncMock(return_value="SUCCESS"),
            ),
            patch(
                "scripts.kpi_capacity_benchmark._observe_child_task_ids", AsyncMock(return_value=[])
            ),
            patch("scripts.kpi_capacity_benchmark.asyncio.sleep", sleep),
            _stacked(staged),
        ):
            args = _sh_args(
                manifest=str(staged["manifest"]),
                dry_run=False,
                freeze_dir=str(tmp_path / "freezes"),
                report_out=str(report_out),
                timeout=60,
            )
            if outcome == "interrupted":
                with pytest.raises(RuntimeError, match="later poll"):
                    await run_standard_hourly(args)
            else:
                rc = await run_standard_hourly(args)
                assert rc == (0 if outcome == "pending_then_pass" else 1)
        report = json.loads(report_out.read_text())
        expected_reason = {
            "mismatch": "TERMINAL_MISMATCH",
            "pending_then_pass": "BARRIER_PASSED",
            "parent_failed": "PARENT_FAILED",
            "interrupted": "RUNNING",
        }[outcome]
        assert report["completionReason"] == expected_reason
        assert report["parentTaskId"] == PARENT
        assert report["gate"]["denominators"]["frozenEligible"] == 2
        assert report["sloPassed"] is (outcome == "pending_then_pass")
        if outcome in {"mismatch", "parent_failed"}:
            sleep.assert_not_awaited()
            assert barrier.await_count == 2
        if outcome == "mismatch":
            assert report["gate"]["violations"]["expectedStatus"]["count"] == 1
            assert report["gate"]["conditions"]["latest"] is True


def test_缺口预登记INCONCLUSIVE不得按实际SUCCESS重解释为通过():
    freeze = _freeze_fixture()
    freeze["eligible"][0].update(
        expectedStatus="INCONCLUSIVE(窗口覆盖不足——缺口段按 held/coverage 语义)",
        expectedSnapshotStatus="INCONCLUSIVE",
    )
    gate = _evaluate_run_gate(
        freeze,
        _full_pass_barrier(),
        parent_task_id=PARENT,
        parent_state="SUCCESS",
        task_record_status="SUCCESS",
    )
    assert gate["conditions"]["expected_status"] is False
    assert gate["violations"]["expectedStatus"]["count"] == 1
    assert gate["ok"] is False
