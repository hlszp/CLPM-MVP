"""P1-05 结果账本（calculation_result_record）单元测试.

覆盖《03-阶段任务计划》P1-05 验收口径 C12a 的可单测部分：
- append_result_record 幂等：同唯一键复用（不重复建）；并发冲突回读复用；
  不同 logicalRunId 双版本共存（各自追加）。
- logicalRunId 推导确定性：同上下文同 id；celery/自定义/兜底三级；
  节点内容寻址（同 payload 同 id、变 payload 新 id）。
- _persist_snapshot：record 先行 + 投影写 result_record_id（同事务）。
- _save_confidence_latest：DEC-09 迟到历史窗口不更新当前投影；
  同窗/更新窗正常覆盖。
- save_node_snapshot：NODE record payload 含 loopRecordIds 与各指标分母，
  投影行带 result_record_id。
- find_record_any_id：recordId 直读 + 旧 ID 经 legacy map 兼容。
- config_revision_digest：内容变 → 摘要变。

行级落库/迁移去重/唯一约束由 dev 库 upgrade + 临时库新装路径验证（见
交接文件），不在本文件 mock 层重复。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import result_ledger
from app.services.result_ledger import (
    CONFIG_REVISION_UNVERSIONED,
    LEGACY_UNVERIFIABLE,
    derive_loop_logical_run_id,
    derive_node_logical_run_id,
)
from app.tasks.kpi_calc import ALGORITHM_VERSION, _persist_snapshot, _save_confidence_latest

# ---------------------------------------------------------------------------
# mock 助手
# ---------------------------------------------------------------------------

_TS0 = datetime(2026, 10, 12, 8, 0, 0)
_TS1 = _TS0 + timedelta(hours=1)
_TS_OLD_END = _TS0  # 迟到窗口的 ts_end（早于当前投影）


def _scalar_result(value):
    """db.execute 返回值：scalar_one_or_none() → value。"""
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _record_stub(record_id: str = "rec-00000000-0000-0000-0000-000000000001", **overrides):
    base = SimpleNamespace(
        id=record_id,
        logical_run_id="lrid-1",
        object_kind="LOOP",
        object_id="loop-1",
        ts_start=_TS0,
        ts_end=_TS1,
        source_record_id=None,
        algorithm_version=ALGORITHM_VERSION,
        config_revision="digest:abc",
        dataset_snapshot_id=None,
        status="COMPLETED",
        payload={"score": 80.0},
        created_at=_TS1,
    )
    for k, v in overrides.items():
        setattr(base, k, v)
    return base


def _returning_first_result(value):
    """db.execute 返回值：first() → 可索引 row（_save_snapshot RETURNING 用）。"""
    result = MagicMock()
    result.first.return_value = (value,)
    return result


def _stmt_tables(stmt) -> list[str]:
    """提取语句涉及的表名（Insert 有 .table；Select 用 final_froms）。"""
    table = getattr(stmt, "table", None)
    if table is not None:
        return [table.name]
    return [f.name for f in stmt.get_final_froms()]


# ---------------------------------------------------------------------------
# append_result_record 幂等 / 双版本共存
# ---------------------------------------------------------------------------


class TestAppendResultRecordIdempotent:
    """同唯一键重试幂等：冲突即复用既有 record，不重复建。"""

    @pytest.mark.asyncio
    async def test_existing_record_reused_without_insert(self) -> None:
        """SELECT 命中既有 record → 直接复用，不再 INSERT。"""
        db = AsyncMock()
        existing = _record_stub("rec-existing")
        db.execute = AsyncMock(return_value=_scalar_result(existing))

        record = await result_ledger.append_result_record(
            db,
            logical_run_id="lrid-1",
            object_kind="LOOP",
            object_id="loop-1",
            ts_start=_TS0,
            ts_end=_TS1,
            algorithm_version=ALGORITHM_VERSION,
            config_revision="digest:abc",
            payload={"score": 80},
        )

        assert record is existing
        # 仅 1 次 execute（SELECT），无 INSERT
        assert db.execute.await_count == 1

    @pytest.mark.asyncio
    async def test_insert_when_missing(self) -> None:
        """SELECT 未命中 → INSERT RETURNING 新 record。"""
        db = AsyncMock()
        inserted = _record_stub("rec-new")
        db.execute = AsyncMock(side_effect=[_scalar_result(None), _scalar_result(inserted)])

        record = await result_ledger.append_result_record(
            db,
            logical_run_id="lrid-1",
            object_kind="LOOP",
            object_id="loop-1",
            ts_start=_TS0,
            ts_end=_TS1,
            algorithm_version=ALGORITHM_VERSION,
            config_revision="digest:abc",
            payload={"score": Decimal("80.00")},
        )

        assert record is inserted
        assert db.execute.await_count == 2  # SELECT + INSERT

    @pytest.mark.asyncio
    async def test_concurrent_conflict_rereads_existing(self) -> None:
        """并发竞态（ON CONFLICT DO NOTHING 返回空）→ 回读复用既有 record。"""
        db = AsyncMock()
        existing = _record_stub("rec-raced")
        # SELECT(None) → INSERT(冲突返回 None) → 回读(existing)
        db.execute = AsyncMock(
            side_effect=[_scalar_result(None), _scalar_result(None), _scalar_result(existing)]
        )

        record = await result_ledger.append_result_record(
            db,
            logical_run_id="lrid-1",
            object_kind="NODE",
            object_id="node-1",
            ts_start=_TS0,
            ts_end=_TS1,
            algorithm_version=ALGORITHM_VERSION,
            config_revision=CONFIG_REVISION_UNVERSIONED,
            payload={"loopRecordIds": []},
        )

        assert record is existing
        assert db.execute.await_count == 3

    @pytest.mark.asyncio
    async def test_dual_logical_run_ids_coexist(self) -> None:
        """同 loop/同窗不同 logicalRunId（v1/v2）→ 各自 INSERT（双版本共存）。"""
        db = AsyncMock()
        v1 = _record_stub("rec-v1")
        v2 = _record_stub("rec-v2")
        # 每次调用：SELECT(None) + INSERT(record)
        db.execute = AsyncMock(
            side_effect=[
                _scalar_result(None),
                _scalar_result(v1),
                _scalar_result(None),
                _scalar_result(v2),
            ]
        )

        common = {
            "object_kind": "LOOP",
            "object_id": "loop-1",
            "ts_start": _TS0,
            "ts_end": _TS1,
            "algorithm_version": ALGORITHM_VERSION,
            "config_revision": "digest:abc",
            "payload": {"score": 80},
        }
        r1 = await result_ledger.append_result_record(db, logical_run_id="lrid-v1", **common)
        r2 = await result_ledger.append_result_record(db, logical_run_id="lrid-v2", **common)

        assert r1 is v1
        assert r2 is v2
        assert db.execute.await_count == 4  # 两次完整 SELECT+INSERT，无复用

    @pytest.mark.asyncio
    async def test_aware_datetimes_normalized_to_naive_utc(self) -> None:
        """aware 窗口入参归一为 naive UTC（对齐 DB TIMESTAMP 列）。"""
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_scalar_result(_record_stub()))

        await result_ledger.append_result_record(
            db,
            logical_run_id="lrid-1",
            object_kind="LOOP",
            object_id="loop-1",
            ts_start=_TS0.replace(tzinfo=UTC),
            ts_end=_TS1.replace(tzinfo=UTC),
            algorithm_version=ALGORITHM_VERSION,
            config_revision="digest:abc",
            payload={},
        )
        stmt = db.execute.await_args_list[0].args[0]
        params = stmt.compile().params
        # 账本唯一键 SELECT 的窗口绑定值为 naive UTC（SELECT 参数名带序号后缀）
        assert any(v == _TS0 for v in params.values())
        assert any(v == _TS1 for v in params.values())


# ---------------------------------------------------------------------------
# logicalRunId 推导
# ---------------------------------------------------------------------------


class TestLogicalRunIdDerivation:
    """logicalRunId 确定性：同上下文同 id；不同上下文/窗口不同 id。"""

    def test_same_context_same_id(self) -> None:
        a = derive_loop_logical_run_id(_TS0, source="SCHEDULED", source_task_id="t-1")
        b = derive_loop_logical_run_id(_TS0, source="SCHEDULED", source_task_id="t-1")
        assert a == b

    def test_different_window_different_id(self) -> None:
        a = derive_loop_logical_run_id(_TS0, source="SCHEDULED", source_task_id="t-1")
        b = derive_loop_logical_run_id(
            _TS0 + timedelta(hours=1), source="SCHEDULED", source_task_id="t-1"
        )
        assert a != b

    def test_celery_task_id_wins(self) -> None:
        """Celery retry 复用同一 task id → 同一 logicalRunId（重试幂等）。"""
        a = derive_loop_logical_run_id(_TS0, celery_task_id="celery-1", source="BACKFILL")
        b = derive_loop_logical_run_id(
            _TS0 + timedelta(hours=1), celery_task_id="celery-1", source="BACKFILL"
        )
        c = derive_loop_logical_run_id(_TS0, celery_task_id="celery-2", source="BACKFILL")
        assert a != b  # 跨窗口仍按窗口区分（唯一键含窗口）
        assert a != c

    def test_custom_task_stable_across_retries(self) -> None:
        a = derive_loop_logical_run_id(_TS0, custom_task_id="task-uuid-1")
        b = derive_loop_logical_run_id(_TS0, custom_task_id="task-uuid-1")
        assert a == b

    def test_node_content_addressed(self) -> None:
        payload = {"score": 75.0, "loopRecordIds": [{"loopId": "l1", "recordId": "r1"}]}
        a = derive_node_logical_run_id("node-1", _TS0, payload)
        b = derive_node_logical_run_id("node-1", _TS0, payload)
        assert a == b
        # 输入 recordId 变（回路 record 换版本）→ 新 logicalRunId → 新版本 record
        payload_v2 = {"score": 75.0, "loopRecordIds": [{"loopId": "l1", "recordId": "r2"}]}
        assert a != derive_node_logical_run_id("node-1", _TS0, payload_v2)

    def test_config_revision_digest_changes_with_content(self) -> None:
        a = result_ledger.config_revision_digest({"typeWeights": {"FAST": {}}, "fitness": {}})
        b = result_ledger.config_revision_digest({"typeWeights": {"FAST": {}}, "fitness": {}})
        c = result_ledger.config_revision_digest({"typeWeights": {"SLOW": {}}, "fitness": {}})
        assert a == b
        assert a != c


# ---------------------------------------------------------------------------
# _persist_snapshot：record 先行 + 投影指针
# ---------------------------------------------------------------------------


class TestPersistSnapshotRecordFirst:
    """_persist_snapshot 先追加 record，同事务把 result_record_id 写入投影。"""

    @pytest.mark.asyncio
    async def test_hourly_projection_carries_result_record_id(self) -> None:
        from tests.test_kpi_calc import _extract_upsert_set_values

        db = AsyncMock()
        record = _record_stub("rec-persist-1")
        # 1) 账本 SELECT 2) 账本 INSERT 3) hourly UPSERT 4) latest 守卫 SELECT 5) latest UPSERT
        db.execute = AsyncMock(
            side_effect=[
                _scalar_result(None),
                _scalar_result(record),
                _returning_first_result("snap-1"),
                _scalar_result(None),
                _scalar_result(None),
            ]
        )

        result = await _persist_snapshot(
            db=db,
            loop_id="loop-1",
            ts_start=_TS0,
            ts_end=_TS1,
            status="SUCCESS",
            score=Decimal("80.00"),
            source="SCHEDULED",
        )

        assert result["snapshotId"] == "snap-1"
        stmts = [c.args[0] for c in db.execute.await_args_list]
        # 顺序：账本 SELECT → 账本 INSERT → hourly UPSERT → latest 守卫 → latest UPSERT
        assert _stmt_tables(stmts[0]) == ["calculation_result_record"]
        assert _stmt_tables(stmts[1]) == ["calculation_result_record"]
        assert _stmt_tables(stmts[2]) == ["kpi_snapshot_hourly"]
        assert _stmt_tables(stmts[3]) == ["loop_confidence_latest"]
        assert _stmt_tables(stmts[4]) == ["loop_confidence_latest"]
        # 投影 UPSERT 的 set_ 带 result_record_id（指向先行追加的 record）
        set_values = _extract_upsert_set_values(stmts[2])
        assert set_values["result_record_id"] == "rec-persist-1"
        # 账本 INSERT 的 payload 含完整快照字段与业务状态
        insert_params = stmts[1].compile().params
        assert insert_params["algorithm_version"] == ALGORITHM_VERSION
        assert insert_params["config_revision"] == CONFIG_REVISION_UNVERSIONED
        assert insert_params["status"] == "COMPLETED"

    @pytest.mark.asyncio
    async def test_custom_projection_carries_result_record_id(self) -> None:
        db = AsyncMock()
        record = _record_stub("rec-custom-1")
        db.add = MagicMock()
        # 1) 账本 SELECT 2) 账本 INSERT 3) custom select-then-add（SELECT→None）
        db.execute = AsyncMock(
            side_effect=[
                _scalar_result(None),
                _scalar_result(record),
                _scalar_result(None),
            ]
        )

        result = await _persist_snapshot(
            db=db,
            loop_id="loop-1",
            ts_start=_TS0,
            ts_end=_TS1,
            status="SUCCESS",
            custom_task_id="task-1",
        )

        assert result["taskId"] == "task-1"
        db.add.assert_called_once()
        added = db.add.call_args.args[0]
        assert added.result_record_id == "rec-custom-1"

    @pytest.mark.asyncio
    async def test_ledger_failure_does_not_block_projection(self) -> None:
        """账本追加异常仅记日志，投影照常写入（兼容期投影优先）。"""
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[
                RuntimeError("ledger down"),  # 账本 SELECT 即失败
                _returning_first_result("snap-ok"),  # hourly UPSERT
                _scalar_result(None),  # latest 守卫 SELECT
                _scalar_result(None),  # latest UPSERT
            ]
        )

        result = await _persist_snapshot(
            db=db,
            loop_id="loop-1",
            ts_start=_TS0,
            ts_end=_TS1,
            status="SUCCESS",
        )
        assert result["snapshotId"] == "snap-ok"

    @pytest.mark.asyncio
    async def test_same_logical_run_id_reuse_in_persist(self) -> None:
        """同 logicalRunId 二次写入：账本 SELECT 命中复用，无第二次 INSERT。"""
        from tests.test_kpi_calc import _extract_upsert_set_values

        db = AsyncMock()
        record = _record_stub("rec-reused")
        # 第一次：SELECT(None)+INSERT+UPSERT+守卫+latest；第二次：SELECT(命中)+UPSERT+守卫+latest
        db.execute = AsyncMock(
            side_effect=[
                _scalar_result(None),
                _scalar_result(record),
                _returning_first_result("snap-1"),
                _scalar_result(None),
                _scalar_result(None),
                _scalar_result(record),
                _returning_first_result("snap-1"),
                _scalar_result(_TS1),  # 守卫：现存投影窗=同窗 → 允许覆盖
                _scalar_result(None),
            ]
        )

        kwargs = {
            "db": db,
            "loop_id": "loop-1",
            "ts_start": _TS0,
            "ts_end": _TS1,
            "status": "SUCCESS",
            "logical_run_id": "lrid-fixed",
        }
        await _persist_snapshot(**kwargs)
        await _persist_snapshot(**kwargs)

        stmts = [c.args[0] for c in db.execute.await_args_list]
        ledger_tables = [
            t for s in stmts for t in _stmt_tables(s) if t == "calculation_result_record"
        ]
        # 第二次仅 1 次账本 SELECT（复用），无第二次 INSERT
        assert ledger_tables.count("calculation_result_record") == 3  # SELECT+INSERT+SELECT
        both_upserts = [
            s for s in stmts if _stmt_tables(s) == ["kpi_snapshot_hourly"] and hasattr(s, "table")
        ]
        assert len(both_upserts) == 2
        assert _extract_upsert_set_values(both_upserts[1])["result_record_id"] == "rec-reused"


# ---------------------------------------------------------------------------
# DEC-09：迟到历史窗口不更新当前投影
# ---------------------------------------------------------------------------


class TestConfidenceLatestLateArrivalGuard:
    """迟到历史写入只追加 record，不反向覆盖 loop_confidence_latest。"""

    @pytest.mark.asyncio
    async def test_late_window_skips_latest_upsert(self) -> None:
        """incoming ts_end 早于现存投影 data_ts_end → 不 UPSERT。"""
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_scalar_result(_TS1))  # 现存投影窗终点

        await _save_confidence_latest(
            db,
            loop_id="loop-1",
            ts_start=_TS0 - timedelta(hours=1),
            ts_end=_TS_OLD_END,  # 08:00 < 09:00 → 迟到
            status="SUCCESS",
        )

        assert db.execute.await_count == 1  # 仅守卫 SELECT，无 UPSERT
        guard_stmt = db.execute.await_args_list[0].args[0]
        assert _stmt_tables(guard_stmt) == ["loop_confidence_latest"]

    @pytest.mark.asyncio
    async def test_same_window_still_updates(self) -> None:
        """同窗重评（ts_end 相等）→ 正常覆盖切换投影。"""
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[_scalar_result(_TS1), _scalar_result(None)])

        await _save_confidence_latest(
            db,
            loop_id="loop-1",
            ts_start=_TS0,
            ts_end=_TS1,
            status="SUCCESS",
            result_record_id="rec-1",
        )
        assert db.execute.await_count == 2  # 守卫 SELECT + UPSERT

    @pytest.mark.asyncio
    async def test_newer_window_updates(self) -> None:
        """更新窗口 → 正常覆盖。"""
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[_scalar_result(_TS0), _scalar_result(None)])

        await _save_confidence_latest(
            db,
            loop_id="loop-1",
            ts_start=_TS0,
            ts_end=_TS1,
            status="SUCCESS",
        )
        assert db.execute.await_count == 2

    @pytest.mark.asyncio
    async def test_no_existing_row_updates(self) -> None:
        """无现存投影（首评）→ 正常写入。"""
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[_scalar_result(None), _scalar_result(None)])

        await _save_confidence_latest(
            db,
            loop_id="loop-1",
            ts_start=_TS0,
            ts_end=_TS1,
            status="SUCCESS",
        )
        assert db.execute.await_count == 2

    @pytest.mark.asyncio
    async def test_result_record_id_written_to_latest(self) -> None:
        from tests.test_kpi_calc import _extract_upsert_set_values

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=[_scalar_result(None), _scalar_result(None)])

        await _save_confidence_latest(
            db,
            loop_id="loop-1",
            ts_start=_TS0,
            ts_end=_TS1,
            status="SUCCESS",
            result_record_id="rec-latest-1",
        )
        stmt = db.execute.await_args_list[1].args[0]
        assert _stmt_tables(stmt) == ["loop_confidence_latest"]
        assert _extract_upsert_set_values(stmt)["result_record_id"] == "rec-latest-1"


# ---------------------------------------------------------------------------
# 节点：save_node_snapshot record + 输入可追
# ---------------------------------------------------------------------------


def _node_snap_data(**overrides) -> dict:
    base = {
        "plant_node_id": "node-001",
        "ts_start": _TS0,
        "ts_end": _TS1,
        "score": Decimal("75.00"),
        "good_value_rate": Decimal("95.00"),
        "auto_mode_rate": Decimal("88.00"),
        "effective_auto_rate": Decimal("85.00"),
        "steady_rate": Decimal("80.00"),
        "accuracy_rate": Decimal("78.00"),
        "fast_rate": Decimal("82.00"),
        "oscillation_rate": Decimal("15.00"),
        "saturation_rate": Decimal("8.00"),
        "instrument_fault_rate": Decimal("3.00"),
        "stiction_index": Decimal("0.12"),
        "settling_time": Decimal("135.00"),
        "output_trip_index": Decimal("38.00"),
        "ideal_settling_time": Decimal("180.00"),
        "auto_loop_ratio": Decimal("66.67"),
        "realtime_auto_rate": Decimal("90.00"),
        "loop_count": 2,
        "status": "FAIR",
        "algorithm_version": "KPI_CALC_v2.0",
        # v5.3 unit 字段（不写入节点小时表）
        "total_loops": 3,
        "evaluated_loops": 2,
        "excluded_loops": 1,
        "inconclusive_loops": 0,
        "unit_status": "SUCCESS",
        # P1-05 账本字段（进 record payload，不进节点小时表）
        "loop_record_ids": [
            {"loopId": "loop-1", "recordId": "rec-loop-1"},
            {"loopId": "loop-2", "recordId": None},
        ],
        "metric_denominators": {
            "score": {"validCount": 2, "validWeight": 2.0, "totalWeight": 2.0},
            "fast_rate": {"validCount": 1, "validWeight": 1.0, "totalWeight": 2.0},
        },
    }
    base.update(overrides)
    return base


class TestSaveNodeSnapshotLedger:
    """节点快照写入：record 先行（内容寻址），投影行带 result_record_id。"""

    @pytest.mark.asyncio
    async def test_new_snapshot_with_record(self) -> None:
        from app.services.node_performance import save_node_snapshot

        db = AsyncMock()
        db.flush = AsyncMock()
        db.add = MagicMock()
        node_record = _record_stub("rec-node-1", object_kind="NODE", object_id="node-001")
        # 1) 账本 SELECT 2) 账本 INSERT 3) 节点表 SELECT（不存在）
        # 4) UnitKpiSummary SELECT（不存在）
        db.execute = AsyncMock(
            side_effect=[
                _scalar_result(None),
                _scalar_result(node_record),
                _scalar_result(None),
                _scalar_result(None),
            ]
        )

        result = await save_node_snapshot(db, _node_snap_data())

        assert result["plant_node_id"] == "node-001"
        # 节点小时表 + UnitKpiSummary 两个 db.add
        assert db.add.call_count == 2
        node_snap_obj = db.add.call_args_list[0].args[0]
        assert node_snap_obj.result_record_id == "rec-node-1"
        # 账本 INSERT 的 payload 含 loopRecordIds 与各指标分母
        ledger_insert = db.execute.await_args_list[1].args[0]
        payload = ledger_insert.compile().params["payload"]
        assert payload["loopRecordIds"] == [
            {"loopId": "loop-1", "recordId": "rec-loop-1"},
            {"loopId": "loop-2", "recordId": None},
        ]
        assert payload["metricDenominators"]["fast_rate"]["validCount"] == 1
        assert payload["metricDenominators"]["fast_rate"]["totalWeight"] == 2.0

    @pytest.mark.asyncio
    async def test_overwrite_existing_sets_record_pointer(self) -> None:
        from app.services.node_performance import save_node_snapshot

        db = AsyncMock()
        db.flush = AsyncMock()
        existing = MagicMock()
        existing.result_record_id = None
        node_record = _record_stub("rec-node-2", object_kind="NODE")
        # 1) 账本 SELECT 2) 账本 INSERT 3) 节点表 SELECT（存在）4) Unit SELECT（存在）
        db.execute = AsyncMock(
            side_effect=[
                _scalar_result(None),
                _scalar_result(node_record),
                _scalar_result(existing),
                _scalar_result(MagicMock()),
            ]
        )

        await save_node_snapshot(db, _node_snap_data())
        assert existing.result_record_id == "rec-node-2"

    @pytest.mark.asyncio
    async def test_node_record_content_addressed_idempotent(self) -> None:
        """同输入重跑：内容寻址 → 账本 SELECT 命中复用（无第二次 INSERT）。"""
        from app.services.node_performance import save_node_snapshot

        db = AsyncMock()
        db.flush = AsyncMock()
        db.add = MagicMock()
        node_record = _record_stub("rec-node-stable", object_kind="NODE")
        # 第一次：SELECT(None)+INSERT+节点 SELECT(None)+Unit SELECT(None)
        db.execute = AsyncMock(
            side_effect=[
                _scalar_result(None),
                _scalar_result(node_record),
                _scalar_result(None),
                _scalar_result(None),
            ]
        )
        await save_node_snapshot(db, _node_snap_data())
        first_tables = [t for c in db.execute.await_args_list for t in _stmt_tables(c.args[0])]

        # 第二次（同输入）：账本 SELECT 命中 → 复用
        db.execute = AsyncMock(
            side_effect=[
                _scalar_result(node_record),
                _scalar_result(None),
                _scalar_result(None),
            ]
        )
        await save_node_snapshot(db, _node_snap_data())
        second_tables = [t for c in db.execute.await_args_list for t in _stmt_tables(c.args[0])]

        assert first_tables.count("calculation_result_record") == 2  # SELECT+INSERT
        assert second_tables.count("calculation_result_record") == 1  # 仅 SELECT 复用


# ---------------------------------------------------------------------------
# 读取：recordId 直读 + 旧 ID 映射兼容
# ---------------------------------------------------------------------------


class TestFindRecordAnyId:
    """历史详情读取：recordId 直读；旧 ID 经 calculation_result_legacy_map。"""

    @pytest.mark.asyncio
    async def test_direct_record_id_hit(self) -> None:
        db = AsyncMock()
        record = _record_stub("rec-direct")
        db.execute = AsyncMock(return_value=_scalar_result(record))

        found, via = await result_ledger.find_record_any_id(db, "rec-direct")
        assert found is record
        assert via == "record"

    @pytest.mark.asyncio
    async def test_legacy_snapshot_id_mapped(self) -> None:
        """旧 kpi_snapshot_hourly 行 ID → 经映射解析归档 record。"""
        db = AsyncMock()
        record = _record_stub("rec-archived")
        # 1) record 直读（None）2) legacy map hourly（命中）
        db.execute = AsyncMock(side_effect=[_scalar_result(None), _scalar_result(record)])

        found, via = await result_ledger.find_record_any_id(db, "old-snap-id")
        assert found is record
        assert via == "legacy:kpi_snapshot_hourly"

    @pytest.mark.asyncio
    async def test_unknown_id_returns_none(self) -> None:
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_scalar_result(None))

        found, via = await result_ledger.find_record_any_id(db, "no-such-id")
        assert found is None
        assert via is None
        # 直读 + 3 张旧表映射 = 4 次 execute
        assert db.execute.await_count == 4

    def test_record_to_dict_shape(self) -> None:
        record = _record_stub(
            "rec-shape",
            payload={"snapshotStatus": "SUCCESS", "loopRecordIds": []},
        )
        d = result_ledger.record_to_dict(record)
        assert d["recordId"] == "rec-shape"
        assert d["objectKind"] == "LOOP"
        assert d["status"] == "COMPLETED"
        assert d["datasetSnapshotId"] is None  # P1 记录不可完整复现（显式事实）
        assert d["payload"]["snapshotStatus"] == "SUCCESS"


# ---------------------------------------------------------------------------
# canonical / LEGACY 标记
# ---------------------------------------------------------------------------


class TestCanonicalization:
    def test_canonical_value_converts(self) -> None:
        out = result_ledger.canonical_value(
            {"score": Decimal("80.00"), "ts": _TS0, "nested": [Decimal("1"), None, "x"]}
        )
        assert out == {"score": 80.0, "ts": _TS0.isoformat(), "nested": [1.0, None, "x"]}

    def test_legacy_marker_constant(self) -> None:
        """未知旧算法/输入的显式标记（不伪补版本）。"""
        assert LEGACY_UNVERIFIABLE == "LEGACY_UNVERIFIABLE"
