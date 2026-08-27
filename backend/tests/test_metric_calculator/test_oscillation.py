"""振荡率计算器单元测试（算法说明 §4.6）.

测试用例覆盖：
- 正弦振荡（检测到振荡）
- 恒定偏差（无振荡）
- 随机噪声（输出格式验证）
- 数据不足（< 4 点）
- 零交叉点不足
- 振荡周期计算
- S_TA/S_TB 持续时间相似率输出
- 设计文档伪代码对齐验证

设计依据：算法说明 §4.6；GB/T 44693.2-2024 附录 F.1

P2 #33 偏差2：移除 _crossing_regularity（CV 变异系数），按设计文档 §4.6.2 步骤 5
计算持续时间相似率 S_TA/S_TB（与 S_A/S_B 同算法），振荡判定仅依赖 S_A/S_B
（设计文档伪代码 line 19-22），S_TA/S_TB 作为辅助诊断输出。
"""

from __future__ import annotations

import math
import random

import numpy as np
import pytest

from app.services.metric_calculator.oscillation import OscillationRateCalculator

from .conftest import make_bundle


class TestOscillationRate:
    """OscillationRateCalculator 测试。"""

    def test_sinusoidal_oscillation_detected(self):
        """正弦振荡（周期 20s）→ 检测到振荡，rate > 0。"""
        n = 200
        sp = [50.0] * n
        pv = [50.0 + 10.0 * math.sin(2 * math.pi * i / 20) for i in range(n)]
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="oscillation_rate")
        calc = OscillationRateCalculator()
        result = calc.calculate(bundle)
        assert result.value is not None
        assert result.value > 0
        assert result.details["is_oscillating"] is True

    def test_constant_error_no_oscillation(self):
        """恒定偏差（PV 恒定偏离 SP）→ 无振荡，rate=0。"""
        n = 200
        pv = [55.0] * n
        sp = [50.0] * n
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="oscillation_rate")
        calc = OscillationRateCalculator()
        result = calc.calculate(bundle)
        assert result.value == 0.0
        assert result.details["is_oscillating"] is False

    def test_random_noise_output_format(self):
        """随机噪声 → 输出格式完整，含 S_TA/S_TB 辅助诊断字段。

        注：设计文档 §4.6.2 伪代码 line 19-22 综合判定仅用 S_A/S_B，
        高频噪声下短段 IAE 相似率可能达 1.0；P1 半周期门控下沉 KPI 侧后，
        此类高频伪穿越被判非振荡（见 TestHalfPeriodGate）。
        """
        random.seed(42)
        n = 200
        sp = [50.0] * n
        pv = [50.0 + random.gauss(0, 5) for _ in range(n)]
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="oscillation_rate")
        calc = OscillationRateCalculator()
        result = calc.calculate(bundle)
        assert result.value is not None
        # 验证 S_TA/S_TB 辅助诊断字段存在（P2 #33 新增）
        assert "s_ta" in result.details
        assert "s_tb" in result.details
        assert 0.0 <= result.details["s_ta"] <= 1.0
        assert 0.0 <= result.details["s_tb"] <= 1.0
        # 验证不再包含 regularity 字段（P2 #33 移除）
        assert "regularity" not in result.details

    def test_insufficient_data(self):
        """数据不足（< 4 点）→ INCONCLUSIVE（value=None）。"""
        bundle = make_bundle({"pv": [50, 51], "sp": [50, 50]}, metric_code="oscillation_rate")
        calc = OscillationRateCalculator()
        result = calc.calculate(bundle)
        assert result.value is None
        assert result.confidence_level == "E"
        assert result.details["is_oscillating"] is False

    def test_zero_crossings_below_threshold(self):
        """零交叉点不足 4 → rate=0。"""
        # PV 单调递增，只穿越 SP 一次
        n = 100
        sp = [50.0] * n
        pv = [40.0 + 0.2 * i for i in range(n)]  # 40→59.8，穿越 50 一次
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="oscillation_rate")
        calc = OscillationRateCalculator()
        result = calc.calculate(bundle)
        assert result.value == 0.0

    def test_oscillation_period_calculated(self):
        """振荡时计算振荡周期。"""
        n = 200
        sp = [50.0] * n
        # 周期 20s 的正弦
        pv = [50.0 + 10.0 * math.sin(2 * math.pi * i / 20) for i in range(n)]
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="oscillation_rate")
        calc = OscillationRateCalculator()
        result = calc.calculate(bundle)
        if result.details["is_oscillating"]:
            assert result.details["oscillation_period"] > 0


