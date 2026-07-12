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

# 单 tag 趋势查询函数签名（与 app.core.tdengine.query_trend_data 一致）
# 返回 list[dict]，每个 dict 含 {ts, value, quality}
TrendQueryFn = Callable[
    [str, str, str],
    Awaitable[list[dict[str, Any]]],
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

    对于不走 DataPlanner 的遗留代码（如 monitor.py 的单 tag 趋势查询），
    可直接调用 ``query_trend_data`` 方法，签名与
    ``app.core.tdengine.query_trend_data`` 保持一致。
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

    async def query_trend_data(
        self, tag_name: str, start_time: str, end_time: str,
        sample_interval: int = 1,
    ) -> list[dict[str, Any]]:
        """查询单个 tag 的趋势数据（兼容 query_trend_data 签名）.

        Args:
            tag_name: Tag 位号（如 "LIC-101.PV"）
            start_time: 开始时间（ISO 8601 字符串）
            end_time: 结束时间（ISO 8601 字符串）
            sample_interval: 采样间隔（秒），默认 1s。
                远程 API 模式下透传给外部接口；TDengine 模式下用于
                后端 LTTB 降采样参考。

        Returns:
            趋势数据列表，每个元素为 ``{"ts": str, "value": float|None, "quality": int|str}``
        """
        ...

    async def close(self) -> None:
        """释放资源（HTTP 连接池等）."""
        ...
