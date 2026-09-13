"""G25：ARXResult.is_stable 的稳定性判据（S3 算法契约与量纲）。

事实来源（离散系统稳定性定义）：
A(z) = 1 + a1·z⁻¹ + ... + an·z⁻ⁿ 的极点即 zⁿ + a1·zⁿ⁻¹ + ... + an = 0 的根；
系统稳定 ⟺ 全部极点严格位于单位圆内（|z| < 1）。
2 阶 Jury 判据的等价形式：|a2| < 1 且 a1 < 1 + a2 且 −a1 < 1 + a2。

缺陷（修复前实测，4 例错 3 例）：
原实现一阶判 a1 < 0、高阶判 all(|a_i| < 1)：
  - 一阶 a1=+0.5 → 极点 −0.5（**稳定**），原判 False；
  - 一阶 a1=−2.0 → 极点 2.0（**不稳定**），原判 True；
  - 二阶 a1=−0.5, a2=−0.9 → 极点 1.2311（**不稳定**），原判 True。
根因：|a_i| < 1 只是稳定的必要非充分条件；且一阶把极点 z = −a1 误判为 a1 本身。

影响面：is_stable 是 ARXResult 的公开 property，但全仓当前仅
tests/test_tuning_identification.py::test_arx_stability_property 消费，
**无生产消费方**——故本次修复零生产行为变化，属拆除哑雷。
"""

from __future__ import annotations

import numpy as np
import pytest

from app.services.tuning_identification.arx import ARXResult


def _res(a_coeffs: list[float]) -> ARXResult:
    return ARXResult(
        a_coeffs=a_coeffs,
        b_coeffs=[0.1] * len(a_coeffs),
        d=1,
        residual_var=0.01,
        n_samples=200,
        r_squared=0.95,
    )


def _poles(a_coeffs: list[float]) -> np.ndarray:
    """极点 = zⁿ + a1·zⁿ⁻¹ + ... + an 的根（与实现同源定义，用于交叉验证）。"""
    return np.roots([1.0, *a_coeffs])


class TestFirstOrder:
    """一阶：极点 z = −a1，稳定 ⟺ |a1| < 1（注意不是 a1 < 0）。"""

    def test_positive_a1_within_unit_is_stable(self) -> None:
        """a1=+0.5 → 极点 −0.5 → 稳定。修复前误判 False。"""
        assert _res([0.5]).is_stable is True

    def test_negative_a1_beyond_unit_is_unstable(self) -> None:
        """a1=−2.0 → 极点 2.0 → 不稳定。修复前误判 True（致命方向）。"""
        assert _res([-2.0]).is_stable is False

    def test_negative_a1_within_unit_is_stable(self) -> None:
        """a1=−0.9 → 极点 0.9 → 稳定（原实现此处恰好判对）。"""
        assert _res([-0.9]).is_stable is True

    def test_boundary_pole_on_unit_circle_is_unstable(self) -> None:
        """极点恰在单位圆上（a1=−1）→ 非严格稳定。"""
        assert _res([-1.0]).is_stable is False


class TestSecondOrderJury:
    """二阶：Jury 判据，而非逐系数取模。"""

    def test_jury_counterexample(self) -> None:
        """a1=−0.5, a2=−0.9：|a1|<1 且 |a2|<1 但极点 1.2311 → 不稳定。

        修复前误判 True——这是 all(|a_i| < 1) 必要非充分的直接反例。
        """
        res = _res([-0.5, -0.9])
        assert np.max(np.abs(_poles([-0.5, -0.9]))) == pytest.approx(1.2311, abs=1e-4)
        assert res.is_stable is False

    def test_stable_second_order(self) -> None:
        """a1=0.2, a2=0.3 → 极点模 0.5477 → 稳定。"""
        assert np.max(np.abs(_poles([0.2, 0.3]))) == pytest.approx(0.5477, abs=1e-4)
        assert _res([0.2, 0.3]).is_stable is True

    def test_jury_conditions_equivalent_to_pole_test(self) -> None:
        """在网格上验证：Jury 三条件 ⟺ 极点模全 <1。

        排除极点模恰为 1 的网格点（如 a1=1.4, a2=0.4 → 极点 −1.0）：此时
        "严格稳定"与"临界稳定"只差浮点误差，两种判据都可能因数值噪声给出
        不同答案，不构成判据分歧。临界情形由 test_boundary_pole_on_unit_circle_is_unstable
        单独覆盖。
        """
        checked = 0
        skipped = 0
        for a1 in (-1.8, -1.0, -0.5, -0.2, 0.0, 0.3, 0.9, 1.4):
            for a2 in (-1.4, -0.9, -0.5, 0.0, 0.4, 0.9, 1.4):
                max_mod = float(np.max(np.abs(_poles([a1, a2]))))
                if abs(max_mod - 1.0) < 1e-6:
                    skipped += 1
                    continue
                jury = (abs(a2) < 1.0) and (a1 < 1.0 + a2) and (-a1 < 1.0 + a2)
                poles_ok = bool(np.all(np.abs(_poles([a1, a2])) < 1.0))
                assert jury == poles_ok, f"Jury 与极点判定不一致: a1={a1}, a2={a2}"
                assert _res([a1, a2]).is_stable == poles_ok
                checked += 1
        assert checked + skipped == 56
        assert checked >= 50  # 绝大多数网格点参与比对
        assert skipped <= 6


class TestStabilityMatchesPoleModulus:
    """性质测试：实现输出必须等于"极点模全 <1"。"""

    def test_random_family(self) -> None:
        rng = np.random.default_rng(20260913)
        for _ in range(300):
            order = int(rng.integers(1, 5))
            a = [float(v) for v in rng.uniform(-1.6, 1.6, order)]
            expected = bool(np.all(np.abs(_poles(a)) < 1.0))
            assert _res(a).is_stable == expected, f"a={a}"

    def test_empty_coefficients_is_stable(self) -> None:
        """A(z)=1：无极点，视为稳定。"""
        assert _res([]).is_stable is True