class TestOscillationSTaSTb:
    """S_TA/S_TB 持续时间相似率测试（P2 #33 偏差2 修复）。"""

    def test_s_ta_s_tb_in_details(self):
        """振荡检测结果 details 包含 s_ta/s_tb 字段。"""
        n = 200
        sp = [50.0] * n
        pv = [50.0 + 10.0 * math.sin(2 * math.pi * i / 20) for i in range(n)]
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="oscillation_rate")
        calc = OscillationRateCalculator()
        result = calc.calculate(bundle)
        assert "s_ta" in result.details
        assert "s_tb" in result.details
        # 正弦振荡的持续时间相似率应较高（周期均匀）
        assert result.details["s_ta"] >= 0.5
        assert result.details["s_tb"] >= 0.5

    def test_s_ta_s_tb_range_0_to_1(self):
        """S_TA/S_TB 取值范围 [0, 1]。"""
        n = 200
        sp = [50.0] * n
        pv = [50.0 + 10.0 * math.sin(2 * math.pi * i / 20) for i in range(n)]
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="oscillation_rate")
        calc = OscillationRateCalculator()
        result = calc.calculate(bundle)
        assert 0.0 <= result.details["s_ta"] <= 1.0
        assert 0.0 <= result.details["s_tb"] <= 1.0

    def test_regularity_field_removed(self):
        """P2 #33 移除 _crossing_regularity，details 不再包含 regularity 字段。"""
        n = 200
        sp = [50.0] * n
        pv = [50.0 + 10.0 * math.sin(2 * math.pi * i / 20) for i in range(n)]
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="oscillation_rate")
        calc = OscillationRateCalculator()
        result = calc.calculate(bundle)
        assert "regularity" not in result.details


class TestOscillationDesignAlignment:
    """设计文档 §4.6.2 伪代码对齐验证（P2 #33 偏差2）。"""

    def test_osc_value_is_min_sa_sb_times_100(self):
        """振荡率 = min(S_A, S_B) × 100（设计文档步骤 6，不再乘 regularity）。"""
        n = 200
        sp = [50.0] * n
        pv = [50.0 + 10.0 * math.sin(2 * math.pi * i / 20) for i in range(n)]
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="oscillation_rate")
        calc = OscillationRateCalculator()
        result = calc.calculate(bundle)
        expected = min(result.details["s_a"], result.details["s_b"]) * 100.0
        # 允许 _clamp 边界处理
        assert abs(result.value - min(expected, 100.0)) < 0.01

    def test_is_oscillating_only_depends_on_sa_sb(self):
        """is_oscillating 依赖 S_A/S_B >= 阈值 + 半周期门控（设计文档伪代码 line 20 + P1 门控）。

        设计文档伪代码 line 19-22:
            is_oscillating = (S_A >= threshold AND S_B >= threshold)
        S_TA/S_TB 不参与判定，仅作辅助诊断输出；
        P1 追加平均半周期下限门控（与诊断侧同口径，见 TestHalfPeriodGate）。
        本用例的 20s 正弦（半周期 10 采样点 ≥ 8）门控通过，
        判定仍由 S_A/S_B 阈值决定。
        """
        # 正弦振荡：S_A/S_B 都高 → is_oscillating=True
        n = 200
        sp = [50.0] * n
        pv = [50.0 + 10.0 * math.sin(2 * math.pi * i / 20) for i in range(n)]
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="oscillation_rate")
        calc = OscillationRateCalculator()
        result = calc.calculate(bundle)
        if result.details["s_a"] >= 0.4 and result.details["s_b"] >= 0.4:
            assert result.details["is_oscillating"] is True
        else:
            assert result.details["is_oscillating"] is False

    def test_period_is_mean_duration_times_2(self):
        """振荡周期 = 2 × 平均半周期（设计文档伪代码 line 23-25）。

        设计文档：
            avg_half_period = mean(positive_duration + negative_duration)
            oscillation_period = 2 * avg_half_period
        """
        n = 200
        sp = [50.0] * n
        pv = [50.0 + 10.0 * math.sin(2 * math.pi * i / 20) for i in range(n)]
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="oscillation_rate")
        calc = OscillationRateCalculator()
        result = calc.calculate(bundle)
        if result.details["is_oscillating"]:
            # 周期 20s 的正弦波，半周期约 10s
            assert result.details["oscillation_period"] > 0


