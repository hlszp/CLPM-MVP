"""MODE 离散域不得使用 PV 工程量程或模拟量跳变规则。"""

from datetime import datetime, timedelta

import pytest

from app.contracts.data_types import ControlType, LoopPreprocessConfig, RawTimeSeries, TagGroup
from app.services.metric_calculator.effective_auto import EffectiveAutoRateCalculator
from app.services.metric_data_bundle import MetricDataBundleAssembler
from app.services.preprocessing.data_quality_assessor import DataQualityAssessor
from app.services.preprocessing.pipeline import PreprocessingPipeline


@pytest.mark.parametrize("pv_range", [(185.0, 215.0), (0.0, 2.5)])
@pytest.mark.parametrize("mode", range(5))
def test_standard_mode_survives_unrelated_pv_range(pv_range, mode):
    start = datetime(2026, 10, 20, 9)
    config = LoopPreprocessConfig("mode-proof", ControlType.FLOW, *pv_range)
    pv = sum(pv_range) / 2
    raw = RawTimeSeries(
        [start + timedelta(seconds=i) for i in range(121)],
        {"pv": [pv] * 121, "sp": [pv] * 121, "op": [50.0] * 121, "mode": [mode] * 121},
        {"pv_quality": [1] * 121},
    )
    block = PreprocessingPipeline(config).process(raw, TagGroup.MODE_HF)
    assessment = DataQualityAssessor(config).assess(raw)
    assert all(block.validity["mode_valid"])
    assert all(assessment.validity["mode_valid"])
    assert block.loop_valid_rate == assessment.loop_valid_rate == 1.0
    bundle = MetricDataBundleAssembler().assemble(
        "effective_auto_rate", block, "mode_valid && op_valid"
    )
    result = EffectiveAutoRateCalculator().calculate(bundle)
    assert result.value == (0.0 if mode == 0 else 100.0)


def test_mode_transitions_are_not_analog_spikes_and_bad_values_stay_invalid():
    values = [0, 1, 4, 0, 2, 3, 1.5, -1, 999, None, float("nan"), float("inf"), 1]
    raw = RawTimeSeries(
        [datetime(2026, 10, 20) + timedelta(seconds=i) for i in range(len(values))],
        {"mode": values},
        {"mode_quality": [1] * (len(values) - 1) + [0]},
    )
    config = LoopPreprocessConfig("mode-proof", ControlType.FLOW, 185.0, 215.0)
    for output in (
        PreprocessingPipeline(config).process(raw, TagGroup.MODE_HF),
        DataQualityAssessor(config).assess(raw),
    ):
        assert output.validity["mode_valid"] == [True] * 6 + [False] * 7
        assert all(not reasons for reasons in output.outlier_reasons["mode"][:6])
        assert "QC_BAD" in output.outlier_reasons["mode"][-1]
        assert "OUT_OF_RANGE" in output.outlier_reasons["mode"][8]


@pytest.mark.asyncio
async def test_preprocess_change_invalidates_bundle_cache(monkeypatch):
    from unittest.mock import AsyncMock

    from app.services import data_planner
    from app.services.cache.l2_bundle import L2BundleCache

    monkeypatch.setattr(
        "app.services.data_source.history_layout_router.current_layout_version",
        AsyncMock(return_value="point-proof"),
    )
    monkeypatch.setattr(data_planner, "PREPROCESS_VERSION", "pre_v2")
    old_version = await data_planner.resolve_data_version()
    monkeypatch.setattr(data_planner, "PREPROCESS_VERSION", "pre_v3")
    new_version = await data_planner.resolve_data_version()
    args = {
        "loop_id": "mode-proof",
        "metrics": ["effective_auto_rate"],
        "time_window_start": datetime(2026, 10, 20, 9),
        "time_window_end": datetime(2026, 10, 20, 10),
        "control_type": "TC",
    }
    assert L2BundleCache.build_key(**args, data_version=old_version) != L2BundleCache.build_key(
        **args, data_version=new_version
    )
