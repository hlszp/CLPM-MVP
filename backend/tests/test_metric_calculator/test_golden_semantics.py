"""核心指标数值金标准（整改 S1-c）。

存在理由
--------
2026-09-13 评审发现：算法层的多项口径缺陷**全部在"单测通过"状态下存在**
（accuracy 归一化倒挂、stiction 双门控方向相反、NaN 被静默放大为上界、
SOPDT 缺 tau 致整定差 2 个数量级）。原因不是测试少，而是测试只断言
"能跑通/结构对"，从不断言"数值应该是多少"。

本文件补上数值口径这一层，分两类：

1. **TestMetricGoldenSemantics** —— 当前行为正确、必须不回归的硬断言；
2. **TestKnownAlgorithmDefects** —— 已知错误、已登记入整改方案的口径，
   以 `pytest.mark.xfail(strict=True)` 固化：
   - 现在必然失败（证明缺陷真实存在，不是纸面判断）；
   - S3 阶段修好后会变为 XPASS —— strict 模式使其**转为测试失败**，
     强制修复者同步更新此处的期望值，杜绝"悄悄改口径"。

因此本文件同时承担"回归守护"与"缺陷台账"两个职责。

相关登记：整改方案 G18（accuracy）、G19（stiction）、G20（数值出口守卫）。
"""

from __future__ import annotations

import math

import pytest

from app.services.metric_calculator.accuracy import AccuracyRateCalculator
from app.services.metric_calculator.base import MetricCalculatorBase
from app.services.metric_calculator.good_value import GoodValueRateCalculator
from app.services.metric_calculator.oscillation import OscillationRateCalculator
from app.services.metric_calculator.stability import StabilityRateCalculator

from .conftest import make_bundle

#: 计算器默认 loop_confidence_level 为 E（视同不可计算），金标准用例需显式给 A
_CONF = "A"


def _confident(bundle):
    """把回路级可信度置为 A，避免落入 INCONCLUSIVE 分支。"""
    bundle.data_block.loop_confidence_level = _CONF
    return bundle


# ---------------------------------------------------------------------------
# 一、硬断言：当前正确，必须不回归
# ---------------------------------------------------------------------------


class TestMetricGoldenSemantics:
    """数值口径回归守护。"""

    def test_clamp_bounds_are_monotonic(self) -> None:
        """_clamp 对有限值必须双向截断且保持单调。"""
        clamp = MetricCalculatorBase._clamp
        assert clamp(-5.0) == 0.0
        assert clamp(0.0) == 0.0
        assert clamp(50.0) == 50.0
        assert clamp(100.0) == 100.0
        assert clamp(150.0) == 100.0
        # 单调：a < b ⟹ clamp(a) <= clamp(b)
        assert clamp(20.0) <= clamp(80.0)

    def test_good_value_all_good_is_100(self) -> None:
        """全部 Good 质量码 → 好值率 100。"""
        n = 120
        bundle = _confident(
            make_bundle(
                {"pv": [50.0] * n, "pv_quality": [1] * n},
                metric_code="good_value_rate",
            )
        )
        result = GoodValueRateCalculator().calculate(bundle)
        assert result.value == 100.0, f"全 Good 应为 100，实际 {result.value}"

    def test_stability_constant_signal_is_high(self) -> None:
        """PV 恒定（无波动）→ 稳定率高。"""
        n = 200
        bundle = _confident(
            make_bundle(
                {"pv": [50.0] * n, "sp": [50.0] * n, "op": [50.0] * n, "pv_range": [100.0] * n},
                metric_code="stability_rate",
            )
        )
        result = StabilityRateCalculator().calculate(bundle)
        if result.value is not None:
            assert result.value >= 90.0, f"恒定信号稳定率应 >=90，实际 {result.value}"

    def test_oscillation_pure_noise_is_not_oscillating(self) -> None:
        """纯高频噪声不得被判为振荡（P1 抗噪半周期门控的核心承诺）。

        白噪声伪穿越的 IAE 相似率实测可达 0.9+，单靠相似率无法区分噪声与
        真实振荡，故引入 min_half_period_samples 门控。本用例守护该门控：
        逐点交替的噪声（半周期 = 1 采样点，远低于门控 8）必须判非振荡。
        """
        n = 240
        pv = [50.0 + (0.4 if i % 2 == 0 else -0.4) for i in range(n)]
        sp = [50.0] * n
        bundle = _confident(
            make_bundle(
                {"pv": pv, "sp": sp, "pv_range": [100.0] * n},
                metric_code="oscillation_rate",
            )
        )
        result = OscillationRateCalculator().calculate(bundle)
        details = result.details or {}
        assert details.get("is_oscillating") is False, f"逐点交替噪声被判为振荡：details={details}"

    def test_accuracy_zero_deviation_is_full_score(self) -> None:
        """零偏差（PV 恒等于 SP）→ 准确率 100。"""
        n = 120
        bundle = _confident(
            make_bundle(
                {"pv": [50.0] * n, "sp": [50.0] * n, "pv_range": [100.0] * n},
                metric_code="accuracy_rate",
            )
        )
        result = AccuracyRateCalculator().calculate(bundle)
        assert result.value == 100.0, f"零偏差应为 100，实际 {result.value}"


