"""有效自控率计算器（算法说明 §4.2）.

公式：R = T_auto_effective / T_total × 100%

其中 T_auto_effective 需同时满足：
    1. MODE 为 Auto/Cascade/Remote（自动模式）
    2. OP 未饱和（OP_low+ε < OP < OP_high-ε）
    3. 控制偏差在合理范围（|E| < |E|_max）

设计依据：算法说明 §4.2；GB/T 44693.2-2024 附录 B.2

定位：投用指标，作为综合评分的整体折扣因子（乘数 R）。
"""

from __future__ import annotations

import logging
from typing import Any

from app.constants.mode import AUTO_MODES
from app.contracts.data_types import MetricDataBundle, MetricResult
from app.services.metric_calculator.base import MetricCalculatorBase
from app.services.metric_calculator.time_axis import MetricTimeView, view_or_none

logger = logging.getLogger(__name__)

#: 默认偏差最大允许基准占量程比例
DEFAULT_E_MAX_RATIO = 0.05

#: 归一化信号量程
NORMALIZED_RANGE = 100.0

#: 默认饱和容差。2026-10-10 用户裁决：去容差带，与 saturation_rate 同口径
#: （饱和=严格贴限位端点）；CONFIG saturation_epsilon 可按回路覆盖
DEFAULT_EPSILON = 0.0

#: 默认 OP 上下限
DEFAULT_OP_LOW = 0.0
DEFAULT_OP_HIGH = 100.0


