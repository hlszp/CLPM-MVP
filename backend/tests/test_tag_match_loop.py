"""P3 #45: /api/v1/tags/match-loop 端点测试。

验证：
- 返回的 role 使用 PID_P/PID_I/PID_D（与 loop_tag_mapping.tag_role CHECK 约束一致）
- 不再返回旧版 KP/TI/TD（与 schema 不一致）
- 同时支持 `_` 和 `-` 分隔符的测点位号
- 仅返回数据库中存在的测点
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from tests.conftest import TEST_USERS, mock_current_user


def _make_tag(tag_id: str, tag_name: str, tag_type: str) -> MagicMock:
    """构造一个 mock TagRegistry 对象。"""
    tag = MagicMock()
    tag.id = tag_id
    tag.tag_name = tag_name
    tag.tag_description = f"{tag_name} 描述"
    tag.tag_type = tag_type
    tag.measure_type = "OTHER"
    tag.unit = "%"
    return tag


def _make_scalar_one_or_none(value) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


class TestMatchTagsForLoop:
    """GET /api/v1/tags/match-loop 端点测试。"""

    def test_returns_pid_p_pid_i_pid_d_roles(self, client, mock_db, fake_redis) -> None:
        """P3 #45：应返回 PID_P/PID_I/PID_D 角色，而非旧版 KP/TI/TD。"""
        # 构造 7 个完整 tag（使用 `-` 分隔符，对齐 seed data 命名约定）
        tags_by_role = {
            "PV": _make_tag("t1", "T-HDS-001-PV", "PV"),
            "SP": _make_tag("t2", "T-HDS-001-SP", "SP"),
            "OP": _make_tag("t3", "T-HDS-001-OP", "OP"),
            "MODE": _make_tag("t4", "T-HDS-001-MODE", "MODE"),
            "PID_P": _make_tag("t5", "T-HDS-001-PID_P", "PID_P"),
            "PID_I": _make_tag("t6", "T-HDS-001-PID_I", "PID_I"),
            "PID_D": _make_tag("t7", "T-HDS-001-PID_D", "PID_D"),
        }
        # 候选 tag_name → tag 映射（与端点构造的候选名一致）
        name_to_tag: dict[str, MagicMock] = {}
        for role, tag in tags_by_role.items():
            name_to_tag[f"T-HDS-001-{role}"] = tag
            name_to_tag[f"T-HDS-001_{role}"] = tag

        async def execute_side_effect(stmt, *args, **kwargs):
            # 使用 literal_binds 让 IN 子句渲染为字面值，便于字符串匹配
            compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
            for name, tag in name_to_tag.items():
                if name in compiled:
                    return _make_scalar_one_or_none(tag)
            return _make_scalar_one_or_none(None)

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/tags/match-loop?loopTagName=T-HDS-001",
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        data = body["data"]
        assert len(data) == 7

        roles = {item["role"] for item in data}
        # P3 #45 核心断言：必须使用 PID_P/PID_I/PID_D，不能出现 KP/TI/TD
        assert {"PV", "SP", "OP", "MODE", "PID_P", "PID_I", "PID_D"} == roles
        assert "KP" not in roles
        assert "TI" not in roles
        assert "TD" not in roles

    def test_underscore_separator_supported(self, client, mock_db, fake_redis) -> None:
        """P3 #45：同时支持 `_` 分隔符（部分 DCS 命名约定）。"""
        tag = _make_tag("t1", "80PIC31306_PV", "PV")
        name_to_tag = {
            "80PIC31306_PV": tag,
            "80PIC31306-PV": tag,
        }

        async def execute_side_effect(stmt, *args, **kwargs):
            compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
            for name, t in name_to_tag.items():
                if name in compiled:
                    return _make_scalar_one_or_none(t)
            return _make_scalar_one_or_none(None)

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/tags/match-loop?loopTagName=80PIC31306",
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        data = body["data"]
        assert len(data) == 1
        assert data[0]["role"] == "PV"
        assert data[0]["tagName"] == "80PIC31306_PV"

    def test_partial_tags_only_returns_existing(self, client, mock_db, fake_redis) -> None:
        """P3 #45：仅 PV/SP/OP/MODE 存在时（缺 PID_*），只返回 4 个。"""
        tags_by_role = {
            "PV": _make_tag("t1", "T-HDC-003-PV", "PV"),
            "SP": _make_tag("t2", "T-HDC-003-SP", "SP"),
            "OP": _make_tag("t3", "T-HDC-003-OP", "OP"),
            "MODE": _make_tag("t4", "T-HDC-003-MODE", "MODE"),
        }
        name_to_tag: dict[str, MagicMock] = {}
        for role, tag in tags_by_role.items():
            name_to_tag[f"T-HDC-003-{role}"] = tag
            name_to_tag[f"T-HDC-003_{role}"] = tag

        async def execute_side_effect(stmt, *args, **kwargs):
            compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
            for name, tag in name_to_tag.items():
                if name in compiled:
                    return _make_scalar_one_or_none(tag)
            return _make_scalar_one_or_none(None)

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/tags/match-loop?loopTagName=T-HDC-003",
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        data = body["data"]
        assert len(data) == 4
        roles = {item["role"] for item in data}
        assert roles == {"PV", "SP", "OP", "MODE"}

    def test_no_matching_tags_returns_empty(self, client, mock_db, fake_redis) -> None:
        """P3 #45：无匹配测点时返回空列表（而非错误）。"""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none(None))

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/tags/match-loop?loopTagName=NONEXISTENT-LOOP",
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert body["data"] == []

    def test_query_count_matches_role_count(self, client, mock_db, fake_redis) -> None:
        """P3 #45：查询次数 = 7（每个 role 一次），验证 role 列表完整。

        防止回归：如果硬编码列表恢复为旧版 7 项但包含 KP/TI/TD，
        查询次数仍是 7，但 role 值错误（由 test_returns_pid_p_pid_i_pid_d_roles 覆盖）。
        """
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none(None))

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/tags/match-loop?loopTagName=ANY-LOOP",
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 200
        # 7 个 role → 7 次 db.execute 调用
        assert mock_db.execute.await_count == 7
