"""Phase 6 GB/T 44693.2 符合性验证 — 附录 F 公式级验证（任务 G3）.

覆盖附录 F 各条目（期望值均手算核实，合成数据 seed 固定）：

- F.1 振荡检测一致性：KPI 侧 metric_calculator/oscillation.py 与诊断侧
  diagnosis_engine._detect_oscillation_iae 同一信号判定一致（Phase 1 已统一
  为同一套零交叉 + IAE 相似率静态方法）；零交叉数与平均周期恢复精度 ±10%。
- F.2 粘滞椭圆法：已知 b/a 椭圆散点 St 精度 ±20%；圆团散点 INCONCLUSIVE；
  大滞后无粘滞回路（FOPDT θ=30s）θ 补偿后不误报 SEVERE（G1 固化为国标用例）；
  已知 θ 粘滑注入（死区跳变）补偿后 St 落手算带；非振荡平稳回路 no_limit_cycle。
- F.3 饱和率：数值 MODE（StandardMode AUTO=1/CAS=2/REMOTE=3/APC=4 + MANUAL=0）
  下饱和诊断可检出（Phase 1 P0-2 修复固化）；归一化 OP 边界 ε=2% 计时长精确值；
  APC 计入自控。
- F.4 ARMA 稳态时间：已知 τ 一阶合成阶跃偏差（τ=30s/60s，dt=1s），
  理论稳态时间 = τ·ln(20)（Green 函数 5% 阈值），误差带 ±30%；
  采样缺口/非均匀采样行为不抛异常、结果可解释。
- F.5 输出行程：已知 OP 锯齿，手算 Σ|ΔOP|/(T·op_range) 精确值；
  6 位精度不被抹零。

Phase 6 基线实测摘要（seed=42, 2026-07-28，全部通过，无 xfail）：
- F.1：P=60 正弦零交叉 59（期望 60）、周期 59.97s（期望 60），KPI/诊断判定一致
- F.2：噪声椭圆 St=20.29（手算 20.26，偏差 0.15%）；FOPDT θ̂=48s（理论 49s）、
  补偿后 St=1.2（NONE）；粘滑 θ̂=13s、St=5.1（手算带 [4,20]）
- F.3：数值 MODE 序列检出（high_count=80，rate=1.0）；ε 边界 55.00% 精确
- F.4：τ=30 → 90.0s（理论 89.87）；τ=60 → 178.0s（理论 179.66）
- F.5：锯齿 0.0392 精确；小行程 0.001 未被抹零
"""

from __future__ import annotations

import numpy as np
import pytest

from app.services.metric_calculator.oscillation import OscillationRateCalculator
from app.services.metric_calculator.output_trip import OutputTripIndexCalculator
from app.services.metric_calculator.saturation import SaturationRateCalculator
from app.services.metric_calculator.stiction import StictionIndexCalculator
from app.tasks.arma import SettlingStatus, compute_settling_time_detailed
from app.tasks.diagnosis_engine import (
    _analyze_saturation,
    _detect_oscillation_iae,
    _is_auto_mode,
)

from .conftest import build_bundle

_SEED = 42


def _rng() -> np.random.Generator:
    return np.random.default_rng(_SEED)


# ---------------------------------------------------------------------------
# F.1 振荡检测一致性（IAE 零交叉相似率法，KPI 侧 vs 诊断侧）
# ---------------------------------------------------------------------------


def _sine_case(period: float, n: int, amp: float, noise: float):
    """构造正弦+噪声 PV（SP 恒定），返回 (pv, sp) 数组."""
    t = np.arange(n, dtype=float)
    rng = _rng()
    pv = 50.0 + amp * np.sin(2 * np.pi * t / period) + noise * rng.standard_normal(n)
    sp = np.full(n, 50.0)
    return pv, sp


