"""复杂回路分组测试（P4 S4）。

覆盖：
- Schema 字段存在性（LoopCreate/LoopUpdate/LoopListItem/LoopBasicInfo/LoopUpdateResult）
- 新增 schema 校验（LoopBatchGroupingRequest min/max length、mainLoopId 必填）
- Service 签名包含 complex_loop_group_id / complex_role / batch_group_loops / list_complex_groups
- _validate_complex_group 校验规则（一致性/角色/UUID/MAIN 唯一性）
- API endpoint：batch-grouping 权限校验、complex-groups 返回结构
- 创建回路时携带 complexLoopGroupId/complexRole 透传到 service
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.conftest import TEST_USERS, mock_current_user

# 测试用 UUID
GROUP_ID_1 = "11111111-1111-1111-1111-111111111111"
GROUP_ID_2 = "22222222-2222-2222-2222-222222222222"
LOOP_ID_MAIN = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
LOOP_ID_SUB1 = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
LOOP_ID_SUB2 = "cccccccc-cccc-cccc-cccc-cccccccccccc"


def _make_loop_mock(
    loop_id: str,
    tag_name: str,
    complex_loop_group_id: str | None = None,
    complex_role: str | None = None,
    is_active: bool = True,
) -> MagicMock:
    """构造一个 LoopLedger mock 对象。"""
    lp = MagicMock()
    lp.id = loop_id
    lp.tag_name = tag_name
    lp.description = f"{tag_name} 描述"
    lp.unit_id = None
    lp.is_active = is_active
    lp.status = "READY"
    lp.loop_type = "TEMPERATURE"
    lp.control_type = "STABLE"
    lp.importance_level = 2
    lp.include_in_evaluation = True
    lp.modeattr_tag_id = None
    lp.data_retention_days = None
    lp.score_weight = None
    lp.score_weights = None
    lp.remark = None
    lp.op_output_lower_limit = None
    lp.op_output_upper_limit = None
    lp.dcs_model_id = None
    lp.ideal_settling_time = None
    lp.complex_loop_group_id = complex_loop_group_id
    lp.complex_role = complex_role
    lp.created_at = MagicMock()
    lp.created_at.isoformat.return_value = "2026-07-24T10:00:00"
    lp.updated_at = MagicMock()
    lp.updated_at.isoformat.return_value = "2026-07-24T10:00:00"
    lp.created_by = "admin"
    lp.updated_by = "admin"
    return lp


def _make_scalars_mock(items: list) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    return result


def _make_scalar_one_or_none_mock(value) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


class TestComplexGroupSchemaFields:
    """Schema 字段存在性校验（确保前后端契约对齐）。"""

    def test_loop_create_has_complex_fields(self) -> None:
        from app.schemas.loop import LoopCreate

        fields = set(LoopCreate.model_fields.keys())
        assert "complexLoopGroupId" in fields
        assert "complexRole" in fields

    def test_loop_update_has_complex_fields(self) -> None:
        from app.schemas.loop import LoopUpdate

        fields = set(LoopUpdate.model_fields.keys())
        assert "complexLoopGroupId" in fields
        assert "complexRole" in fields

    def test_loop_list_item_has_complex_fields(self) -> None:
        from app.schemas.loop import LoopListItem

        fields = set(LoopListItem.model_fields.keys())
        assert "complexLoopGroupId" in fields
        assert "complexRole" in fields

    def test_loop_basic_info_has_complex_fields(self) -> None:
        from app.schemas.loop import LoopBasicInfo

        fields = set(LoopBasicInfo.model_fields.keys())
        assert "complexLoopGroupId" in fields
        assert "complexRole" in fields

    def test_loop_update_result_has_complex_fields(self) -> None:
        from app.schemas.loop import LoopUpdateResult

        fields = set(LoopUpdateResult.model_fields.keys())
        assert "complexLoopGroupId" in fields
        assert "complexRole" in fields


class TestBatchGroupingSchema:
    """LoopBatchGroupingRequest 校验。"""

    def test_min_length_2(self) -> None:
        """loopIds 少于 2 个应校验失败。"""
        from pydantic import ValidationError

        from app.schemas.loop import LoopBatchGroupingRequest

        with pytest.raises(ValidationError):
            LoopBatchGroupingRequest(loopIds=[LOOP_ID_MAIN], mainLoopId=LOOP_ID_MAIN)

    def test_max_length_20(self) -> None:
        """loopIds 超过 20 个应校验失败。"""
        from pydantic import ValidationError

        from app.schemas.loop import LoopBatchGroupingRequest

        ids = [f"00000000-0000-0000-0000-{i:012d}" for i in range(21)]
        with pytest.raises(ValidationError):
            LoopBatchGroupingRequest(loopIds=ids, mainLoopId=ids[0])

    def test_main_loop_id_required(self) -> None:
        """mainLoopId 必填。"""
        from pydantic import ValidationError

        from app.schemas.loop import LoopBatchGroupingRequest

        with pytest.raises(ValidationError):
            LoopBatchGroupingRequest(loopIds=[LOOP_ID_MAIN, LOOP_ID_SUB1])  # type: ignore[call-arg]

    def test_valid_request(self) -> None:
        """合法请求应通过。"""
        from app.schemas.loop import LoopBatchGroupingRequest

        req = LoopBatchGroupingRequest(
            loopIds=[LOOP_ID_MAIN, LOOP_ID_SUB1, LOOP_ID_SUB2],
            mainLoopId=LOOP_ID_MAIN,
        )
        assert len(req.loopIds) == 3
        assert req.mainLoopId == LOOP_ID_MAIN


class TestComplexGroupServiceSignatures:
    """Service 函数签名包含复杂回路分组参数。"""

    def test_create_loop_has_complex_params(self) -> None:
        from app.services.loop import create_loop

        sig = inspect.signature(create_loop)
        params = set(sig.parameters.keys())
        assert "complex_loop_group_id" in params
        assert "complex_role" in params

    def test_update_loop_has_complex_params(self) -> None:
        from app.services.loop import update_loop

        sig = inspect.signature(update_loop)
        params = set(sig.parameters.keys())
        assert "complex_loop_group_id" in params
        assert "complex_role" in params
        assert "_complex_group_set" in params
        assert "_complex_role_set" in params

    def test_batch_group_loops_signature(self) -> None:
        from app.services.loop import batch_group_loops

        sig = inspect.signature(batch_group_loops)
        params = set(sig.parameters.keys())
        assert "loop_ids" in params
        assert "main_loop_id" in params
        assert "operator" in params

    def test_list_complex_groups_signature(self) -> None:
        from app.services.loop import list_complex_groups

        sig = inspect.signature(list_complex_groups)
        assert "db" in sig.parameters


class TestValidateComplexGroup:
    """_validate_complex_group 校验规则（直接调用 service 层异步函数）。"""

    @pytest.mark.asyncio
    async def test_both_none_passes(self) -> None:
        """group_id 和 role 均为 None（普通单回路）应通过。"""
        from app.services.loop import _validate_complex_group

        db = AsyncMock()
        # 不应触发任何查询
        await _validate_complex_group(db, None, None)
        db.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_coherence_violation_group_only(self) -> None:
        """仅传 group_id 不传 role 应报 ERR_COMPLEX_GROUP_COHERENCE。"""
        from app.core.exceptions import BizError
        from app.services.loop import _validate_complex_group

        db = AsyncMock()
        with pytest.raises(BizError) as exc_info:
            await _validate_complex_group(db, GROUP_ID_1, None)
        assert exc_info.value.code == "ERR_COMPLEX_GROUP_COHERENCE"

    @pytest.mark.asyncio
    async def test_coherence_violation_role_only(self) -> None:
        """仅传 role 不传 group_id 应报 ERR_COMPLEX_GROUP_COHERENCE。"""
        from app.core.exceptions import BizError
        from app.services.loop import _validate_complex_group

        db = AsyncMock()
        with pytest.raises(BizError) as exc_info:
            await _validate_complex_group(db, None, "MAIN")
        assert exc_info.value.code == "ERR_COMPLEX_GROUP_COHERENCE"

    @pytest.mark.asyncio
    async def test_invalid_role(self) -> None:
        """role 非 MAIN/SUB 应报 ERR_COMPLEX_GROUP_ROLE_INVALID。"""
        from app.core.exceptions import BizError
        from app.services.loop import _validate_complex_group

        db = AsyncMock()
        with pytest.raises(BizError) as exc_info:
            await _validate_complex_group(db, GROUP_ID_1, "PRIMARY")
        assert exc_info.value.code == "ERR_COMPLEX_GROUP_ROLE_INVALID"

    @pytest.mark.asyncio
    async def test_invalid_uuid(self) -> None:
        """group_id 非合法 UUID 应报 ERR_COMPLEX_GROUP_ID_INVALID。"""
        from app.core.exceptions import BizError
        from app.services.loop import _validate_complex_group

        db = AsyncMock()
        with pytest.raises(BizError) as exc_info:
            await _validate_complex_group(db, "not-a-uuid", "SUB")
        assert exc_info.value.code == "ERR_COMPLEX_GROUP_ID_INVALID"

    @pytest.mark.asyncio
    async def test_main_uniqueness_violation(self) -> None:
        """同 group_id 已有其他 MAIN 回路应报 ERR_COMPLEX_GROUP_MAIN_EXISTS。"""
        from app.core.exceptions import BizError
        from app.services.loop import _validate_complex_group

        db = AsyncMock()
        # 模拟已存在一个 MAIN 回路（ID 不同于 exclude_loop_id）
        db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(LOOP_ID_MAIN))
        with pytest.raises(BizError) as exc_info:
            await _validate_complex_group(db, GROUP_ID_1, "MAIN", exclude_loop_id=LOOP_ID_SUB1)
        assert exc_info.value.code == "ERR_COMPLEX_GROUP_MAIN_EXISTS"

    @pytest.mark.asyncio
    async def test_main_uniqueness_self_excluded(self) -> None:
        """更新自身 MAIN 回路时 exclude_loop_id 等于自身应通过。"""
        from app.services.loop import _validate_complex_group

        db = AsyncMock()
        # 已存在的 MAIN 就是当前回路自身 → 应通过
        db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(LOOP_ID_MAIN))
        await _validate_complex_group(db, GROUP_ID_1, "MAIN", exclude_loop_id=LOOP_ID_MAIN)

    @pytest.mark.asyncio
    async def test_sub_role_no_main_check(self) -> None:
        """role=SUB 时不触发 MAIN 唯一性查询。"""
        from app.services.loop import _validate_complex_group

        db = AsyncMock()
        await _validate_complex_group(db, GROUP_ID_1, "SUB")
        # SUB 角色不查 MAIN 唯一性，execute 不应被调用
        db.execute.assert_not_called()


class TestBatchGroupLoopsService:
    """batch_group_loops service 层逻辑。"""

    @pytest.mark.asyncio
    async def test_main_not_in_list_raises(self) -> None:
        """main_loop_id 不在 loop_ids 中应报错。"""
        from app.core.exceptions import BizError
        from app.services.loop import batch_group_loops

        db = AsyncMock()
        with pytest.raises(BizError) as exc_info:
            await batch_group_loops(
                db,
                loop_ids=[LOOP_ID_SUB1, LOOP_ID_SUB2],
                main_loop_id=LOOP_ID_MAIN,
                operator="admin",
            )
        assert exc_info.value.code == "ERR_COMPLEX_GROUP_MAIN_NOT_IN_LIST"

    @pytest.mark.asyncio
    async def test_loop_not_found_raises(self) -> None:
        """部分回路不存在应报 ERR_LOOP_NOT_FOUND。"""
        from app.core.exceptions import BizError
        from app.services.loop import batch_group_loops

        db = AsyncMock()
        # 只查到 1 个，但传了 2 个 ID
        db.execute = AsyncMock(
            return_value=_make_scalars_mock([_make_loop_mock(LOOP_ID_MAIN, "TIC-101")])
        )
        with pytest.raises(BizError) as exc_info:
            await batch_group_loops(
                db,
                loop_ids=[LOOP_ID_MAIN, LOOP_ID_SUB1],
                main_loop_id=LOOP_ID_MAIN,
                operator="admin",
            )
        assert exc_info.value.code == "ERR_LOOP_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_main_already_main_raises(self) -> None:
        """所选主回路已是其他分组 MAIN 应报错。"""
        from app.core.exceptions import BizError
        from app.services.loop import batch_group_loops

        db = AsyncMock()
        main_loop = _make_loop_mock(
            LOOP_ID_MAIN, "TIC-101", complex_loop_group_id=GROUP_ID_2, complex_role="MAIN"
        )
        sub_loop = _make_loop_mock(LOOP_ID_SUB1, "TIC-102")
        db.execute = AsyncMock(return_value=_make_scalars_mock([main_loop, sub_loop]))
        with pytest.raises(BizError) as exc_info:
            await batch_group_loops(
                db,
                loop_ids=[LOOP_ID_MAIN, LOOP_ID_SUB1],
                main_loop_id=LOOP_ID_MAIN,
                operator="admin",
            )
        assert exc_info.value.code == "ERR_COMPLEX_GROUP_MAIN_EXISTS"

    @pytest.mark.asyncio
    async def test_batch_group_success(self) -> None:
        """正常批量分组：生成新 group_id，MAIN/SUB 角色分配正确。"""
        from app.services.loop import batch_group_loops

        db = AsyncMock()
        main_loop = _make_loop_mock(LOOP_ID_MAIN, "TIC-101")
        sub1_loop = _make_loop_mock(LOOP_ID_SUB1, "TIC-102")
        sub2_loop = _make_loop_mock(LOOP_ID_SUB2, "TIC-103")
        db.execute = AsyncMock(return_value=_make_scalars_mock([main_loop, sub1_loop, sub2_loop]))
        result = await batch_group_loops(
            db,
            loop_ids=[LOOP_ID_MAIN, LOOP_ID_SUB1, LOOP_ID_SUB2],
            main_loop_id=LOOP_ID_MAIN,
            operator="admin",
        )
        assert result["affected"] == 3
        assert "groupId" in result
        # MAIN 在前
        assignments = result["assignments"]
        assert assignments[0]["role"] == "MAIN"
        assert assignments[0]["loopId"] == LOOP_ID_MAIN
        assert all(a["role"] == "SUB" for a in assignments[1:])
        # 验证 loop 对象被赋值
        assert main_loop.complex_role == "MAIN"
        assert sub1_loop.complex_role == "SUB"
        assert sub2_loop.complex_role == "SUB"
        assert main_loop.complex_loop_group_id == result["groupId"]
        db.commit.assert_awaited_once()


class TestBatchGroupingEndpoint:
    """POST /api/v1/loops/batch-grouping endpoint。"""

    def test_sponsor_forbidden(self, client, mock_db, fake_redis) -> None:
        """SPONSOR 无权调用批量分组（403）。"""
        with mock_current_user(TEST_USERS["sponsor"]):
            resp = client.post(
                "/api/v1/loops/batch-grouping",
                headers={"Authorization": "Bearer fake-token"},
                json={
                    "loopIds": [LOOP_ID_MAIN, LOOP_ID_SUB1],
                    "mainLoopId": LOOP_ID_MAIN,
                },
            )
        assert resp.status_code == 403

    def test_no_token_returns_401(self, client) -> None:
        """未认证返回 401。"""
        resp = client.post(
            "/api/v1/loops/batch-grouping",
            json={
                "loopIds": [LOOP_ID_MAIN, LOOP_ID_SUB1],
                "mainLoopId": LOOP_ID_MAIN,
            },
        )
        assert resp.status_code == 401

    def test_invalid_body_min_length(self, client, mock_db, fake_redis) -> None:
        """loopIds 少于 2 个返回 422。"""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/loops/batch-grouping",
                headers={"Authorization": "Bearer fake-token"},
                json={"loopIds": [LOOP_ID_MAIN], "mainLoopId": LOOP_ID_MAIN},
            )
        assert resp.status_code == 422

    def test_batch_grouping_success(self, client, mock_db, fake_redis) -> None:
        """ADMIN 成功批量分组。"""
        main_loop = _make_loop_mock(LOOP_ID_MAIN, "TIC-101")
        sub_loop = _make_loop_mock(LOOP_ID_SUB1, "TIC-102")
        mock_db.execute = AsyncMock(return_value=_make_scalars_mock([main_loop, sub_loop]))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/loops/batch-grouping",
                headers={"Authorization": "Bearer fake-token"},
                json={
                    "loopIds": [LOOP_ID_MAIN, LOOP_ID_SUB1],
                    "mainLoopId": LOOP_ID_MAIN,
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert body["data"]["affected"] == 2
        assert len(body["data"]["assignments"]) == 2


class TestComplexGroupsEndpoint:
    """GET /api/v1/loops/complex-groups endpoint。"""

    def test_list_complex_groups_success(self, client, mock_db, fake_redis) -> None:
        """认证用户可查询分组列表。"""
        # list_complex_groups 使用 text() SQL，返回 .all() 行
        row1 = MagicMock()
        row1.__getitem__ = MagicMock(side_effect=lambda k: {0: GROUP_ID_1, 1: "TIC-101", 2: 2}[k])
        row2 = MagicMock()
        row2.__getitem__ = MagicMock(side_effect=lambda k: {0: GROUP_ID_2, 1: "TIC-201", 2: 3}[k])
        result_mock = MagicMock()
        result_mock.all.return_value = [row1, row2]
        mock_db.execute = AsyncMock(return_value=result_mock)

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/loops/complex-groups",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert len(body["data"]) == 2
        assert body["data"][0]["groupId"] == GROUP_ID_1
        assert body["data"][0]["mainTagName"] == "TIC-101"
        assert body["data"][0]["memberCount"] == 2

    def test_list_complex_groups_no_token(self, client) -> None:
        """未认证返回 401。"""
        resp = client.get("/api/v1/loops/complex-groups")
        assert resp.status_code == 401

    def test_list_complex_groups_empty(self, client, mock_db, fake_redis) -> None:
        """无分组时返回空列表。"""
        result_mock = MagicMock()
        result_mock.all.return_value = []
        mock_db.execute = AsyncMock(return_value=result_mock)
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.get(
                "/api/v1/loops/complex-groups",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        assert resp.json()["data"] == []


class TestCreateLoopWithComplexGroup:
    """创建回路时携带 complexLoopGroupId/complexRole 透传。"""

    def test_create_with_complex_fields(self, client, mock_db, fake_redis) -> None:
        """创建回路时携带分组字段应透传到 service 并返回。"""
        # create_loop 内部多次 execute：tag_name 唯一校验 → None，unit 校验不触发，
        # _validate_complex_group（SUB 不查 MAIN），最终 flush
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/loops",
                headers={"Authorization": "Bearer fake-token"},
                json={
                    "tagName": "NEW-GROUPED-LOOP",
                    "isActive": True,
                    "complexLoopGroupId": GROUP_ID_1,
                    "complexRole": "SUB",
                },
            )
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["tagName"] == "NEW-GROUPED-LOOP"
        # create_loop 返回的 complexLoopGroupId/complexRole 来自 loop 对象
        # mock_db.flush 不会真正写入，LoopLedger 构造时传入的值会被读取
        assert data["complexLoopGroupId"] == GROUP_ID_1
        assert data["complexRole"] == "SUB"

    def test_create_coherence_violation(self, client, mock_db, fake_redis) -> None:
        """仅传 complexLoopGroupId 不传 complexRole 应返回 400。"""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                "/api/v1/loops",
                headers={"Authorization": "Bearer fake-token"},
                json={
                    "tagName": "BAD-LOOP",
                    "isActive": True,
                    "complexLoopGroupId": GROUP_ID_1,
                    # 缺 complexRole
                },
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_COMPLEX_GROUP_COHERENCE"
