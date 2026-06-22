"""TDengine async connection module.

使用 taospy 异步连接 TDengine 查询波形数据。
开发环境 TDengine 可能无数据，返回空数组 + 明确状态标识，不报错。
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)


async def query_trend_data(
    tag_name: str,
    start_time: str,
    end_time: str,
) -> list[dict[str, Any]]:
    """从 TDengine 查询 Tag 的趋势数据。

    Args:
        tag_name: Tag 位号名
        start_time: 起始时间（ISO 格式）
        end_time: 结束时间（ISO 格式）

    Returns:
        数据点列表，每项 {ts, value, quality}。连接失败或无数据返回空数组。
    """
    try:
        # 延迟导入，避免开发环境未安装/无法连接 TDengine 时报错
        import taosws
    except ImportError:
        logger.warning("taosws 未安装，跳过 TDengine 查询")
        return []

    try:
        dsn = f"ws://{settings.TDENGINE_HOST}:{settings.TDENGINE_PORT + 1000}/rest/ws"
        # taosws 使用 WebSocket 端口（默认 6041）
        async with await taosws.connect(
            url=dsn,
            user=settings.TDENGINE_USER,
            password=settings.TDENGINE_PASSWORD,
            database=settings.TDENGINE_DB,
        ) as conn:
            # 查询趋势数据（假设超级表名为 tag_data，按 tag_name 子查询）
            sql = (
                f"SELECT ts, val, quality FROM {settings.TDENGINE_DB}.tag_data "
                f"WHERE tag_name = '{tag_name}' "
                f"AND ts >= '{start_time}' AND ts <= '{end_time}' "
                f"ORDER BY ts ASC"
            )
            result = await conn.query(sql)
            rows: list[dict[str, Any]] = []
            for row in result:
                rows.append(
                    {
                        "ts": str(row[0]),
                        "value": float(row[1]) if row[1] is not None else None,
                        "quality": str(row[2]) if len(row) > 2 and row[2] is not None else "GOOD",
                    }
                )
            return rows
    except Exception as exc:  # noqa: BLE001
        logger.warning("TDengine 查询失败（返回空数组）: %s", exc)
        return []


__all__ = ["query_trend_data"]
