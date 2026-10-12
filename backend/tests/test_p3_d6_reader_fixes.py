"""P3-08d/D6 读端误导路径修复回归（R11/R12，11§5）。

R11：confidence D/E 单独不生成"数据质量异常"——只有真实数据质量证据
（快照适用性 L0/L1 或 DATA_INSUFFICIENT 标签）才产出 DATA_QUALITY 来源；
R12：AI 洞察 performance 场景——真实 0 分不丢（score=0 不是"未知"）、
score=null 不落"性能良好"、参考 F 不指导 PID 增减。
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.monitor_attention import _aggregate_degradation_and_data_quality


def _result(rows):
    m = MagicMock()
    m.all.return_value = rows
    return m


def _make_db(
    *,
    snap_rows: list | None = None,
    confidence_rows: list | None = None,
) -> AsyncMock:
    """按语句类型分发的 db mock（loop/snap/prev/confidence 四查询）。"""
    loop = SimpleNamespace(id="loop-1", tag_name="FIC101", is_active=True, unit_id="u1")
    loop_rows = [(loop, "单元A", "装置B")]
    db = AsyncMock()

    async def _execute(stmt, *a, **kw):
        sql = str(stmt)
        if "kpi_snapshot_hourly" in sql:
            if "ts_end <" in sql:  # prev snap 查询
                return _result([])
            return _result(snap_rows or [])
        if "loop_confidence_latest" in sql:
            return _result(confidence_rows or [])
        # loop_ledger join plant_node（回路信息）
        return _result(loop_rows)

    db.execute = AsyncMock(side_effect=_execute)
    return db


def _snap_row(
    *,
    status: str = "SUCCESS",
    score=None,
    fitness_level: str | None = None,
    fitness_tags: dict | None = None,
):
    return SimpleNamespace(
        id="snap-1",
        loop_id="loop-1",
        score=score,
        ts_end=datetime(2026, 10, 12, 9, 0, 0),
        confidence_level="D",
        fitness_level=fitness_level,
        fitness_tags=fitness_tags,
    )


def _conf_row(level: str = "D", score=None):
    return SimpleNamespace(
        loop_id="loop-1",
        confidence_level=level,
        eval_time=datetime(2026, 10, 12, 9, 0, 0),
        score=score,
    )


class TestR11ConfidenceAloneNotDataQuality:
    @pytest.mark.asyncio
    async def test_confidence_de_alone_no_data_quality_item(self):
        """R11 反例：可信度 D/E 但无真实数据质量证据 → 不产 DATA_QUALITY。"""
        db = _make_db(
            snap_rows=[_snap_row(status="SUCCESS", score=80.0, fitness_level="L4")],
            confidence_rows=[_conf_row("D")],
        )
        items, _ = await _aggregate_degradation_and_data_quality(db, None)
        assert not [i for i in items if i.source == "DATA_QUALITY"]

    @pytest.mark.asyncio
    async def test_fitness_l0_produces_data_quality_item(self):
        """真实证据：适用性 L0 → DATA_QUALITY 产出，confidence D 作补充理由。"""
        db = _make_db(
            snap_rows=[
                _snap_row(
                    status="INCONCLUSIVE",
                    score=None,
                    fitness_level="L0",
                    fitness_tags={"tags": ["DATA_INSUFFICIENT"]},
                )
            ],
            confidence_rows=[_conf_row("E")],
        )
        items, _ = await _aggregate_degradation_and_data_quality(db, None)
        dq = [i for i in items if i.source == "DATA_QUALITY"]
        assert len(dq) == 1
        assert "L0" in dq[0].summary
        assert "DATA_INSUFFICIENT" in dq[0].summary
        assert "E" in dq[0].summary  # 补充理由并列展示


# ---------------------------------------------------------------------------
# R12：AI 洞察 performance 场景误导路径
# ---------------------------------------------------------------------------

from unittest.mock import patch  # noqa: E402

from app.services.ai_insight.context import AiInsightContext  # noqa: E402
from app.services.ai_insight.scenes.performance import (  # noqa: E402
    PerformanceScene,
    _grade_from_score,
)


def _perf_ctx(
    *,
    score: float | None,
    grade: str | None = None,
    metrics: dict | None = None,
    fast_evidence: dict | None = None,
) -> AiInsightContext:
    return AiInsightContext(
        scene="performance",
        loopId="loop-1",
        tagName="FIC101",
        data={
            "metrics": metrics
            or {
                "good_value_rate": 0.98,
                "auto_mode_rate": 0.95,
                "effective_auto_rate": 0.92,
                "steady_rate": 0.91,
                "accuracy_rate": 0.9,
                "fast_rate": None,
                "oscillation_rate": 0.05,
                "saturation_rate": 0.02,
                "instrument_fault_rate": 0.01,
            },
            "score": score,
            "grade": grade if grade is not None else _grade_from_score(score),
            "status": "SUCCESS",
            "confidenceLevel": "B",
            "tsStart": "2026-10-12T08:00:00",
            "total": 1,
            "fastRateEvidence": fast_evidence or {"kind": "unknown"},
        },
    )


class TestR12PerformanceScene:
    def test_real_zero_score_is_not_unknown_grade(self):
        """真实 0 分不被 falsy 丢成"未知"——等级=不合格（原 bug：0→未知）。"""
        assert _grade_from_score(0.0) == "不合格"

    @pytest.mark.asyncio
    async def test_load_context_zero_score_keeps_zero(self):
        """load_context：score=0 → ctx.data.score=0.0（非 None）。"""
        from datetime import datetime as _dt
        from decimal import Decimal as _Decimal

        snap = MagicMock()
        snap.score = _Decimal("0")
        snap.status = "SUCCESS"
        snap.confidence_level = "C"
        snap.ts_start = _dt(2026, 10, 12, 8, 0, 0)
        snap.result_record_id = None
        db = AsyncMock()
        with patch(
            "app.services.ai_insight.scenes.performance.list_loop_snapshots",
            new=AsyncMock(return_value=([(snap, "FIC101")], 1)),
        ):
            ctx = await PerformanceScene().load_context(db, loop_id="loop-1")
        assert ctx.data["score"] == 0.0
        assert ctx.data["grade"] == "不合格"

    def test_null_score_not_reported_good(self):
        """score=None：优先级不落"性能良好"，显式"不能判定性能良好"。"""
        text = PerformanceScene().generate_template(_perf_ctx(score=None, grade="未知"))
        assert "性能良好，纳入常规监测" not in text
        assert "不能判定性能良好" in text
        assert "暂无有效综合评分" in text

    def test_reference_fast_rate_never_guides_pid(self):
        """F 仅有静稳参考分：不给 PID 增减建议，明示动态证据不足。"""
        text = PerformanceScene().generate_template(
            _perf_ctx(
                score=55.0,
                fast_evidence={"kind": "reference", "referenceScore": 100.0},
            )
        )
        assert "静稳参考分" in text
        assert "动态证据不足" in text
        assert "不建议据此调整 PID" in text
        assert "增大比例增益" not in text  # 不由参考分指导 PID 增减

    def test_formal_low_fast_rate_keeps_pid_advice(self):
        """正式 F 偏低仍可给整定建议（区分参考与正式值，不一刀切）。"""
        text = PerformanceScene().generate_template(
            _perf_ctx(
                score=55.0,
                metrics={
                    "good_value_rate": 0.98,
                    "auto_mode_rate": 0.95,
                    "effective_auto_rate": 0.92,
                    "steady_rate": 0.91,
                    "accuracy_rate": 0.9,
                    "fast_rate": 0.5,
                    "oscillation_rate": 0.05,
                    "saturation_rate": 0.02,
                    "instrument_fault_rate": 0.01,
                },
                fast_evidence={"kind": "formal", "value": 0.5},
            )
        )
        assert "增大比例增益" in text


class TestR12WorkbenchScene:
    def test_null_score_not_reported_bad(self):
        """工作台场景：score=None 不渲染"整体运行状态不理想"。"""
        from app.services.ai_insight.scenes.workbench import WorkbenchScene

        board = {
            "kpiSummary": {"composite_score": None},
            "kpiCards": [],
            "partialWarning": {},
            "steadyRateTrend": {},
            "filterScope": {"plantNodeName": "全厂"},
        }
        ctx = AiInsightContext(
            scene="workbench",
            loopId=None,
            tagName=None,
            data={"board": board},
        )
        text = WorkbenchScene().generate_template(ctx)
        assert "整体运行状态不理想" not in text
        assert "暂无有效综合评分" in text
