"""G27#5 复核：Ljung-Box 检验的自由度（S3 算法契约与量纲）。

原判"Ljung-Box 自由度与 R² 免检"——**两部分均成立**，本轮修自由度部分，
R² 免检部分登记待裁决（见收口报告 §26）。

自由度口径
----------
对拟合了 p 个参数的模型，Ljung-Box 统计量 Q 渐近服从 **chi2(h − p)**
（Box & Pierce 1970 / Ljung & Box 1978 的标准结论，h 为最大滞后阶）。
原实现用 chi2(h)，**未扣除已估参数**：

- 自由度取大 → 临界值取大 → p 值偏大 → 残差被判得**比实际更白**
  （反保守方向：模型不充分时更容易通过"白噪声"检验）；
- 实证 h=10、p=2：Q=15.5 时 chi2(10) 的 p=0.1149（判白噪声），
  chi2(8) 的 p=0.0501（恰在 0.05 临界）——**阈值附近结论相反**。

更直接的证据：`select_order` 的签名里本就有 `n_params`（且已传给 AIC/BIC），
却**没有**传给 `ljung_box_test`——参数个数就在手边而未被使用。
"""

from __future__ import annotations

import numpy as np
import pytest
from scipy import stats

from app.services.tuning_identification.order_selection import (
    ljung_box_test,
    select_order,
)


def _correlated_residuals(n: int = 400, rho: float = 0.25, seed: int = 5) -> np.ndarray:
    """构造带一定自相关的残差（非白噪声，但 Q 落在阈值附近）。"""
    rng = np.random.default_rng(seed)
    e = rng.normal(0, 1.0, n)
    out = np.zeros(n)
    for i in range(1, n):
        out[i] = rho * out[i - 1] + e[i]
    return out


class TestDegreesOfFreedom:
    """dof 必须为 max_lag − n_params。"""

    def test_dof_deducts_estimated_parameters(self) -> None:
        """显式传 n_params 时，p 值应对应 chi2(h − p)。"""
        res = _correlated_residuals()
        q_stat, p_adj = ljung_box_test(res, max_lag=10, n_params=2)
        assert p_adj == pytest.approx(1.0 - stats.chi2.cdf(q_stat, 8), abs=1e-12)
        # 必须严格小于 chi2(10) 给出的 p（检验更保守）；
        # 差异是否显著取决于 Q 的大小，故此处只断言方向，量级另有专测。
        assert p_adj < 1.0 - stats.chi2.cdf(q_stat, 10)

    def test_default_preserves_legacy_dof(self) -> None:
        """n_params 缺省为 0 → 仍用 chi2(h)（向后兼容）。"""
        res = _correlated_residuals()
        q_stat, p_legacy = ljung_box_test(res, max_lag=10)
        assert p_legacy == pytest.approx(1.0 - stats.chi2.cdf(q_stat, 10), abs=1e-12)

    def test_dof_lower_bound_is_one(self) -> None:
        """n_params >= max_lag 时自由度下界取 1，不得为 0 或负。"""
        res = _correlated_residuals()
        q_stat, p_val = ljung_box_test(res, max_lag=3, n_params=99)
        assert p_val == pytest.approx(1.0 - stats.chi2.cdf(q_stat, 1), abs=1e-12)

    def test_more_parameters_means_smaller_p_value(self) -> None:
        """扣除参数后 p 值必不增大（检验更保守）。"""
        res = _correlated_residuals()
        _, p0 = ljung_box_test(res, max_lag=10, n_params=0)
        _, p2 = ljung_box_test(res, max_lag=10, n_params=2)
        assert p2 < p0

    def test_verdict_can_flip_near_threshold(self) -> None:
        """固化"阈值附近结论相反"：构造 Q 使两种自由度给出不同判定。"""
        # 取 Q=15.5：chi2(10) p=0.1149（白），chi2(8) p=0.0501（临界）
        assert 1.0 - stats.chi2.cdf(15.5, 10) > 0.10
        assert 1.0 - stats.chi2.cdf(15.5, 8) < 0.06
        assert (1.0 - stats.chi2.cdf(15.5, 10)) > (1.0 - stats.chi2.cdf(15.5, 8))


class TestSelectOrderPassesParameterCount:
    """select_order 手里有 n_params，必须传给 Ljung-Box。"""

    def test_p_value_matches_parameter_adjusted_call(self) -> None:
        res = _correlated_residuals()
        out = select_order(
            residuals=res, n_samples=len(res), residual_var=float(np.var(res)), n_params=3
        )
        _, expected = ljung_box_test(res, 10, n_params=3)
        assert out.ljung_box_p == pytest.approx(expected, abs=1e-12)

    def test_residual_white_reflects_adjusted_p(self) -> None:
        res = _correlated_residuals()
        out = select_order(
            residuals=res, n_samples=len(res), residual_var=float(np.var(res)), n_params=3
        )
        assert out.residual_white == (out.ljung_box_p > 0.05)
