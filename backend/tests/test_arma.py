"""ARMA 稳态时间计算测试 — 对齐 GB/T 44693.2-2024 附录 F.4。"""

import numpy as np

from app.tasks.arma import (
    compute_green_function,
    compute_ideal_settling_time,
    compute_settling_time,
    fit_ar_model,
)


class TestFitArModel:
    """AR(p) 模型辨识测试。"""

    def test_pure_ar2_signal(self):
        """AR(2) 信号辨识：x(t) = -0.5·x(t-1) + 0.3·x(t-2) + e(t)

        标准形式 x(t) + a₁·x(t-1) + a₂·x(t-2) = e(t)
        所以 a₁=0.5, a₂=-0.3（生成系数取反）
        """
        np.random.seed(42)
        n = 500
        signal = np.zeros(n)
        for t in range(2, n):
            signal[t] = -0.5 * signal[t - 1] + 0.3 * signal[t - 2] + np.random.randn() * 0.1

        coeffs = fit_ar_model(signal, order=2)
        assert abs(coeffs[0].item() - 0.5) < 0.15
        assert abs(coeffs[1].item() - (-0.3)) < 0.15

    def test_ar1_signal(self):
        """AR(1) 信号辨识：x(t) = -0.3·x(t-1) + e(t)

        标准形式 x(t) + a₁·x(t-1) = e(t)，所以 a₁=0.3
        """
        np.random.seed(42)
        n = 500
        signal = np.zeros(n)
        for t in range(1, n):
            signal[t] = -0.3 * signal[t - 1] + np.random.randn() * 0.1

        coeffs = fit_ar_model(signal, order=1)
        assert abs(coeffs[0].item() - 0.3) < 0.15

    def test_constant_signal(self):
        """恒定信号辨识 → 系数接近 0。"""
        signal = np.ones(100) * 5.0
        coeffs = fit_ar_model(signal, order=3)
        assert np.allclose(coeffs, 0, atol=1e-6)

    def test_insufficient_data_reduces_order(self):
        """数据不足时自动降低阶数。"""
        signal = np.random.randn(10)
        coeffs = fit_ar_model(signal, order=5)
        assert len(coeffs) == 5  # 返回长度仍为 order


class TestGreenFunction:
    """Green 函数计算测试。"""

    def test_ar1_exponential_decay(self):
        """AR(1): a₁ = -0.8 → G(k) = 0.8^k。"""
        ar_coeffs = np.array([-0.8])
        g = compute_green_function(ar_coeffs, length=50)
        assert abs(g[0] - 1.0) < 1e-10
        for k in range(1, 20):
            assert abs(g[k] - 0.8 ** k) < 1e-10

    def test_green_function_decays_to_zero(self):
        """稳定系统的 Green 函数衰减到 0。"""
        ar_coeffs = np.array([-0.5, 0.3])
        g = compute_green_function(ar_coeffs, length=200)
        assert abs(g[-1]) < 0.01

    def test_g0_always_one(self):
        """G(0) 恒等于 1（单位脉冲）。"""
        for coeffs in [np.array([-0.3]), np.array([-0.5, 0.3]), np.array([-0.9, 0.2, -0.1])]:
            g = compute_green_function(coeffs, length=10)
            assert abs(g[0] - 1.0) < 1e-10


class TestSettlingTime:
    """稳态时间计算测试。"""

    def test_fast_response_low_settling_time(self):
        """快速响应信号 → 稳态时间短。"""
        np.random.seed(42)
        n = 500
        signal = np.zeros(n)
        for t in range(1, n):
            signal[t] = -0.3 * signal[t - 1] + np.random.randn() * 0.1
        settling = compute_settling_time(signal, sample_interval_sec=1.0)
        assert settling < 30

    def test_slow_response_high_settling_time(self):
        """慢速响应信号 → 稳态时间长。"""
        np.random.seed(42)
        n = 1000
        signal = np.zeros(n)
        for t in range(1, n):
            signal[t] = -0.95 * signal[t - 1] + np.random.randn() * 0.1
        settling = compute_settling_time(signal, sample_interval_sec=1.0)
        assert settling > 30

    def test_constant_signal_returns_zero(self):
        """恒定信号 → 稳态时间为 0。"""
        signal = np.ones(100) * 5.0
        settling = compute_settling_time(signal)
        assert settling == 0.0

    def test_insufficient_data_returns_zero(self):
        """数据不足 → 返回 0。"""
        signal = np.array([1.0, 2.0, 3.0])
        settling = compute_settling_time(signal)
        assert settling == 0.0

    def test_threshold_2pct_longer_than_5pct(self):
        """2% 阈值的稳态时间应 ≥ 5% 阈值。"""
        np.random.seed(42)
        n = 500
        signal = np.zeros(n)
        for t in range(1, n):
            signal[t] = -0.5 * signal[t - 1] + np.random.randn() * 0.1
        settling_5pct = compute_settling_time(signal, threshold=0.05)
        settling_2pct = compute_settling_time(signal, threshold=0.02)
        assert settling_2pct >= settling_5pct


class TestIdealSettlingTime:
    """理想稳态时间测试。"""

    def test_control_type_mapping(self):
        assert compute_ideal_settling_time(100, "STABLE") == 60.0
        assert compute_ideal_settling_time(100, "SLOW") == 120.0
        assert compute_ideal_settling_time(100, "FAST") == 10.0
        assert compute_ideal_settling_time(100, "LOGIC") == 30.0

    def test_unknown_type_fallback(self):
        assert compute_ideal_settling_time(100, "UNKNOWN") == 60.0
