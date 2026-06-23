"""S3-C5: NaN/Inf 输入测试.

验证各算法处理异常输入（NaN、Inf、全零、极端参数）时的健壮性：
- 不产生未捕获异常
- 返回合理结果或明确的错误信息
- 不导致进程崩溃
"""

from __future__ import annotations

import math

import numpy as np

from app.services.tuning_algorithms import (
    PIDParams,
    identify_fopdt,
    identify_sopdt,
    simulate_closed_loop,
    tune_lambda,
)

# ---------------------------------------------------------------------------
# identify_fopdt NaN/Inf 输入测试
# ---------------------------------------------------------------------------


class TestIdentifyFopdtNanInf:
    """identify_fopdt 处理 NaN/Inf 输入的健壮性测试。"""

    def test_fopdt_with_nan_in_pv(self) -> None:
        """PV 数据含 NaN 时不应崩溃，应返回合理结果或兜底值。"""
        # 正常数据中混入 NaN
        pv_values = [float(i) for i in range(50)]
        pv_values[10] = float("nan")
        pv_values[20] = float("nan")
        timestamps = [float(i) for i in range(50)]

        # 不应抛出未捕获异常
        try:
            result = identify_fopdt(pv_values, timestamps, 10.0, method="COMBINED")
            # 应返回字典结构（即使 K/tau/theta 可能为 None 或异常值）
            assert isinstance(result, dict)
            assert "K" in result
            assert "fitting_score" in result
        except (ValueError, RuntimeError) as exc:
            # 如果抛出异常，应为明确的错误信息（非未捕获异常）
            assert str(exc), "异常应有明确的错误信息"

    def test_fopdt_with_nan_in_timestamps(self) -> None:
        """时间戳含 NaN 时不应崩溃。"""
        pv_values = [float(i) for i in range(50)]
        timestamps = [float(i) for i in range(50)]
        timestamps[5] = float("nan")

        try:
            result = identify_fopdt(pv_values, timestamps, 10.0)
            assert isinstance(result, dict)
        except (ValueError, RuntimeError) as exc:
            assert str(exc), "异常应有明确的错误信息"

    def test_fopdt_with_inf_in_pv(self) -> None:
        """PV 数据含 Inf 时不应崩溃。"""
        pv_values = [float(i) for i in range(50)]
        pv_values[10] = float("inf")
        pv_values[20] = float("-inf")
        timestamps = [float(i) for i in range(50)]

        try:
            result = identify_fopdt(pv_values, timestamps, 10.0)
            assert isinstance(result, dict)
        except (ValueError, RuntimeError) as exc:
            assert str(exc), "异常应有明确的错误信息"

    def test_fopdt_all_nan(self) -> None:
        """全 NaN 数据应安全返回兜底值。"""
        pv_values = [float("nan")] * 50
        timestamps = [float(i) for i in range(50)]

        try:
            result = identify_fopdt(pv_values, timestamps, 10.0)
            assert isinstance(result, dict)
        except (ValueError, RuntimeError) as exc:
            assert str(exc), "异常应有明确的错误信息"

    def test_fopdt_all_inf(self) -> None:
        """全 Inf 数据应安全返回兜底值。"""
        pv_values = [float("inf")] * 50
        timestamps = [float(i) for i in range(50)]

        try:
            result = identify_fopdt(pv_values, timestamps, 10.0)
            assert isinstance(result, dict)
        except (ValueError, RuntimeError) as exc:
            assert str(exc), "异常应有明确的错误信息"


# ---------------------------------------------------------------------------
# identify_sopdt NaN/Inf 输入测试
# ---------------------------------------------------------------------------


class TestIdentifySopdtNanInf:
    """identify_sopdt 处理 NaN/Inf 输入的健壮性测试。"""

    def test_sopdt_with_nan_in_pv(self) -> None:
        """PV 数据含 NaN 时不应崩溃。"""
        pv_values = [float(i) for i in range(50)]
        pv_values[10] = float("nan")
        timestamps = [float(i) for i in range(50)]

        try:
            result = identify_sopdt(pv_values, timestamps, 10.0)
            assert isinstance(result, dict)
            assert "K" in result
        except (ValueError, RuntimeError) as exc:
            assert str(exc), "异常应有明确的错误信息"

    def test_sopdt_with_inf_in_pv(self) -> None:
        """PV 数据含 Inf 时不应崩溃。"""
        pv_values = [float(i) for i in range(50)]
        pv_values[10] = float("inf")
        timestamps = [float(i) for i in range(50)]

        try:
            result = identify_sopdt(pv_values, timestamps, 10.0)
            assert isinstance(result, dict)
        except (ValueError, RuntimeError) as exc:
            assert str(exc), "异常应有明确的错误信息"

    def test_sopdt_all_nan(self) -> None:
        """全 NaN 数据应安全返回兜底值。"""
        pv_values = [float("nan")] * 50
        timestamps = [float(i) for i in range(50)]

        try:
            result = identify_sopdt(pv_values, timestamps, 10.0)
            assert isinstance(result, dict)
        except (ValueError, RuntimeError) as exc:
            assert str(exc), "异常应有明确的错误信息"


