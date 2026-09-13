"""G27#4 复核：PE 激励门禁"恒真"之疑——**结论不成立**（S3 算法契约与量纲）。

背景
----
G27 原判列"PE 激励门禁恒真"。本文件是对该条的**复核证据**：结论为**误诊**。

复核过程（含一次自我纠正）
--------------------------
check_excitation 对回归矩阵做列 2-范数归一化（V62-P1-011，消除单位影响），
随后用 _COND_NUMBER_OK = 1e4 / _COND_NUMBER_LOW = 1e6 判定。

对两列单位化矩阵，Gram = [[1, ρ], [ρ, 1]]，故 cond = sqrt((1+|ρ|)/(1−|ρ|))；
反解阈值：1e4 ⟺ |ρ| = 1 − 2e-8，1e6 ⟺ |ρ| = 1 − 2e-12。
据此曾**误判**该分支"浮点上不可达、是死代码"——错因是默认两列相关系数
恒约 0.96（两列差一个采样滞后）。**实测否证**：当 PV 近似为 OP 的滞后副本时
ρ→1、cond 可达 1e15。噪声扫描（本文件固化）：

    eps=0      cond=7.07e15  触发
    eps=1e-6   cond=4.54e6   触发
    eps=1e-4   cond=4.73e4   触发（进入低可信度档）
    eps=1e-2   cond=489      不触发（判"激励充分/A"）

故条件数分支**真实可达且在多档输入上生效**，与方向变化、OP 幅度两个分支
共同构成有效门禁；"恒真"不成立。

残余观察（登记，非缺陷）
------------------------
阈值为 1e4/1e6（需 |ρ| 近 1 到 1e-8/1e-12），而工程上常把 |ρ| > 0.999
（cond ≈ 45）即视为共线。当前口径下 |ρ| = 0.9999（cond = 141）仍判"激励充分/A"。
是否应收紧属**阈值标定决策**，且算法说明全文未定义"条件数"，本轮不擅改。
"""

from __future__ import annotations

import numpy as np
import pytest

from app.services.tuning_identification.excitation import (
    _COND_NUMBER_LOW,
    _COND_NUMBER_OK,
    check_excitation,
)


def _two_freq_op(n: int, seed: int = 3) -> np.ndarray:
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    u = np.sin(t * 2 * np.pi / 40.0) + 0.5 * np.sin(t * 2 * np.pi / 13.0)
    return u + rng.normal(0, 0.05, n)


def _first_order_pv(u: np.ndarray) -> np.ndarray:
    return np.convolve(u, np.exp(-np.arange(30) / 8.0), mode="same")


class TestNormalizedConditionNumberMath:
    """固化"阈值对应何等的共线度"这一数学关系。"""

    def test_cond_maps_to_correlation_for_two_unit_columns(self) -> None:
        """两列单位化后 cond = sqrt((1+|ρ|)/(1−|ρ|))。"""
        for rho in (0.9, 0.99, 0.999, 0.9999):
            a = np.array([1.0, 0.0])
            b = np.array([rho, np.sqrt(1 - rho**2)])
            phi = np.column_stack([a, b])
            phi = phi / np.linalg.norm(phi, axis=0)
            assert np.linalg.cond(phi) == pytest.approx(np.sqrt((1 + rho) / (1 - rho)), rel=1e-9)

    def test_thresholds_imply_near_perfect_collinearity(self) -> None:
        """阈值 1e4/1e6 对应 |ρ| 与 1 的距离。"""
        for thr in (_COND_NUMBER_OK, _COND_NUMBER_LOW):
            rho_required = (thr**2 - 1) / (thr**2 + 1)
            assert 1.0 - rho_required < 1e-6
        assert _COND_NUMBER_OK == 1e4
        assert _COND_NUMBER_LOW == 1e6


