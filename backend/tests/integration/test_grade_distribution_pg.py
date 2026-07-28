"""grade-distribution / grade 筛选 真实 PG 集成测试（Phase 4 性能项）.

验证：
1. GET /performance/grade-distribution 的 SQL 聚合结果与"逐行取每回路最新快照
   → Python 逐个判定等级"的统计完全一致；
2. /loops/snapshots?grade=X 的服务端筛选 + 分页结果与分布桶计数一致，
   且每条返回记录的 score 均落在该等级阈值区间内；
3. 不传 grade 时列表 total 与分布 total 一致（向后兼容）。

测试前提：
- 本地开发 PG 可达（默认读取 app settings 的 postgres_dsn，
  可用环境变量 TEST_DATABASE_URL 覆盖）

运行方式：
    cd backend && uv run pytest tests/integration/test_grade_distribution_pg.py \
        -v -m integration --no-header

CI 跳过：pyproject.toml addopts 中 -m "not integration" 默认排除。
"""

from __future__ import annotations

import os
import socket
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.services.performance import (
    _load_grading_thresholds,
    get_grade_distribution,
    list_loop_snapshots,
)

# ---------------------------------------------------------------------------
# 测试数据：8 个回路覆盖 5 级 + INCONCLUSIVE + latestOnly 语义
# ---------------------------------------------------------------------------

_BASE_TS = datetime(2026, 7, 20, 8, 0, 0)  # naive UTC（DB 字段无时区）
_START = datetime(2026, 7, 19, 0, 0, 0)
_END = datetime(2026, 7, 21, 0, 0, 0)

# (tag_suffix, [(score, status, ts_offset_hours), ...])
# L7: 较新的 INCONCLUSIVE + 较旧的 SUCCESS(95) → latestOnly 优先非 INCONCLUSIVE → 95
# L8: 两条 SUCCESS，新的 85 覆盖旧的 30 → 最新一条 85
_LOOP_SPECS: list[tuple[str, list[tuple[float | None, str, int]]]] = [
    ("L1", [(95.0, "SUCCESS", 0)]),
    ("L2", [(85.0, "SUCCESS", 0)]),
    ("L3", [(70.0, "SUCCESS", 0)]),
    ("L4", [(50.0, "SUCCESS", 0)]),
    ("L5", [(30.0, "SUCCESS", 0)]),
    ("L6", [(None, "INCONCLUSIVE", 0)]),
    ("L7", [(95.0, "SUCCESS", 0), (None, "INCONCLUSIVE", 1)]),
    ("L8", [(30.0, "SUCCESS", 0), (85.0, "SUCCESS", 1)]),
]


def _database_url() -> str:
    """测试库连接串：优先 TEST_DATABASE_URL，否则取 app settings。"""
    url = os.getenv("TEST_DATABASE_URL")
    if url:
        return url
    from app.core.config import settings

    return settings.postgres_dsn


def _pg_reachable() -> bool:
    """检查 PG 端口是否可达（socket 探测，不依赖额外驱动）。"""
    try:
        from app.core.config import settings

        with socket.create_connection((settings.POSTGRES_HOST, settings.POSTGRES_PORT), timeout=3):
            return True
    except Exception:
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _pg_reachable(), reason="PG 不可达，跳过集成测试"),
]


def _python_grade(score: float | None, thresholds: list[dict]) -> str:
    """Python 侧逐行等级判定（与 SQL CASE 语义一致）。"""
    if score is None:
        return "INCONCLUSIVE"
    for t in thresholds:
        if t["level"] == 1:
            if score >= t["minScore"]:
                return t["name"]
        elif score >= t["minScore"] and score < t["maxScore"]:
            return t["name"]
    return "INCONCLUSIVE"


def _reference_distribution(
    snapshots: list[tuple[str, float | None, str, datetime]],
    thresholds: list[dict],
) -> dict:
    """逐行统计参考值：每回路取 latestOnly 口径的最新一条，再按等级计数。

    snapshots: [(loop_id, score, status, ts_start), ...]
    latestOnly 口径：status != 'INCONCLUSIVE' 优先，其次 ts_start 最新。
    """
    latest: dict[str, tuple[float | None, str, datetime]] = {}
    for loop_id, score, status, ts_start in snapshots:
        current = latest.get(loop_id)
        if current is None:
            latest[loop_id] = (score, status, ts_start)
            continue
        _, cur_status, cur_ts = current
        cur_key = (cur_status != "INCONCLUSIVE", cur_ts)
        new_key = (status != "INCONCLUSIVE", ts_start)
        if new_key > cur_key:
            latest[loop_id] = (score, status, ts_start)

    dist = {"EXCELLENT": 0, "GOOD": 0, "FAIR": 0, "WARNING": 0, "POOR": 0, "INCONCLUSIVE": 0}
    for score, _, _ in latest.values():
        dist[_python_grade(score, thresholds)] += 1
    dist["total"] = len(latest)
    return dist


