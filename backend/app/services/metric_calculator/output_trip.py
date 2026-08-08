"""输出值行程指数计算器（算法说明 §4.9）.

公式：Trip = Σ|OP_i - OP_{i-1}| / (T_total · OP_range)

其中：
    OP_range = OP_max - OP_min（归一化后为 100）
    T_total：评估时段总时长（秒）

设计依据：算法说明 §4.9；GB/T 44693.2-2024 附录 F.5

定位：辅助诊断指标，用于评估阀门健康状态。
单位：行程/秒
"""

from __future__ import annotations

import logging
from typing import Any

from app.contracts.data_types import MetricDataBundle, MetricResult
from app.services.algorithm_config import get_algorithm_params
from app.services.metric_calculator.base import MetricCalculatorBase

logger = logging.getLogger(__name__)

#: 归一化 OP 量程
DEFAULT_OP_RANGE = 100.0

#: 行程等级阈值
TRIP_INACTIVE = 0.01
TRIP_NORMAL = 0.1
TRIP_FREQUENT = 1.0


class OutputTripIndexCalculator(MetricCalculatorBase):
    """输出值行程指数计算器（算法说明 §4.9）.

    衡量控制器输出的活跃程度，反映阀门行程磨损和控制动作频率。
    """

    @property
    def metric_code(self) -> str:
        return "output_trip_index"

    def calculate(self, bundle: MetricDataBundle) -> MetricResult:
        """计算输出值行程指数.

        Args:
            bundle: 指标数据包（需含 op 信号，mask 为 op_valid）

        Returns:
            MetricResult：value 为行程指数（行程/秒），trip_level 在 details 中
        """
        masked_op = self._get_masked_values(bundle, "op")
        masked_ts = self._get_masked_timestamps(bundle)
        n = len(masked_op)

        logger.debug("[输出行程] 输入: masked_points=%d", n)

        if n < 2 or len(masked_ts) < 2:
            return self._make_inconclusive(bundle, "insufficient_op_data")

        total_duration = self._total_duration_seconds(masked_ts)
        if total_duration <= 0:
            return self._make_inconclusive(bundle, "zero_total_duration")

        op_range = self._read_op_range(bundle)
        if op_range <= 0:
            return self._make_inconclusive(bundle, "zero_op_range")

        # 计算 OP 变化量绝对值之和
        total_trip = 0.0
        for i in range(1, n):
            total_trip += abs(_to_float(masked_op[i]) - _to_float(masked_op[i - 1]))

        trip_index = total_trip / (total_duration * op_range)
        # 整改 F2：行程分级阈值从配置链读取
        params = get_algorithm_params("output_trip_index", bundle.data_block.control_type)
        trip_level = _determine_level(
            trip_index,
            float(params.get("trip_inactive", TRIP_INACTIVE)),
            float(params.get("trip_normal", TRIP_NORMAL)),
            float(params.get("trip_frequent", TRIP_FREQUENT)),
        )

        logger.debug(
            "[输出行程] total_trip=%.4f, duration=%.1f, op_range=%.1f, trip=%.6f, level=%s",
            total_trip,
            total_duration,
            op_range,
            trip_index,
            trip_level,
        )

        return self._make_result(
            bundle,
            trip_index,
            {
                "trip_level": trip_level,
                "total_trip": round(total_trip, 4),
                "total_duration_s": round(total_duration, 2),
                "op_range": op_range,
                "sample_count": n,
            },
            # 行程指数量级通常 1e-4~1（行程/秒），默认 2 位精度会把
            # INACTIVE/NORMAL 区间的值抹零成 0.00，使 0.01/0.1/1.0 阈值失去意义
            precision=6,
        )

    @staticmethod
    def _read_op_range(bundle: MetricDataBundle) -> float:
        """读取 OP 量程范围.

        优先从 CONFIG 信号读取，否则默认 100（归一化）。
        """
        signals = bundle.data_block.signals
        for key in ("op_range", "output_range"):
            val = MetricCalculatorBase._read_config_scalar(signals, key)
            if val is not None:
                try:
                    v = float(val)
                    if v > 0:
                        return v
                except (TypeError, ValueError):
                    continue
        return DEFAULT_OP_RANGE


def _determine_level(
    trip: float,
    inactive: float = TRIP_INACTIVE,
    normal: float = TRIP_NORMAL,
    frequent: float = TRIP_FREQUENT,
) -> str:
    """判定行程等级 INACTIVE/NORMAL/FREQUENT/EXCESSIVE."""
    if trip < inactive:
        return "INACTIVE"
    if trip < normal:
        return "NORMAL"
    if trip < frequent:
        return "FREQUENT"
    return "EXCESSIVE"


def _to_float(val: Any) -> float:
    """安全转换为 float."""
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


__all__ = ["OutputTripIndexCalculator"]
