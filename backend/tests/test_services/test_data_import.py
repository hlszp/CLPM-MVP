"""data_import 服务单元测试.

验证核心转换函数和任务跟踪逻辑。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.data_import import (
    _convert_to_wide_rows,
    _map_quality,
    _parse_float_val,
    _parse_int_val,
    _parse_ts_str,
    _task_to_response,
)


class TestParseHelpers:
    """解析辅助函数测试."""

    def test_parse_float_val_valid(self):
        assert _parse_float_val("1.5") == 1.5
        assert _parse_float_val(1.5) == 1.5
        assert _parse_float_val("0") == 0.0

    def test_parse_float_val_invalid(self):
        assert _parse_float_val(None) is None
        assert _parse_float_val("") is None
        assert _parse_float_val("abc") is None

    def test_parse_int_val_valid(self):
        assert _parse_int_val("1") == 1
        assert _parse_int_val(1) == 1
        assert _parse_int_val("1.0") == 1

    def test_parse_int_val_invalid(self):
        assert _parse_int_val(None) is None
        assert _parse_int_val("") is None
        assert _parse_int_val("abc") is None

    def test_map_quality_good(self):
        assert _map_quality(1) == 1
        assert _map_quality(192) == 1

    def test_map_quality_bad(self):
        assert _map_quality(0) == 0
        assert _map_quality(2) == 0
        assert _map_quality(None) == 0
        assert _map_quality("abc") == 0

    def test_parse_ts_str_iso(self):
        result = _parse_ts_str("2026-07-15T10:00:00")
        assert result is not None
        assert "2026-07-15 10:00:00" in result

    def test_parse_ts_str_with_z(self):
        result = _parse_ts_str("2026-07-15T10:00:00Z")
        assert result is not None
        assert "2026-07-15 10:00:00" in result

    def test_parse_ts_str_invalid(self):
        assert _parse_ts_str("invalid") is None


class TestConvertToWideRows:
    """宽表行转换测试."""

    def test_convert_basic(self):
        """基本转换：7 个角色都有数据."""
        timestamps = ["2026-07-15T10:00:00", "2026-07-15T10:00:01"]
        series_map = {
            "LIC-101.PV": {"values": ["1.0", "1.1"], "qualities": [1, 1]},
            "LIC-101.SP": {"values": ["2.0", "2.0"], "qualities": [1, 1]},
            "LIC-101.OP": {"values": ["50.0", "51.0"], "qualities": [1, 1]},
            "LIC-101.MODE": {"values": ["1", "1"], "qualities": [1, 1]},
            "LIC-101.PID_P": {"values": ["0.5", "0.5"], "qualities": [1, 1]},
            "LIC-101.PID_I": {"values": ["0.1", "0.1"], "qualities": [1, 1]},
            "LIC-101.PID_D": {"values": ["0.01", "0.01"], "qualities": [1, 1]},
        }
        role_tag_map = {
            "PV": "LIC-101.PV",
            "SP": "LIC-101.SP",
            "OP": "LIC-101.OP",
            "MODE": "LIC-101.MODE",
            "PID_P": "LIC-101.PID_P",
            "PID_I": "LIC-101.PID_I",
            "PID_D": "LIC-101.PID_D",
        }
        rows = _convert_to_wide_rows((timestamps, series_map), role_tag_map)
        assert len(rows) == 2
        # 第一行: (ts, pv, sp, op, mode, pid_p, pid_i, pid_d, pv_quality)
        ts, pv, sp, op, mode, pid_p, pid_i, pid_d, pv_quality = rows[0]
        assert "2026-07-15 10:00:00" in ts
        assert pv == 1.0
        assert sp == 2.0
        assert op == 50.0
        assert mode == 1
        assert pid_p == 0.5
        assert pid_i == 0.1
        assert pid_d == 0.01
        assert pv_quality == 1

    def test_convert_with_missing_roles(self):
        """部分角色缺失时，缺失值应为 None."""
        timestamps = ["2026-07-15T10:00:00"]
        series_map = {
            "LIC-101.PV": {"values": ["1.0"], "qualities": [1]},
        }
        role_tag_map = {
            "PV": "LIC-101.PV",
            "SP": "LIC-101.SP",  # 无数据
        }
        rows = _convert_to_wide_rows((timestamps, series_map), role_tag_map)
        assert len(rows) == 1
        ts, pv, sp, op, mode, pid_p, pid_i, pid_d, pv_quality = rows[0]
        assert pv == 1.0
        assert sp is None
        assert op is None
        assert pv_quality == 1

    def test_convert_empty_timestamps(self):
        """空时间戳返回空列表."""
        rows = _convert_to_wide_rows(([], {}), {})
        assert rows == []

    def test_convert_bad_quality(self):
        """坏质量码映射为 0."""
        timestamps = ["2026-07-15T10:00:00"]
        series_map = {
            "LIC-101.PV": {"values": ["1.0"], "qualities": [0]},
        }
        role_tag_map = {"PV": "LIC-101.PV"}
        rows = _convert_to_wide_rows((timestamps, series_map), role_tag_map)
        assert len(rows) == 1
        _, _, _, _, _, _, _, _, pv_quality = rows[0]
        assert pv_quality == 0


class TestTaskResponse:
    """任务响应转换测试."""

    def test_task_to_response_basic(self):
        data = {
            "task_id": "test-123",
            "status": "RUNNING",
            "progress": "0.5",
            "loop_count": "10",
            "imported_count": "5",
            "error_count": "0",
            "ts_start": "2026-07-15T00:00:00",
            "ts_end": "2026-07-15T10:00:00",
            "created_at": "2026-07-15T10:00:00",
            "started_at": "2026-07-15T10:00:01",
            "finished_at": "",
            "error_message": "",
            "created_by": "admin",
            "conflict_strategy": "overwrite",
            "trigger_backfill": "false",
        }
        resp = _task_to_response(data)
        assert resp["taskId"] == "test-123"
        assert resp["status"] == "RUNNING"
        assert resp["progress"] == 0.5
        assert resp["loopCount"] == 10
        assert resp["importedCount"] == 5
        assert resp["errorCount"] == 0
        assert resp["triggerBackfill"] is False


class TestImportHistoryData:
    """import_history_data 集成测试（mock 依赖）."""

    @pytest.mark.asyncio
    async def test_import_empty_loop_ids(self):
        """空回路列表应正常返回零结果."""
        from app.services.data_import import import_history_data

        result = await import_history_data(
            loop_ids=[],
            ts_start="2026-07-15T00:00:00",
            ts_end="2026-07-15T01:00:00",
        )
        assert result["total"] == 0
        assert result["succeeded"] == 0
        assert result["failed"] == 0

    @pytest.mark.asyncio
    async def test_import_with_task_id_cancelled(self):
        """任务被取消时应提前终止."""
        from app.services.data_import import import_history_data

        mock_db = AsyncMock()
        # 模拟 execute().scalars().all() 返回空列表（无 tag 映射）
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute.return_value = mock_result

        mock_session = AsyncMock()
        # mock_session 在 _batch_get_loop_data 中直接作为 db 使用，需要 execute 返回 mock_result
        mock_session.execute.return_value = mock_result
        mock_session.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session.__aexit__ = AsyncMock(return_value=None)

        with (
            patch(
                "app.services.data_import._is_task_cancelled",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "app.services.data_import._update_task",
                new=AsyncMock(),
            ),
            patch("app.core.db.AsyncSessionLocal", return_value=mock_session),
        ):
            result = await import_history_data(
                loop_ids=["loop-1"],
                ts_start="2026-07-15T00:00:00",
                ts_end="2026-07-15T01:00:00",
                task_id="test-task",
            )
            assert result["succeeded"] == 0
