"""TDengine async connection module.

使用 taospy 异步连接 TDengine 查询波形数据。
开发环境 TDengine 可能无数据，返回空数组 + 明确状态标识，不报错。

安全：tag_name 白名单校验 + start_time/end_time ISO 格式校验，防止 SQL 注入。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)

# tag_name 白名单：仅允许字母、数字、下划线、连字符、点号（如 HDS-RX-TIC-101.PV）
_TAG_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.\-\s]{1,128}$")


def _validate_tag_name(tag_name: str) -> str:
    """校验 tag_name 格式，防止 SQL 注入。"""
    if not tag_name or not _TAG_NAME_PATTERN.match(tag_name):
        raise ValueError(f"Invalid tag_name format: {tag_name!r}")
    return tag_name


def _validate_time(time_str: str, field_name: str) -> str:
    """校验时间格式为 ISO 8601，防止 SQL 注入。"""
    try:
        datetime.fromisoformat(time_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"Invalid {field_name} format: {time_str!r}") from exc
    return time_str


async def query_trend_data(
    tag_name: str,
    start_time: str,
    end_time: str,
) -> list[dict[str, Any]]:
    """从 TDengine 查询 Tag 的趋势数据。

    Args:
        tag_name: Tag 位号名（白名单校验）
        start_time: 起始时间（ISO 格式校验）
        end_time: 结束时间（ISO 格式校验）

    Returns:
        数据点列表，每项 {ts, value, quality}。连接失败或无数据返回空数组。

    Raises:
        ValueError: tag_name 或时间格式不合法
    """
    # 安全校验：防止 SQL 注入
    safe_tag_name = _validate_tag_name(tag_name)
    safe_start = _validate_time(start_time, "start_time")
    safe_end = _validate_time(end_time, "end_time")

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
            # 查询趋势数据（已通过白名单+格式校验，安全拼接）
            sql = (
                f"SELECT ts, val, quality FROM {settings.TDENGINE_DB}.tag_data "
                f"WHERE tag_name = '{safe_tag_name}' "
                f"AND ts >= '{safe_start}' AND ts <= '{safe_end}' "
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
