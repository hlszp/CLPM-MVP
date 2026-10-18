"""快速率计算器（算法说明 §4.5）.

公式（分段，阈值 T_th = T'×ratio×(1+tol)，默认 ratio=1.0/tol=0.0 时 T_th=T'）：
    F = 100%                          当 T ≤ T_th
    F = 1/e^((T-T_th)/T') × 100%      当 T > T_th

默认 profile 下 T_th = T'，退化为算法说明 §4.5 原式：
    F = 100%                          当 T ≤ T'
    F = 1/e^((T-T')/T') × 100%        当 T > T'

其中：
    T：实际稳态时间（秒，由 settling_time 计算器提供）
    T'：理想稳态时间（秒，由 ideal_settling_time 计算器提供）

设计依据：算法说明 §4.5；GB/T 44693.2-2024 附录 B.4

P0-1：settling_time 三语义分流——already_stable（真已稳态）保持 100 分；
never_settles（窗口内不衰减）以 Green 函数窗口长度作为 actual_t 代入
指数衰减公式（不再误判满分）；identification_failed（辨识失败）返回
INCONCLUSIVE。

STD-02（P1-01）：非默认容差/比率下衰减指数锚定阈值，保证边界连续
（见 calculate 内注释）；分段 profile 与默认数值不变。

定位：核心质量指标，参与综合评分加权。
依赖：settling_time（实际稳态时间）、ideal_settling_time（理想稳态时间）。
"""

from __future__ import annotations

import logging
import math
from typing import Any

import numpy as np

from app.contracts.data_types import MetricDataBundle, MetricResult
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

# ---------------------------------------------------------------------------
# P3-09a：静稳 F 参考分（10§3.6.1，EA-D02 采纳口径）
#
# 正式动态 fast_rate.value 缺动态证据时仍为 null（不虚构 T=0 秒）；对满足
# 静稳参考资格的窗口另存 details.referenceScore=100（管理统计值，不是测量
# 值/置信概率/真实响应速度）。正式指标 assessmentState=UNASSESSABLE，参考
# 值资格单独记录，不因 referenceScore 非空变 COMPLETE。
#
# 参考资格（任一不满足即不发参考 100——缺数据、算法异常、配置失效、未
# 收敛、尾段不足均不套用）：
#   - 数据质量：valid_rate ≥ reference_min_valid_rate；
#   - 连续观测：样本数 ≥ reference_min_samples；时间视图可用时
#     coverageRatio ≥ reference_min_coverage（未知不补）；
#   - 单位量程：pv_range > 0；
#   - 冻结噪声：σ(E) ≤ reference_noise_floor_ratio × U（与 settling 噪声底
#     同源）；
#   - SP 平稳：max(SP)−min(SP) ≤ reference_sp_range_ratio × U（超门槛 SP
#     事件/斜坡直接拒）；
#   - PV 趋势：|线性斜率|×时长 ≤ reference_trend_ratio × U（SP/PV 同步
#     爬坡即使误差恒定也不满足）；
#   - PV 冻结反证：PV 一字不变且恒偏置（pv≠sp）→ 死信号，不发参考；
#     PV=SP 完美贴死保持参考资格（与零偏差语义一致，无法与真完美区分）；
#   - OP/MODE 激励：信号在 bundle 内时检查（OP 行程超限/模式变化 → 拒）；
#     不在 bundle 内时如实记录 NOT_IN_BUNDLE（不冒称已检查，也不据此
#     发/拒——PV/SP 冻结噪声主判定已覆盖绝大多数激励场景）。
#
# 互斥与优先：具备完整动态证据时用实际公式得正式 F（含真实 0 分），不叠加
# 参考 100；两种资格互斥（实现上参考字段仅在 value=None 分支产生）。
# ---------------------------------------------------------------------------
#: 参考政策标识（进入快照与复现比较）
REFERENCE_METHOD = "STATIC_WINDOW_POLICY"
REFERENCE_POLICY = "STATIC_BASELINE_100_V1"