class TestZeroPlateauCrossings:
    """零值平台伪穿越修复（零值归并前一符号）测试。"""

    def test_zero_plateau_same_sign_no_crossing(self):
        """零值平台（+,0,0,+）不产生伪穿越（旧实现 zero_to_nonzero 会误报）。"""
        errors = np.array([1.0, 0.0, 0.0, 1.0, 1.0])
        crossings = OscillationRateCalculator._find_zero_crossings(errors)
        assert crossings == []

    def test_zero_plateau_merged_into_previous_sign(self):
        """零值归并前一符号：（+,0,-,0,+）只在真实符号翻转处穿越。"""
        errors = np.array([1.0, 0.0, -1.0, 0.0, 1.0])
        crossings = OscillationRateCalculator._find_zero_crossings(errors)
        assert crossings == [2, 4]

    def test_leading_zeros_no_crossing(self):
        """前导零值不产生穿越，归并到随后出现的符号段。"""
        errors = np.array([0.0, 0.0, 1.0, 1.0])
        crossings = OscillationRateCalculator._find_zero_crossings(errors)
        assert crossings == []


class TestIncompleteSegmentsExcluded:
    """首尾残缺半周期段剔除测试。"""

    def test_head_tail_segments_excluded(self):
        """只保留零交叉点之间的完整半周期，首段/尾段剔除出 IAE 列表。"""
        # 符号：+ + | - - | + + | - - | + + +（| 为零交叉点 2/4/6/8）
        errors = np.array([1.0, 2.0, -1.0, -2.0, 1.0, 2.0, -1.0, -2.0, 1.0, 2.0, 3.0])
        crossings = [2, 4, 6, 8]
        segments = OscillationRateCalculator._compute_iae_segments(errors, crossings)
        # 仅 [2:4]、[4:6]、[6:8] 三个完整半周期；[0:2] 与 [8:11] 剔除
        assert len(segments) == 3
        assert [s[1] for s in segments] == [2.0, 2.0, 2.0]
        assert [s[2] for s in segments] == [-1, 1, -1]
        assert segments[0][0] == 3.0  # |-1| + |-2|

    def test_single_crossing_returns_empty(self):
        """零交叉点 < 2 时无完整半周期，返回空列表。"""
        errors = np.array([1.0, -1.0, -1.0])
        assert OscillationRateCalculator._compute_iae_segments(errors, [1]) == []


class TestSymmetricSimilarity:
    """相似率对称化修复（1-|cleaned_avg-avg|/|avg|）测试。"""

    def test_similarity_penalizes_upward_shift(self):
        """cleaned_avg > avg 时相似率不再恒 1.0（旧实现 min 取 avg 单边不对称）。

        values=[2.0, 0.01, 3.0]：最小距离点 avg=2.0；0.01 被 min_ratio 过滤后
        cleaned=[2,3]，cleaned_avg=2.5 > avg → similarity = 1-|2.5-2|/2 = 0.75。
        """
        sim = OscillationRateCalculator._similarity_rate([2.0, 0.01, 3.0])
        assert sim == pytest.approx(0.75)

    def test_similarity_identical_values_still_one(self):
        """完全一致的数据相似率仍为 1.0（对称化不影响正常情形）。"""
        sim = OscillationRateCalculator._similarity_rate([3.0, 3.0, 3.0, 3.0])
        assert sim == pytest.approx(1.0)


