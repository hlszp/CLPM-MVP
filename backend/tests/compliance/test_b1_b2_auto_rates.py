"""附录 B.1 自控率 / B.2 有效自控率 公式级验证（任务 G2）.

公式事实来源：算法说明 §4.2（附录 B.2）与 §4.0.3 / mode 常量模块（附录 B.1）：
    B.1: Auto = T_auto / T_total × 100%（MODE ∈ {AUTO, CAS, REMOTE, APC}）
    B.2: R = T_auto_effective / T_total × 100%
         （自控模式 AND OP 未饱和 AND 偏差 < |E|_max）

时长模型（实现 base._point_durations）：零阶保持，第 i 点时长 = ts[i+1]-ts[i]，
末点沿用前一段时长。

手算数据（非均匀时间戳 offsets = [0, 60, 100, 200, 400] 秒）：
    间隔 = [60, 40, 100, 200]，末点沿用 200
    durations = [60, 40, 100, 200, 200]，T_total = 600 s
"""

from __future__ import annotations

import pytest

from app.services.metric_calculator.auto_mode import AutoModeRateCalculator
from app.services.metric_calculator.effective_auto import EffectiveAutoRateCalculator

from .g2_helpers import make_ts_bundle, ts_from_offsets

#: 非均匀时间戳偏移（秒）：间隔 [60, 40, 100, 200]，末点沿用 200
OFFSETS = [0.0, 60.0, 100.0, 200.0, 400.0]
#: durations = [60, 40, 100, 200, 200]，总时长 600 s
TOTAL_DURATION = 600.0


class TestB1AutoModeRate:
    """附录 B.1 自控率：混合 MODE 序列时长统计精确值."""

    def test_mixed_modes_exact_duration_ratio(self):
        """附录 B.1：AUTO/MANUAL/CAS/APC/REMOTE 混合序列，时长占比精确值.

        modes = [AUTO, MANUAL, CAS, APC, REMOTE]
        T_auto = 60(AUTO) + 100(CAS) + 200(APC) + 200(REMOTE) = 560 s
        Auto = 560/600 × 100 = 93.333... → 93.33

        本用例同时固化两条口径：
        - APC（先控，mode=4）计入自控（GB/T 44693.2 附录 B.1 / mode 常量表 is_auto=TRUE）
        - 零阶保持末点沿用前段时长（REMOTE 末点贡献 200 s；若末点时长计 0
          则 Auto=360/400=90，若丢弃末点则 360/400=90，均 ≠ 93.33）
        """
        ts = ts_from_offsets(OFFSETS)
        bundle = make_ts_bundle(
            {"mode": [1, 0, 2, 4, 3]},
            ts,
            metric_code="auto_mode_rate",
            tag_group="MODE_HF",
        )
        result = AutoModeRateCalculator().calculate(bundle)

        assert result.value == pytest.approx(93.33, abs=1e-9)
        assert result.details["auto_duration_s"] == pytest.approx(560.0)
        assert result.details["total_duration_s"] == pytest.approx(TOTAL_DURATION)

    def test_apc_counts_as_auto(self):
        """附录 B.1：APC（mode=4）算自控——与 MANUAL 对照.

        将上例 APC 点改为 MANUAL：
        T_auto = 60(AUTO) + 100(CAS) + 200(REMOTE) = 360 s
        Auto = 360/600 × 100 = 60.0
        与上例差值 33.33 恰好为 APC 点 200 s 的占比，证明 APC 被计入自控集合。
        """
        ts = ts_from_offsets(OFFSETS)
        bundle = make_ts_bundle(
            {"mode": [1, 0, 2, 0, 3]},
            ts,
            metric_code="auto_mode_rate",
            tag_group="MODE_HF",
        )
        result = AutoModeRateCalculator().calculate(bundle)

        assert result.value == pytest.approx(60.0, abs=1e-9)
        assert result.details["auto_duration_s"] == pytest.approx(360.0)

    def test_last_point_zero_order_hold_duration(self):
        """附录 B.1：零阶保持末点时长处理——末点沿用前一段时长.

        单 AUTO 末点 + 前段全 MANUAL：
        offsets=[0, 100, 300] → durations=[100, 200, 200]，total=500
        modes=[MANUAL, MANUAL, AUTO] → T_auto=200（末点沿用前段 200 s）
        Auto = 200/500 × 100 = 40.0
        """
        ts = ts_from_offsets([0.0, 100.0, 300.0])
        bundle = make_ts_bundle(
            {"mode": [0, 0, 1]},
            ts,
            metric_code="auto_mode_rate",
            tag_group="MODE_HF",
        )
        result = AutoModeRateCalculator().calculate(bundle)

        assert result.value == pytest.approx(40.0, abs=1e-9)
        assert result.details["total_duration_s"] == pytest.approx(500.0)


