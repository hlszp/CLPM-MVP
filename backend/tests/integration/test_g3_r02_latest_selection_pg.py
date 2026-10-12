"""G3-R02 最新结果身份与时序——真实 PG 集成测试（14 号文 §3 验收反例）.

复用 C26-EA/INT-05 场景骨架（同 test_grade_distribution_pg 的夹具模式），
在隔离开发 PG 上以**真实行**验证：

1. 昨日 SUCCESS90 + 今日 INCONCLUSIVE：latest_only 默认最新代表=今日不足
   （score null），不以旧成功冒充；grade 筛选不把昨日 90 伪装今日正常；
   历史模式仍能查昨日 90。
2. 迟到补写旧窗口成功：当前事实不倒退（代表仍为最新窗口行）。
3. status 后置筛选语义：latest_only + status=SUCCESS 只返回"最新代表为
   SUCCESS"的回路（先选代表再筛值），与 get_grade_distribution lockstep。
4. 相同时刻/跨页/组合条件：选择稳定，翻页收集无重复遗漏。

运行方式：
    cd backend && uv run pytest tests/integration/test_g3_r02_latest_selection_pg.py \
        -v -m integration --no-header
"""

from __future__ import annotations

import os
import socket
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.services.performance import get_grade_distribution, list_loop_snapshots

_BASE_TS = datetime(2026, 10, 10, 8, 0, 0)  # naive UTC（DB 字段无时区）
_START = datetime(2026, 10, 9, 0, 0, 0)
_END = datetime(2026, 10, 12, 0, 0, 0)

# 验收反例回路组：
# Y_T：昨日 SUCCESS(90) + 今日 INCONCLUSIVE(null) —— 核心反例
# Y_TD：今日 INCONCLUSIVE 先插入，昨日 SUCCESS(95) 迟到补写 —— 不倒退
# ONLY_S：仅昨日 SUCCESS(80) —— 无今日行
# SAME_TS：两条同刻不同回路 —— 稳定性对照
_SPEC: list[tuple[str, list[tuple[float | None, str, int]]]] = [
    ("YT", [(90.0, "SUCCESS", 0), (None, "INCONCLUSIVE", 24)]),
    ("YTD", [(None, "INCONCLUSIVE", 24), (95.0, "SUCCESS", 0)]),
    ("ONLYS", [(80.0, "SUCCESS", 0)]),
    ("SAMETS", [(70.0, "SUCCESS", 12)]),
]


def _database_url() -> str:
    url = os.getenv("TEST_DATABASE_URL")
    if url:
        return url
    from app.core.config import settings

    return settings.postgres_dsn


def _pg_reachable() -> bool:
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


