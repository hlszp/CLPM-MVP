"""DCS PID 参数转换（V62-P1-015）.

使用现有 ``DcsPidStructure`` 模型，在整定推荐 PID（标准形式 Kp/Ti/Td，
秒）与 DCS 私有表示（比例度 PB / 增益、分钟 / 秒、微分滤波）之间转换。

标准形式（平台内部 + 整定算法）：
    Kp: 比例增益（无量纲）
    Ti: 积分时间（秒）
    Td: 微分时间（秒）

DCS 私有表示（由 DcsPidStructure 描述）：
    p:  比例项 — PROPORTION(Kp) 或 PROPORTION_BAND(PB=100/Kp)
    i:  积分时间 — SECONDS 或 MINUTES
    d:  微分时间 — SECONDS 或 MINUTES
    d_filter: 微分滤波器配置（独立于 Td，不影响往返转换）

设计依据：DDS §3.1 / DcsPidStructure 模型 / 算法说明 §4.0.3
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.models.dcs_pid_structure import (
    P_TYPE_PROPORTION_BAND,
    PID_REPRESENTATION_KNOWN,
    PID_REPRESENTATION_UNKNOWN,
    UNIT_MINUTES,
    DcsPidStructure,
)

logger = logging.getLogger(__name__)

#: 标准形式 PID 字段名
_KP = "kp"
_TI = "ti"
_TD = "td"

#: DCS 私有形式 PID 字段名
_P = "p"
_I = "i"
_D = "d"
_D_FILTER = "dFilter"


@dataclass(frozen=True)
class StandardPid:
    """标准形式 PID 参数（Kp/Ti/Td，秒）."""

    kp: float
    ti: float
    td: float = 0.0

    def to_dict(self) -> dict[str, float]:
        return {_KP: self.kp, _TI: self.ti, _TD: self.td}


@dataclass(frozen=True)
class DcsPid:
    """DCS 私有形式 PID 参数.

    p:  比例项值（增益 Kp 或比例度 PB，由 structure.p_type 决定）
    i:  积分时间（秒或分，由 structure.i_unit 决定）
    d:  微分时间（秒或分，由 structure.d_unit 决定）
    d_filter: 微分滤波参数（可选，语义由 structure.d_filter_* 决定）
    """

    p: float
    i: float
    d: float = 0.0
    d_filter: float | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {_P: self.p, _I: self.i, _D: self.d}
        if self.d_filter is not None:
            result[_D_FILTER] = self.d_filter
        return result


def to_standard_pid(dcs_pid: DcsPid, structure: DcsPidStructure) -> StandardPid:
    """DCS 私有 PID → 标准 PID（Kp/Ti/Td，秒）.

    转换规则：
    1. 比例项：PROPORTION_BAND → Kp = 100/PB；PROPORTION → Kp = p
    2. 积分时间：MINUTES → Ti = i × 60；SECONDS → Ti = i
    3. 微分时间：MINUTES → Td = d × 60；SECONDS → Td = d

    微分滤波器不影响标准 Td（DCS 实现细节，标准形式只表达有效 Td）。
    """
    # 比例项
    if structure.p_type == P_TYPE_PROPORTION_BAND:
        if dcs_pid.p == 0:
            raise ValueError("比例度 PB=0 无法转换为增益 Kp（除零）")
        kp = 100.0 / dcs_pid.p
    else:
        # PROPORTION：已经是增益
        kp = dcs_pid.p

    # 积分时间 → 秒
    ti = dcs_pid.i * 60.0 if structure.i_unit == UNIT_MINUTES else dcs_pid.i

    # 微分时间 → 秒
    td = dcs_pid.d * 60.0 if structure.d_unit == UNIT_MINUTES else dcs_pid.d

    return StandardPid(kp=kp, ti=ti, td=td)


def from_standard_pid(
    standard_pid: StandardPid,
    structure: DcsPidStructure,
) -> DcsPid:
    """标准 PID（Kp/Ti/Td，秒）→ DCS 私有 PID.

    转换规则（``to_standard_pid`` 的逆运算）：
    1. 比例项：PROPORTION_BAND → PB = 100/Kp；PROPORTION → p = Kp
    2. 积分时间：MINUTES → i = Ti / 60；SECONDS → i = Ti
    3. 微分时间：MINUTES → d = Td / 60；SECONDS → d = Td

    微分滤波器不在此恢复（DCS 侧滤波配置由结构模板定义，不由整定推荐设置）。
    """
    # 比例项
    if structure.p_type == P_TYPE_PROPORTION_BAND:
        if standard_pid.kp == 0:
            raise ValueError("增益 Kp=0 无法转换为比例度 PB（除零）")
        p = 100.0 / standard_pid.kp
    else:
        p = standard_pid.kp

    # 积分时间 → DCS 单位
    i = standard_pid.ti / 60.0 if structure.i_unit == UNIT_MINUTES else standard_pid.ti

    # 微分时间 → DCS 单位
    d = standard_pid.td / 60.0 if structure.d_unit == UNIT_MINUTES else standard_pid.td

    return DcsPid(p=p, i=i, d=d)


def convert_pid_dict(
    pid_dict: dict[str, Any],
    structure: DcsPidStructure,
    *,
    to_standard: bool,
) -> dict[str, Any]:
    """字典形式 PID 参数转换（便捷工具）.

    Args:
        pid_dict: PID 参数字典
            to_standard=True 时期望键 p/i/d（DCS 私有形式）
            to_standard=False 时期望键 kp/ti/td（标准形式）
        structure: DCS PID 结构模板
        to_standard: True=DCS→标准；False=标准→DCS

    Returns:
        转换后的 PID 参数字典
    """
    if to_standard:
        dcs_pid = DcsPid(
            p=float(pid_dict.get(_P, pid_dict.get(_KP, 0))),
            i=float(pid_dict.get(_I, pid_dict.get(_TI, 0))),
            d=float(pid_dict.get(_D, pid_dict.get(_TD, 0))),
            d_filter=pid_dict.get(_D_FILTER),
        )
        return to_standard_pid(dcs_pid, structure).to_dict()

    standard_pid = StandardPid(
        kp=float(pid_dict.get(_KP, 0)),
        ti=float(pid_dict.get(_TI, 0)),
        td=float(pid_dict.get(_TD, 0)),
    )
    return from_standard_pid(standard_pid, structure).to_dict()


# ---------------------------------------------------------------------------
# P3-03 B1-1：工程事实驱动的转换入口（未知 DCS 表示显式拒绝，不默认标准 kp/ti/td）
# ---------------------------------------------------------------------------


class PidRepresentationUnknownError(ValueError):
    """DCS PID 表示未知（工程事实 status=UNKNOWN），转换不可执行.

    P2-04 工程事实口径承接：未知 DCS 型号（未绑定型号 / 未配置结构 /
    结构显式 representation_status=UNKNOWN）不得默认成标准 kp/ti/td——
    任何"看起来能转其实是猜的"转换都是产品事故（诚实化原则）。
    """


def _structure_from_representation(pid_representation: dict[str, Any]) -> DcsPidStructure:
    """工程事实 ``pidRepresentation``（engineering_facts 输出形）→ 结构模板形.

    Raises:
        PidRepresentationUnknownError: status 非 KNOWN（显式未知拒绝）。
    """
    status = str(pid_representation.get("status") or PID_REPRESENTATION_UNKNOWN)
    if status != PID_REPRESENTATION_KNOWN:
        reason = (
            pid_representation.get("reason")
            or "回路 DCS PID 表示未知（未绑定/未配置/显式 UNKNOWN）"
        )
        raise PidRepresentationUnknownError(
            f"DCS PID 表示未知（status={status}），不得默认标准 kp/ti/td：{reason}"
        )
    p_type = pid_representation.get("pType")
    i_unit = pid_representation.get("iUnit")
    d_unit = pid_representation.get("dUnit")
    if not p_type or not i_unit or not d_unit:
        raise PidRepresentationUnknownError(
            "DCS PID 表示 KNOWN 但 pType/iUnit/dUnit 缺失（数据不一致，按未知拒绝）"
        )
    return DcsPidStructure(
        dcs_model_id=pid_representation.get("modelId"),
        p_type=str(p_type),
        i_unit=str(i_unit),
        d_unit=str(d_unit),
        d_filter_enabled=bool(pid_representation.get("dFilterEnabled") or False),
        d_filter_unit=pid_representation.get("dFilterUnit"),
        d_filter_multiplier=bool(pid_representation.get("dFilterMultiplier") or False),
    )


def standard_pid_from_facts(
    dcs_pid: DcsPid,
    pid_representation: dict[str, Any],
) -> StandardPid:
    """DCS 私有 PID → 标准 PID（工程事实驱动；未知表示显式拒绝）.

    Args:
        dcs_pid: DCS 私有形式参数（p/i/d）
        pid_representation: ``engineering_facts.resolve_loop_engineering_facts``
            输出的 ``pidRepresentation`` 节（status/pType/iUnit/dUnit/...）

    Returns:
        StandardPid（Kp 无量纲、Ti/Td 秒）

    Raises:
        PidRepresentationUnknownError: 表示未知/字段缺失（不默认标准 kp/ti/td）。
    """
    structure = _structure_from_representation(pid_representation)
    return to_standard_pid(dcs_pid, structure)


def dcs_pid_from_standard_facts(
    standard_pid: StandardPid,
    pid_representation: dict[str, Any],
) -> DcsPid:
    """标准 PID → DCS 私有 PID（工程事实驱动；未知表示显式拒绝）.

    Raises:
        PidRepresentationUnknownError: 表示未知/字段缺失。
    """
    structure = _structure_from_representation(pid_representation)
    return from_standard_pid(standard_pid, structure)


__all__ = [
    "DcsPid",
    "PidRepresentationUnknownError",
    "StandardPid",
    "convert_pid_dict",
    "dcs_pid_from_standard_facts",
    "from_standard_pid",
    "standard_pid_from_facts",
    "to_standard_pid",
]
