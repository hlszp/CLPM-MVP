"""位号级数据质量体检（点表口径）回归用例（2026-09-26）。

覆盖四类判定：断流 / 质量码坏 / 覆盖率（事件密度）不足 / held_too_long 纳入，
以及两个防坑单测（ISO-Z 字面量、等长前窗），全部用替身数据，不依赖真实库状态。
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.services import data_quality_audit as dq

CUR_START = datetime(2026, 9, 25, 6, 0)
CUR_END = datetime(2026, 9, 25, 8, 0)


def test_iso_z_literal_is_utc_suffixed() -> None:
    """时间字面量必须带 Z（裸字面量会被会话时区解释，偏移 8 小时）。"""
    assert dq.iso_z(datetime(2026, 9, 25, 8, 0)).startswith("2026-09-25T08:00:00")
    assert dq.iso_z(datetime(2026, 9, 25, 8, 0)).endswith("Z")
    assert dq.iso_z(datetime(2026, 9, 25, 16, 0, tzinfo=UTC)).startswith("2026-09-25T16:00:00")


def test_previous_window_equal_length_and_adjacent() -> None:
    start, end = CUR_START, CUR_END
    prev_start, prev_end = dq.previous_window(start, end)
    assert prev_end == start, "基线窗口必须紧邻当前窗口起点"
    assert prev_end - prev_start == end - start, "基线窗口必须等长"


def test_classify_point_matrix() -> None:
    # 断流：窗口内零行
    assert dq.classify_point(None, {"rows": 100}, min_density_ratio=0.5) == (
        [dq.ISSUE_NO_DATA],
        None,
    )
    # 质量码坏：有值但存在非 Good 质量码
    issues, density = dq.classify_point(
        {"rows": 100, "badRows": 5}, {"rows": 100}, min_density_ratio=0.5
    )
    assert issues == [dq.ISSUE_BAD_QUALITY]
    assert density == 1.0
    # 密度不足：相对等长前窗事件数骤降
    issues, density = dq.classify_point(
        {"rows": 10, "badRows": 0}, {"rows": 100}, min_density_ratio=0.5
    )
    assert issues == [dq.ISSUE_LOW_DENSITY]
    assert density == 0.1
    # 基线窗口无数据：密度不可比 → 不误判（仅 None）
    issues, density = dq.classify_point(
        {"rows": 10, "badRows": 0}, {"rows": 0}, min_density_ratio=0.5
    )
    assert issues == []
    assert density is None


async def test_audit_flags_three_issue_types(monkeypatch) -> None:
    """三种问题各自落到对应位号上，且汇总数正确。"""
    points = [
        {"pointId": "p1", "tagName": "TAG-NO-DATA", "loopId": None, "role": None},
        {"pointId": "p2", "tagName": "TAG-BAD", "loopId": None, "role": None},
        {"pointId": "p3", "tagName": "TAG-SPARSE", "loopId": None, "role": None},
    ]

    async def _points(_db, *, loop_id=None):
        return points

    async def _stats(_ids, start, _end):
        if start == CUR_START:  # 当前窗口
            return {
                "p2": {"rows": 100, "badRows": 7},
                "p3": {"rows": 10, "badRows": 0},
            }
        return {  # 基线窗口
            "p2": {"rows": 100, "badRows": 0},
            "p3": {"rows": 100, "badRows": 0},
        }

    monkeypatch.setattr(dq, "resolve_audit_points", _points)
    monkeypatch.setattr(dq, "fetch_point_stats", _stats)

    data = await dq.audit_data_quality(
        object(), start=CUR_START, end=CUR_END, min_density_ratio=0.5
    )
    assert data["summary"] == {
        "points": 3,
        "noData": 1,
        "badQuality": 1,
        "lowDensity": 1,
        "withHeldTooLong": 0,
    }
    by_id = {it["pointId"]: it for it in data["points"]}
    assert by_id["p1"]["issues"] == [dq.ISSUE_NO_DATA]
    assert by_id["p2"]["issues"] == [dq.ISSUE_BAD_QUALITY]
    assert by_id["p3"]["issues"] == [dq.ISSUE_LOW_DENSITY]
    assert by_id["p1"]["rows"] == 0


async def test_held_too_long_is_included(monkeypatch) -> None:
    """R2 的 held_too_long 必须纳入体检结果（原宽表工具没有这项信息）。"""
    points = [{"pointId": "p1", "tagName": "TAG-PV", "loopId": "loop-1", "role": "PV"}]

    async def _points(_db, *, loop_id=None):
        return points

    async def _stats(_ids, _start, _end):
        return {"p1": {"rows": 5, "badRows": 0}}

    async def _held(loop_ids, _start, _end):
        assert loop_ids == ["loop-1"]
        return {
            "loop-1": {
                "heldTooLong": 300,
                "gap": 0,
                "pvCoverage": 0.42,
                "expectedSlots": 720,
            }
        }

    monkeypatch.setattr(dq, "resolve_audit_points", _points)
    monkeypatch.setattr(dq, "fetch_point_stats", _stats)
    monkeypatch.setattr(dq, "fetch_held_too_long", _held)

    data = await dq.audit_data_quality(object(), start=CUR_START, end=CUR_END, with_held=True)
    assert data["points"][0]["heldTooLong"] == 300
    assert data["points"][0]["pvCoverage"] == 0.42
    assert data["summary"]["withHeldTooLong"] == 1
    assert data["loops"] and data["loops"][0]["loopId"] == "loop-1"


async def test_window_echo_uses_iso_z(monkeypatch) -> None:
    """回显窗口也必须 ISO-Z（便于运维核对时区）。"""

    async def _points(_db, *, loop_id=None):
        return []

    async def _stats(_ids, _start, _end):
        return {}

    monkeypatch.setattr(dq, "resolve_audit_points", _points)
    monkeypatch.setattr(dq, "fetch_point_stats", _stats)

    data = await dq.audit_data_quality(object(), start=CUR_START, end=CUR_END, with_held=False)
    assert data["window"]["start"].endswith("Z")
    assert data["window"]["reference"]["end"] == data["window"]["start"]
    assert data["summary"]["points"] == 0
