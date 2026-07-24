"""快速率计算器（算法说明 §4.5）.

公式：
    F = 100%                          当 T ≤ T'
    F = 1/e^((T-T')/T') × 100%        当 T > T'

其中：
    T：实际稳态时间（秒，由 settling_time 计算器提供）
    T'：理想稳态时间（秒，由 ideal_settling_time 计算器提供）

设计依据：算法说明 §4.5；GB/T 44693.2-2024 附录 B.4

定位：核心质量指标，参与综合评分加权。
依赖：settling_time（实际稳态时间）、ideal_settling_time（理想稳态时间）。
"""

from __future__ import annotations

import logging
import math
from typing import Any

from app.contracts.data_types import MetricDataBundle, MetricResult
from app.services.algorithm_config import get_algorithm_params
from app.services.metric_calculator.base import MetricCalculatorBase
from app.services.metric_calculator.disturbance import detect_disturbances

logger = logging.getLogger(__name__)

#: 算法回退默认值（与 algorithm_config._DEFAULTS 一致，配置缺失时使用）
_DEFAULT_IDEAL_SETTLING_RATIO = 1.0
_DEFAULT_SETTLING_TOLERANCE = 0.0
#: P2 抗扰性分析回退默认值（开关默认关闭，零回归）
_DEFAULT_ANTI_DISTURBANCE_ENABLED = False
_DEFAULT_DISTURBANCE_BAND_SIGMA = 2.0
_DEFAULT_RECOVERY_PERSISTENCE = 5
_DEFAULT_MIN_DISTURBANCE_DURATION = 3.0
_DEFAULT_SP_STEP_SIGMA = 3.0