# ---------------------------------------------------------------------------
# 二、已知缺陷台账：xfail(strict) —— 修复后必须翻转并更新期望
# ---------------------------------------------------------------------------


class TestKnownAlgorithmDefects:
    """已登记缺陷的数值证据（strict xfail）。"""

    @pytest.mark.xfail(
        strict=True,
        reason="G20：_clamp 以 max(low, min(high, v)) 实现，Python 语义下 "
        "min(100, nan) == 100 且 max(0, 100) == 100，故 NaN 被静默放大为上界；"
        "accuracy/stability 等『越高越好』的指标会把 NaN 报成满分。"
        "S3 修复后本用例应转为通过，此时删除 xfail 标记。",
    )
    def test_clamp_nan_must_not_become_score(self) -> None:
        """非有限输入不得被 clamp 成有效分值（应为 NaN 或抛错）。"""
        out = MetricCalculatorBase._clamp(float("nan"))
        assert not math.isfinite(out) or out == 0.0, (
            f"NaN 被 clamp 成 {out}——非有限值不应产生有效分值"
        )

    @pytest.mark.xfail(
        strict=True,
        reason="G18：e_max 取数据驱动 max|E|-mean|E|，使 r=mean|E|/e_max 成为峰均比。"
        "单个大偏差把 max|E| 抬高、进而压低 r，反而提高 A。"
        "S3 修复（e_max 回到量程比例 / 稳健分位）后本用例应转为通过。",
    )
    def test_accuracy_big_excursion_must_not_increase_score(self) -> None:
        """加入一个大的瞬时偏差，不得让准确率升高。"""
        n = 100
        base_sp = [50.5] * n  # 恒定 0.5 余差
        pv = [50.0] * n
        range_sig = [100.0] * n

        without = _confident(
            make_bundle(
                {"pv": pv, "sp": base_sp, "pv_range": range_sig},
                metric_code="accuracy_rate",
            )
        )
        score_without = AccuracyRateCalculator().calculate(without).value

        sp_with_spike = list(base_sp)
        sp_with_spike[n // 2] = 100.0  # 单个 50 的大偏差尖峰
        with_spike = _confident(
            make_bundle(
                {"pv": pv, "sp": sp_with_spike, "pv_range": range_sig},
                metric_code="accuracy_rate",
            )
        )
        score_with = AccuracyRateCalculator().calculate(with_spike).value

        assert score_with is not None and score_without is not None
        assert score_with <= score_without, (
            f"加入大偏差后准确率反而升高：{score_without} -> {score_with}；"
            "准确率必须对『偏差变大』单调不增"
        )