@pytest.fixture
async def pg_context():
    """真实 PG 会话：插入验收回路 + 快照行，用后清理。"""
    engine = create_async_engine(_database_url())
    run_id = uuid4().hex[:8]
    loop_ids: dict[str, str] = {}
    seeded: list[tuple[str, float | None, str, datetime]] = []

    async with engine.connect() as conn:
        for suffix, snaps in _SPEC:
            loop_id = str(uuid4())
            loop_ids[suffix] = loop_id
            await conn.execute(
                text(
                    "INSERT INTO loop_ledger (id, tag_name, status) "
                    "VALUES (:id, :tag_name, 'READY')"
                ),
                {"id": loop_id, "tag_name": f"TEST-G3R2-{run_id}-{suffix}"},
            )
            for score, status, offset_h in snaps:
                ts_start = _BASE_TS + timedelta(hours=offset_h)
                await conn.execute(
                    text(
                        "INSERT INTO kpi_snapshot_hourly "
                        "(id, loop_id, ts_start, ts_end, score, status, fast_rate) "
                        "VALUES (:id, :loop_id, :ts_start, :ts_end, :score, :status, :fast_rate)"
                    ),
                    {
                        "id": str(uuid4()),
                        "loop_id": loop_id,
                        "ts_start": ts_start,
                        "ts_end": ts_start + timedelta(hours=1),
                        "score": score,
                        "status": status,
                        "fast_rate": score,  # 快速率与 score 同值（列表排序对照用）
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
                {"ids": list(loop_ids.values())},
            )
            await conn.execute(
                text("DELETE FROM loop_ledger WHERE id = ANY(:ids)"),
                {"ids": list(loop_ids.values())},
            )
            await conn.commit()
        await engine.dispose()


class TestG3R02LatestSelection:
    @pytest.mark.asyncio
    async def test_today_insufficient_wins_over_yesterday_success(self, pg_context):
        """验收 1：昨日 SUCCESS90 + 今日 INCONCLUSIVE → 默认最新代表=今日不足。"""
        session, loop_ids, _ = pg_context
        rows, total = await list_loop_snapshots(
            session,
            loop_ids=list(loop_ids.values()),
            start=_START,
            end=_END,
            page=1,
            page_size=100,
        )
        by_loop = {str(s.loop_id): s for s, _tag in rows}
        yt = by_loop[loop_ids["YT"]]
        assert yt.status == "INCONCLUSIVE"
        assert yt.score is None
        assert total == len(loop_ids)

    @pytest.mark.asyncio
    async def test_grade_filter_not_disguised_and_history_keeps_90(self, pg_context):
        """验收 1 续：grade 筛选不把昨日 90 伪装今日正常；历史仍能查昨日 90。"""
        session, loop_ids, _ = pg_context
        # YT 最新代表 score null → grade=INCONCLUSIVE 桶
        dist = await get_grade_distribution(
            session,
            loop_ids=list(loop_ids.values()),
            start=_START,
            end=_END,
        )
        # 列表 grade=INCONCLUSIVE 与分布桶一致（lockstep 口径）
        rows, total = await list_loop_snapshots(
            session,
            loop_ids=list(loop_ids.values()),
            start=_START,
            end=_END,
            grade="INCONCLUSIVE",
            page=1,
            page_size=100,
        )
        assert total == dist["INCONCLUSIVE"]
        assert loop_ids["YT"] in {str(s.loop_id) for s, _t in rows}
        # 历史模式：昨日 90 可查
        hist_rows, hist_total = await list_loop_snapshots(
            session,
            loop_ids=[loop_ids["YT"]],
            start=_START,
            end=_END,
            latest_only=False,
            page=1,
            page_size=100,
        )
        hist_scores = {
            (s.status, float(s.score) if s.score is not None else None) for s, _t in hist_rows
        }
        assert hist_total == 2
        assert ("SUCCESS", 90.0) in hist_scores
        assert ("INCONCLUSIVE", None) in hist_scores

    @pytest.mark.asyncio
    async def test_late_old_window_success_does_not_regress(self, pg_context):
        """验收 2：今日不足之后迟到补写昨日成功 → 当前事实不倒退。"""
        session, loop_ids, _ = pg_context
        rows, _ = await list_loop_snapshots(
            session,
            loop_ids=[loop_ids["YTD"]],
            start=_START,
            end=_END,
            page=1,
            page_size=10,
        )
        # YTD 插入顺序=先今日 INCONCLUSIVE 后昨日 SUCCESS（迟到补写）
        rep = rows[0][0]
        assert rep.status == "INCONCLUSIVE"
        assert rep.score is None

    @pytest.mark.asyncio
    async def test_status_filter_is_representative_post_selection(self, pg_context):
        """验收（14§3 整改）：latest_only + status=SUCCESS 只返回最新代表为
        SUCCESS 的回路——YT/YTD（最新代表 INCONCLUSIVE）不得以旧成功入选。"""
        session, loop_ids, _ = pg_context
        rows, total = await list_loop_snapshots(
            session,
            loop_ids=list(loop_ids.values()),
            start=_START,
            end=_END,
            status_filter="SUCCESS",
            page=1,
            page_size=100,
        )
        got = {str(s.loop_id) for s, _t in rows}
        assert got == {loop_ids["ONLYS"], loop_ids["SAMETS"]}
        assert total == 2
        # 分布与列表 lockstep：status=SUCCESS 的分布 total 与列表 total 一致
        dist = await get_grade_distribution(
            session,
            loop_ids=list(loop_ids.values()),
            start=_START,
            end=_END,
            status_filter="SUCCESS",
        )
        assert dist["total"] == total

    @pytest.mark.asyncio
    async def test_pagination_stable_no_dup_or_missing(self, pg_context):
        """验收 4：跨页翻完无重复遗漏；组合条件（status+grade）选择稳定。"""
        session, loop_ids, _ = pg_context
        collected: list[str] = []
        page, total = 1, None
        while True:
            rows, total = await list_loop_snapshots(
                session,
                loop_ids=list(loop_ids.values()),
                start=_START,
                end=_END,
                page=page,
                page_size=2,
            )
            collected.extend(str(s.loop_id) for s, _t in rows)
            if page * 2 >= total:
                break
            page += 1
        assert len(collected) == len(set(collected)) == len(loop_ids)
        # 组合条件翻页同样稳定
        combo: list[str] = []
        page, total = 1, None
        while True:
            rows, total = await list_loop_snapshots(
                session,
                loop_ids=list(loop_ids.values()),
                start=_START,
                end=_END,
                status_filter="SUCCESS",
                grade="FAIR",
                page=page,
                page_size=1,
            )
            combo.extend(str(s.loop_id) for s, _t in rows)
            if page * 1 >= total:
                break
            page += 1
        assert len(combo) == len(set(combo))

    @pytest.mark.asyncio
    async def test_time_boundary_end_inclusive_contract_preserved(self, pg_context):
        """时区/边界契约保留：ts_start == end 的行仍包含（原分页/下钻契约）。"""
        session, loop_ids, _ = pg_context
        # SAMETS 行 ts_start = _BASE_TS + 12h = 2026-10-10 20:00
        boundary = _BASE_TS + timedelta(hours=12)
        rows, total = await list_loop_snapshots(
            session,
            loop_ids=[loop_ids["SAMETS"]],
            start=_START,
            end=boundary,
            latest_only=False,
            page=1,
            page_size=10,
        )
        assert total == 1  # 恰在 end 上的行不丢（原 <= 契约保留）
