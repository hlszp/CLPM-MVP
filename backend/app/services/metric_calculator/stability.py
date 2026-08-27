"""稳定率计算器（算法说明 §4.3）.

公式：S = 1/e^(σ/(d·U)) × (1-Osc) × 100%

其中：
    E_i = PV_i - SP_i
    σ = sqrt((1/(n-1)) × Σ(E_i - Ē)²)  （无偏估计，分母 n-1，对齐 FDS v5.1 / 算法 v2.1）
    U = PV 量程范围（归一化后为 100）
    d = 指数衰减基准（量程比例，默认 0.05，经 algorithm_config 配置链可调）
    Osc = 振荡率（0~1，由 oscillation_rate 计算器提供）

石化惯例辅助口径（带内时间占比）：
    band_in_rate = Σ(|E_i| ≤ b·U 的点时长) / 总时长 × 100
    b = 平稳带比例（默认 0.01，即量程 ±1%，配置链可调）；
    始终输出到 details.band_in_rate 供石化考核对标；
    band_in_score_enabled=True 时替代指数公式作为分值（不乘振荡修正）。

SP 阶跃剔除（sp_step_exclusion_enabled，默认开启——2026-08-27 起由默认关闭
    改为默认打开）：
    复用 disturbance.detect_sp_tracking_windows 检测 SP 阶跃，
    剔除阶跃后 sp_tracking_window 个跟踪点再计算 σ 与带内率，
    避免"操作员正常改设定值"的跟踪暂态被误判为不平稳；
    需计入跟踪暂态考核时可经配置链显式关闭。

设计依据：算法说明 §4.3 v2.1；GB/T 44693.2-2024 附录 B.5

v2.1 修正：标准差 σ 由"分母 n（有偏估计）"改为"分母 n-1（无偏估计）"，
对齐 FDS v5.1 §4.3.4 步骤 7。无偏估计在小样本（n<30）时更准确，
避免系统性低估标准差导致稳定率偏高。
"""

from __future__ import annotations

import logging
import math

import numpy as np

from app.contracts.data_types import MetricDataBundle, MetricResult
from app.services.algorithm_config import get_algorithm_params
from app.services.metric_calculator.base import MetricCalculatorBase
from app.services.metric_calculator.disturbance import detect_sp_tracking_windows

logger = logging.getLogger(__name__)

#: 归一化量程
DEFAULT_PV_RANGE = 100.0

#: 指数衰减基准默认值（量程比例，与 algorithm_config._DEFAULTS 一致）
DEFAULT_DECAY_RATIO = 0.05

#: 石化惯例平稳带默认值（量程比例，±1%，与 algorithm_config._DEFAULTS 一致）
DEFAULT_BAND_RATIO = 0.01

#: SP 阶跃检测阈值默认值（sp_diff 标准差倍数，与 algorithm_config._DEFAULTS 一致）
DEFAULT_SP_STEP_SIGMA = 3.0

#: SP 阶跃后跟踪窗默认点数（与 algorithm_config._DEFAULTS 一致）
DEFAULT_SP_TRACKING_WINDOW = 60

#: 最少数据点数
MIN_POINTS = 2


