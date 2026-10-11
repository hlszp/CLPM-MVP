"""时间常数计算器（整改 F5，HiaMonitor 借鉴 P1 指标）.

基于相关分析法（correlation analysis）粗估回路时间常数 τ（秒）：
    1. 提取掩码后的 PV/OP 对齐序列
    2. 激励充分性检测（check_excitation）：评估窗内 OP 激励不足时不估
    3. OP→PV 互相关估脉冲响应，质心滞后时间作为 τ 粗估

定位：L1 DISPLAY_ONLY 辅助指标（不参与综合评分与节点聚合）。
老快照数据保持 NULL，仅新计算窗口起写入（回算走导入页"触发KPI回算"机制）。

P3-10c 新 profile（CANDIDATE_V3_RC1，10§3.4 时间常数行 + C16-EA4）：
    - 展示口径为**相关响应时间粗估**（``quantity``）——质心混合 τ+θ，
      不是已分离的时间常数（``estimateBasis=centroid_lag_tau_plus_theta``）；
      τ/θ 分离归 P3-06 唯一辨识引擎，本卡不新增辨识质量字段；
    - 记录**实际搜索秒数**（``searchWindowSeconds``/``searchLagSamples``，
      取自 nonparametric 返回的 lags，不硬编码内部常量）；
    - **截断明确**（``truncated=true``）：脉冲响应在搜索窗末端仍未衰减
      （末值/峰值 > 尾部比阈值）→ 慢于搜索窗的系统，粗估有下偏，不冒充
      精确 τ；
    - 激励不足/估计不可信 → UNASSESSABLE（回路级数据质量保留，
      数据好 ≠ 可估计，09a 同口径）。

设计依据：CLPM_v6.1_HiaMonitor借鉴重构计划.md v1.1 §4 时间常数（L1）；
         tuning_identification/nonparametric.correlation_analysis 复用。
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from app.contracts.data_types import MetricDataBundle, MetricResult
from app.services.metric_calculator.base import MetricCalculatorBase
from app.services.tuning_identification.excitation import check_excitation
from app.services.tuning_identification.nonparametric import correlation_analysis

logger = logging.getLogger(__name__)

#: 最少数据点数（相关分析需要足够样本支撑互相关估计，对齐 settling_time 口径）
MIN_POINTS = 100

#: 质心滞后物理合理性上限（秒）：超过视为激励/信号不可信，不输出估计值
MAX_TAU_SECONDS = 4 * 3600.0

#: 默认采样周期（秒，sampling_freq 解析失败时回退，对齐 settling_time 口径）
DEFAULT_SAMPLE_INTERVAL = 1.0

#: 截断判定质心比阈值（新 profile）：质心 ≥ 0.4×搜索窗 → 响应在窗内未充分
#: 衰减（慢于搜索窗系统，粗估下偏）。物理依据：ir∝e^{-k/τ} 截断在 W 时，
#: 理想质心/W = 0.4 ⟺ e^{-W/τ}≈0.35（窗末仍余 ~35% 响应）；衰减充分的
#: 系统（如 τ=W/3）理想质心比 ≈0.28 不触发。保守标记——不触发不代表
#: 响应已在窗内衰减完（估计噪声），触发则确定受窗限制。
#: 工程默认（显示/资格标记，非评分 oracle 值；调整须登记依据）
TRUNCATION_CENTROID_FRACTION = 0.4

#: 相关搜索 lag 上限（nonparametric 引擎口径，仅用于 searchLimitingFactor 归因；
#: 实际搜索秒数取自返回 lags，不镜像该常量做计算）
_ENGINE_MAX_LAG_SAMPLES = 300

#: 新 profile 展示口径标注（P6 文案按此展示：相关响应时间粗估）
_TC_QUANTITY = "correlation_response_time_rough_estimate"
_TC_QUANTITY_NOTE = (
    "centroid-based rough response time estimate combining tau+theta; "
    "tau/theta separation belongs to the identification engine (P3-06)"
)


def _read_sample_interval(bundle: MetricDataBundle) -> float:
    """读取采样周期（秒）：从 sampling_freq 标签解析（"1s" → 1.0, "5s" → 5.0）。"""
    freq = bundle.data_block.sampling_freq
    if not freq:
        return DEFAULT_SAMPLE_INTERVAL
    s = freq.strip().lower().replace("s", "")
    try:
        return float(s) if s else DEFAULT_SAMPLE_INTERVAL
    except ValueError:
        return DEFAULT_SAMPLE_INTERVAL


class TimeConstantCalculator(MetricCalculatorBase):
    """时间常数计算器（L1 DISPLAY_ONLY）.

    闭环评估窗下 OP 为 PID 输出、激励通常有限，因此先做激励充分性检测：
    激励不足时返回 INCONCLUSIVE（value=None，reason=insufficient_excitation），
    避免输出无意义的伪估值。
    """

    @property
    def metric_code(self) -> str:
        return "time_constant"

    def calculate(self, bundle: MetricDataBundle) -> MetricResult:
        pairs = self._get_masked_pair(bundle, "pv", "op")
        n = len(pairs)

        logger.debug("[time_constant] 输入: masked_points=%d", n)

        if n < MIN_POINTS:
            return self._make_inconclusive(
                bundle,
                "insufficient_data",
                {"sample_count": n, "min_required": MIN_POINTS},
            )

        pv = np.array([float(p) for p, _ in pairs], dtype=float)
        op = np.array([float(o) for _, o in pairs], dtype=float)

        # 健壮性契约：NaN/Inf 输入不做线性代数运算（SVD 不收敛），直接 INCONCLUSIVE
        if not (np.all(np.isfinite(pv)) and np.all(np.isfinite(op))):
            return self._make_inconclusive(
                bundle,
                "invalid_data",
                {"sample_count": n, "verdict": "信号含 NaN/Inf"},
            )

        sample_interval = _read_sample_interval(bundle)

        # 纯滞后采样数未知，按 0 处理（质心估计对 d 不敏感）
        try:
            excitation = check_excitation(op, pv, d=0)
        except (ValueError, np.linalg.LinAlgError) as exc:
            logger.debug("[time_constant] 激励检测异常: %s", exc)
            return self._tc_unassessable_or_inconclusive(
                bundle,
                "insufficient_excitation",
                {"sample_count": n, "verdict": f"激励检测异常: {exc}"},
            )
        if not excitation.is_sufficient:
            logger.debug("[time_constant] 激励不足，返回 INCONCLUSIVE: %s", excitation.verdict)
            return self._tc_unassessable_or_inconclusive(
                bundle,
                "insufficient_excitation",
                {
                    "verdict": excitation.verdict,
                    "significant_changes": excitation.significant_changes,
                    "sample_count": n,
                },
            )

        ts = float(sample_interval) if sample_interval else 1.0
        estimate = correlation_analysis(op, pv, ts=ts)
        tau = estimate.time_constant_estimate

        # 质心可能为负（反向因果噪声）或异常大（激励伪相关），做物理合理性截断
        if not np.isfinite(tau) or tau <= 0 or tau > MAX_TAU_SECONDS:
            logger.debug("[time_constant] 质心估计不可信: tau=%s", tau)
            return self._tc_unassessable_or_inconclusive(
                bundle,
                "estimation_unreliable",
                {"tau_estimate": tau, "sample_count": n},
            )

        logger.debug("[time_constant] tau=%.2fs, n=%d", tau, n)
        details: dict = {
            "tau_seconds": round(tau, 2),
            "gain_estimate": round(estimate.gain_estimate, 4),
            "significant_changes": excitation.significant_changes,
            "sample_count": n,
        }
        if self.new_semantics:
            details.update(self._rough_estimate_audit(estimate, ts, sample_count=n, tau=tau))
        return self._make_result(bundle, tau, details)

    # ------------------------------------------------------------------
    # P3-10c 新 profile 辅助
    # ------------------------------------------------------------------

    def _tc_unassessable_or_inconclusive(
        self, bundle: MetricDataBundle, reason: str, details: dict
    ) -> MetricResult:
        """新 profile：数据好 ≠ 可估计（09a 同口径，UNASSESSABLE 保留回路级）."""
        if self.new_semantics:
            return self._make_unassessable(bundle, reason, details)
        return self._make_inconclusive(bundle, reason, details)

    @staticmethod
    def _rough_estimate_audit(
        estimate: Any,
        ts: float,
        *,
        sample_count: int,
        tau: float,
    ) -> dict:
        """新 profile 粗估审计 details：搜索秒数 + 截断判定 + 口径正名（C16-EA4）.

        - 搜索窗取自 nonparametric 返回的 ``lags``（实际生效搜索范围）；
        - ``searchLimitingFactor``：搜索范围受数据长度还是引擎上限限制
          （确定性归因，可审计）；
        - ``truncated``：质心 ≥ TRUNCATION_CENTROID_FRACTION × 搜索窗 →
          响应在窗内未充分衰减（慢于搜索窗系统），粗估有下偏。保守标记：
          不触发不证明已衰减（估计噪声），触发则确定受窗限制。
        """
        lags = getattr(estimate, "lags", None)
        n_lags = int(len(lags)) if lags is not None and len(lags) else 0
        search_window_seconds = round(float(lags[-1]) + ts, 2) if n_lags else None
        limiting = (
            "data_length"
            if n_lags and sample_count // 2 < _ENGINE_MAX_LAG_SAMPLES
            else "engine_max_lag"
        )
        details: dict = {
            "quantity": _TC_QUANTITY,
            "quantityNote": _TC_QUANTITY_NOTE,
            "estimateBasis": "centroid_lag_tau_plus_theta",
            "searchLagSamples": n_lags,
            "searchWindowSeconds": search_window_seconds,
            "searchLimitingFactor": limiting if n_lags else None,
            "assessmentState": "COMPLETE",
        }
        if n_lags and search_window_seconds and search_window_seconds > 0:
            centroid_ratio = float(tau) / search_window_seconds
            details["centroidToWindowRatio"] = round(centroid_ratio, 4)
            truncated = centroid_ratio >= TRUNCATION_CENTROID_FRACTION
            details["truncated"] = truncated
            if truncated:
                details["truncationNote"] = (
                    "response not decayed within the correlation search window "
                    "(system slower than window); estimate is a lower-bound-biased "
                    "rough value, not an exact time constant"
                )
        return details