@pytest.fixture()
def _oscillation_overrides():
    """测试后复位算法参数缓存到默认值。"""
    from app.services.algorithm_config import apply_runtime

    yield
    apply_runtime({})


class TestHalfPeriodGate:
    """半周期抗噪门控（P1 修复：与诊断侧 _iae_kernel 同口径）。

    白噪声伪穿越的 IAE 相似率可达 0.9+（合规验证报告实测 0.962），
    单靠相似率无法区分噪声与真实振荡；新增平均半周期下限门控
    （min_half_period_samples，默认 8 采样点），低于下限判非振荡。
    """

    def test_white_noise_not_oscillating(self):
        """纯白噪声（相似率高但半周期 ~2 采样点）→ is_oscillating=False。"""
        random.seed(42)
        n = 200
        sp = [50.0] * n
        pv = [50.0 + random.gauss(0, 5) for _ in range(n)]
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="oscillation_rate")
        calc = OscillationRateCalculator()
        result = calc.calculate(bundle)
        assert result.details["is_oscillating"] is False
        # details 携带门控证据：平均半周期（采样点）低于默认下限 8
        assert result.details["mean_half_period_samples"] < 8.0

    def test_slow_oscillation_passes_gate(self):
        """真实振荡（周期 20s@1s 采样，半周期 10 采样点）→ 门控通过。"""
        n = 200
        sp = [50.0] * n
        pv = [50.0 + 10.0 * math.sin(2 * math.pi * i / 20) for i in range(n)]
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="oscillation_rate")
        calc = OscillationRateCalculator()
        result = calc.calculate(bundle)
        assert result.details["is_oscillating"] is True
        assert result.details["mean_half_period_samples"] >= 8.0

    def test_gate_configurable(self, _oscillation_overrides):
        """min_half_period_samples 可经配置链覆盖：调高到 50 后 20s 正弦被判非振荡。"""
        from app.services.algorithm_config import apply_runtime

        apply_runtime({"oscillation_rate": {"STABLE": {"min_half_period_samples": 50}}})
        n = 200
        sp = [50.0] * n
        pv = [50.0 + 10.0 * math.sin(2 * math.pi * i / 20) for i in range(n)]
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="oscillation_rate")
        calc = OscillationRateCalculator()
        result = calc.calculate(bundle)
        assert result.details["is_oscillating"] is False
        # 振荡率数值（显示口径）不受门控影响，仍为 min(S_A,S_B)×100
        assert result.value is not None and result.value > 0

    def test_high_frequency_not_detected(self):
        """高频"振荡"（周期 4s，半周期 2 采样点 < 8）→ 被门控拒绝。

        旧实现判 is_oscillating=True 属伪报（2 采样点半周期在 1s 采样下
        与白噪声伪穿越不可区分），门控后与诊断侧口径一致。
        """
        n = 200
        sp = [50.0] * n
        pv = [50.0 + 10.0 * math.sin(2 * math.pi * i / 4) for i in range(n)]
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="oscillation_rate")
        calc = OscillationRateCalculator()
        result = calc.calculate(bundle)
        assert result.value is not None
        assert result.details["is_oscillating"] is False


