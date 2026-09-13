"""G27#1 复核：延迟搜索的 BIC 跨样本量比较（S3 算法契约与量纲）。

原判"延迟 d 的 BIC 跨样本量比较"——**成立**。

事实依据
--------
BIC = n·ln(σ²) + k·ln(n) 的似然项随 n **线性**缩放，故只有 n 相同才可比。
`_search_delay`（pipeline.py）对每个候选 d 各用 `res.n_samples`，
而 d 越大、回归行越少（n 随 d 递减），因此跨候选的 BIC 不可比。

实测（真值 d=8，1200 点，σ² 基本恒定）："错误候选"的 BIC 随 d **单调变差**
（d=0 时 −6059 → d=12 时 −5991），artifact 贡献约 +68 BIC / 12 步
≈ **每单位 d 5.7**，与合法复杂度罚项 ln(n)≈7.1 **量级相当**——
即延误搜索对大延迟的保守度约翻了一倍；余量小时会误选偏小的 d。

本文件用"与 OP 无关的极小残差白噪声 PV"把该 artifact 放大到可判定：
此时 σ²（≈1e-7）在各候选中几乎不变，故 BIC 差异应仅反映"同一 n 下的噪声"，
而实测随 d 出现近百量级的单调漂移。

状态：**已确认，尚未修复**。曾尝试"公共窗重算残差"的实现，但
`u(t-d-j)` 索引与 `identify_arx` 的回归约定差一拍，验证时真值 d=8 反而落败
（d=7 胜出、d=8 的公共窗 σ² 异常），故**已回退**——延误选择上留一个未验证的
改动比原有偏差更危险。正确实现需先读准 `identify_arx` 的回归索引约定。
"""

from __future__ import annotations

import math

import numpy as np

from app.services.tuning_identification.pipeline import _search_delay


def _bic_spread(d_max: int = 12) -> float:
    """与 OP 无关的极小残差 PV：σ² 各候选近似恒定，BIC 漂移即跨样本量 artifact。"""
    rng = np.random.default_rng(21)
    n = 1200
    u = rng.choice([-1.0, 1.0], size=n)
    y = 1e-4 * rng.normal(0, 1.0, n)  # 极小残差尺度放大 n·ln(σ²) 项
    _, trace = _search_delay(u, y, na=1, nb=1, d_max=d_max)
    vals = [v for _, v in trace if math.isfinite(v)]
    return max(vals) - min(vals)


class TestDelaySearchBic:
    """BIC 必须在同一样本窗上比较。"""

    def test_bic_must_not_drift_with_sample_count(self) -> None:
        """σ² 近似恒定时，BIC 不应随 d 出现大幅单调漂移。"""
        spread = _bic_spread()
        assert spread < 20.0, (
            f"BIC 跨候选漂移 {spread:.1f}，远超同窗比较应有的噪声量级——各候选使用了不同的样本量"
        )

    def test_delay_search_still_recovers_known_delay(self) -> None:
        """回归护栏：具明确滞后结构的数据仍应被正确识别（当前实现可做到）。"""
        rng = np.random.default_rng(7)
        n = 1200
        u = rng.choice([-1.0, 1.0], size=n)
        tau, k_gain, d_true = 25.0, 2.0, 8
        a = math.exp(-1.0 / tau)
        b = k_gain * (1 - a)
        y = np.zeros(n)
        yv = 0.0
        for i in range(n):
            uu = u[i - d_true] if i - d_true >= 0 else 0.0
            yv = a * yv + b * uu
            y[i] = yv + rng.normal(0, 0.01)
        best_d, _ = _search_delay(u, y, na=1, nb=1, d_max=12)
        assert best_d == d_true
