"""data_import 服务单元测试.

验证核心转换函数和任务跟踪逻辑。
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services.data_import import (
    HistoryDataSourceError,
    _convert_to_wide_rows,
    _fetch_remote_history,
    _map_quality,
    _parse_dt,
    _parse_float_val,
    _parse_int_val,
    _parse_ts_str,
    _task_to_response,
    prune_import_task_index,
    sweep_stale_running_tasks,
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
        """naive（无时区）视为已在目标时区 _TARGET_TZ（Asia/Shanghai）。"""
        result = _parse_ts_str("2026-07-15T10:00:00")
        assert result is not None
        assert "2026-07-15 10:00:00" in result

    def test_parse_ts_str_with_z(self):
        """带 Z 后缀的 UTC 时间应显式 astimezone 到 Asia/Shanghai（+8h）。"""
        result = _parse_ts_str("2026-07-15T10:00:00Z")
        assert result is not None
        # 10:00 UTC → 18:00 CST
        assert "2026-07-15 18:00:00" in result

    def test_parse_ts_str_with_utc_offset(self):
        """带 +00:00 偏移的 UTC 时间应转换到 Asia/Shanghai。"""
        result = _parse_ts_str("2026-07-15T10:00:00+00:00")
        assert result is not None
        assert "2026-07-15 18:00:00" in result

    def test_parse_ts_str_with_non_utc_offset(self):
        """非 UTC 偏移（如 -05:00）应正确转换到 Asia/Shanghai。"""
        result = _parse_ts_str("2026-07-15T05:00:00-05:00")
        assert result is not None
        # 05:00-05:00 = 10:00 UTC → 18:00 CST
        assert "2026-07-15 18:00:00" in result

    def test_parse_ts_str_invalid(self):
        assert _parse_ts_str("invalid") is None

    def test_parse_dt_naive(self):
        """naive datetime 视为已在 _TARGET_TZ，返回 naive（无 tzinfo）。"""

        dt = _parse_dt("2026-07-15T10:00:00")
        assert dt.tzinfo is None
        assert dt.year == 2026 and dt.hour == 10

    def test_parse_dt_with_z(self):
        """带 Z 后缀的 UTC 应 astimezone 到 _TARGET_TZ（+8h），返回 naive。"""

        dt = _parse_dt("2026-07-15T10:00:00Z")
        assert dt.tzinfo is None
        # 10:00 UTC → 18:00 CST
        assert dt.hour == 18

    def test_parse_dt_with_offset(self):
        """带 +00:00 偏移的 UTC 应转换到 _TARGET_TZ（+8h）。"""

        dt = _parse_dt("2026-07-15T10:00:00+00:00")
        assert dt.tzinfo is None
        assert dt.hour == 18


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


class TestFetchRemoteHistoryRetry:
    """_fetch_remote_history 重试逻辑测试（P0 改造 + 共享熔断/限流）.

    所有请求经共享守卫（RemoteApiProvider）发出，mock ``_get_remote_guard``
    返回的守卫实例。覆盖场景:
    - 200 OK 不重试
    - 504 触发重试，重试后 200 成功
    - 504 重试 3 次仍失败，抛出 HistoryDataSourceError
    - 超时（httpx.TimeoutException）触发重试
    - 400 不可重试，直接抛出
    - 熔断中（RemoteApiCircuitOpenError）快速失败，不重试
    """

    def _make_guard(self, side_effect=None, return_value=None) -> MagicMock:
        """构造 mock 共享守卫（fetch_history_guarded 为 AsyncMock）。"""
        guard = MagicMock()
        guard.fetch_history_guarded = AsyncMock(side_effect=side_effect, return_value=return_value)
        return guard

    @pytest.fixture
    def _mock_settings(self):
        """模拟 settings 配置，避免依赖真实 .env。"""
        with patch("app.services.data_import.settings") as mock_settings:
            mock_settings.HISTORY_DATA_API_URL = "http://mock-api/HistoryData/Get"
            mock_settings.HISTORY_DATA_API_TOKEN = ""
            mock_settings.HISTORY_DATA_API_TIMEOUT = 120.0
            yield mock_settings

    def _make_response(self, status_code: int = 200, payload: dict | None = None) -> MagicMock:
        """构造 mock httpx.Response."""
        resp = MagicMock()
        resp.status_code = status_code
        resp.text = "<html>error</html>" if status_code != 200 else ""
        resp.json.return_value = payload or {
            "code": 200,
            "data": {
                "timestamps": ["2026-07-15T10:00:00"],
                "series": [
                    {
                        "tagCode": "TI-101.PV",
                        "values": ["1.0"],
                        "qualities": [1],
                    }
                ],
            },
        }
        return resp

    @pytest.mark.asyncio
    async def test_200_ok_no_retry(self, _mock_settings):
        """200 OK 时不触发重试。"""
        mock_guard = self._make_guard(return_value=self._make_response(200))

        with (
            patch("app.services.data_import._get_remote_guard", return_value=mock_guard),
            patch("asyncio.sleep", new=AsyncMock()) as mock_sleep,
        ):
            result = await _fetch_remote_history(
                tag_codes=["TI-101.PV"],
                start_time="2026-07-15T10:00:00",
                end_time="2026-07-15T11:00:00",
                interval=1,
            )

        assert mock_guard.fetch_history_guarded.await_count == 1  # 只请求 1 次
        assert mock_sleep.await_count == 0  # 没有重试等待
        timestamps, series_map = result
        assert len(timestamps) == 1
        assert "TI-101.PV" in series_map

    @pytest.mark.asyncio
    async def test_504_retry_then_success(self, _mock_settings):
        """504 触发重试，第 2 次成功。"""
        mock_guard = self._make_guard(
            side_effect=[
                self._make_response(504),  # 首次 504
                self._make_response(200),  # 重试成功
            ]
        )

        with (
            patch("app.services.data_import._get_remote_guard", return_value=mock_guard),
            patch("asyncio.sleep", new=AsyncMock()) as mock_sleep,
        ):
            result = await _fetch_remote_history(
                tag_codes=["TI-101.PV"],
                start_time="2026-07-15T10:00:00",
                end_time="2026-07-15T11:00:00",
                interval=1,
            )

        assert mock_guard.fetch_history_guarded.await_count == 2  # 共请求 2 次
        assert mock_sleep.await_count == 1  # 重试等待 1 次
        timestamps, _ = result
        assert len(timestamps) == 1

    @pytest.mark.asyncio
    async def test_504_retry_exhausted(self, _mock_settings):
        """504 重试 3 次仍失败，抛出 HistoryDataSourceError。"""
        mock_guard = self._make_guard(return_value=self._make_response(504))

        with (
            patch("app.services.data_import._get_remote_guard", return_value=mock_guard),
            patch("asyncio.sleep", new=AsyncMock()) as mock_sleep,
            pytest.raises(HistoryDataSourceError) as exc_info,
        ):
            await _fetch_remote_history(
                tag_codes=["TI-101.PV"],
                start_time="2026-07-15T10:00:00",
                end_time="2026-07-15T11:00:00",
                interval=1,
            )

        # 首次 + 3 次重试 = 4 次请求
        assert mock_guard.fetch_history_guarded.await_count == 4
        assert mock_sleep.await_count == 3
        assert "HTTP 504" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_timeout_retry(self, _mock_settings):
        """httpx.TimeoutException 触发重试，最终成功。"""
        mock_guard = self._make_guard(
            side_effect=[
                httpx.ReadTimeout("read timeout"),  # 首次超时
                self._make_response(200),  # 重试成功
            ]
        )

        with (
            patch("app.services.data_import._get_remote_guard", return_value=mock_guard),
            patch("asyncio.sleep", new=AsyncMock()) as mock_sleep,
        ):
            result = await _fetch_remote_history(
                tag_codes=["TI-101.PV"],
                start_time="2026-07-15T10:00:00",
                end_time="2026-07-15T11:00:00",
                interval=1,
            )

        assert mock_guard.fetch_history_guarded.await_count == 2
        assert mock_sleep.await_count == 1
        timestamps, _ = result
        assert len(timestamps) == 1

    @pytest.mark.asyncio
    async def test_timeout_retry_exhausted(self, _mock_settings):
        """超时重试 3 次仍失败，抛出包含'已重试 3 次'的错误。"""
        mock_guard = self._make_guard(side_effect=httpx.ReadTimeout("read timeout"))

        with (
            patch("app.services.data_import._get_remote_guard", return_value=mock_guard),
            patch("asyncio.sleep", new=AsyncMock()) as mock_sleep,
            pytest.raises(HistoryDataSourceError) as exc_info,
        ):
            await _fetch_remote_history(
                tag_codes=["TI-101.PV"],
                start_time="2026-07-15T10:00:00",
                end_time="2026-07-15T11:00:00",
                interval=1,
            )

        assert mock_guard.fetch_history_guarded.await_count == 4  # 首次 + 3 次重试
        assert mock_sleep.await_count == 3
        assert "已重试 3 次" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_400_no_retry(self, _mock_settings):
        """400 不可重试，直接抛出。"""
        mock_guard = self._make_guard(return_value=self._make_response(400))

        with (
            patch("app.services.data_import._get_remote_guard", return_value=mock_guard),
            patch("asyncio.sleep", new=AsyncMock()) as mock_sleep,
            pytest.raises(HistoryDataSourceError) as exc_info,
        ):
            await _fetch_remote_history(
                tag_codes=["TI-101.PV"],
                start_time="2026-07-15T10:00:00",
                end_time="2026-07-15T11:00:00",
                interval=1,
            )

        assert mock_guard.fetch_history_guarded.await_count == 1  # 只请求 1 次
        assert mock_sleep.await_count == 0  # 无重试
        assert "HTTP 400" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_business_error_no_retry(self, _mock_settings):
        """HTTP 200 但业务 code != 200 时，不重试直接抛出。"""
        mock_guard = self._make_guard(
            return_value=self._make_response(
                200,
                payload={"code": 500, "message": "内部错误"},
            )
        )

        with (
            patch("app.services.data_import._get_remote_guard", return_value=mock_guard),
            patch("asyncio.sleep", new=AsyncMock()) as mock_sleep,
            pytest.raises(HistoryDataSourceError) as exc_info,
        ):
            await _fetch_remote_history(
                tag_codes=["TI-101.PV"],
                start_time="2026-07-15T10:00:00",
                end_time="2026-07-15T11:00:00",
                interval=1,
            )

        assert mock_guard.fetch_history_guarded.await_count == 1  # 业务错误不重试
        assert mock_sleep.await_count == 0
        assert "业务错误" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_circuit_open_fast_fail_no_retry(self, _mock_settings):
        """熔断中（RemoteApiCircuitOpenError）快速失败，不重试。"""
        from app.services.data_source.remote_api_provider import RemoteApiCircuitOpenError

        mock_guard = self._make_guard(side_effect=RemoteApiCircuitOpenError("熔断中"))

        with (
            patch("app.services.data_import._get_remote_guard", return_value=mock_guard),
            patch("asyncio.sleep", new=AsyncMock()) as mock_sleep,
            pytest.raises(HistoryDataSourceError) as exc_info,
        ):
            await _fetch_remote_history(
                tag_codes=["TI-101.PV"],
                start_time="2026-07-15T10:00:00",
                end_time="2026-07-15T11:00:00",
                interval=1,
            )

        assert mock_guard.fetch_history_guarded.await_count == 1  # 不重试
        assert mock_sleep.await_count == 0
        assert "熔断中" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_no_url_configured(self, _mock_settings):
        """未配置 HISTORY_DATA_API_URL 时直接抛出。"""
        _mock_settings.HISTORY_DATA_API_URL = ""
        mock_guard = self._make_guard()

        with (
            patch("app.services.data_import._get_remote_guard", return_value=mock_guard),
            pytest.raises(HistoryDataSourceError) as exc_info,
        ):
            await _fetch_remote_history(
                tag_codes=["TI-101.PV"],
                start_time="2026-07-15T10:00:00",
                end_time="2026-07-15T11:00:00",
                interval=1,
            )

        assert "未配置" in str(exc_info.value)
        assert mock_guard.fetch_history_guarded.await_count == 0


# ---------------------------------------------------------------------------
# 导入任务生命周期测试（WS-B2）
# ---------------------------------------------------------------------------


class _FakeRedisImport:
    """支持 pipeline/hset/hgetall/zadd/zrange/zrem/expire 的轻量 Redis mock.

    pipeline 方法返回专用 _Pipe 对象，其 hset/expire/zadd 为同步排队，
    execute 为 async 批量执行（与 redis-py async pipeline 行为一致）。
    """

    def __init__(self) -> None:
        self._hashes: dict[str, dict[str, str]] = {}
        self._zsets: dict[str, dict[str, float]] = {}

    def pipeline(self):
        return _FakePipe(self)

    async def hget(self, key: str, field: str) -> str | None:
        return self._hashes.get(key, {}).get(field)

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self._hashes.get(key, {}))

    async def zadd(self, key: str, mapping: dict) -> int:
        self._zsets.setdefault(key, {}).update(mapping)
        return 1

    async def zrange(self, key: str, start: int, stop: int) -> list[str]:
        members = sorted(self._zsets.get(key, {}).items(), key=lambda x: x[1])
        if stop == -1:
            return [m for m, _ in members]
        return [m for m, _ in members[start : stop + 1]]

    async def zrevrange(self, key: str, start: int, stop: int) -> list[str]:
        members = sorted(self._zsets.get(key, {}).items(), key=lambda x: -x[1])
        if stop == -1:
            return [m for m, _ in members]
        return [m for m, _ in members[start : stop + 1]]

    async def zrem(self, key: str, member: str) -> int:
        if member in self._zsets.get(key, {}):
            del self._zsets[key][member]
            return 1
        return 0

    async def eval(self, script: str, numkeys: int, key: str, *args: str) -> list:
        """模拟 _IMPORT_TASK_CAS_LUA 语义（data_import CAS 专用）.

        其他 Lua 脚本不支持 —— _FakeRedisImport 仅用于 data_import 测试，
        该模块唯一使用 eval 的位置是 _update_task_cas。
        """
        from app.services.data_import import _IMPORT_TASK_CAS_LUA

        if script != _IMPORT_TASK_CAS_LUA:
            raise NotImplementedError(f"_FakeRedisImport.eval 不支持该脚本: {script[:60]}...")

        if key not in self._hashes:
            return ["MISSING", ""]

        new_status = args[0] if len(args) > 0 else ""
        old_status = self._hashes[key].get("status", "")
        terminal = {"SUCCESS", "FAILED", "CANCELLED"}

        if new_status != "":
            if old_status in terminal and old_status != new_status:
                return ["BLOCKED", old_status]
            self._hashes[key]["status"] = new_status

        # field/value 对从 args[2] 开始（args[0]=new_status, args[1]=ttl）
        for i in range(2, len(args), 2):
            field = args[i]
            value = args[i + 1]
            if field in ("progress", "imported_count", "error_count"):
                current_raw = self._hashes[key].get(field)
                try:
                    current_f = float(current_raw) if current_raw not in (None, "") else None
                    incoming_f = float(value)
                    if current_f is None or incoming_f >= current_f:
                        self._hashes[key][field] = value
                except (ValueError, TypeError):
                    self._hashes[key][field] = value
            else:
                self._hashes[key][field] = value

        return ["UPDATED", old_status]


class _FakePipe:
    """Pipeline mock — hset/expire/zadd 同步排队，execute async 批量执行."""

    def __init__(self, owner: _FakeRedisImport) -> None:
        self._owner = owner
        self._ops: list[tuple] = []

    def hset(self, key: str, mapping: dict | None = None, **kwargs) -> Any:
        if mapping is None:
            mapping = kwargs
        self._ops.append(("hset", key, mapping))
        return self

    def expire(self, key: str, ttl: int) -> Any:
        self._ops.append(("expire", key, ttl))
        return self

    def zadd(self, key: str, mapping: dict) -> Any:
        self._ops.append(("zadd", key, mapping))
        return self

    async def execute(self) -> list:
        for op in self._ops:
            kind = op[0]
            if kind == "hset":
                _, key, mapping = op
                self._owner._hashes.setdefault(key, {}).update(mapping)
            elif kind == "zadd":
                _, key, mapping = op
                self._owner._zsets.setdefault(key, {}).update(mapping)
            # expire 是 no-op（mock 不模拟过期）
        self._ops.clear()
        return []


@pytest.fixture
def _mock_import_settings():
    """Mock data_import settings for lifecycle tests."""
    with patch("app.services.data_import.settings") as mock:
        mock.IMPORT_TASK_TTL_DAYS = 30
        mock.IMPORT_TASK_RUNNING_TIMEOUT_SECONDS = 7200
        yield mock


class TestSweepStaleRunningTasks:
    """sweep_stale_running_tasks 测试 — 清扫超时 RUNNING 任务。"""

    @pytest.mark.asyncio
    async def test_sweeps_timed_out_running_task(self, _mock_import_settings):
        """RUNNING 且 started_at 超过超时阈值的任务应被置为 FAILED。"""
        from datetime import UTC, datetime, timedelta

        fake = _FakeRedisImport()
        # 构造一个 3 小时前 started_at 的 RUNNING 任务（超时阈值 7200s = 2h）
        old_started = (datetime.now(UTC) - timedelta(hours=3)).isoformat()
        fake._hashes["import_task:stale-1"] = {
            "task_id": "stale-1",
            "status": "RUNNING",
            "started_at": old_started,
        }
        fake._zsets["import_task:index"] = {"stale-1": 1.0}

        with patch("app.services.data_import.redis_client", fake):
            result = await sweep_stale_running_tasks()

        assert result["swept"] == 1
        assert "stale-1" in result["details"]
        assert fake._hashes["import_task:stale-1"]["status"] == "FAILED"

    @pytest.mark.asyncio
    async def test_skips_recent_running_task(self, _mock_import_settings):
        """RUNNING 但 started_at 在阈值内的任务不应被清扫。"""
        from datetime import UTC, datetime

        fake = _FakeRedisImport()
        recent_started = datetime.now(UTC).isoformat()
        fake._hashes["import_task:fresh-1"] = {
            "task_id": "fresh-1",
            "status": "RUNNING",
            "started_at": recent_started,
        }
        fake._zsets["import_task:index"] = {"fresh-1": 1.0}

        with patch("app.services.data_import.redis_client", fake):
            result = await sweep_stale_running_tasks()

        assert result["swept"] == 0
        assert fake._hashes["import_task:fresh-1"]["status"] == "RUNNING"

    @pytest.mark.asyncio
    async def test_skips_non_running_tasks(self, _mock_import_settings):
        """非 RUNNING 状态（如 SUCCESS/FAILED）不应被清扫。"""
        from datetime import UTC, datetime, timedelta

        fake = _FakeRedisImport()
        old_started = (datetime.now(UTC) - timedelta(hours=10)).isoformat()
        fake._hashes["import_task:done-1"] = {
            "task_id": "done-1",
            "status": "SUCCESS",
            "started_at": old_started,
        }
        fake._zsets["import_task:index"] = {"done-1": 1.0}

        with patch("app.services.data_import.redis_client", fake):
            result = await sweep_stale_running_tasks()

        assert result["swept"] == 0

    @pytest.mark.asyncio
    async def test_skips_task_without_started_at(self, _mock_import_settings):
        """无 started_at 的 RUNNING 任务应跳过（无法判断超时）。"""
        fake = _FakeRedisImport()
        fake._hashes["import_task:nostart-1"] = {
            "task_id": "nostart-1",
            "status": "RUNNING",
            "started_at": "",
        }
        fake._zsets["import_task:index"] = {"nostart-1": 1.0}

        with patch("app.services.data_import.redis_client", fake):
            result = await sweep_stale_running_tasks()

        assert result["swept"] == 0

    @pytest.mark.asyncio
    async def test_skips_task_with_invalid_started_at(self, _mock_import_settings):
        """started_at 格式无效的 RUNNING 任务应跳过。"""
        fake = _FakeRedisImport()
        fake._hashes["import_task:badstart-1"] = {
            "task_id": "badstart-1",
            "status": "RUNNING",
            "started_at": "not-a-date",
        }
        fake._zsets["import_task:index"] = {"badstart-1": 1.0}

        with patch("app.services.data_import.redis_client", fake):
            result = await sweep_stale_running_tasks()

        assert result["swept"] == 0


class TestPruneImportTaskIndex:
    """prune_import_task_index 测试 — 修剪已过期任务的索引条目。"""

    @pytest.mark.asyncio
    async def test_prunes_expired_index_entries(self, _mock_import_settings):
        """索引中存在但 Hash 已过期（不存在）的条目应被移除。"""
        fake = _FakeRedisImport()
        # "expired-1" 在索引中但 Hash 不存在（已 TTL 过期）
        # "alive-1" 在索引中且 Hash 存在
        fake._hashes["import_task:alive-1"] = {
            "task_id": "alive-1",
            "status": "success",
        }
        fake._zsets["import_task:index"] = {
            "expired-1": 1.0,
            "alive-1": 2.0,
        }

        with patch("app.services.data_import.redis_client", fake):
            removed = await prune_import_task_index()

        assert removed == 1
        assert "expired-1" not in fake._zsets["import_task:index"]
        assert "alive-1" in fake._zsets["import_task:index"]

    @pytest.mark.asyncio
    async def test_no_prune_when_all_alive(self, _mock_import_settings):
        """所有索引条目都有对应 Hash 时不修剪。"""
        fake = _FakeRedisImport()
        fake._hashes["import_task:t1"] = {"task_id": "t1", "status": "success"}
        fake._hashes["import_task:t2"] = {"task_id": "t2", "status": "failed"}
        fake._zsets["import_task:index"] = {"t1": 1.0, "t2": 2.0}

        with patch("app.services.data_import.redis_client", fake):
            removed = await prune_import_task_index()

        assert removed == 0

    @pytest.mark.asyncio
    async def test_no_prune_when_index_empty(self, _mock_import_settings):
        """空索引不修剪。"""
        fake = _FakeRedisImport()

        with patch("app.services.data_import.redis_client", fake):
            removed = await prune_import_task_index()

        assert removed == 0


# ---------------------------------------------------------------------------
# 幂等防护测试（CAS 终态守卫 + 缓存重构 + _do_import 短路）
# ---------------------------------------------------------------------------


class TestImportTaskCAS:
    """_update_task_cas Lua CAS 行为测试。"""

    @pytest.mark.asyncio
    async def test_blocks_running_overwrite_success(self, _mock_import_settings):
        """已 SUCCESS 时置 RUNNING → BLOCKED，状态不变。"""
        from app.services.data_import import _update_task_cas

        fake = _FakeRedisImport()
        fake._hashes["import_task:t1"] = {"status": "SUCCESS", "imported_count": "27"}
        with patch("app.services.data_import.redis_client", fake):
            code, old = await _update_task_cas("t1", new_status="RUNNING", started_at="x")
        assert code == "BLOCKED"
        assert old == "SUCCESS"
        assert fake._hashes["import_task:t1"]["status"] == "SUCCESS"  # 未被覆盖

    @pytest.mark.asyncio
    async def test_allows_pending_to_running(self, _mock_import_settings):
        """PENDING → RUNNING 正常 UPDATED。"""
        from app.services.data_import import _update_task_cas

        fake = _FakeRedisImport()
        fake._hashes["import_task:t1"] = {"status": "PENDING"}
        with patch("app.services.data_import.redis_client", fake):
            code, old = await _update_task_cas("t1", new_status="RUNNING", started_at="x")
        assert code == "UPDATED"
        assert old == "PENDING"
        assert fake._hashes["import_task:t1"]["status"] == "RUNNING"

    @pytest.mark.asyncio
    async def test_blocks_cancel_after_success(self, _mock_import_settings):
        """SUCCESS → CANCELLED 被 BLOCKED（取消已完成任务）。"""
        from app.services.data_import import _update_task_cas

        fake = _FakeRedisImport()
        fake._hashes["import_task:t1"] = {"status": "SUCCESS"}
        with patch("app.services.data_import.redis_client", fake):
            code, old = await _update_task_cas("t1", new_status="CANCELLED", finished_at="x")
        assert code == "BLOCKED"
        assert old == "SUCCESS"
        assert fake._hashes["import_task:t1"]["status"] == "SUCCESS"

    @pytest.mark.asyncio
    async def test_monotonic_progress(self, _mock_import_settings):
        """progress 倒退被忽略（0.8 → 0.5 不写入）。"""
        from app.services.data_import import _update_task_cas

        fake = _FakeRedisImport()
        fake._hashes["import_task:t1"] = {"status": "RUNNING", "progress": "0.8"}
        with patch("app.services.data_import.redis_client", fake):
            await _update_task_cas("t1", new_status="RUNNING", progress=0.5)
        assert fake._hashes["import_task:t1"]["progress"] == "0.8"  # 未被 0.5 覆盖

    @pytest.mark.asyncio
    async def test_missing_task(self, _mock_import_settings):
        """任务不存在返回 MISSING。"""
        from app.services.data_import import _update_task_cas

        fake = _FakeRedisImport()
        with patch("app.services.data_import.redis_client", fake):
            code, old = await _update_task_cas("nonexistent", new_status="RUNNING")
        assert code == "MISSING"
        assert old == ""

    @pytest.mark.asyncio
    async def test_running_to_running_not_blocked(self, _mock_import_settings):
        """RUNNING → RUNNING（接续执行场景）不被 BLOCKED。"""
        from app.services.data_import import _update_task_cas

        fake = _FakeRedisImport()
        fake._hashes["import_task:t1"] = {"status": "RUNNING"}
        with patch("app.services.data_import.redis_client", fake):
            code, old = await _update_task_cas("t1", new_status="RUNNING", started_at="x")
        assert code == "UPDATED"
        assert old == "RUNNING"


class TestBuildCachedResult:
    """_build_cached_result 重构逻辑测试。"""

    def test_uses_persisted_result_json(self):
        """优先用持久化的 result JSON 字段。"""
        from app.services.data_import import _build_cached_result

        data = {
            "status": "SUCCESS",
            "result": '{"total": 27, "succeeded": 27, "failed": 0, "errors": []}',
            "loop_count": "27",
            "imported_count": "27",
            "error_count": "0",
            "error_message": "",
        }
        r = _build_cached_result(data)
        assert r["total"] == 27
        assert r["succeeded"] == 27
        assert r["skipped_redelivery"] is True

    def test_fallback_reconstruct_when_no_result(self):
        """result 字段缺失时用 loop_count/imported_count 兜底重构。"""
        from app.services.data_import import _build_cached_result

        data = {
            "status": "SUCCESS",
            "result": "",
            "loop_count": "10",
            "imported_count": "8",
            "error_count": "2",
            "error_message": "",
        }
        r = _build_cached_result(data)
        assert r["total"] == 10
        assert r["succeeded"] == 8  # SUCCESS 时用 imported_count
        assert r["failed"] == 0  # 非 FAILED 时 failed=0
        assert r["skipped_redelivery"] is True

    def test_fallback_failed_status(self):
        """FAILED 状态兜底重构。"""
        from app.services.data_import import _build_cached_result

        data = {
            "status": "FAILED",
            "result": "",
            "loop_count": "10",
            "imported_count": "3",
            "error_count": "7",
            "error_message": "超时",
        }
        r = _build_cached_result(data)
        assert r["succeeded"] == 0  # 非 SUCCESS 时 succeeded=0
        assert r["failed"] == 7
        assert "超时" in r["errors"]


class TestImportHistoryDataCasShortCircuit:
    """_do_import 已终态时 CAS 短路测试。"""

    @pytest.mark.asyncio
    async def test_skipped_when_already_success(self, _mock_import_settings):
        """任务已 SUCCESS → _do_import 开头 CAS BLOCKED，返回缓存结果不执行导入。"""
        from app.services.data_import import import_history_data

        fake = _FakeRedisImport()
        fake._hashes["import_task:t1"] = {
            "task_id": "t1",
            "status": "SUCCESS",
            "loop_count": "5",
            "imported_count": "5",
            "error_count": "0",
            "result": '{"total": 5, "succeeded": 5, "failed": 0, "errors": []}',
        }
        with patch("app.services.data_import.redis_client", fake):
            result = await import_history_data(
                loop_ids=["loop-1"],
                ts_start="2026-07-15T00:00:00",
                ts_end="2026-07-15T01:00:00",
                task_id="t1",
            )
        assert result["skipped_redelivery"] is True
        assert result["total"] == 5
        # status 未被 RUNNING 覆盖
        assert fake._hashes["import_task:t1"]["status"] == "SUCCESS"

    @pytest.mark.asyncio
    async def test_skipped_when_already_failed(self, _mock_import_settings):
        """任务已 FAILED → 同上短路。"""
        from app.services.data_import import import_history_data

        fake = _FakeRedisImport()
        fake._hashes["import_task:t1"] = {
            "task_id": "t1",
            "status": "FAILED",
            "loop_count": "5",
            "imported_count": "0",
            "error_count": "5",
            "result": "",
            "error_message": "远端不可用",
        }
        with patch("app.services.data_import.redis_client", fake):
            result = await import_history_data(
                loop_ids=["loop-1"],
                ts_start="2026-07-15T00:00:00",
                ts_end="2026-07-15T01:00:00",
                task_id="t1",
            )
        assert result["skipped_redelivery"] is True
        assert fake._hashes["import_task:t1"]["status"] == "FAILED"


class TestCancelImportTaskCas:
    """cancel_import_task CAS 行为测试。"""

    @pytest.mark.asyncio
    async def test_cancel_running_task(self, _mock_import_settings):
        """RUNNING 任务可正常取消（CANCELLED）。"""
        from app.services.data_import import cancel_import_task

        fake = _FakeRedisImport()
        fake._hashes["import_task:t1"] = {
            "task_id": "t1",
            "status": "RUNNING",
            "celery_task_id": "",
        }
        with patch("app.services.data_import.redis_client", fake):
            resp = await cancel_import_task("t1")
        assert resp is not None
        assert resp["status"] == "CANCELLED"
        assert "was_blocked" not in resp

    @pytest.mark.asyncio
    async def test_cancel_already_success_blocked(self, _mock_import_settings):
        """已 SUCCESS 任务取消被 CAS 拒绝，返回实际状态 + was_blocked。"""
        from app.services.data_import import cancel_import_task

        fake = _FakeRedisImport()
        fake._hashes["import_task:t1"] = {
            "task_id": "t1",
            "status": "SUCCESS",
            "celery_task_id": "",
            "loop_count": "5",
            "imported_count": "5",
            "error_count": "0",
        }
        with patch("app.services.data_import.redis_client", fake):
            resp = await cancel_import_task("t1")
        assert resp is not None
        assert resp["status"] == "SUCCESS"  # 未变 CANCELLED
        assert resp.get("was_blocked") is True
