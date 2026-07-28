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
复用 app.tasks.arma.compute_settling_time_detailed（只读引用，不修改）。

P0-1：通过 details.reason 区分三种边界语义，不再统一返回 0：
    - already_stable：真已稳态（value=0.0）
    - never_settles：窗口内不衰减（value=None，
      details.actual_settling_time = Green 函数窗口长度，供快速率代入衰减公式）
    - identification_failed：辨识失败（value=None）
"""

from __future__ import annotations

import logging

import numpy as np

from app.contracts.data_types import MetricDataBundle, MetricResult
from app.services.metric_calculator.base import MetricCalculatorBase

logger = logging.getLogger(__name__)

#: 最少数据点数（AR(2) 模型辨识需要足够自由度，设计要求 100 点）
#: 注：P2 #34 已将默认 AR 阶数从 10 降至 2（对齐 ARMA(2,1)），
#: MIN_POINTS 保持 100 以确保数据充分性
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
            MetricResult：value 为实际稳态时间（秒）；
            已稳态时 value=0.0（reason=already_stable）；
            窗口内不衰减 / 辨识失败时 value=None，
            由 details.reason（never_settles / identification_failed）区分
        """
        from app.tasks.arma import SettlingStatus, compute_settling_time_detailed

        pairs = self._get_masked_pair(bundle, "pv", "sp")
        n = len(pairs)

        logger.debug("[稳态时间] 输入: masked_points=%d", n)

        if n < MIN_POINTS:
            logger.debug("[稳态时间] 数据不足（%d < %d），返回 INCONCLUSIVE", n, MIN_POINTS)
            return self._make_inconclusive(
                bundle,
                "insufficient_data",
                {"sample_count": n, "min_required": MIN_POINTS},
            )

        # 控制偏差序列（PV - SP），去均值
        errors = np.array([float(pv) - float(sp) for pv, sp in pairs], dtype=float)
        errors = errors - np.mean(errors)

        if np.std(errors) < 1e-9:
            logger.debug("[稳态时间] 偏差恒定，返回 0（已处于稳态）")
            return self._make_result(
                bundle,
                0.0,
                {"reason": "already_stable", "actual_settling_time": 0.0, "std": 0.0},
            )

        # 采样周期（秒）
        sample_interval = self._read_sample_interval(bundle)

        # ARMA 辨识 + Green 函数 → 实际稳态时间（含三语义状态）
        settling = compute_settling_time_detailed(
            signal=errors,
            sample_interval_sec=sample_interval,
            threshold=SETTLING_THRESHOLD,
        )

        logger.debug(
            "[稳态时间] sample_interval=%.2f, status=%s, settling_time=%s",
            sample_interval,
            settling.status.value,
            settling.value,
        )

        base_details = {
            "sample_interval": sample_interval,
            "threshold": SETTLING_THRESHOLD,
            "sample_count": n,
        }

        # 窗口内不衰减（持续振荡/近单位根）：value=None，details 携带窗口长度
        # 作为 actual_t 下界，供快速率代入指数衰减公式（P0-1）
        if settling.status is SettlingStatus.NEVER_SETTLES:
            return self._make_inconclusive(
                bundle,
                "never_settles",
                {
                    "actual_settling_time": round(settling.window_length_sec, 2),
                    **base_details,
                },
            )

        # 辨识失败（所有尝试阶数 Green 函数发散）
        if settling.status is SettlingStatus.IDENTIFICATION_FAILED:
            return self._make_inconclusive(
                bundle,
                "identification_failed",
                base_details,
            )

        # 已稳态（arma 层兜底，通常已被上方 std 检查拦截）
        if settling.status is SettlingStatus.ALREADY_STABLE:
            return self._make_result(
                bundle,
                0.0,
                {"reason": "already_stable", "actual_settling_time": 0.0, **base_details},
            )

        # SETTLED：正常稳态时间
        settling_time = float(settling.value)
        return self._make_result(
            bundle,
            settling_time,
            {
                "actual_settling_time": round(settling_time, 2),
                "reason": "settled",
                **base_details,
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
