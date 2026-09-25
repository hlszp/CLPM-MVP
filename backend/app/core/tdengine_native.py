"""TDengine 连接器（taosrest 封装），用于写入和批量查询。

性能优势（实测）：
- 批量 INSERT 1000 行：7ms（~142K 行/秒）
- 宽表查询 1000 行：10ms
- 相比单行 INSERT（483 行/秒）提升 ~295 倍

设计说明：
- 使用 taosrest（taospy 内置 REST 连接器），无需 libtaos.dylib 客户端库
- taosrest 基于 HTTP REST API，通过连接池复用连接
- 同步调用通过 asyncio.to_thread 包装为异步，兼容 Celery AsyncTask

连接池：
- 线程安全（threading.Lock 保护）
- max_size=10，足够覆盖 Celery 线程池 + RealtimeSubscriber
- 连接绑定 event loop，loop 变化时自动重建

设计依据：
- spec: docs/过程文档/data-architecture-optimization-spec-2026-07-15.md §3.2.2
- TDengine 3.x REST API: https://docs.tdengine.com/reference/rest-api/
"""

from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC
from typing import TYPE_CHECKING, Any

from app.core.config import settings

if TYPE_CHECKING:
    from collections.abc import Iterator

logger = logging.getLogger(__name__)

# TDengine REST API 端口（原生端口 + 11，如 6030→6041, 7104→7115）
_TD_REST_PORT = settings.TDENGINE_PORT + 11

# TD 调用专用线程池（0920 间歇断流修复）：此前所有 TD REST 调用走
# asyncio 默认线程池，与趋势/分析等长查询（单次 9~18s）共享少数线程——
# 查询高峰期实时写入的 to_thread 排队饥饿，flush 挂起 → 队列满仓 →
# 全回路间歇断流。独立线程池保证实时写入不再与重查询争抢
_TDNATIVE_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="tdnative")


class TDengineConnectionPool:
    """TDengine REST 连接池（线程安全）。

    taosrest 的 TaosRestConnection 内部使用 requests.Session，
    不是线程安全的，因此需要连接池管理。

    连接生命周期：
    - get_connection: 从池中取，池空则创建
    - 归还: 用完归还到池，池满则关闭
    - close_all: 应用关闭时调用，清理所有连接
    """

    _pool: list[Any] = []  # list[TaosRestConnection]
    _lock = threading.Lock()
    _max_size: int = 10
    _created_count: int = 0  # 已创建的总连接数（用于日志）

    @classmethod
    def _create_connection(cls) -> Any:
        """创建新的 taosrest 连接。

        timezone=UTC：taosrest 默认将 TIMESTAMP 列转为客户端本地时区的
        naive datetime（环境相关，+8 机器上得到 +8 墙钟），显式指定 UTC
        后返回 aware UTC datetime，由上层 ``_parse_ts`` 统一转 naive UTC，
        保证查询结果时间戳口径与部署机器时区无关（P0-3 修复）。
        """

        from taosrest import connect

        url = f"http://{settings.TDENGINE_HOST}:{_TD_REST_PORT}"
        conn = connect(
            url=url,
            user=settings.TDENGINE_USER,
            password=settings.TDENGINE_PASSWORD,
            database=settings.TDENGINE_DB,
            timezone=UTC,
            # taosrest 默认 timeout=None（requests 无限等待），TDengine 无响应时
            # 调用线程会永久阻塞（导入任务"停滞"根因之一），显式超时快速失败
            timeout=settings.TDENGINE_REST_TIMEOUT,
        )
        cls._created_count += 1
        logger.debug("创建 TDengine REST 连接 #%d (url=%s)", cls._created_count, url)
        return conn

    @classmethod
    @contextmanager
    def get_connection(cls) -> Iterator[Any]:
        """获取连接（从池中取，用完归还）。

        用法:
            with TDengineConnectionPool.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(sql)
                ...
        """
        conn: Any | None = None
        with cls._lock:
            if cls._pool:
                conn = cls._pool.pop()
        if conn is None:
            conn = cls._create_connection()
        try:
            yield conn
        finally:
            with cls._lock:
                if len(cls._pool) < cls._max_size:
                    cls._pool.append(conn)
                else:
                    # 池已满，丢弃连接
                    try:
                        conn.close()
                    except Exception:  # noqa: BLE001
                        pass

    @classmethod
    def close_all(cls) -> None:
        """关闭所有连接（应用关闭时调用）。"""
        with cls._lock:
            count = len(cls._pool)
            for conn in cls._pool:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
            cls._pool.clear()
            if count:
                logger.info("关闭 %d 个 TDengine REST 连接", count)


async def execute_native(sql: str) -> list[dict[str, Any]]:
    """异步执行 SQL（通过 asyncio.to_thread 包装同步调用）。

    Args:
        sql: SQL 语句（调用方需确保安全，不接受外部输入拼接）

    Returns:
        行列表，每项 {column: value}。DML 语句返回空列表。

    Raises:
        Exception: SQL 执行失败时抛出
    """

    def _execute() -> list[dict[str, Any]]:
        with TDengineConnectionPool.get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(sql)
                rows = cursor.fetchall()
                # description: [[name, type, bytes], ...]
                fields = [desc[0] for desc in cursor.description] if cursor.description else []
                if not rows or not fields:
                    return []
                return [dict(zip(fields, row, strict=False)) for row in rows]
            finally:
                cursor.close()

    # 0920 加固：asyncio 层超时——同步 REST 调用虽自带 timeout=60s，但
    # 取消无法中断已阻塞线程；此处超时向上抛错，调用方（flush 循环）下一拍
    # 重试，避免写入协程被单条慢查询无限期钉死。
    # 专用线程池：与默认执行器隔离，实时写入不与趋势/分析长查询争抢
    loop = asyncio.get_running_loop()
    return await asyncio.wait_for(loop.run_in_executor(_TDNATIVE_EXECUTOR, _execute), timeout=90)


async def execute_native_effective(sql: str) -> int:
    """异步执行 DML 语句，返回影响行数。

    用于 INSERT/DELETE/UPDATE 语句。

    Args:
        sql: DML SQL 语句

    Returns:
        影响行数

    Raises:
        Exception: SQL 执行失败时抛出
    """

    def _execute() -> int:
        with TDengineConnectionPool.get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute(sql)
                return cursor.affected_rows or 0
            finally:
                cursor.close()

    loop = asyncio.get_running_loop()
    return await asyncio.wait_for(loop.run_in_executor(_TDNATIVE_EXECUTOR, _execute), timeout=90)