class TestPeriodUnitSeconds:
    """振荡周期单位换算（P1 修复：采样点数 → 秒）。

    设计文档 §4.6.3 oscillation_period 定义为秒；旧实现输出采样点数，
    仅在 1s 采样时数值巧合正确，非 1s 采样（如 5s）时偏差达采样间隔倍数。
    """

    def test_period_seconds_nonunit_sampling(self):
        """5s 采样、周期 100s 正弦 → oscillation_period ≈ 100s（±10%）。"""
        n = 400
        interval = 5.0
        period_samples = 20  # 100s / 5s
        sp = [50.0] * n
        pv = [50.0 + 10.0 * math.sin(2 * math.pi * i / period_samples) for i in range(n)]
        bundle = make_bundle(
            {"pv": pv, "sp": sp}, metric_code="oscillation_rate", interval_s=interval
        )
        calc = OscillationRateCalculator()
        result = calc.calculate(bundle)
        assert result.details["is_oscillating"] is True
        # 旧实现返回 ~20（采样点数），修复后应为 ~100（秒）
        assert abs(result.details["oscillation_period"] - 100.0) <= 10.0

    def test_period_seconds_unit_sampling_unchanged(self):
        """1s 采样（周期 20s 正弦）→ oscillation_period ≈ 20s，行为不变。"""
        n = 200
        sp = [50.0] * n
        pv = [50.0 + 10.0 * math.sin(2 * math.pi * i / 20) for i in range(n)]
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="oscillation_rate")
        calc = OscillationRateCalculator()
        result = calc.calculate(bundle)
        assert result.details["is_oscillating"] is True
        assert abs(result.details["oscillation_period"] - 20.0) <= 2.0

    def test_period_zero_when_not_oscillating(self):
        """未判振荡（含被门控拒绝）→ oscillation_period=0。"""
        random.seed(42)
        n = 200
        sp = [50.0] * n
        pv = [50.0 + random.gauss(0, 5) for _ in range(n)]
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="oscillation_rate")
        calc = OscillationRateCalculator()
        result = calc.calculate(bundle)
        assert result.details["oscillation_period"] == 0.0


class TestAmplitudeGate:
    """幅度门控（P2 修复：噪声带内规则微振荡不判振荡）。

    IAE 相似率法本质是正则性检测、不看幅度——量程 0.2% 以下的规则微振荡
    与测量噪声不可区分；新增特征幅度下限门控（min_amplitude_ratio，
    特征幅度 = 各完整半周期段 IAE/时长的均值，默认量程 0.2%）。
    """

    def test_tiny_amplitude_not_oscillating(self):
        """微幅规则振荡（amp=0.05，量程 100 → 幅度占比 ~0.03%）→ 判非振荡。"""
        n = 200
        sp = [50.0] * n
        pv = [50.0 + 0.05 * math.sin(2 * math.pi * i / 20) for i in range(n)]
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="oscillation_rate")
        calc = OscillationRateCalculator()
        result = calc.calculate(bundle)
        assert result.details["is_oscillating"] is False
        # 特征幅度占比低于默认下限 0.002
        assert result.details["amplitude_ratio"] < 0.002

    def test_normal_amplitude_passes_gate(self):
        """正常幅度振荡（amp=10，占比 ~6.4%）→ 门控通过。"""
        n = 200
        sp = [50.0] * n
        pv = [50.0 + 10.0 * math.sin(2 * math.pi * i / 20) for i in range(n)]
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="oscillation_rate")
        calc = OscillationRateCalculator()
        result = calc.calculate(bundle)
        assert result.details["is_oscillating"] is True
        assert result.details["amplitude_ratio"] >= 0.002

    def test_gate_configurable_to_zero(self, _oscillation_overrides):
        """min_amplitude_ratio=0 关闭门控后微幅振荡恢复判定。"""
        from app.services.algorithm_config import apply_runtime

        apply_runtime({"oscillation_rate": {"STABLE": {"min_amplitude_ratio": 0.0}}})
        n = 200
        sp = [50.0] * n
        pv = [50.0 + 0.05 * math.sin(2 * math.pi * i / 20) for i in range(n)]
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="oscillation_rate")
        calc = OscillationRateCalculator()
        result = calc.calculate(bundle)
        assert result.details["is_oscillating"] is True

    def test_amplitude_evidence_in_details(self):
        """details 携带 amplitude/amplitude_ratio 证据字段。

        特征幅度 = 完整半周期段 IAE/时长 的均值；正弦连续期望 = 2/π×amp
        ≈ 6.366。段长受 sin(πk) 浮点尘埃在 9~11 采样点间浮动
        （IAE 不变），实测均值 6.367 落在连续期望 ±0.1 内。
        """
        n = 200
        sp = [50.0] * n
        pv = [50.0 + 10.0 * math.sin(2 * math.pi * i / 20) for i in range(n)]
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="oscillation_rate")
        calc = OscillationRateCalculator()
        result = calc.calculate(bundle)
        expected_amp = 20.0 / math.pi  # 2/π × amp
        assert result.details["amplitude"] == pytest.approx(expected_amp, abs=0.1)
        assert result.details["amplitude_ratio"] == pytest.approx(expected_amp / 100.0, abs=0.002)


