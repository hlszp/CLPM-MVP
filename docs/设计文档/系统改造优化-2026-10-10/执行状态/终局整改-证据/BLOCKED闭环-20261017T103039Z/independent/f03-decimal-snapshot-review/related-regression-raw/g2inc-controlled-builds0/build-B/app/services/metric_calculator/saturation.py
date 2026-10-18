"""饱和率计算器（算法说明 §4.7）.

公式（对齐国标 GB/T 44693.2-2024 附录 F.3）：
    η_sat = T_auto_saturated / T_total × 100%

其中：
    T_total = Σ Δt_i
        评估时段总时长（含手动模式，对应国标 AllTime）
    T_auto_saturated = Σ Δt_i · 𝟙(mode_i ∈ Auto ∧ (OP_i ≤ OP_low+ε ∨ OP_i ≥ OP_high-ε))
        仅自控模式下的饱和时长（对应国标 AutoSaturateTime）
    ε：饱和容差（默认 0=严格贴限，CONFIG saturation_epsilon 可按回路覆盖）

设计依据：算法说明 §4.7；GB/T 44693.2-2024 附录 F.3（Sa = AutoSaturateTime / AllTime）

定位：辅助诊断指标，用于有效自控率判定和输出能力诊断。

注：自控模式含 AUTO/CAS/REMOTE/APC（StandardMode 1/2/3/4），手动 MANUAL(0)
不计入分子；分母为评估时段总时长，故全程手动时 Sa=0%（非 INCONCLUSIVE）。
"""

from __future__ import annotations

import logging
from typing import Any

from app.contracts.data_types import MetricDataBundle, MetricResult
from app.services.metric_calculator.auto_mode import AUTO_MODES
from app.services.metric_calculator.base import MetricCalculatorBase
from app.services.metric_calculator.time_axis import MetricTimeView, view_or_none

logger = logging.getLogger(__name__)

#: 默认输出上下限（归一化 0~100）
DEFAULT_OP_LOW = 0.0
DEFAULT_OP_HIGH = 100.0

#: 默认饱和容差（%）。2026-10-10 用户裁决：去容差带，饱和=严格贴限位
#: 端点（OP ≤ op_low 或 ≥ op_high），默认 0；CONFIG 信号 saturation_epsilon
#: 仍可按回路覆盖（需模糊带的场景显式配置）
DEFAULT_EPSILON = 0.0


