"""Backfill dispatcher loop filtering tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4


def _mock_chord() -> tuple[MagicMock, MagicMock]:
    canvas = MagicMock()
    factory = MagicMock(return_value=canvas)
    return factory, canvas


def test_dispatch_backfill_with_loop_ids_filter() -> None:
    """Valid loop UUIDs are forwarded unchanged to every child signature."""
    from app.tasks.kpi_calc import _dispatch_backfill_chord

    loop_ids = [str(uuid4()), str(uuid4())]
    chord_factory, canvas = _mock_chord()
    with patch("app.tasks.kpi_calc.chord", chord_factory):
        result = _dispatch_backfill_chord(
            "2026-07-04T00:00:00Z",
            "2026-07-04T04:00:00Z",
            loop_ids=loop_ids,
            task_id="task-1",
        )

    chord_factory.assert_called_once()
    canvas.apply_async.assert_called_once_with()
    header = chord_factory.call_args.args[0]
    # _BACKFILL_BATCH_SIZE=1：4 个窗口 → 4 个子任务
    assert len(header) == 4
    assert all(signature.kwargs["loop_ids"] == loop_ids for signature in header)
    assert result["total_windows"] == 4


def test_dispatch_backfill_without_loop_ids() -> None:
    """None retains the all-active-loops behavior."""
    from app.tasks.kpi_calc import _dispatch_backfill_chord

    chord_factory, _ = _mock_chord()
    with patch("app.tasks.kpi_calc.chord", chord_factory):
        _dispatch_backfill_chord(
            "2026-07-04T00:00:00Z",
            "2026-07-04T01:00:00Z",
            loop_ids=None,
            task_id=None,
        )

    header = chord_factory.call_args.args[0]
    assert header[0].kwargs["loop_ids"] is None


def test_backfill_empty_loop_ids_returns_noop() -> None:
    """The dispatcher task does not create a chord for an explicit empty selection."""
    from app.tasks.kpi_calc import backfill_kpi_range

    with patch("app.tasks.kpi_calc._dispatch_backfill_chord") as dispatch:
        result = backfill_kpi_range.run(
            "2026-07-04T00:00:00Z",
            "2026-07-04T02:00:00Z",
            loop_ids=[],
        )

    dispatch.assert_not_called()
    assert result["total_windows"] == 2
    assert result["loop_success"] == 0