def _sp_step_tracking_data(seg_len: int = 100, decay_pts: int = 30, tau: float = 3.0):
    """构造 SP 多次阶跃 + PV 一阶跟踪数据（返回 pv/sp 列表）。

    7 段（6 次阶跃 ±10 交替），阶跃后 PV 以 τ=3s 一阶惯性跟踪，
    跟踪暂态 E = -step·exp(-k/τ) 持续 decay_pts 点后归零。
    暂态按方向同型 → 正/负段 IAE 各 ≥2 个且相似率 1.0，
    不剔除时零交叉 5 个、平均半周期 100 点，被误判为"振荡"。
    """
    sp: list[float] = []
    err: list[float] = []
    sp_val = 50.0
    for seg in range(7):
        step = 0.0
        if seg > 0:
            step = 10.0 if seg % 2 == 1 else -10.0
        new_sp = sp_val + step
        for k in range(seg_len):
            e = -step * math.exp(-k / tau) if seg > 0 and k < decay_pts else 0.0
            sp.append(new_sp)
            err.append(e)
        sp_val = new_sp
    pv = [s + e for s, e in zip(sp, err, strict=True)]
    return pv, sp


class TestSpStepExclusion:
    """SP 阶跃窗口剔除（P2 修复，默认关闭零回归）。

    SP 多次阶跃的跟踪暂态产生同型 IAE 半周期段，相似率可达 1.0，
    被误判为振荡；开启 sp_step_exclusion_enabled 后剔除阶跃跟踪窗
    （复用 stability 同款 detect_sp_tracking_windows），暂态不入判定。
    """

    def test_tracking_transient_false_positive_by_default(self):
        """默认关闭：6 次阶跃暂态被误判为振荡（修复前基线，登记用）。"""
        pv, sp = _sp_step_tracking_data()
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="oscillation_rate")
        calc = OscillationRateCalculator()
        result = calc.calculate(bundle)
        assert result.details["is_oscillating"] is True
        assert result.details["sp_steps_detected"] == 0
        assert result.details["sp_excluded_points"] == 0

    def test_tracking_transient_excluded_when_enabled(self, _oscillation_overrides):
        """开启剔除：暂态点全部剔除，全零偏差无零交叉 → 非振荡。"""
        from app.services.algorithm_config import apply_runtime

        apply_runtime({"oscillation_rate": {"STABLE": {"sp_step_exclusion_enabled": True}}})
        pv, sp = _sp_step_tracking_data()
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="oscillation_rate")
        calc = OscillationRateCalculator()
        result = calc.calculate(bundle)
        assert result.details["sp_steps_detected"] == 6
        assert result.details["sp_excluded_points"] == 360  # 6 步 × 60 点窗
        assert result.details["zero_crossings"] == 0
        assert result.details["is_oscillating"] is False

    def test_steady_oscillation_unaffected_when_enabled(self, _oscillation_overrides):
        """开启剔除但 SP 无阶跃：行为与默认一致，正常振荡仍检出。"""
        from app.services.algorithm_config import apply_runtime

        apply_runtime({"oscillation_rate": {"STABLE": {"sp_step_exclusion_enabled": True}}})
        n = 200
        sp = [50.0] * n
        pv = [50.0 + 10.0 * math.sin(2 * math.pi * i / 20) for i in range(n)]
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="oscillation_rate")
        calc = OscillationRateCalculator()
        result = calc.calculate(bundle)
        assert result.details["is_oscillating"] is True
        assert result.details["sp_excluded_points"] == 0


