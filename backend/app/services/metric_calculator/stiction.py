"""粘滞系数计算器（算法说明 §4.8）.

公式：St = b/a × 100%（简化计算方法，国标附录 F.2 推荐）

其中：
    a：PV-OP 散点椭圆的长轴（主方向）
    b：PV-OP 散点椭圆的短轴（垂直于主方向）

椭圆拟合采用 PCA（主成分分析）：
    对归一化的 (PV, OP) 散点计算协方差矩阵，特征值即为椭圆长短轴的平方。

设计依据：算法说明 §4.8；GB/T 44693.2-2024 附录 F.2

定位：辅助诊断指标，用于检测阀门粘滞故障。
"""

from __future__ import annotations

import logging
import math

import numpy as np

from app.contracts.data_types import MetricDataBundle, MetricResult
from app.services.metric_calculator.base import MetricCalculatorBase

logger = logging.getLogger(__name__)

#: 最少数据点数
MIN_POINTS = 100

#: 椭圆拟合度阈值（低于此值返回 INCONCLUSIVE）
MIN_FITTING_SCORE = 0.5

#: 归一化量程
DEFAULT_PV_RANGE = 100.0
DEFAULT_OP_RANGE = 100.0


class StictionIndexCalculator(MetricCalculatorBase):
    """粘滞系数计算器（算法说明 §4.8）.

    基于 PV-OP 散点图的椭圆拟合，计算椭圆长短轴比值。
    采用 PCA 方法拟合椭圆主轴。
    """

    @property
    def metric_code(self) -> str:
        return "stiction_index"

    def calculate(self, bundle: MetricDataBundle) -> MetricResult:
        """计算粘滞系数.

        Args:
            bundle: 指标数据包（需含 pv/op 信号，mask 为 pv_valid && op_valid）

        Returns:
            MetricResult：value 为粘滞系数 0~100，
            details 中含 stiction_level/fitting_score
        """
        pairs = self._get_masked_pair(bundle, "pv", "op")
        n = len(pairs)

        logger.debug("[粘滞系数] 输入: masked_points=%d", n)

        if n < MIN_POINTS:
            return self._make_inconclusive(
                bundle,
                "insufficient_data",
                {"sample_count": n, "min_required": MIN_POINTS},
            )

        pv_range = self._read_range(bundle, "pv_range", DEFAULT_PV_RANGE)
        op_range = self._read_range(bundle, "op_range", DEFAULT_OP_RANGE)

        # 数据归一化
        pv_vals = np.array([float(p) for p, _ in pairs], dtype=float)
        op_vals = np.array([float(o) for _, o in pairs], dtype=float)

        pv_norm = (pv_vals - np.min(pv_vals)) / (pv_range if pv_range > 0 else 1.0)
        op_norm = (op_vals - np.min(op_vals)) / (op_range if op_range > 0 else 1.0)

        # 椭圆拟合（PCA）；fitting_score 为 OP-PV 线性相关系数平方 R²
        a, b, fitting_score = self._fit_ellipse(pv_norm, op_norm)

        # 有效性门控（算法说明 §4.8.4 步骤 8：R² < 0.5 → INCONCLUSIVE）：
        # 圆团/随机散点 |r|≈0，PCA 椭圆 b/a≈1 会把 St 误报到 ~100（SEVERE），
        # 低相关意味着散点无主导方向，b/a 宽度比不具备粘滞物理含义，不予检出
        if fitting_score < MIN_FITTING_SCORE:
            logger.debug(
                "[粘滞系数] 拟合度 R²=%.4f < %.1f（低相关），INCONCLUSIVE",
                fitting_score,
                MIN_FITTING_SCORE,
            )
            return self._make_inconclusive(
                bundle,
                "low_correlation",
                {
                    "stiction_level": "NONE",
                    "fitting_score": round(fitting_score, 4),
                    "sample_count": n,
                },
            )

        # 粘滞系数 St = b/a × 100（R²≥0.5 隐含方差非零，a>0 必然成立）
        stiction = (b / a) * 100.0
        stiction = self._clamp(stiction)
        level = _determine_level(stiction)

        logger.debug(
            "[粘滞系数] a=%.4f, b=%.4f, St=%.2f%%, level=%s, R2=%.4f",
            a,
            b,
            stiction,
            level,
            fitting_score,
        )

        return self._make_result(
            bundle,
            stiction,
            {
                "stiction_level": level,
                "fitting_score": round(fitting_score, 4),
                "long_axis": round(a, 4),
                "short_axis": round(b, 4),
                "sample_count": n,
            },
        )

    @staticmethod
    def _fit_ellipse(x: np.ndarray, y: np.ndarray) -> tuple[float, float, float]:
        """PCA 椭圆拟合.

        计算散点协方差矩阵的特征值，sqrt(特征值) 即为椭圆半轴长度。
        长轴 a = sqrt(max(λ))，短轴 b = sqrt(min(λ))。
        拟合度 R² 取 OP-PV 线性相关系数的平方（对齐算法说明 §4.8.3
        fitting_score 定义）：R² = r² = cov(x,y)² / (var(x)·var(y))。

        注：旧实现用 λmax/(λmax+λmin) 近似 R²，该比值恒 ≥ 0.5，
        使 MIN_FITTING_SCORE 门控分支不可达；圆团散点（|r|≈0）因此
        被误判 St≈100（SEVERE）。改为 r² 后门控真实生效。

        Returns:
            (a, b, fitting_score) — 长轴/短轴/拟合度 R²
        """
        if len(x) < 2:
            return 0.0, 0.0, 0.0

        # 中心化
        x_c = x - np.mean(x)
        y_c = y - np.mean(y)

        # 协方差矩阵
        cov = np.cov(x_c, y_c)
        if cov.shape != (2, 2):
            return 0.0, 0.0, 0.0

        var_x = float(cov[0, 0])
        var_y = float(cov[1, 1])
        if var_x <= 0 or var_y <= 0:
            # 恒定信号无相关性可言，拟合度 0
            return 0.0, 0.0, 0.0

        # 特征值分解
        eigenvalues = np.linalg.eigvalsh(cov)
        eigenvalues = np.maximum(eigenvalues, 0.0)  # 数值稳定性

        lambda_max = float(np.max(eigenvalues))
        lambda_min = float(np.min(eigenvalues))

        a = np.sqrt(lambda_max)
        b = np.sqrt(lambda_min)

        r = float(cov[0, 1]) / math.sqrt(var_x * var_y)
        fitting = r * r

        return a, b, fitting

    @staticmethod
    def _read_range(bundle: MetricDataBundle, key: str, default: float) -> float:
        """读取量程范围."""
        val = bundle.data_block.signals.get(key)
        if val is None:
            return default
        try:
            v = float(val)
            return v if v > 0 else default
        except (TypeError, ValueError):
            return default


def _determine_level(stiction: float) -> str:
    """判定粘滞等级 NONE/MILD/MODERATE/SEVERE."""
    if stiction < 5.0:
        return "NONE"
    if stiction < 15.0:
        return "MILD"
    if stiction < 30.0:
        return "MODERATE"
    return "SEVERE"


__all__ = ["StictionIndexCalculator"]
