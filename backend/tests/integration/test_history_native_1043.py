"""显式本机 TDengine 隔离库：跨点分页、LAST_ROW、INTERVAL 与批量 UPSERT。

TEST_TDENGINE_REST_URL=http://127.0.0.1:17115 uv run pytest \
  tests/integration/test_history_native_1043.py -m integration -q
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse
from uuid import uuid4

import pytest

from app.core import tdengine_native as native
from app.core.config import settings
from app.services.data_source import point_history_repository as repo

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.getenv("TEST_TDENGINE_REST_URL"), reason="需显式提供本机隔离 TDengine 端点"
    ),
]


@pytest.fixture
async def scratch_td(monkeypatch):
    target = urlparse(os.environ["TEST_TDENGINE_REST_URL"])
    assert target.hostname in ("127.0.0.1", "localhost"), "此测试只允许本机隔离库"
    monkeypatch.setattr(settings, "TDENGINE_HOST", target.hostname)
    monkeypatch.setattr(settings, "TDENGINE_PORT", target.port - 11)
    monkeypatch.setattr(native, "_TD_REST_PORT", target.port)
    native.TDengineConnectionPool.close_all()
    database = "qa1043_" + uuid4().hex[:12]
    await native.execute_native(f"CREATE DATABASE {database} PRECISION 'ms'")
    monkeypatch.setattr(settings, "TDENGINE_DB", database)
    native.TDengineConnectionPool.close_all()
    try:
        await repo.ensure_schema()
        yield database
    finally:
        await native.execute_native(f"DROP DATABASE IF EXISTS {database}")
        native.TDengineConnectionPool.close_all()


async def test_native_functions_and_tied_timestamp_pages(scratch_td, monkeypatch):
    origin = datetime(2026, 10, 18, tzinfo=UTC)
    first, second = str(uuid4()), str(uuid4())
    events = []
    for i in range(3):
        for pid in [first, second]:
            value = None if pid == first and i == 1 else float(i)
            quality = 0 if pid == first and i == 2 else 1
            events.append(
                repo.PointEvent(
                    point_id=pid,
                    ts=origin + timedelta(seconds=i),
                    value=value,
                    quality_class=quality,
                    quality_raw=quality,
                    quality_schema=repo.QSCHEMA_AAS,
                    source_kind=repo.SOURCE_KIND_HISTORY,
                    source_id="qa1043",
                    received_at=origin,
                )
            )
    for _ in range(2):
        result = await repo.write_events(events, dedup=False)
        assert result.inserted == 6 and not result.failed
    monkeypatch.setattr(repo, "READ_CHUNK_ROWS", 3)
    rows = await repo.read_events([first, second], origin, origin + timedelta(seconds=3))
    assert [row["value"] for row in rows[first]] == [0, None, 2]
    assert [row["value"] for row in rows[second]] == [0, 1, 2]
    count = await native.execute_native(
        f"SELECT COUNT(*) AS n FROM {scratch_td}.{repo.POINT_STABLE}"
    )
    assert count[0]["n"] == 6  # 相同 ts 重放没有增加物理行
    last = await repo.read_last_states_before([first, second], origin + timedelta(seconds=3))
    assert last[first]["value"] == 2 and last[first]["quality_class"] == 0
    seed = await repo.read_last_states_before([first], origin + timedelta(seconds=2))
    assert seed[first]["value"] == 0  # NULL 不覆盖有效种子，BAD 有值仍保留
    buckets = await repo.read_trend_buckets(
        [second], origin, origin + timedelta(seconds=4), interval_s=1
    )
    assert len(buckets[second]) == 4
    assert [value for value, quality in buckets[second].values()] == [0, 1, 2, 2]
