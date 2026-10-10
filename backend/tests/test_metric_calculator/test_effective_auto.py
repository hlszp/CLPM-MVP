"""有效自控率计算器单元测试（算法说明 §4.2）.

测试用例覆盖：
- 全自动且有效（rate=100）
- OP 饱和导致降级
- 偏差过大导致降级
- 手动模式
- 空数据

注：auto_mode_rate 指标由 AutoModeRateCalculator 单独计算（2026-08-27 起
EffectiveAutoRateCalculator 不再副产出该值，历史副产出口径与自控率指标
掩码不一致）；本文件用 details.auto_duration_s 断言自控时长上下文。

设计依据：算法说明 §4.2；GB/T 44693.2-2024 附录 B.2
"""

from __future__ import annotations

from app.services.metric_calculator.effective_auto import EffectiveAutoRateCalculator

from .conftest import make_bundle


class TestEffectiveAutoRate:
    """EffectiveAutoRateCalculator 测试。"""

    def test_full_effective_auto(self):
        """全自动、OP 未饱和、偏差合理 → R=100。"""
        n = 100
        mode = [1] * n
        op = [50.0] * n
        pv = [50.0] * n
        sp = [50.0] * n
        bundle = make_bundle(
            {"mode": mode, "op": op, "pv": pv, "sp": sp},
            metric_code="effective_auto_rate",
        )
        calc = EffectiveAutoRateCalculator()
        result = calc.calculate(bundle)
        assert result.value == 100.0
        assert result.details["auto_duration_s"] == result.details["total_duration_s"]

    def test_op_saturation_reduces_rate(self):
        """OP 饱和 → 有效自控率 < 自控率。"""
        n = 100
        mode = [1] * n
        op = [100.0] * n  # 饱和
        pv = [50.0] * n
        sp = [50.0] * n
        bundle = make_bundle(
            {"mode": mode, "op": op, "pv": pv, "sp": sp},
            metric_code="effective_auto_rate",
        )
        calc = EffectiveAutoRateCalculator()
        result = calc.calculate(bundle)
        # 自控时长全覆盖（mode 全 Auto），但 OP 全饱和 → effective=0
        assert result.details["auto_duration_s"] == result.details["total_duration_s"]
        assert result.value == 0.0

    def test_large_deviation_reduces_rate(self):
        """偏差超过 e_max → 有效自控率降为 0。"""
        n = 100
        mode = [1] * n
        op = [50.0] * n
        pv = [90.0] * n  # 偏差 40 > e_max=5
        sp = [50.0] * n
        bundle = make_bundle(
            {"mode": mode, "op": op, "pv": pv, "sp": sp},
            metric_code="effective_auto_rate",
        )
        calc = EffectiveAutoRateCalculator()
        result = calc.calculate(bundle)
        assert result.value == 0.0

    def test_manual_mode_zero_rate(self):
        """全手动 → 自控时长=0, effective=0。"""
        n = 100
        mode = [0] * n
        op = [50.0] * n
        pv = [50.0] * n
        sp = [50.0] * n
        bundle = make_bundle(
            {"mode": mode, "op": op, "pv": pv, "sp": sp},
            metric_code="effective_auto_rate",
        )
        calc = EffectiveAutoRateCalculator()
        result = calc.calculate(bundle)
        assert result.value == 0.0
        assert result.details["auto_duration_s"] == 0.0

    def test_empty_data_inconclusive(self):
        """空数据 → INCONCLUSIVE。"""
        bundle = make_bundle({}, metric_code="effective_auto_rate")
        calc = EffectiveAutoRateCalculator()
        result = calc.calculate(bundle)
        assert result.value is None

    def test_partial_auto_partial_saturation(self):
        """50% Auto(有效) + 50% Auto(饱和) → R=50。"""
        n = 100
        mode = [1] * n
        op = [50.0] * 50 + [100.0] * 50
        pv = [50.0] * n
        sp = [50.0] * n
        bundle = make_bundle(
            {"mode": mode, "op": op, "pv": pv, "sp": sp},
            metric_code="effective_auto_rate",
        )
        calc = EffectiveAutoRateCalculator()
        result = calc.calculate(bundle)
        assert result.value == 50.0

    def test_cascade_mode_counts_as_auto(self):
        """Cascade(2) 模式计入自控。"""
        n = 100
        mode = [2] * n
        op = [50.0] * n
        pv = [50.0] * n
        sp = [50.0] * n
        bundle = make_bundle(
            {"mode": mode, "op": op, "pv": pv, "sp": sp},
            metric_code="effective_auto_rate",
        )
        calc = EffectiveAutoRateCalculator()
        result = calc.calculate(bundle)
        assert result.value == 100.0
        assert result.details["auto_duration_s"] == result.details["total_duration_s"]

    def test_mismatched_lengths_no_index_error(self):
        """信号/时间戳长度不齐时按最短数组截断，不抛 IndexError。"""
        n = 100
        mode = [1] * n
        op = [50.0] * n
        pv = [50.0] * n
        sp = [50.0] * n
        bundle = make_bundle(
            {"mode": mode, "op": op, "pv": pv, "sp": sp},
            metric_code="effective_auto_rate",
        )
        # 时间戳截断到 50 点，模拟数组长度不一致
        bundle.data_block.timestamps = bundle.data_block.timestamps[:50]
        calc = EffectiveAutoRateCalculator()
        result = calc.calculate(bundle)
        assert result.value == 100.0
        assert result.details["total_duration_s"] == 50.0


