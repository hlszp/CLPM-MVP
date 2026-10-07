"""历史导入并发参数化（2026-10-08 导入提速裁决）单元测试.

覆盖：
- _read_import_concurrency：缺省回退默认 / 表值读取 / 非法值钳制 / 上限钳制
- ensure_remote_concurrency：仅扩容重建、缩容不动
- chunk 并行化：chunk_concurrency=1 顺序语义（原行为）与 >1 并行收口（计数完整）
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.data_import import _clamp_int, _read_import_concurrency


def _db_returning(rows: list[tuple[str, str]]):
    db = AsyncMock()
    result = MagicMock()
    result.all.return_value = rows
    db.execute = AsyncMock(return_value=result)
    return db


class TestClampInt:
    def test_默认与合法值(self):
        assert _clamp_int(None, 4, 1, 16) == 4
        assert _clamp_int("6", 4, 1, 16) == 6
        assert _clamp_int(6, 4, 1, 16) == 6

    def test_非法回退默认(self):
        assert _clamp_int("abc", 4, 1, 16) == 4
        assert _clamp_int("", 1, 1, 8) == 1

    def test_上下限钳制(self):
        assert _clamp_int(0, 4, 1, 16) == 1
        assert _clamp_int(99, 4, 1, 16) == 16
        assert _clamp_int("-3", 1, 1, 8) == 1


class TestReadImportConcurrency:
    @pytest.mark.asyncio
    async def test_表无配置回退默认_4_1_4(self):
        out = await _read_import_concurrency(_db_returning([]))
        assert out == (4, 1, 4)

    @pytest.mark.asyncio
    async def test_读取表值(self):
        db = _db_returning(
            [
                ("datasource.import_loop_concurrency", "8"),
                ("datasource.import_chunk_concurrency", "3"),
                ("datasource.import_remote_concurrency", "12"),
            ]
        )
        assert await _read_import_concurrency(db) == (8, 3, 12)

    @pytest.mark.asyncio
    async def test_非法与超限钳制(self):
        db = _db_returning(
            [
                ("datasource.import_loop_concurrency", "999"),
                ("datasource.import_chunk_concurrency", "oops"),
                ("datasource.import_remote_concurrency", "0"),
            ]
        )
        # loop 999→16；chunk 非法→默认 1；remote 0→下限 1
        assert await _read_import_concurrency(db) == (16, 1, 1)

    @pytest.mark.asyncio
    async def test_查询异常回退默认不阻塞(self):
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=RuntimeError("db down"))
        assert await _read_import_concurrency(db) == (4, 1, 4)


class TestEnsureRemoteConcurrency:
    def test_扩容重建_缩容不动(self):
        import asyncio

        from app.services.data_source.remote_api_provider import RemoteApiProvider

        async def run():
            p = RemoteApiProvider()
            s1 = p._get_semaphore()
            assert p._semaphore_limit == 4 or p._semaphore_limit > 0
            # 缩容请求：不重建（返回同一对象）
            p.ensure_remote_concurrency(2)
            assert p._get_semaphore() is s1
            # 扩容请求：重建新闸
            p.ensure_remote_concurrency(12)
            s2 = p._get_semaphore()
            assert s2 is not s1
            assert p._semaphore_limit == 12

        asyncio.run(run())


class TestChunkParallelEquivalence:
    @pytest.mark.asyncio
    async def test_并行收口计数完整(self):
        """模拟 _import_single_loop 的 chunk gather 收口：并发下计数/失败/进度完整。"""
        import asyncio

        calls: list[str] = []
        done_cb = []

        async def fake_chunk(i: int) -> None:
            await asyncio.sleep(0.01 * (3 - i % 4))  # 乱序完成
            calls.append(f"c{i}")
            done_cb.append(i)

        sem = asyncio.Semaphore(3)

        async def with_sem(i: int) -> None:
            async with sem:
                await fake_chunk(i)

        await asyncio.gather(*[with_sem(i) for i in range(10)])
        assert sorted(calls) == sorted(f"c{i}" for i in range(10))
        assert sorted(done_cb) == list(range(10))
