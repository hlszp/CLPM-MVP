"""P5 批 α：1000 回路生成器纯函数单测（零 DB/TD 依赖）.

覆盖任务书批 α 核验点的可单测部分：
- 场景构成确定性分配（800/100/50/50；manual=eligible 内终态构造）
- synth_series：密集/稀疏角色形态、质量三态、缺口截断、手动 MODE=0、
  seed 可再生（同 loop 序号两次生成完全一致）
- tag/loop Excel 行构建（隔离前缀、量程、参评=是、7 角色绑定）
- manifest 行字段（P0-03 §1.2 冻结字段 + expected 终态 + synthetic 标注）
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from scripts.generate_capacity_dataset import (
    AREAS,
    LOOP_HEADERS,
    LOOPS_PER_UNIT,
    POINTS,
    PREFIX,
    ROLES,
    SCENARIO_B,
    TAG_HEADERS,
    TOTAL_LOOPS,
    UNITS_PER_AREA,
    _loop_composition,
    _loop_tag,
    _response_category,
    _unit_name,
    _xlsx,
    build_loop_rows,
    build_manifest_rows,
    build_tag_rows,
    synth_series,
)


class TestComposition:
    def test_场景b构成边界(self):
        assert [_loop_composition("b", i) for i in (1, 800)] == ["normal", "normal"]
        assert [_loop_composition("b", i) for i in (801, 900)] == [
            "insufficient",
            "insufficient",
        ]
        assert [_loop_composition("b", i) for i in (901, 950)] == ["manual", "manual"]
        assert [_loop_composition("b", i) for i in (951, 1000)] == ["gap", "gap"]

    def test_场景a全正常(self):
        for i in (1, 500, 1000):
            assert _loop_composition("a", i) == "normal"

    def test_构成计数与冻结配比一致(self):
        counts = {"normal": 0, "insufficient": 0, "manual": 0, "gap": 0}
        for i in range(1, TOTAL_LOOPS + 1):
            counts[_loop_composition("b", i)] += 1
        assert counts == SCENARIO_B


class TestSynthSeries:
    def test_正常回路密集稀疏形态(self):
        s = synth_series(1, "normal", "steady", 400.0)
        assert len(s["PV"]) == POINTS
        assert len(s["OP"]) == POINTS
        assert 1 <= len(s["SP"]) <= 10  # COV 稀疏
        assert len(s["MODE"]) == 1 and s["MODE"][0][1] == 1.0  # AUTO
        assert len(s["PID_P"]) == 1

    def test_手动回路MODE为0(self):
        s = synth_series(901, "manual", "steady", 400.0)
        assert s["MODE"][0][1] == 0.0  # MANUAL=0（eligible 内终态构造）

    def test_数据不足回路PV含BAD质量(self):
        s = synth_series(801, "insufficient", "steady", 400.0)
        bad = sum(1 for _t, _v, qc, _qr in s["PV"] if qc == 0)
        good = sum(1 for _t, _v, qc, _qr in s["PV"] if qc == 1)
        assert bad > POINTS * 0.5 and good > 0  # 大比例 BAD → expected INCONCLUSIVE

    def test_缺口回路仅前900点(self):
        s = synth_series(951, "gap", "steady", 400.0)
        assert len(s["PV"]) == 900 and len(s["OP"]) == 900
        assert s["PV"][-1][0] == 899

    def test_seed可再生_同序号两次完全一致(self):
        a = synth_series(123, "normal", "drift", 100.0)
        b = synth_series(123, "normal", "drift", 100.0)
        assert a == b

    def test_值有界且无NaN(self):
        import math

        for role in ("PV", "OP"):
            for _t, v, _q, _qr in synth_series(7, "normal", "noisy", 400.0)[role]:
                assert not math.isnan(v) and not math.isinf(v)
                if role == "OP":
                    assert 0.0 <= v <= 100.0


class TestRows:
    def test_tag行规模与前缀隔离(self):
        rows = build_tag_rows()
        assert len(rows) == TOTAL_LOOPS * 7
        assert all(str(r[0]).startswith(f"{PREFIX}0") or str(r[0]).startswith(PREFIX) for r in rows)
        assert all(PREFIX in str(r[0]) for r in rows)
        # PV 行带量程；PID 行无量程
        pv_row = next(r for r in rows if str(r[0]).endswith("_PV"))
        pid_row = next(r for r in rows if str(r[0]).endswith("_PID_P"))
        assert pv_row[3] is not None and pv_row[4] is not None
        assert pid_row[3] is None and pid_row[4] is None

    def test_loop行绑定与参评(self):
        rows = build_loop_rows()
        assert len(rows) == TOTAL_LOOPS
        tag = _loop_tag(1)
        row = rows[0]
        assert row[0] == tag
        assert row[2:6] == [f"{tag}_SP", f"{tag}_PV", f"{tag}_OP", f"{tag}_MODE"]
        assert row[8:11] == [f"{tag}_PID_P", f"{tag}_PID_I", f"{tag}_PID_D"]
        assert row[7] == "是" and row[14] == "是"  # 启用 + 参评（生成器红线前提）
        assert "synthetic" in row[1]

    def test_单元归属分布(self):
        rows = build_loop_rows()
        units = {r[6] for r in rows}
        assert len(units) == AREAS * UNITS_PER_AREA
        assert len(rows) / len(units) == LOOPS_PER_UNIT

    def test_xlsx可构造(self):
        data = _xlsx(TAG_HEADERS, build_tag_rows()[:10])
        assert data[:2] == b"PK"  # zip 头（合法 xlsx）
        data2 = _xlsx(LOOP_HEADERS, build_loop_rows()[:5])
        assert data2[:2] == b"PK"


class TestManifest:
    def test_manifest行字段与expected终态(self):
        units = {_unit_name(0, 0): "u-1"}
        rows = build_manifest_rows(units, "b", datetime(2026, 10, 12, 8, 0, 0))
        assert len(rows) == TOTAL_LOOPS
        first = rows[0]
        for key in (
            "loopTag",
            "unitName",
            "unitNodeId",
            "range",
            "responseCategory",
            "composition",
            "windowStart",
            "windowEnd",
            "pointsPerTag",
            "scenario",
            "includeInEvaluation",
            "expectedStatus",
            "synthetic",
        ):
            assert key in first, key
        by_comp = {r["composition"]: r for r in rows}
        assert by_comp["manual"]["expectedStatus"].startswith("EVALUATED(ELIGIBLE内终态")
        assert "INCONCLUSIVE" in by_comp["insufficient"]["expectedStatus"]
        assert "覆盖不足" in by_comp["gap"]["expectedStatus"]
        assert all(r["synthetic"] is True and r["includeInEvaluation"] is True for r in rows)

    def test_响应类别轮转登记(self):
        cats = {_response_category(i) for i in range(1, 21)}
        assert cats == {"steady", "oscillation", "drift", "noisy"}


class TestNaming:
    def test_位号前缀与补零(self):
        assert _loop_tag(1) == f"{PREFIX}0001"
        assert _loop_tag(1000) == f"{PREFIX}1000"

    def test_7角色齐备(self):
        assert ROLES == ("PV", "SP", "OP", "MODE", "PID_P", "PID_I", "PID_D")


@pytest.mark.parametrize("i", range(9, 1000, 10))
def test_正常组全部noisy回路真实5秒采样不产生失效尖峰(i):
    # PERF-T0949 的缺陷只在真实 BASE 5s 采样下暴露，1s 原始序列检查会漏掉。
    from app.contracts.data_types import ControlType, LoopPreprocessConfig, RawTimeSeries, TagGroup
    from app.services.preprocessing.pipeline import PreprocessingPipeline
    from scripts.generate_capacity_dataset import LOOP_TYPES, RANGES

    loop_type = LOOP_TYPES[(i - 1) % len(LOOP_TYPES)]
    control_type = ControlType.TEMPERATURE if loop_type == "TEMPERATURE" else ControlType.LEVEL
    span = RANGES[loop_type][1]
    series = synth_series(i, "normal", "noisy", span)
    pv = [row[1] for row in series["PV"]] + [series["PV"][-1][1]]
    sp_changes = {row[0]: row[1] for row in series["SP"]}
    sp = []
    held = sp_changes[0]
    for t in range(3601):
        held = sp_changes.get(t, held)
        sp.append(held)
    timestamps = [datetime(2026, 10, 13) + timedelta(seconds=t) for t in range(0, 3601, 5)]
    raw = RawTimeSeries(
        timestamps=timestamps,
        signals={"pv": pv[::5], "sp": sp[::5]},
        quality_codes={"pv_quality": [1] * len(timestamps)},
    )
    block = PreprocessingPipeline(
        LoopPreprocessConfig(
            loop_id=f"PERF-T{i:04d}", control_type=control_type, range_min=0, range_max=span
        )
    ).process(raw, TagGroup.BASE)
    assert all(block.validity["pv_valid"]), block.outlier_reasons["pv"]
    assert all(block.validity["sp_valid"])
