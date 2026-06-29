"""TDengine 数据源提供者 — 包装现有 query_trend_data + make_dataplanner_query_fn.

此实现是零改动的适配层：直接复用 ``app.core.tdengine.make_dataplanner_query_fn``，
保持与 v4.0 重构后的生产链路完全一致。
"""

from __future__ import annotations

import logging
from typing import Any

from app.services.data_source.base import QueryFn

logger = logging.getLogger(__name__)


class TDengineProvider:
    """TDengine 数据源提供者.

    直接复用现有 ``make_dataplanner_query_fn`` 适配器闭包，
    不引入额外抽象层，性能开销为零。
    """

    def make_query_fn(self, db: Any) -> QueryFn:
        """构造 TDengine 查询函数.

        Args:
            db: 异步数据库会话（查询回路-Tag 映射）

        Returns:
            ``make_dataplanner_query_fn`` 闭包
        """
        from app.core.tdengine import make_dataplanner_query_fn

        return make_dataplanner_query_fn(db)

    async def query_trend_data(
        self, tag_name: str, start_time: str, end_time: str
    ) -> list[dict[str, Any]]:
        """查询单个 tag 的趋势数据（直接委托给 app.core.tdengine.query_trend_data）."""
        from app.core.tdengine import query_trend_data

        return await query_trend_data(tag_name, start_time, end_time)

    async def close(self) -> None:
        """关闭 TDengine httpx 连接池."""
        from app.core.tdengine import close_client

        await close_client()
        logger.info("TDengineProvider 已关闭")
