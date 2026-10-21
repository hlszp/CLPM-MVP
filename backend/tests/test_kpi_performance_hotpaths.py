"""KPI 批量热路径：精确时间戳、输入包身份和跨指标数据隔离。"""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest
from sqlalchemy import Column, DateTime, MetaData, String, Table, create_engine
from sqlalchemy.dialects import postgresql

from app.services import calc_snapshot as cs
from app.services.data_source import logical_wide_builder as lwb
from app.services.data_source import point_history_metadata as metadata
from app.tasks import kpi_calc
from tests.test_calc_snapshot import _make_kpi_bundles


@pytest.mark.parametrize(
    "values",
    [
        [],
        [datetime(1970, 1, 1), datetime(1969, 12, 31, 23, 59, 59, 999999)],
        [datetime(2026, 10, 21, 1, 2, 3, 123456), datetime(1900, 1, 1, 0, 0, 0, 7)],
        [
            datetime(2026, 10, 21, 1, 2, 3, 123456, tzinfo=UTC),
            datetime(2026, 10, 21, 9, 2, 3, 123456, tzinfo=timezone(timedelta(hours=8))),
            datetime(2026, 10, 20, 20, 2, 3, 123456, tzinfo=timezone(timedelta(hours=-5))),
        ],
    ],
)
def test_epoch_conversion_preserves_exact_legacy_bytes(values):
    base = np.datetime64("1970-01-01T00:00:00", "ns")
    expected = np.array(
        [
            (
                np.datetime64((cs.to_naive_utc(dt) if dt.tzinfo else dt).isoformat(), "ns") - base
            ).astype(np.int64)
            for dt in values
        ],
        dtype=np.int64,
    )
    actual = cs._ns_int(values)
    assert actual.dtype == expected.dtype
    assert actual.tobytes() == expected.tobytes()


def test_shared_block_encoding_preserves_full_package_and_input_hash(monkeypatch):
    bundles = _make_kpi_bundles(n_points=16)
    block = bundles[0].data_block
    block.signals["pv"][1] = None
    block.signals["pv"][2] = float("nan")
    block.signals["op_limits_source"] = ["LOOP_FIELD", None]
    block.validity["pv_valid"][1] = False
    expected_arrays, expected_meta = {}, []
    # 独立编码每个指标，不经过跨指标复用；NPZ 键、dtype、NaN 位和顺序均比较。
    for i, bundle in enumerate(bundles):
        arrays, meta = cs.serialize_data_blocks([bundle])
        expected_arrays.update({key.replace("blk0__", f"blk{i}__"): v for key, v in arrays.items()})
        expected_meta.extend(meta)

    original = cs._ns_int
    calls = []

    def encode(values):
        calls.append(values)
        return original(values)

    monkeypatch.setattr(cs, "_ns_int", encode)
    arrays, meta = cs.serialize_data_blocks(bundles)
    assert len(calls) == len({id(b.data_block) for b in bundles})
    assert list(arrays) == list(expected_arrays)
    assert meta == expected_meta
    for key, value in arrays.items():
        assert value.dtype == expected_arrays[key].dtype
        assert value.tobytes() == expected_arrays[key].tobytes()
    assert cs.compute_input_hash({"blocks": meta}, arrays) == cs.compute_input_hash(
        {"blocks": expected_meta}, expected_arrays
    )
    arrays["blk0__ts"][0] += 1
    arrays["blk0__sig_pv"][0] = -123.0
    assert arrays["blk1__ts"][0] == expected_arrays["blk1__ts"][0]
    assert arrays["blk1__sig_pv"][0] == expected_arrays["blk1__sig_pv"][0]


def test_same_id_distinct_blocks_and_later_mutations_are_not_cached():
    first, second = _make_kpi_bundles(n_points=4)[:2]
    second.data_block = deepcopy(first.data_block)
    second.data_block.signals["pv"][0] = 123.0
    before, _ = cs.serialize_data_blocks([first, second])
    assert before["blk0__sig_pv"][0] != before["blk1__sig_pv"][0]
    first.data_block.timestamps[0] += timedelta(microseconds=1)
    first.data_block.signals["pv"][0] = 456.0
    after, _ = cs.serialize_data_blocks([first, second])
    assert after["blk0__ts"][0] == before["blk0__ts"][0] + 1000
    assert after["blk0__sig_pv"][0] == 456.0
    assert after["blk1__sig_pv"][0] == 123.0


