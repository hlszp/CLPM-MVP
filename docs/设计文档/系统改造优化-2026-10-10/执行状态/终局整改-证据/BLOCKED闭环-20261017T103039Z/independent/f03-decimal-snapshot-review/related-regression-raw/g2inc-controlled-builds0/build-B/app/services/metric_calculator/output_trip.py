"""输出值行程指数计算器（算法说明 §4.9）.

公式：Trip = Σ|OP_i - OP_{i-1}| / (T_total · OP_range)

其中：
    OP_range = OP_max - OP_min（归一化后为 100）
    T_total：评估时段总时长（秒）

设计依据：算法说明 §4.9；GB/T 44693.2-2024 附录 F.5

定位：辅助诊断指标，用于评估阀门健康状态。
单位：行程/秒

P3-10c 新 profile（CANDIDATE_V3_RC1，10§3.4 输出行程行 + C16-EA3）：
    - **分段累加**：每个连续有效片段内累加 OP 变化，**不累加跨缺口差**
      （legacy 把 gap 两侧的跳变计入行程，断点跳变会被误读为阀行程）；
    - **未知活动显式化**：存在缺口时 ``cross_gap_excluded_pairs>0`` 且
      ``unknown_activity=true``（跨缺口期间的实际指令活动不可知，不冒充
      已知）；
    - 名称口径为**指令活动量**（``quantity=output_command_activity``）：
      OP 是控制器输出指令，不是实测阀位行程；
    - 报告采样分辨率（``sampling_interval_seconds``）——粗采样下相邻差
      分本身就是欠采样近似。
"""

from __future__ import annotations

import logging
from typing import Any

from app.contracts.data_types import MetricDataBundle, MetricResult
from app.services.metric_calculator.base import MetricCalculatorBase

logger = logging.getLogger(__name__)

#: 归一化 OP 量程
DEFAULT_OP_RANGE = 100.0

#: 行程等级阈值
TRIP_INACTIVE = 0.01
TRIP_NORMAL = 0.1
TRIP_FREQUENT = 1.0

#: 新 profile 指令活动量口径标注（P6 文案按此展示，不称实测阀行程）
_TRIP_QUANTITY = "output_command_activity"
_TRIP_QUANTITY_NOTE = (
    "OP output command activity per second, not measured valve travel; "
    "cross-gap differences are excluded and the gap-time activity is unknown"
)


def _op_values_and_runs(
    bundle: MetricDataBundle,
) -> tuple[list[float], list[tuple[int, int]]]:
    """masked OP 数值序列 + 连续游程（数值位置空间；缺口断开）.

    游程断开条件（C16-EA3）：相邻可用点的原始索引不邻接（中间有被排除
    的槽）或夹 None（信号缺失）。与 setpoint_crossing._diffs_and_runs
    同型（单信号版本）。

    Returns:
        (values, runs)：values 为过滤 None 后的 float 序列；runs 为 values
        位置空间闭区间对，可直接 ``values[a:b+1]`` 切段。
    """
    idx = bundle.masked_indices
    raw = bundle.data_block.signals.get("op", [])
    values: list[float] = []
    pos_of_point: list[int | None] = []
    for i in idx:
        v = raw[i] if i < len(raw) else None
        if v is None:
            pos_of_point.append(None)
        else:
            pos_of_point.append(len(values))
            values.append(_to_float(v))
    if not values:
        return values, []

    runs: list[tuple[int, int]] = []
    start = 0
    prev_usable: int | None = None  # masked 位置
    for k, pos in enumerate(pos_of_point):
        if pos is None:
            continue
        if prev_usable is not None:
            adjacent = (
                k == prev_usable + 1
                and k < len(idx)
                and prev_usable < len(idx)
                and idx[k] == idx[prev_usable] + 1
            )
            if not adjacent:
                runs.append((start, pos - 1))
                start = pos
        prev_usable = k
    runs.append((start, len(values) - 1))
    return values, runs


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

        params = self._effective_params("output_trip_index", bundle)

        if self.new_semantics:
            return self._calculate_new_profile(bundle, masked_ts, total_duration, op_range, params)

        # 计算 OP 变化量绝对值之和
        total_trip = 0.0
        for i in range(1, n):
            total_trip += abs(_to_float(masked_op[i]) - _to_float(masked_op[i - 1]))

        trip_index = total_trip / (total_duration * op_range)
        # 整改 F2：行程分级阈值从配置链读取
        # G2-INC-a：任务固定五层有效参数优先，未固定才读配置链当前缓存
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

    def _calculate_new_profile(
        self,
        bundle: MetricDataBundle,
        masked_ts: list,
        total_duration: float,
        op_range: float,
        params: dict[str, Any],
    ) -> MetricResult:
        """新 profile：连续片段内累加 OP 变化，不累加跨缺口差（C16-EA3）."""
        values, runs = _op_values_and_runs(bundle)

        total_trip = 0.0
        for a, b in runs:
            for i in range(a + 1, b + 1):
                total_trip += abs(values[i] - values[i - 1])

        trip_index = total_trip / (total_duration * op_range)
        trip_level = _determine_level(
            trip_index,
            float(params.get("trip_inactive", TRIP_INACTIVE)),
            float(params.get("trip_normal", TRIP_NORMAL)),
            float(params.get("trip_frequent", TRIP_FREQUENT)),
        )

        gap_pairs = len(runs) - 1 if len(runs) > 1 else 0
        # 采样分辨率（秒）：masked 时间戳均值口径（时间戳不可用回退 1s 默认）
        n_pts = len(values)
        sampling_interval = (
            total_duration / (len(masked_ts) - 1)
            if len(masked_ts) >= 2 and total_duration > 0
            else 1.0
        )

        logger.debug(
            "[输出行程|新] total_trip=%.4f, segments=%d, gap_pairs=%d, trip=%.6f",
            total_trip,
            len(runs),
            gap_pairs,
            trip_index,
        )
        return self._make_result(
            bundle,
            trip_index,
            {
                "trip_level": trip_level,
                "total_trip": round(total_trip, 4),
                "total_duration_s": round(total_duration, 2),
                "op_range": op_range,
                "sample_count": n_pts,
                "segment_count": len(runs),
                "cross_gap_excluded_pairs": gap_pairs,
                "unknown_activity": gap_pairs > 0,
                "sampling_interval_seconds": round(sampling_interval, 4),
                "quantity": _TRIP_QUANTITY,
                "quantityNote": _TRIP_QUANTITY_NOTE,
                "assessmentState": "COMPLETE",
            },
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
