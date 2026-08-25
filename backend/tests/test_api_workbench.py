"""工作台 v2.0 BFF 端点单测（M1 skeleton）。

覆盖 12 个端点（A-01~A-13 跳过 A-06）：
- skeleton GET 端点返回 200 + 结构完整（参数化）
- A-10 plugins 查询 module_plugin（mock DB 返回空列表）
- A-12 events/read 批量标记已读（mock DB 返回 rowcount）
- A-09 lane-more 参数校验（缺少 lane → 422）
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def auth_user() -> MagicMock:
    user = MagicMock()
    user.id = 1
    user.role = "ADMIN"
    user.display_name = "测试管理员"
    return user


@pytest.fixture
def auth_client(client: object, auth_user: MagicMock):
    """在 client fixture 基础上 override get_current_user。"""
    from app.api.deps import get_current_user
    from app.main import app

    async def _override() -> MagicMock:
        return auth_user

    app.dependency_overrides[get_current_user] = _override
    yield client
    app.dependency_overrides.pop(get_current_user, None)


# ---------------------------------------------------------------------------
# skeleton GET 端点（不依赖 DB 查询，返回结构完整的空数据）
# ---------------------------------------------------------------------------

_SKELETON_GET_PATHS = [
    "/api/v1/workbench/overview",
    "/api/v1/workbench/assessment",
    "/api/v1/workbench/diagnosis",
    "/api/v1/workbench/tuning",
    "/api/v1/workbench/handling",
    "/api/v1/workbench/flags",
    "/api/v1/workbench/staff-load",
    "/api/v1/workbench/aggregate",
    "/api/v1/workbench/tuning-scatters",
]


@pytest.mark.parametrize("path", _SKELETON_GET_PATHS)
def test_skeleton_get_endpoints_return_200(auth_client: object, path: str) -> None:
    """skeleton GET 端点返回 200 + success=True + data 结构完整。"""
    resp = auth_client.get(path)
    assert resp.status_code == 200, f"{path} 返回 {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["code"] == "0"
    assert body["data"] is not None


def test_overview_returns_windows_structure(auth_client: object) -> None:
    """A-01 overview 返回三窗口 + plants/units/pareto/roots 结构。"""
    resp = auth_client.get("/api/v1/workbench/overview")
    data = resp.json()["data"]
    assert set(data["windows"].keys()) == {"24h", "7d", "30d"}
    assert data["plants"] == []
    assert data["units"] == []
    assert data["pareto"] == []
    assert data["roots"] == []


def test_handling_returns_kanban_four_lanes(auth_client: object) -> None:
    """A-05 handling 返回 4 泳道看板结构。"""
    resp = auth_client.get("/api/v1/workbench/handling")
    data = resp.json()["data"]
    assert set(data["kanban"].keys()) == {"PENDING", "EXECUTING", "VERIFYING", "CLOSED"}


def test_aggregate_returns_cache_meta(auth_client: object) -> None:
    """A-11 aggregate 返回 cache_hit + elapsed_ms 元数据。"""
    resp = auth_client.get("/api/v1/workbench/aggregate")
    meta = resp.json()["data"]["meta"]
    assert meta["cache_hit"] is False
    assert "elapsed_ms" in meta


# ---------------------------------------------------------------------------
# A-10 plugins — 查询 module_plugin（mock DB 返回空列表）
# ---------------------------------------------------------------------------


def test_plugins_returns_empty_list(auth_client: object, mock_db: AsyncMock) -> None:
    """A-10 plugins 返回空列表（module_plugin 表无数据时）。"""
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    mock_db.execute = AsyncMock(return_value=result)

    resp = auth_client.get("/api/v1/workbench/plugins")
    assert resp.status_code == 200
    assert resp.json()["data"]["plugins"] == []


# ---------------------------------------------------------------------------
# A-12 events/read — 批量标记已读
# ---------------------------------------------------------------------------


def test_events_read_returns_marked_count(auth_client: object, mock_db: AsyncMock) -> None:
    """A-12 events/read 返回标记已读的行数。"""
    result = MagicMock()
    result.rowcount = 3
    mock_db.execute = AsyncMock(return_value=result)

    resp = auth_client.post("/api/v1/workbench/events/read", json={"event_ids": [1, 2, 3]})
    assert resp.status_code == 200
    assert resp.json()["data"]["marked"] == 3


def test_events_read_empty_ids_returns_zero(auth_client: object, mock_db: AsyncMock) -> None:
    """A-12 events/read 空列表返回 marked=0（不调 execute）。"""
    resp = auth_client.post("/api/v1/workbench/events/read", json={"event_ids": []})
    assert resp.status_code == 200
    assert resp.json()["data"]["marked"] == 0
    mock_db.execute.assert_not_awaited()


# ---------------------------------------------------------------------------
# A-09 lane-more — 参数校验
# ---------------------------------------------------------------------------


def test_lane_more_missing_lane_returns_422(auth_client: object) -> None:
    """A-09 lane-more 缺少必填参数 lane → 422。"""
    resp = auth_client.get("/api/v1/workbench/lane-more")
    assert resp.status_code == 422


def test_lane_more_with_param_returns_200(auth_client: object) -> None:
    """A-09 lane-more 带 lane 参数返回 200 + 分页结构。"""
    resp = auth_client.get("/api/v1/workbench/lane-more?lane=PENDING&offset=0&limit=10")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["lane"] == "PENDING"
    assert data["offset"] == 0
    assert data["limit"] == 10
    assert data["has_more"] is False
