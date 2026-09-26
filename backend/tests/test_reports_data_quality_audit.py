"""位号级数据质量体检端点用例（GET /reports/data-quality/audit，2026-09-26）。"""

from __future__ import annotations

import pytest

from app.api.v1.endpoints import reports as reports_ep
from app.core.exceptions import BizError


def _raw(start: str = "2026-09-25T06:00:00.000Z", end: str = "2026-09-25T08:00:00.000Z") -> dict:
    """替身体检结果（形状与 data_quality_audit.audit_data_quality 一致）。"""
    return {
        "window": {
            "start": start,
            "end": end,
            "reference": {"start": "2026-09-25T04:00:00.000Z", "end": start},
        },
        "minDensityRatio": 0.5,
        "summary": {
            "points": 3,
            "noData": 1,
            "badQuality": 1,
            "lowDensity": 1,
            "withHeldTooLong": 1,
        },
        "points": [
            {
                "pointId": "p-1",
                "tagName": "TAG.PV",
                "loopId": None,
                "role": "pv",
                "rows": 0,
                "badRows": 0,
                "densityRatio": None,
                "issues": ["no_data"],
                "heldTooLong": 0,
                "pvCoverage": None,
            },
            {
                "pointId": "p-2",
                "tagName": "TAG.OP",
                "loopId": None,
                "role": "op",
                "rows": 100,
                "badRows": 30,
                "densityRatio": 1.0,
                "issues": ["bad_quality"],
                "heldTooLong": 0,
                "pvCoverage": None,
            },
            {
                "pointId": "p-3",
                "tagName": "TAG.MODE",
                "loopId": None,
                "role": "mode",
                "rows": 10,
                "badRows": 0,
                "densityRatio": 0.1,
                "issues": ["low_density"],
                "heldTooLong": 4,
                "pvCoverage": 0.8,
            },
        ],
        "loops": [{"loopId": "L-1", "heldTooLong": 4, "pvCoverage": 0.8, "gap": 0}],
    }


@pytest.fixture
def patch_audit(monkeypatch):
    """替换服务层，避免用例依赖真实 TDengine/PG 状态。"""
    captured: dict = {}

    async def _fake(db, *, start, end, loop_id=None, min_density_ratio=0.5, with_held=True):
        captured.update(
            {
                "start": start,
                "end": end,
                "loop_id": loop_id,
                "min_density_ratio": min_density_ratio,
                "with_held": with_held,
            }
        )
        return _raw()

    monkeypatch.setattr("app.services.data_quality_audit.audit_data_quality", _fake)
    return captured


async def _call(**kwargs):
    """按 FastAPI 默认值补齐参数后直调端点函数（避免 Query 对象参与运算）。"""
    params = {
        "startDate": None,
        "endDate": None,
        "lastHours": 2,
        "loopId": None,
        "minDensityRatio": 0.5,
        "includeHeld": True,
        "issueType": None,
        "page": 1,
        "pageSize": 20,
    }
    params.update(kwargs)
    return await reports_ep.get_report_data_quality_audit(db=None, _=None, **params)


class TestDataQualityAuditEndpoint:
    async def test_returns_paginated_items_with_summary(self, patch_audit) -> None:
        resp = await _call(page=1, pageSize=2)
        data = resp["data"]
        assert data["total"] == 3
        assert len(data["items"]) == 2, "分页未生效"
        assert data["page"] == 1 and data["pageSize"] == 2
        # summary 按过滤后全集统计，不受分页影响
        assert data["summary"] == {
            "points": 3,
            "noData": 1,
            "badQuality": 1,
            "lowDensity": 1,
            "heldFilled": 1,
        }
        assert data["window"]["start"] == "2026-09-25T06:00:00.000Z"
        assert data["window"]["referenceEnd"] == "2026-09-25T06:00:00.000Z"
        assert data["thresholds"]["minDensityRatio"] == 0.5
        assert data["loops"][0]["loopId"] == "L-1"

    async def test_second_page_returns_remainder(self, patch_audit) -> None:
        data = (await _call(page=2, pageSize=2))["data"]
        assert [i["pointId"] for i in data["items"]] == ["p-3"]
        assert data["total"] == 3

    async def test_filters_by_issue_type(self, patch_audit) -> None:
        data = (await _call(issueType="no_data"))["data"]
        assert [i["pointId"] for i in data["items"]] == ["p-1"]
        assert data["summary"]["points"] == 1
        assert data["summary"]["badQuality"] == 0, "过滤后 summary 未随之收窄"

    async def test_held_filter_uses_held_too_long(self, patch_audit) -> None:
        data = (await _call(issueType="held"))["data"]
        assert [i["pointId"] for i in data["items"]] == ["p-3"]
        assert data["summary"]["heldFilled"] == 1

    async def test_rejects_unknown_issue_type(self, patch_audit) -> None:
        with pytest.raises(BizError) as exc:
            await _call(issueType="bogus")
        assert exc.value.code == "ERR_PARAM"
        assert exc.value.status_code == 400

    async def test_passes_window_and_flags_to_service(self, patch_audit) -> None:
        await _call(lastHours=6, loopId="L-9", minDensityRatio=0.25, includeHeld=False)
        assert patch_audit["loop_id"] == "L-9"
        assert patch_audit["min_density_ratio"] == 0.25
        assert patch_audit["with_held"] is False
        span = patch_audit["end"] - patch_audit["start"]
        assert span.total_seconds() == 6 * 3600, "lastHours 未正确换算为窗口"

    async def test_defaults_to_24h_naive_utc_window(self, patch_audit) -> None:
        await _call(lastHours=None)
        start, end = patch_audit["start"], patch_audit["end"]
        assert end.tzinfo is None and start.tzinfo is None, "服务层要求 naive UTC"
        assert (end - start).total_seconds() == 24 * 3600
