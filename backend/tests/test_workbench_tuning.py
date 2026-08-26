"""A-04/A-13 工作台整定聚合 service 单测（M2 批次 G-整定）.

覆盖：
- 纯 shaper：resolve_batch_status（B-06 阻塞判定/终态透传/自动解除）、
  shape_batches（前置依赖 pills/评分变化/排序）、shape_pending_queue（批次阻塞 >
  同回路工单阻塞/优先级阈值/建议来源/阻塞沉底）、shape_scatter_points（Δ 正负 /
  significance≥5 / 降序）
- build_tuning / build_tuning_scatters 编排：patch 各 _query_* helper 返回种子数据，
  断言四块组装正确 + 单块失败容错（对齐 test_workbench_diagnosis 的 patch 范式）
"""

from __future__ import annotations

import contextlib
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.services.loop_fitness import LoopFitnessLatest
from app.services.workbench_tuning import (
    SIGNIFICANT_DELTA,
    _batch_score_change,
    _priority_of,
    build_tuning,
    build_tuning_scatters,
    resolve_batch_status,
    shape_batches,
    shape_pending_queue,
    shape_scatter_points,
)

NOW = datetime(2026, 8, 25, 12, 0, 0)


# ---------------------------------------------------------------------------
# 合成行构造
# ---------------------------------------------------------------------------


def _batch_row(
    batch_id: int = 1,
    batch_no: str = "ZD-2026-0145",
    title: str = "催化反再振荡组整定",
    status: str = "PENDING",
    prereq_order_ids: list | None = None,
    block_reason: str | None = None,
    scatters_before: list | None = None,
    scatters_after: list | None = None,
) -> dict[str, Any]:
    return {
        "id": batch_id,
        "batch_no": batch_no,
        "title": title,
        "scope_type": "FACTORY",
        "scope_id": 100,
        "status": status,
        "prereq_order_ids": prereq_order_ids or [],
        "block_reason": block_reason,
        "scatters_before": scatters_before,
        "scatters_after": scatters_after,
        "expected_start_at": None,
        "actual_start_at": None,
        "completed_at": None,
        "created_at": NOW - timedelta(hours=6),
    }


def _pending_row(
    record_id: str = "r-1",
    loop_id: str = "loop-1",
    loop_name: str = "TIC-408",
    unit_name: str | None = "反再单元",
    algorithm: str | None = "IMC",
    created_by: str | None = None,
) -> dict[str, Any]:
    return {
        "record_id": record_id,
        "loop_id": loop_id,
        "loop_name": loop_name,
        "loop_desc": "反再温度",
        "unit_name": unit_name,
        "algorithm": algorithm,
        "status": "DRAFT",
        "created_by": created_by,
        "created_at": NOW - timedelta(hours=2),
        "fitting_score": None,
    }


# ===========================================================================
# resolve_batch_status（B-06 前置阻塞）
# ===========================================================================


class TestResolveBatchStatus:
    def test_前置未闭合阻塞(self):
        """prereq 任一 EXECUTING → BLOCKED + block_reason"前置工单 CL-2026-0819 未闭合"。"""
        status, reason = resolve_batch_status(
            "PENDING", [{"order_no": "CL-2026-0819", "status": "EXECUTING"}]
        )
        assert status == "BLOCKED"
        assert reason == "前置工单 CL-2026-0819 未闭合"

    def test_多前置未闭合计数(self):
        status, reason = resolve_batch_status(
            "READY",
            [
                {"order_no": "CL-2026-0819", "status": "PENDING"},
                {"order_no": "CL-2026-0820", "status": "VERIFYING"},
                {"order_no": "CL-2026-0801", "status": "CLOSED"},
            ],
        )
        assert status == "BLOCKED"
        assert reason == "前置工单 CL-2026-0819 等 2 项未闭合"

    def test_前置全闭合自动解除(self):
        """库存储 BLOCKED + 前置全 CLOSED/CANCELLED → READY。"""
        status, reason = resolve_batch_status(
            "BLOCKED",
            [
                {"order_no": "CL-2026-0819", "status": "CLOSED"},
                {"order_no": "CL-2026-0820", "status": "CANCELLED"},
            ],
        )
        assert status == "READY"
        assert reason is None

    def test_终态不被重算(self):
        """COMPLETED/CANCELLED 即使前置未闭合也保持终态（不回退阻塞）。"""
        for terminal in ("COMPLETED", "CANCELLED"):
            status, reason = resolve_batch_status(
                terminal, [{"order_no": "X", "status": "PENDING"}]
            )
            assert status == terminal
            assert reason is None

    def test_非阻塞状态透传(self):
        for s in ("PENDING", "READY", "RUNNING"):
            status, reason = resolve_batch_status(s, [])
            assert status == s
            assert reason is None

    def test_前置工单缺失按已闭合处理(self):
        """prereq 行被删（status=None）不阻塞。"""
        status, _ = resolve_batch_status("PENDING", [{"order_no": None, "status": None}])
        assert status == "PENDING"


