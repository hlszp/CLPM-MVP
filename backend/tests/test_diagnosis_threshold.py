"""C3 差异化阈值 + C4 配置版本回滚测试.

测试覆盖：
- 阈值覆盖 CRUD（list_overrides / list_templates / upsert / delete）
- C4 版本历史查询与回滚（list_config_versions / rollback_config）
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.core.exceptions import BizError
from app.services import diagnosis_threshold as svc

# ===========================================================================
# 辅助函数
# ===========================================================================


def _make_override(
    override_id: str | None = None,
    diag_code: str = "OSCILLATION",
    scope_type: str = "loop_type",
    scope_id: str = "FLOW",
    threshold: dict | None = None,
    version: int = 1,
    updated_by: str | None = "admin",
) -> MagicMock:
    """构造 DiagnosisThresholdOverride mock。"""
    o = MagicMock()
    o.id = override_id or str(uuid4())
    o.diag_code = diag_code
    o.scope_type = scope_type
    o.scope_id = scope_id
    o.threshold = threshold or {"amplitude_threshold": 0.5}
    o.version = version
    o.updated_by = updated_by
    o.updated_at = datetime.now(UTC).replace(tzinfo=None)
    return o


def _make_audit_log(
    log_id: str | None = None,
    target_id: str = "diag-cfg-001",
    before_value: str | None = None,
    after_value: str | None = None,
    operator: str = "admin",
) -> MagicMock:
    """构造 SysAuditLog mock。"""
    log = MagicMock()
    log.id = log_id or str(uuid4())
    log.target_type = "diagnosis_config"
    log.target_id = target_id
    log.operation_type = "DIAG_CONFIG_UPDATE"
    log.before_value = before_value
    log.after_value = after_value
    log.operator = operator
    log.operated_at = datetime.now(UTC).replace(tzinfo=None)
    return log


def _make_config(
    config_id: str = "diag-cfg-001",
    diag_code: str = "OSCILLATION",
    diag_name: str = "振荡诊断",
    version: int = 2,
) -> MagicMock:
    """构造 DiagnosisConfig mock。"""
    c = MagicMock()
    c.id = config_id
    c.diag_code = diag_code
    c.diag_name = diag_name
    c.algorithm_type = "statistical"
    c.calc_method = "amplitude"
    c.params = {"window": 60}
    c.threshold = {"amplitude_threshold": 0.3}
    c.is_enabled = True
    c.version = version
    c.updated_by = "admin"
    c.updated_at = datetime.now(UTC).replace(tzinfo=None)
    return c


# ===========================================================================
# C3: 阈值覆盖 CRUD
# ===========================================================================


class TestThresholdOverrideCRUD:
    """测试阈值覆盖 CRUD 服务。"""

    @pytest.mark.asyncio
    async def test_list_overrides(self) -> None:
        db = AsyncMock()
        ov = _make_override()
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [ov]
        db.execute = AsyncMock(return_value=result_mock)

        overrides = await svc.list_overrides(db)
        assert len(overrides) == 1
        assert overrides[0]["diagCode"] == "OSCILLATION"
        assert overrides[0]["scopeType"] == "loop_type"

    @pytest.mark.asyncio
    async def test_list_overrides_with_scope_filter(self) -> None:
        db = AsyncMock()
        ov = _make_override(scope_type="plant", scope_id="plant-001")
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [ov]
        db.execute = AsyncMock(return_value=result_mock)

        overrides = await svc.list_overrides(db, scope_type="plant", scope_id="plant-001")
        assert len(overrides) == 1
        assert overrides[0]["scopeType"] == "plant"

    @pytest.mark.asyncio
    async def test_list_templates(self) -> None:
        db = AsyncMock()
        ov = _make_override(scope_type="loop_type")
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [ov]
        db.execute = AsyncMock(return_value=result_mock)

        templates = await svc.list_templates(db)
        assert len(templates) == 1
        assert templates[0]["scopeType"] == "loop_type"

    @pytest.mark.asyncio
    async def test_upsert_override_create(self) -> None:
        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=result_mock)
        db.add = MagicMock()
        db.commit = AsyncMock()

        data = await svc.upsert_override(
            db,
            "admin",
            diag_code="OSCILLATION",
            scope_type="loop",
            scope_id="loop-001",
            threshold={"amplitude_threshold": 0.6},
        )
        assert data["diagCode"] == "OSCILLATION"
        assert data["scopeType"] == "loop"
        assert data["version"] == 1
        assert data["threshold"] == {"amplitude_threshold": 0.6}
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_upsert_override_update(self) -> None:
        db = AsyncMock()
        existing = _make_override(version=1, threshold={"amplitude_threshold": 0.3})
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = existing
        db.execute = AsyncMock(return_value=result_mock)
        db.add = MagicMock()
        db.commit = AsyncMock()

        data = await svc.upsert_override(
            db,
            "engineer",
            diag_code="OSCILLATION",
            scope_type="loop",
            scope_id="FLOW",
            threshold={"amplitude_threshold": 0.8},
        )
        assert data["version"] == 2
        assert existing.threshold == {"amplitude_threshold": 0.8}
        assert existing.updated_by == "engineer"

    @pytest.mark.asyncio
    async def test_upsert_override_invalid_scope(self) -> None:
        db = AsyncMock()

        with pytest.raises(BizError) as exc_info:
            await svc.upsert_override(
                db,
                "admin",
                diag_code="OSCILLATION",
                scope_type="invalid_scope",
                scope_id="x",
                threshold={},
            )
        assert exc_info.value.code == "ERR_INVALID_SCOPE"

    @pytest.mark.asyncio
    async def test_delete_override_success(self) -> None:
        db = AsyncMock()
        ov = _make_override()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = ov
        db.execute = AsyncMock(return_value=result_mock)
        db.delete = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()

        await svc.delete_override(db, ov.id, "admin")
        db.delete.assert_awaited_once_with(ov)
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_override_not_found(self) -> None:
        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=result_mock)

        with pytest.raises(BizError) as exc_info:
            await svc.delete_override(db, "non-existent", "admin")
        assert exc_info.value.code == "ERR_OVERRIDE_NOT_FOUND"


# ===========================================================================
# C4: 配置版本历史与回滚
# ===========================================================================


class TestConfigVersionHistory:
    """测试 C4 配置版本历史查询与回滚。"""

    @pytest.mark.asyncio
    async def test_list_config_versions(self) -> None:
        db = AsyncMock()
        before_dict = {
            "diagName": "旧名称",
            "version": 1,
            "threshold": {"amplitude_threshold": 0.3},
        }
        after_dict = {
            "diagName": "新名称",
            "version": 2,
            "threshold": {"amplitude_threshold": 0.5},
        }
        log1 = _make_audit_log(
            before_value=json.dumps(before_dict, default=str),
            after_value=json.dumps(after_dict, default=str),
        )
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [log1]
        db.execute = AsyncMock(return_value=result_mock)

        versions = await svc.list_config_versions(db, "diag-cfg-001")
        assert len(versions) == 1
        assert versions[0]["version"] == 2
        assert versions[0]["afterValue"]["diagName"] == "新名称"
        assert versions[0]["beforeValue"]["diagName"] == "旧名称"

    @pytest.mark.asyncio
    async def test_list_config_versions_empty(self) -> None:
        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = []
        db.execute = AsyncMock(return_value=result_mock)

        versions = await svc.list_config_versions(db, "diag-cfg-001")
        assert versions == []

    @pytest.mark.asyncio
    async def test_rollback_config_success(self) -> None:
        db = AsyncMock()
        # 审计日志
        before_dict = {
            "diagName": "旧名称",
            "algorithmType": "statistical",
            "calcMethod": "amplitude",
            "params": {"window": 60},
            "threshold": {"amplitude_threshold": 0.3},
            "isEnabled": True,
            "version": 1,
        }
        audit_log = _make_audit_log(before_value=json.dumps(before_dict, default=str))
        # 当前配置
        config = _make_config(diag_name="新名称", version=2)

        # 两次 execute：第一次查审计日志，第二次查配置
        log_result = MagicMock()
        log_result.scalar_one_or_none.return_value = audit_log
        cfg_result = MagicMock()
        cfg_result.scalar_one_or_none.return_value = config
        db.execute = AsyncMock(side_effect=[log_result, cfg_result])
        db.add = MagicMock()
        db.commit = AsyncMock()

        data = await svc.rollback_config(db, "diag-cfg-001", audit_log.id, "admin")
        assert data["diagName"] == "旧名称"
        assert config.diag_name == "旧名称"
        assert config.threshold == {"amplitude_threshold": 0.3}
        assert config.version == 3  # 2 + 1

    @pytest.mark.asyncio
    async def test_rollback_config_audit_not_found(self) -> None:
        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=result_mock)

        with pytest.raises(BizError) as exc_info:
            await svc.rollback_config(db, "diag-cfg-001", "non-existent", "admin")
        assert exc_info.value.code == "ERR_AUDIT_LOG_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_rollback_config_no_before_value(self) -> None:
        db = AsyncMock()
        audit_log = _make_audit_log(before_value=None)
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = audit_log
        db.execute = AsyncMock(return_value=result_mock)

        with pytest.raises(BizError) as exc_info:
            await svc.rollback_config(db, "diag-cfg-001", audit_log.id, "admin")
        assert exc_info.value.code == "ERR_NO_BEFORE_VALUE"

    @pytest.mark.asyncio
    async def test_rollback_config_corrupt_before_value(self) -> None:
        db = AsyncMock()
        audit_log = _make_audit_log(before_value="{bad json")
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = audit_log
        db.execute = AsyncMock(return_value=result_mock)

        with pytest.raises(BizError) as exc_info:
            await svc.rollback_config(db, "diag-cfg-001", audit_log.id, "admin")
        assert exc_info.value.code == "ERR_INVALID_BEFORE_VALUE"

    @pytest.mark.asyncio
    async def test_rollback_config_not_found(self) -> None:
        db = AsyncMock()
        before_dict = {"diagName": "旧名称", "version": 1}
        audit_log = _make_audit_log(before_value=json.dumps(before_dict, default=str))

        log_result = MagicMock()
        log_result.scalar_one_or_none.return_value = audit_log
        cfg_result = MagicMock()
        cfg_result.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(side_effect=[log_result, cfg_result])

        with pytest.raises(BizError) as exc_info:
            await svc.rollback_config(db, "diag-cfg-001", audit_log.id, "admin")
        assert exc_info.value.code == "ERR_DIAG_CONFIG_NOT_FOUND"
