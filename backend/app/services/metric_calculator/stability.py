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
from app.services.metric_calculator.base import MetricCalculatorBase
from app.services.metric_calculator.disturbance import detect_sp_tracking_windows
from app.services.metric_calculator.time_axis import view_or_none

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

#: P3-09c：SP 暂态剔除窗物理秒默认值（10§3.3"暂态剔除改为可追溯的真实
#: 秒数"；旧 60 点 × 声明周期 1s 的兼容迁移初值=60s，来源可查）
_DEFAULT_SP_TRACKING_WINDOW_SECONDS = 60.0

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
        # 2026-10-23：时间视图可用时剔除 pv/sp 覆盖 UNKNOWN 的槽（真实 gap
        # 的保持陈值不进 σ/带内率；正常 COV 保持=HELD 保留），与 R/Auto/Sat
        # 口径对齐；legacy 不变。视图在下方 durations 处复用，避免重复构建。
        view = view_or_none(bundle, ("pv", "sp"))
        known_idx = self._known_slot_indices(bundle, ("pv", "sp"), view=view)
        if known_idx is not None:
            sig_pv = bundle.data_block.signals.get("pv", [])
            sig_sp = bundle.data_block.signals.get("sp", [])
            pairs = [
                (sig_pv[i], sig_sp[i]) for i in known_idx if i < len(sig_pv) and i < len(sig_sp)
            ]
            all_ts = bundle.data_block.timestamps
            ts = [all_ts[i] for i in known_idx if i < len(all_ts)]
        else:
            pairs = self._get_masked_pair(bundle, "pv", "sp")
            ts = self._get_masked_timestamps(bundle)
        n = len(pairs)

        logger.debug("[稳定率] 输入: masked_points=%d", n)

        if n < MIN_POINTS:
            return self._make_inconclusive(bundle, "insufficient_data")

        # 配置链：decay_ratio / band_ratio / band_in_score_enabled / SP 阶跃剔除
        # （sp_step_exclusion 默认 True，与 algorithm_config._DEFAULTS 一致）
        # G2-INC-a：任务固定五层有效参数优先，未固定才读配置链当前缓存
        params = self._effective_params("stability_rate", bundle)
        decay_ratio = float(params.get("decay_ratio", DEFAULT_DECAY_RATIO))
        band_ratio = float(params.get("band_ratio", DEFAULT_BAND_RATIO))
        band_in_score = bool(params.get("band_in_score_enabled", False))
        sp_exclusion = bool(params.get("sp_step_exclusion_enabled", True))
        sp_step_sigma = float(params.get("sp_step_sigma", DEFAULT_SP_STEP_SIGMA))
        sp_window = int(params.get("sp_tracking_window", DEFAULT_SP_TRACKING_WINDOW))

        # 计算控制偏差
        errors = np.array([float(pv) - float(sp) for pv, sp in pairs], dtype=float)
        # 点时间权重（与 errors 对齐；时间戳缺失时为空 → 带内率回退点等权）
        # P3-08c 时间契约消费（08c→09c 串行链首位——本卡只接时间轴不改算法
        # 本体，σ/振荡修正/带内公式不动）：时间视图可用时，逐点时长取**半开
        # 槽秒数**（末槽截到窗口 end，不复制上一段时长越窗——C04-EA1）；
        # 视图不可得（legacy/手工 bundle）→ 原 _point_durations 口径不变。
        # 2026-10-23：known_idx 过滤后时长与过滤点集对齐（ts 已按同索引过滤）。
        if view is not None and len(view.indices) >= 2 and len(ts) == n:
            durations = view.masked_slot_seconds(
                known_idx if known_idx is not None else bundle.masked_indices
            )
        else:
            durations = self._point_durations(ts) if len(ts) == n else []

        # SP 阶跃剔除：剔除阶跃后跟踪窗内的点（默认关闭，开启后σ/带内率仅基于稳态段）
        # P3-09c 新 profile：跟踪窗按物理秒（sp_tracking_window_seconds，默认
        # 60s）；剔除秒数可追溯（时间契约消费：带内率分母=窗口−已知剔除暂态）
        sp_steps = 0
        sp_excluded = 0
        sp_excluded_seconds: float | None = None
        sp_window_source = "legacy_points"
        if sp_exclusion:
            sp_vals = [float(sp) for _, sp in pairs]
            if self.new_semantics:
                # 平均采样间隔（物理秒换算基准）：时间视图网格周期 > 时间戳均值
                interval = 1.0
                if view is not None and view.grid_period_s > 0:
                    interval = float(view.grid_period_s)
                elif len(ts) == n and n > 1:
                    total = self._total_duration_seconds(ts)
                    interval = total / (n - 1) if total > 0 else 1.0
                window_seconds = float(
                    params.get("sp_tracking_window_seconds", _DEFAULT_SP_TRACKING_WINDOW_SECONDS)
                )
                sp_window_source = "physical_seconds"
                tracking = detect_sp_tracking_windows(
                    sp_vals,
                    n,
                    ideal_t=0.0,
                    sample_interval=interval,
                    sp_step_sigma=sp_step_sigma,
                    window_seconds=window_seconds,
                )
            else:
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
                if durations:
                    sp_excluded_seconds = round(float(np.sum(np.asarray(durations)[~keep])), 3)
                errors = errors[keep]
                if durations:
                    durations = [d for d, k in zip(durations, keep, strict=True) if k]
                logger.debug(
                    "[稳定率] SP 阶跃剔除: steps=%d, excluded=%d/%d, window=%s(%s), "
                    "excluded_seconds=%s",
                    sp_steps,
                    sp_excluded,
                    n,
                    (
                        f"{
                            params.get(
                                'sp_tracking_window_seconds',
                                _DEFAULT_SP_TRACKING_WINDOW_SECONDS,
                            )
                        }s"
                        if self.new_semantics
                        else f"{sp_window}pts"
                    ),
                    sp_window_source,
                    sp_excluded_seconds,
                )
                if len(errors) < MIN_POINTS:
                    return self._make_inconclusive(
                        bundle,
                        "insufficient_data_after_sp_exclusion",
                        {
                            "sp_steps_detected": sp_steps,
                            "sp_excluded_points": sp_excluded,
                            **({"assessmentState": "UNASSESSABLE"} if self.new_semantics else {}),
                        },
                    )

        mean_error = float(np.mean(errors))
        # v2.1 修正：使用 ddof=1（无偏估计，分母 n-1），对齐 FDS v5.1 / 算法 v2.1 §4.3.4 步骤 7
        # n>=2 保证（MIN_POINTS=2 已在上方校验），ddof=1 不会除零
        std_error = float(np.std(errors, ddof=1))

        # 完全死值（σ=0 且恒定偏离 SP，2026-10-10 整改；CAL-02 修正 2026-10-10）：
        # 仅当 **PV 自身全程一字不变**（典型为仪表卡死或 COV 稳定不落库的填充
        # 假象）时判 INCONCLUSIVE（诚实化）——"稳定"不可评估，满分 100 会奖励
        # 死信号。判据看信号自身而非仅 error 方差：PV=变化SP+常数偏置 的回路
        # error 恒定（std=0 且 mean≠0）但 PV 随 SP 移动，是活的跟踪回路，不得
        # 误判死信号（其 σ=0 的稳态跟踪按公式得满分）。
        # PV 自身波动在完整掩码对上检查，不受 SP 阶跃剔除影响（卡死是全窗
        # 物理属性）；σ=0 且均值=0 的完美贴死 SP 情形保持满分（与零偏差语义
        # 一致，无法与真完美区分）。
        if std_error == 0.0 and mean_error != 0.0:
            pv_values = np.array([float(pv) for pv, _ in pairs], dtype=float)
            pv_frozen = bool(pv_values.size == 0 or np.all(pv_values == pv_values[0]))
            if pv_frozen:
                return self._make_inconclusive(
                    bundle,
                    "dead_signal_constant_offset",
                    {
                        "mean_error": round(mean_error, 6),
                        "std_error": 0.0,
                        "sample_count": int(len(errors)),
                        "pv_frozen": True,
                    },
                )

        # U = PV 量程范围
        u = self._read_pv_range(bundle)
        if u <= 0:
            return self._make_inconclusive(bundle, "zero_pv_range")

        # 石化惯例辅助口径：带内时间占比（始终输出 details，供考核对标）
        band = band_ratio * u
        # P3-09c 新 profile：带内率分母=窗口减**已知被剔除暂态**的秒数
        # （契约 §2.1 带内率行——08c 只落对账字段，本卡完成算法语义）；
        # 未知段单列不缩分母；评价域为零则不可判定。legacy：合法点时长口径。
        band_in_rate = self._band_in_rate(errors, band, durations)
        evaluation_domain_zero = False
        if self.new_semantics and view is not None and view.window_seconds > 0:
            denom_s = view.window_seconds - (sp_excluded_seconds or 0.0)
            in_band = np.abs(errors) <= band
            num_s = (
                float(np.sum(np.asarray(durations)[in_band]))
                if durations and len(durations) == len(errors)
                else float(np.sum(in_band))
            )
            if denom_s > 0:
                band_in_rate = num_s / denom_s * 100.0
            else:
                # 评价域为零（整窗被剔除暂态覆盖）→ 带内率不可判定
                band_in_rate = 0.0
                evaluation_domain_zero = True

        # P3-08c 时间契约对账字段：带内率的分子/分母秒数
        # 新 profile：契约口径（分母=窗口−已知剔除暂态）；legacy：合法点口径
        band_in_numerator_s: float | None = None
        band_in_denominator_s: float | None = None
        unknown_seconds: float | None = None
        if durations and len(durations) == len(errors):
            in_band = np.abs(errors) <= band
            weights = np.array(durations, dtype=float)
            band_in_numerator_s = round(float(np.sum(weights[in_band])), 3)
            band_in_denominator_s = (
                round(view.window_seconds - (sp_excluded_seconds or 0.0), 3)
                if self.new_semantics and view is not None
                else round(float(np.sum(weights)), 3)
            )
        if view is not None:
            unknown_seconds = round(
                min(
                    view.window_seconds,
                    view.uncovered_window_seconds
                    + sum(
                        view.slot_seconds[i]
                        for i in range(len(view.indices))
                        if not view.roles_known(i)
                    ),
                ),
                3,
            )

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
            "band_in_numerator_seconds": band_in_numerator_s,
            "band_in_denominator_seconds": band_in_denominator_s,
            # 分母口径说明：legacy=合法点时长减 SP 暂态剔除；新 profile=
            # 完整窗口秒数减已知被剔除暂态秒数（未知段单列不缩分母，契约 §2.1）
            "band_in_denominator_basis": (
                "WINDOW_MINUS_KNOWN_EXCLUDED_TRANSIENTS"
                if self.new_semantics and view is not None
                else "LEGAL_POINTS_MINUS_EXCLUDED_TRANSIENTS"
            ),
            "unknown_seconds": unknown_seconds,
        }
        if self.new_semantics:
            base_details["sp_tracking_window_source"] = sp_window_source
            if sp_excluded_seconds is not None:
                base_details["sp_excluded_transient_seconds"] = sp_excluded_seconds

        # 石化惯例分值模式：带内时间占比直接作为分值（不乘振荡修正）
        # P3-09c 新 profile：评价域为零 → 不可判定（不硬造 0 分/满分）
        if band_in_score:
            if evaluation_domain_zero:
                return self._make_inconclusive(
                    bundle,
                    "empty_evaluation_domain",
                    {**base_details, "assessmentState": "UNASSESSABLE"},
                )
            stability = self._clamp(band_in_rate)
            logger.debug(
                "[稳定率] 石化惯例口径: band=%.4f, band_in_rate=%.2f",
                band,
                stability,
            )
            return self._make_result(
                bundle,
                stability,
                {
                    **base_details,
                    "score_mode": "band_in",
                    **({"assessmentState": "COMPLETE"} if self.new_semantics else {}),
                },
            )

        # 振荡率（0~1）
        # P0 修复：仅在判定振荡（is_oscillating=True）时应用 (1-Osc) 修正；
        # 未判振荡时振荡率仅为相似率连续值（<阈值），不作为扣减因子，
        # 避免非振荡回路被误扣；振荡率数值仍透传 details 供展示。
        # P3-09c 新 profile（C07-EA5）：Osc 缺失/失败不默认为 0——指数 S 的
        # 完整值不可判定，保留可复算的基础波动分（exp(−σ/(dU))×100，不乘
        # 振荡修正），assessmentState=PARTIAL + oscDependency 可查；合法 0
        # （充分数据评估的非振荡）按 COMPLETE 处理。
        osc_result = self.dependencies.get("oscillation_rate")
        osc_rate_pct = osc_result.value if osc_result and osc_result.value is not None else 0.0
        is_oscillating = (
            bool(osc_result.details.get("is_oscillating", True)) if osc_result else True
        )
        osc_factor = 1.0 - (osc_rate_pct / 100.0) if is_oscillating else 1.0
        osc_dependency: dict | None = None
        if self.new_semantics:
            if osc_result is None:
                osc_dependency = {"state": "MISSING", "reason": "oscillation_rate_not_computed"}
            elif osc_result.value is None:
                osc_dependency = {
                    "state": "UNAVAILABLE",
                    "reason": (osc_result.details or {}).get("reason"),
                }
            if osc_dependency is not None:
                # 基础波动分：不乘振荡修正（修正不可知），S 整体 PARTIAL
                base_fluctuation = self._clamp(math.exp(-(std_error / (decay_ratio * u))) * 100.0)
                logger.debug(
                    "[稳定率] Osc %s：基础波动分 %.2f（PARTIAL）",
                    osc_dependency["state"],
                    base_fluctuation,
                )
                return self._make_result(
                    bundle,
                    base_fluctuation,
                    {
                        **base_details,
                        "score_mode": "exponential",
                        "decay_ratio": decay_ratio,
                        "normalized_std": round(std_error / (decay_ratio * u), 4),
                        "oscillation_rate": None,
                        "osc_factor": 1.0,
                        "oscDependency": osc_dependency,
                        "assessmentState": "PARTIAL",
                        "note": "指数 S 完整值不可判定（Osc 缺失/失败），保留基础波动分",
                    },
                )

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
                    **({"assessmentState": "COMPLETE"} if self.new_semantics else {}),
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
                **({"assessmentState": "COMPLETE"} if self.new_semantics else {}),
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
