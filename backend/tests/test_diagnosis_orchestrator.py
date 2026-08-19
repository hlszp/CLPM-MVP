"""诊断编排器测试：mock 取数与 DB，验证正常诊断 / 数据门禁 / 算子异常三场景。"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import numpy as np
import pytest

import app.services.diagnosis_orchestrator as orch
from app.contracts.data_types import RawTimeSeries
from app.services.diagnosis_operators import OPERATOR_REGISTRY

LOOP_ID = str(uuid4())
START = datetime(2026, 8, 15, 0, 0, 0)
END = START + timedelta(hours=1)


def _frozen_series(n: int = 3600) -> RawTimeSeries:
    """前 60% 冻结 + 后 40% 正常 → 仪表 INSTRUMENT 主分类。"""
    rng = np.random.default_rng(1)
    pv = np.concatenate([np.full(int(n * 0.6), 50.0), 50.0 + rng.normal(0, 0.5, n - int(n * 0.6))])
    ts = [START + timedelta(seconds=i) for i in range(n)]
    return RawTimeSeries(
        timestamps=ts,
        signals={
            "pv": pv.tolist(),
            "sp": [50.0] * n,
            "op": (50.0 + 0.1 * rng.normal(0, 1, n)).tolist(),
            "mode": [1] * n,
        },
        quality_codes={"pv_quality": [1] * n},  # TDengine：1=Good
    )


def _mock_db(loop: MagicMock) -> AsyncMock:
    db = AsyncMock()
    loop_result = MagicMock()
    loop_result.scalar_one_or_none.return_value = loop
    mapping_result = MagicMock()
    mapping_result.scalars.return_value.all.return_value = []

    kpi_result = MagicMock()
    kpi_result.one_or_none.return_value = (0.95, 75.0)

    db.execute.side_effect = [loop_result, mapping_result, kpi_result]
    db.add = MagicMock()  # sync 方法，避免 AsyncMock 未 await 告警
    return db


def _mock_loop() -> MagicMock:
    loop = MagicMock()
    loop.id = LOOP_ID
    loop.tag_name = "TEST-001"
    loop.loop_type = "FLOW"
    return loop


async def _run(raw_series: RawTimeSeries | None):
    loop = _mock_loop()
    db = _mock_db(loop)

    provider = MagicMock()
    provider.make_query_fn.return_value = AsyncMock(return_value=raw_series)

    with (
        patch.object(orch, "get_provider", return_value=provider),
        patch(
            "app.services.diagnosis_threshold.recommend_for_loop",
            new=AsyncMock(return_value={}),
        ),
    ):
        run = await orch.run_diagnosis_for_loop(
            db, LOOP_ID, start=START, end=END, task_id="task-1", triggered_by="tester"
        )
    return run, db


@pytest.mark.asyncio
async def test_normal_run_classifies_instrument() -> None:
    run, db = await _run(_frozen_series())
    assert run is not None
    assert run.primary_category == "INSTRUMENT"
    assert run.status == "SUCCESS"
    assert run.data_gate["passed"] is True
    assert run.severity in {"HIGH", "MEDIUM", "LOW"}
    # 全部 11 个算子执行成功（mode/pv_quality 均在输入中）
    executed = [r["executed"] for r in run.operator_results.values()]
    assert len(run.operator_results) == 11
    assert all(executed)
    # 融合结果含仪表族命中
    assert run.fusion_results["QUALITY_ABNORMAL"]["detected"] is True
    # 建议非空且第一条为主分类建议
    assert run.recommendations[0]["priority"] == 1
    # 波形快照自包含且 ≤2000 点
    assert 0 < len(run.evidence_charts["trend"]["ts"]) <= 2000
    assert len(run.evidence_charts["scatter"]["pv"]) <= 2000
    db.add.assert_called_once()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_gate_fail_outputs_data_insufficient() -> None:
    run, _ = await _run(None)  # 宽表查询失败 → 无数据
    assert run is not None
    assert run.data_gate["passed"] is False
    assert run.primary_category == "DATA_INSUFFICIENT"
    assert run.status == "SUCCESS"  # 门禁不过属正常完成
    assert run.operator_results == {}  # 不执行算子
    assert run.recommendations[0]["content"].startswith("先通过数据管理")


@pytest.mark.asyncio
async def test_operator_error_yields_partial_status() -> None:
    original = OPERATOR_REGISTRY["oscillation_fft"]

    def _boom(inp, th):  # noqa: ARG001
        msg = "synthetic operator failure"
        raise RuntimeError(msg)

    OPERATOR_REGISTRY["oscillation_fft"] = (original[0], _boom)
    try:
        run, _ = await _run(_frozen_series())
    finally:
        OPERATOR_REGISTRY["oscillation_fft"] = original

    assert run is not None
    assert run.status == "PARTIAL"
    fft = run.operator_results["oscillation_fft"]
    assert fft["executed"] is False
    assert "synthetic operator failure" in (fft["error"] or "")
    # 其余算子不受影响，分类正常产出
    assert run.primary_category == "INSTRUMENT"


@pytest.mark.asyncio
async def test_loop_not_found_returns_none() -> None:
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db.execute.side_effect = [result]
    run = await orch.run_diagnosis_for_loop(db, LOOP_ID, start=START, end=END)
    assert run is None


# ---------------------------------------------------------------------------
# 方案 A：metricSummary 聚合（2026-08-19）
# ---------------------------------------------------------------------------


class TestBuildMetricSummary:
    """_build_metric_summary：窗口 KPI 均值 + 算子特征 → 0~100 统一口径。"""

    def test_full_kpi_window(self) -> None:
        """KPI 窗口均值齐全：负向全 kpi 源，坏值率=100−好值率。"""
        kpi_avgs = {
            "goodValueRate": 93.67,
            "saturationRate": 0.53,
            "oscillationRate": 82.84,
            "stictionIndex": 0.0,
            "settlingTime": 12.5,
            "outputTripIndex": 3.2,
            "score": 66.68,
            "effectiveAutoRate": 99.47,
        }
        ops = {
            "quality_code_rules": {"features": {"bad_rate": 0.0633}},
            "output_saturation": {"features": {"saturation_rate": 0.0044}},
        }
        ms = orch._build_metric_summary(kpi_avgs, ops)
        neg = ms["negative"]
        assert neg["badValueRate"] == round(100 - 93.67, 2)
        assert neg["saturationRate"] == 0.53
        assert neg["oscillationRate"] == 82.84
        assert neg["stictionIndex"] == 0.0
        assert neg["settlingTime"] == 12.5
        assert neg["outputTravelIndex"] == 3.2
        assert ms["source"]["badValueRate"] == "kpi"
        assert ms["source"]["saturationRate"] == "kpi"
        assert ms["positive"]["score"] == 66.68
        assert ms["positive"]["effectiveAutoRate"] == 99.47

    def test_operator_fallback(self) -> None:
        """窗口无 KPI 快照：算子特征 0~1 → ×100 兜底，source=operator。"""
        ops = {
            "quality_code_rules": {"features": {"bad_rate": 0.0633}},
            "output_saturation": {"features": {"saturation_rate": 0.0044}},
            "stiction_ellipse": {"features": {"stiction_index": 0.42}},
        }
        ms = orch._build_metric_summary({}, ops)
        neg = ms["negative"]
        assert neg["badValueRate"] == 6.33
        assert neg["saturationRate"] == 0.44
        assert neg["stictionIndex"] == 42.0
        assert ms["source"]["badValueRate"] == "operator"
        assert ms["source"]["saturationRate"] == "operator"
        assert ms["source"]["stictionIndex"] == "operator"
        assert ms["positive"]["score"] is None

    def test_empty_inputs(self) -> None:
        """全空输入：负向全 None，source=none，不抛异常。"""
        ms = orch._build_metric_summary({}, {})
        assert all(v is None for v in ms["negative"].values())
        assert set(ms["source"].values()) == {"none"}
        assert all(v is None for v in ms["positive"].values())

    def test_invalid_operator_feature_ignored(self) -> None:
        """算子特征非法值（None/字符串）：透传 None 不抛异常。"""
        ops = {
            "quality_code_rules": {"features": {"bad_rate": "not-a-number"}},
            "output_saturation": {"features": {"saturation_rate": None}},
        }
        ms = orch._build_metric_summary({}, ops)
        assert ms["negative"]["badValueRate"] is None
        assert ms["negative"]["saturationRate"] is None

    def test_kpi_priority_over_operator(self) -> None:
        """KPI 与算子同时有值：KPI 优先（同时间窗口径更可信）。"""
        kpi_avgs = {"saturationRate": 2.11}
        ops = {"output_saturation": {"features": {"saturation_rate": 0.9}}}
        ms = orch._build_metric_summary(kpi_avgs, ops)
        assert ms["negative"]["saturationRate"] == 2.11
        assert ms["source"]["saturationRate"] == "kpi"


@pytest.mark.asyncio
async def test_run_persists_metric_summary() -> None:
    """诊断 run 落库携带 metricSummary（非空且含 negative/positive/source）。"""
    run, _ = await _run(_frozen_series())
    assert run is not None
    ms = run.metric_summary
    assert isinstance(ms, dict)
    assert set(ms.keys()) == {"negative", "positive", "source"}
    assert "badValueRate" in ms["negative"]
    assert "score" in ms["positive"]
