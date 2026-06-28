"""诊断标签接口测试 (IDS v3.2 §2.4.10-2.4.12).

测试覆盖：
- GET  /api/v1/diagnosis/tags               — 查询标签列表
- GET  /api/v1/diagnosis/tags/{loopId}      — 按回路查询
- PUT  /api/v1/diagnosis/tags/{tagId}/resolve — 处理标签（RESOLVED/SUPPRESSED）

设计依据：IDS §2.4.10-2.4.12, PRD §5.6
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.conftest import TEST_USERS, mock_current_user

# 路由冲突说明：
# main.py:116 先 include diagnosis.router（含 GET /{loop_id}），
# main.py:120 后 include diagnosis.tags_router（含 GET ""，prefix=/diagnosis/tags）。
# FastAPI 按注册顺序匹配，GET /api/v1/diagnosis/tags 被 diagnosis.router 的
# GET /{loop_id} 拦截（loop_id="tags"），tags_router 的列表端点不可达。
# 以下标记 xfail 的测试验证列表端点的预期行为，待路由顺序修复后应通过。
_ROUTE_CONFLICT_REASON = (
    "路由冲突: GET /api/v1/diagnosis/tags 被 diagnosis.router 的 "
    "GET /{loop_id} 拦截 (loop_id='tags')，tags_router 列表端点不可达"
)

# ---------------------------------------------------------------------------
# 测试数据构造
# ---------------------------------------------------------------------------


def _make_diag_tag(
    tag_id: str = "00000000-0000-0000-0000-000000000b01",
    loop_id: str = "00000000-0000-0000-0000-000000000201",
    tag_code: str = "OSCILLATION",
    tag_name: str = "振荡",
    severity: str = "WARN",
    status: str = "ACTIVE",
    source_metric: str = "oscillation_rate",
    trigger_condition: dict | None = None,
    trigger_value: Decimal | None = None,
    triggered_at: datetime | None = None,
    resolved_at: datetime | None = None,
    resolved_by: str | None = None,
    resolution_note: str | None = None,
) -> MagicMock:
    """构造 DiagnosisTag ORM mock."""
    tag = MagicMock()
    tag.id = tag_id
    tag.loop_id = loop_id
    tag.tag_code = tag_code
    tag.tag_name = tag_name
    tag.severity = severity
    tag.status = status
    tag.source_metric = source_metric
    tag.trigger_condition = trigger_condition or {"threshold": 0.4}
    tag.trigger_value = trigger_value
    tag.triggered_at = triggered_at or datetime.now(UTC)
    tag.resolved_at = resolved_at
    tag.resolved_by = resolved_by
    tag.resolution_note = resolution_note
    return tag


def _make_scalars_mock(items: list) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    return result


def _make_count_mock(count: int) -> MagicMock:
    result = MagicMock()
    result.scalar.return_value = count
    return result


def _make_scalar_one_or_none_mock(value) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


# ---------------------------------------------------------------------------
# GET /api/v1/diagnosis/tags — 查询标签列表
# ---------------------------------------------------------------------------


class TestListDiagnosisTags:
    """GET /api/v1/diagnosis/tags tests."""

    def test_list_tags_success(self, client, mock_db, fake_redis) -> None:
        """认证用户可以查询诊断标签列表."""
        tags = [
            _make_diag_tag(tag_code="OSCILLATION", severity="WARN"),
            _make_diag_tag(
                tag_id="00000000-0000-0000-0000-000000000b02",
                tag_code="VALVE_STICTION",
                severity="ERROR",
            ),
        ]
        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_count_mock(2)
            return _make_scalars_mock(tags)

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/diagnosis/tags",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        data = body["data"]
        assert data["total"] == 2
        assert len(data["items"]) == 2
        assert data["page"] == 1
        assert data["pageSize"] == 20
        item = data["items"][0]
        assert item["tagType"] == "OSCILLATION"
        assert item["severity"] == "WARN"
        assert item["status"] == "ACTIVE"

    def test_list_tags_filter_by_tag_type(
        self, client, mock_db, fake_redis
    ) -> None:
        """按标签类型筛选."""
        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_count_mock(1)
            return _make_scalars_mock([_make_diag_tag(tag_code="OSCILLATION")])

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.get(
                "/api/v1/diagnosis/tags?tagType=OSCILLATION",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 1

    def test_list_tags_filter_by_severity(
        self, client, mock_db, fake_redis
    ) -> None:
        """按严重等级筛选."""
        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_count_mock(1)
            return _make_scalars_mock([_make_diag_tag(severity="CRITICAL")])

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/diagnosis/tags?severity=CRITICAL",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 1
        assert data["items"][0]["severity"] == "CRITICAL"

    def test_list_tags_filter_by_status(
        self, client, mock_db, fake_redis
    ) -> None:
        """按处理状态筛选."""
        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_count_mock(0)
            return _make_scalars_mock([])

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/diagnosis/tags?status=RESOLVED",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 0

    def test_list_tags_invalid_tag_type(
        self, client, mock_db, fake_redis
    ) -> None:
        """无效标签类型返回 422."""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/diagnosis/tags?tagType=INVALID_TYPE",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 422
        assert resp.json()["code"] == "ERR_VALIDATION"

    def test_list_tags_pagination(
        self, client, mock_db, fake_redis
    ) -> None:
        """分页查询."""
        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_count_mock(50)
            return _make_scalars_mock([_make_diag_tag()])

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/diagnosis/tags?page=2&pageSize=10",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 50
        assert data["page"] == 2
        assert data["pageSize"] == 10

    def test_list_tags_no_token(self, client) -> None:
        """未认证请求返回 401."""
        resp = client.get("/api/v1/diagnosis/tags")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/v1/diagnosis/tags/{loopId} — 按回路查询
# ---------------------------------------------------------------------------


class TestListLoopDiagnosisTags:
    """GET /api/v1/diagnosis/tags/{loopId} tests."""

    def test_list_loop_tags_success(
        self, client, mock_db, fake_redis
    ) -> None:
        """按回路查询诊断标签."""
        loop_id = "00000000-0000-0000-0000-000000000201"
        tags = [
            _make_diag_tag(loop_id=loop_id, tag_code="OSCILLATION"),
            _make_diag_tag(
                tag_id="00000000-0000-0000-0000-000000000b02",
                loop_id=loop_id,
                tag_code="OUTPUT_SATURATION",
                severity="ERROR",
            ),
        ]
        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_count_mock(2)
            return _make_scalars_mock(tags)

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                f"/api/v1/diagnosis/tags/{loop_id}",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 2
        assert len(data["items"]) == 2
        for item in data["items"]:
            assert item["loopId"] == loop_id

    def test_list_loop_tags_empty(
        self, client, mock_db, fake_redis
    ) -> None:
        """回路无标签时返回空列表."""
        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_count_mock(0)
            return _make_scalars_mock([])

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.get(
                "/api/v1/diagnosis/tags/00000000-0000-0000-0000-000000000999",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 0
        assert data["items"] == []

    def test_list_loop_tags_with_filter(
        self, client, mock_db, fake_redis
    ) -> None:
        """回路标签支持二次筛选."""
        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_count_mock(1)
            return _make_scalars_mock(
                [_make_diag_tag(severity="ERROR", status="ACTIVE")]
            )

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/diagnosis/tags/00000000-0000-0000-0000-000000000201"
                "?severity=ERROR&status=ACTIVE",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 1

    def test_list_loop_tags_no_token(self, client) -> None:
        """未认证请求返回 401."""
        resp = client.get("/api/v1/diagnosis/tags/00000000-0000-0000-0000-000000000001")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# PUT /api/v1/diagnosis/tags/{tagId}/resolve — 处理标签
# ---------------------------------------------------------------------------


class TestResolveDiagnosisTag:
    """PUT /api/v1/diagnosis/tags/{tagId}/resolve tests."""

    def test_resolve_tag_resolved(
        self, client, mock_db, fake_redis
    ) -> None:
        """IC_ENGINEER 可以将标签标记为 RESOLVED."""
        tag = _make_diag_tag(status="ACTIVE")
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(tag))
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()

        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.put(
                f"/api/v1/diagnosis/tags/{tag.id}/resolve",
                headers={"Authorization": "Bearer fake-token"},
                json={"status": "RESOLVED", "resolutionNote": "已处理"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        data = body["data"]
        assert data["status"] == "RESOLVED"
        assert data["resolutionNote"] == "已处理"
        assert data["resolvedBy"] is not None
        # 验证 ORM 更新
        assert tag.status == "RESOLVED"
        assert tag.resolution_note == "已处理"

    def test_resolve_tag_suppressed(
        self, client, mock_db, fake_redis
    ) -> None:
        """PE_ENGINEER 可以将标签标记为 SUPPRESSED."""
        tag = _make_diag_tag(status="ACTIVE")
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(tag))
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()

        with mock_current_user(TEST_USERS["pe_engineer"]):
            resp = client.put(
                f"/api/v1/diagnosis/tags/{tag.id}/resolve",
                headers={"Authorization": "Bearer fake-token"},
                json={"status": "SUPPRESSED", "resolutionNote": "误报抑制"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] == "SUPPRESSED"
        assert tag.status == "SUPPRESSED"

    def test_resolve_tag_admin(
        self, client, mock_db, fake_redis
    ) -> None:
        """ADMIN 也可以处理标签."""
        tag = _make_diag_tag(status="ACTIVE")
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(tag))
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.put(
                f"/api/v1/diagnosis/tags/{tag.id}/resolve",
                headers={"Authorization": "Bearer fake-token"},
                json={"status": "RESOLVED"},
            )
        assert resp.status_code == 200

    def test_resolve_tag_not_found(
        self, client, mock_db, fake_redis
    ) -> None:
        """标签不存在返回 404."""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.put(
                "/api/v1/diagnosis/tags/00000000-0000-0000-0000-000000000000/resolve",
                headers={"Authorization": "Bearer fake-token"},
                json={"status": "RESOLVED"},
            )
        assert resp.status_code == 404
        assert resp.json()["code"] == "ERR_DIAG_TAG_NOT_FOUND"

    def test_resolve_tag_invalid_status(
        self, client, mock_db, fake_redis
    ) -> None:
        """无效目标状态返回 422."""
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.put(
                "/api/v1/diagnosis/tags/00000000-0000-0000-0000-000000000001/resolve",
                headers={"Authorization": "Bearer fake-token"},
                json={"status": "ACTIVE"},
            )
        assert resp.status_code == 422
        assert resp.json()["code"] == "ERR_VALIDATION"

    def test_resolve_tag_sponsor_forbidden(
        self, client, mock_db, fake_redis
    ) -> None:
        """SPONSOR 不能处理标签（403）."""
        with mock_current_user(TEST_USERS["sponsor"]):
            resp = client.put(
                "/api/v1/diagnosis/tags/00000000-0000-0000-0000-000000000001/resolve",
                headers={"Authorization": "Bearer fake-token"},
                json={"status": "RESOLVED"},
            )
        assert resp.status_code == 403
        assert resp.json()["code"] == "ERR_PERMISSION_DENIED"

    def test_resolve_tag_writes_audit_log(
        self, client, mock_db, fake_redis
    ) -> None:
        """处理标签时写入审计日志."""
        tag = _make_diag_tag(status="ACTIVE")
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(tag))
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()

        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.put(
                f"/api/v1/diagnosis/tags/{tag.id}/resolve",
                headers={"Authorization": "Bearer fake-token"},
                json={"status": "RESOLVED", "resolutionNote": "审计测试"},
            )
        assert resp.status_code == 200
        # 验证审计日志被写入
        mock_db.add.assert_called_once()
        audit_obj = mock_db.add.call_args[0][0]
        assert audit_obj.operation_type == "DIAG_TAG_RESOLVE"
        assert audit_obj.target_type == "diagnosis_tag"
        assert audit_obj.target_id == str(tag.id)
        assert audit_obj.operator == "ic_engineer"
        # 验证 commit 被调用
        mock_db.commit.assert_called_once()

    def test_resolve_tag_no_token(self, client) -> None:
        """未认证请求返回 401."""
        resp = client.put(
            "/api/v1/diagnosis/tags/00000000-0000-0000-0000-000000000001/resolve",
            json={"status": "RESOLVED"},
        )
        assert resp.status_code == 401
