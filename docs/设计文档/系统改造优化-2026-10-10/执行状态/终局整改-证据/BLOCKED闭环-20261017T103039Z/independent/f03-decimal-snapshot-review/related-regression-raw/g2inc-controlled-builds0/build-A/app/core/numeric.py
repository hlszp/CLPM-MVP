"""工业实时数值解析共享契约（数据链路整改 R06，S0 固定）.

采集（realtime_subscriber）、REST 实时值（services/tag.py、services/monitor.py）
与 WS 消费方必须引用同一套解析规则，禁止各自复制实现：

- 解析失败或得到非有限数（NaN/Infinity/±1e999 溢出）一律返回 ``None``，
  绝不折算为 0，绝不把非有限值写入 JSON/SQL；
- 空串/None 表示"本次无值"，与"值无效"同样返回 ``None``；数值有效性
  与 quality 字段相互独立（无效值不得吞掉质量更新，反之亦然）；
- 合法科学计数法（如 ``"1.5E3"``）必须照常解析；
- MODE 等整数语义字段额外要求 int32 范围（TDengine BIGINT 安全余量），
  小数输入按向零截断取整，超界视为无效。

前端实时消费方（composables/views）须保持与本模块一致的语义（R06 前端侧）。
"""

from __future__ import annotations

import math
from typing import Any

# 整数语义字段（MODE 等）的可接受范围：int32
_INT32_MIN = -(2**31)
_INT32_MAX = 2**31 - 1


def parse_finite_float(raw: Any) -> float | None:
    """解析为有限 float；无效/非有限/空值返回 None.

    接受工业推送的原始字面量：``"12.5"``、``"1.5E3"``、数字类型；
    拒绝 ``"-1.#QNAN0"``、``"nan"``、``"Infinity"``、``"1e999"``（溢出为 inf）、
    空串/空白/None/bool。
    """
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int | float):
        try:
            value = float(raw)
        except (OverflowError, ValueError):  # pragma: no cover - 极大 int 转 float
            return None
        return value if math.isfinite(value) else None
    text = str(raw).strip()
    if not text:
        return None
    try:
        value = float(text)
    except (ValueError, TypeError, OverflowError):
        return None
    return value if math.isfinite(value) else None


def parse_mode_int(raw: Any) -> int | None:
    """解析为 int32 范围内的整数（MODE 等整数语义字段）；无效返回 None.

    小数输入向零截断（``"2.7"`` → 2）；非有限或超 int32 范围返回 None。
    """
    value = parse_finite_float(raw)
    if value is None:
        return None
    ivalue = int(value)  # value 已保证有限，int() 不会 OverflowError
    if not (_INT32_MIN <= ivalue <= _INT32_MAX):
        return None
    return ivalue


def finite_or_none(value: Any) -> float | None:
    """守卫已解析数值：仅有限 float 原样通过，其余返回 None.

    用于 JSON 载荷出口（防止上游已写入的 NaN/Infinity 经序列化泄漏到
    REST/WS 响应）。
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        as_float = float(value)
    except (OverflowError, ValueError):  # pragma: no cover
        return None
    return as_float if math.isfinite(as_float) else None
