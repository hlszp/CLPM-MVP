"""G27#2 复核：离散→连续转换（S3 算法契约与量纲）。

原判两项，复核结论各异：
- "log(abs(p)) 伪装负实极点" —— **成立，已修复**；
- "ZOH 重复计拍" —— **不成立**（一阶 τ/K 精确还原，见 TestExactZohRoundtrip）。

事实来源（离散→连续的正确映射）
--------------------------------
离散极点 p 的连续对应取自**复**对数：s = ln(p)/Ts。
- p > 0 实极点 → s = ln(p)/Ts 为**实**数；
- p < 0 实极点 → ln(p) = ln|p| + jπ → s = (ln|p| + jπ)/Ts 为**复**数（振荡模态）；
- p 为共轭复数 → s 为共轭复数。

SOPDT G(s) = K/((T1·s+1)(T2·s+1)) 只表示**两个实极点**，故后两种情形均不适用。
实现原本已对"离散复极点（disc<0）"设防，却漏了"负实离散极点"这一同类情形，
且先取 abs 再取对数把 jπ 丢掉，使振荡模态被伪装成实极点。

实测（修复前）
--------------
a1=0, a2=-0.25 → 离散极点 ±0.5；真值连续极点为 -0.6931 与
-0.6931 + 3.1416j（Nyquist 频率振荡），而实现输出 T1 = T2 = 1.4427 的
良性过阻尼模型 —— 据此整定会得到与真实动态无关的 PID 参数。
a1=1.2, a2=0.35 → 离散极点 -0.5, -0.7，两级均为复连续极点，
实现仍输出 T1=1.4427, T2=2.8037。
"""

from __future__ import annotations

import math

import pytest

from app.services.tuning_identification.discrete_to_continuous import (
    arx_to_fopdt,
    arx_to_sopdt,
)


class TestNegativeRealPoleIsRejected:
    """负实离散极点 → 复连续极点（振荡模态）→ SOPDT 不适用，必须拒绝。"""

    def test_one_negative_pole(self) -> None:
        """a1=0, a2=-0.25 → 极点 ±0.5，其中 -0.5 映射为复极点。

        修复前：静默返回 T1=T2=1.4427（良性过阻尼），掩盖 Nyquist 振荡。
        """
        with pytest.raises(ValueError, match="负实离散极点"):
            arx_to_sopdt(a1=0.0, a2=-0.25, b1=1.0, d=1, ts=1.0)

    def test_both_poles_negative(self) -> None:
        """a1=1.2, a2=0.35 → 极点 -0.5, -0.7，两级均为复连续极点。"""
        with pytest.raises(ValueError, match="负实离散极点"):
            arx_to_sopdt(a1=1.2, a2=0.35, b1=0.01, d=1, ts=1.0)

    def test_true_continuous_pole_has_imaginary_part(self) -> None:
        """固化"为什么必须拒绝"：p<0 的连续极点为 ln|p| + jπ 而非 ln|p|。"""
        p = -0.5
        s_true = complex(math.log(abs(p)), math.pi)
        assert s_true.real == pytest.approx(math.log(0.5))
        assert s_true.imag == pytest.approx(math.pi, abs=1e-12)
        # 原实现用的 |p| 给出完全不同的（实）极点
        assert math.log(abs(p)) != pytest.approx(s_true.imag, abs=1e-3)


class TestPositiveRealPolesStillConvert:
    """回归护栏：过阻尼（正实极点）系统必须正常转换。"""

    def test_overdamped_sopdt_converts(self) -> None:
        """取离散极点 0.6 与 0.8（正实）→ 应正常得到两个时间常数。"""
        a1 = -(0.6 + 0.8)
        a2 = 0.6 * 0.8
        m = arx_to_sopdt(a1=a1, a2=a2, b1=0.2, d=2, ts=1.0)
        # s_i = ln(p_i) → T_i = -1/s_i
        # _solve_quadratic 返回 (x1, x2)，x1 对应 +sqrt(disc) → 0.8
        assert m.T1 == pytest.approx(-1.0 / math.log(0.8), rel=1e-9)
        assert m.T2 == pytest.approx(-1.0 / math.log(0.6), rel=1e-9)
        assert m.theta == 2.0

    def test_complex_discrete_poles_rejected(self) -> None:
        """既有守卫（P2-012）：离散复极点 → 拒绝。"""
        with pytest.raises(ValueError, match="复共轭极点"):
            arx_to_sopdt(a1=0.4, a2=0.5, b1=0.1, d=1, ts=1.0)

    def test_zero_pole_rejected(self) -> None:
        """p=0 无连续对应（s=-inf → T=0）→ 由 T<=0 检查拒绝。"""
        with pytest.raises(ValueError, match="<= 0"):
            arx_to_sopdt(a1=0.0, a2=0.0, b1=1.0, d=1, ts=1.0)


class TestExactZohRoundtrip:
    """否证 G27#2 的"ZOH 重复计拍"：一阶 τ/K 由 ZOH 精确逆变换还原。"""

    def test_fopdt_tau_and_k_roundtrip_exactly(self) -> None:
        """G(s)=K/(τs+1) 经 ZOH 精确离散为 a1=-exp(-Ts/τ)、b1=K(1+a1)，
        逆变换必须无偏还原 τ 与 K。"""
        tau_true, k_true, ts = 10.0, 2.0, 1.0
        a1 = -math.exp(-ts / tau_true)
        b1 = k_true * (1.0 + a1)
        m = arx_to_fopdt(a1=a1, b1=b1, d=3, ts=ts)
        assert m.tau == pytest.approx(tau_true, rel=1e-12)
        assert m.K == pytest.approx(k_true, rel=1e-12)

    def test_fopdt_theta_is_sample_grid_quantization(self) -> None:
        """θ = d·Ts：纯滞后被量化到采样网格（非 Ts/2 误差）。

        这是与"ZOH 半拍补偿"不同的约定选择；因 τ/K 无偏，θ 取 d·Ts
        不构成本轮所述缺陷。
        """
        m = arx_to_fopdt(a1=-0.5, b1=0.5, d=4, ts=2.0)
        assert m.theta == 8.0

    def test_fopdt_requires_negative_a1(self) -> None:
        """a1>=0 表示非稳定/非一阶实极点 → 拒绝（与 SOPDT 的负实极点守卫同族）。"""
        with pytest.raises(ValueError, match="a1="):
            arx_to_fopdt(a1=0.5, b1=0.1, d=1, ts=1.0)
