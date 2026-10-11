"""振荡率计算器（算法说明 §4.6）.

基于控制偏差的 IAE（积分绝对误差）零交叉规律性检测振荡。
对齐 GB/T 44693.2-2024 附录 F.1，结合 Hägglund (2005) 的 IAE 方法。

算法步骤：
    1. 计算控制偏差 E = PV - SP
    2. 识别零交叉点（偏差符号变化时刻）
    3. 计算相邻零交叉间的 IAE（积分绝对误差）
    4. 分别对正值段/负值段计算 IAE 相似率 S_A/S_B（最小距离法）
    5. 分别对正值段/负值段计算持续时间相似率 S_TA/S_TB（同一算法）
    6. 振荡率 = min(S_A, S_B) × 100；is_oscillating = S_A>=τ AND S_B>=τ

设计依据：算法说明 §4.6；GB/T 44693.2-2024 附录 F.1

定位：辅助诊断指标，用于稳定率修正和振荡诊断。

P2 #33 偏差2 修正：移除 _crossing_regularity（CV 变异系数），
按设计文档 §4.6.2 步骤 5 计算持续时间相似率 S_TA/S_TB（与 S_A/S_B 同算法），
振荡判定仅依赖 S_A/S_B（设计文档伪代码 line 19-22），S_TA/S_TB 作为辅助诊断输出。

P1 修正（对齐设计文档的工程加固）：
- 半周期抗噪门控：平均半周期 < min_half_period_samples（默认 8 采样点）
  判非振荡，与诊断侧 _iae_kernel 同口径，剔除白噪声/高频伪穿越；
- 振荡周期单位：按零交叉点时间戳换算为秒（旧实现输出采样点数，
  仅 1s 采样时数值巧合正确）。

P2 修正（可选门控，经配置链启用/调参）：
- 幅度门控：特征幅度（段 IAE/段时长秒的均值）< min_amplitude_ratio×U
  （默认量程 0.2%）判非振荡，剔除噪声带内规则微振荡；
- SP 阶跃剔除：sp_step_exclusion_enabled（默认开启，2026-08-28 起与
  stability 同步默认打开）剔除 SP 阶跃跟踪窗内的点（同款
  detect_sp_tracking_windows），避免阶跃暂态同型段被误判为振荡；
  需计入阶跃暂态考核时可经配置链显式关闭。

P3 修正：IAE = Σ|E_i|·Δt_i（对齐设计文档 ∫|E|dt），非均匀采样下
短/长采样段按真实时长计权；均匀 1s 采样与旧实现逐位一致。
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import numpy as np

from app.contracts.data_types import MetricDataBundle, MetricResult
from app.services.metric_calculator.base import MetricCalculatorBase
from app.services.metric_calculator.disturbance import detect_sp_tracking_windows

logger = logging.getLogger(__name__)

#: 振荡判定相似率阈值（配置缺失时的算法回退默认值，与 algorithm_config._DEFAULTS 一致）
SIMILARITY_THRESHOLD = 0.4

#: 最少零交叉点数（至少 2 个完整周期）
MIN_ZERO_CROSSINGS = 4

#: 相似率过滤比率的算法回退默认值（与 algorithm_config._DEFAULTS 一致）
_DEFAULT_MIN_RATIO = 0.05
_DEFAULT_MAX_RATIO = 15.0

#: 抗噪门控：最小平均半周期（采样点数，与 algorithm_config._DEFAULTS 一致）。
#: 白噪声伪穿越的 IAE 相似率可达 0.9+（合规验证报告实测 0.962），单靠相似率
#: 无法区分噪声与真实振荡；真实振荡半周期为数十个采样点，下限门控可稳健
#: 剔除高频伪振荡。取值与 stiction.MIN_HALF_PERIOD_SAMPLES / 诊断算子
#: threshold_schema 保持一致（stiction 已 import 本模块，反向 import 会循环）。
_DEFAULT_MIN_HALF_PERIOD_SAMPLES = 8.0

#: P2 幅度门控：最小特征幅度（量程比例）。特征幅度 = 各完整半周期段
#: IAE/时长（采样点）的均值；IAE 相似率法不看幅度，量程 0.2% 以下的
#: 规则微振荡与测量噪声不可区分，低于下限判非振荡。
_DEFAULT_MIN_AMPLITUDE_RATIO = 0.002

#: P2 SP 阶跃剔除默认参数（与 stability 同款，默认关闭零回归）
_DEFAULT_SP_STEP_SIGMA = 3.0
_DEFAULT_SP_TRACKING_WINDOW = 60

#: PV 量程归一化默认值（signals 无 pv_range CONFIG 时使用，与 stability 一致）
_DEFAULT_PV_RANGE = 100.0

#: P3-09c：SP 暂态剔除窗的物理秒默认值（10§3.3"暂态剔除改为可追溯的真实
#: 秒数"；60 点×1s 的兼容迁移初值=60s，来源登记 legacy_points_conversion）
_DEFAULT_SP_TRACKING_WINDOW_SECONDS = 60.0


class OscillationRateCalculator(MetricCalculatorBase):
    """振荡率计算器（算法说明 §4.6）.

    基于 IAE 零交叉相似率法检测振荡。
    振荡率用于稳定率修正：S = 1/e^(σ/0.05U) × (1-Osc) × 100。
    """

    @property
    def metric_code(self) -> str:
        return "oscillation_rate"

    def calculate(self, bundle: MetricDataBundle) -> MetricResult:
        """计算振荡率.

        Args:
            bundle: 指标数据包（需含 pv/sp 信号，mask 为 pv_valid && sp_valid）

        Returns:
            MetricResult：value 为振荡率 0~100，
            details 中含 is_oscillating/oscillation_period
        """
        pairs = self._get_masked_pair(bundle, "pv", "sp")
        n = len(pairs)

        logger.debug("[振荡率] 输入: masked_points=%d", n)

        if n < 4:
            return self._make_inconclusive(
                bundle,
                "insufficient_data",
                {
                    "is_oscillating": False,
                    "oscillation_period": 0.0,
                    "sample_count": n,
                    **({"assessmentState": "UNASSESSABLE"} if self.new_semantics else {}),
                },
            )

        # P0-B: 从配置链读取算法参数（control_type 为响应类别 STABLE/SLOW/FAST/LOGIC）
        # G2-INC-a：任务固定五层有效参数优先，未固定才读配置链当前缓存
        params = self._effective_params("oscillation_rate", bundle)
        similarity_threshold = float(params.get("similarity_threshold", SIMILARITY_THRESHOLD))
        min_ratio = float(params.get("min_ratio", _DEFAULT_MIN_RATIO))
        max_ratio = float(params.get("max_ratio", _DEFAULT_MAX_RATIO))
        # 整改 F2：零交叉下限纳入配置链
        min_zero_crossings = int(params.get("min_zero_crossings", MIN_ZERO_CROSSINGS))
        # P1：抗噪门控下限纳入配置链（与诊断侧 _iae_kernel 同口径）
        min_half_period = float(
            params.get("min_half_period_samples", _DEFAULT_MIN_HALF_PERIOD_SAMPLES)
        )
        # P2：幅度门控下限（特征幅度占量程比例）
        min_amplitude_ratio = float(params.get("min_amplitude_ratio", _DEFAULT_MIN_AMPLITUDE_RATIO))
        # P2：SP 阶跃剔除（默认开启，与 stability 同步；口径见模块 docstring）
        sp_exclusion = bool(params.get("sp_step_exclusion_enabled", True))
        sp_step_sigma = float(params.get("sp_step_sigma", _DEFAULT_SP_STEP_SIGMA))
        sp_window = int(params.get("sp_tracking_window", _DEFAULT_SP_TRACKING_WINDOW))

        errors = np.array([float(pv) - float(sp) for pv, sp in pairs], dtype=float)

        # P2 SP 阶跃剔除：剔除阶跃后跟踪窗内的点，跟踪暂态不入零交叉/IAE 判定
        # （SP 多次阶跃的暂态段同型 → IAE 相似率 1.0，会被误判为"振荡"）
        # P3-09c 新 profile：跟踪窗改物理秒（sp_tracking_window_seconds，
        # 默认 60s——旧 60 点 × 声明周期的兼容迁移初值）；剔除边界形成段
        # 边界，零交叉/IAE 半周期段不跨剔除区拼接（C14-EA1"跨剔除边界不拼
        # 周期"）；剔除秒数可追溯（excluded_transient_seconds）。
        sp_steps = 0
        sp_excluded = 0
        sp_excluded_seconds: float | None = None
        sp_window_source = "legacy_points"
        ts_aligned = self._get_masked_timestamps(bundle)
        if len(ts_aligned) != n:
            ts_aligned = []
        # P3：IAE 时间计权（零阶保持点时长，与 errors 对齐；时间戳不可用时
        # 退回按采样点计权，均匀 1s 采样下与旧实现逐位一致）
        point_durations = self._point_durations(ts_aligned) if ts_aligned else None
        # 保留段的连续区间（kept 空间索引对，新 profile 用于不跨剔除拼接）
        kept_runs: list[tuple[int, int]] = [(0, len(errors) - 1)] if len(errors) else []
        if sp_exclusion:
            sp_vals = [float(sp) for _, sp in pairs]
            interval = 1.0
            if ts_aligned:
                total = self._total_duration_seconds(ts_aligned)
                interval = total / (n - 1) if total > 0 and n > 1 else 1.0
            if self.new_semantics:
                window_seconds = float(
                    params.get(
                        "sp_tracking_window_seconds",
                        _DEFAULT_SP_TRACKING_WINDOW_SECONDS,
                    )
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
                    sample_interval=interval,
                    sp_step_sigma=sp_step_sigma,
                    window_points=sp_window,
                )
            sp_steps = sum(1 for i in range(1, n) if tracking[i] and not tracking[i - 1])
            sp_excluded = sum(tracking)
            if sp_excluded:
                keep = np.array([not t for t in tracking], dtype=bool)
                # 保留段连续区间（原始空间 → kept 空间）：剔除区两侧不拼接
                kept_runs = []
                run_start: int | None = None
                kept_idx = 0
                for i in range(n):
                    if keep[i]:
                        if run_start is None:
                            run_start = kept_idx
                        kept_idx += 1
                    elif run_start is not None:
                        kept_runs.append((run_start, kept_idx - 1))
                        run_start = None
                if run_start is not None:
                    kept_runs.append((run_start, kept_idx - 1))
                # 剔除秒数（可追溯真实秒）：被剔除点的时长和（有 Δt 时），
                # 无 Δt 回退点数 × 平均间隔并记录口径
                if point_durations is not None:
                    sp_excluded_seconds = round(
                        float(np.sum(np.asarray(point_durations)[~keep])), 3
                    )
                elif interval > 0:
                    sp_excluded_seconds = round(sp_excluded * interval, 3)
                errors = errors[keep]
                if ts_aligned:
                    ts_aligned = [t for t, k in zip(ts_aligned, keep, strict=True) if k]
                if point_durations is not None:
                    point_durations = [d for d, k in zip(point_durations, keep, strict=True) if k]
                logger.debug(
                    "[振荡率] SP 阶跃剔除: steps=%d, excluded=%d/%d, window=%s(%s)",
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
                )
                if len(errors) < 4:
                    return self._make_inconclusive(
                        bundle,
                        "insufficient_data_after_sp_exclusion",
                        {
                            "is_oscillating": False,
                            "oscillation_period": 0.0,
                            "sp_steps_detected": sp_steps,
                            "sp_excluded_points": sp_excluded,
                            **({"assessmentState": "UNASSESSABLE"} if self.new_semantics else {}),
                        },
                    )
        sp_info: dict[str, Any] = {
            "sp_steps_detected": sp_steps,
            "sp_excluded_points": sp_excluded,
        }
        if self.new_semantics:
            sp_info["sp_tracking_window_source"] = sp_window_source
            if sp_excluded_seconds is not None:
                sp_info["sp_excluded_transient_seconds"] = sp_excluded_seconds

        # P3-09c 偏置振荡补充证据（C14-EA2）：围绕 SP 的 IAE 零交叉法只反映
        # 自身定义；偏置正弦（如 5+2sin）不交叉零 → 原口径判非振荡属正确，
        # 补充证据识别"分离偏置/慢趋势后的周期性"——**仅辅助证据**，不改
        # is_oscillating/value，不直接扣 S（纳入 S 属后续版本裁决）。
        bias_evidence: dict[str, Any] | None = None
        if self.new_semantics:
            bias_evidence = self._bias_oscillation_evidence(
                errors,
                self._read_pv_range(bundle),
                min_half_period_samples=min_half_period,
                min_amplitude_ratio=min_amplitude_ratio,
                similarity_threshold=similarity_threshold,
                min_zero_crossings=min_zero_crossings,
            )
        bias_key = {"biasOscillation": bias_evidence} if bias_evidence is not None else {}

        # 步骤 2：识别零交叉点
        # P3-09c 新 profile：零交叉在**保留段内**分别识别（不跨剔除区拼接，
        # C14-EA1）；legacy：剔除后整段连续识别（既有行为）。
        if self.new_semantics and kept_runs:
            zero_crossings: list[int] = []
            for seg_a, seg_b in kept_runs:
                if seg_b - seg_a + 1 >= 2:
                    run_crossings = self._find_zero_crossings(errors[seg_a : seg_b + 1])
                    zero_crossings.extend(c + seg_a for c in run_crossings)
        else:
            zero_crossings = self._find_zero_crossings(errors)
        if len(zero_crossings) < min_zero_crossings:
            logger.debug(
                "[振荡率] 零交叉点 %d < %d，返回 0", len(zero_crossings), min_zero_crossings
            )
            return self._make_result(
                bundle,
                0.0,
                {
                    "is_oscillating": False,
                    "oscillation_period": 0.0,
                    "zero_crossings": len(zero_crossings),
                    **({"assessmentState": "COMPLETE"} if self.new_semantics else {}),
                    **bias_key,
                    **sp_info,
                },
            )

        # 步骤 3：计算相邻零交叉间的 IAE 与持续时间（首尾残缺半周期已剔除；
        # P3：IAE 按点时长 Δt 加权，对齐设计文档 ∫|E|dt；
        # P3-09c 新 profile：半周期段不跨保留段边界（跨界的零交叉对弃用））
        if self.new_semantics and kept_runs:
            segments = []
            for seg_a, seg_b in kept_runs:
                in_run = [c for c in zero_crossings if seg_a <= c <= seg_b]
                segments.extend(self._compute_iae_segments(errors, in_run, point_durations))
        else:
            segments = self._compute_iae_segments(errors, zero_crossings, point_durations)
        pos_iae = [s[0] for s in segments if s[2] > 0]
        neg_iae = [s[0] for s in segments if s[2] < 0]
        pos_dur = [s[1] for s in segments if s[2] > 0]
        neg_dur = [s[1] for s in segments if s[2] < 0]

        if not pos_iae or not neg_iae:
            return self._make_result(
                bundle,
                0.0,
                {
                    "is_oscillating": False,
                    "oscillation_period": 0.0,
                    "reason": "empty_polarity",
                    **({"assessmentState": "COMPLETE"} if self.new_semantics else {}),
                    **bias_key,
                    **sp_info,
                },
            )

        # 步骤 4：计算 IAE 相似率 S_A/S_B（最小距离法）
        s_a = self._similarity_rate(pos_iae, min_ratio, max_ratio)
        s_b = self._similarity_rate(neg_iae, min_ratio, max_ratio)

        # 步骤 5：计算持续时间相似率 S_TA/S_TB（设计文档 §4.6.2 步骤 5，同一算法）
        # 注：设计文档伪代码 line 19-22 综合判定仅用 S_A/S_B，S_TA/S_TB 作为辅助诊断输出
        s_ta = self._similarity_rate(pos_dur, min_ratio, max_ratio)
        s_tb = self._similarity_rate(neg_dur, min_ratio, max_ratio)

        # 步骤 6：综合振荡率（设计文档 §4.6.2 步骤 6）
        osc_value = min(s_a, s_b) * 100.0
        # P1 抗噪门控：平均半周期低于下限时判非振荡（与诊断侧 _iae_kernel 同口径）。
        # 白噪声伪穿越相似率可达 0.9+，仅靠 S_A/S_B 阈值无法区分噪声与真实振荡。
        all_durations = pos_dur + neg_dur
        mean_half_period = float(np.mean(all_durations)) if all_durations else 0.0
        # P2 幅度门控：特征幅度 = 各完整半周期段 IAE/段真实时长（秒）的均值；
        # 低于量程比例下限时判非振荡（噪声带内规则微振荡）。
        # P3：时长分母与 IAE 同步用秒口径（Δt 不可用时按时长采样点数，等价 dt=1）
        seg_seconds = [
            (
                float(np.sum(point_durations[zero_crossings[i] : zero_crossings[i + 1]]))
                if point_durations is not None
                else float(zero_crossings[i + 1] - zero_crossings[i])
            )
            for i in range(len(zero_crossings) - 1)
            if zero_crossings[i + 1] > zero_crossings[i]
        ]
        amp_values = [
            seg[0] / dur for seg, dur in zip(segments, seg_seconds, strict=True) if dur > 0
        ]
        amplitude = float(np.mean(amp_values)) if amp_values else 0.0
        u = self._read_pv_range(bundle)
        amplitude_ratio = amplitude / u if u > 0 else 0.0
        is_osc = (
            s_a >= similarity_threshold
            and s_b >= similarity_threshold
            and mean_half_period >= min_half_period
            and amplitude_ratio >= min_amplitude_ratio
        )

        # 振荡周期 = 2 × 平均半周期（设计文档伪代码 line 23-25，单位秒）
        # P1 修复：旧实现输出采样点数，仅 1s 采样时数值巧合正确；
        # 改为按零交叉点时间戳逐段换算秒，时间戳不可用时按平均采样间隔估计。
        period = 0.0
        if is_osc and all_durations:
            period = 2.0 * self._mean_half_period_seconds(
                zero_crossings, ts_aligned, len(errors), mean_half_period
            )

        osc_value = self._clamp(osc_value)

        # 输出语义（2026-10-10 整改）：value 仅在判定振荡（is_oscillating=True）
        # 时输出 IAE 相似率，非振荡时置 0。原实现无条件输出相似率数值，
        # 噪声带内微穿越的相似率可达 0.9+，非振荡回路（σ 仅量程万分之几）
        # 的快照/前端显示"振荡率 89%"造成误导（实证 05TY05P0803_PIDA）。
        # 相似率仍保留在 details（osc_similarity_rate + s_a/s_b）供诊断侧
        # 与趋势分析使用；stability 的 (1-Osc) 修正本就只在 is_oscillating
        # 时应用，行为不变。
        suppressed = False
        if not is_osc and osc_value > 0.0:
            suppressed = True
            osc_value = 0.0

        logger.debug(
            "[振荡率] s_a=%.4f, s_b=%.4f, s_ta=%.4f, s_tb=%.4f, mean_half=%.1fpts, "
            "amp=%.4f(%.5f), osc=%.2f%%, is_osc=%s, period=%.1fs",
            s_a,
            s_b,
            s_ta,
            s_tb,
            mean_half_period,
            amplitude,
            amplitude_ratio,
            osc_value,
            is_osc,
            period,
        )

        return self._make_result(
            bundle,
            osc_value,
            {
                "is_oscillating": is_osc,
                "oscillation_period": round(period, 2),
                "osc_similarity_rate": round(min(s_a, s_b) * 100.0, 2),
                "similarity_suppressed": suppressed,
                "s_a": round(s_a, 4),
                "s_b": round(s_b, 4),
                "s_ta": round(s_ta, 4),
                "s_tb": round(s_tb, 4),
                "zero_crossings": len(zero_crossings),
                "positive_segments": len(pos_iae),
                "negative_segments": len(neg_iae),
                "mean_half_period_samples": round(mean_half_period, 2),
                "amplitude": round(amplitude, 4),
                "amplitude_ratio": round(amplitude_ratio, 6),
                **({"assessmentState": "COMPLETE"} if self.new_semantics else {}),
                **bias_key,
                **sp_info,
            },
        )

    def _bias_oscillation_evidence(
        self,
        errors: np.ndarray,
        pv_range: float,
        *,
        min_zero_crossings: int = 4,
        min_half_period_samples: float = 8.0,
        min_amplitude_ratio: float = 0.002,
        similarity_threshold: float = 0.4,
    ) -> dict[str, Any]:
        """偏置振荡补充证据（P3-09c，C14-EA2；纯辅助证据，不参与评分）.

        算法（10§3.3"偏置振荡候选"行）：连续段上分离偏置（均值）与允许的
        慢趋势（线性拟合），再验证去偏信号的四重条件——完整周期数（零交叉
        ≥ min_zero_crossings）、周期一致性（半周期时长相似率 ≥ 阈值，正负
        极性分别）、幅度（均值绝对偏差 ≥ min_amplitude_ratio×U）、最短半
        周期（≥ min_half_period_samples）。

        拒绝语义：单调漂移/白噪声/有色噪声去趋势后残差幅度不足或周期不
        一致，不因"去趋势"自动命中；少点周期（粗采样）按最短半周期拒绝。

        Returns:
            证据字典（evidenceOnly=True 恒定——只作辅助证据，不另造评分引擎）。
        """
        out: dict[str, Any] = {
            "detected": False,
            "evidenceOnly": True,
            "note": "偏置振荡补充证据：不改变振荡率判定，不参与 S 修正",
        }
        n = len(errors)
        if n < 8 or pv_range <= 0:
            out["reason"] = "insufficient_data"
            return out
        arr = np.asarray(errors, dtype=float)
        offset_mean = float(np.mean(arr))
        centered = arr - offset_mean
        # 分离慢趋势（线性拟合）：漂移的主要成分被移除后残差幅度不足即拒绝
        idx = np.arange(n, dtype=float)
        slope: float | None = None
        try:
            fit_slope, intercept = np.polyfit(idx, centered, 1)
            detrended = centered - (fit_slope * idx + intercept)
            slope = float(fit_slope)
        except np.linalg.LinAlgError:
            detrended = centered
        amplitude_mean = float(np.mean(np.abs(detrended)))
        out.update(
            {
                "offsetMean": round(offset_mean, 6),
                "trendSlopePerSample": round(slope, 8) if slope is not None else None,
                "amplitudeMean": round(amplitude_mean, 6),
                "amplitudeRatio": round(amplitude_mean / pv_range, 6),
            }
        )
        # 幅度条件（量程比例）
        if amplitude_mean < min_amplitude_ratio * pv_range:
            out["reason"] = "amplitude_below_floor"
            return out
        # 完整周期数与周期一致性
        crossings = self._find_zero_crossings(detrended)
        out["zeroCrossings"] = len(crossings)
        if len(crossings) < min_zero_crossings:
            out["reason"] = "insufficient_complete_cycles"
            return out
        segments = self._compute_iae_segments(detrended, crossings, None)
        pos_dur = [s[1] for s in segments if s[2] > 0]
        neg_dur = [s[1] for s in segments if s[2] < 0]
        if not pos_dur or not neg_dur:
            out["reason"] = "empty_polarity"
            return out
        mean_half = float(np.mean(pos_dur + neg_dur))
        out["meanHalfPeriodSamples"] = round(mean_half, 2)
        if mean_half < min_half_period_samples:
            out["reason"] = "half_period_below_floor"
            return out
        s_ta = self._similarity_rate(pos_dur)
        s_tb = self._similarity_rate(neg_dur)
        out["durationSimilarity"] = {"s_ta": round(s_ta, 4), "s_tb": round(s_tb, 4)}
        if s_ta < similarity_threshold or s_tb < similarity_threshold:
            out["reason"] = "period_inconsistent"
            return out
        out.update(
            {
                "detected": True,
                "cycles": len(crossings) // 2,
                "periodSamples": round(2.0 * mean_half, 2),
            }
        )
        return out

    def _mean_half_period_seconds(
        self,
        zero_crossings: list[int],
        ts_aligned: list,
        n: int,
        fallback_samples: float,
    ) -> float:
        """平均半周期换算为秒.

        优先用相邻零交叉点时间戳差（对非均匀采样精确）；时间戳与点数
        不对齐或不可解析时，回退 平均采样间隔 × 采样半周期（间隔取
        总时长/(n-1)，再失败按 1s）。

        Args:
            zero_crossings: 零交叉点索引（与 errors 数组对齐）
            ts_aligned: 与 errors 对齐的时间戳列表（SP 剔除后为保留子集）
            n: errors 点数
            fallback_samples: 采样点数口径的平均半周期（时间戳不可用时使用）
        """
        if len(ts_aligned) == n:
            halves: list[float] = []
            for i in range(len(zero_crossings) - 1):
                prev, cross = zero_crossings[i], zero_crossings[i + 1]
                if cross <= prev:
                    continue
                delta = ts_aligned[cross] - ts_aligned[prev]
                if isinstance(delta, timedelta):
                    halves.append(delta.total_seconds())
                else:
                    try:
                        halves.append(float(delta))
                    except (TypeError, ValueError):
                        halves = []
                        break
            if halves:
                return float(np.mean(halves))
        # 时间戳不可用：按平均采样间隔估计
        total = self._total_duration_seconds(ts_aligned)
        interval = total / (n - 1) if total > 0 and n > 1 else 1.0
        return fallback_samples * interval

    @staticmethod
    def _read_pv_range(bundle: MetricDataBundle) -> float:
        """读取 PV 量程范围（幅度门控分母）.

        按 ``_read_config_scalar`` 契约解包列表形式 CONFIG 标量（与
        stability._read_pv_range 一致）；缺失/非法时回退归一化默认 100。
        """
        val = MetricCalculatorBase._read_config_scalar(bundle.data_block.signals, "pv_range")
        if val is None:
            return _DEFAULT_PV_RANGE
        try:
            v = float(val)
            return v if v > 0 else _DEFAULT_PV_RANGE
        except (TypeError, ValueError):
            return _DEFAULT_PV_RANGE

    @staticmethod
    def _find_zero_crossings(errors: np.ndarray) -> list[int]:
        """识别零交叉点（偏差符号变化时刻）— 向量化实现.

        零值平台处理：PV 恰好等于 SP 的连续零值段不产生独立符号，
        归并到前一非零符号（前向填充），避免 "+,0,0,+" 这类零值平台
        在旧实现（zero_to_nonzero 规则）下产生伪穿越、切出虚假的零值半周期。
        """
        n = len(errors)
        if n < 2:
            return []
        signs = np.sign(errors)
        # 前向填充：零值继承前一非零符号（向量化，leading zeros 保持 0）
        idx = np.where(signs != 0, np.arange(n), 0)
        np.maximum.accumulate(idx, out=idx)
        filled = signs[idx]
        # 严格符号变化（一正一负）
        sign_change = filled[:-1] * filled[1:] < 0
        return (np.where(sign_change)[0] + 1).tolist()

    @staticmethod
    def _compute_iae_segments(
        errors: np.ndarray,
        zero_crossings: list[int],
        point_durations: list[float] | None = None,
    ) -> list[tuple[float, float, int]]:
        """计算相邻零交叉间的 IAE 段 — 向量化实现.

        只保留完整半周期段（两个零交叉点之间）；首段（数据起点→首个穿越）
        与尾段（最后穿越→数据终点）是残缺半周期，其 IAE/时长与完整段不可比，
        混入会拉低相似率，故剔除出 IAE 列表。

        Args:
            errors: 控制偏差序列
            zero_crossings: 零交叉点索引
            point_durations: 每个采样点代表的时长（秒，零阶保持，与 errors
                对齐）；None 时 Δt=1（按采样点计权，均匀采样下与旧实现
                逐位一致）。P3：IAE = Σ|E_i|·Δt_i 对齐设计文档 ∫|E|dt。

        Returns:
            [(iae, duration, sign), ...] 每段的 IAE/时长（采样点数）/符号
        """
        if len(zero_crossings) < 2:
            return []

        segments: list[tuple[float, float, int]] = []
        for i in range(len(zero_crossings) - 1):
            prev = zero_crossings[i]
            cross = zero_crossings[i + 1]
            if cross <= prev:
                continue
            seg = errors[prev:cross]
            if point_durations is not None:
                iae = float(np.sum(np.abs(seg) * np.asarray(point_durations[prev:cross])))
            else:
                iae = float(np.sum(np.abs(seg)))
            duration = float(cross - prev)
            mean_val = float(np.mean(seg))
            sign = 1 if mean_val > 0 else -1
            segments.append((iae, duration, sign))
        return segments

    @staticmethod
    def _similarity_rate(
        values: list[float],
        min_ratio: float = _DEFAULT_MIN_RATIO,
        max_ratio: float = _DEFAULT_MAX_RATIO,
    ) -> float:
        """计算相似率（最小距离法）— 向量化实现.

        算法：
            1. 找到使 Σ(v_i - v_j)² 最小的 v_j 作为 avg
            2. 清除不相似数据（|v/avg| < min_ratio 或 > max_ratio）
            3. 重新计算平均值 cleaned_avg
            4. similarity = 1 - |cleaned_avg - avg| / |avg|

        注：旧实现第 4 步为 1 - |min(cleaned_avg, avg) - avg| / |avg|，
        单边不对称——cleaned_avg > avg 时 min 恒为 avg、相似率恒 1.0，
        清洗后均值上偏不被惩罚；改为对称形式对齐设计口径。
        """
        if len(values) < 2:
            return 0.0
        arr = np.array(values, dtype=float)

        # 向量化最小距离法：计算每个元素作为参考时的距离平方和
        # dist[j] = Σ_i (arr[i] - arr[j])²
        # 展开：= Σ_i arr[i]² - 2*arr[j]*Σ_i arr[i] + n*arr[j]²
        # 向量化计算避免 O(n²) 双重循环
        sum_arr = float(np.sum(arr))
        sum_sq = float(np.sum(arr**2))
        n = len(arr)
        dists = sum_sq - 2.0 * arr * sum_arr + n * arr**2
        best_j = int(np.argmin(dists))
        avg = float(arr[best_j])
        if abs(avg) < 1e-12:
            return 0.0

        # 清除不相似数据（min_ratio/max_ratio 来自算法参数配置）
        ratios = np.abs(arr / avg)
        cleaned = arr[(ratios >= min_ratio) & (ratios <= max_ratio)]
        if len(cleaned) == 0:
            return 0.0

        cleaned_avg = float(np.mean(cleaned))
        similarity = 1.0 - abs(cleaned_avg - avg) / abs(avg)
        return max(0.0, min(1.0, similarity))


__all__ = ["OscillationRateCalculator"]
