"""时间常数计算器（整改 F5，HiaMonitor 借鉴 P1 指标）.

基于相关分析法（correlation analysis）粗估回路时间常数 τ（秒）：
    1. 提取掩码后的 PV/OP 对齐序列
    2. 激励充分性检测（check_excitation）：评估窗内 OP 激励不足时不估
    3. OP→PV 互相关估脉冲响应，质心滞后时间作为 τ 粗估

定位：L1 DISPLAY_ONLY 辅助指标（不参与综合评分与节点聚合）。
老快照数据保持 NULL，仅新计算窗口起写入（回算走导入页"触发KPI回算"机制）。

设计依据：CLPM_v6.1_HiaMonitor借鉴重构计划.md v1.1 §4 时间常数（L1）；
         tuning_identification/nonparametric.correlation_analysis 复用。
"""

from __future__ import annotations

import logging

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
            return self._make_inconclusive(
                bundle,
                "insufficient_excitation",
                {"sample_count": n, "verdict": f"激励检测异常: {exc}"},
            )
        if not excitation.is_sufficient:
            logger.debug("[time_constant] 激励不足，返回 INCONCLUSIVE: %s", excitation.verdict)
            return self._make_inconclusive(
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
            return self._make_inconclusive(
                bundle,
                "estimation_unreliable",
                {"tau_estimate": tau, "sample_count": n},
            )

        logger.debug("[time_constant] tau=%.2fs, n=%d", tau, n)
        return self._make_result(
            bundle,
            tau,
            {
                "tau_seconds": round(tau, 2),
                "gain_estimate": round(estimate.gain_estimate, 4),
                "significant_changes": excitation.significant_changes,
                "sample_count": n,
            },
        )