class EffectiveAutoRateCalculator(MetricCalculatorBase):
    """有效自控率计算器（算法说明 §4.2）.

    统计评估时段内控制器处于自动模式且控制有效的时长占比。
    作为综合评分的整体折扣因子（乘数 R）。
    """

    @property
    def metric_code(self) -> str:
        return "effective_auto_rate"

    def calculate(self, bundle: MetricDataBundle) -> MetricResult:
        """计算有效自控率.

        Args:
            bundle: 指标数据包（需含 mode/op/pv/sp 信号）

        Returns:
            MetricResult：value 为有效自控率 0~100；
            details 含 auto_duration_s / effective_duration_s / total_duration_s
            等时长上下文；数据块携带查询窗/point 上下文时另含契约 §2.1 比率
            口径字段（numeratorSeconds/denominatorSeconds/unknownSeconds/
            knownSeconds/coverageRatio/possibleRange/denominatorBasis）。

        Note:
            自控率指标（auto_mode_rate）由 AutoModeRateCalculator 单独计算，
            本计算器不再副产出该值——历史副产出口径为 ``mode_valid && op_valid``
            掩码下的自控时长占比，与自控率指标（``mode_valid`` 掩码）不一致，
            同名不同值易造成口径混淆（2026-08-27 移除重复实现）。

        时间契约（P3-08c，10§3.1 表 R 行）：
            - 分母 = 完整请求窗口秒数（**坏 PV/SP 留在全窗分母**，C03-EA3）；
            - 分子只计"有效自动模式、有效 OP 且未饱和、PV/SP 有效且偏差合格"
              的秒数——PV/SP 已标 Bad（数值资格不过）的段按契约不能计有效，
              不当作"有数值就合格"（质量门禁）；
            - 未知段（MODE/OP/PV/SP 任一无依据）不补时、不缩分母，单列
              unknownSeconds 与可能区间（不把未知当手动/已确认不饱和）。
            时间视图不可得（legacy/手工 bundle）→ 原 masked 压缩口径不变。
        """
        masked_mode = self._get_masked_values(bundle, "mode")
        masked_op = self._get_masked_values(bundle, "op")
        masked_pv = self._get_masked_values(bundle, "pv")
        masked_sp = self._get_masked_values(bundle, "sp")
        masked_ts = self._get_masked_timestamps(bundle)
        n = len(masked_mode)

        logger.debug("[有效自控率] 输入: masked_points=%d", n)

        if n < 2 or len(masked_ts) < 2:
            return self._make_inconclusive(bundle, "insufficient_data")

        has_pv_sp = len(masked_pv) > 0 and len(masked_sp) > 0
        # CAL-03：PV/SP 是偏差检查必需输入，缺失显式不可评（不静默当有效）
        if not has_pv_sp:
            missing = [
                name for name, vals in (("pv", masked_pv), ("sp", masked_sp)) if len(vals) == 0
            ]
            return self._make_inconclusive(
                bundle,
                "deviation_inputs_missing",
                {
                    "missing_signals": missing,
                    "note": "deviation criterion requires pv+sp; missing input is not effective",
                },
            )

        op_low, op_high, epsilon = self._read_bounds(bundle)
        # 整改 F2：默认偏差带比例从配置链读取
        # G2-INC-a：任务固定五层有效参数优先，未固定才读配置链当前缓存
        params = self._effective_params("effective_auto_rate", bundle)
        default_e_max_ratio = float(params.get("default_e_max_ratio", DEFAULT_E_MAX_RATIO))
        e_max = self._read_e_max(bundle, default_e_max_ratio)

        # P3-08c：时间视图可用 → 半开窗口契约口径（分母=全窗秒数）
        required_roles = tuple(
            r for r in ("mode", "op", "pv", "sp") if r in bundle.data_block.signals
        )
        view = view_or_none(bundle, required_roles)
        if view is not None and len(view.indices) >= 2:
            return self._calculate_with_view(bundle, view, op_low, op_high, epsilon, e_max)

        # ---------------- legacy 回退（无窗口/上下文：原口径不变） ----------------
        # 采用零阶保持模型：每个采样点代表一个时间间隔（最后一个点沿用前段时长）
        # 循环上界取 mode/时间戳最小长度防 IndexError。op 是可选信号（缺失时
        # 不判饱和）；pv/sp 自 CAL-03（P1-01）起为必需偏差输入——契约 tags
        # 已扩为 ["mode","op","pv","sp"]，缺失时上方显式 INCONCLUSIVE，
        # 不再把上界截断为 0 误报 zero_total_duration（2026-07-28 回归教训：
        # 上一版对全部 5 个数组取 min，导致缺 pv/sp 时全回路 INCONCLUSIVE）。
        durations = self._point_durations(masked_ts)
        bound = min(n, len(durations))
        durations = durations[:bound]
        total_duration = sum(durations)
        if total_duration <= 0:
            return self._make_inconclusive(bundle, "zero_total_duration")

        has_op = len(masked_op) > 0

        auto_duration = 0.0
        effective_duration = 0.0
        deviation_unknown_points = 0

        for i in range(bound):
            segment = durations[i]
            mode_val = _to_int(masked_mode[i])
            if mode_val not in AUTO_MODES:
                continue
            auto_duration += segment

            # OP 饱和检查（op 信号缺失时不判饱和）
            is_saturated = False
            if has_op and i < len(masked_op):
                op_val = _to_float(masked_op[i])
                is_saturated = (op_val <= op_low + epsilon) or (op_val >= op_high - epsilon)

            # 偏差检查（CAL-03：e_max>0 时逐点检查；pv/sp 值缺失/非法的采样点
            # 计"偏差不可判"，不得计入有效自控——_to_float(None)=0.0 会把缺失
            # 值伪装成零偏差，必须先显式排除）
            is_deviation_ok = True
            if e_max > 0:
                pv_val = masked_pv[i] if i < len(masked_pv) else None
                sp_val = masked_sp[i] if i < len(masked_sp) else None
                if pv_val is None or sp_val is None:
                    is_deviation_ok = False
                    deviation_unknown_points += 1
                else:
                    deviation = abs(_to_float(pv_val) - _to_float(sp_val))
                    is_deviation_ok = deviation < e_max

            # 有效自控：模式自控 AND OP 未饱和 AND 偏差合理
            if not is_saturated and is_deviation_ok:
                effective_duration += segment

        effective_rate = (effective_duration / total_duration) * 100.0
        effective_rate = self._clamp(effective_rate)

        logger.debug(
            "[有效自控率] auto=%.1f, effective=%.1f, total=%.1f, R=%.2f%%",
            auto_duration,
            effective_duration,
            total_duration,
            effective_rate,
        )

        return self._make_result(
            bundle,
            effective_rate,
            {
                "auto_duration_s": round(auto_duration, 2),
                "effective_duration_s": round(effective_duration, 2),
                "total_duration_s": round(total_duration, 2),
                "op_low": op_low,
                "op_high": op_high,
                "epsilon": epsilon,
                "e_max": e_max,
                "deviation_check": "applied" if e_max > 0 else "disabled_nonpositive_e_max",
                "deviation_unknown_points": deviation_unknown_points,
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
        e_max: float,
    ) -> MetricResult:
        """契约口径计算（P3-08c）：分母=完整请求窗口秒数（半开 [start,end)）。

        槽分类（互斥）：
        - MODE/OP/PV/SP 任一无依据（UNKNOWN）→ 不判分子，计 unknownSeconds
          （可能区间上界=分子+未知；不把未知当手动）；
        - 已知但 PV/SP 数值资格不过（valid=False，含 QC Bad 保留有限数值——
          C03-EA3）或 OP 无效或 MODE 无效 → 不进分子，**留在分母**；
        - 已知且自动 ∧ OP 有效未饱和 ∧ PV/SP 有效偏差合格 → 分子。
        """
        signals = bundle.data_block.signals
        mode_sig = signals.get("mode") or []
        op_sig = signals.get("op") or []
        pv_sig = signals.get("pv") or []
        sp_sig = signals.get("sp") or []
        has_op = len(op_sig) > 0

        auto_duration = 0.0
        deviation_unknown_points = 0
        bad_quality_points = 0

        def _eligible(slot: int) -> bool:
            nonlocal deviation_unknown_points, bad_quality_points
            orig = view.indices[slot]
            mode_val = _to_int(mode_sig[orig]) if orig < len(mode_sig) else -1
            if mode_val not in AUTO_MODES:
                return False
            # 质量门禁：MODE 数值资格（无效模式样本不证明自动）
            if not view.role_valid("mode", slot):
                return False
            # OP 饱和检查（缺 op 信号不判饱和；op 无效不进分子——不能证明未饱和）
            if has_op:
                if not view.role_valid("op", slot):
                    return False
                op_val = _to_float(op_sig[orig]) if orig < len(op_sig) else 0.0
                if (op_val <= op_low + epsilon) or (op_val >= op_high - epsilon):
                    return False
            # PV/SP 质量门禁 + 偏差检查（C03-EA3：Bad PV 有数值不算合格）
            if not (view.role_valid("pv", slot) and view.role_valid("sp", slot)):
                bad_quality_points += 1
                return False
            pv_val = pv_sig[orig] if orig < len(pv_sig) else None
            sp_val = sp_sig[orig] if orig < len(sp_sig) else None
            if pv_val is None or sp_val is None:
                deviation_unknown_points += 1
                return False
            if e_max > 0 and abs(_to_float(pv_val) - _to_float(sp_val)) >= e_max:
                return False
            return True

        def _auto_known(slot: int) -> bool:
            orig = view.indices[slot]
            mode_val = _to_int(mode_sig[orig]) if orig < len(mode_sig) else -1
            return mode_val in AUTO_MODES and view.role_valid("mode", slot)

        check_roles = tuple(r for r in view.required_roles if r != "op" or has_op)
        for slot in range(len(view.indices)):
            if not view.roles_known(slot, check_roles):
                continue
            if _auto_known(slot):
                auto_duration += view.slot_seconds[slot]

        accounting = view.ratio_accounting(_eligible, roles=check_roles)
        if accounting.denominator_seconds <= 0:
            return self._make_inconclusive(bundle, "zero_total_duration")
        effective_rate = self._clamp(accounting.proven_ratio * 100.0)

        details: dict[str, Any] = {
            "auto_duration_s": round(auto_duration, 2),
            "effective_duration_s": round(accounting.numerator_seconds, 2),
            "total_duration_s": round(accounting.denominator_seconds, 2),
            "op_low": op_low,
            "op_high": op_high,
            "epsilon": epsilon,
            "e_max": e_max,
            "deviation_check": "applied" if e_max > 0 else "disabled_nonpositive_e_max",
            "deviation_unknown_points": deviation_unknown_points,
            "bad_quality_points": bad_quality_points,
            "time_basis": "half_open_window",
        }
        details.update(accounting.to_details())
        return self._make_result(bundle, effective_rate, details)

    def _read_bounds(self, bundle: MetricDataBundle) -> tuple[float, float, float]:
        """读取 OP 上下限与饱和容差.

        ε 优先级（2026-10-10 用户裁决，可配置默认 0）：CONFIG 信号
        saturation_epsilon（回路级）> 配置链 effective_auto_rate.
        saturation_epsilon（算法参数页）> 默认 0（严格贴限）。
        """
        signals = bundle.data_block.signals
        op_low = _read_float(signals, "op_low", DEFAULT_OP_LOW)
        op_high = _read_float(signals, "op_high", DEFAULT_OP_HIGH)
        epsilon = _read_float(signals, "saturation_epsilon", None)
        if epsilon is None:
            # G2-INC-a：任务固定五层有效参数优先，未固定才读配置链当前缓存
            params = self._effective_params("effective_auto_rate", bundle)
            try:
                epsilon = float(params.get("saturation_epsilon", DEFAULT_EPSILON))
            except (TypeError, ValueError):
                epsilon = DEFAULT_EPSILON
        return op_low, op_high, epsilon

    @staticmethod
    def _read_e_max(bundle: MetricDataBundle, default_ratio: float) -> float:
        """读取偏差最大允许基准 |E|_max."""
        signals = bundle.data_block.signals
        for key in ("e_max", "accuracy_e_max", "error_max"):
            val = MetricCalculatorBase._read_config_scalar(signals, key)
            if val is not None:
                try:
                    return float(val)
                except (TypeError, ValueError):
                    continue
        return NORMALIZED_RANGE * default_ratio


def _to_int(val: Any) -> int:
    """安全转换为 int."""
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return -1


def _to_float(val: Any) -> float:
    """安全转换为 float."""
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _read_float(signals: dict, key: str, default: float) -> float:
    """从信号字典读取 float 值（兼容列表存储）."""
    val = MetricCalculatorBase._read_config_scalar(signals, key)
    if val is None:
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


__all__ = ["EffectiveAutoRateCalculator"]