class SaturationRateCalculator(MetricCalculatorBase):
    """饱和率计算器（算法说明 §4.7）.

    统计控制器输出 OP 处于限位的时长占评估时段总时长的比例。
    分子：仅自控模式（AUTO/CAS/REMOTE/APC）下的饱和时长（AutoSaturateTime）；
    分母：评估时段总时长（含手动模式，对齐国标 F.3 AllTime）。
    """

    @property
    def metric_code(self) -> str:
        return "saturation_rate"

    def calculate(self, bundle: MetricDataBundle) -> MetricResult:
        """计算饱和率（国标 F.3：Sa = AutoSaturateTime / AllTime）.

        Args:
            bundle: 指标数据包（需含 op/mode 信号，mask 为 op_valid && mode_valid）

        Returns:
            MetricResult：value 为饱和率 0~100，saturation_type 在 details 中；
            数据不足（点数<2 或总时长=0）时 INCONCLUSIVE。

        时间契约（P3-08c，10§3.1 表 Sat 行/C04-EA2）：
            - 分母 = 完整请求窗口秒数（半开 [start,end)；与 R/Auto 同口径）；
            - 分子只计"已知自动模式且有效 OP 贴真实限位"的秒数；缺 MODE 不
              假设 AUTO；OP 无值/无效不能证明饱和也不能证明未饱和；
            - 未知段不补时、不缩分母——unknown ≠ 已确认未饱和；保留严格
              端点与已批准 epsilon 配置（不随改造扩大）；
            - 限位证据来源（P3-08b 三分）：op_limits_source/op_limits_provenance
              在 signals 时透传进 details（DEFAULT_0_100=默认假设不当已核实）。
            时间视图不可得（legacy/手工 bundle）→ 原 masked 压缩口径不变。
        """
        masked_op = self._get_masked_values(bundle, "op")
        masked_mode = self._get_masked_values(bundle, "mode")
        masked_ts = self._get_masked_timestamps(bundle)
        n = len(masked_op)

        logger.debug("[饱和率] 输入: masked_points=%d", n)

        if n < 2 or len(masked_ts) < 2:
            return self._make_inconclusive(bundle, "insufficient_op_mode_data")

        op_low, op_high, epsilon = self._read_op_bounds(bundle)

        # P3-08c：时间视图可用 → 半开窗口契约口径（分母=全窗秒数）
        view = view_or_none(bundle, ("mode", "op"))
        if view is not None and len(view.indices) >= 2:
            return self._calculate_with_view(bundle, view, op_low, op_high, epsilon)

        # ---------------- legacy 回退（无窗口/上下文：原口径不变） ----------------
        # 采用零阶保持模型：每个采样点代表一个时间间隔（最后一个点沿用前段时长）
        # 数组长度可能不一致（信号与时间戳长度不齐），循环上界取各数组最小长度防止 IndexError
        durations = self._point_durations(masked_ts)
        bound = min(n, len(masked_mode), len(durations))

        # 分母：评估时段总时长（AllTime，含手动模式）——对齐国标 F.3
        total_duration = float(sum(durations[:bound]))
        if total_duration <= 0:
            return self._make_inconclusive(bundle, "zero_total_duration")

        auto_duration = 0.0
        sat_high_duration = 0.0
        sat_low_duration = 0.0

        for i in range(bound):
            segment = durations[i]
            mode_val = _to_int(masked_mode[i])
            if mode_val not in AUTO_MODES:
                # 非自控：已计入分母 total_duration，不计入分子与自控时长
                continue
            auto_duration += segment
            op_val = _to_float(masked_op[i])
            if op_val is None:
                # OP 解析失败：计入分母与自控时长，不计入饱和分子，
                # 避免按 0.0 处理被误计为低限饱和
                continue
            if op_val >= op_high - epsilon:
                sat_high_duration += segment
            elif op_val <= op_low + epsilon:
                sat_low_duration += segment

        sat_total = sat_high_duration + sat_low_duration
        rate = (sat_total / total_duration) * 100.0
        rate = self._clamp(rate)
        sat_type = _determine_type(sat_high_duration, sat_low_duration)

        logger.debug(
            "[饱和率] sat_high=%.1f, sat_low=%.1f, auto=%.1f, total=%.1f, rate=%.2f%%, type=%s",
            sat_high_duration,
            sat_low_duration,
            auto_duration,
            total_duration,
            rate,
            sat_type,
        )

        return self._make_result(
            bundle,
            rate,
            {
                "saturation_type": sat_type,
                "sat_high_duration_s": round(sat_high_duration, 2),
                "sat_low_duration_s": round(sat_low_duration, 2),
                "auto_duration_s": round(auto_duration, 2),
                "total_duration_s": round(total_duration, 2),
                "op_low": op_low,
                "op_high": op_high,
                "epsilon": epsilon,
                "time_basis": "legacy_masked_extent",
            },
        )

    def _calculate_with_view(
        self,
        bundle: MetricDataBundle,
        view: MetricTimeView,
        op_low: float,
        op_high: float,
        epsilon: float,
    ) -> MetricResult:
        """契约口径计算（P3-08c）：分母=完整请求窗口秒数（半开）。

        槽分类：MODE 或 OP 无依据 → unknown（不判分子，不缩分母）；已知
        非自动（含手动）→ 仅分母；已知自动且 OP 有效贴限 → 分子；已知自动
        但 OP 无效/无值 → 不能证明饱和（仅分母，不计未饱和结论）。
        """
        signals = bundle.data_block.signals
        mode_sig = signals.get("mode") or []
        op_sig = signals.get("op") or []
        auto_duration = 0.0
        sat_high_duration = 0.0
        sat_low_duration = 0.0

        def _eligible(slot: int) -> bool:
            orig = view.indices[slot]
            mode_val = _to_int(mode_sig[orig]) if orig < len(mode_sig) else -1
            if mode_val not in AUTO_MODES or not view.role_valid("mode", slot):
                return False
            if not view.role_valid("op", slot):
                return False
            op_val = _to_float(op_sig[orig]) if orig < len(op_sig) else None
            if op_val is None:
                return False
            return op_val >= op_high - epsilon or op_val <= op_low + epsilon

        # 自控时长（已知自动模式；供 details 上下文）
        for slot in range(len(view.indices)):
            if not view.roles_known(slot, ("mode", "op")):
                continue
            orig = view.indices[slot]
            mode_val = _to_int(mode_sig[orig]) if orig < len(mode_sig) else -1
            if mode_val in AUTO_MODES and view.role_valid("mode", slot):
                auto_duration += view.slot_seconds[slot]
                if view.role_valid("op", slot):
                    op_val = _to_float(op_sig[orig]) if orig < len(op_sig) else None
                    if op_val is not None:
                        if op_val >= op_high - epsilon:
                            sat_high_duration += view.slot_seconds[slot]
                        elif op_val <= op_low + epsilon:
                            sat_low_duration += view.slot_seconds[slot]

        accounting = view.ratio_accounting(_eligible, roles=("mode", "op"))
        if accounting.denominator_seconds <= 0:
            return self._make_inconclusive(bundle, "zero_total_duration")
        rate = self._clamp(accounting.proven_ratio * 100.0)
        sat_type = _determine_type(sat_high_duration, sat_low_duration)

        details: dict[str, Any] = {
            "saturation_type": sat_type,
            "sat_high_duration_s": round(sat_high_duration, 2),
            "sat_low_duration_s": round(sat_low_duration, 2),
            "auto_duration_s": round(auto_duration, 2),
            "total_duration_s": round(accounting.denominator_seconds, 2),
            "op_low": op_low,
            "op_high": op_high,
            "epsilon": epsilon,
            "time_basis": "half_open_window",
        }
        # P3-08b 限位来源三分（B1 语义消费；缺失=未记录）
        source = MetricCalculatorBase._read_config_scalar(signals, "op_limits_source")
        provenance = MetricCalculatorBase._read_config_scalar(signals, "op_limits_provenance")
        if source is not None:
            details["op_limits_source"] = source
        if provenance is not None:
            details["op_limits_provenance"] = provenance
        details.update(accounting.to_details())
        return self._make_result(bundle, rate, details)

    def _read_op_bounds(self, bundle: MetricDataBundle) -> tuple[float, float, float]:
        """读取 OP 上下限与饱和容差.

        优先级（2026-10-10 用户裁决，ε 可配置默认 0）：
            1. CONFIG 信号 op_low/op_high（回路限位）/
               saturation_epsilon（回路级覆盖）
            2. 配置链 algorithm_config（saturation_rate.saturation_epsilon，算法参数页可改）
            3. 默认值 0/100/0（严格贴限）
        """
        signals = bundle.data_block.signals
        op_low = _read_float(signals, "op_low", DEFAULT_OP_LOW)
        op_high = _read_float(signals, "op_high", DEFAULT_OP_HIGH)
        epsilon = _read_float(signals, "saturation_epsilon", None)
        if epsilon is None:
            # G2-INC-a：任务固定五层有效参数优先，未固定才读配置链当前缓存
            params = self._effective_params("saturation_rate", bundle)
            try:
                epsilon = float(params.get("saturation_epsilon", DEFAULT_EPSILON))
            except (TypeError, ValueError):
                epsilon = DEFAULT_EPSILON
        return op_low, op_high, epsilon


def _determine_type(sat_high: float, sat_low: float) -> str:
    """判定饱和类型 HIGH/LOW/BOTH/NONE."""
    has_high = sat_high > 0
    has_low = sat_low > 0
    if has_high and has_low:
        return "BOTH"
    if has_high:
        return "HIGH"
    if has_low:
        return "LOW"
    return "NONE"


def _to_int(val: Any) -> int:
    """安全转换为 int."""
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return -1


def _to_float(val: Any) -> float | None:
    """安全转换为 float；解析失败返回 None（调用方应跳过该点）."""
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _read_float(signals: dict, key: str, default: float) -> float:
    """从信号字典读取 float 值（兼容列表存储）."""
    val = MetricCalculatorBase._read_config_scalar(signals, key)
    if val is None:
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


__all__ = ["SaturationRateCalculator"]
