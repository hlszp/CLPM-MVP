"""P3 #45: /api/v1/tags/match-loop 端点测试。

验证：
- 返回的 role 使用 PID_P/PID_I/PID_D（与 loop_tag_mapping.tag_role CHECK 约束一致）
- 不再返回旧版 KP/TI/TD（与 schema 不一致）
- 同时支持 `_` 和 `-` 两种分隔符
- 仅返回数据库中存在的测点

Phase 10 性能优化后：原 7 次 ``for role in roles`` 单条 IN 查询合并为
1 次 IN 查询（14 个候选 tag_name 一次性 WHERE IN）。测试 mock 跟随调整为
``db.execute`` 仅被调用 1 次，返回所有匹配 tag 列表。
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


def _make_scalars_mock(tags: list) -> MagicMock:
    """构造 .scalars().all() 返回 tags 的 mock 结果（合并 IN 查询返回多行）。"""
    result = MagicMock()
    result.scalars.return_value.all.return_value = tags
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
        # 合并查询：一次性返回所有匹配 tag
        all_tags = list(tags_by_role.values())
        mock_db.execute = AsyncMock(return_value=_make_scalars_mock(all_tags))

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
        mock_db.execute = AsyncMock(return_value=_make_scalars_mock([tag]))

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
        tags = [
            _make_tag("t1", "T-HDC-003-PV", "PV"),
            _make_tag("t2", "T-HDC-003-SP", "SP"),
            _make_tag("t3", "T-HDC-003-OP", "OP"),
            _make_tag("t4", "T-HDC-003-MODE", "MODE"),
        ]
        mock_db.execute = AsyncMock(return_value=_make_scalars_mock(tags))

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
        mock_db.execute = AsyncMock(return_value=_make_scalars_mock([]))

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/tags/match-loop?loopTagName=NONEXISTENT-LOOP",
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert body["data"] == []

    def test_query_count_is_single_in_query(self, client, mock_db, fake_redis) -> None:
        """Phase 10 性能优化：合并 IN 查询后 db.execute 仅被调用 1 次。

        防止回归：若恢复为 ``for role in roles`` 单条查询，调用次数会变 7。
        """
        mock_db.execute = AsyncMock(return_value=_make_scalars_mock([]))

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/tags/match-loop?loopTagName=ANY-LOOP",
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 200
        # 合并 IN 查询后仅 1 次 db.execute 调用
        assert mock_db.execute.await_count == 1

    def test_underscore_preferred_over_dash_when_both_exist(
        self, client, mock_db, fake_redis
    ) -> None:
        """Phase 10：同一 role 同时存在 `_` 和 `-` 分隔符时，优先返回 `_`。

        合并查询会同时拿到两个 tag，按"OPC DA 命名约定优先 `_`"返回前者。
        """
        tag_underscore = _make_tag("t1", "LIC-101_PV", "PV")
        tag_dash = _make_tag("t2", "LIC-101-PV", "PV")
        mock_db.execute = AsyncMock(return_value=_make_scalars_mock([tag_underscore, tag_dash]))

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/tags/match-loop?loopTagName=LIC-101",
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 200
        body = resp.json()
        data = body["data"]
        assert len(data) == 1
        assert data[0]["role"] == "PV"
        # 优先返回 `_` 分隔符版本
        assert data[0]["tagName"] == "LIC-101_PV"
