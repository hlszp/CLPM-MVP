"""TDengine 数据源提供者 — 宽表查询 + taosrest.

数据架构优化 Phase 2：从窄表 7 次查询改为宽表 1 次查询。
- make_query_fn: 使用 query_wide_table_native（宽表一次查 7 列）
- query_trend_data: 保留窄表查询（波形展示路径兼容）
"""

from __future__ import annotations

import logging
from typing import Any

from app.services.data_source.base import QueryFn

logger = logging.getLogger(__name__)


class TDengineProvider:
    """TDengine 数据源提供者（宽表 + taosrest）.

    Phase 2 改造：
    - make_query_fn 从窄表 7 次查询改为宽表 1 次查询
    - query_trend_data 保留窄表查询（兼容波形展示路径）
    """

    def make_query_fn(self, db: Any) -> QueryFn:
        """构造 TDengine 查询函数（宽表查询）。

        替代原 make_dataplanner_query_fn 的 7 次窄表并行查询，
        改为宽表 1 次查询（一次查 7 列 + 质量码）。

        Args:
            db: 异步数据库会话（查询回路-Tag 映射）

        Returns:
            DataPlanner 兼容的查询闭包
        """
        from app.contracts.data_types import RawTimeSeries
        from app.core.tdengine import make_subtable_name
        from app.core.tdengine_native import query_wide_table_native

        async def _query_fn_wide(
            loop_id: str,
            tag_roles: list[str],
            start: Any,
            end: Any,
            interval_s: int,
        ) -> RawTimeSeries:
            """宽表查询闭包：一次查 7 列，替代 7 次窄表查询。"""
            from sqlalchemy import select

            from app.models.loop import LoopTagMapping
            from app.models.tag import TagRegistry

            # 1. 查询 LoopTagMapping 获取任意一个 tag 的 tag_name（用于解析 loop_part）
            m_result = await db.execute(
                select(LoopTagMapping).where(LoopTagMapping.loop_id == loop_id)
            )
            mappings = list(m_result.scalars().all())
            if not mappings:
                logger.debug("宽表查询: 回路 %s 无 tag 映射", loop_id)
                return RawTimeSeries(timestamps=[], signals={}, quality_codes={})

            # 2. 查询 TagRegistry 获取 tag_name
            tag_ids = [str(m.tag_id) for m in mappings]
            t_result = await db.execute(
                select(TagRegistry).where(TagRegistry.id.in_(tag_ids))
            )
            tags = list(t_result.scalars().all())
            if not tags:
                logger.debug("宽表查询: 回路 %s 无 tag 记录", loop_id)
                return RawTimeSeries(timestamps=[], signals={}, quality_codes={})

            # 3. 从 tag_name 解析 loop_part（取第一个 tag_name）
            # tag_name 格式: "LIC-101.PV" → loop_part="LIC-101"
            tag_name = tags[0].tag_name
            loop_part = tag_name.rsplit(".", 1)[0] if "." in tag_name else tag_name
            subtable = make_subtable_name(loop_part)

            # 4. 构造查询时间范围（毫秒精度）
            start_str = _format_ts(start)
            end_str = _format_ts(end)

            # 5. 宽表查询（一次查 7 列 + 质量码）
            try:
                rows = await query_wide_table_native(subtable, start_str, end_str)
            except Exception as exc:  # noqa: BLE001
                logger.warning("宽表查询失败 loop=%s subtable=%s: %s", loop_id, subtable, exc)
                return RawTimeSeries(timestamps=[], signals={}, quality_codes={})

            if not rows:
                logger.debug("宽表查询: 回路 %s 无数据 (subtable=%s)", loop_id, subtable)
                return RawTimeSeries(timestamps=[], signals={}, quality_codes={})

            # 6. 转换为 RawTimeSeries
            timestamps = [_parse_ts(row.get("ts")) for row in rows]
            signals: dict[str, list[Any]] = {}
            for role in tag_roles:
                role_lower = role.lower()
                signals[role_lower] = [row.get(role_lower) for row in rows]

            # PV 质量码
            quality_codes: dict[str, list[int]] = {}
            if "pv" in [r.lower() for r in tag_roles]:
                quality_codes["pv_quality"] = [
                    int(row.get("pv_quality") or 0) for row in rows
                ]

            logger.debug(
                "宽表查询: loop=%s, subtable=%s, points=%d, signals=%s",
                loop_id,
                subtable,
                len(timestamps),
                {k: len(v) for k, v in signals.items()},
            )

            return RawTimeSeries(
                timestamps=timestamps,
                signals=signals,
                quality_codes=quality_codes,
            )

        return _query_fn_wide

    async def query_trend_data(
        self,
        tag_name: str,
        start_time: str,
        end_time: str,
        sample_interval: int = 1,
    ) -> list[dict[str, Any]]:
        """查询单个 tag 的趋势数据（窄表查询，波形展示路径兼容）。

        Args:
            sample_interval: 采样间隔（秒）。TDengine 模式下查询全量数据，
                由上层 LTTB 降采样处理，此参数仅用于日志记录。
        """
        from app.core.tdengine import query_trend_data

        return await query_trend_data(tag_name, start_time, end_time)

    async def close(self) -> None:
        """关闭 TDengine 连接池."""
        from app.core.tdengine import close_client
        from app.core.tdengine_native import TDengineConnectionPool

        await close_client()
        TDengineConnectionPool.close_all()
        logger.info("TDengineProvider 已关闭")


def _format_ts(dt: Any) -> str:
    """格式化 datetime 为 TDengine 查询时间字符串（毫秒精度）。"""
    from datetime import datetime

    if isinstance(dt, datetime):
        return dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    return str(dt)


def _parse_ts(ts_val: Any) -> Any:
    """解析 TDengine 返回的时间戳为 datetime。

    taosrest 默认返回 datetime 对象（convert_timestamp=True）。
    """
    from datetime import datetime

    if isinstance(ts_val, datetime):
        # 保持 naive UTC，对齐 DB TIMESTAMP WITHOUT TIME ZONE
        return ts_val.replace(tzinfo=None) if ts_val.tzinfo else ts_val
    if isinstance(ts_val, str):
        try:
            return datetime.fromisoformat(ts_val.replace("Z", "+00:00")).replace(tzinfo=None)
        except (ValueError, TypeError):
            pass
    return ts_val
