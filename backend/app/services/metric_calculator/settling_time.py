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
from typing import Any

import numpy as np

from app.contracts.data_types import MetricDataBundle, MetricResult
from app.services.algorithm_config import get_algorithm_params
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

# ---------------------------------------------------------------------------
# R14-4（2026-09-06）：ARMA 等间隔准入容差
#
# settling_time 基于 AR 模型辨识 + Green 函数递推，前提是输入为**等间隔**
# 采样序列（时间尺度 = 采样周期 × 步数）。两类不满足即跳过该指标并记录
# 原因，不得按声明间隔（尤其伪 1s）计算：
#   - 间隔失真：实际中位间隔与声明（sampling_freq，现为实际中位间隔标签）
#     偏差 > 20%，且 > 0.5s 绝对余量（容纳亚秒数据的标签取整）；
#   - 非均匀：偏离中位 ±20% 的间隔占比 > 10%（COV 事件流天然不规则）。
# ---------------------------------------------------------------------------
_INTERVAL_DEVIATION_TOLERANCE = 0.20
_INTERVAL_DEVIATION_ABS_S = 0.5
_INTERVAL_JITTER_TOLERANCE = 0.20
_INTERVAL_JITTER_RATIO_LIMIT = 0.10


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

        # R14-4：等间隔准入——实际间隔与声明偏差超容差或间隔抖动超阈值时
        # 跳过该指标（INCONCLUSIVE + 原因），不得按声明间隔计算时间尺度
        sample_interval = self._read_sample_interval(bundle)
        uniform_ok, uniform_reason, uniform_details = self._check_uniform_sampling(
            bundle, sample_interval
        )
        if not uniform_ok:
            logger.info(
                "[稳态时间] 等间隔前提不满足，跳过计算: reason=%s, details=%s",
                uniform_reason,
                uniform_details,
            )
            return self._make_inconclusive(bundle, uniform_reason, uniform_details)

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

        # 整改 F2：衰减阈值从配置链读取（默认与常量一致）
        params = get_algorithm_params("settling_time", bundle.data_block.control_type)
        settling_threshold = float(params.get("settling_threshold", SETTLING_THRESHOLD))

        # ARMA 辨识 + Green 函数 → 实际稳态时间（含三语义状态）
        settling = compute_settling_time_detailed(
            signal=errors,
            sample_interval_sec=sample_interval,
            threshold=settling_threshold,
        )

        logger.debug(
            "[稳态时间] sample_interval=%.2f, status=%s, settling_time=%s",
            sample_interval,
            settling.status.value,
            settling.value,
        )

        base_details = {
            "sample_interval": sample_interval,
            "threshold": settling_threshold,
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
        R14-1 后该标签反映实际中位间隔（稀疏数据为 "30s" 等）。
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

    @staticmethod
    def _check_uniform_sampling(
        bundle: MetricDataBundle,
        declared_interval_s: float,
    ) -> tuple[bool, str, dict[str, Any]]:
        """校验序列等间隔性（R14-4 ARMA 准入前提）.

        Args:
            bundle: 指标数据包（取掩码后时间戳）
            declared_interval_s: 声明采样周期（秒，来自 sampling_freq 标签）

        Returns:
            (ok, reason, details)：ok=False 时 reason 为跳过原因码
            （"insufficient_timestamps" / "timestamps_not_comparable" /
            "non_positive_median_interval" / "sampling_interval_mismatch" /
            "non_uniform_sampling"）
        """
        timestamps = MetricCalculatorBase._get_masked_timestamps(bundle)
        if len(timestamps) < 2:
            return False, "insufficient_timestamps", {"sample_count": len(timestamps)}
        try:
            ts_sorted = sorted(timestamps)
            deltas = [
                (b - a).total_seconds() for a, b in zip(ts_sorted, ts_sorted[1:], strict=False)
            ]
        except (TypeError, AttributeError):
            return False, "timestamps_not_comparable", {}
        if not deltas:
            return False, "insufficient_timestamps", {"sample_count": len(timestamps)}

        deltas_sorted = sorted(deltas)
        m = len(deltas_sorted)
        mid = m // 2
        median_interval = (
            float(deltas_sorted[mid])
            if m % 2 == 1
            else (deltas_sorted[mid - 1] + deltas_sorted[mid]) / 2.0
        )
        if median_interval <= 0:
            return (
                False,
                "non_positive_median_interval",
                {"median_interval_s": median_interval, "declared_interval_s": declared_interval_s},
            )

        # 间隔失真：实际中位间隔 vs 声明（20% 相对 + 0.5s 绝对余量）
        deviation = abs(median_interval - declared_interval_s)
        deviation_limit = max(
            _INTERVAL_DEVIATION_TOLERANCE * max(median_interval, declared_interval_s),
            _INTERVAL_DEVIATION_ABS_S,
        )
        if deviation > deviation_limit:
            return (
                False,
                "sampling_interval_mismatch",
                {
                    "median_interval_s": round(median_interval, 4),
                    "declared_interval_s": declared_interval_s,
                    "deviation_s": round(deviation, 4),
                    "deviation_limit_s": round(deviation_limit, 4),
                },
            )

        # 非均匀：偏离中位 ±20% 的间隔占比 > 10%
        jitter_count = sum(
            1
            for d in deltas
            if abs(d - median_interval) > _INTERVAL_JITTER_TOLERANCE * median_interval
        )
        jitter_ratio = jitter_count / len(deltas)
        if jitter_ratio > _INTERVAL_JITTER_RATIO_LIMIT:
            return (
                False,
                "non_uniform_sampling",
                {
                    "median_interval_s": round(median_interval, 4),
                    "declared_interval_s": declared_interval_s,
                    "jitter_ratio": round(jitter_ratio, 4),
                    "jitter_ratio_limit": _INTERVAL_JITTER_RATIO_LIMIT,
                },
            )

        return True, "", {"median_interval_s": round(median_interval, 4)}


__all__ = ["SettlingTimeCalculator"]
