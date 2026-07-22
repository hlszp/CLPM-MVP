"""C5 诊断关键配置变更审批流测试.

测试覆盖：
- 变更请求创建（create_change_request）+ 校验
- 变更请求列表查询（list_change_requests）
- 审批通过（approve_change_request）— config / rule / trigger 三种目标 + 自动应用
- 审批拒绝（reject_change_request）
- "双人确认"：审批人 = 申请人 时拒绝
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.core.exceptions import BizError
from app.services import diagnosis_approval as svc

# ===========================================================================
# 辅助函数
# ===========================================================================


def _make_change(
    change_id: str | None = None,
    target_type: str = "config",
    target_id: str = "diag-cfg-001",
    change_type: str = "update",
    before_value: str | None = None,
    after_value: str | None = None,
    status: str = "PENDING",
    requested_by: str = "engineer",
) -> MagicMock:
    """构造 DiagnosisConfigChange mock。"""
    c = MagicMock()
    c.id = change_id or str(uuid4())
    c.target_type = target_type
    c.target_id = target_id
    c.change_type = change_type
    c.before_value = before_value
    c.after_value = after_value
    c.status = status
    c.requested_by = requested_by
    c.requested_at = datetime.now(UTC).replace(tzinfo=None)
    c.reviewed_by = None
    c.reviewed_at = None
    c.review_note = None
    c.effective_from = None
    return c


def _make_config(
    config_id: str = "diag-cfg-001",
    version: int = 2,
) -> MagicMock:
    """构造 DiagnosisConfig mock。"""
    c = MagicMock()
    c.id = config_id
    c.diag_code = "OSCILLATION"
    c.diag_name = "振荡诊断"
    c.algorithm_type = "statistical"
    c.calc_method = "amplitude"
    c.params = {"window": 60}
    c.threshold = {"amplitude_threshold": 0.3}
    c.is_enabled = True
    c.version = version
    c.updated_by = "admin"
    c.updated_at = datetime.now(UTC).replace(tzinfo=None)
    return c


def _make_rule(
    rule_id: str = "rule-001",
    version: int = 1,
) -> MagicMock:
    """构造 DiagnosisRule mock。"""
    r = MagicMock()
    r.id = rule_id
    r.rule_code = "R01"
    r.rule_name = "测试规则"
    r.condition_expr = "True"
    r.action_type = "REMOVE_LABEL"
    r.action_params = {}
    r.is_enabled = True
    r.priority = 10
    r.version = version
    r.updated_by = "admin"
    r.updated_at = datetime.now(UTC).replace(tzinfo=None)
    return r


# ===========================================================================
# 变更请求创建
# ===========================================================================


class TestCreateChangeRequest:
    """测试变更请求创建。"""

    @pytest.mark.asyncio
    async def test_create_success(self) -> None:
        db = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()

        data = await svc.create_change_request(
            db,
            "engineer",
            target_type="config",
            target_id="diag-cfg-001",
            change_type="update",
            before_value={"diagName": "旧名称"},
            after_value={"diagName": "新名称"},
        )
        assert data["targetType"] == "config"
        assert data["changeType"] == "update"
        assert data["status"] == "PENDING"
        assert data["requestedBy"] == "engineer"
        assert data["afterValue"]["diagName"] == "新名称"
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_invalid_target(self) -> None:
        db = AsyncMock()

        with pytest.raises(BizError) as exc_info:
            await svc.create_change_request(
                db,
                "engineer",
                target_type="invalid",
                target_id="x",
                change_type="update",
            )
        assert exc_info.value.code == "ERR_INVALID_TARGET"

    @pytest.mark.asyncio
    async def test_create_invalid_change_type(self) -> None:
        db = AsyncMock()

        with pytest.raises(BizError) as exc_info:
            await svc.create_change_request(
                db,
                "engineer",
                target_type="config",
                target_id="x",
                change_type="invalid",
            )
        assert exc_info.value.code == "ERR_INVALID_CHANGE_TYPE"


# ===========================================================================
# 变更请求列表
# ===========================================================================


class TestListChangeRequests:
    """测试变更请求列表查询。"""

    @pytest.mark.asyncio
    async def test_list_all(self) -> None:
        db = AsyncMock()
        ch = _make_change()
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [ch]
        db.execute = AsyncMock(return_value=result_mock)

        requests = await svc.list_change_requests(db)
        assert len(requests) == 1
        assert requests[0]["targetType"] == "config"

    @pytest.mark.asyncio
    async def test_list_filtered_by_status(self) -> None:
        db = AsyncMock()
        ch = _make_change(status="PENDING")
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [ch]
        db.execute = AsyncMock(return_value=result_mock)

        requests = await svc.list_change_requests(db, status="PENDING")
        assert len(requests) == 1

    @pytest.mark.asyncio
    async def test_list_filtered_by_target_type(self) -> None:
        db = AsyncMock()
        ch = _make_change(target_type="rule")
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [ch]
        db.execute = AsyncMock(return_value=result_mock)

        requests = await svc.list_change_requests(db, target_type="rule")
        assert len(requests) == 1
        assert requests[0]["targetType"] == "rule"


# ===========================================================================
# 审批通过
# ===========================================================================


class TestApproveChangeRequest:
    """测试审批通过与自动应用。"""

    @pytest.mark.asyncio
    async def test_approve_config_target(self) -> None:
        db = AsyncMock()
        after_dict = {"threshold": {"amplitude_threshold": 0.8}, "diagName": "新名称"}
        change = _make_change(
            target_type="config",
            target_id="diag-cfg-001",
            after_value=json.dumps(after_dict, default=str),
            requested_by="engineer",
        )
        config = _make_config(version=2)

        change_result = MagicMock()
        change_result.scalar_one_or_none.return_value = change
        cfg_result = MagicMock()
        cfg_result.scalar_one_or_none.return_value = config
        db.execute = AsyncMock(side_effect=[change_result, cfg_result])
        db.add = MagicMock()
        db.commit = AsyncMock()

        data = await svc.approve_change_request(db, change.id, "admin", "同意")
        assert data["status"] == "APPROVED"
        assert data["reviewedBy"] == "admin"
        assert config.threshold == {"amplitude_threshold": 0.8}
        assert config.diag_name == "新名称"
        assert config.version == 3  # 2 + 1

    @pytest.mark.asyncio
    async def test_approve_rule_target(self) -> None:
        db = AsyncMock()
        after_dict = {"conditionExpr": "has('OSCILLATION')", "isEnabled": False}
        change = _make_change(
            target_type="rule",
            target_id="rule-001",
            after_value=json.dumps(after_dict, default=str),
            requested_by="engineer",
        )
        rule = _make_rule(version=1)

        change_result = MagicMock()
        change_result.scalar_one_or_none.return_value = change
        rule_result = MagicMock()
        rule_result.scalar_one_or_none.return_value = rule
        db.execute = AsyncMock(side_effect=[change_result, rule_result])
        db.add = MagicMock()
        db.commit = AsyncMock()

        with patch("app.services.diagnosis_rule.invalidate_rule_cache", new_callable=AsyncMock):
            data = await svc.approve_change_request(db, change.id, "admin")

        assert data["status"] == "APPROVED"
        assert rule.condition_expr == "has('OSCILLATION')"
        assert rule.is_enabled is False
        assert rule.version == 2  # 1 + 1

    @pytest.mark.asyncio
    async def test_approve_trigger_target(self) -> None:
        db = AsyncMock()
        after_dict = {"scoreThreshold": 70, "concurrency": 10}
        change = _make_change(
            target_type="trigger",
            target_id="diagnosis_trigger.current",
            after_value=json.dumps(after_dict, default=str),
            requested_by="engineer",
        )
        change_result = MagicMock()
        change_result.scalar_one_or_none.return_value = change
        db.execute = AsyncMock(side_effect=[change_result])
        db.add = MagicMock()
        db.commit = AsyncMock()

        with (
            patch(
                "app.services.diagnosis_trigger_config.set_config_value",
                new_callable=AsyncMock,
            ),
            patch("app.services.diagnosis_trigger_config.apply_runtime") as mock_apply,
        ):
            data = await svc.approve_change_request(db, change.id, "admin")

        assert data["status"] == "APPROVED"
        mock_apply.assert_called_once()

    @pytest.mark.asyncio
    async def test_approve_not_found(self) -> None:
        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=result_mock)

        with pytest.raises(BizError) as exc_info:
            await svc.approve_change_request(db, "non-existent", "admin")
        assert exc_info.value.code == "ERR_CHANGE_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_approve_not_pending(self) -> None:
        db = AsyncMock()
        change = _make_change(status="APPROVED")
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = change
        db.execute = AsyncMock(return_value=result_mock)

        with pytest.raises(BizError) as exc_info:
            await svc.approve_change_request(db, change.id, "admin")
        assert exc_info.value.code == "ERR_CHANGE_NOT_PENDING"

    @pytest.mark.asyncio
    async def test_approve_self_approval_rejected(self) -> None:
        db = AsyncMock()
        change = _make_change(requested_by="admin")
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = change
        db.execute = AsyncMock(return_value=result_mock)

        with pytest.raises(BizError) as exc_info:
            await svc.approve_change_request(db, change.id, "admin")
        assert exc_info.value.code == "ERR_SELF_APPROVAL"

    @pytest.mark.asyncio
    async def test_approve_corrupt_after_value(self) -> None:
        db = AsyncMock()
        change = _make_change(after_value="{bad json", requested_by="engineer")
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = change
        db.execute = AsyncMock(return_value=result_mock)

        with pytest.raises(BizError) as exc_info:
            await svc.approve_change_request(db, change.id, "admin")
        assert exc_info.value.code == "ERR_AFTER_VALUE_PARSE"


# ===========================================================================
# 审批拒绝
# ===========================================================================


class TestRejectChangeRequest:
    """测试审批拒绝。"""

    @pytest.mark.asyncio
    async def test_reject_success(self) -> None:
        db = AsyncMock()
        change = _make_change(requested_by="engineer")
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = change
        db.execute = AsyncMock(return_value=result_mock)
        db.add = MagicMock()
        db.commit = AsyncMock()

        data = await svc.reject_change_request(db, change.id, "admin", "不同意")
        assert data["status"] == "REJECTED"
        assert data["reviewedBy"] == "admin"
        assert data["reviewNote"] == "不同意"

    @pytest.mark.asyncio
    async def test_reject_not_found(self) -> None:
        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=result_mock)

        with pytest.raises(BizError) as exc_info:
            await svc.reject_change_request(db, "non-existent", "admin")
        assert exc_info.value.code == "ERR_CHANGE_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_reject_not_pending(self) -> None:
        db = AsyncMock()
        change = _make_change(status="REJECTED")
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = change
        db.execute = AsyncMock(return_value=result_mock)

        with pytest.raises(BizError) as exc_info:
            await svc.reject_change_request(db, change.id, "admin")
        assert exc_info.value.code == "ERR_CHANGE_NOT_PENDING"

    @pytest.mark.asyncio
    async def test_reject_self_approval_rejected(self) -> None:
        db = AsyncMock()
        change = _make_change(requested_by="admin")
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = change
        db.execute = AsyncMock(return_value=result_mock)

        with pytest.raises(BizError) as exc_info:
            await svc.reject_change_request(db, change.id, "admin")
        assert exc_info.value.code == "ERR_SELF_APPROVAL"