# ===========================================================================
# shape_batches（F-TN-01）
# ===========================================================================


class TestShapeBatches:
    def test_阻塞批次动态判定与排序(self):
        """BLOCKED 排最前；block_reason 来自动态判定；前置 pills 携带闭合标记。"""
        rows = [
            _batch_row(batch_id=1, batch_no="ZD-2026-0142", status="COMPLETED"),
            _batch_row(
                batch_id=2,
                batch_no="ZD-2026-0145",
                status="PENDING",
                prereq_order_ids=["o-1"],
            ),
        ]
        prereq_map = {"o-1": {"order_no": "CL-2026-0819", "title": "换阀", "status": "EXECUTING"}}
        stats = {1: {"loop_count": 6, "algorithms": ["IMC"], "owner": "王工"}}
        out = shape_batches(rows, prereq_map, stats)
        assert [b["batch_no"] for b in out] == ["ZD-2026-0145", "ZD-2026-0142"]
        blocked = out[0]
        assert blocked["status"] == "BLOCKED"
        assert blocked["stored_status"] == "PENDING"
        assert blocked["block_reason"] == "前置工单 CL-2026-0819 未闭合"
        assert blocked["prereq_orders"][0]["closed"] is False
        done = out[1]
        assert done["loop_count"] == 6
        assert done["strategy"] == "IMC"
        assert done["owner"] == "王工"

    def test_评分变化由快照配对均值(self):
        """scatters 71→88（▲17）：before/after 按 loop_id 配对取均值。"""
        row = _batch_row(
            status="COMPLETED",
            scatters_before=[{"loop_id": "l1", "score": 69.0}, {"loop_id": "l2", "score": 73.0}],
            scatters_after=[{"loop_id": "l1", "score": 88.0}, {"loop_id": "l2", "score": 88.0}],
        )
        out = shape_batches([row], {}, {})
        assert out[0]["score_before"] == 71.0
        assert out[0]["score_after"] == 88.0
        assert out[0]["score_delta"] == 17.0

    def test_无快照评分变化为None(self):
        out = shape_batches([_batch_row(status="PENDING")], {}, {})
        assert out[0]["score_before"] is None
        assert out[0]["score_delta"] is None

    def test_前置工单缺失占位不阻塞(self):
        row = _batch_row(status="PENDING", prereq_order_ids=["o-gone"])
        out = shape_batches([row], {}, {})
        assert out[0]["status"] == "PENDING"
        assert out[0]["prereq_orders"][0]["closed"] is True

    def test_空数据容错(self):
        assert shape_batches([], {}, {}) == []


class TestBatchScoreChange:
    def test_负Δ回退(self):
        b, a, d = _batch_score_change(
            [{"loop_id": "l1", "score": 66.0}], [{"loop_id": "l1", "score": 62.0}]
        )
        assert (b, a, d) == (66.0, 62.0, -4.0)

    def test_loop不匹配跳过(self):
        b, a, d = _batch_score_change(
            [{"loop_id": "l1", "score": 66.0}], [{"loop_id": "l2", "score": 62.0}]
        )
        assert (b, a, d) == (None, None, None)

    def test_非列表容错(self):
        assert _batch_score_change(None, []) == (None, None, None)


# ===========================================================================
# shape_pending_queue（F-TN-02）
# ===========================================================================