@pytest.fixture
async def pg_context():
    """真实 PG 会话：插入 8 个测试回路 + 快照，用后清理。"""
    from uuid import uuid4

    engine = create_async_engine(_database_url())
    run_id = uuid4().hex[:8]
    loop_ids: list[str] = []
    seeded: list[tuple[str, float | None, str, datetime]] = []

    async with engine.connect() as conn:
        for suffix, snaps in _LOOP_SPECS:
            loop_id = str(uuid4())
            loop_ids.append(loop_id)
            tag_name = f"TEST-GRADE-{run_id}-{suffix}"
            await conn.execute(
                text(
                    "INSERT INTO loop_ledger (id, tag_name, status) "
                    "VALUES (:id, :tag_name, 'READY')"
                ),
                {"id": loop_id, "tag_name": tag_name},
            )
            for score, status, offset_h in snaps:
                ts_start = _BASE_TS + timedelta(hours=offset_h)
                ts_end = ts_start + timedelta(hours=1)
                await conn.execute(
                    text(
                        "INSERT INTO kpi_snapshot_hourly "
                        "(id, loop_id, ts_start, ts_end, score, status) "
                        "VALUES (:id, :loop_id, :ts_start, :ts_end, :score, :status)"
                    ),
                    {
                        "id": str(uuid4()),
                        "loop_id": loop_id,
                        "ts_start": ts_start,
                        "ts_end": ts_end,
                        "score": score,
                        "status": status,
                    },
                )
                seeded.append((loop_id, score, status, ts_start))
        await conn.commit()

    session = AsyncSession(engine)
    try:
        yield session, loop_ids, seeded
    finally:
        await session.close()
        async with engine.connect() as conn:
            await conn.execute(
                text("DELETE FROM kpi_snapshot_hourly WHERE loop_id = ANY(:ids)"),
                {"ids": loop_ids},
            )
            await conn.execute(
                text("DELETE FROM loop_ledger WHERE id = ANY(:ids)"),
                {"ids": loop_ids},
            )
            await conn.commit()
        await engine.dispose()


class TestGradeDistributionPg:
    """真实 PG：SQL 聚合结果与逐行统计一致。"""

    @pytest.mark.asyncio
    async def test_distribution_matches_row_by_row(self, pg_context) -> None:
        """聚合结果 == 逐行（每回路最新快照 → 逐条判定等级）统计。"""
        session, loop_ids, seeded = pg_context
        thresholds = await _load_grading_thresholds(session)

        distribution = await get_grade_distribution(
            session,
            loop_ids=loop_ids,
            start=_START,
            end=_END,
        )

        expected = _reference_distribution(seeded, thresholds)
        assert distribution == expected
        # 数据确实落进了多个桶（防止"全 0 也通过"的假阳性）
        assert expected["total"] == len(_LOOP_SPECS)
        assert sum(1 for g, c in expected.items() if g != "total" and c > 0) >= 4

    @pytest.mark.asyncio
    async def test_grade_filter_matches_distribution_bucket(self, pg_context) -> None:
        """grade 筛选 + 服务端分页：每页 2 条翻完，集合与分布桶一致。"""
        session, loop_ids, seeded = pg_context
        thresholds = await _load_grading_thresholds(session)
        reference = _reference_distribution(seeded, thresholds)

        # 每回路最新一条（latestOnly 口径）的 loop_id → grade 映射
        latest: dict[str, tuple[float | None, str, datetime]] = {}
        for loop_id, score, status, ts_start in seeded:
            current = latest.get(loop_id)
            if current is None or (status != "INCONCLUSIVE", ts_start) > (
                current[1] != "INCONCLUSIVE",
                current[2],
            ):
                latest[loop_id] = (score, status, ts_start)
        loop_grade = {
            lid: _python_grade(score, thresholds) for lid, (score, _, _) in latest.items()
        }

        for grade in ("EXCELLENT", "GOOD", "FAIR", "WARNING", "POOR", "INCONCLUSIVE"):
            collected: list[str] = []
            page = 1
            total = None
            while True:
                rows, total = await list_loop_snapshots(
                    session,
                    loop_ids=loop_ids,
                    start=_START,
                    end=_END,
                    grade=grade,
                    page=page,
                    page_size=2,
                )
                for snap, _tag in rows:
                    lid = str(snap.loop_id)
                    collected.append(lid)
                    # 每条返回记录的等级必须等于筛选等级
                    assert loop_grade[lid] == grade
                if page * 2 >= total:
                    break
                page += 1

            # total 与分布桶计数一致，且翻页收集到的集合无重无漏
            assert total == reference[grade], f"grade={grade}"
            assert len(collected) == len(set(collected)) == reference[grade], f"grade={grade}"

    @pytest.mark.asyncio
    async def test_list_total_matches_distribution_total(self, pg_context) -> None:
        """不传 grade：列表 total == 分布 total（向后兼容，口径一致）。"""
        session, loop_ids, _seeded = pg_context

        distribution = await get_grade_distribution(
            session,
            loop_ids=loop_ids,
            start=_START,
            end=_END,
        )
        _, total = await list_loop_snapshots(
            session,
            loop_ids=loop_ids,
            start=_START,
            end=_END,
            page=1,
            page_size=100,
        )

        assert total == distribution["total"] == len(_LOOP_SPECS)

    @pytest.mark.asyncio
    async def test_invalid_grade_raises_biz_error(self, pg_context) -> None:
        """非法等级名抛 ERR_INVALID_GRADE。"""
        from app.core.exceptions import BizError

        session, loop_ids, _seeded = pg_context
        with pytest.raises(BizError) as exc_info:
            await list_loop_snapshots(
                session,
                loop_ids=loop_ids,
                start=_START,
                end=_END,
                grade="SUPERB",
            )
        assert exc_info.value.code == "ERR_INVALID_GRADE"