class TestB2EffectiveAutoRate:
    """附录 B.2 有效自控率：自控 AND OP 未饱和 AND 偏差合理."""

    def test_saturation_excludes_segments(self):
        """附录 B.2：OP 饱和段不计入有效自控（ε=2 默认容差）.

        modes 全 AUTO；op=[50, 99, 50, 1.5, 50]：
        - i=1：op=99 ≥ 100-2=98 → 高饱和，剔除 40 s
        - i=3：op=1.5 ≤ 0+2 → 低饱和，剔除 200 s
        T_effective = 60 + 100 + 200 = 360 s → R = 360/600 × 100 = 60.0
        自控时长全覆盖（auto_duration_s = total_duration_s = 600）
        """
        ts = ts_from_offsets(OFFSETS)
        bundle = make_ts_bundle(
            {"mode": [1, 1, 1, 1, 1], "op": [50.0, 99.0, 50.0, 1.5, 50.0]},
            ts,
            metric_code="effective_auto_rate",
            tag_group="MODE_HF",
        )
        result = EffectiveAutoRateCalculator().calculate(bundle)

        assert result.value == pytest.approx(60.0, abs=1e-9)
        assert result.details["auto_duration_s"] == pytest.approx(600.0)
        assert result.details["effective_duration_s"] == pytest.approx(360.0)

    def test_deviation_beyond_e_max_excludes_segments(self):
        """附录 B.2：|E| ≥ |E|_max（默认量程 5%=5.0）的段不计入有效自控.

        modes 全 AUTO，op 全部未饱和；i=2 偏差 |60-50|=10 ≥ 5.0 → 剔除 100 s
        T_effective = 60 + 40 + 200 + 200 = 500 s → R = 500/600 × 100 = 83.33
        """
        ts = ts_from_offsets(OFFSETS)
        bundle = make_ts_bundle(
            {
                "mode": [1, 1, 1, 1, 1],
                "op": [50.0] * 5,
                "pv": [50.0, 50.0, 60.0, 50.0, 50.0],
                "sp": [50.0] * 5,
            },
            ts,
            metric_code="effective_auto_rate",
            tag_group="MODE_HF",
        )
        result = EffectiveAutoRateCalculator().calculate(bundle)

        assert result.value == pytest.approx(83.33, abs=1e-9)
        assert result.details["effective_duration_s"] == pytest.approx(500.0)

    def test_deviation_boundary_strict_less_than(self):
        """附录 B.2：偏差边界为严格小于（|E| < |E|_max），等于阈值即剔除.

        i=4（末点，200 s）偏差恰为 5.0 = |E|_max → 不计入有效自控
        T_effective = 60 + 40 + 100 + 200 = 400 s → R = 400/600 × 100 = 66.67
        """
        ts = ts_from_offsets(OFFSETS)
        bundle = make_ts_bundle(
            {
                "mode": [1, 1, 1, 1, 1],
                "op": [50.0] * 5,
                "pv": [50.0, 50.0, 50.0, 50.0, 55.0],
                "sp": [50.0] * 5,
            },
            ts,
            metric_code="effective_auto_rate",
            tag_group="MODE_HF",
        )
        result = EffectiveAutoRateCalculator().calculate(bundle)

        assert result.value == pytest.approx(66.67, abs=1e-9)
        assert result.details["effective_duration_s"] == pytest.approx(400.0)

    def test_manual_mode_excluded_from_effective(self):
        """附录 B.2：MANUAL 段既不计入自控也不计入有效自控.

        modes=[AUTO, MANUAL, AUTO, AUTO, AUTO]，op 全部未饱和，无偏差：
        T_auto = T_effective = 60 + 100 + 200 + 200 = 560 s
        R = 560/600 × 100 = 93.33；auto_duration_s = 560（自控时长同口径）
        """
        ts = ts_from_offsets(OFFSETS)
        bundle = make_ts_bundle(
            {
                "mode": [1, 0, 1, 1, 1],
                "op": [50.0] * 5,
                "pv": [50.0] * 5,
                "sp": [50.0] * 5,
            },
            ts,
            metric_code="effective_auto_rate",
            tag_group="MODE_HF",
        )
        result = EffectiveAutoRateCalculator().calculate(bundle)

        assert result.value == pytest.approx(93.33, abs=1e-9)
        assert result.details["auto_duration_s"] == pytest.approx(560.0)