class TestShapePendingQueue:
    def test_批次阻塞优先(self):
        rows = [_pending_row(record_id="r-1", loop_id="loop-1")]
        batch_map = {
            "r-1": {
                "batch_no": "ZD-2026-0145",
                "status": "BLOCKED",
                "block_reason": "前置工单 CL-2026-0819 未闭合",
            }
        }
        out = shape_pending_queue(rows, {}, batch_map, {"loop-1": 58.4}, {})
        assert out[0]["blocked"] is True
        assert out[0]["block_reason"] == "前置工单 CL-2026-0819 未闭合"
        assert out[0]["batch_no"] == "ZD-2026-0145"

    def test_同回路工单阻塞(self):
        """先硬件后整定：同回路未闭合非 TUNING 工单 → blocked + reason。"""
        rows = [_pending_row(record_id="r-1", loop_id="loop-1")]
        block_map = {"loop-1": {"order_no": "CL-2026-0819", "status": "EXECUTING"}}
        out = shape_pending_queue(rows, block_map, {}, {"loop-1": 58.4}, {})
        assert out[0]["blocked"] is True
        assert out[0]["block_reason"] == "前置工单 CL-2026-0819 未闭合"

    def test_可操作在前阻塞沉底(self):
        """非阻塞按评分升序（最差优先整定）；阻塞灰化排末尾。"""
        rows = [
            _pending_row(record_id="r-1", loop_id="loop-1"),
            _pending_row(record_id="r-2", loop_id="loop-2"),
            _pending_row(record_id="r-3", loop_id="loop-3"),
        ]
        block_map = {"loop-3": {"order_no": "CL-X", "status": "PENDING"}}
        score_map = {"loop-1": 71.3, "loop-2": 58.4, "loop-3": 61.2}
        out = shape_pending_queue(rows, block_map, {}, score_map, {})
        assert [r["loop_id"] for r in out] == ["loop-2", "loop-1", "loop-3"]
        assert out[2]["blocked"] is True

    def test_优先级阈值(self):
        """对齐原型：<65 高（58.4/61.2）· <73 中（66.5/71.3）· ≥73 低（74.8）。"""
        assert _priority_of(58.4) == "HIGH"
        assert _priority_of(64.9) == "HIGH"
        assert _priority_of(65.0) == "MEDIUM"
        assert _priority_of(71.3) == "MEDIUM"
        assert _priority_of(74.8) == "LOW"
        assert _priority_of(None) == "MEDIUM"

    def test_建议来源三态(self):
        rows = [
            _pending_row(record_id="r-1", loop_id="loop-1"),
            _pending_row(record_id="r-2", loop_id="loop-2", created_by="刘工"),
            _pending_row(record_id="r-3", loop_id="loop-3"),
        ]
        diag_src = {"loop-1": "回路振荡"}
        out = shape_pending_queue(rows, {}, {}, {}, diag_src)
        src = {r["loop_id"]: r["source"] for r in out}
        assert src["loop-1"] == "诊断：回路振荡"
        assert src["loop-2"] == "人工登记 · 刘工"
        assert src["loop-3"] == "人工登记"

    def test_空数据容错(self):
        assert shape_pending_queue([], {}, {}, {}, {}) == []


# ===========================================================================
# shape_scatter_points（F-TN-03，B-12）
# ===========================================================================


class TestShapeScatterPoints:
    def test_Δ正负与significance(self):
        """Δ=after-before；Δ≥5 significance=True（整定有效口径）。"""
        pairs = [(71.0, 88.0), (66.0, 62.0), (70.0, 73.0)]
        meta = [
            {"loop_id": "l1", "loop_name": "TIC-0511", "batch_no": "ZD-2026-0142"},
            {"loop_id": "l2", "loop_name": "FIC-109", "order_no": "CL-2026-0768"},
            {"loop_id": "l3", "loop_name": "PIC-0305", "order_no": "CL-2026-0831"},
        ]
        out = shape_scatter_points(pairs, meta)
        assert [p["delta"] for p in out] == [17.0, 3.0, -4.0]  # Δ 降序
        by_loop = {p["loop_id"]: p for p in out}
        assert by_loop["l1"]["significance"] is True
        assert by_loop["l1"]["batch_no"] == "ZD-2026-0142"
        assert by_loop["l2"]["significance"] is False
        assert by_loop["l2"]["delta"] == -4.0  # 负 Δ → 前端红
        assert by_loop["l3"]["significance"] is False

    def test_显著性边界(self):
        pairs = [(70.0, 70.0 + SIGNIFICANT_DELTA)]
        meta = [{"loop_id": "l1"}]
        assert shape_scatter_points(pairs, meta)[0]["significance"] is True

    def test_空数据容错(self):
        assert shape_scatter_points([], []) == []


