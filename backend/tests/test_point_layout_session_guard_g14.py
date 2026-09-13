"""point 布局不得并发使用共享 AsyncSession（整改 G14 守护）。

背景：红线原文见 tdengine_provider.py —— DataPlanner 会并发执行多个 tagGroup 查询，
这些查询共享同一个 AsyncSession，而 SQLAlchemy 明确不允许同一 AsyncSession 并发
execute。legacy 路径已用「先串行解析并缓存」规避，并配有结构性回归
（tests/test_runtime_regressions.py）；但 **point 布局曾把元数据查询留在共享 session
上且仍处于 asyncio.gather 之下**——与 2026-07-20「全回路取数失败、只能重启 worker」
事故同一根因，且只在生产并发量下复现。

本轮已修（三处改走 _metadata_execute 的独立短会话）。既有守护只覆盖 legacy 路径，
故补本文件守护 point 布局：断言 logical_wide_builder 不再直接使用调用方传入的
db.execute。

说明：本用例是**源码级**守护（构造真实并发需要 PG + TDengine + 并发编排，成本高于
收益）。它能防止「又把元数据查询挂回共享 session」这一具体回归，但不能证明运行时
无并发冲突。
"""

from __future__ import annotations

import inspect

from app.services.data_source import logical_wide_builder as b


class TestPointLayoutNoSharedSessionExecute:
    """point 布局的元数据查询必须走独立短会话。"""

    def test_builder_has_no_shared_db_execute(self) -> None:
        """logical_wide_builder 不得直接 await db.execute(...)。"""
        src = inspect.getsource(b)
        assert "db.execute(" not in src, (
            "logical_wide_builder 又出现 db.execute —— point 布局会在 asyncio.gather "
            "下并发使用共享 AsyncSession（G14 回归，与 2026-07-20 取数事故同根因）"
        )
        assert "_metadata_execute(" in src, (
            "元数据查询未走独立短会话助手 _metadata_execute（G14 修复被移除？）"
        )

    def test_metadata_execute_opens_own_session(self) -> None:
        """_metadata_execute 必须自建会话，不得复用调用方 session。"""
        src = inspect.getsource(b._metadata_execute)
        assert "AsyncSessionLocal()" in src, (
            "_metadata_execute 未自建独立会话——仍会与并发查询共享 AsyncSession"
        )
