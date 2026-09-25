"""用户输入 ISO 8601 时间解析共享契约.

背景（2026-09 系统性检查）：多个端点与服务各自复制了一份形如

    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return datetime.fromisoformat(s)   # 同样的调用，必然再次抛错

的"兜底"实现。该 except 分支与 try 分支等价，属于死代码：一旦用户传入
非法时间串（例如手改 URL 的 ?startTime=abc），ValueError 会穿透到全局
异常处理器，返回 500 + 堆栈，而不是可读的 400 参数错误。

本模块提供唯一实现：非法输入一律抛 BizError(ERR_PARAM, 400)，
错误消息与既有的 dataplanner / tuning 口径保持一致。

注意：本模块只负责"解析与报错"，不改变调用方对时区的既有语义
（需要 naive UTC 的调用方自行调用 to_naive_utc）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.core.exceptions import BizError

__all__ = ["parse_iso_datetime", "to_naive_utc"]


def to_naive_utc(dt: datetime) -> datetime:
    """aware datetime → naive UTC；naive 输入原样返回（按 UTC 解释）。

    禁止 aware/naive 混用做库查询与比较；带偏移量的输入必须先换算到 UTC
    再去掉时区标记（直接 replace(tzinfo=None) 会丢掉偏移量）。
    """
    if dt.tzinfo is not None:
        return dt.astimezone(UTC).replace(tzinfo=None)
    return dt


def parse_iso_datetime(value: Any, *, field: str = "时间") -> datetime:
    """解析用户输入的 ISO 8601 字符串.

    - 接受 Z 后缀、+08:00 偏移、纯日期（2026-09-01）与空格分隔
      （2026-09-01 08:00:00）；
    - 保留解析出的 tzinfo，调用方按需用 to_naive_utc 归一化；
    - 非法/空输入抛 BizError(ERR_PARAM, 400)，绝不让 ValueError 变成 500。
    """
    if not isinstance(value, str) or not value.strip():
        raise BizError(
            code="ERR_PARAM",
            message=f"{field} 不能为空（需 ISO 8601）",
            status_code=400,
        )
    raw = value.strip()
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise BizError(
            code="ERR_PARAM",
            message=f"{field} 不是合法 ISO 8601 时间: {raw}",
            status_code=400,
        ) from None
