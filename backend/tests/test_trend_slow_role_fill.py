"""趋势快路径慢变量（SP/MODE）窗口开头 null 段种子填充（2026-10-09）。

用户口径「SP/MODE 慢变量每分钟存一遍+前向补齐」的查询侧等价实现：
TD FILL(PREV) 不回看窗口外——窗口开头无前值的 null 段用
read_last_states_before 的「窗口前最后有值状态」填平。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.trend_service import _fetch_trend_fast


class _Mapping:
    def __init__(self, role: str, tag_id: str):
        self.tag_role = role
        self.tag_id = tag_id


def _db_with_mappings() -> MagicMock:
    db = MagicMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = [
        _Mapping("PV", "pv-1"),
        _Mapping("SP", "sp-1"),
        _Mapping("OP", "op-1"),
        _Mapping("MODE", "mode-1"),
    ]
    db.execute = AsyncMock(return_value=result)
    return db


def _buckets() -> dict[str, dict[int, tuple[float | None, int]]]:
    # 三桶：SP/MODE 首桶缺失（窗口开头 null 段）、次桶起有值
    return {
        "pv-1": {0: (10.0, 1), 1000: (11.0, 1), 2000: (12.0, 1)},
        "sp-1": {1000: (5.0, 1), 2000: (5.0, 1)},
        "op-1": {0: (50.0, 1), 1000: (51.0, 1), 2000: (52.0, 1)},
        "mode-1": {1000: (1.0, 1), 2000: (1.0, 1)},
    }


class TestSlowRoleLeadingFill:
    @pytest.mark.asyncio
    async def test_sp_mode_leading_nulls_filled_with_seed(self) -> None:
        with (
            patch(
                "app.services.data_source.point_history_repository.read_trend_buckets",
                new=AsyncMock(return_value=_buckets()),
            ),
            patch(
                "app.services.data_source.point_history_repository.read_last_states_before",
                new=AsyncMock(
                    return_value={
                        "pv-1": None,
                        "sp-1": {"ts": None, "value": 4.5},
                        "op-1": None,
                        "mode-1": {"ts": None, "value": 1.0},
                    }
                ),
            ),
        ):
            out = await _fetch_trend_fast(
                _db_with_mappings(),
                "loop-1",
                datetime(2026, 10, 9, tzinfo=UTC),
                datetime(2026, 10, 10, tzinfo=UTC),
                1,
            )
        # SP/MODE 首桶 null 被窗口前种子填平；MODE 取整
        assert out["sp"] == [4.5, 5.0, 5.0]
        assert out["mode"] == [1, 1, 1]
        # PV/OP 不受影响
        assert out["pv"] == [10.0, 11.0, 12.0]
        assert out["op"] == [50.0, 51.0, 52.0]

    @pytest.mark.asyncio
    async def test_no_seed_keeps_null(self) -> None:
        """窗口前也无值（死点从未推送）→ 保持 null（诚实：无数据可补）。"""
        with (
            patch(
                "app.services.data_source.point_history_repository.read_trend_buckets",
                new=AsyncMock(return_value=_buckets()),
            ),
            patch(
                "app.services.data_source.point_history_repository.read_last_states_before",
                new=AsyncMock(
                    return_value={
                        "pv-1": None,
                        "sp-1": None,
                        "op-1": None,
                        "mode-1": None,
                    }
                ),
            ),
        ):
            out = await _fetch_trend_fast(
                _db_with_mappings(),
                "loop-1",
                datetime(2026, 10, 9, tzinfo=UTC),
                datetime(2026, 10, 10, tzinfo=UTC),
                1,
            )
        assert out["sp"] == [None, 5.0, 5.0]
        assert out["mode"] == [None, 1, 1]

    @pytest.mark.asyncio
    async def test_seed_query_failure_never_blocks(self) -> None:
        """种子查询异常静默降级（趋势主链路不阻断）。"""
        with (
            patch(
                "app.services.data_source.point_history_repository.read_trend_buckets",
                new=AsyncMock(return_value=_buckets()),
            ),
            patch(
                "app.services.data_source.point_history_repository.read_last_states_before",
                new=AsyncMock(side_effect=RuntimeError("td down")),
            ),
        ):
            out: dict[str, Any] = await _fetch_trend_fast(
                _db_with_mappings(),
                "loop-1",
                datetime(2026, 10, 9, tzinfo=UTC),
                datetime(2026, 10, 10, tzinfo=UTC),
                1,
            )
        assert out["sp"] == [None, 5.0, 5.0]
