"""G15 的真实 PostgreSQL 集成断言。

背景：workbench_precalc 每个 (scope, window) 主动保留 64 行历史（供 sparkline），
而 workbench_overview 的两处读查询此前没有 ORDER BY / LIMIT —— 连续运行超过
5.3h（5min x 64）后，装置排名会出现最多 64 份同名重复项，头部 KPI 卡片取到
64 行中任意一行（可能是 5 小时前的旧快照），同页自相矛盾且不可复现。

为何必须是真实 PG：修复依赖 DISTINCT ON，而该语法由数据库执行，mock 会话
看不到去重效果——源码级断言只能证明 SQL 文本含 DISTINCT ON，不能证明真的
取到最新行。本文件补齐这一层。

隔离性：使用专用 scope_id（987654）并在用例内清理，不触碰真实业务数据。
可用性：PG 不可达时 skip（本地需 POSTGRES_* 指向实例；CI 由 S1-a 引入的
postgres service 提供，需 pytest 步骤带上 POSTGRES_* 环境变量）。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, text

from app.core.db import AsyncSessionLocal
from app.models.workbench_summary import WorkbenchWindowSummary
from app.services import workbench_overview as wo

#: 专用测试 scope，避免污染真实业务数据
_TEST_SCOPE_ID = 987654


def _probe_or_skip() -> None:
    """PG 不可达即 skip（不伪造通过，也不让整套测试因环境失败）。"""

    async def _ping() -> None:
        async with AsyncSessionLocal() as s:
            await s.execute(text("SELECT 1"))

    try:
        asyncio.run(_ping())
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL 不可用，跳过真实库断言: {type(exc).__name__}")


class TestLatestRowRealPg:
    """读侧必须只取每个分组的最新 window_end（真实去重效果）。"""

    def test_query_windows_returns_only_latest_row(self) -> None:
        """同一 scope + window 写入 3 个不同 window_end，只应返回最新一行。"""
        _probe_or_skip()

        async def _body() -> None:
            base = datetime.now(UTC).replace(microsecond=0)
            async with AsyncSessionLocal() as s:
                await s.execute(
                    delete(WorkbenchWindowSummary).where(
                        WorkbenchWindowSummary.scope_id == _TEST_SCOPE_ID
                    )
                )
                # 越晚写入的 window_end 越新、score 越高，便于断言取到的是最新
                for offset_s, score in ((3600, 10.0), (1800, 20.0), (60, 30.0)):
                    s.add(
                        WorkbenchWindowSummary(
                            scope_type="GLOBAL",
                            scope_id=_TEST_SCOPE_ID,
                            window_w="24h",
                            window_start=base - timedelta(seconds=offset_s * 2),
                            window_end=base - timedelta(seconds=offset_s),
                            score=score,
                            status="GOOD",  # 见模型 ck_ws_status 约束
                            loop_count=1,
                        )
                    )
                await s.commit()

                rows = await wo._query_windows(s, "GLOBAL", _TEST_SCOPE_ID)
                assert len(rows) == 1, (
                    f"应只返回最新一行，实际 {len(rows)} 行 —— "
                    "64 份历史快照重复渲染的问题回归（G15）"
                )
                assert float(rows[0].score) == 30.0, (
                    f"未取到最新快照：score={rows[0].score}，期望 30.0"
                )

                await s.execute(
                    delete(WorkbenchWindowSummary).where(
                        WorkbenchWindowSummary.scope_id == _TEST_SCOPE_ID
                    )
                )
                await s.commit()

        asyncio.run(_body())