class TestMissingOptionalSignals:
    """缺信号语义（CAL-03 / P1-01：PV/SP 缺失显式不可评，不静默当有效）.

    历史（2026-07-28 线上回归）：上一版对全部 5 个数组取 min 长度，缺 pv/sp
    时循环上界被截断为 0 → total_duration=0 → 全回路 INCONCLUSIVE
    (zero_total_duration)。CAL-03 起（2026-10-10）：pv/sp 是偏差检查必需输入，
    契约 tags 已扩为 ["mode","op","pv","sp"]，bundle 缺 pv/sp → 显式
    INCONCLUSIVE(deviation_inputs_missing)——上界截断回归不复现（原因码
    区分），缺输入不再被静默计为偏差合理。
    """

    def test_missing_pv_sp_explicit_inconclusive(self):
        """缺 pv/sp 信号（mode+op only，旧契约兜底）→ INCONCLUSIVE，原因明确."""
        n = 100
        bundle = make_bundle(
            {"mode": [1] * n, "op": [50.0] * n},
            metric_code="effective_auto_rate",
        )
        result = EffectiveAutoRateCalculator().calculate(bundle)
        assert result.value is None
        assert result.confidence_level == "E"
        assert result.details["reason"] == "deviation_inputs_missing"
        # 2026-07-28 回归不复现：不得误报 zero_total_duration
        assert result.details["reason"] != "zero_total_duration"

    def test_missing_pv_only_explicit_inconclusive(self):
        """缺 pv（sp 在）→ 同样显式 INCONCLUSIVE，缺什么登记什么."""
        n = 100
        bundle = make_bundle(
            {"mode": [1] * n, "op": [50.0] * n, "sp": [50.0] * n},
            metric_code="effective_auto_rate",
        )
        result = EffectiveAutoRateCalculator().calculate(bundle)
        assert result.value is None
        assert result.details["reason"] == "deviation_inputs_missing"
        assert result.details["missing_signals"] == ["pv"]

    def test_missing_op_treated_as_unsaturated(self):
        """缺 op 信号（pv/sp 在）→ 不判饱和，mode 全自动零偏差 → R=100."""
        n = 100
        bundle = make_bundle(
            {"mode": [1] * n, "pv": [50.0] * n, "sp": [50.0] * n},
            metric_code="effective_auto_rate",
        )
        result = EffectiveAutoRateCalculator().calculate(bundle)
        assert result.value == 100.0

    def test_missing_mode_still_insufficient(self):
        """缺 mode 信号（n=0）→ 仍 INCONCLUSIVE(insufficient_data)。"""
        bundle = make_bundle(
            {"op": [50.0] * 100},
            metric_code="effective_auto_rate",
        )
        result = EffectiveAutoRateCalculator().calculate(bundle)
        assert result.value is None
        assert result.details.get("reason") == "insufficient_data"


class TestC03DeviationInputCombinations:
    """C03（04 验收基线 / 台账 CAL-03）：判据组合的明确值或不可评.

    统一 1s 采样（每点时长 1s，n=5 → total=5s），e_max 默认 = 100×0.05 = 5.0：
        modes=[AUTO, MANUAL, AUTO, AUTO, AUTO]
        op   =[50,   50,     100, 50,   50]      # i2 贴上限饱和（ε=0）
        pv   =[50,   50,     50,  60,   50]      # i3 |E|=10 ≥ 5 → 偏差超限
        sp   =[50,   50,     50,  50,   50]
    逐点：i0 有效（1s）；i1 手动；i2 OP 饱和；i3 偏差超限；i4 有效（1s）
    → effective=2s, auto=4s, R = 2/5 × 100 = 40.0
    """

    def test_combined_criteria_exact_value(self):
        """C03：手动/OP 饱和/偏差超限组合 → 明确值 R=40.0（auto=4s）."""
        n = 5
        bundle = make_bundle(
            {
                "mode": [1, 0, 1, 1, 1],
                "op": [50.0, 50.0, 100.0, 50.0, 50.0],
                "pv": [50.0, 50.0, 50.0, 60.0, 50.0],
                "sp": [50.0] * n,
            },
            metric_code="effective_auto_rate",
        )
        result = EffectiveAutoRateCalculator().calculate(bundle)

        assert result.value == 40.0
        assert result.details["auto_duration_s"] == 4.0
        assert result.details["effective_duration_s"] == 2.0
        assert result.details["total_duration_s"] == 5.0
        assert result.details["deviation_check"] == "applied"
        assert result.details["deviation_unknown_points"] == 0

    def test_per_point_none_pv_not_effective(self):
        """C03：采样点 PV 值缺失（None）→ 该点偏差不可判，不计有效（缺输入不当有效）.

        pv=[50, None, 50, 50, 50]（sp 恒 50，全 AUTO 不饱和）：
        i1 偏差不可判 → effective = 4s → R = 4/5 × 100 = 80.0，
        details.deviation_unknown_points = 1（显式计数，不静默）。
        """
        bundle = make_bundle(
            {
                "mode": [1] * 5,
                "op": [50.0] * 5,
                "pv": [50.0, None, 50.0, 50.0, 50.0],
                "sp": [50.0] * 5,
            },
            metric_code="effective_auto_rate",
        )
        result = EffectiveAutoRateCalculator().calculate(bundle)

        assert result.value == 80.0
        assert result.details["effective_duration_s"] == 4.0
        assert result.details["deviation_unknown_points"] == 1

    def test_per_point_none_sp_not_effective(self):
        """C03：采样点 SP 值缺失同样不计有效（对称）。"""
        bundle = make_bundle(
            {
                "mode": [1] * 5,
                "op": [50.0] * 5,
                "pv": [50.0] * 5,
                "sp": [None, 50.0, 50.0, 50.0, 50.0],
            },
            metric_code="effective_auto_rate",
        )
        result = EffectiveAutoRateCalculator().calculate(bundle)

        assert result.value == 80.0
        assert result.details["deviation_unknown_points"] == 1
