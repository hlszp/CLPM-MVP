"""P2 #40 TC3 大数据集性能测试.

验证各指标计算器在 2 小时 1Hz 采样（7200 点）大数据集下的：
    1. 执行时间在可接受范围（单指标 < 5s，防止 O(n²) 退化）
    2. 结果与小数据集行为一致（n=100 → n=7200 结果方向一致）

设计依据：
    - 项目记忆硬约束："统一 1 秒采集"
    - 项目记忆硬约束："Data sampling must use unified 1-second acquisition"
    - 算法说明 §3.7（性能边界）
    - 设计要求：2 小时时间窗口 × 1Hz = 7200 点
    - LTTB 降采样 maxPoints=2000（KPI 计算路径不走降采样，直接处理 7200 点）

注：settling_time（ARMA 模型辨识）复杂度最高（O(n·p²)），7200 点是
关键性能边界。其他计算器复杂度 O(n)，7200 点应在毫秒级完成。
"""

from __future__ import annotations

import math
import time

import numpy as np
import pytest

from app.services.metric_calculator.accuracy import AccuracyRateCalculator
from app.services.metric_calculator.auto_mode import AutoModeRateCalculator
from app.services.metric_calculator.good_value import GoodValueRateCalculator
from app.services.metric_calculator.oscillation import OscillationRateCalculator
from app.services.metric_calculator.output_trip import OutputTripIndexCalculator
from app.services.metric_calculator.saturation import SaturationRateCalculator
from app.services.metric_calculator.settling_time import SettlingTimeCalculator
from app.services.metric_calculator.stability import StabilityRateCalculator
from app.services.metric_calculator.stiction import StictionIndexCalculator

from .conftest import make_bundle

#: 大数据集点数（2 小时 × 1Hz）
LARGE_N = 7200

#: 性能阈值（秒）：单指标计算不应超过此值
PERF_THRESHOLD_S = 5.0

#: ARMA 模型辨识性能阈值（更宽松，因 O(n·p²) 复杂度）
ARMA_PERF_THRESHOLD_S = 10.0


# ---------------------------------------------------------------------------
# 数据生成辅助
# ---------------------------------------------------------------------------


def _make_large_normal_data(n: int = LARGE_N) -> dict:
    """生成 n 点正常工况数据（PV 微偏离 SP，OP 中间区域，全自动）."""
    rng = np.random.default_rng(seed=42)
    sp = [50.0] * n
    # PV 在 SP 附近小幅波动（σ=0.5）
    pv = [50.0 + 0.5 * rng.standard_normal() for _ in range(n)]
    # OP 在中间区域（50%）
    op = [50.0 + 0.3 * rng.standard_normal() for _ in range(n)]
    # 全自动模式
    mode = [1] * n
    return {"pv": pv, "sp": sp, "op": op, "mode": mode}


def _make_large_oscillation_data(n: int = LARGE_N) -> dict:
    """生成 n 点振荡数据（PV 在 SP 上下周期性波动，周期 20s）."""
    sp = [50.0] * n
    pv = [50.0 + 10.0 * math.sin(2 * math.pi * t / 20) for t in range(n)]
    op = [50.0 + 5.0 * math.sin(2 * math.pi * t / 20) for t in range(n)]
    mode = [1] * n
    return {"pv": pv, "sp": sp, "op": op, "mode": mode}


def _make_large_saturation_data(n: int = LARGE_N) -> dict:
    """生成 n 点饱和数据（50% 时间 OP=99 高饱和）."""
    rng = np.random.default_rng(seed=42)
    sp = [50.0] * n
    pv = [50.0 + 0.5 * rng.standard_normal() for _ in range(n)]
    # 50% 高饱和 + 50% 中间
    op = [99.5 if i % 2 == 0 else 50.0 for i in range(n)]
    mode = [1] * n
    return {"pv": pv, "sp": sp, "op": op, "mode": mode}


# ---------------------------------------------------------------------------
# 性能测试：各计算器在 7200 点下的执行时间
# ---------------------------------------------------------------------------


