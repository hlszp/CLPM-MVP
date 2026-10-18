"""自控率计算器（算法说明 §4.0.3）.

公式：Auto = T_auto / T_total × 100%

其中：
    T_auto：MODE 为 Auto/Cascade/Remote 的累计时长
    T_total：评估时段总时长

设计依据：算法说明 §4.0.3；GB/T 44693.2-2024 附录 B.1

定位：辅助诊断指标，不参与综合评分加权（有效自控率才是折扣因子）。
"""

from __future__ import annotations

import logging
from typing import Any

from app.constants.mode import AUTO_MODES
from app.contracts.data_types import MetricDataBundle, MetricResult
from app.services.metric_calculator.base import MetricCalculatorBase
from app.services.metric_calculator.time_axis import view_or_none

logger = logging.getLogger(__name__)


class AutoModeRateCalculator(MetricCalculatorBase):
    """自控率计算器（算法说明 §4.0.3）.

    统计评估时段内控制器处于自动/串级/远程模式的时长占比。
    与有效自控率的区别：自控率仅判定 MODE，不考虑 OP 饱和和偏差。
    """

    @property
    def metric_code(self) -> str:
        return "auto_mode_rate"

    def calculate(self, bundle: MetricDataBundle) -> MetricResult:
        """计算自控率.

        Args:
            bundle: 指标数据包（需含 mode 信号，mask 为 mode_valid）

        Returns:
            MetricResult：value 为自控率 0~100，数据不足时 INCONCLUSIVE

        时间契约（P3-08c，10§3.1 表 Auto 行/C03-EA2）：
            - 分母 = 完整请求窗口秒数；分子只计"已知有效自动模式"的秒数；
            - MODE 未知段不补时、不缩分母，也不得标作手动——输出已证实比例
              与可能区间（如 40s 自动 + 20s 未知 + 40s 手动 → 已证实 40%，
              范围 40%~60%，unknown=20s，禁止报确定 60%）。
            时间视图不可得（legacy/手工 bundle）→ 原 masked 压缩口径不变。
        """
        masked_mode = self._get_masked_values(bundle, "mode")
        masked_ts = self._get_masked_timestamps(bundle)
        n = len(masked_mode)

        logger.debug("[自控率] 输入: masked_points=%d", n)

        if n < 2 or len(masked_ts) < 2:
            return self._make_inconclusive(bundle, "insufficient_mode_data")

        # P3-08c：时间视图可用 → 半开窗口契约口径
        view = view_or_none(bundle, ("mode",))
        if view is not None and len(view.indices) >= 2:
            mode_sig = bundle.data_block.signals.get("mode") or []

            def _eligible(slot: int) -> bool:
                orig = view.indices[slot]
                mode_val = self._to_int(mode_sig[orig]) if orig < len(mode_sig) else -1
                return mode_val in AUTO_MODES and view.role_valid("mode", slot)

            accounting = view.ratio_accounting(_eligible, roles=("mode",))
            if accounting.denominator_seconds <= 0:
                return self._make_inconclusive(bundle, "zero_total_duration")
            rate = self._clamp(accounting.proven_ratio * 100.0)
            details: dict[str, Any] = {
                "auto_duration_s": round(accounting.numerator_seconds, 2),
                "total_duration_s": round(accounting.denominator_seconds, 2),
                "sample_count": n,
                "time_basis": "half_open_window",
            }
            details.update(accounting.to_details())
            logger.debug(
                "[自控率] auto=%.1f, total=%.1f, unknown=%.1f, rate=%.2f%%",
                accounting.numerator_seconds,
                accounting.denominator_seconds,
                accounting.unknown_seconds,
                rate,
            )
            return self._make_result(bundle, rate, details)

        # ---------------- legacy 回退（无窗口/上下文：原口径不变） ----------------
        # 采用零阶保持模型：每个采样点代表一个时间间隔（最后一个点沿用前段时长）
        # 数组长度可能不一致（信号与时间戳长度不齐），循环上界取最小长度防止 IndexError
        durations = self._point_durations(masked_ts)
        bound = min(n, len(durations))
        durations = durations[:bound]
        total_duration = sum(durations)
        if total_duration <= 0:
            return self._make_inconclusive(bundle, "zero_total_duration")

        auto_duration = 0.0
        for i in range(bound):
            segment = durations[i]
            mode_val = self._to_int(masked_mode[i])
            if mode_val in AUTO_MODES:
                auto_duration += segment

        rate = (auto_duration / total_duration) * 100.0
        rate = self._clamp(rate)

        logger.debug(
            "[自控率] auto_duration=%.1f, total=%.1f, rate=%.2f%%",
            auto_duration,
            total_duration,
            rate,
        )

        return self._make_result(
            bundle,
            rate,
            {
                "auto_duration_s": round(auto_duration, 2),
                "total_duration_s": round(total_duration, 2),
                "sample_count": n,
                "time_basis": "legacy_masked_extent",
            },
        )

    @staticmethod
    def _to_int(val: Any) -> int:
        """安全转换为 int（mode 可能是 float/str）."""
        try:
            return int(float(val))
        except (TypeError, ValueError):
            return -1


__all__ = ["AutoModeRateCalculator"]