class StabilityRateCalculator(MetricCalculatorBase):
    """稳定率计算器（算法说明 §4.3）.

    采用控制偏差标准差衡量 PV 波动平稳程度，结合振荡率修正。
    指数型公式：σ=0 时 S=100%，σ 增大时 S 指数衰减。
    """

    #: 依赖振荡率计算器
    depends_on = ["oscillation_rate"]

    @property
    def metric_code(self) -> str:
        return "stability_rate"

    def calculate(self, bundle: MetricDataBundle) -> MetricResult:
        """计算稳定率.

        Args:
            bundle: 指标数据包（需含 pv/sp 信号，mask 为 pv_valid && sp_valid）

        Returns:
            MetricResult：value 为稳定率 0~100，
            oscillation_rate 从 dependencies 读取
        """
        pairs = self._get_masked_pair(bundle, "pv", "sp")
        n = len(pairs)

        logger.debug("[稳定率] 输入: masked_points=%d", n)

        if n < MIN_POINTS:
            return self._make_inconclusive(bundle, "insufficient_data")

        # 配置链：decay_ratio / band_ratio / band_in_score_enabled / SP 阶跃剔除
        # （sp_step_exclusion 默认 True，与 algorithm_config._DEFAULTS 一致）
        params = get_algorithm_params("stability_rate", bundle.data_block.control_type)
        decay_ratio = float(params.get("decay_ratio", DEFAULT_DECAY_RATIO))
        band_ratio = float(params.get("band_ratio", DEFAULT_BAND_RATIO))
        band_in_score = bool(params.get("band_in_score_enabled", False))
        sp_exclusion = bool(params.get("sp_step_exclusion_enabled", True))
        sp_step_sigma = float(params.get("sp_step_sigma", DEFAULT_SP_STEP_SIGMA))
        sp_window = int(params.get("sp_tracking_window", DEFAULT_SP_TRACKING_WINDOW))

        # 计算控制偏差
        errors = np.array([float(pv) - float(sp) for pv, sp in pairs], dtype=float)
        # 点时间权重（与 errors 对齐；时间戳缺失时为空 → 带内率回退点等权）
        ts = self._get_masked_timestamps(bundle)
        durations = self._point_durations(ts) if len(ts) == n else []

        # SP 阶跃剔除：剔除阶跃后跟踪窗内的点（默认关闭，开启后σ/带内率仅基于稳态段）
        sp_steps = 0
        sp_excluded = 0
        if sp_exclusion:
            sp_vals = [float(sp) for _, sp in pairs]
            tracking = detect_sp_tracking_windows(
                sp_vals,
                n,
                ideal_t=0.0,
                sample_interval=1.0,
                sp_step_sigma=sp_step_sigma,
                window_points=sp_window,
            )
            sp_steps = sum(1 for i in range(1, n) if tracking[i] and not tracking[i - 1])
            sp_excluded = sum(tracking)
            if sp_excluded:
                keep = np.array([not t for t in tracking], dtype=bool)
                errors = errors[keep]
                if durations:
                    durations = [d for d, k in zip(durations, keep, strict=True) if k]
                logger.debug(
                    "[稳定率] SP 阶跃剔除: steps=%d, excluded=%d/%d, window=%d",
                    sp_steps,
                    sp_excluded,
                    n,
                    sp_window,
                )
                if len(errors) < MIN_POINTS:
                    return self._make_inconclusive(
                        bundle,
                        "insufficient_data_after_sp_exclusion",
                        {"sp_steps_detected": sp_steps, "sp_excluded_points": sp_excluded},
                    )

        mean_error = float(np.mean(errors))
        # v2.1 修正：使用 ddof=1（无偏估计，分母 n-1），对齐 FDS v5.1 / 算法 v2.1 §4.3.4 步骤 7
        # n>=2 保证（MIN_POINTS=2 已在上方校验），ddof=1 不会除零
        std_error = float(np.std(errors, ddof=1))

        # U = PV 量程范围
        u = self._read_pv_range(bundle)
        if u <= 0:
            return self._make_inconclusive(bundle, "zero_pv_range")

        # 石化惯例辅助口径：带内时间占比（始终输出 details，供考核对标）
        band = band_ratio * u
        band_in_rate = self._band_in_rate(errors, band, durations)

        base_details = {
            "mean_error": round(mean_error, 4),
            "std_error": round(std_error, 4),
            "pv_range": u,
            "band_ratio": band_ratio,
            "band_in_rate": round(band_in_rate, 2),
            "sample_count": int(len(errors)),
            "sp_step_exclusion": sp_exclusion,
            "sp_steps_detected": sp_steps,
            "sp_excluded_points": sp_excluded,
        }

        # 石化惯例分值模式：带内时间占比直接作为分值（不乘振荡修正）
        if band_in_score:
            stability = self._clamp(band_in_rate)
            logger.debug(
                "[稳定率] 石化惯例口径: band=%.4f, band_in_rate=%.2f",
                band,
                stability,
            )
            return self._make_result(
                bundle,
                stability,
                {**base_details, "score_mode": "band_in"},
            )

        # 振荡率（0~1）
        # P0 修复：仅在判定振荡（is_oscillating=True）时应用 (1-Osc) 修正；
        # 未判振荡时振荡率仅为相似率连续值（<阈值），不作为扣减因子，
        # 避免非振荡回路被误扣；振荡率数值仍透传 details 供展示。
        osc_result = self.dependencies.get("oscillation_rate")
        osc_rate_pct = osc_result.value if osc_result and osc_result.value is not None else 0.0
        is_oscillating = (
            bool(osc_result.details.get("is_oscillating", True)) if osc_result else True
        )
        osc_factor = 1.0 - (osc_rate_pct / 100.0) if is_oscillating else 1.0

        if osc_factor <= 0:
            logger.debug("[稳定率] 振荡率 %.2f%% ≥ 100%%，稳定率返回 0", osc_rate_pct)
            return self._make_result(
                bundle,
                0.0,
                {
                    **base_details,
                    "score_mode": "exponential",
                    "oscillation_rate": round(osc_rate_pct, 2),
                    "reason": "osc_too_high",
                },
            )

        # 指数衰减：S = 1/e^(σ/(d·U)) × (1-Osc) × 100
        # 使用 e^(-x) 等价形式：大量程/大 σ 时 x 可达 1e5+，
        # 直接 math.exp(x) 会 OverflowError，math.exp(-x) 在 x→∞ 时稳定返回 0.0
        normalized_std = std_error / (decay_ratio * u)
        stability = math.exp(-normalized_std) * osc_factor * 100.0
        stability = self._clamp(stability)

        logger.debug(
            "[稳定率] mean_error=%.4f, std=%.4f, U=%.1f, norm_std=%.4f, osc=%.2f%%, S=%.2f",
            mean_error,
            std_error,
            u,
            normalized_std,
            osc_rate_pct,
            stability,
        )

        return self._make_result(
            bundle,
            stability,
            {
                **base_details,
                "score_mode": "exponential",
                "decay_ratio": decay_ratio,
                "normalized_std": round(normalized_std, 4),
                "oscillation_rate": round(osc_rate_pct, 2),
                "osc_factor": round(osc_factor, 4),
            },
        )

    def _band_in_rate(self, errors: np.ndarray, band: float, durations: list[float]) -> float:
        """石化惯例带内时间占比：|E| ≤ band 的点按时长加权占比（0~100）.

        durations 需与 errors 对齐；为空（时间戳不可用）时回退点等权。
        """
        in_band = np.abs(errors) <= band
        if not durations or len(durations) != len(errors):
            return float(np.mean(in_band)) * 100.0
        weights = np.array(durations, dtype=float)
        total = float(np.sum(weights))
        if total <= 0:
            return float(np.mean(in_band)) * 100.0
        return float(np.sum(weights[in_band])) / total * 100.0

    @staticmethod
    def _read_pv_range(bundle: MetricDataBundle) -> float:
        """读取 PV 量程范围.

        按 ``_read_config_scalar`` 契约解包列表形式 CONFIG 标量（与
        accuracy._read_pv_range 一致）；缺失/非法时回退归一化默认 100。
        """
        val = MetricCalculatorBase._read_config_scalar(bundle.data_block.signals, "pv_range")
        if val is None:
            return DEFAULT_PV_RANGE
        try:
            v = float(val)
            return v if v > 0 else DEFAULT_PV_RANGE
        except (TypeError, ValueError):
            return DEFAULT_PV_RANGE


__all__ = ["StabilityRateCalculator"]