# ===========================================================================
# build_tuning / build_tuning_scatters 编排（patch 范式，不依赖真实 PG）
# ===========================================================================


def _patchers(svc: Any) -> dict[str, Any]:
    """build_tuning 全部外部依赖的 patcher 集（默认种子数据）。"""
    return {
        "_get_scope_unit_ids": patch.object(
            svc, "_get_scope_unit_ids", new=AsyncMock(return_value=None)
        ),
        "_query_batch_rows": patch.object(
            svc,
            "_query_batch_rows",
            new=AsyncMock(
                return_value=[
                    _batch_row(batch_id=2, batch_no="ZD-2026-0145", prereq_order_ids=["o-1"])
                ]
            ),
        ),
        "_query_prereq_orders": patch.object(
            svc,
            "_query_prereq_orders",
            new=AsyncMock(
                return_value={
                    "o-1": {"order_no": "CL-2026-0819", "title": "换阀", "status": "EXECUTING"}
                }
            ),
        ),
        "_query_batch_record_stats": patch.object(
            svc,
            "_query_batch_record_stats",
            new=AsyncMock(
                return_value={2: {"loop_count": 4, "algorithms": ["IMC"], "owner": "张工"}}
            ),
        ),
        "_query_pending_records": patch.object(
            svc,
            "_query_pending_records",
            new=AsyncMock(return_value=[_pending_row(record_id="r-1", loop_id="loop-1")]),
        ),
        "_query_record_batch_map": patch.object(
            svc, "_query_record_batch_map", new=AsyncMock(return_value={})
        ),
        "_query_loop_open_orders": patch.object(
            svc,
            "_query_loop_open_orders",
            new=AsyncMock(
                return_value={"loop-1": {"order_no": "CL-2026-0819", "status": "EXECUTING"}}
            ),
        ),
        "_query_latest_scores": patch.object(
            svc, "_query_latest_scores", new=AsyncMock(return_value={"loop-1": 58.4})
        ),
        "_query_diag_src_map": patch.object(
            svc, "_query_diag_src_map", new=AsyncMock(return_value={"loop-1": "回路振荡"})
        ),
        "_query_scope_loop_ids": patch.object(
            svc, "_query_scope_loop_ids", new=AsyncMock(return_value=["loop-1"])
        ),
        "get_latest_fitness_per_loop": patch.object(
            svc,
            "get_latest_fitness_per_loop",
            new=AsyncMock(
                return_value={
                    "loop-1": LoopFitnessLatest(loop_id="loop-1", level="L2", tags=[], detail={})
                }
            ),
        ),
        "_query_loop_names": patch.object(
            svc, "_query_loop_names", new=AsyncMock(return_value={"loop-1": "TIC-0511"})
        ),
        "_query_scatter_orders": patch.object(
            svc, "_query_scatter_orders", new=AsyncMock(return_value=[])
        ),
    }


@contextlib.contextmanager
def patched_common(svc: Any, **overrides: Any):
    """进入全部 patcher；overrides 按名替换（如注入 side_effect）。"""
    patchers = _patchers(svc)
    for name, new in overrides.items():
        patchers[name] = patch.object(svc, name, new=new)
    with contextlib.ExitStack() as stack:
        for p in patchers.values():
            stack.enter_context(p)
        yield


class TestBuildTuning:
    @pytest.mark.asyncio
    async def test_组装四块与阻塞语义(self):
        import app.services.workbench_tuning as svc

        with patched_common(svc):
            out = await build_tuning(object(), scope_type="GLOBAL", window="24h")

        assert out["scope"] == {"type": "GLOBAL", "id": None}
        assert out["window"] == "24h"
        # batches：动态 BLOCKED
        assert len(out["batches"]) == 1
        assert out["batches"][0]["status"] == "BLOCKED"
        assert out["batches"][0]["block_reason"] == "前置工单 CL-2026-0819 未闭合"
        assert out["batches"][0]["strategy"] == "IMC"
        # pending_queue：同回路工单阻塞
        assert len(out["pending_queue"]) == 1
        assert out["pending_queue"][0]["blocked"] is True
        assert out["pending_queue"][0]["priority"] == "HIGH"  # 58.4 < 65
        assert out["pending_queue"][0]["source"] == "诊断：回路振荡"
        # fitness_gates
        assert out["fitness_gates"]["level"] == "L2"
        assert out["fitness_gates"]["level_counts"]["L2"] == 1
        # scatters（无快照 + 无工单 → 空）
        assert out["scatters"] == []

    @pytest.mark.asyncio
    async def test_单块失败不阻断其余块(self):
        import app.services.workbench_tuning as svc

        with patched_common(svc, _query_batch_rows=AsyncMock(side_effect=RuntimeError("db down"))):
            out = await build_tuning(object())

        assert out["batches"] == []  # 失败块回退空
        assert len(out["pending_queue"]) == 1  # 其余块正常
        assert out["fitness_gates"]["level"] == "L2"


