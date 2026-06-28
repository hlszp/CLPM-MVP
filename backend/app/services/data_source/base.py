"""数据源抽象接口 — HistoryDataProvider 协议.

所有数据源实现（TDengine / RemoteApi）均需实现此协议。
协议返回的 ``query_fn`` 签名与 DataPlanner 的 ``TDengineQueryFn`` 一致，
确保 DataPlanner 无需感知具体数据源。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from app.contracts.data_types import RawTimeSeries

# 查询函数签名（与 DataPlanner.TDengineQueryFn 一致）
QueryFn = Callable[
    [str, list[str], datetime, datetime, int],
    Awaitable[RawTimeSeries],
]


@runtime_checkable
class HistoryDataProvider(Protocol):
    """历史数据源提供者协议.

    实现类需提供 ``make_query_fn`` 方法，返回与 DataPlanner
    兼容的查询函数闭包。

    用法::

        provider = get_provider()
        query_fn = provider.make_query_fn(db)
        planner = DataPlanner(tdengine_query_fn=query_fn, ...)
    """

    def make_query_fn(self, db: Any) -> QueryFn:
        """构造数据源查询函数.

        Args:
            db: 异步数据库会话（用于查询回路-Tag 映射关系）

        Returns:
            查询函数闭包，签名:
            ``(loop_id, tag_roles, start, end, interval_s) → RawTimeSeries``
        """
        ...

    async def close(self) -> None:
        """释放资源（HTTP 连接池等）."""
        ...
