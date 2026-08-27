"""A-03 工作台诊断聚合 service 单测（M2 批次 G-诊断 · 14 号方案 A2 迁 diagnosis_run）.

覆盖：
- 纯 shaper：shape_open_tags（Top6 筛选/v2 severity 排序与四档映射/fitness 分级/
  置信度归一/SLA 字段下线）、filter_open_tag_rows（未处置语义：终态处置 run 被排除）、
  synth_disposition + shape_concl_timeline（四态合成/时间倒序/only_active 过滤/中文标签）、
  shape_fitness_gates（L0 横幅触发/L4 徽章/进度条计算/空数据）、
  shape_rule_stats（JSONB 聚合行 × 中文规则名）、shape_pareto（中文标签 + 代码域）、
  shape_rootcause_top（症状标签中文映射）
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
    filter_open_tag_rows,
    shape_concl_timeline,
    shape_fitness_gates,
    shape_open_tags,
    shape_pareto,
    shape_rootcause_top,
    shape_rule_stats,
    shape_summary_band,
    synth_disposition,
)

NOW = datetime(2026, 8, 25, 12, 0, 0)


# ---------------------------------------------------------------------------
# 合成行构造（diagnosis_run 联查行域）
# ---------------------------------------------------------------------------


def _run_row(
    run_id: str = "run-1",
    loop_id: str = "loop-1",
    loop_name: str = "TIC-408",
    severity: str | None = "HIGH",
    created_at: datetime | None = None,
    primary_category: str | None = "VALVE",
    confidence: Any = 0.96,
    conclusion: str | None = "主分类 阀门/执行机构问题（置信 0.96）：粘滞算子命中",
    top_symptom: str | None = "VALVE_STICTION",
    terminal_action_cnt: int = 0,
    unit_name: str | None = "裂解单元",
    factory_name: str | None = "乙烯装置",
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "loop_id": loop_id,
        "loop_name": loop_name,
        "severity": severity,
        "created_at": created_at or NOW - timedelta(hours=1),
        "primary_category": primary_category,
        "confidence": confidence,
        "conclusion": conclusion,
        "top_symptom": top_symptom,
        "terminal_action_cnt": terminal_action_cnt,
        "unit_name": unit_name,
        "factory_name": factory_name,
    }


def _concl_row(
    run_id: str = "run-1",
    loop_id: str = "loop-1",
    loop_name: str = "TIC-408",
    review_status: str = "PENDING",
    converted_cnt: int = 0,
    ignored_cnt: int = 0,
    ts: datetime | None = None,
    primary_category: str | None = "VALVE",
    evidence_summary: str | None = "粘滞算子命中：椭圆拟合（融合置信 0.96）",
    confidence: Any = 0.96,
    severity: str | None = "HIGH",
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "loop_id": loop_id,
        "loop_name": loop_name,
        "review_status": review_status,
        "converted_cnt": converted_cnt,
        "ignored_cnt": ignored_cnt,
        "ts": ts or NOW - timedelta(hours=1),
        "primary_category": primary_category,
        "evidence_summary": evidence_summary,
        "confidence": confidence,
        "severity": severity,
    }


# ===========================================================================
# filter_open_tag_rows（未处置语义 · A2-1）
# ===========================================================================


class TestFilterOpenTagRows:
    def test_终态处置run被排除(self):
        """关联建议达终态（CONVERTED/REJECTED/IGNORED）的 run 出队；无关联/非终态保留。"""
        rows = [
            _run_row(run_id="r-none", terminal_action_cnt=0),
            _run_row(run_id="r-conv", terminal_action_cnt=1),  # CONVERTED
            _run_row(run_id="r-rej", terminal_action_cnt=1),  # REJECTED
            _run_row(run_id="r-ign", terminal_action_cnt=1),  # IGNORED
            _run_row(run_id="r-pending", terminal_action_cnt=0),  # PENDING/ACCEPTED 建议
        ]
        out = filter_open_tag_rows(rows)
        assert [r["run_id"] for r in out] == ["r-none", "r-pending"]

    def test_空数据容错(self):
        assert filter_open_tag_rows([]) == []


# ===========================================================================
# synth_disposition + shape_concl_timeline（四态合成 · A2-2）
# ===========================================================================


class TestSynthDisposition:
    def test_四态优先级(self):
        """CONVERTED > ACK_REVIEWED > IGNORED > UNADDRESSED。"""
        assert synth_disposition("REVIEWED", 1, 1) == "CONVERTED"
        assert synth_disposition("REVIEWED", 0, 1) == "ACK_REVIEWED"
        assert synth_disposition("PENDING", 0, 1) == "IGNORED"
        assert synth_disposition("PENDING", 0, 0) == "UNADDRESSED"
        assert synth_disposition(None, 0, 0) == "UNADDRESSED"


class TestShapeConclTimeline:
    def test_四态合成与时间倒序(self):
        rows = [
            _concl_row(run_id="r-old", ts=NOW - timedelta(hours=5), converted_cnt=1),
            _concl_row(run_id="r-new", ts=NOW - timedelta(hours=1), ignored_cnt=1),
            _concl_row(run_id="r-mid", ts=NOW - timedelta(hours=3), review_status="REVIEWED"),
        ]
        out = shape_concl_timeline(rows)
        assert [r["result_id"] for r in out] == ["r-new", "r-mid", "r-old"]
        dispositions = {r["result_id"]: r["disposition"] for r in out}
        assert dispositions == {"r-new": "IGNORED", "r-mid": "ACK_REVIEWED", "r-old": "CONVERTED"}

    def test_默认态UNADDRESSED与id回退run_id(self):
        out = shape_concl_timeline([_concl_row()])
        assert out[0]["disposition"] == "UNADDRESSED"
        assert out[0]["id"] == "run-1"
        assert out[0]["result_id"] == "run-1"

    def test_中文category与代码域tag_code(self):
        out = shape_concl_timeline([_concl_row(primary_category="VALVE")])
        assert out[0]["category"] == "阀门/执行机构问题"
        assert out[0]["tag_code"] == "VALVE"

    def test_severity映射四档(self):
        out = shape_concl_timeline(
            [
                _concl_row(run_id="r-h", severity="HIGH"),
                _concl_row(run_id="r-m", severity="MEDIUM"),
                _concl_row(run_id="r-l", severity="LOW"),
            ]
        )
        sev = {r["result_id"]: r["severity"] for r in out}
        assert sev == {"r-h": "CRITICAL", "r-m": "WARN", "r-l": "INFO"}

    def test_only_active过滤未处置(self):
        rows = [
            _concl_row(run_id="r-un"),
            _concl_row(run_id="r-cv", converted_cnt=1),
            _concl_row(run_id="r-ig", ignored_cnt=1),
            _concl_row(run_id="r-ack", review_status="REVIEWED"),
        ]
        out = shape_concl_timeline(rows, only_active=True)
        assert [r["result_id"] for r in out] == ["r-un"]
        # 默认不过滤
        assert len(shape_concl_timeline(rows)) == 4

    def test_空数据容错(self):
        assert shape_concl_timeline([]) == []
        assert shape_concl_timeline([], only_active=True) == []


# ===========================================================================
# shape_open_tags（F-DG-01 · v2 run 域）
# ===========================================================================


class TestShapeOpenTags:
    def test_top6筛选与severity排序(self):
        """8 条候选 → Top6；HIGH 优先于 MEDIUM/LOW（v2 三档原生域排序）。"""
        rows = [
            _run_row(run_id=f"r-{i}", severity=sev)
            for i, sev in enumerate(
                ["MEDIUM", "HIGH", "LOW", "MEDIUM", "MEDIUM", "HIGH", "LOW", "MEDIUM"]
            )
        ]
        out = shape_open_tags(rows, {}, {})
        assert len(out) == OPEN_TAGS_TOP_N
        # 前 2 条 HIGH（→CRITICAL），第 3~6 条 MEDIUM（→WARN）；LOW 被裁掉
        assert [r["severity"] for r in out[:2]] == ["CRITICAL", "CRITICAL"]
        assert [r["severity"] for r in out[2:6]] == ["WARN", "WARN", "WARN", "WARN"]
        assert "INFO" not in [r["severity"] for r in out]

    def test_同severity按时间降序(self):
        rows = [
            _run_row(run_id="r-old", created_at=NOW - timedelta(hours=8)),
            _run_row(run_id="r-new", created_at=NOW - timedelta(hours=1)),
        ]
        out = shape_open_tags(rows, {}, {})
        assert [r["tag_id"] for r in out] == ["r-new", "r-old"]

    def test_SLA字段下线(self):
        """D1=a：返回结构不再含 sla_due_sec / sla_stage。"""
        out = shape_open_tags([_run_row()], {}, {})
        assert "sla_due_sec" not in out[0]
        assert "sla_stage" not in out[0]

    def test_category中文与symptom中文标签(self):
        row = _run_row(primary_category="VALVE", top_symptom="VALVE_STICTION")
        out = shape_open_tags([row], {}, {})
        assert out[0]["category"] == "阀门/执行机构问题"
        assert out[0]["symptom"] == "阀门粘滞"

    def test_fitness分级与spark注入(self):
        rows = [_run_row(loop_id="loop-1"), _run_row(run_id="r-2", loop_id="loop-2")]
        spark_map = {"loop-1": [66.6, 65.2, 63.4]}
        fitness_map = {"loop-1": "L2", "loop-2": "L0"}
        out = shape_open_tags(rows, spark_map, fitness_map)
        by_loop = {r["loop_id"]: r for r in out}
        assert by_loop["loop-1"]["spark"] == [66.6, 65.2, 63.4]
        assert by_loop["loop-1"]["fitness_level"] == "L2"
        assert by_loop["loop-2"]["fitness_level"] == "L0"
        assert by_loop["loop-2"]["spark"] == []

    def test_confidence归一化(self):
        """0~1 口径（primary_confidence）透传；>1 兜底归一；None 透传。"""
        rows = [
            _run_row(run_id="r-a", confidence=0.96),
            _run_row(run_id="r-b", confidence=85),
            _run_row(run_id="r-c", confidence=None),
        ]
        out = shape_open_tags(rows, {}, {})
        by_id = {r["tag_id"]: r for r in out}
        assert by_id["r-a"]["confidence"] == 0.96
        assert by_id["r-b"]["confidence"] == 0.85
        assert by_id["r-c"]["confidence"] is None

    def test_空数据容错(self):
        assert shape_open_tags([], {}, {}) == []


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
# shape_rule_stats + shape_pareto + shape_rootcause_top（D2=a / A2-3 / A2-4）
# ===========================================================================


class TestShapeRuleStats:
    def test_标签域名与中文规则名(self):
        """rule_id 保标签域名（OSCILLATION），name 为中文标签（D2=a 口径）。"""
        rows = [
            {"tag_code": "OSCILLATION", "hits": 4, "resolved": 1},
            {"tag_code": "VALVE_STICTION", "hits": 3, "resolved": 3},
        ]
        out = shape_rule_stats(rows)
        assert out[0] == {
            "rule_id": "OSCILLATION",
            "name": "回路振荡",
            "hits": 4,
            "resolved_rate": 0.25,
        }
        assert out[1]["name"] == "阀门粘滞"
        assert out[1]["resolved_rate"] == 1.0

    def test_零命中解决率为None(self):
        assert (
            shape_rule_stats([{"tag_code": "X", "hits": 0, "resolved": 0}])[0]["resolved_rate"]
            is None
        )

    def test_空数据容错(self):
        assert shape_rule_stats([]) == []


class TestShapePareto:
    def test_中文标签与代码域并存(self):
        rows = [
            {"category_code": "VALVE", "tag_count": 5, "converted_count": 1, "ignored_count": 0},
            {"category_code": "TUNING", "tag_count": 2, "converted_count": 0, "ignored_count": 1},
        ]
        out = shape_pareto(rows)
        assert out[0]["root_cause"] == "阀门/执行机构问题"
        assert out[0]["root_cause_code"] == "VALVE"
        assert out[0]["tag_count"] == 5
        assert out[1]["root_cause"] == "参数问题（PID 整定）"
        assert all(r["sla_warned_count"] == 0 for r in out)  # D1=a SLA 下线恒 0

    def test_按tag_count降序(self):
        rows = [
            {"category_code": "TUNING", "tag_count": 1},
            {"category_code": "VALVE", "tag_count": 9},
        ]
        assert shape_pareto(rows)[0]["root_cause_code"] == "VALVE"


class TestShapeRootcauseTop:
    def test_症状标签中文映射与tag_type别名(self):
        rows = [
            {
                "tag_code": "OSCILLATION",
                "count": 5,
                "active_count": 3,
                "severity_rank": 4,
            }
        ]
        out = shape_rootcause_top(rows)
        assert out[0]["tag_type"] == "OSCILLATION"
        assert out[0]["tag_code"] == "OSCILLATION"
        assert out[0]["tag_name"] == "回路振荡"
        assert out[0]["count"] == 5
        assert out[0]["severity"] == "CRITICAL"  # HIGH→rank4→CRITICAL

    def test_severity_rank映射四档域(self):
        rows = [
            {"tag_code": "X", "count": 1, "active_count": 1, "severity_rank": 2},
            {"tag_code": "Y", "count": 1, "active_count": 1, "severity_rank": 1},
        ]
        out = shape_rootcause_top(rows)
        sev = {r["tag_code"]: r["severity"] for r in out}
        assert sev == {"X": "WARN", "Y": "INFO"}


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
        run_rows = [_run_row(loop_id="loop-1")]
        concl_rows = [_concl_row(loop_id="loop-1")]
        fitness = {"loop-1": LoopFitnessLatest(loop_id="loop-1", level="L2", tags=[], detail={})}
        with (
            patch(
                "app.services.workbench_diagnosis._get_scope_unit_ids",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "app.services.workbench_diagnosis._query_open_tag_rows",
                new=AsyncMock(return_value=run_rows),
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
                new=AsyncMock(return_value=[{"tag_code": "OSCILLATION", "hits": 2, "resolved": 1}]),
            ),
            patch(
                "app.services.workbench_diagnosis._query_pareto_rows",
                new=AsyncMock(
                    return_value=[
                        {
                            "category_code": "VALVE",
                            "tag_count": 5,
                            "converted_count": 0,
                            "ignored_count": 0,
                        }
                    ]
                ),
            ),
            patch(
                "app.services.workbench_diagnosis._query_rootcause_rows",
                new=AsyncMock(
                    return_value=[
                        {
                            "tag_code": "OSCILLATION",
                            "count": 5,
                            "active_count": 3,
                            "severity_rank": 4,
                        }
                    ]
                ),
            ),
        ):
            out = await build_diagnosis(object(), scope_type="GLOBAL", window="24h")

        assert out["scope"] == {"type": "GLOBAL", "id": None}
        assert out["window"] == "24h"
        # summary_band
        assert "summary_band" in out
        assert out["summary_band"]["worsening_loops"] == 1  # open_tags=1
        assert out["summary_band"]["diag_count"] == 1  # concl=1 (置信度0.96→归一)
        assert out["summary_band"]["high_confidence_count"] == 1
        assert out["summary_band"]["avg_latency_ok"] is True
        assert out["summary_band"]["engine_version"] == "v3.2.1"
        # open_tags（severity 映射 + 中文 + SLA 下线）
        assert len(out["open_tags"]) == 1
        assert out["open_tags"][0]["spark"] == [70.0, 68.0]
        assert out["open_tags"][0]["fitness_level"] == "L2"
        assert out["open_tags"][0]["severity"] == "CRITICAL"
        assert out["open_tags"][0]["category"] == "阀门/执行机构问题"
        assert "sla_due_sec" not in out["open_tags"][0]
        # concl_timeline
        assert len(out["concl_timeline"]) == 1
        assert out["concl_timeline"][0]["disposition"] == "UNADDRESSED"
        # fitness_gates（分母 loop-1，L2 一条）
        assert out["fitness_gates"]["level"] == "L2"
        assert out["fitness_gates"]["level_counts"]["L2"] == 1
        assert out["fitness_gates"]["total"] == 1
        # rule_stats / pareto / rootcause_top
        assert out["rule_stats"][0]["rule_id"] == "OSCILLATION"
        assert out["rule_stats"][0]["name"] == "回路振荡"
        assert out["pareto"][0]["root_cause"] == "阀门/执行机构问题"
        assert out["pareto"][0]["root_cause_code"] == "VALVE"
        assert out["rootcause_top"][0]["tag_type"] == "OSCILLATION"

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
                "app.services.workbench_diagnosis._query_pareto_rows",
                new=AsyncMock(side_effect=RuntimeError("db down")),
            ),
            patch(
                "app.services.workbench_diagnosis._query_rootcause_rows",
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
        """近窗口 since = now − 窗口时长（24h/7d/30d）；rule_stats 固定近 30d。"""
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
            patch.object(svc, "_query_rule_stat_rows", new=AsyncMock(return_value=[])) as mock_rule,
            patch.object(svc, "_query_pareto_rows", new=AsyncMock(return_value=[])),
            patch.object(svc, "_query_rootcause_rows", new=AsyncMock(return_value=[])),
        ):
            await build_diagnosis(object(), window=window)

        now_utc = datetime.now(UTC).replace(tzinfo=None)
        since = mock_open.call_args[0][1]
        assert (now_utc - since).total_seconds() / 3600 == pytest.approx(expected_hours, abs=1)
        since_30d = mock_rule.call_args[0][1]
        assert (now_utc - since_30d).total_seconds() / 3600 == pytest.approx(720, abs=1)
