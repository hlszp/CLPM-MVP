"""大查询占满读通道时，写入不等待读线程退出。"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

from app.core import tdengine_native as native


async def test_saturated_reader_does_not_block_insert(monkeypatch):
    read_entered, release_read, written = threading.Event(), threading.Event(), threading.Event()
    reader = ThreadPoolExecutor(max_workers=1)
    writer = ThreadPoolExecutor(max_workers=1)

    class Cursor:
        description = []

        def execute(self, sql):
            if sql.startswith("SELECT"):
                read_entered.set()
                assert release_read.wait(5)
            else:
                written.set()

        def fetchall(self):
            return []

        def close(self):
            pass

    class Connection:
        def cursor(self):
            return Cursor()

    @contextmanager
    def connect():
        yield Connection()

    monkeypatch.setattr(native, "_TDNATIVE_EXECUTOR", reader)
    monkeypatch.setattr(native, "_TDWRITE_EXECUTOR", writer)
    monkeypatch.setattr(native.TDengineConnectionPool, "get_connection", connect)
    blocked = asyncio.create_task(native.execute_native("SELECT slow_read"))
    try:
        assert await asyncio.to_thread(read_entered.wait, 2)
        await asyncio.wait_for(native.execute_native("INSERT INTO test VALUES (1)"), 2)
        assert written.is_set()
        assert not blocked.done()
    finally:
        release_read.set()
        await blocked
        reader.shutdown(wait=True)
        writer.shutdown(wait=True)