class TestF1OscillationConsistency:
    """附录 F.1：振荡检测（IAE 零交叉相似率法）KPI/诊断两侧一致性."""

    def test_f1_sine_detected_both_sides(self):
        """F.1：已知周期正弦振荡，KPI 侧与诊断侧均判定振荡且相似率一致."""
        pv, sp = _sine_case(period=60.0, n=1800, amp=3.0, noise=0.05)
        bundle = build_bundle(
            {"pv": pv.tolist(), "sp": sp.tolist()}, metric_code="oscillation_rate"
        )
        kpi = OscillationRateCalculator().calculate(bundle)
        diag = _detect_oscillation_iae(pv, sp, sample_interval=1.0)

        assert kpi.details["is_oscillating"] is True
        assert diag["detected"] is True
        # 两侧同一算法：相似率 = min(s_a, s_b)，判定阈值同为 0.4
        # （KPI details 中 s_a/s_b 保留 4 位小数，容差取舍入误差 5e-5）
        assert diag["similarity"] == pytest.approx(
            min(kpi.details["s_a"], kpi.details["s_b"]), abs=5e-5
        )
        assert kpi.value == pytest.approx(diag["similarity"] * 100.0, abs=0.01)

    @pytest.mark.parametrize(
        ("period", "n", "expected_crossings"),
        [(60.0, 1800, 60), (120.0, 1800, 30)],
    )
    def test_f1_zero_crossings_and_period_recovery(
        self, period: float, n: int, expected_crossings: int
    ):
        """F.1：零交叉数 ≈ 2·N/P（±10%），平均周期恢复精度 ±10%，两侧一致."""
        pv, sp = _sine_case(period=period, n=n, amp=3.0, noise=0.05)
        bundle = build_bundle(
            {"pv": pv.tolist(), "sp": sp.tolist()}, metric_code="oscillation_rate"
        )
        kpi = OscillationRateCalculator().calculate(bundle)
        diag = _detect_oscillation_iae(pv, sp, sample_interval=1.0)

        tol_cross = expected_crossings * 0.1
        assert abs(kpi.details["zero_crossings"] - expected_crossings) <= tol_cross
        assert diag["zero_crossing_count"] == kpi.details["zero_crossings"]

        assert abs(kpi.details["oscillation_period"] - period) <= period * 0.1
        assert abs(diag["mean_period"] - period) <= period * 0.1
        # dt=1s 时诊断侧 mean_period（秒）与 KPI 侧 period（采样点）数值相等
        assert diag["mean_period"] == pytest.approx(kpi.details["oscillation_period"], abs=0.01)

    def test_f1_monotonic_ramp_not_oscillating(self):
        """F.1 对照：单调斜坡偏差无零交叉，两侧一致判定非振荡，KPI 值为 0."""
        n = 600
        t = np.arange(n, dtype=float)
        pv = 50.0 + 0.01 * t
        sp = np.full(n, 50.0)
        bundle = build_bundle(
            {"pv": pv.tolist(), "sp": sp.tolist()}, metric_code="oscillation_rate"
        )
        kpi = OscillationRateCalculator().calculate(bundle)
        diag = _detect_oscillation_iae(pv, sp, sample_interval=1.0)

        assert kpi.value == 0.0
        assert kpi.details["is_oscillating"] is False
        assert kpi.details["zero_crossings"] == 0
        assert diag["detected"] is False
        assert diag["zero_crossing_count"] == 0


# ---------------------------------------------------------------------------
# F.2 粘滞椭圆法（St = b/a × 100%，PCA 拟合 + θ 补偿 + 极限环门控）
# ---------------------------------------------------------------------------


