"""A-03 工作台诊断聚合 service 单测（M2 批次 G-诊断）.

覆盖：
- 纯 shaper：shape_open_tags（Top6 筛选/排序/fitness 分级/SLA 倒计时/置信度归一）、
  shape_concl_timeline（时间倒序/四态透传/only_active 过滤/空分类容错）、
  shape_fitness_gates（L0 横幅触发/L4 徽章/进度条计算/空数据）、
  shape_rule_stats（聚合统计）
- build_diagnosis 编排：patch 各 _query_* helper 返回种子数据，断言六块组装正确
  （对齐 test_workbench_assessment 的 patch 范式，不依赖真实 PG）
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.services.loop_fitness import LoopFitnessLatest
from app.services.workbench_diagnosis import (
    AVG_LATENCY_TARGET_SEC,
    GATE_DESCS,
    OPEN_TAGS_TOP_N,
    build_diagnosis,
    shape_concl_timeline,
    shape_fitness_gates,
    shape_open_tags,
    shape_rootcause_top,
    shape_rule_stats,
    shape_summary_band,
)

NOW = datetime(2026, 8, 25, 12, 0, 0)


# ---------------------------------------------------------------------------
# 合成行构造
# ---------------------------------------------------------------------------


def _tag_row(
    tag_id: str = "t-1",
    loop_id: str = "loop-1",
    loop_name: str = "TIC-408",
    tag_code: str = "OSC",
    tag_name: str = "回路振荡",
    severity: str = "CRITICAL",
    triggered_at: datetime | None = None,
    sla_deadline_at: datetime | None = None,
    sla_stage: str = "WARN",
    category: str | None = "INSTRUMENT",
    conclusion: str | None = "检测到主导振荡周期 ≈ 38s",
    confidence: Any = 0.91,
) -> dict[str, Any]:
    return {
        "tag_id": tag_id,
        "loop_id": loop_id,
        "loop_name": loop_name,
        "tag_code": tag_code,
        "tag_name": tag_name,
        "severity": severity,
        "triggered_at": triggered_at or NOW - timedelta(hours=1),
        "sla_deadline_at": sla_deadline_at,
        "sla_stage": sla_stage,
        "category": category,
        "conclusion": conclusion,
        "confidence": confidence,
    }


def _concl_row(
    result_id: str = "r-1",
    loop_id: str = "loop-1",
    loop_name: str = "TIC-408",
    diag_label: str = "OSC",
    disposition: str | None = "UNADDRESSED",
    ts: datetime | None = None,
    category: str | None = "INSTRUMENT",
    evidence_summary: str | None = "振荡周期 ≈ 38s，Stiction 0.62",
    confidence: Any = 0.91,
    severity: str | None = "ERROR",
    tag_id: str | None = "t-1",
) -> dict[str, Any]:
    return {
        "result_id": result_id,
        "loop_id": loop_id,
        "loop_name": loop_name,
        "diag_label": diag_label,
        "disposition": disposition,
        "ts": ts or NOW - timedelta(hours=1),
        "category": category,
        "evidence_summary": evidence_summary,
        "confidence": confidence,
        "severity": severity,
        "tag_id": tag_id,
    }


# ===========================================================================
# shape_open_tags（F-DG-01）
# ===========================================================================


class TestShapeOpenTags:
    def test_top6筛选与严重度排序(self):
        """8 条候选 → Top6；CRITICAL 优先于 WARN/INFO。"""
        rows = [
            _tag_row(tag_id=f"t-{i}", severity=sev)
            for i, sev in enumerate(
                ["WARN", "CRITICAL", "INFO", "ERROR", "WARN", "CRITICAL", "ERROR", "WARN"]
            )
        ]
        out = shape_open_tags(rows, {}, {}, NOW)
        assert len(out) == OPEN_TAGS_TOP_N
        # 前 2 条为 CRITICAL，第 3~4 条 ERROR，末 2 条 WARN；INFO 被裁掉
        assert [r["severity"] for r in out[:2]] == ["CRITICAL", "CRITICAL"]
        assert [r["severity"] for r in out[2:4]] == ["ERROR", "ERROR"]
        assert [r["severity"] for r in out[4:]] == ["WARN", "WARN"]
        assert "INFO" not in [r["severity"] for r in out]

    def test_sla到期优先与倒计时计算(self):
        """同严重度下 SLA 最近到期者先；sla_due_sec = 截止 − 当前；负值 = 已超期。"""
        rows = [
            _tag_row(tag_id="t-far", sla_deadline_at=NOW + timedelta(hours=8)),
            _tag_row(tag_id="t-none", sla_deadline_at=None),
            _tag_row(tag_id="t-near", sla_deadline_at=NOW + timedelta(hours=2)),
            _tag_row(tag_id="t-over", sla_deadline_at=NOW - timedelta(hours=26)),
        ]
        out = shape_open_tags(rows, {}, {}, NOW)
        assert [r["tag_id"] for r in out] == ["t-over", "t-near", "t-far", "t-none"]
        assert out[0]["sla_due_sec"] == -(26 * 3600)  # 已超期 26h
        assert out[1]["sla_due_sec"] == 2 * 3600
        assert out[3]["sla_due_sec"] is None

    def test_fitness分级与spark注入(self):
        rows = [_tag_row(loop_id="loop-1"), _tag_row(tag_id="t-2", loop_id="loop-2")]
        spark_map = {"loop-1": [66.6, 65.2, 63.4]}
        fitness_map = {"loop-1": "L2", "loop-2": "L0"}
        out = shape_open_tags(rows, spark_map, fitness_map, NOW)
        by_loop = {r["loop_id"]: r for r in out}
        assert by_loop["loop-1"]["spark"] == [66.6, 65.2, 63.4]
        assert by_loop["loop-1"]["fitness_level"] == "L2"
        assert by_loop["loop-2"]["fitness_level"] == "L0"
        assert by_loop["loop-2"]["spark"] == []

    def test_confidence归一化(self):
        """0~100 口径（85）→ 0.85；0~1 口径透传；None 透传。"""
        rows = [
            _tag_row(tag_id="t-a", confidence=85),
            _tag_row(tag_id="t-b", confidence=0.62),
            _tag_row(tag_id="t-c", confidence=None),
        ]
        out = shape_open_tags(rows, {}, {}, NOW)
        by_id = {r["tag_id"]: r for r in out}
        assert by_id["t-a"]["confidence"] == 0.85
        assert by_id["t-b"]["confidence"] == 0.62
        assert by_id["t-c"]["confidence"] is None

    def test_空数据容错(self):
        assert shape_open_tags([], {}, {}, NOW) == []


# ===========================================================================
# shape_concl_timeline（F-DG-02）
# ===========================================================================


class TestShapeConclTimeline:
    def test_时间倒序与四态透传(self):
        rows = [
            _concl_row(result_id="r-old", ts=NOW - timedelta(hours=5), disposition="CONVERTED"),
            _concl_row(result_id="r-new", ts=NOW - timedelta(hours=1), disposition="IGNORED"),
            _concl_row(result_id="r-mid", ts=NOW - timedelta(hours=3), disposition="ACK_REVIEWED"),
        ]
        out = shape_concl_timeline(rows)
        assert [r["result_id"] for r in out] == ["r-new", "r-mid", "r-old"]
        dispositions = {r["result_id"]: r["disposition"] for r in out}
        assert dispositions == {"r-new": "IGNORED", "r-mid": "ACK_REVIEWED", "r-old": "CONVERTED"}

    def test_only_active过滤未处置(self):
        rows = [
            _concl_row(result_id="r-un", disposition="UNADDRESSED"),
            _concl_row(result_id="r-cv", disposition="CONVERTED"),
            _concl_row(result_id="r-ig", disposition="IGNORED"),
        ]
        out = shape_concl_timeline(rows, only_active=True)
        assert [r["result_id"] for r in out] == ["r-un"]
        # 默认不过滤
        assert len(shape_concl_timeline(rows)) == 3

    def test_空分类回退diag_label(self):
        rows = [_concl_row(category=None, diag_label="OSC")]
        out = shape_concl_timeline(rows)
        assert out[0]["category"] == "OSC"

    def test_无标签关联时disposition为None(self):
        rows = [_concl_row(tag_id=None, disposition=None)]
        out = shape_concl_timeline(rows)
        assert out[0]["disposition"] is None
        assert out[0]["id"] == "r-1"  # 无 tag_id 时回退 result_id 作主键

    def test_空数据容错(self):
        assert shape_concl_timeline([]) == []
        assert shape_concl_timeline([], only_active=True) == []


# ===========================================================================
# shape_fitness_gates（F-DG-03）
# ===========================================================================


class TestShapeFitnessGates:
    def test_L0横幅触发(self):
        """存在 L0 回路 → level=L0、gates[0]=False（红横幅「诊断数据不足」）。"""
        out = shape_fitness_gates({"L0": 2, "L4": 8}, evaluated=10, total=12)
        assert out["level"] == "L0"
        assert out["gates_passed"][0] is False
        assert out["gates_passed"][1:] == [True, True, True]
        assert out["level_counts"] == {"L0": 2, "L1": 0, "L2": 0, "L3": 0, "L4": 8}
        assert out["gate_desc"] == list(GATE_DESCS)

    def test_L1黄徽章门禁(self):
        out = shape_fitness_gates({"L1": 3, "L4": 7}, evaluated=10, total=10)
        assert out["level"] == "L1"
        assert out["gates_passed"] == [True, False, True, True]

    def test_L4徽章全过(self):
        out = shape_fitness_gates({"L4": 10}, evaluated=10, total=10)
        assert out["level"] == "L4"
        assert out["score"] == 100.0
        assert out["gates_passed"] == [True, True, True, True]

    def test_进度条加权计算(self):
        """score = Σ(层级权重 × 数量) / 参评数（L2=50/L3=75 → 62.5）。"""
        out = shape_fitness_gates({"L2": 1, "L3": 1}, evaluated=2, total=4)
        assert out["level"] == "L2"
        assert out["score"] == 62.5
        assert out["evaluated"] == 2
        assert out["total"] == 4

    def test_空数据容错(self):
        out = shape_fitness_gates({}, evaluated=0, total=0)
        assert out["level"] is None
        assert out["score"] is None
        assert out["gates_passed"] == [True, True, True, True]


# ===========================================================================
# shape_rule_stats + shape_rootcause_top
# ===========================================================================


class TestShapeRuleStats:
    def test_聚合统计与解决率(self):
        rows = [
            {"tag_code": "OSC", "tag_name": "回路振荡", "hits": 4, "resolved": 1},
            {"tag_code": "SAT", "tag_name": "阀位饱和", "hits": 3, "resolved": 3},
        ]
        out = shape_rule_stats(rows)
        assert out[0] == {
            "rule_id": "OSC",
            "name": "回路振荡",
            "hits": 4,
            "resolved_rate": 0.25,
        }
        assert out[1]["resolved_rate"] == 1.0

    def test_零命中解决率为None(self):
        assert (
            shape_rule_stats([{"tag_code": "X", "tag_name": None, "hits": 0, "resolved": 0}])[0][
                "resolved_rate"
            ]
            is None
        )

    def test_空数据容错(self):
        assert shape_rule_stats([]) == []


class TestShapeRootcauseTop:
    def test_tag_type别名对齐方案(self):
        rows = [{"tag_code": "OSC", "tag_name": "回路振荡", "count": 5, "active_count": 3}]
        out = shape_rootcause_top(rows)
        assert out[0]["tag_type"] == "OSC"
        assert out[0]["tag_code"] == "OSC"
        assert out[0]["count"] == 5


# ===========================================================================
# shape_summary_band（Row1 摘要带 · 5 项派生）
# ===========================================================================


class TestShapeSummaryBand:
    def test_基础派生(self):
        """17 条结论，12 条≥0.8 → 均值；劣化回路 6；时延 42s ≤ 60 达标；引擎元信息回退。"""
        concl_items = [{"confidence": 0.91} for _ in range(12)] + [
            {"confidence": 0.65} for _ in range(5)
        ]
        out = shape_summary_band(open_tags_len=6, concl_items=concl_items)
        assert out["diag_count"] == 17
        assert out["worsening_loops"] == 6
        assert out["avg_latency_sec"] == 42
        assert out["avg_latency_target"] == AVG_LATENCY_TARGET_SEC
        assert out["avg_latency_ok"] is True
        # 均值 = (12*0.91 + 5*0.65)/17 = (10.92+3.25)/17 = 14.17/17 ≈ 0.83
        assert out["avg_confidence"] == round((12 * 0.91 + 5 * 0.65) / 17, 2)
        assert out["high_confidence_count"] == 12
        assert out["total_confidence_count"] == 17
        # 引擎元信息默认回退
        assert out["engine_version"] == "v3.2.1"
        assert out["engine_running_days"] == 126
        assert out["engine_status"] == "ONLINE"

    def test_置信度归一与空容错(self):
        """85 → 0.85（归一）；0~1 口径透传；None 跳过。"""
        concl_items = [
            {"confidence": 85},
            {"confidence": 0.62},
            {"confidence": None},
        ]
        out = shape_summary_band(open_tags_len=0, concl_items=concl_items)
        assert out["total_confidence_count"] == 2  # None 不计数
        assert out["high_confidence_count"] == 1  # 0.85 高
        assert out["avg_confidence"] == round((0.85 + 0.62) / 2, 2)

    def test_时延超限(self):
        out = shape_summary_band(
            open_tags_len=0,
            concl_items=[],
            avg_latency_sec=72,
        )
        assert out["avg_latency_ok"] is False
        assert out["avg_latency_sec"] == 72

    def test_delta字段透传(self):
        out = shape_summary_band(
            open_tags_len=6,
            concl_items=[{"confidence": 0.9}],
            diag_count_delta=-3,
            worsening_delta=-2,
        )
        assert out["diag_count_delta"] == -3
        assert out["worsening_delta"] == -2

    def test_空数据(self):
        out = shape_summary_band(open_tags_len=0, concl_items=[])
        assert out["diag_count"] == 0
        assert out["worsening_loops"] == 0
        assert out["avg_confidence"] is None
        assert out["high_confidence_count"] == 0
        assert out["engine_rulebase_updated_at"] == "2026-08-18"


# ===========================================================================
# build_diagnosis 编排（patch 范式，不依赖真实 PG）
# ===========================================================================


class TestBuildDiagnosis:
    @pytest.mark.asyncio
    async def test_组装六块与scope字段(self):
        tag_rows = [_tag_row(loop_id="loop-1")]
        concl_rows = [_concl_row(loop_id="loop-1")]
        fitness = {"loop-1": LoopFitnessLatest(loop_id="loop-1", level="L2", tags=[], detail={})}
        with (
            patch(
                "app.services.workbench_diagnosis._get_scope_unit_ids",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.services.workbench_diagnosis._query_open_tag_rows",
                new=AsyncMock(return_value=tag_rows),
            ),
            patch(
                "app.services.workbench_diagnosis._query_spark_map",
                new=AsyncMock(return_value={"loop-1": [70.0, 68.0]}),
            ),
            patch(
                "app.services.workbench_diagnosis.get_latest_fitness_per_loop",
                new=AsyncMock(return_value=fitness),
            ),
            patch(
                "app.services.workbench_diagnosis._query_concl_rows",
                new=AsyncMock(return_value=concl_rows),
            ),
            patch(
                "app.services.workbench_diagnosis._query_scope_loop_ids",
                new=AsyncMock(return_value=["loop-1"]),
            ),
            patch(
                "app.services.workbench_diagnosis._query_rule_stat_rows",
                new=AsyncMock(
                    return_value=[
                        {"tag_code": "OSC", "tag_name": "回路振荡", "hits": 2, "resolved": 0}
                    ]
                ),
            ),
            patch(
                "app.services.workbench_diagnosis._query_pareto",
                new=AsyncMock(return_value=[{"root_cause": "仪表故障", "tag_count": 5}]),
            ),
            patch(
                "app.services.workbench_diagnosis._query_roots",
                new=AsyncMock(
                    return_value=[{"tag_code": "OSC", "tag_name": "回路振荡", "count": 5}]
                ),
            ),
        ):
            out = await build_diagnosis(object(), scope_type="GLOBAL", window="24h")

        assert out["scope"] == {"type": "GLOBAL", "id": None}
        assert out["window"] == "24h"
        # summary_band
        assert "summary_band" in out
        assert out["summary_band"]["worsening_loops"] == 1  # open_tags=1
        assert out["summary_band"]["diag_count"] == 1  # concl=1 (置信度0.91→归一)
        assert out["summary_band"]["high_confidence_count"] == 1
        assert out["summary_band"]["avg_latency_ok"] is True
        assert out["summary_band"]["engine_version"] == "v3.2.1"
        # open_tags
        assert len(out["open_tags"]) == 1
        assert out["open_tags"][0]["spark"] == [70.0, 68.0]
        assert out["open_tags"][0]["fitness_level"] == "L2"
        # concl_timeline
        assert len(out["concl_timeline"]) == 1
        assert out["concl_timeline"][0]["disposition"] == "UNADDRESSED"
        # fitness_gates（分母 loop-1，L2 一条）
        assert out["fitness_gates"]["level"] == "L2"
        assert out["fitness_gates"]["level_counts"]["L2"] == 1
        assert out["fitness_gates"]["total"] == 1
        # rule_stats / pareto / rootcause_top
        assert out["rule_stats"][0]["rule_id"] == "OSC"
        assert out["pareto"][0]["root_cause"] == "仪表故障"
        assert out["rootcause_top"][0]["tag_type"] == "OSC"

    @pytest.mark.asyncio
    async def test_单块失败不阻断其余块(self):
        fitness = {"loop-1": LoopFitnessLatest(loop_id="loop-1", level="L4", tags=[], detail={})}
        with (
            patch(
                "app.services.workbench_diagnosis._get_scope_unit_ids",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.services.workbench_diagnosis._query_open_tag_rows",
                new=AsyncMock(side_effect=RuntimeError("db down")),
            ),
            patch(
                "app.services.workbench_diagnosis._query_concl_rows",
                new=AsyncMock(return_value=[_concl_row()]),
            ),
            patch(
                "app.services.workbench_diagnosis._query_scope_loop_ids",
                new=AsyncMock(return_value=["loop-1"]),
            ),
            patch(
                "app.services.workbench_diagnosis.get_latest_fitness_per_loop",
                new=AsyncMock(return_value=fitness),
            ),
            patch(
                "app.services.workbench_diagnosis._query_rule_stat_rows",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "app.services.workbench_diagnosis._query_pareto",
                new=AsyncMock(side_effect=RuntimeError("mv missing")),
            ),
            patch(
                "app.services.workbench_diagnosis._query_roots",
                new=AsyncMock(return_value=[]),
            ),
        ):
            out = await build_diagnosis(object())

        assert out["open_tags"] == []  # 失败块回退空
        assert out["pareto"] == []
        assert len(out["concl_timeline"]) == 1  # 其余块正常
        assert out["fitness_gates"]["level"] == "L4"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("window,expected_hours", [("24h", 24), ("7d", 168), ("30d", 720)])
    async def test_窗口换算下发到查询(self, window: str, expected_hours: int):
        """近窗口 since = now − 窗口时长（24h/7d/30d）。"""
        import app.services.workbench_diagnosis as svc

        with (
            patch(
                "app.services.workbench_diagnosis._get_scope_unit_ids",
                new=AsyncMock(return_value=None),
            ),
            patch.object(svc, "_query_open_tag_rows", new=AsyncMock(return_value=[])) as mock_open,
            patch.object(svc, "_query_concl_rows", new=AsyncMock(return_value=[])),
            patch.object(svc, "_query_scope_loop_ids", new=AsyncMock(return_value=[])),
            patch.object(svc, "get_latest_fitness_per_loop", new=AsyncMock(return_value={})),
            patch.object(svc, "_query_rule_stat_rows", new=AsyncMock(return_value=[])),
            patch.object(svc, "_query_pareto", new=AsyncMock(return_value=[])),
            patch.object(svc, "_query_roots", new=AsyncMock(return_value=[])),
        ):
            await build_diagnosis(object(), window=window)

        since = mock_open.call_args[0][1]
        now_utc = datetime.now(UTC).replace(tzinfo=None)
        assert (now_utc - since).total_seconds() / 3600 == pytest.approx(expected_hours, abs=1)