async def test_gap_query_keeps_only_intersecting_half_open_segments(monkeypatch):
    """实际执行 SQL：尾格点的 gap 保留，终点等于首格点的 gap 排除。"""
    start, end = datetime(2026, 10, 21, tzinfo=UTC), datetime(2026, 10, 21, 1, tzinfo=UTC)
    engine = create_engine("sqlite://")
    table = Table(
        "history_coverage_segment",
        MetaData(),
        Column("seg_start", DateTime(timezone=True)),
        Column("seg_end", DateTime(timezone=True)),
        Column("status", String),
    )
    table.create(engine)
    rows = [
        (start - timedelta(hours=2), start),
        (start - timedelta(seconds=1), start + timedelta(seconds=1)),
        (end, end + timedelta(seconds=1)),
        (end + timedelta(seconds=1), end + timedelta(hours=1)),
    ]
    try:
        with engine.begin() as connection:
            connection.execute(
                table.insert(),
                [{"seg_start": a, "seg_end": b, "status": "gap"} for a, b in rows]
                + [{"seg_start": start, "seg_end": end, "status": "confirmed"}],
            )

            async def execute(statement):
                return connection.execute(statement)

            monkeypatch.setattr(lwb, "_metadata_execute", execute)
            assert await lwb._load_gap_windows(None, start, end) == rows[1:3]
    finally:
        engine.dispose()


async def test_batch_timings_include_commit_close_and_run_identity(monkeypatch, caplog):
    from app.core import db as db_module

    sessions = []

    def session_factory():
        session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
        sessions.append(session)
        return session

    monkeypatch.setattr(db_module, "AsyncSessionLocal", session_factory)
    close = AsyncMock()
    monkeypatch.setattr(db_module, "close_session_guarded", close)
    monkeypatch.setattr(
        "app.services.data_planner.resolve_data_version", AsyncMock(return_value="point-test")
    )
    monkeypatch.setattr(kpi_calc, "_build_data_planner", lambda *_a, **_k: SimpleNamespace())
    monkeypatch.setattr(kpi_calc, "_get_shared_bundle_cache", lambda: None)

    async def calculate(**_kwargs):
        return {"status": "SUCCESS", "_timing": {"read": 1.0, "compute": 2.0, "persist": 3.0}}

    monkeypatch.setattr(kpi_calc, "_calculate_loop_kpi", calculate)
    with caplog.at_level("INFO", logger=kpi_calc.__name__):
        results = await kpi_calc._run_batch_loop_calculations(
            loops=[SimpleNamespace(id="L1", tag_name="LOOP-1")],
            loop_configs={},
            metric_configs={},
            ts_start=datetime(2026, 10, 21),
            ts_end=datetime(2026, 10, 21, 1),
            type_weights=None,
            logical_run_id="run-perf-test",
        )
    timing = results[0]["_timing"]
    assert {"wait", "commit", "close", "total"} <= timing.keys()
    assert all(timing[key] >= 0 for key in ("wait", "commit", "close", "total"))
    sessions[0].commit.assert_awaited_once()
    close.assert_awaited_once()
    assert "[评估批量进度] run=run-perf-test" in caplog.text
    assert "已处理=1/1" in caplog.text
    assert "[批量计时] run=run-perf-test" in caplog.text
    assert "commit p50=" in caplog.text and "close p50=" in caplog.text


async def test_anchor_batch_selects_latest_in_postgres_before_validation(monkeypatch):
    start = datetime(2026, 10, 21, tzinfo=UTC)
    rows = [
        SimpleNamespace(point_id="valid", anchor_ts=start, coverage_until=None),
        SimpleNamespace(point_id="expired", anchor_ts=start, coverage_until=start),
        SimpleNamespace(
            point_id="future", anchor_ts=start + timedelta(seconds=1), coverage_until=None
        ),
    ]
    execute = AsyncMock(return_value=MagicMock(scalars=lambda: SimpleNamespace(all=lambda: rows)))
    own = SimpleNamespace(execute=execute)

    class Session:
        async def __aenter__(self):
            return own

        async def __aexit__(self, *_args):
            return False

    monkeypatch.setattr("app.core.db.AsyncSessionLocal", Session)
    result = await metadata.applicable_anchors_batch(
        None,
        point_ids=[r.point_id for r in rows],
        window_start=start,
    )
    assert result == {"valid": rows[0]}
    sql = str(execute.call_args.args[0].compile(dialect=postgresql.dialect()))
    assert "DISTINCT ON (point_state_anchor.point_id)" in sql
    assert "ORDER BY point_state_anchor.point_id, point_state_anchor.anchor_ts DESC" in sql