# ---------------------------------------------------------------------------
# _detect_oscillation_fft 全零/异常数据测试
# ---------------------------------------------------------------------------


class TestDetectOscillationFftEdgeCases:
    """_detect_oscillation_fft 处理全零/异常数据的健壮性测试。"""

    def test_fft_all_zeros(self) -> None:
        """全零数据应安全返回（不检测到振荡）。"""
        from app.tasks.diagnosis_engine import _detect_oscillation_fft

        pv_values = np.zeros(100, dtype=float)
        result = _detect_oscillation_fft(pv_values, sample_interval=1.0)

        assert isinstance(result, dict)
        # 全零数据无振荡
        assert result["detected"] is False
        assert result["confidence"] == 0.0

    def test_fft_all_nan(self) -> None:
        """全 NaN 数据应安全返回兜底值（不崩溃）。"""
        from app.tasks.diagnosis_engine import _detect_oscillation_fft

        pv_values = np.full(100, float("nan"))
        # 不应抛出未捕获异常
        try:
            result = _detect_oscillation_fft(pv_values, sample_interval=1.0)
            assert isinstance(result, dict)
        except (ValueError, RuntimeError) as exc:
            assert str(exc), "异常应有明确的错误信息"

    def test_fft_all_inf(self) -> None:
        """全 Inf 数据应安全返回兜底值。"""
        from app.tasks.diagnosis_engine import _detect_oscillation_fft

        pv_values = np.full(100, float("inf"))
        try:
            result = _detect_oscillation_fft(pv_values, sample_interval=1.0)
            assert isinstance(result, dict)
        except (ValueError, RuntimeError) as exc:
            assert str(exc), "异常应有明确的错误信息"

    def test_fft_constant_value(self) -> None:
        """恒定值数据应安全返回（不检测到振荡）。"""
        from app.tasks.diagnosis_engine import _detect_oscillation_fft

        pv_values = np.full(100, 50.0, dtype=float)
        result = _detect_oscillation_fft(pv_values, sample_interval=1.0)

        assert isinstance(result, dict)
        assert result["detected"] is False

    def test_fft_with_nan_mixed(self) -> None:
        """含 NaN 的混合数据应安全处理。"""
        from app.tasks.diagnosis_engine import _detect_oscillation_fft

        pv_values = np.array([50.0 + i * 0.1 for i in range(100)], dtype=float)
        pv_values[10] = float("nan")
        pv_values[50] = float("nan")

        try:
            result = _detect_oscillation_fft(pv_values, sample_interval=1.0)
            assert isinstance(result, dict)
        except (ValueError, RuntimeError) as exc:
            assert str(exc), "异常应有明确的错误信息"

    def test_fft_zero_sample_interval(self) -> None:
        """采样间隔为零应安全处理（使用兜底值）。"""
        from app.tasks.diagnosis_engine import _detect_oscillation_fft

        t = np.linspace(0, 10 * np.pi, 200)
        pv_values = 50.0 + 10.0 * np.sin(t)

        # sample_interval=0 应使用兜底值，不崩溃
        result = _detect_oscillation_fft(pv_values, sample_interval=0.0)
        assert isinstance(result, dict)

    def test_fft_negative_sample_interval(self) -> None:
        """负采样间隔应安全处理。"""
        from app.tasks.diagnosis_engine import _detect_oscillation_fft

        t = np.linspace(0, 10 * np.pi, 200)
        pv_values = 50.0 + 10.0 * np.sin(t)

        result = _detect_oscillation_fft(pv_values, sample_interval=-1.0)
        assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# simulate_closed_loop 极端参数测试
# ---------------------------------------------------------------------------