class TestLargeDatasetPerformance:
    """7200 点大数据集性能测试（单指标 < 5s）."""

    @pytest.mark.parametrize(
        "calculator_cls,metric_code,data_maker",
        [
            (AccuracyRateCalculator, "accuracy_rate", _make_large_normal_data),
            (AutoModeRateCalculator, "auto_mode_rate", _make_large_normal_data),
            (SaturationRateCalculator, "saturation_rate", _make_large_saturation_data),
            (OscillationRateCalculator, "oscillation_rate", _make_large_oscillation_data),
            (GoodValueRateCalculator, "good_value_rate", _make_large_normal_data),
            (StabilityRateCalculator, "stability_rate", _make_large_normal_data),
            (StictionIndexCalculator, "stiction_index", _make_large_normal_data),
            (OutputTripIndexCalculator, "output_trip_index", _make_large_normal_data),
        ],
        ids=lambda x: getattr(x, "__name__", str(x)),
    )
    def test_large_dataset_within_threshold(self, calculator_cls, metric_code, data_maker):
        """7200 点数据集：单指标计算 < 5s."""
        data = data_maker()
        bundle = make_bundle(data, metric_code=metric_code)
        calc = calculator_cls()

        start = time.perf_counter()
        result = calc.calculate(bundle)
        elapsed = time.perf_counter() - start

        # 性能断言
        assert elapsed < PERF_THRESHOLD_S, (
            f"{calculator_cls.__name__} 在 7200 点耗时 {elapsed:.3f}s > {PERF_THRESHOLD_S}s"
        )
        # 结果非异常（value 是 float 或 None，confidence_level 有值）
        assert result.metric_code == metric_code
        assert result.confidence_level is not None

    def test_settling_time_large_dataset_within_threshold(self):
        """settling_time（ARMA 模型辨识）在 7200 点 < 10s（O(n·p²) 复杂度更宽松）."""
        data = _make_large_normal_data()
        bundle = make_bundle(data, metric_code="settling_time")
        calc = SettlingTimeCalculator()

        start = time.perf_counter()
        result = calc.calculate(bundle)
        elapsed = time.perf_counter() - start

        assert elapsed < ARMA_PERF_THRESHOLD_S, (
            f"SettlingTimeCalculator 在 7200 点耗时 {elapsed:.3f}s > {ARMA_PERF_THRESHOLD_S}s"
        )
        assert result.metric_code == "settling_time"


# ---------------------------------------------------------------------------
# 一致性测试：7200 点 vs 100 点结果方向一致
# ---------------------------------------------------------------------------


class TestLargeDatasetConsistency:
    """大数据集与小数据集结果方向一致（防算法在大数据下退化）."""

    def test_accuracy_large_vs_small_consistent(self):
        """准确率：7200 点 vs 100 点，零偏差 → 都接近 100."""
        for n in (100, LARGE_N):
            val = [50.0] * n
            bundle = make_bundle({"pv": list(val), "sp": list(val)}, metric_code="accuracy_rate")
            result = AccuracyRateCalculator().calculate(bundle)
            assert result.value == 100.0, f"n={n}: accuracy={result.value} 应为 100"

    def test_saturation_large_vs_small_consistent(self):
        """饱和率：7200 点 vs 100 点，全 OP=99.5 → 都应为 100%."""
        for n in (100, LARGE_N):
            mode = [1] * n
            op = [99.5] * n
            bundle = make_bundle({"mode": mode, "op": op}, metric_code="saturation_rate")
            result = SaturationRateCalculator().calculate(bundle)
            assert result.value == 100.0
            assert result.details["saturation_type"] == "HIGH"

    def test_auto_mode_large_vs_small_consistent(self):
        """自控率：7200 点 vs 100 点，全自动 → 都应为 100%."""
        for n in (100, LARGE_N):
            mode = [1] * n
            bundle = make_bundle({"mode": mode}, metric_code="auto_mode_rate")
            result = AutoModeRateCalculator().calculate(bundle)
            assert result.value == 100.0

    def test_good_value_large_vs_small_consistent(self):
        """好值率：7200 点 vs 100 点，全 Good → 都应为 100%."""
        for n in (100, LARGE_N):
            pv = [50.0] * n
            bundle = make_bundle({"pv": pv}, metric_code="good_value_rate")
            result = GoodValueRateCalculator().calculate(bundle)
            assert result.value == 100.0

    def test_oscillation_large_recognized(self):
        """振荡率：7200 点振荡数据（周期 20s → 360 个完整周期）→ is_oscillating=True."""
        data = _make_large_oscillation_data()
        bundle = make_bundle(data, metric_code="oscillation_rate")
        result = OscillationRateCalculator().calculate(bundle)
        # 正弦波规律振荡，应被识别
        assert result.details["is_oscillating"] is True
        # 360 个周期 → 720 个零交叉
        assert result.details["zero_crossings"] >= 100
        # 振荡周期应接近 20s
        assert 15.0 <= result.details["oscillation_period"] <= 25.0


# ---------------------------------------------------------------------------
# 数据量级断言
# ---------------------------------------------------------------------------


class TestLargeDatasetScale:
    """验证测试确实使用了 7200 点（防止误用小数据集）."""

    def test_large_n_constant_is_7200(self):
        """LARGE_N 常量值校验（2 小时 × 1Hz = 7200）."""
        assert LARGE_N == 7200
        assert LARGE_N == 2 * 60 * 60  # 2 小时 × 60 分 × 60 秒

    def test_large_data_has_correct_point_count(self):
        """生成的数据确实有 7200 个点."""
        data = _make_large_normal_data()
        for key in ("pv", "sp", "op", "mode"):
            assert len(data[key]) == LARGE_N, f"{key} 长度应为 {LARGE_N}"

    def test_perf_threshold_reasonable(self):
        """性能阈值合理性校验."""
        # 单指标应 < 5s（O(n) 计算器应 < 0.5s）
        assert PERF_THRESHOLD_S == 5.0
        # ARMA 模型辨识 O(n·p²) 更宽松
        assert ARMA_PERF_THRESHOLD_S == 10.0
        assert ARMA_PERF_THRESHOLD_S > PERF_THRESHOLD_S
