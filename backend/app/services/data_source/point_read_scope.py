"""同一批并发 TagGroup 复用只读 IO；最后一个读者退出即释放，不跨任务缓存。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any


class PointReadScope:
    def __init__(self) -> None:
        self.readers = 0
        self._reads: dict[tuple, asyncio.Task] = {}

    async def read(self, key: tuple, factory: Callable[[], Awaitable[Any]]) -> Any:
        if key not in self._reads:
            self._reads[key] = asyncio.create_task(factory())
        # 单个消费者取消不能取消仍被兄弟组消费的查询。
        return await asyncio.shield(self._reads[key])

    async def finish(self) -> None:
        # 包括失败/取消路径：收束已提交的只读 IO，避免外层重试与旧查询叠加。
        try:
            await asyncio.gather(*self._reads.values(), return_exceptions=True)
        finally:
            self._reads.clear()


class PointReadScopes:
    def __init__(self) -> None:
        self._active: dict[tuple, PointReadScope] = {}

    @asynccontextmanager
    async def use(self, loop_id: str, start, end):
        # 闭包可能被后续 Celery 事件循环复用；在途任务也必须按循环隔离。
        key = (asyncio.get_running_loop(), loop_id, start, end)
        scope = self._active.setdefault(key, PointReadScope())
        scope.readers += 1
        try:
            yield scope
        finally:
            scope.readers -= 1
            if scope.readers == 0:
                self._active.pop(key)
                await scope.finish()


async def scoped_read(scope: PointReadScope | None, key: tuple, factory):
    return await scope.read(key, factory) if scope is not None else await factory()