class TestF2StictionEllipse:
    """附录 F.2：粘滞系数椭圆拟合法（含 G1 θ 补偿与振荡门控增强）."""

    def test_f2_ellipse_known_axis_ratio(self):
        """F.2：已知 b/a 的椭圆散点，St 恢复精度 ±20%.

        手算：pv=sin(t)（var=0.5），op=pv+0.3ε（var=0.5+0.09=0.59，cov=0.5）。
        协方差特征值 λ± = 0.545 ± √(0.045²+0.25) = 1.0470 / 0.0430，
        b/a = √(λmin/λmax) = 0.20261 → St = 20.26。
        容差带 ±20% = [16.21, 24.31]。
        噪声置于 OP 侧：极限环门控只看 PV（纯净正弦必过门控），
        互相关峰在 lag=0（corr=1/√(1+2·0.09)≈0.92），不触发 θ 补偿。
        """
        n = 2000
        t = np.arange(n, dtype=float)
        rng = _rng()
        pv = np.sin(2 * np.pi * t / 100.0)
        op = pv + 0.3 * rng.standard_normal(n)
        bundle = build_bundle({"pv": pv.tolist(), "op": op.tolist()}, metric_code="stiction_index")
        res = StictionIndexCalculator().calculate(bundle)

        expected_st = 20.26
        assert res.value == pytest.approx(expected_st, rel=0.2)
        assert res.details["stiction_level"] == "MODERATE"
        assert res.details["fitting_score"] >= 0.5
        assert res.details["theta_compensated"] is False

    def test_f2_round_scatter_inconclusive(self):
        """F.2：圆团散点（PV 极限环正弦 + OP 独立噪声）R²≈0 → INCONCLUSIVE.

        圆团 b/a≈1 若强行出值会把 St 误报到 ~100（SEVERE）；
        R² < 0.5 门控应拦截，reason=low_correlation。
        """
        n = 2000
        t = np.arange(n, dtype=float)
        rng = _rng()
        pv = np.sin(2 * np.pi * t / 100.0)
        op = rng.standard_normal(n)
        bundle = build_bundle({"pv": pv.tolist(), "op": op.tolist()}, metric_code="stiction_index")
        res = StictionIndexCalculator().calculate(bundle)

        assert res.value is None
        assert res.confidence_level == "E"
        assert res.details["reason"] == "low_correlation"

    def test_f2_large_lag_no_stiction_not_severe(self):
        """F.2（G1 固化用例）：FOPDT 大滞后无粘滞回路，θ 补偿后不误报 SEVERE.

        手算：θ=30s，τ=20s，P=300s（ω=0.02094），一阶相位滞后
        atan(ωτ)=0.397 rad → 互相关峰值滞后 θ̂ ≈ θ + atan(ωτ)/ω ≈ 48.96s。
        未补偿相位 ≈ 49/300×360° ≈ 58.8°，椭圆被拉成近圆团
        （R²≈cos²58.8°≈0.27）；补偿后残差宽度很小，St 应远低于 SEVERE(≥30)。
        """
        n = 1800
        t = np.arange(n, dtype=float)
        theta, tau = 30, 20.0
        op = 50.0 + 30.0 * np.sin(2 * np.pi * t / 300.0)
        pv = np.zeros(n)
        pv[0] = 50.0
        for i in range(1, n):
            op_d = op[i - 1 - theta] if i - 1 - theta >= 0 else 50.0
            pv[i] = pv[i - 1] + (op_d - pv[i - 1]) / tau
        bundle = build_bundle({"pv": pv.tolist(), "op": op.tolist()}, metric_code="stiction_index")
        res = StictionIndexCalculator().calculate(bundle)

        assert res.details["theta_compensated"] is True
        # θ̂ 理论值 ≈ 49s，容差 ±30%
        assert 34.0 <= res.details["theta_hat_seconds"] <= 64.0
        assert res.value is not None
        assert res.value < 30.0
        assert res.details["stiction_level"] != "SEVERE"

    def test_f2_stick_slip_known_theta_compensated(self):
        """F.2：已知 θ 的粘滑注入（死区跳变），补偿后 St 落手算带内.

        注入：阀位在指令偏差越过粘带 S=4 时跳变（stick-slip），纯滞后 θ=10s，
        OP 幅值 A=20，P=200s。手算带：粘带相对宽度 S/(2A)×100 = 10%，
        椭圆法 b/a 对粘带宽度存在几何衰减（经验系数 0.4~2.0），
        手算带 [4, 20]；实测 St=5.1 落带内。θ̂ 应接近注入值 10s（粘滑相位
        畸变允许 ±10s）。
        """
        n = 1200
        t = np.arange(n, dtype=float)
        stick_band, theta = 4.0, 10
        op_cmd = 50.0 + 20.0 * np.sin(2 * np.pi * t / 200.0)
        valve = np.zeros(n)
        valve[0] = 50.0
        for i in range(1, n):
            if abs(op_cmd[i] - valve[i - 1]) >= stick_band:
                valve[i] = op_cmd[i]
            else:
                valve[i] = valve[i - 1]
        pv = np.array([valve[i - theta] if i - theta >= 0 else 50.0 for i in range(n)])
        bundle = build_bundle(
            {"pv": pv.tolist(), "op": op_cmd.tolist()}, metric_code="stiction_index"
        )
        res = StictionIndexCalculator().calculate(bundle)

        assert res.details["theta_compensated"] is True
        assert 0.0 <= res.details["theta_hat_seconds"] <= 20.0
        assert res.value is not None
        assert 4.0 <= res.value <= 20.0

    def test_f2_stationary_loop_no_limit_cycle(self):
        """F.2：非振荡平稳回路（PV≈恒定+微小噪声）→ INCONCLUSIVE(no_limit_cycle).

        粘滞椭圆只在极限环振荡时有物理意义（Kano/Choudhury 前提）；
        平稳回路高频伪穿越平均半周期 < 8 采样点，门控应拒绝出值。
        """
        n = 1200
        rng = _rng()
        pv = 50.0 + 0.01 * rng.standard_normal(n)
        op = 50.0 + 0.01 * rng.standard_normal(n)
        bundle = build_bundle({"pv": pv.tolist(), "op": op.tolist()}, metric_code="stiction_index")
        res = StictionIndexCalculator().calculate(bundle)

        assert res.value is None
        assert res.confidence_level == "E"
        assert res.details["reason"] == "no_limit_cycle"


