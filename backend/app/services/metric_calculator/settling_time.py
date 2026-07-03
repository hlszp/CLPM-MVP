"""稳态时间计算器（算法说明 §4.5）.

基于 ARMA 模型辨识和 Green 函数计算实际稳态时间（秒）。

算法流程：
    1. 提取控制偏差序列 E = PV - SP（去均值）
    2. AR(p) 模型辨识（Yule-Walker 方程）
    3. Green 函数递推（单位脉冲响应）
    4. 找到 |G(k)| 首次持续低于 5% 的时刻
    5. 实际稳态时间 = k × 采样周期

设计依据：算法说明 §4.5；GB/T 44693.2-2024 附录 F.4

定位：辅助诊断指标，为快速率计算提供实际稳态时间。
复用 app.tasks.arma.compute_settling_time（只读引用，不修改）。
"""

from __future__ import annotations

import logging

import numpy as np

from app.contracts.data_types import MetricDataBundle, MetricResult
from app.services.metric_calculator.base import MetricCalculatorBase

logger = logging.getLogger(__name__)

#: 最少数据点数（AR(10) 模型辨识需要足够自由度，设计要求 100 点）
MIN_POINTS = 100

#: 默认采样周期（秒）
DEFAULT_SAMPLE_INTERVAL = 1.0

#: Green 函数衰减阈值（5%）
SETTLING_THRESHOLD = 0.05


class SettlingTimeCalculator(MetricCalculatorBase):
    """稳态时间计算器（算法说明 §4.5）.

    通过 ARMA 模型辨识和 Green 函数计算实际稳态时间。
    复用 app.tasks.arma 模块的 compute_settling_time 函数。
    """

    @property
    def metric_code(self) -> str:
        return "settling_time"

    def calculate(self, bundle: MetricDataBundle) -> MetricResult:
        """计算实际稳态时间.

        Args:
            bundle: 指标数据包（需含 pv/sp 信号）

        Returns:
            MetricResult：value 为实际稳态时间（秒），
            0 表示已处于稳态或辨识失败
        """
        from app.tasks.arma import compute_settling_time

        pairs = self._get_masked_pair(bundle, "pv", "sp")
        n = len(pairs)

        logger.debug("[稳态时间] 输入: masked_points=%d", n)

        if n < MIN_POINTS:
            logger.debug("[稳态时间] 数据不足（%d < %d），返回 0", n, MIN_POINTS)
            return self._make_result(
                bundle,
                0.0,
                {"reason": "insufficient_data", "sample_count": n, "min_required": MIN_POINTS},
            )

        # 控制偏差序列（PV - SP），去均值
        errors = np.array([float(pv) - float(sp) for pv, sp in pairs], dtype=float)
        errors = errors - np.mean(errors)

        if np.std(errors) < 1e-9:
            logger.debug("[稳态时间] 偏差恒定，返回 0（已处于稳态）")
            return self._make_result(
                bundle,
                0.0,
                {"reason": "constant_signal", "std": 0.0},
            )

        # 采样周期（秒）
        sample_interval = self._read_sample_interval(bundle)

        # ARMA 辨识 + Green 函数 → 实际稳态时间
        settling_time = compute_settling_time(
            signal=errors,
            sample_interval_sec=sample_interval,
            threshold=SETTLING_THRESHOLD,
        )

        logger.debug(
            "[稳态时间] sample_interval=%.2f, settling_time=%.1f 秒",
            sample_interval,
            settling_time,
        )

        return self._make_result(
            bundle,
            settling_time,
            {
                "actual_settling_time": round(settling_time, 2),
                "sample_interval": sample_interval,
                "threshold": SETTLING_THRESHOLD,
                "sample_count": n,
            },
        )

    @staticmethod
    def _read_sample_interval(bundle: MetricDataBundle) -> float:
        """读取采样周期（秒）.

        从 sampling_freq 标签解析（如 "1s" → 1.0, "5s" → 5.0）。
        """
        freq = bundle.data_block.sampling_freq
        if not freq:
            return DEFAULT_SAMPLE_INTERVAL
        # 解析 "Ns" 格式
        s = freq.strip().lower().replace("s", "")
        try:
            return float(s) if s else DEFAULT_SAMPLE_INTERVAL
        except ValueError:
            return DEFAULT_SAMPLE_INTERVAL


__all__ = ["SettlingTimeCalculator"]
