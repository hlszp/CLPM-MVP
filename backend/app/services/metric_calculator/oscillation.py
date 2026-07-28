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
"""

from __future__ import annotations

import logging

import numpy as np

from app.contracts.data_types import MetricDataBundle, MetricResult
from app.services.algorithm_config import get_algorithm_params
from app.services.metric_calculator.base import MetricCalculatorBase

logger = logging.getLogger(__name__)

#: 振荡判定相似率阈值（配置缺失时的算法回退默认值，与 algorithm_config._DEFAULTS 一致）
SIMILARITY_THRESHOLD = 0.4

#: 最少零交叉点数（至少 2 个完整周期）
MIN_ZERO_CROSSINGS = 4

#: 相似率过滤比率的算法回退默认值（与 algorithm_config._DEFAULTS 一致）
_DEFAULT_MIN_RATIO = 0.05
_DEFAULT_MAX_RATIO = 15.0


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
                {"is_oscillating": False, "oscillation_period": 0.0, "sample_count": n},
            )

        # P0-B: 从配置链读取算法参数（control_type 为响应类别 STABLE/SLOW/FAST/LOGIC）
        params = get_algorithm_params("oscillation_rate", bundle.data_block.control_type)
        similarity_threshold = float(params.get("similarity_threshold", SIMILARITY_THRESHOLD))
        min_ratio = float(params.get("min_ratio", _DEFAULT_MIN_RATIO))
        max_ratio = float(params.get("max_ratio", _DEFAULT_MAX_RATIO))

        errors = np.array([float(pv) - float(sp) for pv, sp in pairs], dtype=float)

        # 步骤 2：识别零交叉点
        zero_crossings = self._find_zero_crossings(errors)
        if len(zero_crossings) < MIN_ZERO_CROSSINGS:
            logger.debug(
                "[振荡率] 零交叉点 %d < %d，返回 0", len(zero_crossings), MIN_ZERO_CROSSINGS
            )
            return self._make_result(
                bundle,
                0.0,
                {
                    "is_oscillating": False,
                    "oscillation_period": 0.0,
                    "zero_crossings": len(zero_crossings),
                },
            )

        # 步骤 3：计算相邻零交叉间的 IAE 与持续时间（首尾残缺半周期已剔除）
        segments = self._compute_iae_segments(errors, zero_crossings)
        pos_iae = [s[0] for s in segments if s[2] > 0]
        neg_iae = [s[0] for s in segments if s[2] < 0]
        pos_dur = [s[1] for s in segments if s[2] > 0]
        neg_dur = [s[1] for s in segments if s[2] < 0]

        if not pos_iae or not neg_iae:
            return self._make_result(
                bundle,
                0.0,
                {"is_oscillating": False, "oscillation_period": 0.0, "reason": "empty_polarity"},
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
        is_osc = s_a >= similarity_threshold and s_b >= similarity_threshold

        # 振荡周期 = 2 × 平均半周期（设计文档伪代码 line 23-25）
        period = 0.0
        if is_osc and (pos_dur or neg_dur):
            all_durations = pos_dur + neg_dur
            period = float(np.mean(all_durations)) * 2.0

        osc_value = self._clamp(osc_value)

        logger.debug(
            "[振荡率] s_a=%.4f, s_b=%.4f, s_ta=%.4f, s_tb=%.4f, osc=%.2f%%, is_osc=%s, period=%.1f",
            s_a,
            s_b,
            s_ta,
            s_tb,
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
                "s_a": round(s_a, 4),
                "s_b": round(s_b, 4),
                "s_ta": round(s_ta, 4),
                "s_tb": round(s_tb, 4),
                "zero_crossings": len(zero_crossings),
                "positive_segments": len(pos_iae),
                "negative_segments": len(neg_iae),
            },
        )

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
        errors: np.ndarray, zero_crossings: list[int]
    ) -> list[tuple[float, float, int]]:
        """计算相邻零交叉间的 IAE 段 — 向量化实现.

        只保留完整半周期段（两个零交叉点之间）；首段（数据起点→首个穿越）
        与尾段（最后穿越→数据终点）是残缺半周期，其 IAE/时长与完整段不可比，
        混入会拉低相似率，故剔除出 IAE 列表。

        Returns:
            [(iae, duration, sign), ...] 每段的 IAE/时长/符号
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