# ---------------------------------------------------------------------------
# F.3 饱和率（Sa = T_saturated / T_total × 100%，仅自控模式，ε=2%）
# ---------------------------------------------------------------------------


class TestF3Saturation:
    """附录 F.3：饱和率（诊断侧数值 MODE 检出 + KPI 侧 ε 边界精确计时长）."""

    def test_f3_numeric_mode_saturation_detected(self):
        """F.3：数值 MODE 序列（AUTO=1/CAS=2/REMOTE=3/APC=4）下饱和诊断可检出.

        Phase 1 P0-2 修复固化：TDengine 数值 MODE 曾被 `"AUTO" in str()` 误判，
        饱和诊断永久失效。手算：80 点自控（OP=99.5 ≥ 98 全饱和）+ 20 点 MANUAL
        （不计入）→ high_count=80，saturation_rate=1.0 > 0.2 → detected。
        """
        op = np.full(100, 99.5)
        mode = np.array([1, 2, 3, 4] * 20 + [0] * 20)
        result = _analyze_saturation(op, mode)

        assert result["detected"] is True
        assert result["high_count"] == 80
        assert result["low_count"] == 0
        assert result["saturation_rate"] == pytest.approx(1.0)

    @pytest.mark.parametrize(
        ("mode_val", "expected"),
        [
            (1, True),  # AUTO
            (2, True),  # CAS
            (3, True),  # REMOTE
            (4, True),  # APC 计入自控
            (0, False),  # MANUAL
            (2.0, True),  # 浮点整数值
            ("1", True),  # 数值字符串
            ("AUTO", True),
            ("CAS", True),
            ("MANUAL", False),
            (True, False),  # bool 不是自控模式
        ],
    )
    def test_f3_is_auto_mode_numeric(self, mode_val, expected: bool):
        """F.3：_is_auto_mode 数值/字符串 MODE 判定（APC=4 计入自控）."""
        assert _is_auto_mode(mode_val) is expected

    def test_f3_kpi_epsilon_boundary_exact_durations(self):
        """F.3：归一化 OP 边界 ε=2%，KPI 侧计时长精确值.

        手算（100 点 × 1s，全 AUTO，op_low=0/op_high=100/ε=2）：
            20×OP=1.5（≤2 低限饱和）+ 10×OP=2.0（边界计入）→ sat_low=30s
            15×OP=98.0（≥98 边界计入）+ 10×OP=99 → sat_high=25s
            10×OP=2.1 + 15×OP=97.9 + 20×OP=50 → 不饱和
            total=100s → Sa = 55/100 × 100% = 55.00%，type=BOTH。
        """
        op = (
            [1.5] * 20
            + [2.0] * 10
            + [2.1] * 10
            + [98.0] * 15
            + [97.9] * 15
            + [99.0] * 10
            + [50.0] * 20
        )
        mode = [1] * 100
        bundle = build_bundle({"op": op, "mode": mode}, metric_code="saturation_rate")
        res = SaturationRateCalculator().calculate(bundle)

        assert res.value == 55.0
        assert res.details["sat_low_duration_s"] == 30.0
        assert res.details["sat_high_duration_s"] == 25.0
        assert res.details["auto_duration_s"] == 100.0
        assert res.details["saturation_type"] == "BOTH"

    def test_f3_kpi_manual_excluded(self):
        """F.3：MANUAL 模式点既不计入分子也不计入分母.

        手算：50 点 AUTO@OP=99（饱和）+ 50 点 MANUAL@OP=0（剔除）
        → total=50s，sat_high=50s → Sa=100.00%。
        """
        op = [99.0] * 50 + [0.0] * 50
        mode = [1] * 50 + [0] * 50
        bundle = build_bundle({"op": op, "mode": mode}, metric_code="saturation_rate")
        res = SaturationRateCalculator().calculate(bundle)

        assert res.value == 100.0
        assert res.details["auto_duration_s"] == 50.0
        assert res.details["saturation_type"] == "HIGH"

    def test_f3_kpi_all_manual_inconclusive(self):
        """F.3：全 MANUAL 时自控时长为 0 → INCONCLUSIVE(zero_auto_duration)."""
        bundle = build_bundle(
            {"op": [99.0] * 100, "mode": [0] * 100}, metric_code="saturation_rate"
        )
        res = SaturationRateCalculator().calculate(bundle)

        assert res.value is None
        assert res.details["reason"] == "zero_auto_duration"

    def test_f3_kpi_apc_counted_as_auto(self):
        """F.3：APC=4 计入自控（KPI 侧），全 APC 饱和 → Sa=100.00%."""
        bundle = build_bundle(
            {"op": [99.0] * 100, "mode": [4] * 100}, metric_code="saturation_rate"
        )
        res = SaturationRateCalculator().calculate(bundle)

        assert res.value == 100.0


