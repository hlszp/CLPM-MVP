"""read_events 分页与截断保护（整改 G12）。

背景
----
read_events 按 7 天分片，每片只发一条带 LIMIT READ_CHUNK_ROWS 的 SQL。
返回行数等于上限时尾部被**静默丢弃**（ORDER BY ts ASC 丢的正是后半段），
而调用方 logical_wide_builder 并无分页——KPI 于是基于只有前半段的数据算出
"看似合理"的结论，属最伤可信度的一类缺陷。

本文件守护：
1. 满批必须以最后一条 ts 为游标继续翻页，结果完整拼接；
2. 游标无法推进或超过翻页上限时显式失败——绝不静默返回不完整结果。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.services.data_source import point_history_repository as repo

_PID = "00000000-0000-0000-0000-0000000000bb"
_T0 = datetime(2026, 9, 13, 0, 0, 0, tzinfo=UTC)


def _row(i: int) -> dict:
    return {
        "point_id": _PID,
        "ts": _T0 + timedelta(seconds=i),
        "value": float(i),
        "quality_class": 1,
        "quality_raw": 1,
        "quality_schema": 1,
        "source_kind": 1,
        "payload_hash": "h",
    }


def _run(monkeypatch, pages: list[list[dict]], limit: int = 3):
    """按页返回预设结果，记录每次 SQL。"""
    calls: list[str] = []

    async def _fake(sql):
        calls.append(sql)
        return pages[len(calls) - 1] if len(calls) <= len(pages) else []

    monkeypatch.setattr(repo, "READ_CHUNK_ROWS", limit)
    monkeypatch.setattr(repo, "execute_native", _fake)
    out = asyncio.run(read_events_wrapper())
    return out, calls


async def read_events_wrapper():
    return await repo.read_events([_PID], _T0, _T0 + timedelta(hours=1))


class TestReadEventsPagination:
    """满批翻页与截断保护。"""

    def test_full_batch_triggers_next_page_and_concatenates(self, monkeypatch) -> None:
        """满批 → 继续翻页，两页结果完整拼接（不得丢尾）。"""
        pages = [[_row(0), _row(1), _row(2)], [_row(3), _row(4)]]
        out, calls = _run(monkeypatch, pages)
        assert len(calls) == 2, f"满批未触发翻页：实际 {len(calls)} 次查询"
        assert len(out[_PID]) == 5, f"结果被截断：实际 {len(out[_PID])} 行，期望 5"

    def test_cursor_uses_last_ts_exclusive(self, monkeypatch) -> None:
        """第二页必须以首末条 ts 为界且改为开区间（避免重复取边界行）。"""
        pages = [[_row(0), _row(1), _row(2)], [_row(3)]]
        _, calls = _run(monkeypatch, pages)
        assert len(calls) == 2
        second = calls[1]
        assert "> " in second, "翻页应使用开区间游标（ts > last_ts）"
        assert repo.format_ts_utc(_row(2)["ts"]) in second

    def test_partial_batch_stops(self, monkeypatch) -> None:
        """不足上限 → 立即停止（不额外查询）。"""
        out, calls = _run(monkeypatch, [[_row(0), _row(1)]])
        assert len(calls) == 1
        assert len(out[_PID]) == 2

    def test_non_advancing_cursor_raises(self, monkeypatch) -> None:
        """游标无法推进 → 显式失败，绝不静默返回不完整结果。"""
        same = [_row(0), _row(0), _row(0)]  # 同一 ts 且满批
        with pytest.raises(RuntimeError, match="游标无法推进"):
            _run(monkeypatch, [same, same])

    def test_page_cap_raises(self, monkeypatch) -> None:
        """超过翻页上限 → 显式失败并提示缩小窗口。"""
        monkeypatch.setattr(repo, "_READ_MAX_PAGES_PER_CHUNK", 2)
        pages = [[_row(0), _row(1), _row(2)], [_row(3), _row(4), _row(5)]]
        with pytest.raises(RuntimeError, match="超过上限"):
            _run(monkeypatch, pages)