#: 静稳参考资格参数默认值（EA-D02 采纳口径内登记；均与归一化信号同量纲）
_DEFAULT_REF_MIN_VALID_RATE = 0.95
_DEFAULT_REF_MIN_SAMPLES = 100
_DEFAULT_REF_MIN_COVERAGE = 0.95
_DEFAULT_REF_NOISE_FLOOR_RATIO = 0.001
_DEFAULT_REF_SP_RANGE_RATIO = 0.001
_DEFAULT_REF_TREND_RATIO = 0.001
_DEFAULT_REF_OP_RANGE_RATIO = 0.001


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
        # P3-09a 新 profile：状态矩阵 + 静稳参考分语义（legacy 分支零变化）
        if self.new_semantics:
            return self._calculate_new_profile(bundle)

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
        # G2-INC-a：任务固定五层有效参数优先，未固定才读配置链当前缓存
        params = self._effective_params("fast_rate", bundle)
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

        # 实际稳态时间无效（settling_time 返回 INCONCLUSIVE）→ 按 reason 三语义分流（P0-1）：
        # - never_settles：窗口内不衰减（持续振荡/近单位根），以 Green 函数窗口长度
        #   作为 actual_t 代入现有指数衰减公式得分（不再误判满分）
        # - identification_failed / insufficient_data 等：快速率同样 INCONCLUSIVE
        # 扰动覆盖时跳过（扰动恢复时间是独立有效度量，不依赖 ARMA 辨识）
        never_settles = False
        if (
            not disturbance_override
            and settling_result is not None
            and settling_result.value is None
        ):
            settling_reason = (settling_result.details or {}).get("reason")
            if settling_reason == "never_settles":
                window_t = self._extract_settling_time(settling_result)
                if window_t > 0:
                    never_settles = True
                    actual_t = window_t
                    logger.debug(
                        "[快速率] settling_time=never_settles，以窗口长度 %.1f 秒代入衰减公式",
                        actual_t,
                    )
                else:
                    # details 缺少窗口长度，无法代入公式，按辨识失败处理
                    return self._make_inconclusive(
                        bundle,
                        "identification_failed",
                        {
                            "ideal_settling_time": round(ideal_t, 2),
                            **disturbance_details,
                        },
                    )
            else:
                return self._make_inconclusive(
                    bundle,
                    settling_reason or "settling_time_inconclusive",
                    {
                        "actual_settling_time": actual_t,
                        "ideal_settling_time": round(ideal_t, 2),
                        **disturbance_details,
                    },
                )

        # never_settles 标记（写入 details，注明得分依据为窗口长度下界）
        never_settles_detail: dict[str, Any] = {"reason": "never_settles"} if never_settles else {}

        # 实际稳态时间 ≤ 0（已稳态）→ 快速率 100%
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
                    "fast_threshold": round(fast_threshold, 2),
                    "ratio": round(actual_t / ideal_t, 4),
                    **disturbance_details,
                    **never_settles_detail,
                },
            )

        # T > 阈值 → F = 1/e^((T-阈值)/T') × 100
        # 用 e^(-x) 等价形式：never_settles 时 actual_t 可达 Green 窗口上限
        # （3600×采样间隔），ratio 可 >709，直接 math.exp(ratio) 会 OverflowError
        # 被上游宽 except 吞掉 → fast_rate 缺失 → 整回路 composite=INCONCLUSIVE。
        # 与 stability.py 同口径（该处已注明同一原因）。
        # STD-02 修复（P1-01）：衰减指数以 fast_threshold 为锚点，消除非默认
        # 容差/比率下阈值处 100 → 衰减值的不连续跳变（容差 0.2 时阈值右侧
        # 一步从 100 跳到 e^-0.2×100≈81.87）。默认 profile（ratio=1.0、
        # tolerance=0.0）下 fast_threshold == ideal_t，公式与冻结口径
        # e^−((T−T′)/T′)×100 完全一致，数值零回归；分段结构（满分区+指数
        # 衰减）不变，仅在阈值处连续。
        ratio = (actual_t - fast_threshold) / ideal_t
        fast_rate = math.exp(-ratio) * 100.0
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
                "fast_threshold": round(fast_threshold, 2),
                "ratio": round(ratio, 4),
                **disturbance_details,
                **never_settles_detail,
            },
        )

    # ------------------------------------------------------------------
    # P3-09a：新 profile（CANDIDATE_V3_RC1）状态矩阵
    # ------------------------------------------------------------------

    def _calculate_new_profile(self, bundle: MetricDataBundle) -> MetricResult:
        """新 profile 快速率：静稳/不足/估计/未收敛状态矩阵（10§3.3 表）.

        状态矩阵（与 settling_time 三语义 + 09b 尾段状态对齐）：
        - already_stable（当前平稳）→ 正式 F=null + 参考资格评估（合格 →
          referenceScore=100；不合格 → 无参考，原因可查）
        - never_settles（未收敛）→ 正式 F=null + 模型衰减时间下界/F 上界
          details（下界不冒充实测 T，不据此满分）
        - insufficient_tail_support（尾段不足/观察不足）→ F=null + 候选时刻
        - identification_failed / 其余 → F=null，无参考（算法失败不套 100）
        - settled / 扰动覆盖 → 正式 F（分段公式，method=error_ar_decay /
          disturbance_recovery；真实 0 分优先于任何参考语义）
        """
        settling_result = self.dependencies.get("settling_time")
        ideal_result = self.dependencies.get("ideal_settling_time")
        ideal_t = ideal_result.value if ideal_result else None
        settling_details: dict[str, Any] = (
            dict(settling_result.details or {}) if settling_result else {}
        )

        # 无效 T′（配置失效，含非法 T′）→ UNASSESSABLE，不套参考 100（C07-EA6）
        if ideal_t is None or ideal_t <= 0:
            return self._make_unassessable(
                bundle,
                "invalid_ideal_settling_time",
                {
                    "ideal_settling_time": ideal_t,
                    "settling_reason": settling_details.get("reason"),
                },
            )

        params = self._effective_params("fast_rate", bundle)
        ideal_settling_ratio = float(
            params.get("ideal_settling_ratio", _DEFAULT_IDEAL_SETTLING_RATIO)
        )
        settling_tolerance = float(params.get("settling_tolerance", _DEFAULT_SETTLING_TOLERANCE))
        fast_threshold = ideal_t * ideal_settling_ratio * (1.0 + settling_tolerance)

        # P2 抗扰扰动覆盖：独立有效的动态估计（正式值，method=disturbance_recovery）
        disturbance_details: dict[str, Any] = {"source": "arma"}
        actual_t = self._extract_settling_time(settling_result)
        anti_enabled = bool(
            params.get("anti_disturbance_enabled", _DEFAULT_ANTI_DISTURBANCE_ENABLED)
        )
        disturbance_override = False
        if anti_enabled:
            disturbance_details, disturbance_override, actual_t = self._run_disturbance_analysis(
                bundle, ideal_t, params, actual_t
            )

        if not disturbance_override:
            reason = settling_details.get("reason")
            if reason == "already_stable":
                return self._static_reference_outcome(bundle, params, settling_details, ideal_t)
            if reason == "never_settles":
                # 未收敛：模型衰减时间下界（Green 窗口）≠ 实测恢复时间；
                # F 给对应上界 details，点估计与 P 不可完整评估（C07-EA3）
                lower = settling_details.get("settling_lower_bound_seconds")
                if lower is None:
                    lower = settling_details.get("actual_settling_time")
                extra: dict[str, Any] = {
                    "ideal_settling_time": round(ideal_t, 2),
                    "fast_threshold": round(fast_threshold, 2),
                    "note": "模型推演时域内未收敛；下界非实测恢复时间",
                    **disturbance_details,
                }
                if lower is not None and float(lower) > 0:
                    lower_f = float(lower)
                    extra["settling_lower_bound_seconds"] = round(lower_f, 2)
                    upper = (
                        math.exp(-(lower_f - fast_threshold) / ideal_t) * 100.0
                        if lower_f > fast_threshold
                        else 100.0
                    )
                    extra["fast_rate_upper_bound"] = round(self._clamp(upper), 2)
                return self._make_unassessable(bundle, "never_settles", extra)
            if reason == "insufficient_tail_support":
                return self._make_unassessable(
                    bundle,
                    "insufficient_tail_support",
                    {
                        "ideal_settling_time": round(ideal_t, 2),
                        "note": "尾段支持不足，观察不足非算法失败",
                        **{
                            k: settling_details[k]
                            for k in (
                                "candidate_settling_time",
                                "tail_seconds",
                                "required_tail_seconds",
                                "pole_period_seconds",
                                "observationSeconds",
                                "simulationHorizonSeconds",
                            )
                            if k in settling_details
                        },
                        **disturbance_details,
                    },
                )
            # settled（正式 T 有效）→ 正式动态 F 路径（真实 0 分有效，含 0 分）
            if settling_result is not None and settling_result.value is not None:
                return self._formal_fast_rate(
                    bundle,
                    actual_t,
                    ideal_t,
                    fast_threshold,
                    {**disturbance_details, "method": "error_ar_decay"},
                )
            # identification_failed / insufficient_data / 缺依赖等：无参考
            return self._make_unassessable(
                bundle,
                reason or "settling_time_inconclusive",
                {
                    "ideal_settling_time": round(ideal_t, 2),
                    **disturbance_details,
                },
            )

        # 正式动态 F：扰动覆盖（独立有效动态估计，method=disturbance_recovery）
        return self._formal_fast_rate(
            bundle,
            actual_t,
            ideal_t,
            fast_threshold,
            {**disturbance_details, "method": "disturbance_recovery"},
        )

    def _formal_fast_rate(
        self,
        bundle: MetricDataBundle,
        actual_t: float,
        ideal_t: float,
        fast_threshold: float,
        extra_details: dict[str, Any],
    ) -> MetricResult:
        """正式动态 F（分段映射：T≤阈值满分，否则指数衰减；STD-02 连续锚点）."""
        base = {
            "actual_settling_time": round(actual_t, 2),
            "ideal_settling_time": round(ideal_t, 2),
            "fast_threshold": round(fast_threshold, 2),
            "assessmentState": "COMPLETE",
            **extra_details,
        }
        if actual_t <= 0 or actual_t <= fast_threshold:
            return self._make_result(
                bundle,
                100.0,
                {**base, "ratio": round(actual_t / ideal_t, 4) if ideal_t > 0 else None},
            )
        ratio = (actual_t - fast_threshold) / ideal_t
        fast_rate = self._clamp(math.exp(-ratio) * 100.0)
        return self._make_result(bundle, fast_rate, {**base, "ratio": round(ratio, 4)})

    def _static_reference_outcome(
        self,
        bundle: MetricDataBundle,
        params: dict[str, Any],
        settling_details: dict[str, Any],
        ideal_t: float,
    ) -> MetricResult:
        """静稳分支：参考资格评估 → 参考 100（合格）或无参考（不合格）."""
        eligible, checks = self._evaluate_static_reference(bundle, params)
        # 私有键（_failed/_deviation/_window_seconds）不进 referenceChecks
        checks_public = {k: v for k, v in checks.items() if not k.startswith("_")}
        failed: list[str] = checks["_failed"]
        window_seconds = checks.get("_window_seconds")
        if window_seconds is None:
            ts = self._get_masked_timestamps(bundle)
            try:
                window_seconds = (
                    round((ts[-1] - ts[0]).total_seconds(), 2) if len(ts) >= 2 else None
                )
            except (AttributeError, TypeError):
                window_seconds = None
        common = {
            "currentlyStable": True,
            "ideal_settling_time": round(ideal_t, 2),
            "referenceChecks": checks_public,
            **{
                k: settling_details[k]
                for k in ("observationSeconds", "simulationHorizonSeconds", "std", "noise_floor")
                if k in settling_details
            },
        }
        if not eligible:
            # 缺数据/斜坡/趋势/冻结/覆盖不足等：不套参考 100，原因可查
            return self._make_unassessable(
                bundle,
                "static_reference_ineligible",
                {**common, "ineligibleReasons": failed},
            )
        # 合格静稳参考：referenceScore=100（管理统计约定，非测量值）；
        # 恒定偏置时同存偏差提示（平稳≠偏差合格）
        deviation = checks.get("_deviation")
        return self._make_unassessable(
            bundle,
            "NO_DYNAMIC_EVIDENCE",
            {
                **common,
                "referenceScore": 100.0,
                "referenceMethod": REFERENCE_METHOD,
                "referencePolicy": REFERENCE_POLICY,
                "evidenceStatus": "INSUFFICIENT_DYNAMIC_EVIDENCE",
                "reasonCode": "NO_DYNAMIC_EVIDENCE",
                "referencePeriodSeconds": window_seconds,
                **({"referenceDeviation": deviation} if deviation else {}),
            },
        )

    def _evaluate_static_reference(
        self, bundle: MetricDataBundle, params: dict[str, Any]
    ) -> tuple[bool, dict[str, Any]]:
        """静稳参考资格评估（纯输入检查，不读 settling 结果——资格独立可查）.

        Returns:
            (eligible, checks)：checks 为逐项检查结果（含 _failed 汇总与
            _window_seconds/_deviation 内部字段），逐项可追溯。
        """
        pairs = self._get_masked_pair(bundle, "pv", "sp")
        n = len(pairs)
        checks: dict[str, Any] = {"sampleCount": n}
        failed: list[str] = []

        def _flag(name: str, ok: bool, info: dict[str, Any] | None = None) -> None:
            checks[name] = "PASS" if ok else "FAIL"
            if info:
                checks.update(info)
            if not ok:
                failed.append(name)

        # 数据质量：valid_rate（mask 有效点占比）
        vr = self._get_valid_rate(bundle)
        min_vr = float(params.get("reference_min_valid_rate", _DEFAULT_REF_MIN_VALID_RATE))
        _flag("data_quality", vr >= min_vr, {"validRate": round(vr, 4)})

        # 连续观测样本
        min_samples = int(params.get("reference_min_samples", _DEFAULT_REF_MIN_SAMPLES))
        _flag("observation_samples", n >= min_samples)

        # 时间轴覆盖：视图可用时按 coverageRatio（未知不补）；不可用时记录
        try:
            from app.services.metric_calculator.time_axis import view_or_none

            view = view_or_none(bundle, ("pv", "sp"))
        except Exception:  # noqa: BLE001 — 视图失败按不可用处理
            view = None
        if view is not None:
            min_cov = float(params.get("reference_min_coverage", _DEFAULT_REF_MIN_COVERAGE))
            cov = view.coverage_ratio
            checks["timeCoverage"] = {
                "status": "PASS" if cov is not None and cov >= min_cov else "FAIL",
                "coverageRatio": round(cov, 4) if cov is not None else None,
                "unknownSeconds": round(view.window_seconds - (cov or 0.0) * view.window_seconds, 3)
                if cov is not None
                else None,
            }
            if cov is None or cov < min_cov:
                failed.append("timeCoverage")
            checks["_window_seconds"] = round(view.window_seconds, 2)
        else:
            checks["timeCoverage"] = {"status": "TIME_VIEW_UNAVAILABLE"}
            checks["_window_seconds"] = None

        # 单位量程
        u = MetricCalculatorBase._read_config_scalar(bundle.data_block.signals, "pv_range")
        try:
            u = float(u) if u is not None else 0.0
        except (TypeError, ValueError):
            u = 0.0
        _flag("pv_range_available", u > 0, {"pvRange": u})

        pv = np.array([float(p) for p, _ in pairs], dtype=float)
        sp = np.array([float(s) for _, s in pairs], dtype=float)
        errors = pv - sp
        mean_error = float(np.mean(errors)) if n else 0.0

        # 冻结噪声：σ(E) ≤ ratio×U
        noise_ratio = float(
            params.get("reference_noise_floor_ratio", _DEFAULT_REF_NOISE_FLOOR_RATIO)
        )
        std_error = float(np.std(errors)) if n else 0.0
        _flag(
            "frozen_noise",
            u > 0 and std_error <= noise_ratio * u,
            {"stdError": round(std_error, 6), "noiseFloor": round(noise_ratio * u, 6)},
        )

        # SP 平稳：max−min ≤ ratio×U（SP 事件/爬坡直接拒）
        sp_range_ratio = float(params.get("reference_sp_range_ratio", _DEFAULT_REF_SP_RANGE_RATIO))
        sp_range = float(np.max(sp) - np.min(sp)) if n else 0.0
        _flag(
            "sp_stationary",
            u > 0 and sp_range <= sp_range_ratio * u,
            {"spRange": round(sp_range, 6), "limit": round(sp_range_ratio * u, 6)},
        )

        # PV 趋势：|线性斜率|×时长 ≤ ratio×U（SP/PV 同步斜坡不满足）
        trend_ratio = float(params.get("reference_trend_ratio", _DEFAULT_REF_TREND_RATIO))
        ts = self._get_masked_timestamps(bundle)
        drift_span = 0.0
        if n >= 2 and len(ts) == n:
            try:
                secs = np.array([(t - ts[0]).total_seconds() for t in ts], dtype=float)
                slope = float(np.polyfit(secs, pv, 1)[0])
                drift_span = abs(slope) * float(secs[-1] - secs[0])
            except (AttributeError, TypeError, ValueError, np.linalg.LinAlgError):
                drift_span = 0.0
        _flag(
            "pv_trend",
            u > 0 and drift_span <= trend_ratio * u,
            {"pvDriftSpan": round(drift_span, 6), "limit": round(trend_ratio * u, 6)},
        )

        # PV 冻结观察（10§3.6.1"恒值本身不能证明仪表正常"）：PV 一字不变时
        # **不阻断**参考资格（持续恒定偏置若其他条件成立可保留政策参考
        # 100），但保留仪表未确认提示——不能以恒值确定仪表无故障；
        # 反证（已确认质量/仪表故障）阻断属 P3-10b/08d 读端消费
        pv_frozen = n > 0 and bool(np.all(pv == pv[0]))
        checks["pvFrozen"] = pv_frozen
        if pv_frozen:
            checks["pvFrozenInstrumentHint"] = (
                "PV 恒值不能证明仪表正常：参考分基于静稳规则，仪表故障未确认"
            )

        # OP/MODE 激励：信号在 bundle 内时检查；不在则如实记录
        op_sig = bundle.data_block.signals.get("op")
        op_arr = (
            np.array(
                [float(op_sig[i]) for i in bundle.masked_indices if i < len(op_sig)],
                dtype=float,
            )
            if op_sig
            else None
        )
        if op_arr is not None and len(op_arr) == n and n > 0:
            op_range_ratio = float(
                params.get("reference_op_range_ratio", _DEFAULT_REF_OP_RANGE_RATIO)
            )
            op_range = float(np.max(op_arr) - np.min(op_arr))
            _flag(
                "op_no_excitation",
                op_range <= op_range_ratio * 100.0,
                {"opRange": round(op_range, 6)},
            )
        else:
            checks["op_no_excitation"] = "NOT_IN_BUNDLE"
        mode_sig = bundle.data_block.signals.get("mode")
        if mode_sig and len(mode_sig) >= n and n > 0:
            mode_vals = {mode_sig[i] for i in bundle.masked_indices if i < len(mode_sig)}
            _flag("mode_no_change", len(mode_vals) <= 1)
        else:
            checks["mode_no_change"] = "NOT_IN_BUNDLE"

        # 恒定偏置：其他条件成立时保留参考但同显偏差（平稳≠偏差合格）
        checks["_deviation"] = None
        if u > 0 and abs(mean_error) > noise_ratio * u:
            checks["_deviation"] = {
                "meanError": round(mean_error, 6),
                "meanErrorRatio": round(abs(mean_error) / u, 6),
                "note": "持续恒定偏置：政策参考保留，但平稳≠偏差合格，A/R 另见",
            }

        checks["_failed"] = failed
        return (not failed), checks

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