# ---------------------------------------------------------------------------
# F.4 ARMA 稳态时间（AR 辨识 + Green 函数 5% 衰减）
# ---------------------------------------------------------------------------


class TestF4ArmaSettlingTime:
    """附录 F.4：ARMA 模型 + Green 函数稳态时间."""

    @pytest.mark.parametrize(
        ("tau", "n"),
        [(30.0, 600), (60.0, 900)],
    )
    def test_f4_first_order_step_response(self, tau: float, n: int):
        """F.4：已知 τ 一阶合成阶跃偏差（dt=1s），稳态时间辨识误差 ±30%.

        手算：一阶系统偏差 e(t)=A·e^(-t/τ) 的 AR 辨识给出极点 ρ=e^(-1/τ)，
        Green 函数 G(k)=ρ^k；5% 阈值稳态时间 = τ·ln(20)。
        τ=30s → 89.87s（实测 90.0）；τ=60s → 179.66s（实测 178.0）。
        """
        t = np.arange(n, dtype=float)
        signal = 10.0 * np.exp(-t / tau)
        result = compute_settling_time_detailed(signal, sample_interval_sec=1.0)

        expected = tau * np.log(20)
        assert result.status == SettlingStatus.SETTLED
        assert result.value == pytest.approx(expected, rel=0.3)

    def test_f4_sampling_gap_no_exception(self):
        """F.4：采样缺口（前向填充平台段）不抛异常，结果可解释.

        在 τ=30s 衰减曲线第 100 点处插入 100 点平台（模拟缺口 ffill），
        平台段不改变衰减极点结构，稳态时间应仍在理论值 89.87s 的
        ±30% 带内（实测 90.0s），状态 SETTLED。
        """
        t = np.arange(600, dtype=float)
        signal = 10.0 * np.exp(-t / 30.0)
        signal_gap = np.concatenate([signal[:100], np.full(100, signal[100]), signal[100:]])
        result = compute_settling_time_detailed(signal_gap, sample_interval_sec=1.0)

        assert result.status == SettlingStatus.SETTLED
        assert result.value == pytest.approx(30.0 * np.log(20), rel=0.3)

    def test_f4_nonuniform_sampling_equivalent_dt(self):
        """F.4：非均匀/稀疏采样按实际 dt 换算，不抛异常且秒级结果一致.

        τ=30s 衰减曲线按 dt=2s 稀疏采样（等效非均匀采样的均匀极限），
        sample_interval_sec=2 时稳态时间（秒）应与 dt=1s 一致（实测 90.0s）。
        """
        t = np.arange(300, dtype=float) * 2.0
        signal = 10.0 * np.exp(-t / 30.0)
        result = compute_settling_time_detailed(signal, sample_interval_sec=2.0)

        assert result.status == SettlingStatus.SETTLED
        assert result.value == pytest.approx(30.0 * np.log(20), rel=0.3)

    def test_f4_short_data_identification_failed(self):
        """F.4：数据点不足（<30）→ IDENTIFICATION_FAILED，value=None."""
        signal = 10.0 * np.exp(-np.arange(20, dtype=float) / 30.0)
        result = compute_settling_time_detailed(signal, sample_interval_sec=1.0)

        assert result.status == SettlingStatus.IDENTIFICATION_FAILED
        assert result.value is None

    def test_f4_constant_signal_already_stable(self):
        """F.4：恒定信号已处稳态 → ALREADY_STABLE，value=0.0."""
        result = compute_settling_time_detailed(np.full(600, 50.0), sample_interval_sec=1.0)

        assert result.status == SettlingStatus.ALREADY_STABLE
        assert result.value == 0.0


