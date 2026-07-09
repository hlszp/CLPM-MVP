"""MODE 控制模式标准枚举（对齐 DDS §3.1 / 算法说明 §4.0.3）.

本系统采用统一的 5 种标准 MODE 值，覆盖主流 DCS 控制模式：

| standard_mode_value | mode_label | 中文  | is_auto | 含义 |
|---|---|---|---|---|
| 0                   | MANUAL     | 手动  | FALSE   | 操作员直接操作 OP |
| 1                   | AUTO       | 自动  | TRUE    | 单回路 PID 自动控制 |
| 2                   | CAS        | 串级  | TRUE    | 主-副回路串级 |
| 3                   | REMOTE     | 远程  | TRUE    | SCADA/上位机远程设定 |
| 4                   | APC        | 先控  | TRUE    | 先进过程控制（MPC 等） |

不同 DCS 厂商的原始 MODE 编码可能不同（通过 `dcs_mode_mapping` 表映射），
但本系统内部统一使用 `StandardMode` 枚举值。

参考文档：
- DDS §2.22 LoopModeMapping / §3.1 超级表定义
- 算法说明 §4.0.3 calc_auto_mode_rate / §4.2 calc_effective_auto_rate
- GB/T 44693.2-2024 附录 B.1
"""

from __future__ import annotations

from enum import IntEnum
from typing import Final


class StandardMode(IntEnum):
    """MODE 控制模式标准枚举.

    与 TDengine 超级表 `mode` 字段、`dcs_mode_mapping.standard_mode_value` 列、
    `loop_mode_mapping.mode_value` 列保持一致。
    """

    MANUAL = 0  # 手动
    AUTO = 1  # 自动
    CAS = 2  # 串级
    REMOTE = 3  # 远程
    APC = 4  # 先控


#: MODE 中文标签字典（用于前端展示 / 日志可读性）
MODE_LABELS_ZH: Final[dict[int, str]] = {
    StandardMode.MANUAL.value: "手动",
    StandardMode.AUTO.value: "自动",
    StandardMode.CAS.value: "串级",
    StandardMode.REMOTE.value: "远程",
    StandardMode.APC.value: "先控",
}


#: MODE 英文标签字典（与 DDS §2.22 mode_label 字段对齐）
MODE_LABELS_EN: Final[dict[int, str]] = {
    StandardMode.MANUAL.value: "MANUAL",
    StandardMode.AUTO.value: "AUTO",
    StandardMode.CAS.value: "CAS",
    StandardMode.REMOTE.value: "REMOTE",
    StandardMode.APC.value: "APC",
}


#: 计入自控率的 MODE 值集合（AUTO/CAS/REMOTE/APC）
#: 对齐 `app.services.metric_calculator.auto_mode.AUTO_MODES` 与
#: `app.services.node_performance.DEFAULT_AUTO_MODES`
AUTO_MODES: Final[frozenset[int]] = frozenset(
    {
        StandardMode.AUTO.value,
        StandardMode.CAS.value,
        StandardMode.REMOTE.value,
        StandardMode.APC.value,
    }
)


#: 所有合法的标准 MODE 值集合（含 MANUAL）
ALL_STANDARD_MODES: Final[frozenset[int]] = frozenset(MODE_LABELS_ZH.keys())


#: 饼图配色（Hex，与 v6.1 ZL 工业设计规范对齐）
#: 手动-红橙（警示）、自动-绿（正常）、串级-蓝、远程-紫、先控-青
MODE_CHART_COLORS: Final[dict[int, str]] = {
    StandardMode.MANUAL.value: "#d4380d",
    StandardMode.AUTO.value: "#52c41a",
    StandardMode.CAS.value: "#1890ff",
    StandardMode.REMOTE.value: "#722ed1",
    StandardMode.APC.value: "#13c2c2",
}


__all__ = [
    "ALL_STANDARD_MODES",
    "AUTO_MODES",
    "MODE_CHART_COLORS",
    "MODE_LABELS_EN",
    "MODE_LABELS_ZH",
    "StandardMode",
]
