"""SP 随动判定任务测试（2026-10-10 运维圈选功能）。

覆盖：
- 聚合命中 → DELETE+UPSERT 全量重建，evidence/suspected_cascade 正确
- 当前 Cascade（mode_now=2）→ suspected_cascade=True（保留信息不丢）
- 无命中 → 仅 DELETE，不 INSERT
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.tasks.loop_health import _do_compute_sp_follows_pv


def _agg_row(loop_id: str, ratio: float, auto_hours: int = 40, mode_now: float | None = 1.0):
    pv = 5.0
    return SimpleNamespace(
        loop_id=loop_id,
        avg_sp_std=pv * ratio,
        avg_pv_std=pv,
        auto_hours=auto_hours,
        mode_now=mode_now,
    )


def _make_db(agg_rows: list):
    """构造 mock session：首轮聚合 SQL 返回 agg_rows，DELETE/INSERT 记录调用。"""
    db = MagicMock()
    inserts: list[dict] = []
    deleted: list[dict] = []

    async def execute(stmt, params=None, **kwargs):
        sql = str(stmt)
        if sql.strip().upper().startswith("WITH"):
            result = MagicMock()
            result.all.return_value = agg_rows
            return result
        if sql.strip().upper().startswith("DELETE"):
            deleted.append(params or {})
            return MagicMock()
        if sql.strip().upper().startswith("INSERT"):
            inserts.append(params or {})
            return MagicMock()
        return MagicMock()

    db.execute = AsyncMock(side_effect=execute)
    db.commit = AsyncMock()
    return db, inserts, deleted


class TestComputeSpFollowsPv:
    @pytest.mark.asyncio
    async def test_flags_written_with_evidence(self) -> None:
        db, inserts, deleted = _make_db([_agg_row("loop-1", 1.02), _agg_row("loop-2", 0.95)])
        with patch("app.tasks.loop_health.AsyncSessionLocal") as local:
            local.return_value.__aenter__ = AsyncMock(return_value=db)
            local.return_value.__aexit__ = AsyncMock(return_value=None)
            result = await _do_compute_sp_follows_pv()

        assert result["flagged"] == 2
        assert result["suspectedCascade"] == 0
        assert len(deleted) == 1  # 全量重建：先清旧标记
        assert len(inserts) == 2
        ev = json.loads(inserts[0]["evidence"])
        assert ev["autoHours"] == 40
        assert abs(ev["ratio"] - 1.02) < 0.01
        assert inserts[0]["suspected_cascade"] is False
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cascade_marked_suspected(self) -> None:
        db, inserts, _ = _make_db(
            [_agg_row("loop-1", 1.0, mode_now=2.0), _agg_row("loop-2", 0.9, mode_now=1.0)]
        )
        with patch("app.tasks.loop_health.AsyncSessionLocal") as local:
            local.return_value.__aenter__ = AsyncMock(return_value=db)
            local.return_value.__aexit__ = AsyncMock(return_value=None)
            result = await _do_compute_sp_follows_pv()

        assert result["suspectedCascade"] == 1
        by_loop = {i["loop_id"]: i for i in inserts}
        assert by_loop["loop-1"]["suspected_cascade"] is True
        assert by_loop["loop-2"]["suspected_cascade"] is False

    @pytest.mark.asyncio
    async def test_no_hits_clears_only(self) -> None:
        db, inserts, deleted = _make_db([])
        with patch("app.tasks.loop_health.AsyncSessionLocal") as local:
            local.return_value.__aenter__ = AsyncMock(return_value=db)
            local.return_value.__aexit__ = AsyncMock(return_value=None)
            result = await _do_compute_sp_follows_pv()

        assert result["flagged"] == 0
        assert len(deleted) == 1
        assert inserts == []
        db.commit.assert_awaited_once()

    def test_beat_registered(self) -> None:
        """Beat 每日 03:40 注册（避开诊断 00:30 / 归档 04:30）。"""
        from app.tasks.celery_app import celery_app

        entry = celery_app.conf.beat_schedule.get("loop-health-daily")
        assert entry is not None
        assert entry["task"] == "app.tasks.loop_health.compute_loop_health_flags"
        cron = entry["schedule"]
        assert (cron.hour, cron.minute) == ({3}, {40})