class FastRateCalculator(MetricCalculatorBase):
    """快速率计算器（算法说明 §4.5）.

    基于 ARMA 模型辨识的实际稳态时间与理想稳态时间对比，
    采用分段指数映射：T≤T' 时满分，T>T' 时指数衰减。
    """

    #: 依赖稳态时间和理想稳态时间计算器
    depends_on = ["settling_time", "ideal_settling_time"]

    @property
    def metric_code(self) -> str:
        return "fast_rate"

    def calculate(self, bundle: MetricDataBundle) -> MetricResult:
        """计算快速率.

        Args:
            bundle: 指标数据包

        Returns:
            MetricResult：value 为快速率 0~100，
            actual_settling_time 和 ideal_settling_time 在 details 中。
            P2：anti_disturbance_enabled 开启且检测到扰动时，
            actual_settling_time 为扰动平均恢复时间（source=disturbance）。
        """
        # 从依赖中读取实际稳态时间 T
        settling_result = self.dependencies.get("settling_time")
        actual_t = self._extract_settling_time(settling_result)

        # 从依赖中读取理想稳态时间 T'
        ideal_result = self.dependencies.get("ideal_settling_time")
        ideal_t = ideal_result.value if ideal_result else None

        logger.debug(
            "[快速率] actual_settling=%.2f, ideal_settling=%s",
            actual_t,
            ideal_t,
        )

        # 理想稳态时间无效 → 返回 INCONCLUSIVE
        if ideal_t is None or ideal_t <= 0:
            return self._make_inconclusive(
                bundle,
                "invalid_ideal_settling_time",
                {"actual_settling_time": actual_t, "ideal_settling_time": ideal_t},
            )

        # P0-B: 从配置链读取算法参数（control_type 为响应类别 STABLE/SLOW/FAST/LOGIC）
        # 阈值 = ideal_t × ideal_settling_ratio × (1 + settling_tolerance)
        # 默认 ideal_settling_ratio=1.0, settling_tolerance=0.0
        # → 阈值=ideal_t（与原 actual_t<=ideal_t 一致）
        params = get_algorithm_params("fast_rate", bundle.data_block.control_type)
        ideal_settling_ratio = float(
            params.get("ideal_settling_ratio", _DEFAULT_IDEAL_SETTLING_RATIO)
        )
        settling_tolerance = float(params.get("settling_tolerance", _DEFAULT_SETTLING_TOLERANCE))
        fast_threshold = ideal_t * ideal_settling_ratio * (1.0 + settling_tolerance)

        # P2: 抗扰性分析可选分支（开关默认关闭，关闭时走原 ARMA 逻辑，零回归）
        # 开启后用扰动平均恢复时间替代 ARMA 稳态时间作为 fast_rate 公式的 T 值
        disturbance_details: dict[str, Any] = {"source": "arma"}
        disturbance_override = False
        anti_enabled = bool(
            params.get("anti_disturbance_enabled", _DEFAULT_ANTI_DISTURBANCE_ENABLED)
        )
        if anti_enabled:
            disturbance_details, disturbance_override, actual_t = self._run_disturbance_analysis(
                bundle, ideal_t, params, actual_t
            )

        # 实际稳态时间无效（settling_time 返回 INCONCLUSIVE）→ 快速率也 INCONCLUSIVE
        # 扰动覆盖时跳过（扰动恢复时间是独立有效度量，不依赖 ARMA 辨识）
        if (
            not disturbance_override
            and settling_result is not None
            and settling_result.value is None
        ):
            return self._make_inconclusive(
                bundle,
                "settling_time_inconclusive",
                {
                    "actual_settling_time": actual_t,
                    "ideal_settling_time": round(ideal_t, 2),
                    **disturbance_details,
                },
            )

        # 实际稳态时间 ≤ 0（已稳态或辨识失败）→ 快速率 100%
        if actual_t <= 0:
            logger.debug("[快速率] actual_settling ≤ 0，返回 100")
            return self._make_result(
                bundle,
                100.0,
                {
                    "actual_settling_time": 0.0,
                    "ideal_settling_time": round(ideal_t, 2),
                    "reason": "already_stable",
                    **disturbance_details,
                },
            )

        # T ≤ 阈值 → 快速率 100%
        if actual_t <= fast_threshold:
            logger.debug("[快速率] T=%.1f ≤ 阈值=%.1f，返回 100", actual_t, fast_threshold)
            return self._make_result(
                bundle,
                100.0,
                {
                    "actual_settling_time": round(actual_t, 2),
                    "ideal_settling_time": round(ideal_t, 2),
                    "ratio": round(actual_t / ideal_t, 4),
                    **disturbance_details,
                },
            )

        # T > 阈值 → F = 1/e^((T-T')/T') × 100
        ratio = (actual_t - ideal_t) / ideal_t
        fast_rate = (1.0 / math.exp(ratio)) * 100.0
        fast_rate = self._clamp(fast_rate)

        logger.debug(
            "[快速率] T=%.1f > 阈值=%.1f, ratio=%.4f, F=%.2f",
            actual_t,
            fast_threshold,
            ratio,
            fast_rate,
        )

        return self._make_result(
            bundle,
            fast_rate,
            {
                "actual_settling_time": round(actual_t, 2),
                "ideal_settling_time": round(ideal_t, 2),
                "ratio": round(ratio, 4),
                **disturbance_details,
            },
        )

    def _run_disturbance_analysis(
        self,
        bundle: MetricDataBundle,
        ideal_t: float,
        params: dict[str, Any],
        arma_actual_t: float,
    ) -> tuple[dict[str, Any], bool, float]:
        """执行扰动检测，返回 (details, override, actual_t).

        - 检测到扰动 → override=True, actual_t=平均恢复时间
        - 未检测到/数据不足 → override=False, actual_t=原 ARMA 值
        """
        pv_vals = [float(v) for v in self._get_masked_values(bundle, "pv") if v is not None]
        sp_vals = [float(v) for v in self._get_masked_values(bundle, "sp") if v is not None]
        ts = self._get_masked_timestamps(bundle)

        if len(pv_vals) < 3 or len(sp_vals) < 3 or len(pv_vals) != len(sp_vals):
            return (
                {"source": "arma_fallback", "reason": "insufficient_pv_sp_data"},
                False,
                arma_actual_t,
            )

        if len(ts) == len(pv_vals):
            durations = self._point_durations(ts)
        else:
            durations = [1.0] * len(pv_vals)
        sample_interval = (sum(durations) / len(durations)) if durations else 1.0

        analysis = detect_disturbances(
            pv_vals,
            sp_vals,
            durations,
            ideal_t=ideal_t,
            sample_interval=sample_interval,
            disturbance_band_sigma=float(
                params.get("disturbance_band_sigma", _DEFAULT_DISTURBANCE_BAND_SIGMA)
            ),
            recovery_persistence=int(
                params.get("recovery_persistence", _DEFAULT_RECOVERY_PERSISTENCE)
            ),
            min_disturbance_duration=float(
                params.get("min_disturbance_duration", _DEFAULT_MIN_DISTURBANCE_DURATION)
            ),
            sp_step_sigma=float(params.get("sp_step_sigma", _DEFAULT_SP_STEP_SIGMA)),
        )

        if analysis.t_disturb is not None:
            details = analysis.to_details()
            details["source"] = "disturbance"
            return details, True, analysis.t_disturb

        return (
            {"source": "arma_fallback", "reason": "no_disturbance_detected"},
            False,
            arma_actual_t,
        )

    @staticmethod
    def _extract_settling_time(result: MetricResult | None) -> float:
        """从 settling_time 结果中提取实际稳态时间.

        优先从 details.actual_settling_time 读取，回退到 value。
        """
        if result is None:
            return 0.0
        if result.details:
            val = result.details.get("actual_settling_time")
            if val is not None:
                try:
                    return float(val)
                except (TypeError, ValueError):
                    pass
        if result.value is not None:
            try:
                return float(result.value)
            except (TypeError, ValueError):
                pass
        return 0.0


__all__ = ["FastRateCalculator"]
