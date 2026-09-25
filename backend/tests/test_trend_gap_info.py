"""1b：趋势缺口信息附加（attach_gap_info）回归（2026-09-26）。

覆盖四态：
- 有缺口且超出窗口 → 按窗口裁剪后计入 gaps / observedRatio；
- 无缺口 → gaps=[] / observedRatio=1.0；
- 查询异常 → gaps=[] / observedRatio=None（绝不抛错）；
- 时间戳不足两点（无有效窗口）→ 无法判定，observedRatio=None。

背景：趋势 point 快路径走 TD 端 FILL(PREV)，缺口会被填平成平台线，
序列本身看不出缺口，必须依赖 1a 落在 history_coverage_segment 的 gap 段。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.services.trend_service import attach_gap_info


class _FakeRows:
    def __init__(self, rows: list[tuple[datetime, datetime]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[datetime, datetime]]:
        return self._rows


class _FakeDB:
    """只支持 attach_gap_info 里那一句 select 的最小桩。"""

    def __init__(
        self, rows: list[tuple[datetime, datetime]] | None = None, *, raises: bool = False
    ) -> None:
        self.rows = rows or []
        self.raises = raises

    async def execute(self, *_args: Any, **_kwargs: Any) -> _FakeRows:
        if self.raises:
            raise RuntimeError("db down")
        return _FakeRows(self.rows)


def _window() -> dict[str, Any]:
    # 与趋势返回一致：naive UTC ISO 字符串
    return {
        "timestamps": ["2026-09-25T10:00:00", "2026-09-25T12:00:00"],
        "pointCount": 2,
    }


class TestAttachGapInfo:
    async def test_gap_clamped_to_window(self) -> None:
        """缺口 09:00–11:00 与窗口 10:00–12:00 相交 → 裁剪为 10:00–11:00（3600s，比例 0.5）。"""
        db = _FakeDB(
            [
                (
                    datetime(2026, 9, 25, 9, 0, tzinfo=UTC),
                    datetime(2026, 9, 25, 11, 0, tzinfo=UTC),
                )
            ]
        )
        result = await attach_gap_info(db, "loop-1", _window())  # type: ignore[arg-type]
        assert len(result["gaps"]) == 1
        gap = result["gaps"][0]
        assert gap["seconds"] == 3600.0
        assert gap["start"].startswith("2026-09-25T10:00:00")
        assert gap["end"].startswith("2026-09-25T11:00:00")
        assert result["observedRatio"] == 0.5

    async def test_no_gap_means_full_observation(self) -> None:
        result = await attach_gap_info(_FakeDB([]), "loop-1", _window())  # type: ignore[arg-type]
        assert result["gaps"] == []
        assert result["observedRatio"] == 1.0

    async def test_query_failure_never_raises(self) -> None:
        result = await attach_gap_info(
            _FakeDB(raises=True),  # type: ignore[arg-type]
            "loop-1",
            _window(),
        )
        assert result["gaps"] == []
        assert result["observedRatio"] is None

    async def test_insufficient_timestamps(self) -> None:
        result = await attach_gap_info(
            _FakeDB([]),  # type: ignore[arg-type]
            "loop-1",
            {"timestamps": ["2026-09-25T10:00:00"]},
        )
        assert result["gaps"] == []
        assert result["observedRatio"] is None

    async def test_z_suffix_timestamps_supported(self) -> None:
        """带 Z 的 ISO 串同样可用（趋势不同链路可能给出两种写法）。"""
        db = _FakeDB(
            [
                (
                    datetime(2026, 9, 25, 11, 0, tzinfo=UTC),
                    datetime(2026, 9, 25, 11, 30, tzinfo=UTC),
                )
            ]
        )
        result = await attach_gap_info(
            db,  # type: ignore[arg-type]
            "loop-1",
            {"timestamps": ["2026-09-25T10:00:00.000Z", "2026-09-25T12:00:00.000Z"]},
        )
        assert len(result["gaps"]) == 1
        assert result["gaps"][0]["seconds"] == 1800.0
        assert result["observedRatio"] == 0.75
