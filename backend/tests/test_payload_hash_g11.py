"""payload_hash 一致性与空 hash 冲突语义（整改 G11）。

背景
----
历史导入的批量写点表把 payload_hash 硬编码为空串（原 docstring 的理由是
"导入行靠 ts UPSERT 幂等，hash 仅实时路径冲突登记用"）。该判断有误：
write_events 正是用 hash 判断同 ts 的实时事件是"幂等跳过"还是"冲突丢弃"。
空串与实时 hash 永不相等 → 每个同 ts 实时事件都被判冲突 → 按默认
conflict_policy=skip **静默丢弃实时值**，且 history_point_conflict 因写路径
未传 db_session 而永远为空，连审计都没有。

本文件守护两件事：
1. hash 公式单源（PointEvent 与导入侧共用 compute_payload_hash，逐字节一致），
   且 source_kind 不参与 hash（设计 §4.1 语义去重不变量）；
2. 既有行 hash 为空时不构成冲突证据，实时值必须写入而非被 skip 丢弃。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from app.services.data_source import point_history_repository as repo
from app.services.data_source.point_history_repository import (
    PointEvent,
    compute_payload_hash,
    write_events,
)

_TS = datetime(2026, 9, 13, 3, 0, 0, tzinfo=UTC)
_PID = "00000000-0000-0000-0000-0000000000aa"


def _ev(**kw) -> PointEvent:
    base: dict = {
        "point_id": _PID,
        "ts": _TS,
        "value": 50.0,
        "quality_class": 1,
        "quality_raw": 1,
    }
    base.update(kw)
    return PointEvent(**base)


class TestPayloadHashParity:
    """hash 公式单源与设计不变量。"""

    def test_point_event_hash_matches_shared_helper(self) -> None:
        """PointEvent.payload_hash 必须与 compute_payload_hash 逐字节一致。"""
        ev = _ev()
        assert ev.payload_hash() == compute_payload_hash(
            point_id=_PID,
            ts_ms=int(_TS.timestamp() * 1000),
            value=50.0,
            quality_class=1,
            quality_raw=1,
        )

    def test_source_kind_not_in_hash(self) -> None:
        """设计 §4.1：同一事实不因来源类别不同被判冲突（source_kind 不参与）。"""
        assert _ev(source_kind=1).payload_hash() == _ev(source_kind=4).payload_hash()

    def test_value_and_quality_change_hash(self) -> None:
        """值或质量变化必须改变 hash（否则真实冲突会被误当幂等跳过）。"""
        base = _ev().payload_hash()
        assert _ev(value=51.0).payload_hash() != base
        assert _ev(quality_class=0).payload_hash() != base
        assert _ev(quality_raw=2).payload_hash() != base

    def test_hash_is_not_empty(self) -> None:
        """hash 必须是 64 位十六进制（回归"导入侧写空串"的缺陷形态）。"""
        h = _ev().payload_hash()
        assert len(h) == 64 and h.strip() == h and h


class TestEmptyPriorHashDoesNotDropRealtime:
    """既有行 hash 为空时，实时值必须写入而非被 skip 丢弃。"""

    def _run(self, monkeypatch, prior_hash: str, *, policy: str = "skip"):
        """跑一次 write_events，返回 (WriteResult, 是否触发了写入)."""
        existing = {
            _PID: [
                {
                    "ts": _TS,
                    "value": 50.0,
                    "quality_class": 1,
                    "quality_raw": 1,
                    "payload_hash": prior_hash,
                }
            ]
        }
        inserted: list[str] = []

        async def _fake_read_events(*a, **kw):
            return existing

        async def _fake_execute(sql):
            inserted.append(sql)
            return None

        monkeypatch.setattr(repo, "read_events", _fake_read_events)
        monkeypatch.setattr(repo, "execute_native", _fake_execute)
        result = asyncio.run(write_events([_ev()], conflict_policy=policy))
        return result, inserted

    def test_empty_prior_hash_inserts_instead_of_skipping(self, monkeypatch) -> None:
        """既有行 hash 为空（历史遗留导入行）→ 写入，不得静默丢弃实时值。"""
        result, inserted = self._run(monkeypatch, prior_hash="")
        assert inserted, "空 hash 的既有行导致实时值被丢弃（G11 回归）"
        assert result.conflicts
        assert result.conflicts[0]["resolution"] == "overwrite_no_hash"

    def test_matching_hash_still_skips_idempotently(self, monkeypatch) -> None:
        """hash 相同 → 仍走幂等跳过（不得因本修复退化为重复写入）。"""
        result, inserted = self._run(monkeypatch, prior_hash=_ev().payload_hash())
        assert not inserted
        assert result.identical_skipped == 1

    def test_differing_hash_still_conflicts(self, monkeypatch) -> None:
        """hash 不同且非空 → 维持原冲突语义（本次不改变真实冲突的处理）。"""
        result, inserted = self._run(monkeypatch, prior_hash="f" * 64)
        assert not inserted
        assert result.conflicts and result.conflicts[0]["resolution"] == "skip"
