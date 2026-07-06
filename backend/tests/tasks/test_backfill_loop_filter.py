"""backfill_kpi_range loop_ids 过滤测试."""

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_do_backfill_with_loop_ids_filter():
    """_do_backfill 传入 loop_ids 时应只计算指定回路."""
    from app.tasks.kpi_calc import _do_backfill

    # mock _do_calculate 和 _do_calculate_node_kpi
    with (
        patch(
            "app.tasks.kpi_calc._do_calculate",
            new_callable=AsyncMock,
            return_value={"success": 2, "inconclusive": 0, "failed": 0},
        ) as mock_calc,
        patch(
            "app.tasks.kpi_calc._do_calculate_node_kpi",
            new_callable=AsyncMock,
            return_value={"success": 1},
        ),
    ):
        result = await _do_backfill(
            "2026-07-04T00:00:00Z",
            "2026-07-04T02:00:00Z",
            loop_ids=["loop-1", "loop-2"],
        )

    # 2 小时窗口 × 2 次调用 _do_calculate
    assert mock_calc.call_count == 2
    # 每次调用都应传入 loop_ids
    for call in mock_calc.call_args_list:
        assert call.kwargs.get("loop_ids") == ["loop-1", "loop-2"]
    assert result["total_windows"] == 2
    assert result["loop_success"] == 4  # 2 窗口 × 2 成功


@pytest.mark.asyncio
async def test_do_backfill_without_loop_ids():
    """_do_backfill 不传 loop_ids 时 _do_calculate 的 loop_ids 应为 None（全量）."""
    from app.tasks.kpi_calc import _do_backfill

    with (
        patch(
            "app.tasks.kpi_calc._do_calculate",
            new_callable=AsyncMock,
            return_value={"success": 5, "inconclusive": 0, "failed": 0},
        ) as mock_calc,
        patch(
            "app.tasks.kpi_calc._do_calculate_node_kpi",
            new_callable=AsyncMock,
            return_value={"success": 1},
        ),
    ):
        await _do_backfill("2026-07-04T00:00:00Z", "2026-07-04T01:00:00Z")

    assert mock_calc.call_count == 1
    # loop_ids 应为 None（保持原全量行为）
    assert mock_calc.call_args.kwargs.get("loop_ids") is None


@pytest.mark.asyncio
async def test_do_backfill_empty_loop_ids():
    """_do_backfill 传入空列表时应返回 0 窗口结果."""
    from app.tasks.kpi_calc import _do_backfill

    with (
        patch(
            "app.tasks.kpi_calc._do_calculate",
            new_callable=AsyncMock,
        ) as mock_calc,
        patch(
            "app.tasks.kpi_calc._do_calculate_node_kpi",
            new_callable=AsyncMock,
        ),
    ):
        result = await _do_backfill(
            "2026-07-04T00:00:00Z",
            "2026-07-04T02:00:00Z",
            loop_ids=[],
        )

    # 空列表应跳过计算
    assert mock_calc.call_count == 0
    assert result["total_windows"] == 2
    assert result["loop_success"] == 0
