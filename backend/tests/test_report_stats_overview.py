"""管理总览 get_overview KPI 骨架结构测试（Task #18，2026-08-23）。

验证固定 12 格收敛口径：
- S3 开通时 kpis 恰为 12 项，第 12 格 key=scoreImprovement（纯技术口径）；
- kpis 中不再包含 benchmarkGap（已移出 KPI 列表），也无任何"万元/预估收益"经济口径；
- S2 阶段保持 9 项，S1 阶段保持 5 项（骨架不因 S3 改动漂移）。

实现方式：打桩阶段判定/锁定/节点解析，db.execute 按 SQL 文本分发预制结果，
无需真实 PG（CI 默认跑单测即覆盖）。
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.services import report_stats as rs

START = datetime(2026, 7, 1)
END = datetime(2026, 8, 1)


class _FakeResult:
    def __init__(self, rows: list[Any] | None = None, one_row: Any = None) -> None:
        self._rows = rows or []
        self._one = one_row

    def one(self) -> Any:
        return self._one

    def all(self) -> list[Any]:
        return self._rows


def _make_db(good_rows: list[Any] | None = None) -> AsyncMock:
    """按 SQL 文本特征分发预制查询结果（与 get_overview 查询顺序无关）。"""

    async def _execute(stmt: Any, _params: Any = None) -> _FakeResult:
        sql = str(stmt)
        if "COUNT(*) AS total" in sql and "include_in_evaluation" in sql:
            return _FakeResult(one_row=SimpleNamespace(total=2, evaluable=2))
        if "AVG(k.good_value_rate)" in sql:
            rows = good_rows or [
                SimpleNamespace(loop_id="l1", avg_score=55.0, avg_good_value=98.0, avg_auto=90.0),
                SimpleNamespace(loop_id="l2", avg_score=70.0, avg_good_value=97.0, avg_auto=95.0),
            ]
            return _FakeResult(rows=rows)
        if "INTERVAL '90 days'" in sql:
            return _FakeResult(rows=[])
        if "date_trunc('day'" in sql:
            return _FakeResult(rows=[SimpleNamespace(d="2026-07-01", avg_score=62.0, loop_count=2)])
        if "primary_category, severity" in sql:
            return _FakeResult(rows=[])
        if "SELECT id, tag_name, unit_id FROM loop_ledger" in sql:
            return _FakeResult(
                rows=[
                    SimpleNamespace(id="l1", tag_name="TIC-101", unit_id=None),
                    SimpleNamespace(id="l2", tag_name="FIC-202", unit_id=None),
                ]
            )
        if "avg_cycle_h" in sql:
            return _FakeResult(
                one_row=SimpleNamespace(
                    total=6, closed_cnt=5, reopened_cnt=1, avg_cycle_h=12.5, closed_this_month=2
                )
            )
        if "date_trunc('month'" in sql:
            return _FakeResult(rows=[])
        if "INTERVAL '30 days'" in sql:
            return _FakeResult(rows=[])
        if "INTERVAL '60 days'" in sql:
            return _FakeResult(rows=[])
        if "DISTINCT ON (loop_id) loop_id, status" in sql:
            return _FakeResult(rows=[])
        if "AVG((ho.kpi_after" in sql:
            return _FakeResult(one_row=SimpleNamespace(score_delta=8.2, auto_delta=5.0, n=5))
        if "ORDER BY updated_at DESC" in sql:
            # TOP 闭环回路处置前后 score_delta（纯技术口径）
            return _FakeResult(
                rows=[
                    SimpleNamespace(loop_id="l1", score_delta=10.0),
                    SimpleNamespace(loop_id="l2", score_delta=5.0),
                ]
            )
        raise AssertionError(f"未预期的 SQL: {sql[:120]}")

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=_execute)
    return db


def _patch_stage(monkeypatch: pytest.MonkeyPatch, stage: str, s3_available: bool) -> None:
    monkeypatch.setattr(
        rs, "get_stage_lock", AsyncMock(return_value={"locked": False, "lockedStage": None})
    )
    monkeypatch.setattr(
        rs,
        "determine_maturity_stage",
        AsyncMock(
            return_value={
                "detectedStage": stage,
                "availability": {
                    "s1Available": True,
                    "s2Available": True,
                    "s3Available": s3_available,
                },
                "counts": {
                    "diagnosisRuns": 10,
                    "handlingOrders": 8,
                    "tuningRecords": 3,
                    "closedVerifiedOrders": 6,
                },
            }
        ),
    )
    monkeypatch.setattr(rs, "_resolve_subtree_unit_ids", AsyncMock(return_value=None))
    monkeypatch.setattr(rs, "_load_unit_paths", AsyncMock(return_value={}))


class TestOverviewFixed12Slots:
    async def test_s3_kpis_exactly_12_and_slot12_is_score_improvement(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_stage(monkeypatch, "S3", s3_available=True)
        db = _make_db()

        data = await rs.get_overview(
            db, stage="S3", start_date=START, end_date=END, plant_node_id=None
        )

        kpis = data["kpis"]
        assert data["stage"] == "S3"
        # 固定 12 格：S1(5) + S2(4) + S3(3)
        assert len(kpis) == 12
        keys = [k["key"] for k in kpis]
        assert keys == [
            "totalLoops",
            "healthRate",
            "evaluationRate",
            "anomalyCount",
            "dataHealthRate",
            "closedLoopRate",
            "avgCycleHours",
            "closedThisMonth",
            "ineffectiveRate",
            "kpiImprovement",
            "autoRateImprovement",
            "scoreImprovement",
        ]
        # 第 12 格 = scoreImprovement（TOP 闭环回路处置前后 score_delta 均值：(10.0+5.0)/2）
        slot12 = kpis[11]
        assert slot12["key"] == "scoreImprovement"
        assert slot12["value"] == 7.5
        assert slot12["unit"] == "分"
        assert slot12["status"] == "ok"

        # benchmarkGap 已移出 KPI 列表，且无任何经济口径字样
        assert "benchmarkGap" not in keys
        blob = str(kpis)
        assert "万元" not in blob
        assert "预估收益" not in blob

        # TOP 回路行的评分改善列仍为纯技术口径 score_delta
        assert data["topProblemLoops"][0]["benefitEstimate"] in (5.0, 10.0)

    async def test_s2_kpis_stay_9_without_s3_keys(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_stage(monkeypatch, "S2", s3_available=False)
        db = _make_db()

        data = await rs.get_overview(
            db, stage="S2", start_date=START, end_date=END, plant_node_id=None
        )

        kpis = data["kpis"]
        assert data["stage"] == "S2"
        assert len(kpis) == 9
        keys = [k["key"] for k in kpis]
        assert keys[-1] == "ineffectiveRate"
        for k in ("kpiImprovement", "autoRateImprovement", "scoreImprovement", "benchmarkGap"):
            assert k not in keys
        assert data["benefitTrend"] is None

    async def test_s1_kpis_stay_5(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_stage(monkeypatch, "S1", s3_available=False)
        db = _make_db()

        # S1 下 S2 查询不应触发，把 S2 专用 SQL 全部置为报错以守护边界
        data = await rs.get_overview(
            db, stage="S1", start_date=START, end_date=END, plant_node_id=None
        )

        assert data["stage"] == "S1"
        assert len(data["kpis"]) == 5
        assert [k["key"] for k in data["kpis"]][:5] == [
            "totalLoops",
            "healthRate",
            "evaluationRate",
            "anomalyCount",
            "dataHealthRate",
        ]


class TestDataHealthRateDimension:
    """数据健康率量纲防回归（Task #21，9,942.6% 重复放大事故）。"""

    async def test_percentage_input_not_amplified_again(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """kpi_snapshot_hourly.good_value_rate 为 0~100 百分比：不得再乘 100。"""
        _patch_stage(monkeypatch, "S1", s3_available=False)
        db = _make_db()  # 预制 98.0 / 97.0（百分比口径）

        data = await rs.get_overview(
            db, stage="S1", start_date=START, end_date=END, plant_node_id=None
        )

        kpi = next(k for k in data["kpis"] if k["key"] == "dataHealthRate")
        assert kpi["value"] == 97.5
        assert kpi["unit"] == "%"
        assert kpi["status"] == "ok"
        assert 0 <= kpi["value"] <= 100

    async def test_ratio_input_normalized_to_percent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """兼容 0~1 比率量纲：归一化为百分比而非直接输出小数。"""
        _patch_stage(monkeypatch, "S1", s3_available=False)
        rows = [
            SimpleNamespace(loop_id="l1", avg_score=55.0, avg_good_value=0.76, avg_auto=0.90),
            SimpleNamespace(loop_id="l2", avg_score=70.0, avg_good_value=0.78, avg_auto=0.95),
        ]
        db = _make_db(good_rows=rows)

        data = await rs.get_overview(
            db, stage="S1", start_date=START, end_date=END, plant_node_id=None
        )

        kpi = next(k for k in data["kpis"] if k["key"] == "dataHealthRate")
        assert kpi["value"] == 77.0
        assert 0 <= kpi["value"] <= 100

    async def test_empty_good_values_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """参评回路无好值率数据 → None，status 降级 info。"""
        _patch_stage(monkeypatch, "S1", s3_available=False)
        rows = [
            SimpleNamespace(loop_id="l1", avg_score=55.0, avg_good_value=None, avg_auto=90.0),
            SimpleNamespace(loop_id="l2", avg_score=70.0, avg_good_value=None, avg_auto=95.0),
        ]
        db = _make_db(good_rows=rows)

        data = await rs.get_overview(
            db, stage="S1", start_date=START, end_date=END, plant_node_id=None
        )

        kpi = next(k for k in data["kpis"] if k["key"] == "dataHealthRate")
        assert kpi["value"] is None
        assert kpi["status"] == "info"
