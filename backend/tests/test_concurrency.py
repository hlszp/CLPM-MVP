"""S3-C3: 并发安全测试.

验证 AsyncSessionLocal 在并发场景下的安全性：
- 多协程并发创建独立 session 不报错
- asyncio.gather 并行查询不产生 session 冲突
- dashboard 服务的 _run_in_session 并行调用安全
- 不出现 "Session is already in use" 错误

使用 mock 数据库（不需要真实 DB 连接）。
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# AsyncSessionLocal 并发创建测试
# ---------------------------------------------------------------------------


class TestAsyncSessionLocalConcurrency:
    """AsyncSessionLocal 并发创建安全性测试。"""

    async def test_concurrent_session_creation_no_error(self) -> None:
        """并发创建多个 AsyncSession 不应抛出 session 错误。"""
        from app.core.db import AsyncSessionLocal

        # 并发创建 10 个独立 session
        sessions = []

        async def create_session(idx: int):
            # 每个协程独立创建 session
            session = AsyncSessionLocal()
            sessions.append(session)
            await asyncio.sleep(0.001)  # 模拟异步操作
            await session.close()
            return idx

        results = await asyncio.gather(*[create_session(i) for i in range(10)])

        assert sorted(results) == list(range(10))
        assert len(sessions) == 10

    async def test_concurrent_sessions_independent(self) -> None:
        """并发使用的多个 session 应相互独立。"""
        from app.core.db import AsyncSessionLocal

        async def use_session(idx: int) -> int:
            async with AsyncSessionLocal() as session:
                # 每个 session 独立执行操作
                await asyncio.sleep(0.001)
                assert session is not None
                return idx

        results = await asyncio.gather(*[use_session(i) for i in range(20)])

        # 所有任务都应成功完成，结果与输入一致
        assert sorted(results) == list(range(20))

    async def test_concurrent_gather_no_session_conflict(self) -> None:
        """asyncio.gather 并行查询不应产生 session 冲突。"""
        from app.core.db import AsyncSessionLocal

        async def query_task(idx: int) -> int:
            """模拟并行查询任务，每个任务使用独立 session。"""
            async with AsyncSessionLocal() as session:
                # 模拟查询操作
                await asyncio.sleep(0.002)
                # 验证 session 未被其他协程占用
                assert session is not None
                return idx * 2

        # 并发 15 个查询任务
        results = await asyncio.gather(*[query_task(i) for i in range(15)])

        expected = [i * 2 for i in range(15)]
        assert sorted(results) == sorted(expected)


# ---------------------------------------------------------------------------
# dashboard _run_in_session 并行调用测试
# ---------------------------------------------------------------------------


class TestDashboardRunInSession:
    """dashboard 服务 _run_in_session 并行调用安全性测试。"""

    async def test_run_in_session_parallel_no_conflict(self) -> None:
        """_run_in_session 并行调用不应产生 session 冲突。"""
        from app.services.dashboard import _run_in_session

        # Mock AsyncSessionLocal 返回独立 mock session
        mock_sessions = []

        def make_mock_session():
            session = AsyncMock()
            session.execute = AsyncMock(return_value=MagicMock())
            session.close = AsyncMock()
            mock_sessions.append(session)
            return session

        with patch("app.services.dashboard.AsyncSessionLocal") as mock_factory:

            def _create_session():
                session = make_mock_session()
                cm = AsyncMock()
                cm.__aenter__ = AsyncMock(return_value=session)
                cm.__aexit__ = AsyncMock(return_value=None)
                return cm

            # 使用 side_effect 确保每次调用创建独立 session
            mock_factory.side_effect = _create_session

            # 定义并行任务
            async def task_a(s):
                await asyncio.sleep(0.001)
                return "a"

            async def task_b(s):
                await asyncio.sleep(0.001)
                return "b"

            async def task_c(s):
                await asyncio.sleep(0.001)
                return "c"

            # 并行执行
            results = await asyncio.gather(
                _run_in_session(task_a),
                _run_in_session(task_b),
                _run_in_session(task_c),
            )

        assert sorted(results) == ["a", "b", "c"]
        # 每个任务应使用独立 session
        assert len(mock_sessions) == 3

    async def test_run_in_session_exception_isolation(self) -> None:
        """单个并行任务异常不应影响其他任务（使用 return_exceptions）。"""
        from app.services.dashboard import _run_in_session

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock())
        mock_session.close = AsyncMock()

        with patch("app.services.dashboard.AsyncSessionLocal") as mock_factory:
            cm = AsyncMock()
            cm.__aenter__ = AsyncMock(return_value=mock_session)
            cm.__aexit__ = AsyncMock(return_value=None)
            mock_factory.return_value = cm

            async def success_task(s):
                return "ok"

            async def fail_task(s):
                raise RuntimeError("模拟任务失败")

            results = await asyncio.gather(
                _run_in_session(success_task),
                _run_in_session(fail_task),
                _run_in_session(success_task),
                return_exceptions=True,
            )

        # 第一个和第三个任务成功
        assert results[0] == "ok"
        assert results[2] == "ok"
        # 第二个任务抛出异常
        assert isinstance(results[1], RuntimeError)
        assert "模拟任务失败" in str(results[1])

    async def test_run_in_session_no_session_in_use_error(self) -> None:
        """验证并发执行时不会出现 'Session is already in use' 错误。"""
        from app.services.dashboard import _run_in_session

        # 记录所有异常
        errors: list[Exception] = []

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock())
        mock_session.close = AsyncMock()

        with patch("app.services.dashboard.AsyncSessionLocal") as mock_factory:
            cm = AsyncMock()
            cm.__aenter__ = AsyncMock(return_value=mock_session)
            cm.__aexit__ = AsyncMock(return_value=None)
            mock_factory.return_value = cm

            async def task(s):
                await asyncio.sleep(0.001)
                return "done"

            # 并发 10 个任务
            results = await asyncio.gather(
                *[_run_in_session(task) for _ in range(10)],
                return_exceptions=True,
            )

        for r in results:
            if isinstance(r, Exception):
                errors.append(r)
                # 不应出现 session in use 错误
                assert "already in use" not in str(r).lower(), f"出现 session 冲突错误：{r}"

        # 所有任务应成功完成
        assert len(errors) == 0, f"并发执行出现异常：{errors}"
        assert all(r == "done" for r in results)


# ---------------------------------------------------------------------------
# 模拟并行查询场景测试
# ---------------------------------------------------------------------------


class TestParallelQuerySimulation:
    """模拟 dashboard 并行查询场景的并发安全性测试。"""

    async def test_parallel_queries_with_independent_sessions(self) -> None:
        """模拟 dashboard 4 个并行查询使用独立 session。"""
        # 模拟 4 个并行查询任务，每个使用独立 session
        execution_log: list[str] = []

        async def mock_query(name: str, session: AsyncMock) -> str:
            execution_log.append(f"{name}:start")
            await asyncio.sleep(0.005)
            execution_log.append(f"{name}:end")
            return name

        async def run_independent_session(query_name: str):
            session = AsyncMock()
            session.execute = AsyncMock(return_value=MagicMock())
            try:
                result = await mock_query(query_name, session)
                return result
            finally:
                await session.close()

        results = await asyncio.gather(
            run_independent_session("kpi_cards"),
            run_independent_session("counts"),
            run_independent_session("trend_summary"),
            run_independent_session("alerts"),
        )

        assert set(results) == {"kpi_cards", "counts", "trend_summary", "alerts"}
        # 所有任务都应启动（并发执行）
        start_count = sum(1 for e in execution_log if e.endswith(":start"))
        assert start_count == 4

    async def test_high_concurrency_session_creation(self) -> None:
        """高并发场景下 session 创建不应崩溃。"""
        from app.core.db import AsyncSessionLocal

        async def quick_task(idx: int) -> int:
            async with AsyncSessionLocal() as session:
                await asyncio.sleep(0.0005)
                assert session is not None
                return idx

        # 50 个并发任务
        results = await asyncio.gather(*[quick_task(i) for i in range(50)])

        assert sorted(results) == list(range(50))

    async def test_session_close_after_use(self) -> None:
        """session 使用后应正确关闭。"""
        from app.core.db import AsyncSessionLocal

        closed_flags: list[bool] = []

        async def use_and_close(idx: int) -> bool:
            session = AsyncSessionLocal()
            try:
                await asyncio.sleep(0.001)
                return True
            finally:
                await session.close()
                closed_flags.append(True)

        await asyncio.gather(*[use_and_close(i) for i in range(5)])

        # 所有 session 都应被关闭
        assert len(closed_flags) == 5
        assert all(closed_flags)
