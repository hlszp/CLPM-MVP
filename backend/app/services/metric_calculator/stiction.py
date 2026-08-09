"""粘滞系数计算器（算法说明 §4.8）.

公式：St = b/a × 100%（简化计算方法，国标附录 F.2 推荐）

其中：
    a：PV-OP 散点椭圆的长轴（主方向）
    b：PV-OP 散点椭圆的短轴（垂直于主方向）

椭圆拟合采用 PCA（主成分分析）：
    对归一化的 (PV, OP) 散点计算协方差矩阵，特征值即为椭圆长短轴的平方。

Phase 6 G1 增强（Kano/Choudhury 前提对齐）：
    1. 纯滞后 θ 补偿：椭圆法的瞬时 (PV, OP) 配对隐含 θ=0 假设；
       大 θ 回路中 PV 是 OP 的滞后响应，瞬时配对把散点拉成宽椭圆
       （纯滞后相位差被误判为粘滞宽度）。先用 OP-PV 互相关估计滞后
       θ̂（搜索 0..min(300s, n/4·dt)），将 OP 平移 θ̂ 后再做 PCA 拟合；
       互相关峰值不显著（最佳滞后相关系数 < 0.3）时回退不补偿。
    2. 振荡段门控：粘滞椭圆只在极限环振荡时才有物理意义
       （Kano/Choudhury 方法前提）。先检测 PV 是否处于极限环
       （去均值零交叉 ≥4、平均半周期 ≥8 采样点、正负半周期 IAE
       相似率均 ≥0.6），非振荡段直接 INCONCLUSIVE(no_limit_cycle)，
       平稳回路不再强行出粘滞值。

设计依据：算法说明 §4.8；GB/T 44693.2-2024 附录 F.2

定位：辅助诊断指标，用于检测阀门粘滞故障。
"""

from __future__ import annotations

import logging
import math
from typing import Any

import numpy as np

from app.contracts.data_types import MetricDataBundle, MetricResult
from app.services.algorithm_config import get_algorithm_params
from app.services.metric_calculator.base import MetricCalculatorBase
from app.services.metric_calculator.oscillation import OscillationRateCalculator

logger = logging.getLogger(__name__)

#: 最少数据点数
MIN_POINTS = 100

#: 椭圆拟合度阈值（低于此值返回 INCONCLUSIVE）
MIN_FITTING_SCORE = 0.5

#: 归一化量程
DEFAULT_PV_RANGE = 100.0
DEFAULT_OP_RANGE = 100.0

#: 互相关滞后搜索上限（秒）
MAX_LAG_SECONDS = 300

#: 互相关峰值显著性阈值（最佳滞后处归一化相关系数，低于则回退不补偿）
MIN_CORR_PEAK = 0.3

#: 极限环门控：最少零交叉数（至少 2 个完整周期，与振荡率一致）
MIN_ZERO_CROSSINGS = 4

#: 极限环门控：正/负半周期 IAE 相似率阈值
MIN_IAE_SIMILARITY = 0.6

#: 极限环门控：最小平均半周期（采样点数）。
#: 白噪声去均值后每 ~2 点一次伪穿越，其 IAE 相似率因离群清洗宽松也可达 0.9+，
#: 单靠相似率无法区分噪声与极限环；真实极限环半周期为数十个采样点，
#: 平均半周期下限可稳健剔除高频噪声伪振荡。
MIN_HALF_PERIOD_SAMPLES = 8