# ---------------------------------------------------------------------------
# F.5 输出行程指数（Trip = Σ|ΔOP| / (T·op_range)，单位行程/秒）
# ---------------------------------------------------------------------------


class TestF5OutputTripIndex:
    """附录 F.5：输出值行程指数量纲与精度."""

    def test_f5_sawtooth_exact_value(self):
        """F.5：已知 OP 锯齿，手算 Σ|ΔOP|/(T·op_range) 精确值.

        手算：op = 2·(i mod 50)，i=0..100，dt=1s → T=100s，op_range=100。
        每段斜坡 49 步 × 2 = 98，回退 |Δ|=98；共 2 斜坡 + 2 回退：
        Σ|ΔOP| = 98×4 = 392 → Trip = 392/(100×100) = 0.0392（行程/秒），
        0.01 ≤ Trip < 0.1 → NORMAL。
        """
        op = [2.0 * (i % 50) for i in range(101)]
        bundle = build_bundle({"op": op}, metric_code="output_trip_index")
        res = OutputTripIndexCalculator().calculate(bundle)

        assert res.value == pytest.approx(0.0392, abs=1e-6)
        assert res.details["total_trip"] == 392.0
        assert res.details["total_duration_s"] == 100.0
        assert res.details["op_range"] == 100.0
        assert res.details["trip_level"] == "NORMAL"

    def test_f5_small_trip_not_zeroed(self):
        """F.5：小行程值 6 位精度不被抹零（默认 2 位会变成 0.00）.

        手算：OP 每步 +0.1，101 点 → Σ|ΔOP|=10，T=100s，op_range=100
        → Trip = 10/10000 = 0.001（< 0.01 → INACTIVE）。若按默认 2 位
        精度输出 0.00 则阈值分级失效，此处固化 precision=6 行为。
        """
        op = [0.1 * i for i in range(101)]
        bundle = build_bundle({"op": op}, metric_code="output_trip_index")
        res = OutputTripIndexCalculator().calculate(bundle)

        assert res.value == pytest.approx(0.001, abs=1e-9)
        assert res.value != 0.0
        assert res.details["trip_level"] == "INACTIVE"

    def test_f5_zero_trip(self):
        """F.5：OP 恒定 → Trip=0.0（INACTIVE），量纲链路不报错."""
        bundle = build_bundle({"op": [50.0] * 101}, metric_code="output_trip_index")
        res = OutputTripIndexCalculator().calculate(bundle)

        assert res.value == 0.0
        assert res.details["trip_level"] == "INACTIVE"