class TestConditionNumberBranchIsReachable:
    """**复核核心**：条件数分支真实可达（否证"恒真/死代码"）。"""

    def test_exactly_collinear_regressor_is_rejected(self) -> None:
        """PV 为 OP 的精确滞后缩放副本 → 两列成比例 → 拒绝。

        y = roll(3u, 1) 时 col0 = −y[idx−1] = −3u[idx−2]、col1 = u[idx−2]，
        两列精确成比例，cond ~ 1/eps。
        """
        n = 500
        u = _two_freq_op(n)
        res = check_excitation(u, np.roll(3.0 * u, 1), d=2)
        assert res.is_sufficient is False
        assert "条件数过大" in res.verdict
        assert res.condition_number > _COND_NUMBER_LOW

    @pytest.mark.parametrize("eps", [1e-12, 1e-10, 1e-8, 1e-6])
    def test_near_collinear_regressor_still_rejected(self, eps: float) -> None:
        """加微小噪声破坏精确成比例后，条件数分支**仍然**触发。"""
        n = 500
        u = _two_freq_op(n)
        rng = np.random.default_rng(11)
        y = np.roll(3.0 * u, 1) + eps * rng.normal(0, 1.0, n)
        res = check_excitation(u, y, d=2)
        assert res.condition_number > _COND_NUMBER_LOW
        assert res.is_sufficient is False

    def test_medium_collinearity_lands_in_low_confidence_band(self) -> None:
        """eps=1e-4 落在 1e4~1e6 之间 → 标注低可信度 C（仍 sufficient）。"""
        n = 500
        u = _two_freq_op(n)
        rng = np.random.default_rng(11)
        y = np.roll(3.0 * u, 1) + 1e-4 * rng.normal(0, 1.0, n)
        res = check_excitation(u, y, d=2)
        assert _COND_NUMBER_OK < res.condition_number < _COND_NUMBER_LOW
        assert res.is_sufficient is True
        assert res.confidence.value == "C"

    def test_well_excited_case_passes(self) -> None:
        """正常激励：cond 远低于阈值，判"激励充分/A"。"""
        n = 500
        u = _two_freq_op(n)
        res = check_excitation(u, _first_order_pv(u), d=2)
        assert res.is_sufficient is True
        assert res.confidence.value == "A"
        assert res.condition_number < _COND_NUMBER_OK / 100.0


class TestOtherBranchesAlsoWork:
    """门禁的另两个分支同样真实生效。"""

    def test_monotonic_op_is_rejected(self) -> None:
        n = 500
        u = np.linspace(0.0, 100.0, n)
        res = check_excitation(u, _first_order_pv(u), d=2)
        assert res.is_sufficient is False
        assert "方向变化" in res.verdict

    def test_tiny_op_range_is_rejected(self) -> None:
        n = 500
        rng = np.random.default_rng(5)
        u = 50.0 + rng.normal(0, 0.05, n)
        res = check_excitation(u, _first_order_pv(u), d=2)
        assert res.is_sufficient is False
        assert "OP 变化范围过小" in res.verdict

    def test_too_few_points_is_rejected(self) -> None:
        """总点数不足（<10）→ 拒绝（更早的一道守卫）。"""
        u = np.array([1.0, 2.0, 1.5, 2.5])
        res = check_excitation(u, np.array([1.0, 2.0, 1.5, 2.5]), d=5)
        assert res.is_sufficient is False
        assert "数据点不足" in res.verdict

    def test_regression_rows_too_few_is_rejected(self) -> None:
        """点数够但滞后吃掉行数（rows = n − max(d+1,1) < 3）→ 拒绝。"""
        n = 10
        rng = np.random.default_rng(9)
        t = np.arange(n)
        u = np.sin(t * 2 * np.pi / 4.0) + rng.normal(0, 0.05, n)
        y = np.convolve(u, np.exp(-np.arange(3)), mode="same")
        res = check_excitation(u, y, d=8)  # max_lag=9 -> rows=1
        assert res.is_sufficient is False
        assert "回归数据不足" in res.verdict