class StictionIndexCalculator(MetricCalculatorBase):
    """粘滞系数计算器（算法说明 §4.8）.

    基于 PV-OP 散点图的椭圆拟合，计算椭圆长短轴比值。
    采用 PCA 方法拟合椭圆主轴；拟合前先做极限环门控与纯滞后 θ 补偿。
    """

    @property
    def metric_code(self) -> str:
        return "stiction_index"

    def calculate(self, bundle: MetricDataBundle) -> MetricResult:
        """计算粘滞系数.

        Args:
            bundle: 指标数据包（需含 pv/op 信号，mask 为 pv_valid && op_valid）

        Returns:
            MetricResult：value 为粘滞系数 0~100，
            details 中含 stiction_level/fitting_score/theta_hat_seconds 等
        """
        pairs = self._get_masked_pair(bundle, "pv", "op")
        n = len(pairs)

        logger.debug("[粘滞系数] 输入: masked_points=%d", n)

        if n < MIN_POINTS:
            return self._make_inconclusive(
                bundle,
                "insufficient_data",
                {"sample_count": n, "min_required": MIN_POINTS},
            )

        pv_vals = np.array([float(p) for p, _ in pairs], dtype=float)
        op_vals = np.array([float(o) for _, o in pairs], dtype=float)

        # 振荡段门控：粘滞椭圆只在极限环下有物理意义（Kano/Choudhury 前提）
        # 平稳回路（非极限环）返回 value=0 + level="NONE"（"无粘滞"），
        # 而非 INCONCLUSIVE：平稳意味着无粘滞故障，是有意义的诊断结论。
        # 数据不足（vr<0.20）仍由 _make_result 内部判定 INCONCLUSIVE。
        # 整改 F2：极限环门控的零交叉下限复用 oscillation_rate 配置链
        _params = get_algorithm_params("oscillation_rate", bundle.data_block.control_type)
        is_limit_cycle, gate_info = self._detect_limit_cycle(
            pv_vals,
            int(_params.get("min_zero_crossings", MIN_ZERO_CROSSINGS)),
        )
        if not is_limit_cycle:
            logger.debug("[粘滞系数] 非极限环振荡段（平稳回路），返回无粘滞: %s", gate_info)
            return self._make_result(
                bundle,
                0.0,
                {
                    "stiction_level": "NONE",
                    "sample_count": n,
                    "reason": "no_limit_cycle",
                    **gate_info,
                },
            )

        pv_range = self._read_range(bundle, "pv_range", DEFAULT_PV_RANGE)
        op_range = self._read_range(bundle, "op_range", DEFAULT_OP_RANGE)

        # 纯滞后 θ 估计（互相关）；不显著时回退 lag=0 不补偿
        dt = self._sampling_interval(bundle)
        max_lag = min(int(MAX_LAG_SECONDS / dt), n // 4)
        lag, corr_peak = self._estimate_lag(pv_vals, op_vals, max_lag)
        compensated = lag > 0
        theta_hat_seconds = lag * dt

        # 未补偿拟合度（证据对比：θ 未补偿时散点被拉宽/拉散的程度）
        pv_norm_raw = (pv_vals - np.min(pv_vals)) / (pv_range if pv_range > 0 else 1.0)
        op_norm_raw = (op_vals - np.min(op_vals)) / (op_range if op_range > 0 else 1.0)
        _, _, fitting_raw = self._fit_ellipse(pv_norm_raw, op_norm_raw)

        # θ 补偿：PV[t] 与 OP[t-θ̂] 配对（PV 跟随 OP 滞后 θ̂）
        if compensated:
            pv_fit = pv_vals[lag:]
            op_fit = op_vals[: n - lag]
        else:
            pv_fit = pv_vals
            op_fit = op_vals

        # 数据归一化
        pv_norm = (pv_fit - np.min(pv_fit)) / (pv_range if pv_range > 0 else 1.0)
        op_norm = (op_fit - np.min(op_fit)) / (op_range if op_range > 0 else 1.0)

        # 椭圆拟合（PCA）；fitting_score 为 OP-PV 线性相关系数平方 R²
        a, b, fitting_score = self._fit_ellipse(pv_norm, op_norm)

        theta_details: dict[str, Any] = {
            "theta_hat_seconds": round(theta_hat_seconds, 2),
            "theta_compensated": compensated,
            "corr_peak": round(corr_peak, 4),
            "fitting_score_uncompensated": round(fitting_raw, 4),
            **gate_info,
        }

        # 有效性门控（算法说明 §4.8.4 步骤 8：R² < 0.5 → INCONCLUSIVE）：
        # 圆团/随机散点 |r|≈0，PCA 椭圆 b/a≈1 会把 St 误报到 ~100（SEVERE），
        # 低相关意味着散点无主导方向，b/a 宽度比不具备粘滞物理含义，不予检出
        if fitting_score < MIN_FITTING_SCORE:
            logger.debug(
                "[粘滞系数] 拟合度 R²=%.4f < %.1f（低相关），INCONCLUSIVE",
                fitting_score,
                MIN_FITTING_SCORE,
            )
            return self._make_inconclusive(
                bundle,
                "low_correlation",
                {
                    "stiction_level": "NONE",
                    "fitting_score": round(fitting_score, 4),
                    "sample_count": n,
                    **theta_details,
                },
            )

        # 粘滞系数 St = b/a × 100（R²≥0.5 隐含方差非零，a>0 必然成立）
        stiction = (b / a) * 100.0
        stiction = self._clamp(stiction)
        level = _determine_level(stiction)

        logger.debug(
            "[粘滞系数] a=%.4f, b=%.4f, St=%.2f%%, level=%s, R2=%.4f, theta=%.1fs",
            a,
            b,
            stiction,
            level,
            fitting_score,
            theta_hat_seconds,
        )

        return self._make_result(
            bundle,
            stiction,
            {
                "stiction_level": level,
                "fitting_score": round(fitting_score, 4),
                "long_axis": round(a, 4),
                "short_axis": round(b, 4),
                "sample_count": n,
                **theta_details,
            },
        )

    @staticmethod
    def _detect_limit_cycle(
        pv: np.ndarray, min_zero_crossings: int = MIN_ZERO_CROSSINGS
    ) -> tuple[bool, dict[str, Any]]:
        """极限环振荡门控（简化复用振荡率的零交叉 + IAE 相似率判据）.

        判据（全部满足才认为处于极限环）：
            1. 去均值后零交叉数 ≥ 4（至少 2 个完整周期）
            2. 平均半周期 ≥ 8 采样点（剔除高频噪声伪穿越）
            3. 正/负半周期 IAE 相似率均 ≥ 0.6

        Returns:
            (is_limit_cycle, info) — info 含 zero_crossings/mean_half_period/s_a/s_b
        """
        x = pv - float(np.mean(pv))
        zero_crossings = OscillationRateCalculator._find_zero_crossings(x)
        info: dict[str, Any] = {"zero_crossings": len(zero_crossings)}
        if len(zero_crossings) < min_zero_crossings:
            return False, info

        segments = OscillationRateCalculator._compute_iae_segments(x, zero_crossings)
        pos_iae = [s[0] for s in segments if s[2] > 0]
        neg_iae = [s[0] for s in segments if s[2] < 0]
        durations = [s[1] for s in segments]
        mean_half = float(np.mean(durations)) if durations else 0.0
        info["mean_half_period"] = round(mean_half, 2)
        if not pos_iae or not neg_iae or mean_half < MIN_HALF_PERIOD_SAMPLES:
            return False, info

        s_a = OscillationRateCalculator._similarity_rate(pos_iae)
        s_b = OscillationRateCalculator._similarity_rate(neg_iae)
        info["s_a"] = round(s_a, 4)
        info["s_b"] = round(s_b, 4)
        return s_a >= MIN_IAE_SIMILARITY and s_b >= MIN_IAE_SIMILARITY, info

    @staticmethod
    def _estimate_lag(pv: np.ndarray, op: np.ndarray, max_lag: int) -> tuple[int, float]:
        """互相关估计纯滞后 θ̂（采样点数）.

        c[l] = Σ pv[i+l]·op[i]（去均值，full 模式取非负滞后段）。
        PV 跟随 OP 滞后 θ 时 c[l] 在 l=θ 处取峰。搜索 0..max_lag，
        取首个最大峰对应滞后；峰值显著性为最佳滞后处归一化相关系数
        c[θ̂]/(‖pv‖·‖op‖)，低于 MIN_CORR_PEAK 时返回 lag=0（不补偿）。

        Returns:
            (lag_samples, corr_peak) — 滞后采样点数 / 峰值相关系数
        """
        if max_lag < 1:
            return 0, 0.0
        x = pv - float(np.mean(pv))
        y = op - float(np.mean(op))
        denom = math.sqrt(float(np.sum(x**2) * np.sum(y**2)))
        if denom <= 0:
            return 0, 0.0
        n = len(x)
        corr = np.correlate(x, y, mode="full")
        window = corr[n - 1 : n + max_lag]
        lag = int(np.argmax(window))
        peak = float(window[lag]) / denom
        if peak < MIN_CORR_PEAK:
            return 0, peak
        return lag, peak

    def _sampling_interval(self, bundle: MetricDataBundle) -> float:
        """估计采样间隔（秒）= 掩码后时间戳总时长 / (点数 - 1)，失败回退 1.0."""
        ts = self._get_masked_timestamps(bundle)
        if len(ts) < 2:
            return 1.0
        total = self._total_duration_seconds(ts)
        if total <= 0:
            return 1.0
        return total / (len(ts) - 1)

    @staticmethod
    def _fit_ellipse(x: np.ndarray, y: np.ndarray) -> tuple[float, float, float]:
        """PCA 椭圆拟合.

        计算散点协方差矩阵的特征值，sqrt(特征值) 即为椭圆半轴长度。
        长轴 a = sqrt(max(λ))，短轴 b = sqrt(min(λ))。
        拟合度 R² 取 OP-PV 线性相关系数的平方（对齐算法说明 §4.8.3
        fitting_score 定义）：R² = r² = cov(x,y)² / (var(x)·var(y))。

        注：旧实现用 λmax/(λmax+λmin) 近似 R²，该比值恒 ≥ 0.5，
        使 MIN_FITTING_SCORE 门控分支不可达；圆团散点（|r|≈0）因此
        被误判 St≈100（SEVERE）。改为 r² 后门控真实生效。

        Returns:
            (a, b, fitting_score) — 长轴/短轴/拟合度 R²
        """
        if len(x) < 2:
            return 0.0, 0.0, 0.0

        # 中心化
        x_c = x - np.mean(x)
        y_c = y - np.mean(y)

        # 协方差矩阵
        cov = np.cov(x_c, y_c)
        if cov.shape != (2, 2):
            return 0.0, 0.0, 0.0

        var_x = float(cov[0, 0])
        var_y = float(cov[1, 1])
        if var_x <= 0 or var_y <= 0:
            # 恒定信号无相关性可言，拟合度 0
            return 0.0, 0.0, 0.0

        # 特征值分解
        eigenvalues = np.linalg.eigvalsh(cov)
        eigenvalues = np.maximum(eigenvalues, 0.0)  # 数值稳定性

        lambda_max = float(np.max(eigenvalues))
        lambda_min = float(np.min(eigenvalues))

        a = np.sqrt(lambda_max)
        b = np.sqrt(lambda_min)

        r = float(cov[0, 1]) / math.sqrt(var_x * var_y)
        fitting = r * r

        return a, b, fitting

    @staticmethod
    def _read_range(bundle: MetricDataBundle, key: str, default: float) -> float:
        """读取量程范围."""
        val = bundle.data_block.signals.get(key)
        if val is None:
            return default
        try:
            v = float(val)
            return v if v > 0 else default
        except (TypeError, ValueError):
            return default


def _determine_level(stiction: float) -> str:
    """判定粘滞等级 NONE/MILD/MODERATE/SEVERE."""
    if stiction < 5.0:
        return "NONE"
    if stiction < 15.0:
        return "MILD"
    if stiction < 30.0:
        return "MODERATE"
    return "SEVERE"


def assess_stiction_features(
    pv: np.ndarray,
    op: np.ndarray,
    *,
    sample_interval: float = 1.0,
    pv_range: float | None = None,
    op_range: float | None = None,
) -> dict[str, Any]:
    """诊断侧复用入口：极限环门控 + θ 补偿 + 椭圆拟合（与 KPI 同口径）.

    与 ``StictionIndexCalculator.calculate`` 共享同一算法内核（互相关 θ 补偿 +
    极限环门控 + PCA 椭圆拟合 + R² 拟合度），供诊断引擎
    ``_detect_valve_stiction`` 调用，避免诊断侧与 KPI 侧算法分叉
    （大死迟后回路误判、平稳回路误检）。

    Args:
        pv: PV 数据数组（工程单位）
        op: OP 数据数组（工程单位，需与 pv 等长或可截断到等长）
        sample_interval: 采样间隔（秒），用于 θ 估计与换算
        pv_range: PV 量程；缺省用数据 max-min
        op_range: OP 量程；缺省用数据 max-min

    Returns:
        特征字典：
            - is_limit_cycle: 是否处于极限环振荡段（False 时无粘滞物理意义）
            - fitting_score: 椭圆拟合度 R²（0~1）
            - stiction_index: 椭圆短长轴比 b/a（0~1，越大越粘滞）
            - theta_hat_seconds: 估计纯滞后（秒）
            - theta_compensated: 是否做了 θ 补偿
            - corr_peak: 互相关峰值显著性
            - reason: 非极限环时的原因码（no_limit_cycle / insufficient_data）
            - 其余极限环门控信息（zero_crossings/mean_half_period/s_a/s_b）
    """
    n = min(len(pv), len(op))
    info: dict[str, Any] = {
        "is_limit_cycle": False,
        "fitting_score": 0.0,
        "stiction_index": 0.0,
        "theta_hat_seconds": 0.0,
        "theta_compensated": False,
        "corr_peak": 0.0,
        "reason": "insufficient_data",
    }
    if n < MIN_POINTS:
        return info

    pv_vals = np.asarray(pv[:n], dtype=float)
    op_vals = np.asarray(op[:n], dtype=float)

    # 极限环门控：粘滞椭圆只在极限环振荡时才有物理意义（Kano/Choudhury 前提）
    is_limit_cycle, gate_info = StictionIndexCalculator._detect_limit_cycle(pv_vals)
    info.update(gate_info)
    if not is_limit_cycle:
        info["reason"] = "no_limit_cycle"
        return info

    info.pop("reason", None)
    info["is_limit_cycle"] = True

    # 量程：缺省用数据自身极差
    pv_r = (
        pv_range if pv_range and pv_range > 0 else (float(np.max(pv_vals) - np.min(pv_vals)) or 1.0)
    )
    op_r = (
        op_range if op_range and op_range > 0 else (float(np.max(op_vals) - np.min(op_vals)) or 1.0)
    )

    # 纯滞后 θ 估计（互相关）；不显著时回退 lag=0 不补偿
    dt = sample_interval if sample_interval > 0 else 1.0
    max_lag = min(int(MAX_LAG_SECONDS / dt), n // 4)
    lag, corr_peak = StictionIndexCalculator._estimate_lag(pv_vals, op_vals, max_lag)
    theta_hat_seconds = lag * dt
    compensated = lag > 0

    # θ 补偿：PV[t] 与 OP[t-θ̂] 配对（PV 跟随 OP 滞后 θ̂）
    if compensated:
        pv_fit = pv_vals[lag:]
        op_fit = op_vals[: n - lag]
    else:
        pv_fit = pv_vals
        op_fit = op_vals

    # 归一化 + 椭圆拟合（fitting_score = R²）
    pv_norm = (pv_fit - np.min(pv_fit)) / pv_r
    op_norm = (op_fit - np.min(op_fit)) / op_r
    a, b, fitting_score = StictionIndexCalculator._fit_ellipse(pv_norm, op_norm)

    stiction_index = float(b / a) if a > 0 else 0.0
    stiction_index = min(1.0, max(0.0, stiction_index))

    info.update(
        {
            "fitting_score": float(fitting_score),
            "stiction_index": stiction_index,
            "theta_hat_seconds": round(theta_hat_seconds, 2),
            "theta_compensated": compensated,
            "corr_peak": round(float(corr_peak), 4),
        }
    )
    return info


__all__ = ["StictionIndexCalculator", "assess_stiction_features"]