class TestSimulateClosedLoopExtremeParams:
    """simulate_closed_loop 处理极端参数的健壮性测试。"""

    def test_simulate_zero_k(self) -> None:
        """K=0 应安全仿真（兜底处理）。"""
        model_params = {"K": 0.0, "tau": 30.0, "theta": 5.0}
        current_pid = PIDParams(kp=0.5, ti=20.0, td=0.0)
        recommended_pid = PIDParams(kp=2.0, ti=15.0, td=1.0)

        result = simulate_closed_loop(
            model_type="FOPDT",
            model_params=model_params,
            current_pid=current_pid,
            recommended_pid=recommended_pid,
            sim_duration=100.0,
        )

        assert isinstance(result, dict)
        assert len(result["timestamps"]) > 0
        assert len(result["currentResponse"]["pv"]) == len(result["timestamps"])

    def test_simulate_zero_tau(self) -> None:
        """tau=0 应安全仿真（兜底为 1.0）。"""
        model_params = {"K": 1.0, "tau": 0.0, "theta": 5.0}
        current_pid = PIDParams(kp=0.5, ti=20.0, td=0.0)
        recommended_pid = PIDParams(kp=2.0, ti=15.0, td=1.0)

        result = simulate_closed_loop(
            model_type="FOPDT",
            model_params=model_params,
            current_pid=current_pid,
            recommended_pid=recommended_pid,
            sim_duration=100.0,
        )

        assert isinstance(result, dict)
        assert len(result["timestamps"]) > 0

    def test_simulate_negative_tau(self) -> None:
        """负 tau 应安全仿真（兜底处理）。"""
        model_params = {"K": 1.0, "tau": -10.0, "theta": 5.0}
        current_pid = PIDParams(kp=0.5, ti=20.0, td=0.0)
        recommended_pid = PIDParams(kp=2.0, ti=15.0, td=1.0)

        result = simulate_closed_loop(
            model_type="FOPDT",
            model_params=model_params,
            current_pid=current_pid,
            recommended_pid=recommended_pid,
            sim_duration=100.0,
        )

        assert isinstance(result, dict)
        assert len(result["timestamps"]) > 0

    def test_simulate_zero_k_zero_tau(self) -> None:
        """K=0 且 tau=0 应安全仿真。"""
        model_params = {"K": 0.0, "tau": 0.0, "theta": 5.0}
        current_pid = PIDParams(kp=0.5, ti=20.0, td=0.0)
        recommended_pid = PIDParams(kp=2.0, ti=15.0, td=1.0)

        result = simulate_closed_loop(
            model_type="FOPDT",
            model_params=model_params,
            current_pid=current_pid,
            recommended_pid=recommended_pid,
            sim_duration=100.0,
        )

        assert isinstance(result, dict)
        assert len(result["timestamps"]) > 0

    def test_simulate_extreme_k(self) -> None:
        """极大 K 应安全仿真（OP 限幅生效）。"""
        model_params = {"K": 1e6, "tau": 30.0, "theta": 5.0}
        current_pid = PIDParams(kp=0.5, ti=20.0, td=0.0)
        recommended_pid = PIDParams(kp=2.0, ti=15.0, td=1.0)

        result = simulate_closed_loop(
            model_type="FOPDT",
            model_params=model_params,
            current_pid=current_pid,
            recommended_pid=recommended_pid,
            sim_duration=50.0,
        )

        assert isinstance(result, dict)
        # OP 应被限幅在 [-100, 100]
        op_values = result["currentResponse"]["op"]
        assert all(-100.0 <= op <= 100.0 for op in op_values)

    def test_simulate_nan_in_model_params(self) -> None:
        """模型参数含 NaN 应安全处理。"""
        model_params = {"K": float("nan"), "tau": 30.0, "theta": 5.0}
        current_pid = PIDParams(kp=0.5, ti=20.0, td=0.0)
        recommended_pid = PIDParams(kp=2.0, ti=15.0, td=1.0)

        try:
            result = simulate_closed_loop(
                model_type="FOPDT",
                model_params=model_params,
                current_pid=current_pid,
                recommended_pid=recommended_pid,
                sim_duration=50.0,
            )
            assert isinstance(result, dict)
        except (ValueError, RuntimeError) as exc:
            assert str(exc), "异常应有明确的错误信息"

    def test_simulate_inf_in_model_params(self) -> None:
        """模型参数含 Inf 应安全处理。"""
        model_params = {"K": float("inf"), "tau": 30.0, "theta": 5.0}
        current_pid = PIDParams(kp=0.5, ti=20.0, td=0.0)
        recommended_pid = PIDParams(kp=2.0, ti=15.0, td=1.0)

        try:
            result = simulate_closed_loop(
                model_type="FOPDT",
                model_params=model_params,
                current_pid=current_pid,
                recommended_pid=recommended_pid,
                sim_duration=50.0,
            )
            assert isinstance(result, dict)
        except (ValueError, RuntimeError) as exc:
            assert str(exc), "异常应有明确的错误信息"


# ---------------------------------------------------------------------------
# tune_lambda 极端参数测试
# ---------------------------------------------------------------------------