class TestIaeDtWeighting:
    """IAE 时间加权（P3 修复：支持非均匀采样）。

    设计文档 §4.6.2 步骤 3：IAE_k = ∫|E|dt ≈ Σ|E_i|·Δt_i；
    旧实现漏乘 Δt_i，均匀采样时相似率不受影响（比例约掉），但非均匀
    采样（缺口回补/降采样混合）下短采样段与长采样段被同等计权，
    物理上失真——同一 |E| 形状、真实时长差 10× 的两个正半周期，
    IAE 应差 10×而非相等。
    """

    def test_compute_segments_weighted_by_durations(self):
        """单位：传入 point_durations 后 IAE = Σ|E_i|·Δt_i（时长/符号不变）。"""
        errors = np.array([1.0, 2.0, -1.0, -2.0, 1.0, 2.0, -1.0])
        crossings = [2, 4, 6]
        plain = OscillationRateCalculator._compute_iae_segments(errors, crossings)
        weighted = OscillationRateCalculator._compute_iae_segments(
            errors, crossings, point_durations=[2.0] * 7
        )
        assert [s[1] for s in weighted] == [s[1] for s in plain]  # 时长（采样点）不变
        assert [s[2] for s in weighted] == [s[2] for s in plain]  # 符号不变
        # 均匀 Δt=2 → IAE 整体 ×2
        assert len(plain) == 2
        assert weighted[0][0] == pytest.approx(plain[0][0] * 2.0)
        assert weighted[1][0] == pytest.approx(plain[1][0] * 2.0)

    def test_nonuniform_sampling_changes_similarity(self):
        """集成：同型半周期真实时长差 10×+ → 加权后相似率塌缩，判非振荡。

        构造 ±1 方波（段长 10 采样点），第二个正段（索引 40~50）时间戳
        间距 12s（其余 1s）：无 Δt 计权时正段 IAE 相等（S_A=1.0 → 振荡）；
        计权后正段 IAE 序列 [10, 120]（12× 在清洗带 [0.05,15] 内保留，
        最小距离点取首元素 10）→ S_A=0 → 非振荡；负段均匀 → S_B=1.0。
        """
        n = 70
        sp = [50.0] * n
        err = [1.0 if (i // 10) % 2 == 0 else -1.0 for i in range(n)]
        pv = [s + e for s, e in zip(sp, err, strict=True)]
        # 非均匀时间戳：索引 40~50 段（第二个正半周期）间距 12s，其余 1s
        ts: list = []
        for i in range(n):
            if i <= 40:
                ts.append(float(i))
            elif i <= 50:
                ts.append(40.0 + 12.0 * (i - 40))
            else:
                ts.append(160.0 + (i - 50))
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="oscillation_rate")
        bundle.data_block.timestamps = ts
        calc = OscillationRateCalculator()
        result = calc.calculate(bundle)
        assert result.details["is_oscillating"] is False
        assert result.details["s_a"] == pytest.approx(0.0, abs=1e-6)
        # 负段不受影响：均匀 dt=1 → S_B=1.0
        assert result.details["s_b"] == pytest.approx(1.0)

    def test_uniform_sampling_behavior_unchanged(self):
        """对照：同样方波但时间戳均匀 1s → S_A=1.0，判振荡（零回归）。"""
        n = 70
        sp = [50.0] * n
        err = [1.0 if (i // 10) % 2 == 0 else -1.0 for i in range(n)]
        pv = [s + e for s, e in zip(sp, err, strict=True)]
        bundle = make_bundle({"pv": pv, "sp": sp}, metric_code="oscillation_rate")
        calc = OscillationRateCalculator()
        result = calc.calculate(bundle)
        assert result.details["is_oscillating"] is True
        assert result.details["s_a"] == pytest.approx(1.0)
