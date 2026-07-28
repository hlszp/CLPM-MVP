"""运行时回归测试：连接预算、Beat 所有权与历史导入失败语义。"""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


@pytest.mark.asyncio
async def test_tdengine_query_fn_concurrent_wide_queries_share_session_safely() -> None:
    """并发 query_fn 调用共享同一 AsyncSession 时不得崩溃。

    历史：2026-07-28 移除模块级 asyncio.Lock（跨事件循环绑定导致全回路取数
    瘫痪，见 commit f45f498a）。此后并发安全性由 ``_subtable_cache`` 保障——
    宽表名解析缓存命中后不再触碰 AsyncSession，并发查询直接走 TDengine。
    本测试预填充缓存，验证并发 wide_table 查询路径不报错。
    """
    import time

    from app.services.data_source import tdengine_provider as provider_module
    from app.services.data_source.tdengine_provider import TDengineProvider

    # 预填充宽表名缓存，使 _resolve_subtable 不触碰 DB（模拟热缓存场景）
    provider_module._subtable_cache["loop-1"] = (
        "d_loop_lic_101",
        "LIC-101",
        time.monotonic() + 3600.0,
    )
    try:
        db = MagicMock()
        db.execute = AsyncMock()

        wide_query = AsyncMock(return_value=[])
        with (
            patch("app.core.tdengine_native.query_wide_table_native", wide_query),
            patch(
                "app.services.data_source.realtime_subscriber.get_subscriber",
                return_value=None,
            ),
        ):
            query_fn = TDengineProvider().make_query_fn(db)
            await asyncio.gather(
                *[
                    query_fn(
                        loop_id="loop-1",
                        tag_roles=["pv", "sp"],
                        start="2026-07-17T00:00:00",
                        end="2026-07-17T01:00:00",
                        interval_s=1,
                    )
                    for _ in range(4)
                ]
            )

        # 缓存命中时 DB 不应被调用；4 次并发查询各执行一次 wide_table 查询
        db.execute.assert_not_called()
        assert wide_query.await_count == 4
    finally:
        provider_module._subtable_cache.pop("loop-1", None)


def test_kpi_concurrency_stays_within_database_budget() -> None:
    """预热和计算并发都不能再按回路总数扩张。"""
    from app.tasks.kpi_calc import _PREWARM_CONCURRENCY, CONCURRENCY

    assert CONCURRENCY <= 5
    assert _PREWARM_CONCURRENCY <= 5


def test_stop_beat_keeps_pid_file_owned_by_existing_process(tmp_path, monkeypatch) -> None:
    """reload 实例未创建 Beat 时不得删除现有 Beat 的 PID 文件。"""
    from app import main

    monkeypatch.chdir(tmp_path)
    pid_file = tmp_path / "celerybeat.pid"
    pid_file.write_text(str(os.getpid()))
    monkeypatch.setattr(main, "_celery_beat_process", None)

    main._stop_celery_beat()

    assert pid_file.exists()


@pytest.mark.asyncio
async def test_history_fetch_timeout_is_reported_as_data_source_error(monkeypatch) -> None:
    """上游超时必须成为明确失败，不能伪装成空数据成功。"""
    from app.core.config import settings
    from app.services.data_import import HistoryDataSourceError, _fetch_remote_history

    # 测试需与本地 .env 解耦：CI 环境无 HISTORY_DATA_API_URL，
    # 否则会提前命中"未配置"分支而非超时分支
    monkeypatch.setattr(settings, "HISTORY_DATA_API_URL", "http://example.invalid/history")

    guard = MagicMock()
    guard.fetch_history_guarded = AsyncMock(side_effect=httpx.ReadTimeout("timed out"))

    with (
        patch("app.services.data_import._get_remote_guard", return_value=guard),
        patch("asyncio.sleep", new=AsyncMock()),
        pytest.raises(HistoryDataSourceError, match="远端历史数据 API 超时"),
    ):
        await _fetch_remote_history(
            ["LIC-101.PV"],
            "2026-07-17T00:00:00",
            "2026-07-17T01:00:00",
            1,
        )


@pytest.mark.asyncio
async def test_zero_point_import_marks_loop_and_task_failed() -> None:
    """远端返回零点时导入任务必须失败并记录可见错误。"""
    from app.core import db as db_module
    from app.schemas.loop_data import ImportStatus
    from app.services import data_import

    session = MagicMock()
    session.close = AsyncMock()
    update_task = AsyncMock()
    update_task_cas = AsyncMock(return_value=("UPDATED", ""))

    with (
        patch.object(db_module, "AsyncSessionLocal", return_value=session),
        patch.object(
            data_import,
            "_batch_get_loop_data",
            new=AsyncMock(
                return_value={
                    "loop-1": {
                        "role_tag_map": {"PV": "LIC-101.PV"},
                        "unit_id": "unit-1",
                        "subtable": "d_loop_lic_101",
                    }
                }
            ),
        ),
        patch.object(data_import, "_import_single_loop", new=AsyncMock(return_value=0)),
        patch.object(data_import, "_is_task_cancelled", new=AsyncMock(return_value=False)),
        patch.object(data_import, "_update_task", new=update_task),
        patch.object(data_import, "_update_task_cas", new=update_task_cas),
    ):
        result = await data_import.import_history_data(
            loop_ids=["loop-1"],
            ts_start="2026-07-17T00:00:00",
            ts_end="2026-07-17T01:00:00",
            task_id="task-1",
        )

    assert result["succeeded"] == 0
    assert result["failed"] == 1
    assert "未返回可导入数据" in result["errors"][0]
    assert any(
        call.kwargs.get("new_status") == ImportStatus.FAILED.value
        and "未返回可导入数据" in call.kwargs.get("error_message", "")
        for call in update_task_cas.await_args_list
    )
