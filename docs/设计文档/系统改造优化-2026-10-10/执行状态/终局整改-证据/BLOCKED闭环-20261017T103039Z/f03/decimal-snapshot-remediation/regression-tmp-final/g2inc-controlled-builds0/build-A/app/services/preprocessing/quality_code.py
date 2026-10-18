"""质量码映射模块.

将不同来源的原始质量码统一映射为 Good/Bad/Unknown 三态。

支持的质量码 schema（项目约束）：
    - TDengine schema: 1 = Good, 0 = Bad（当前主数据源）
    - OPC DA: 192 (0xC0) = Good
    - OPC UA（算法说明 §4.1.2）: 2 = Good, 3 = Good_Cascaded, 0 = Bad
      注：OPC UA 中 1 = Uncertain，但本项目 TDengine 为主数据源（1 = Good），
      故 1 统一归入 Good 集合，参见下方 _GOOD_CODES 说明。

设计依据：算法说明 §4.1.2, PRD §5.5.1, FDS §5.3.1.2
"""

from __future__ import annotations

import math
from typing import Any

from app.contracts.data_types import QualityStatus

# Good 质量码集合：
#   1   — TDengine Good（主数据源）
#   2   — OPC UA Good
#   3   — OPC UA Good_Cascaded
#   192 — OPC DA Good (0xC0)
_GOOD_CODES: frozenset[int] = frozenset({1, 2, 3, 192})

# Bad 质量码集合：
#   0   — TDengine Bad / OPC UA Bad
_BAD_CODES: frozenset[int] = frozenset({0})


def map_quality_code(raw_code: Any) -> QualityStatus:
    """将原始质量码映射为 Good/Bad/Unknown 三态.

    映射规则（算法说明 §4.1.2 + 项目兼容约束）：
        - ``None`` / 缺省 → Good（容错，视为有效）
        - 1 / 2 / 3 / 192 → Good
        - 0 → Bad
        - 其他 → Unknown

    Args:
        raw_code: 原始质量码（int / float / str / None）

    Returns:
        QualityStatus 三态之一

    设计依据：算法说明 §4.1.2, PRD §5.5.1
    """
    if raw_code is None:
        return QualityStatus.GOOD

    try:
        v = int(float(raw_code))
    except (ValueError, TypeError):
        return QualityStatus.UNKNOWN

    if v in _GOOD_CODES:
        return QualityStatus.GOOD
    if v in _BAD_CODES:
        return QualityStatus.BAD
    return QualityStatus.UNKNOWN


def is_good_quality(raw_code: Any) -> bool:
    """判断质量码是否为 Good.

    兼容 TDengine(1=Good) 和 OPC DA(192=Good) 两种 schema，
    None 缺省值视为 Good（容错）。

    Args:
        raw_code: 原始质量码

    Returns:
        True if Good, False otherwise
    """
    return map_quality_code(raw_code) == QualityStatus.GOOD


def is_nan_or_inf(value: Any) -> bool:
    """判断值是否为 NaN/Inf/None.

    用于异常值检测的 NaN 类别（算法说明 §3.4.3）。

    Args:
        value: 待检测值

    Returns:
        True if value is NaN, Inf, or None
    """
    if value is None:
        return True
    try:
        f = float(value)
    except (ValueError, TypeError):
        return True
    return math.isnan(f) or math.isinf(f)
