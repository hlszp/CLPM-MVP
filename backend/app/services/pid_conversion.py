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


__all__ = [
    "DcsPid",
    "StandardPid",
    "convert_pid_dict",
    "from_standard_pid",
    "to_standard_pid",
]
