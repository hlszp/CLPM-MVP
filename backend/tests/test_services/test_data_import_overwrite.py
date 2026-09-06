"""R12 overwrite 安全替换协议测试（2026-09-06 数据链路整改 S2/B）.

覆盖验收场景（主计划 §4 R12）：
- 首块/中块拉取失败、暂存写入失败、任务取消、进程重启（暂存遗留续跑）
  各场景下主表旧数据保留或可按记录恢复；
- 删除失败反映为任务失败（不得只记日志）；
- 空返回不授权清空（远端窗口无数据时保留本地旧数据并显式告警）；
- 成功路径：暂存齐全 → DELETE 主窗口 → 搬回 → 清理暂存（幂等可重试）；
- skip 策略回归不变（自动 gap backfill 恒 skip，不触碰暂存协议）。

实现方式：FakeTD 模拟 TDengine 子表存储（字典 + ts 排序键），patch
data_import 命名空间内的 batch_insert / execute_native_effective /
query_wide_table_native，不依赖真实 TDengine。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import data_import as di
from app.services.data_import import HistoryDataSourceError

# 主子表 / 暂存子表
_MAIN = "d_loop_a"
_STG = "stg__d_loop_a"

_TS_FMT = "%Y-%m-%d %H:%M:%S.%f"

# DELETE FROM {db}.{table} WHERE ts >= '{lo}' AND ts <= '{hi}'
_DELETE_RE = re.compile(
    r"^DELETE FROM [\w.]+\.(?P<table>\S+) WHERE ts >= '(?P<lo>[^']+)' AND ts <= '(?P<hi>[^']+)'"
)
_DROP_RE = re.compile(r"^DROP TABLE IF EXISTS [\w.]+\.(?P<table>\S+)$")


class FakeTD:
    """内存 TDengine 模拟：子表 = {ts_str: row_tuple}（同 ts UPSERT 覆盖）."""

    def __init__(self) -> None:
        self.tables: dict[str, dict[str, tuple]] = {}
        self.executed_sql: list[str] = []
        self.fail_delete_for: set[str] = set()  # 这些子表的 DELETE 抛错
        self.fail_insert_for: set[str] = set()  # 这些子表的 batch_insert 抛错

    # -- execute_native_effective 模拟 -----------------------------------
    async def execute_effective(self, sql: str) -> int:
        self.executed_sql.append(sql)
        sql = sql.strip()
        if m := _DELETE_RE.match(sql):
            table, lo, hi = m.group("table"), m.group("lo"), m.group("hi")
            if table in self.fail_delete_for:
                raise RuntimeError(f"FakeTD: DELETE 失败 ({table})")
            tbl = self.tables.get(table, {})
            removed = [ts for ts in tbl if lo <= ts <= hi]
            for ts in removed:
                del tbl[ts]
            return len(removed)
        if m := _DROP_RE.match(sql):
            self.tables.pop(m.group("table"), None)
            return 0
        return 0

    # -- batch_insert 模拟 -------------------------------------------------
    async def batch_insert(self, subtable: str, rows: list[tuple], **_kw: Any) -> int:
        if subtable in self.fail_insert_for:
            raise RuntimeError(f"FakeTD: 写入失败 ({subtable})")
        tbl = self.tables.setdefault(subtable, {})
        for row in rows:
            tbl[row[0]] = tuple(row)  # 同 ts 覆盖（TDengine UPSERT 语义）
        return len(rows)

    # -- query_wide_table_native 模拟 --------------------------------------
    async def query_wide(self, subtable: str, start_str: str, end_str: str) -> list[dict]:
        from datetime import UTC

        from app.services.data_import import _TARGET_TZ

        rows: list[dict] = []
        cols = di._WIDE_COLUMNS
        for ts in sorted(self.tables.get(subtable, {})):
            if not (start_str <= ts <= end_str):
                continue
            row = self.tables[subtable][ts]
            stored_naive = datetime.strptime(ts, _TS_FMT)
            # 模拟 taosrest timezone=UTC：返回 aware UTC datetime
            aware_utc = stored_naive.replace(tzinfo=_TARGET_TZ).astimezone(UTC)
            d = {"ts": aware_utc}
            for c, v in zip(cols[1:], row[1:], strict=False):
                d[c] = v
            rows.append(d)
        return rows

    def patch_dict(self) -> dict[str, Any]:
        return {
            "app.services.data_import.execute_native_effective": AsyncMock(
                side_effect=self.execute_effective
            ),
            "app.services.data_import.batch_insert": AsyncMock(side_effect=self.batch_insert),
            "app.services.data_import.query_wide_table_native": AsyncMock(
                side_effect=self.query_wide
            ),
        }

    # -- 断言辅助 -----------------------------------------------------------
    def delete_sqls_for(self, table: str) -> list[str]:
        return [s for s in self.executed_sql if s.startswith("DELETE FROM") and f".{table} " in s]

    def drop_sqls_for(self, table: str) -> list[str]:
        return [
            s for s in self.executed_sql if s.startswith("DROP TABLE") and s.endswith(f".{table}")
        ]


def _seed_old_data(td: FakeTD, table: str = _MAIN) -> None:
    """主表预置旧数据：窗口内 5 行 + 窗口外 1 行（窗口外必须永远保留）."""
    td.tables[table] = {
        "2026-07-15 00:00:10.000": (
            "2026-07-15 00:00:10.000",
            1.0,
            1.0,
            1.0,
            1,
            None,
            None,
            None,
            1,
        ),
        "2026-07-15 00:30:00.000": (
            "2026-07-15 00:30:00.000",
            2.0,
            1.0,
            1.0,
            1,
            None,
            None,
            None,
            1,
        ),
        "2026-07-15 01:00:00.000": (
            "2026-07-15 01:00:00.000",
            3.0,
            1.0,
            1.0,
            1,
            None,
            None,
            None,
            1,
        ),
        "2026-07-15 01:30:00.000": (
            "2026-07-15 01:30:00.000",
            4.0,
            1.0,
            1.0,
            1,
            None,
            None,
            None,
            1,
        ),
        "2026-07-15 02:00:00.000": (
            "2026-07-15 02:00:00.000",
            5.0,
            1.0,
            1.0,
            1,
            None,
            None,
            None,
            1,
        ),
        # 窗口外（导入窗 00:00~03:00 之前）——任何策略都不得动
        "2026-07-14 23:00:00.000": (
            "2026-07-14 23:00:00.000",
            9.0,
            1.0,
            1.0,
            1,
            None,
            None,
            None,
            1,
        ),
    }


def _remote_payload(step_s: int = 1800) -> dict[str, str]:
    """远端数据时间戳（naive ISO，+8 墙钟口径）：00:00~02:59 每 step_s 一条."""
    base = datetime(2026, 7, 15, 0, 0, 0)
    n = int(3 * 3600 / step_s) + 1
    ts_list = [(base + timedelta(seconds=i * step_s)).isoformat() for i in range(n)]
    return ts_list


def _fetch_ok(step_s: int = 1800):
    """全部分块成功的远端拉取 mock."""

    async def _fetch(tag_codes, start_time, end_time, interval):
        return (_remote_payload(step_s), {"A.PV": {"values": [50.0] * 100, "qualities": [1] * 100}})

    return _fetch


def _fetch_fail_at(prefix: str):
    """指定分块（start_time 以 prefix 开头）拉取失败的远端 mock.

    其余分块成功，返回以该分块起点为首的 1 点数据（ts 与分块对齐，
    不同分块不互相覆盖）。
    """

    async def _fetch(tag_codes, start_time, end_time, interval):
        if start_time.startswith(prefix):
            raise HistoryDataSourceError("远端历史数据 API 返回 HTTP 504")
        return (
            [start_time],
            {"A.PV": {"values": [50.0], "qualities": [1]}},
        )

    return _fetch


_START = datetime(2026, 7, 15, 0, 0, 0)
_END = datetime(2026, 7, 15, 3, 0, 0)


async def _call_single_loop(td: FakeTD, fetch_mock, **overrides: Any):
    kwargs: dict[str, Any] = {
        "loop_id": "loop-1",
        "start_dt": _START,
        "end_dt": _END,
        "interval": 1,
        "conflict_strategy": "overwrite",
        "subtable": _MAIN,
        "unit_id": "u1",
        "role_tag_map": {"PV": "A.PV"},
        "chunk_hours": 1,
        "task_id": None,
    }
    kwargs.update(overrides)
    with (
        patch("app.services.data_import._fetch_remote_history", side_effect=fetch_mock),
        patch(
            "app.services.data_import.execute_native_effective",
            AsyncMock(side_effect=td.execute_effective),
        ),
        patch("app.services.data_import.batch_insert", AsyncMock(side_effect=td.batch_insert)),
        patch(
            "app.services.data_import.query_wide_table_native",
            AsyncMock(side_effect=td.query_wide),
        ),
    ):
        return await di._import_single_loop(**kwargs)


# ---------------------------------------------------------------------------
# 旧数据保留场景
# ---------------------------------------------------------------------------


class TestOverwriteKeepsOldDataOnFailure:
    """暂存未齐全的各失败路径：主表旧数据保留、无 DELETE 执行."""

    @pytest.mark.asyncio
    async def test_first_chunk_fetch_failure_keeps_old_data(self):
        """首块拉取失败（其余块成功暂存）：不删除主表，旧数据完整保留."""
        td = FakeTD()
        _seed_old_data(td)
        old_snapshot = dict(td.tables[_MAIN])

        count, failed_windows, cancelled = await _call_single_loop(
            td, _fetch_fail_at("2026-07-15T00")
        )

        assert count == 0
        assert cancelled is False
        assert len(failed_windows) == 1  # 仅首块失败，其余块成功暂存
        assert td.delete_sqls_for(_MAIN) == []  # 从未 DELETE 主表
        assert td.tables[_MAIN] == old_snapshot  # 旧数据原样
        # 成功分块的数据落在暂存表（未进主表），供续跑/核查
        assert _STG in td.tables and len(td.tables[_STG]) == 2  # 2 个成功块 × 1 点

    @pytest.mark.asyncio
    async def test_middle_chunk_fetch_failure_keeps_old_data(self):
        """中块拉取失败（首块成功已入暂存）：主表不动，旧数据保留."""
        td = FakeTD()
        _seed_old_data(td)
        old_snapshot = dict(td.tables[_MAIN])

        count, failed_windows, _ = await _call_single_loop(td, _fetch_fail_at("2026-07-15T01"))

        assert count == 0  # 未进入替换阶段
        assert len(failed_windows) == 1
        assert td.delete_sqls_for(_MAIN) == []
        assert td.tables[_MAIN] == old_snapshot
        # 暂存表保留（供续跑/人工核查）
        assert _STG in td.tables and len(td.tables[_STG]) > 0

    @pytest.mark.asyncio
    async def test_staging_write_failure_keeps_old_data(self):
        """暂存写入失败（batch_insert 抛错）：主表不动，旧数据保留."""
        td = FakeTD()
        _seed_old_data(td)
        td.fail_insert_for = {_STG}
        old_snapshot = dict(td.tables[_MAIN])

        count, failed_windows, _ = await _call_single_loop(td, _fetch_ok())

        assert count == 0
        assert len(failed_windows) == 3
        assert td.delete_sqls_for(_MAIN) == []
        assert td.tables[_MAIN] == old_snapshot

    @pytest.mark.asyncio
    async def test_cancel_during_staging_keeps_old_data(self):
        """暂存阶段取消：主表不动，旧数据保留，返回 cancelled=True."""
        td = FakeTD()
        _seed_old_data(td)
        old_snapshot = dict(td.tables[_MAIN])

        with patch(
            "app.services.data_import._is_task_cancelled",
            new=AsyncMock(side_effect=[False, True, True]),
        ):
            count, failed_windows, cancelled = await _call_single_loop(
                td, _fetch_ok(), task_id="t-cancel"
            )

        assert cancelled is True
        assert count == 0
        assert failed_windows == []
        assert td.delete_sqls_for(_MAIN) == []
        assert td.tables[_MAIN] == old_snapshot
        # 第 1 块已暂存，暂存表保留
        assert _STG in td.tables

    @pytest.mark.asyncio
    async def test_empty_remote_return_refuses_to_clear(self):
        """空返回不授权清空：远端窗口无数据 → 抛错保留旧数据（显式告警）."""
        td = FakeTD()
        _seed_old_data(td)
        old_snapshot = dict(td.tables[_MAIN])

        async def _fetch_empty(tag_codes, start_time, end_time, interval):
            return ([], {})

        with pytest.raises(HistoryDataSourceError, match="拒绝清空"):
            await _call_single_loop(td, _fetch_empty)

        assert td.delete_sqls_for(_MAIN) == []
        assert td.tables[_MAIN] == old_snapshot


# ---------------------------------------------------------------------------
# 替换阶段失败 → 任务失败
# ---------------------------------------------------------------------------


class TestOverwriteReplaceFailures:
    """替换阶段失败必须进入任务失败状态（不得只记日志）."""

    @pytest.mark.asyncio
    async def test_delete_failure_raises_loop_error(self):
        """DELETE 主窗口失败 → 抛出（上层计入回路失败/任务 FAILED）."""
        td = FakeTD()
        _seed_old_data(td)
        td.fail_delete_for = {_MAIN}

        with pytest.raises(RuntimeError, match="DELETE 失败"):
            await _call_single_loop(td, _fetch_ok())

    @pytest.mark.asyncio
    async def test_move_back_failure_raises_loop_error(self):
        """搬移回主表失败（暂存查询后写主表抛错）→ 抛出."""
        td = FakeTD()
        _seed_old_data(td)

        # 先正常暂存（用正常 FakeTD），再在搬移阶段让主表写入失败：
        # 通过二次调用实现——第一次用失败标记放在删除执行之后较复杂，
        # 直接对单回路流程设置 fail_insert_for 在 DELETE 后生效。
        async def _batch_insert_fail_after_delete(subtable, rows, **kw):
            if subtable == _MAIN and td.delete_sqls_for(_MAIN):
                raise RuntimeError("FakeTD: 搬移写入失败")
            return await td.batch_insert(subtable, rows, **kw)

        with (
            patch("app.services.data_import._fetch_remote_history", side_effect=_fetch_ok()),
            patch(
                "app.services.data_import.execute_native_effective",
                AsyncMock(side_effect=td.execute_effective),
            ),
            patch(
                "app.services.data_import.batch_insert",
                AsyncMock(side_effect=_batch_insert_fail_after_delete),
            ),
            patch(
                "app.services.data_import.query_wide_table_native",
                AsyncMock(side_effect=td.query_wide),
            ),
            pytest.raises(RuntimeError, match="搬移写入失败"),
        ):
            await di._import_single_loop(
                loop_id="loop-1",
                start_dt=_START,
                end_dt=_END,
                interval=1,
                conflict_strategy="overwrite",
                subtable=_MAIN,
                unit_id="u1",
                role_tag_map={"PV": "A.PV"},
                chunk_hours=1,
            )


# ---------------------------------------------------------------------------
# 成功路径与幂等
# ---------------------------------------------------------------------------


class TestOverwriteHappyPath:
    """暂存齐全 → DELETE 主窗口 → 搬回 → 清理暂存."""

    @pytest.mark.asyncio
    async def test_full_replace_flow(self):
        """成功路径：窗口内旧数据被替换、窗口外保留、暂存表被 DROP."""
        td = FakeTD()
        _seed_old_data(td)

        count, failed_windows, cancelled = await _call_single_loop(td, _fetch_ok(step_s=600))

        assert cancelled is False
        assert failed_windows == []
        assert count > 0
        # 主表窗口内 = 新数据（19 个 600s 间隔点，含末端 03:00），旧 5 行被替换
        new_ts = [
            ts
            for ts in td.tables[_MAIN]
            if "2026-07-15 00:00:00.000" <= ts <= "2026-07-15 03:00:00.000"
        ]
        assert len(new_ts) == 19
        # 窗口外旧数据保留
        assert "2026-07-14 23:00:00.000" in td.tables[_MAIN]
        # 暂存表已清理
        assert _STG not in td.tables
        assert td.drop_sqls_for(_STG), "替换完成后必须 DROP 暂存表"
        # 新数据的值来自远端（50.0）
        assert all(row[1] == 50.0 for row in td.tables[_MAIN].values() if row[0] >= "2026-07-15 00")

    @pytest.mark.asyncio
    async def test_replace_is_idempotent_on_rerun(self):
        """重启后续跑：同窗口重新 overwrite 得到一致终态（幂等）."""
        td = FakeTD()
        _seed_old_data(td)

        await _call_single_loop(td, _fetch_ok(step_s=600))
        snapshot_after_first = dict(td.tables[_MAIN])

        await _call_single_loop(td, _fetch_ok(step_s=600))
        assert td.tables[_MAIN] == snapshot_after_first
        assert _STG not in td.tables

    @pytest.mark.asyncio
    async def test_stale_staging_dropped_before_restage(self):
        """上次崩溃遗留的暂存表在重新暂存前被 DROP（不混入旧数据）."""
        td = FakeTD()
        _seed_old_data(td)
        # 模拟上次运行遗留：暂存表里有一行"污染"数据
        td.tables[_STG] = {
            "2026-07-15 01:00:00.000": (
                "2026-07-15 01:00:00.000",
                999.0,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
            )
        }

        await _call_single_loop(td, _fetch_ok(step_s=600))

        # 遗留行被清除：主表值全部来自本次远端（50.0），无 999.0
        assert all(row[1] != 999.0 for row in td.tables[_MAIN].values())
        assert _STG not in td.tables


# ---------------------------------------------------------------------------
# 任务级：失败反映到任务状态 / 暂存遗留登记
# ---------------------------------------------------------------------------


class TestOverwriteTaskLevel:
    """import_history_data 任务级状态反映（overwrite 路径）."""

    def _task_patches(self, td: FakeTD, fetch_mock, loop_data_map: dict | None = None):
        loop_map = loop_data_map or {
            "loop-1": {
                "role_tag_map": {"PV": "A.PV"},
                "unit_id": "u1",
                "subtable": _MAIN,
                "loop_part": "A",
            }
        }
        cas_calls: list[dict] = []

        async def _cas(task_id, new_status=None, **fields):
            cas_calls.append({"new_status": new_status, **fields})
            return "UPDATED", "RUNNING"

        mock_session = AsyncMock()
        return (
            cas_calls,
            {
                "app.services.data_import._fetch_remote_history": AsyncMock(side_effect=fetch_mock),
                "app.services.data_import.execute_native_effective": AsyncMock(
                    side_effect=td.execute_effective
                ),
                "app.services.data_import.batch_insert": AsyncMock(side_effect=td.batch_insert),
                "app.services.data_import.query_wide_table_native": AsyncMock(
                    side_effect=td.query_wide
                ),
                "app.services.data_import._batch_get_loop_data": AsyncMock(return_value=loop_map),
                "app.services.data_import._update_task_cas": AsyncMock(side_effect=_cas),
                "app.services.data_import._update_task": AsyncMock(),
                "app.services.data_import._is_task_cancelled": AsyncMock(return_value=False),
                "app.services.data_import._invalidate_loop_caches": AsyncMock(),
                "app.core.db.AsyncSessionLocal": MagicMock(return_value=mock_session),
            },
        )

    @pytest.mark.asyncio
    async def test_delete_failure_marks_task_failed(self):
        """DELETE 失败 → 任务终态 FAILED（错误信息反映删除失败）."""
        import contextlib

        td = FakeTD()
        _seed_old_data(td)
        td.fail_delete_for = {_MAIN}

        cas_calls, patches = self._task_patches(td, _fetch_ok())
        with contextlib.ExitStack() as stack:
            for target, mock in patches.items():
                stack.enter_context(patch(target, mock))
            result = await di.import_history_data(
                loop_ids=["loop-1"],
                ts_start="2026-07-15T00:00:00",
                ts_end="2026-07-15T03:00:00",
                conflict_strategy="overwrite",
                task_id="t-del-fail",
            )

        assert result["failed"] == 1
        final_calls = [c for c in cas_calls if c["new_status"]]
        assert final_calls, "必须写入终态"
        assert final_calls[-1]["new_status"] == "FAILED"
        assert "DELETE" in (final_calls[-1].get("error_message") or "")

    @pytest.mark.asyncio
    async def test_chunk_failure_registers_staging_leftover(self):
        """分块失败：任务 FAILED，result 登记 stagingTablesLeftover（重启可判定）."""
        td = FakeTD()
        _seed_old_data(td)

        cas_calls, patches = self._task_patches(td, _fetch_fail_at("2026-07-15T01"))
        import contextlib

        with contextlib.ExitStack() as stack:
            for target, mock in patches.items():
                stack.enter_context(patch(target, mock))
            result = await di.import_history_data(
                loop_ids=["loop-1"],
                ts_start="2026-07-15T00:00:00",
                ts_end="2026-07-15T03:00:00",
                conflict_strategy="overwrite",
                task_id="t-stg-leftover",
            )

        assert result["failed"] == 1
        assert result.get("stagingTablesLeftover") == [_STG]
        final_calls = [c for c in cas_calls if c["new_status"]]
        assert final_calls[-1]["new_status"] == "FAILED"
        # 主表旧数据保留
        assert "2026-07-15 00:00:10.000" in td.tables[_MAIN]

    @pytest.mark.asyncio
    async def test_success_has_no_staging_leftover(self):
        """成功路径：result 不登记遗留暂存（已 DROP）."""
        td = FakeTD()
        _seed_old_data(td)

        cas_calls, patches = self._task_patches(td, _fetch_ok(step_s=600))
        import contextlib

        with contextlib.ExitStack() as stack:
            for target, mock in patches.items():
                stack.enter_context(patch(target, mock))
            result = await di.import_history_data(
                loop_ids=["loop-1"],
                ts_start="2026-07-15T00:00:00",
                ts_end="2026-07-15T03:00:00",
                conflict_strategy="overwrite",
                task_id="t-ok",
            )

        assert result["succeeded"] == 1
        assert "stagingTablesLeftover" not in result
        assert _STG not in td.tables


# ---------------------------------------------------------------------------
# R13：导入成功后缓存失效联动
# ---------------------------------------------------------------------------


class TestImportInvalidatesCaches:
    """导入写入主表后必须失效 realtime:history 与 L1/L2/L3 计算缓存（R13）."""

    @pytest.mark.asyncio
    async def test_skip_import_success_invalidates_realtime_history_and_l1(self):
        from app.services.cache import invalidation as invalidation_module

        td = FakeTD()
        _seed_old_data(td)

        deleted_keys: list[str] = []

        class _SpyRedis(MagicMock):
            async def delete(self, *keys):
                deleted_keys.extend(keys)
                return len(keys)

        spy_redis = _SpyRedis()
        invalidated_loops: list[str] = []

        async def _fake_invalidate_loop(self, loop_id, config_version=None):
            invalidated_loops.append(loop_id)
            return 0

        mock_session = AsyncMock()
        with (
            patch(
                "app.services.data_import._fetch_remote_history",
                AsyncMock(side_effect=_fetch_ok(step_s=600)),
            ),
            patch(
                "app.services.data_import.execute_native_effective",
                AsyncMock(side_effect=td.execute_effective),
            ),
            patch("app.services.data_import.batch_insert", AsyncMock(side_effect=td.batch_insert)),
            patch(
                "app.services.data_import.query_wide_table_native",
                AsyncMock(side_effect=td.query_wide),
            ),
            patch(
                "app.services.data_import._batch_get_loop_data",
                AsyncMock(
                    return_value={
                        "loop-1": {
                            "role_tag_map": {"PV": "A.PV"},
                            "unit_id": "u1",
                            "subtable": _MAIN,
                            "loop_part": "A_TAG",
                        }
                    }
                ),
            ),
            patch("app.services.data_import.redis_client", spy_redis),
            patch.object(
                invalidation_module.CacheInvalidator,
                "invalidate_loop",
                _fake_invalidate_loop,
            ),
            patch(
                "app.services.data_import._update_task_cas",
                AsyncMock(return_value=("UPDATED", "RUNNING")),
            ),
            patch("app.services.data_import._update_task", AsyncMock()),
            patch("app.services.data_import._is_task_cancelled", AsyncMock(return_value=False)),
            patch("app.services.data_import._trigger_kpi_backfill", AsyncMock()),
            patch("app.core.db.AsyncSessionLocal", MagicMock(return_value=mock_session)),
        ):
            result = await di.import_history_data(
                loop_ids=["loop-1"],
                ts_start="2026-07-15T00:00:00",
                ts_end="2026-07-15T03:00:00",
                conflict_strategy="skip",
                task_id="t-inv",
            )

        assert result["succeeded"] == 1
        # realtime:history:{loop_part} 被 DEL（前缀来自 realtime_subscriber 常量）
        from app.services.data_source.realtime_subscriber import _REDIS_KEY_PREFIX

        assert f"{_REDIS_KEY_PREFIX}history:A_TAG" in deleted_keys
        # L1/L2/L3 失效接入（按 loop_id）
        assert invalidated_loops == ["loop-1"]

    @pytest.mark.asyncio
    async def test_failed_import_does_not_invalidate(self):
        """导入失败的回路不失效缓存（未写入新数据，无需失效）."""
        td = FakeTD()
        _seed_old_data(td)

        invalidated_loops: list[str] = []

        async def _fake_invalidate_loop(self, loop_id, config_version=None):
            invalidated_loops.append(loop_id)
            return 0

        from app.services.cache import invalidation as invalidation_module

        mock_session = AsyncMock()
        with (
            patch(
                "app.services.data_import._fetch_remote_history",
                AsyncMock(side_effect=_fetch_fail_at("2026-07")),
            ),
            patch(
                "app.services.data_import.execute_native_effective",
                AsyncMock(side_effect=td.execute_effective),
            ),
            patch("app.services.data_import.batch_insert", AsyncMock(side_effect=td.batch_insert)),
            patch(
                "app.services.data_import.query_wide_table_native",
                AsyncMock(side_effect=td.query_wide),
            ),
            patch(
                "app.services.data_import._batch_get_loop_data",
                AsyncMock(
                    return_value={
                        "loop-1": {
                            "role_tag_map": {"PV": "A.PV"},
                            "unit_id": "u1",
                            "subtable": _MAIN,
                            "loop_part": "A_TAG",
                        }
                    }
                ),
            ),
            patch.object(
                invalidation_module.CacheInvalidator,
                "invalidate_loop",
                _fake_invalidate_loop,
            ),
            patch(
                "app.services.data_import._update_task_cas",
                AsyncMock(return_value=("UPDATED", "RUNNING")),
            ),
            patch("app.services.data_import._update_task", AsyncMock()),
            patch("app.services.data_import._is_task_cancelled", AsyncMock(return_value=False)),
            patch("app.core.db.AsyncSessionLocal", MagicMock(return_value=mock_session)),
        ):
            result = await di.import_history_data(
                loop_ids=["loop-1"],
                ts_start="2026-07-15T00:00:00",
                ts_end="2026-07-15T03:00:00",
                conflict_strategy="skip",
                task_id="t-noinv",
            )

        assert result["failed"] == 1
        assert invalidated_loops == []