class TestTuneLambdaExtremeParams:
    """tune_lambda 处理极端参数的健壮性测试。"""

    def test_tune_lambda_zero_tau(self) -> None:
        """tau=0 应有兜底处理（不除零）。"""
        pid = tune_lambda(K=1.0, tau=0.0, theta=5.0, lambda_ratio=1.0)
        assert isinstance(pid, PIDParams)
        assert math.isfinite(pid.kp), "kp 应为有限值"
        assert math.isfinite(pid.ti), "ti 应为有限值"

    def test_tune_lambda_zero_k(self) -> None:
        """K=0 应有兜底处理。"""
        pid = tune_lambda(K=0.0, tau=30.0, theta=5.0, lambda_ratio=1.0)
        assert isinstance(pid, PIDParams)
        assert math.isfinite(pid.kp), "kp 应为有限值"

    def test_tune_lambda_zero_tau_zero_k(self) -> None:
        """tau=0 且 K=0 应有兜底处理。"""
        pid = tune_lambda(K=0.0, tau=0.0, theta=5.0, lambda_ratio=1.0)
        assert isinstance(pid, PIDParams)
        assert math.isfinite(pid.kp), "kp 应为有限值"

    def test_tune_lambda_negative_tau(self) -> None:
        """负 tau 应有兜底处理。"""
        pid = tune_lambda(K=1.0, tau=-10.0, theta=5.0, lambda_ratio=1.0)
        assert isinstance(pid, PIDParams)
        assert math.isfinite(pid.kp), "kp 应为有限值"

    def test_tune_lambda_nan_k(self) -> None:
        """K=NaN 应安全处理（不崩溃）。"""
        try:
            pid = tune_lambda(K=float("nan"), tau=30.0, theta=5.0, lambda_ratio=1.0)
            assert isinstance(pid, PIDParams)
        except (ValueError, RuntimeError) as exc:
            assert str(exc), "异常应有明确的错误信息"

    def test_tune_lambda_inf_k(self) -> None:
        """K=Inf 应安全处理。"""
        try:
            pid = tune_lambda(K=float("inf"), tau=30.0, theta=5.0, lambda_ratio=1.0)
            assert isinstance(pid, PIDParams)
        except (ValueError, RuntimeError) as exc:
            assert str(exc), "异常应有明确的错误信息"

    def test_tune_lambda_zero_theta(self) -> None:
        """theta=0 应安全处理。"""
        pid = tune_lambda(K=1.0, tau=30.0, theta=0.0, lambda_ratio=1.0)
        assert isinstance(pid, PIDParams)
        assert math.isfinite(pid.kp), "kp 应为有限值"

    def test_tune_lambda_negative_lambda_ratio(self) -> None:
        """负 lambda_ratio 应有兜底处理。"""
        pid = tune_lambda(K=1.0, tau=30.0, theta=5.0, lambda_ratio=-1.0)
        assert isinstance(pid, PIDParams)
        assert math.isfinite(pid.kp), "kp 应为有限值"


# ---------------------------------------------------------------------------
# 综合健壮性测试
# ---------------------------------------------------------------------------


class TestAlgorithmRobustnessSummary:
    """算法健壮性综合测试。"""

    def test_all_algorithms_handle_empty_input(self) -> None:
        """所有算法对空输入应安全返回。"""
        # identify_fopdt 空输入
        result = identify_fopdt([], [], 10.0)
        assert result["K"] is None

        # identify_sopdt 空输入
        result = identify_sopdt([], [], 10.0)
        assert result["K"] is None

    def test_all_algorithms_handle_single_point(self) -> None:
        """所有算法对单点输入应安全返回。"""
        result = identify_fopdt([50.0], [0.0], 10.0)
        assert result["K"] is None

        result = identify_sopdt([50.0], [0.0], 10.0)
        assert result["K"] is None

    def test_all_tuning_algorithms_handle_extreme_params(self) -> None:
        """所有整定算法对极端参数应安全返回有限值。"""
        from app.services.tuning_algorithms import (
            tune_cohen_coon,
            tune_imc,
            tune_simc,
            tune_zn,
        )

        extreme_cases = [
            (0.0, 0.0, 0.0),  # 全零
            (-1.0, -10.0, -5.0),  # 全负
            (1e6, 1e6, 1e6),  # 极大值
            (1e-6, 1e-6, 1e-6),  # 极小值
        ]

        for K, tau, theta in extreme_cases:
            for tune_fn in [tune_imc, tune_lambda, tune_simc, tune_zn, tune_cohen_coon]:
                pid = tune_fn(K, tau, theta)
                assert isinstance(pid, PIDParams), (
                    f"{tune_fn.__name__} 对极端参数 ({K}, {tau}, {theta}) 未返回 PIDParams"
                )
                # kp 应为有限值（允许 NaN/Inf 被兜底处理为有限值）
                assert math.isfinite(pid.kp), (
                    f"{tune_fn.__name__} 对 ({K}, {tau}, {theta}) 返回非有限 kp={pid.kp}"
                )
