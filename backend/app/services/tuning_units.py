"""辨识 K 单位与标准 PID 的显式换算层（P3-03 B1-2，2026-10-11）.

背景（任务书 B1 范围 2，记忆教训：signals 归一化 0~100，波形 API 归一化
vs tag 实时工程值有 10 倍尺度差）：

- **历史辨识链**（``tuning.identify_model_from_history`` → DataPlanner 预处理
  Step③）：PV/SP 按 PV tag 量程、OP 按 OP tag 量程归一化为 0~100 百分比，
  输出的过程增益 K 量纲为 **%PV/%OP（归一化域）**；
- **阶跃实验链**（``tuning.identify_model`` → 波形 API）：PV/OP 为 TDengine
  点存储**原值域**（与归一化前的 tag 值同域），K 量纲随存储域。

同一物理过程在两链上的 K 相差一个尺度因子（量程比值），不换算直接比较
即出现"10 倍尺度差"事故。本模块提供唯一的仿射换算入口：

- 单位/量程变化属于仿射变换（08 号文 §3.3），PV/SP 同物理量同换算、
  OP 归一化与限幅同步换算；
- 增益换算：``K_dst = K_src × (pv_span_dst/pv_span_src) × (op_span_src/op_span_dst)``
  （归一化域 span=100）；
- **量程改单位后的等价标准 PID**：Kp 随量程比值缩放，Ti/Td 为时间量纲
  不变（03 号文 P3-03 验收"量程改单位后的等价标准 PID"）；
- 量程缺失时显式返回未知（``UnknownConversion``），禁止默认 0~100 冒充
  已核实量程（与 engineering_facts 的 DEFAULT_0_100 三分口径一致）。

本模块为纯函数层，不读库、不落库；量程事实由调用方从
``engineering_facts.resolve_loop_engineering_facts`` 取。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

#: 归一化域的固定跨度（0~100）
NORMALIZED_SPAN = 100.0

#: K 单位域标识（辨识结果 unitBasis.basis 取值）
BASIS_NORMALIZED = "NORMALIZED_0_100"
BASIS_TAG_STORED = "TAG_STORED"
BASIS_ENGINEERING = "ENGINEERING"


class UnknownConversion(ValueError):
    """量程/单位事实缺失，换算不可执行（显式未知，不默认 0~100 冒充）."""


@dataclass(frozen=True)
class SignalSpan:
    """一个信号轴的量程事实（仿射换算基准）.

    Attributes:
        lower: 量程下限
        upper: 量程上限
        source: 量程来源（沿用 engineering_facts 三分口径：
            TAG_RANGE / DEFAULT_0_100 / UNKNOWN）
    """

    lower: float
    upper: float
    source: str = "TAG_RANGE"

    @property
    def span(self) -> float:
        return self.upper - self.lower

    def valid(self) -> bool:
        return (
            math.isfinite(self.lower)
            and math.isfinite(self.upper)
            and abs(self.span) > 1e-12
            and self.source != "UNKNOWN"
        )

    def to_dict(self) -> dict[str, Any]:
        return {"lower": self.lower, "upper": self.upper, "source": self.source}


def normalized_span() -> SignalSpan:
    """归一化域固定跨度 0~100（planner Step③ 的目标域）."""
    return SignalSpan(lower=0.0, upper=NORMALIZED_SPAN, source="NORMALIZED_FIXED")


def value_to_normalized(value: float, span: SignalSpan) -> float:
    """原值 → 归一化 0~100（与 planner Step③ 同式：(v−min)/span×100）."""
    if not span.valid():
        raise UnknownConversion("量程事实缺失或非法，不能执行归一化换算")
    return (value - span.lower) / span.span * NORMALIZED_SPAN


def value_from_normalized(value: float, span: SignalSpan) -> float:
    """归一化 0~100 → 原值（Step③ 逆变换）."""
    if not span.valid():
        raise UnknownConversion("量程事实缺失或非法，不能执行逆归一化换算")
    return value / NORMALIZED_SPAN * span.span + span.lower


def convert_gain(
    k: float,
    src_pv: SignalSpan,
    src_op: SignalSpan,
    dst_pv: SignalSpan,
    dst_op: SignalSpan,
) -> float:
    """过程增益跨域换算（仿射量纲比，数值内核容差 1e-6 级）.

    K_dst = K_src × (pv_span_dst/pv_span_src) × (op_span_src/op_span_dst)

    Raises:
        UnknownConversion: 任一量程缺失/非法。
    """
    for name, span in (
        ("src_pv", src_pv),
        ("src_op", src_op),
        ("dst_pv", dst_pv),
        ("dst_op", dst_op),
    ):
        if not span.valid():
            raise UnknownConversion(f"量程 {name} 缺失或非法（{span.to_dict()}），增益换算不可执行")
    return k * (dst_pv.span / src_pv.span) * (src_op.span / dst_op.span)


def equivalent_standard_pid(
    kp: float,
    ti: float,
    td: float,
    src_pv: SignalSpan,
    src_op: SignalSpan,
    dst_pv: SignalSpan,
    dst_op: SignalSpan,
) -> tuple[float, float, float]:
    """量程改单位后的等价标准 PID（Kp 随量程比值缩放；Ti/Td 时间量纲不变）.

    标准形式 Kp 作用为 ΔOP/Δ误差（各自域内）：换域时
    ``Kp_dst = Kp_src × (op_span_dst/op_span_src) × (pv_span_src/pv_span_dst)``
    （与过程增益的量纲比互为倒数方向）；Ti/Td 以秒计，与信号量纲无关，
    保持不变（03 号文 P3-03 验收"量程改单位后的等价标准 PID"）。

    Returns:
        (kp_dst, ti, td)

    Raises:
        UnknownConversion: 任一量程缺失/非法。
    """
    for name, span in (
        ("src_pv", src_pv),
        ("src_op", src_op),
        ("dst_pv", dst_pv),
        ("dst_op", dst_op),
    ):
        if not span.valid():
            raise UnknownConversion(
                f"量程 {name} 缺失或非法（{span.to_dict()}），等价 PID 换算不可执行"
            )
    kp_dst = kp * (dst_op.span / src_op.span) * (src_pv.span / dst_pv.span)
    return kp_dst, ti, td


def unit_basis_block(
    basis: str,
    *,
    pv_span: SignalSpan | None,
    op_span: SignalSpan | None,
    note: str,
    k_source: float | None = None,
    k_converted: float | None = None,
    converted_basis: str | None = None,
    unknown_reason: str | None = None,
) -> dict[str, Any]:
    """辨识结果 ``unitBasis`` 注入块的统一构造（诚实未知：缺事实给原因）."""
    block: dict[str, Any] = {
        "basis": basis,
        "pvSpan": pv_span.to_dict() if pv_span is not None else None,
        "opSpan": op_span.to_dict() if op_span is not None else None,
        "note": note,
    }
    if k_source is not None:
        block["kSource"] = k_source
    if k_converted is not None:
        block["kConverted"] = k_converted
        block["convertedBasis"] = converted_basis
    if unknown_reason is not None:
        block["kConvertedUnknownReason"] = unknown_reason
    return block


def spans_from_engineering_facts(facts: dict[str, Any] | None) -> tuple[SignalSpan, SignalSpan]:
    """从工程事实提取归一化换算量程（planner Step③ 同源基准）.

    归一化链的量程优先级与 ``data_planner._load_preprocess_config`` 一致：
    tag 量程 > 默认 0~100（默认假设显式标注 DEFAULT_0_100，不冒充已核实）。

    Returns:
        (pv_span, op_span)：恒为有效 SignalSpan（缺失回落 0~100 并标注来源）。
    """
    facts = facts or {}

    def _span(section: str) -> SignalSpan:
        rng = facts.get(section) or {}
        lower, upper = rng.get("lower"), rng.get("upper")
        if lower is not None and upper is not None:
            span = SignalSpan(lower=float(lower), upper=float(upper), source="TAG_RANGE")
            if span.valid():
                return span
        return SignalSpan(lower=0.0, upper=NORMALIZED_SPAN, source="DEFAULT_0_100")

    return _span("pvRange"), _span("opTagRange")


__all__ = [
    "BASIS_ENGINEERING",
    "BASIS_NORMALIZED",
    "BASIS_TAG_STORED",
    "NORMALIZED_SPAN",
    "SignalSpan",
    "UnknownConversion",
    "convert_gain",
    "equivalent_standard_pid",
    "normalized_span",
    "spans_from_engineering_facts",
    "unit_basis_block",
    "value_from_normalized",
    "value_to_normalized",
]
