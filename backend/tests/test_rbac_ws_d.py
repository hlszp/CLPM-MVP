"""WS-D 性能#7 R1：SPONSOR 门控 + PE_ENGINEER 回路配置入口放开测试。

覆盖：
- SPONSOR 只读工作台，禁止下钻回路监控详情 / 诊断详情 / 诊断可视化 / 诊断推荐 / 波形数据
- PE_ENGINEER 放开回路配置入口（create/edit/export/tags），
  仍禁止 delete（ADMIN）/import（IC_ENGINEER）

设计依据：实现契约 v2.0 §权限矩阵；前端 v-permission 与后端 require_roles 双重防御。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.conftest import TEST_USERS, mock_current_user

# 测试用 loop_id（合法 UUID 字符串）
_LOOP_ID = "00000000-0000-0000-0000-000000000201"


def _make_scalar_one_or_none_mock(value) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _make_scalars_mock(items: list) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    return result


# ===========================================================================
# WS-D 性能#7 R1：SPONSOR 门控（5 个下钻端点均 403）
# ===========================================================================


class TestSponsorDrillDownForbidden:
    """SPONSOR 只读工作台，禁止下钻诊断/监控详情。

    require_roles 在端点体之前执行，DB 未被触达即可返回 403。
    """

    def test_sponsor_get_loop_monitor_detail_forbidden(self, client, mock_db, fake_redis) -> None:
        """SPONSOR 不能访问 GET /loops/{id}/monitor（回路监控详情）。"""
        with mock_current_user(TEST_USERS["sponsor"]):
            resp = client.get(
                f"/api/v1/loops/{_LOOP_ID}/monitor",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 403
        assert resp.json()["code"] == "ERR_PERMISSION_DENIED"

    def test_sponsor_get_diagnosis_detail_forbidden(self, client, mock_db, fake_redis) -> None:
        """SPONSOR 不能访问 GET /diagnosis/{loopId}（诊断详情）。"""
        with mock_current_user(TEST_USERS["sponsor"]):
            resp = client.get(
                f"/api/v1/diagnosis/{_LOOP_ID}",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 403
        assert resp.json()["code"] == "ERR_PERMISSION_DENIED"

    def test_sponsor_get_diagnosis_visualization_forbidden(
        self, client, mock_db, fake_redis
    ) -> None:
        """SPONSOR 不能访问 GET /diagnosis/{loopId}/visualization（诊断可视化）。"""
        with mock_current_user(TEST_USERS["sponsor"]):
            resp = client.get(
                f"/api/v1/diagnosis/{_LOOP_ID}/visualization",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 403
        assert resp.json()["code"] == "ERR_PERMISSION_DENIED"

    def test_sponsor_get_diagnosis_recommendations_forbidden(
        self, client, mock_db, fake_redis
    ) -> None:
        """SPONSOR 不能访问 GET /diagnosis/{loopId}/recommendations（诊断推荐）。"""
        with mock_current_user(TEST_USERS["sponsor"]):
            resp = client.get(
                f"/api/v1/diagnosis/{_LOOP_ID}/recommendations",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 403
        assert resp.json()["code"] == "ERR_PERMISSION_DENIED"

    def test_sponsor_get_waveform_forbidden(self, client, mock_db, fake_redis) -> None:
        """SPONSOR 不能访问 GET /timeseries/{loopId}/waveform（波形数据）。"""
        with mock_current_user(TEST_USERS["sponsor"]):
            resp = client.get(
                f"/api/v1/timeseries/{_LOOP_ID}/waveform",
                headers={"Authorization": "Bearer fake-token"},
                params={
                    "startTime": "2026-07-20T00:00:00Z",
                    "endTime": "2026-07-21T00:00:00Z",
                },
            )
        assert resp.status_code == 403
        assert resp.json()["code"] == "ERR_PERMISSION_DENIED"


# ===========================================================================
# WS-D 性能#7 R1：PE_ENGINEER 回路配置入口放开（create/edit/export/tags 200）
# ===========================================================================


class TestPeEngineerLoopConfigAllowed:
    """PE_ENGINEER 放开回路配置入口（对齐后端 require_roles）。

    覆盖：create / update / update_tags / export 四个端点通过权限门禁（进入业务逻辑）。
    """

    def test_pe_engineer_create_loop_allowed(self, client, mock_db, fake_redis) -> None:
        """PE_ENGINEER 可以创建回路（通过 require_roles 门禁，进入业务逻辑）。"""
        # 无重复回路 → 进入 create_loop 业务逻辑（DB mock 返回 None 表示无重复）
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        with mock_current_user(TEST_USERS["pe_engineer"]):
            resp = client.post(
                "/api/v1/loops",
                headers={"Authorization": "Bearer fake-token"},
                json={"tagName": "PE-NEW-001", "isActive": True},
            )
        # 期望 201（通过权限门禁并成功创建）；非 403 即说明门禁已放开
        assert resp.status_code == 201, f"PE_ENGINEER 创建回路被拒: {resp.json()}"

    def test_pe_engineer_update_loop_allowed(self, client, mock_db, fake_redis) -> None:
        """PE_ENGINEER 可以编辑回路（通过 require_roles 门禁）。

        本用例仅验证权限门禁放开（非 403），不深究 update_loop 业务逻辑的完整成功路径
        （业务逻辑的成功路径由 test_loop.py 覆盖）。
        """
        loop = MagicMock()
        loop.id = _LOOP_ID
        loop.tag_name = "PE-EDIT-001"
        loop.description = "desc"
        loop.unit_id = None
        loop.score_weight = None
        loop.is_active = True
        loop.status = "READY"
        loop.loop_type = "TEMPERATURE"
        loop.control_type = "STABLE"
        loop.importance_level = 2
        loop.include_in_evaluation = True
        loop.modeattr_tag_id = None
        loop.data_retention_days = None
        loop.op_output_lower_limit = None
        loop.op_output_upper_limit = None
        loop.created_at = MagicMock()
        loop.created_at.isoformat.return_value = "2026-07-20T10:00:00"
        loop.updated_at = MagicMock()
        loop.updated_at.isoformat.return_value = "2026-07-20T10:00:00"
        loop.created_by = "admin"
        loop.updated_by = None
        loop.score_weights = None
        loop.remark = None
        # update_loop 先查 duplicate（排除自身），再查 existing loop
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(loop))
        with mock_current_user(TEST_USERS["pe_engineer"]):
            resp = client.put(
                f"/api/v1/loops/{_LOOP_ID}",
                headers={"Authorization": "Bearer fake-token"},
                json={"description": "PE 编辑后描述"},
            )
        # 期望非 403（通过权限门禁）；业务返回码取决于 mock，关键是门禁放开
        assert resp.status_code != 403, f"PE_ENGINEER 编辑回路被权限门禁拦截: {resp.json()}"

    def test_pe_engineer_update_loop_tags_allowed(self, client, mock_db, fake_redis) -> None:
        """PE_ENGINEER 可以更新回路 Tag 关联（通过 require_roles 门禁）。"""
        # update_loop_tags 查询 existing loop + tag_registry
        existing_loop = MagicMock()
        existing_loop.id = _LOOP_ID

        async def execute_side_effect(stmt, *args, **kwargs):
            compiled = str(stmt.compile()).lower()
            if "tag_registry" in compiled:
                # tag 查询返回空（update_loop_tags 会校验存在性，这里走 NOT FOUND）
                return _make_scalars_mock([])
            # 默认返回 existing loop
            return _make_scalar_one_or_none_mock(existing_loop)

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        with mock_current_user(TEST_USERS["pe_engineer"]):
            resp = client.put(
                f"/api/v1/loops/{_LOOP_ID}/tags",
                headers={"Authorization": "Bearer fake-token"},
                json={
                    "pv": "TAG-PV-001",
                    "sp": "TAG-SP-001",
                    "op": "TAG-OP-001",
                    "mode": "TAG-MODE-001",
                },
            )
        # 期望非 403（通过门禁）；具体业务返回码取决于 mock，关键是门禁放开
        assert resp.status_code != 403, f"PE_ENGINEER 更新 Tag 关联被权限门禁拦截: {resp.json()}"

    def test_pe_engineer_export_loops_allowed(self, client, mock_db, fake_redis) -> None:
        """PE_ENGINEER 可以导出回路列表（通过 require_roles 门禁）。"""
        mock_db.execute = AsyncMock(return_value=_make_scalars_mock([]))
        with mock_current_user(TEST_USERS["pe_engineer"]):
            resp = client.get(
                "/api/v1/loops/export",
                headers={"Authorization": "Bearer fake-token"},
            )
        # 期望非 403（通过门禁）；导出空列表仍应返回成功响应
        assert resp.status_code != 403, f"PE_ENGINEER 导出回路被权限门禁拦截: {resp.json()}"


# ===========================================================================
# WS-D 性能#7 R1：PE_ENGINEER 仍禁止 delete（ADMIN 专属）/ import（IC_ENGINEER 专属）
# ===========================================================================


class TestPeEngineerStillForbidden:
    """PE_ENGINEER 放开配置入口后，delete/import 仍受角色门禁保护。"""

    def test_pe_engineer_delete_loop_forbidden(self, client, mock_db, fake_redis) -> None:
        """PE_ENGINEER 不能删除回路（仅 ADMIN）。"""
        with mock_current_user(TEST_USERS["pe_engineer"]):
            resp = client.delete(
                f"/api/v1/loops/{_LOOP_ID}",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 403
        assert resp.json()["code"] == "ERR_PERMISSION_DENIED"

    def test_pe_engineer_import_loops_forbidden(self, client, mock_db, fake_redis) -> None:
        """PE_ENGINEER 不能导入回路（仅 IC_ENGINEER/ADMIN）。"""
        with mock_current_user(TEST_USERS["pe_engineer"]):
            # 上传空文件触发权限门禁（require_roles 先于业务逻辑）
            resp = client.post(
                "/api/v1/loops/import",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 403
        assert resp.json()["code"] == "ERR_PERMISSION_DENIED"


# ===========================================================================
# 正向对照：ADMIN / IC_ENGINEER / EXPERT 可访问下钻端点（门禁未误伤）
# ===========================================================================


class TestOtherRolesDrillDownAllowed:
    """ADMIN/IC_ENGINEER/EXPERT 不被 SPONSOR 门禁误伤（非 403）。"""

    @pytest.mark.parametrize(
        "username",
        ["admin", "ic_engineer", "expert"],
    )
    def test_non_sponsor_get_diagnosis_detail_not_forbidden(
        self, client, mock_db, fake_redis, username
    ) -> None:
        """ADMIN/IC_ENGINEER/EXPERT 访问诊断详情不被 403 拦截（门禁未误伤）。"""
        with mock_current_user(TEST_USERS[username]):
            resp = client.get(
                f"/api/v1/diagnosis/{_LOOP_ID}",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code != 403, f"{username} 访问诊断详情被误拦 403: {resp.json()}"
