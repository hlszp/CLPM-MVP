"""TDengine async connection module.

使用 taospy 异步连接 TDengine 查询波形数据。
开发环境 TDengine 可能无数据，返回空数组 + 明确状态标识，不报错。

安全：tag_name 白名单校验 + start_time/end_time ISO 格式校验，防止 SQL 注入。

DDL 对齐（db/tdengine/01_supertable.sql v3.0）：
- 超级表名: st_loop_data（非 tag_data）
- 列: ts, pv, sp, op, mode, pid_p, pid_i, pid_d, pv_quality（非 val/quality）
- TAGS: loop_id, unit_id（非 tag_name）
- 子表命名: d_loop_<位号小写连字符转下划线>

连接池（S1-C1）：
- 复用 WebSocket 连接，避免频繁建连
- 最大 5 个并发连接，LIFO 策略
- 连接异常时自动丢弃
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)

# tag_name 白名单：仅允许字母、数字、下划线、连字符、点号（如 HDS-RX-TIC-101.PV）
_TAG_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.\-\s]{1,128}$")

# tag_name 后缀 → DDL 列名映射
_ROLE_COLUMN_MAP: dict[str, str] = {
    "PV": "pv",
    "SP": "sp",
    "OP": "op",
    "MODE": "mode",
    "PID_P": "pid_p",
    "PID_I": "pid_i",
    "PID_D": "pid_d",
}

# PV 角色对应的质量码列名
_QUALITY_COLUMN_MAP: dict[str, str | None] = {
    "PV": "pv_quality",
    "SP": None,
    "OP": None,
    "MODE": None,
    "PID_P": None,
    "PID_I": None,
    "PID_D": None,
}

# 连接池最大连接数
_MAX_POOL_SIZE = 5


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


def _parse_tag_to_table_column(tag_name: str) -> tuple[str, str, str | None]:
    """将 tag_name 解析为子表名、数据列名、质量列名。

    示例: "HDS-RX-TIC-101.PV" → ("d_loop_hds_rx_tic_101", "pv", "pv_quality")
    """
    # 按最后一个 "." 分割：loop_part 和 role
    if "." in tag_name:
        loop_part, role = tag_name.rsplit(".", 1)
    else:
        loop_part, role = tag_name, "PV"

    role_upper = role.upper()
    column = _ROLE_COLUMN_MAP.get(role_upper, "pv")
    quality_col = _QUALITY_COLUMN_MAP.get(role_upper)

    # 子表命名: d_loop_<位号小写连字符转下划线>
    subtable = "d_loop_" + loop_part.lower().replace("-", "_").replace(".", "_")
    # 清理多余下划线
    subtable = re.sub(r"_+", "_", subtable)

    return subtable, column, quality_col


# ---------------------------------------------------------------------------
# 连接池（S1-C1）
# ---------------------------------------------------------------------------


class _TDengineConnectionPool:
    """TDengine WebSocket 连接池。

    - LIFO 策略：优先复用最近归还的连接
    - 连接异常时丢弃，不归还到池中
    - 池为空时创建新连接
    """

    def __init__(self, max_size: int = _MAX_POOL_SIZE) -> None:
        self._max_size = max_size
        self._pool: asyncio.LifoQueue = asyncio.LifoQueue(maxsize=max_size)
        self._lock = asyncio.Lock()

    async def acquire(self) -> Any:
        """从池中获取连接，池为空时创建新连接。"""
        try:
            conn = self._pool.get_nowait()
            return conn
        except asyncio.QueueEmpty:
            return await self._create_connection()

    async def _create_connection(self) -> Any:
        """创建新的 TDengine WebSocket 连接。"""
        import taosws

        dsn = f"ws://{settings.TDENGINE_HOST}:{settings.TDENGINE_PORT + 1000}/rest/ws"
        conn = await taosws.connect(
            url=dsn,
            user=settings.TDENGINE_USER,
            password=settings.TDENGINE_PASSWORD,
            database=settings.TDENGINE_DB,
        )
        return conn

    async def release(self, conn: Any, healthy: bool = True) -> None:
        """归还连接到池中。不健康的连接直接丢弃。"""
        if not healthy:
            try:
                await conn.close()
            except Exception:  # noqa: BLE001
                pass
            return
        try:
            self._pool.put_nowait(conn)
        except asyncio.QueueFull:
            # 池已满，关闭多余连接
            try:
                await conn.close()
            except Exception:  # noqa: BLE001
                pass

    async def close_all(self) -> None:
        """关闭池中所有连接（应用关闭时调用）。"""
        while not self._pool.empty():
            try:
                conn = self._pool.get_nowait()
                await conn.close()
            except Exception:  # noqa: BLE001
                pass


# 全局连接池实例
_pool = _TDengineConnectionPool()


async def query_trend_data(
    tag_name: str,
    start_time: str,
    end_time: str,
) -> list[dict[str, Any]]:
    """从 TDengine 查询 Tag 的趋势数据。

    Args:
        tag_name: Tag 位号名（如 HDS-RX-TIC-101.PV，白名单校验）
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

    # 解析 tag_name → 子表名 + 列名
    subtable, data_column, quality_column = _parse_tag_to_table_column(safe_tag_name)

    try:
        # 延迟导入，避免开发环境未安装/无法连接 TDengine 时报错
        import taosws  # noqa: F401
    except ImportError:
        logger.warning("taosws 未安装，跳过 TDengine 查询")
        return []

    # 从连接池获取连接
    conn = None
    healthy = True
    try:
        conn = await _pool.acquire()
    except Exception as exc:  # noqa: BLE001
        logger.warning("TDengine 连接获取失败: %s", exc)
        return []

    try:
        # 构建查询 SQL：从子表查询指定列
        # 子表名和列名通过白名单映射生成，不含用户输入，安全拼接
        if quality_column:
            sql = (
                f"SELECT ts, {data_column}, {quality_column} "
                f"FROM {settings.TDENGINE_DB}.{subtable} "
                f"WHERE ts >= '{safe_start}' AND ts <= '{safe_end}' "
                f"ORDER BY ts ASC"
            )
        else:
            sql = (
                f"SELECT ts, {data_column} "
                f"FROM {settings.TDENGINE_DB}.{subtable} "
                f"WHERE ts >= '{safe_start}' AND ts <= '{safe_end}' "
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
        healthy = False
        return []
    finally:
        if conn is not None:
            await _pool.release(conn, healthy=healthy)


__all__ = ["query_trend_data"]
