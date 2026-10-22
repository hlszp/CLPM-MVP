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
        self._points: dict[tuple, dict[str, asyncio.Future]] = {}
        self._pending: dict[tuple, dict[str, asyncio.Future]] = {}
        self._dispatches: dict[tuple, asyncio.Task] = {}
        self._batches: list[asyncio.Task] = []

    async def read(self, key: tuple, factory: Callable[[], Awaitable[Any]]) -> Any:
        if key not in self._reads:
            self._reads[key] = asyncio.create_task(factory())
        # 单个消费者取消不能取消仍被兄弟组消费的查询。
        return await asyncio.shield(self._reads[key])

    async def read_points(self, key: tuple, point_ids: list[str], factory) -> dict:
        """同一事件循环轮次内合并点集合；已提交批次只补查新点。"""
        points = self._points.setdefault(key, {})
        missing = set(point_ids) - points.keys()
        if missing:
            pending = self._pending.setdefault(key, {})
            for pid in missing:
                points[pid] = pending[pid] = asyncio.get_running_loop().create_future()
            if key not in self._dispatches:
                task = asyncio.create_task(self._load_points(key, factory))
                self._dispatches[key] = task
                self._batches.append(task)
        values = await asyncio.gather(*(asyncio.shield(points[pid]) for pid in point_ids))
        return dict(zip(point_ids, values, strict=True))

    async def _load_points(self, key: tuple, factory) -> None:
        await asyncio.sleep(0)
        pending = self._pending.pop(key)
        self._dispatches.pop(key)
        try:
            values = await factory(sorted(pending))
            results = {pid: values.get(pid) for pid in pending}
        except BaseException as exc:
            for future in pending.values():
                future.set_exception(exc)
        else:
            for pid, future in pending.items():
                future.set_result(results[pid])

    async def finish(self) -> None:
        # 包括失败/取消路径：收束已提交的只读 IO，避免外层重试与旧查询叠加。
        try:
            await asyncio.gather(*self._reads.values(), *self._batches, return_exceptions=True)
            # 消费无人继续等待的异常（例如最后一个消费者取消），不遗留 Future 警告。
            await asyncio.gather(
                *(f for points in self._points.values() for f in points.values()),
                return_exceptions=True,
            )
        finally:
            self._reads.clear()
            self._points.clear()
            self._pending.clear()
            self._dispatches.clear()
            self._batches.clear()


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


async def scoped_point_read(
    scope: PointReadScope | None, key: tuple, point_ids: list[str], factory
):
    if scope is None:
        return await factory(point_ids)
    return await scope.read_points(key, point_ids, factory)