class TestBuildTuningScatters:
    @pytest.mark.asyncio
    async def test_批次快照与工单双源(self):
        """批次固化快照点（batch_no）+ TUNING 工单点（order_no）；批次已覆盖回路去重。"""
        import app.services.workbench_tuning as svc

        batch_rows = [
            _batch_row(
                batch_id=1,
                batch_no="ZD-2026-0142",
                status="COMPLETED",
                scatters_before=[{"loop_id": "l1", "score": 69.0}],
                scatters_after=[{"loop_id": "l1", "score": 88.0}],
            )
        ]
        order_rows = [
            {
                "loop_id": "l1",
                "order_no": "CL-DUP",
                "kpi_before": {"score": 70.0},
                "kpi_after": {"score": 80.0},
                "loop_name": "TIC-0511",
            },
            {
                "loop_id": "l2",
                "order_no": "CL-2026-0831",
                "kpi_before": {"score": 67.0},
                "kpi_after": {"score": 76.0},
                "loop_name": "FIC-2101",
            },
        ]
        with (
            patch.object(svc, "_get_scope_unit_ids", new=AsyncMock(return_value=None)),
            patch.object(svc, "_query_loop_names", new=AsyncMock(return_value={"l1": "TIC-0511"})),
            patch.object(svc, "_query_scatter_orders", new=AsyncMock(return_value=order_rows)),
        ):
            out = await build_tuning_scatters(object(), _batch_rows=batch_rows, _unit_ids=None)

        assert len(out["points"]) == 2  # l1 工单点被批次快照去重
        assert out["points"][0]["loop_id"] == "l1"
        assert out["points"][0]["delta"] == 19.0
        assert out["points"][0]["batch_no"] == "ZD-2026-0142"
        assert out["points"][0]["significance"] is True
        assert out["points"][1]["order_no"] == "CL-2026-0831"
        assert out["batch_id"] is None

    @pytest.mark.asyncio
    async def test_batch_id过滤仅快照(self):
        """batch_id 指定时只返回该批次固化点，不查工单源。"""
        import app.services.workbench_tuning as svc

        class _Row:
            def __init__(self, m: dict):
                self._mapping = m

        class _Res:
            def all(self):
                return [
                    _Row(
                        {
                            "id": 1,
                            "batch_no": "ZD-2026-0142",
                            "scatters_before": [{"loop_id": "l1", "score": 69.0}],
                            "scatters_after": [{"loop_id": "l1", "score": 88.0}],
                        }
                    )
                ]

        db = AsyncMock()
        db.execute = AsyncMock(return_value=_Res())
        with patch.object(svc, "_query_loop_names", new=AsyncMock(return_value={"l1": "TIC-0511"})):
            out = await build_tuning_scatters(db, batch_id=1)
        assert out["batch_id"] == 1
        assert len(out["points"]) == 1
        assert out["points"][0]["batch_no"] == "ZD-2026-0142"

    @pytest.mark.asyncio
    async def test_空数据与异常容错(self):
        import app.services.workbench_tuning as svc

        with (
            patch.object(svc, "_get_scope_unit_ids", new=AsyncMock(side_effect=RuntimeError("x"))),
            patch.object(svc, "_query_loop_names", new=AsyncMock(return_value={})),
            patch.object(
                svc, "_query_scatter_orders", new=AsyncMock(side_effect=RuntimeError("db"))
            ),
        ):
            out = await build_tuning_scatters(object(), _batch_rows=[])
        assert out["points"] == []
        assert out["scope"]["type"] == "GLOBAL"
