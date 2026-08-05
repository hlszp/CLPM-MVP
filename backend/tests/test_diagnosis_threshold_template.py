"""P3-02 诊断阈值模板化与自适应测试.

测试覆盖：
- recommend_for_loop：按回路推荐阈值模板（四级合并视图 + scopeChain）
- apply_template_to_loop：一键套用模板到回路/装置（upsert 语义）
- ic_engineer 权限边界：仅可操作 loop scope（upsert/delete/apply）
- ADMIN 权限：全 scope 操作
- 错误场景：回路不存在 / 无匹配模板 / 装置级套用无装置
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.core.exceptions import BizError
from app.services import diagnosis_threshold as svc

# ===========================================================================
# 辅助函数
# ===========================================================================


def _make_loop(
    loop_id: str | None = None,
    tag_name: str = "FIC-101",
    loop_type: str = "FLOW",
    unit_id: str | None = "plant-001",
) -> MagicMock:
    """构造 LoopLedger mock。"""
    loop = MagicMock()
    loop.id = loop_id or str(uuid4())
    loop.tag_name = tag_name
    loop.loop_type = loop_type
    loop.unit_id = unit_id
    return loop


def _make_plant(plant_id: str = "plant-001", name: str = "一装置") -> MagicMock:
    """构造 PlantNode mock。"""
    plant = MagicMock()
    plant.id = plant_id
    plant.name = name
    return plant


_CONFIG_DEFAULT_THRESHOLD: dict = {"similarity_threshold": 0.4, "min_zero_crossings": 4}
_SENTINEL = object()


def _make_config(
    diag_code: str = "OSCILLATION",
    diag_name: str = "振荡诊断",
    threshold: object = _SENTINEL,
) -> MagicMock:
    """构造 DiagnosisConfig mock。

    threshold 用 sentinel 区分"未传参（用默认 dict）"与"显式传 None（MANUAL_REVIEW 无阈值）"。
    """
    c = MagicMock()
    c.diag_code = diag_code
    c.diag_name = diag_name
    c.threshold = _CONFIG_DEFAULT_THRESHOLD if threshold is _SENTINEL else threshold
    return c


def _make_override(
    override_id: str | None = None,
    diag_code: str = "OSCILLATION",
    scope_type: str = "loop_type",
    scope_id: str = "FLOW",
    threshold: dict | None = None,
    version: int = 1,
    updated_by: str = "system",
) -> MagicMock:
    """构造 DiagnosisThresholdOverride mock。"""
    o = MagicMock()
    o.id = override_id or str(uuid4())
    o.diag_code = diag_code
    o.scope_type = scope_type
    o.scope_id = scope_id
    o.threshold = threshold or {"similarity_threshold": 0.35, "min_zero_crossings": 5}
    o.version = version
    o.updated_by = updated_by
    o.updated_at = datetime.now(UTC).replace(tzinfo=None)
    return o


# ===========================================================================
# recommend_for_loop：按回路推荐阈值模板
# ===========================================================================


class TestRecommendForLoop:
    """测试 recommend_for_loop 服务。"""

    @pytest.mark.asyncio
    async def test_returns_merged_view_with_loop_type_template(self) -> None:
        """有 loop_type 模板时，返回四级合并视图 + scopeChain。"""
        loop = _make_loop(loop_type="FLOW", unit_id="plant-001")
        plant = _make_plant()
        config = _make_config(diag_code="OSCILLATION", threshold={"similarity_threshold": 0.4})
        template = _make_override(
            diag_code="OSCILLATION",
            scope_type="loop_type",
            scope_id="FLOW",
            threshold={"similarity_threshold": 0.35, "min_zero_crossings": 5},
        )

        db = AsyncMock()
        # 4 次 execute：loop / plant / config / overrides
        loop_res = MagicMock()
        loop_res.scalar_one_or_none.return_value = loop
        plant_res = MagicMock()
        plant_res.scalar_one_or_none.return_value = plant
        cfg_res = MagicMock()
        cfg_res.scalars.return_value.all.return_value = [config]
        ov_res = MagicMock()
        ov_res.scalars.return_value.all.return_value = [template]
        db.execute = AsyncMock(side_effect=[loop_res, plant_res, cfg_res, ov_res])

        data = await svc.recommend_for_loop(db, loop_id=loop.id)

        assert data["loopId"] == str(loop.id)
        assert data["loopType"] == "FLOW"
        assert data["plantName"] == "一装置"
        assert len(data["recommendations"]) == 1

        rec = data["recommendations"][0]
        assert rec["diagCode"] == "OSCILLATION"
        assert rec["globalDefault"] == {"similarity_threshold": 0.4}
        assert rec["loopTypeTemplate"] == {"similarity_threshold": 0.35, "min_zero_crossings": 5}
        assert rec["plantOverride"] is None
        assert rec["loopOverride"] is None
        # 生效阈值 = 全局默认 + loop_type 模板合并
        assert rec["effectiveThreshold"] == {"similarity_threshold": 0.35, "min_zero_crossings": 5}
        # scopeChain 含全局默认 + loop_type 模板，最后一层 isApplied=True
        assert len(rec["scopeChain"]) == 2
        assert rec["scopeChain"][-1]["isApplied"] is True
        assert rec["scopeChain"][-1]["source"] == "loop_type_template"

    @pytest.mark.asyncio
    async def test_no_template_falls_back_to_global_default(self) -> None:
        """无 loop_type 模板时，生效阈值回退全局默认。"""
        loop = _make_loop(loop_type="OTHER", unit_id=None)
        config = _make_config(diag_code="OSCILLATION", threshold={"similarity_threshold": 0.4})

        db = AsyncMock()
        loop_res = MagicMock()
        loop_res.scalar_one_or_none.return_value = loop
        cfg_res = MagicMock()
        cfg_res.scalars.return_value.all.return_value = [config]
        ov_res = MagicMock()
        ov_res.scalars.return_value.all.return_value = []
        # unit_id=None → 不查 plant，共 3 次 execute
        db.execute = AsyncMock(side_effect=[loop_res, cfg_res, ov_res])

        data = await svc.recommend_for_loop(db, loop_id=loop.id)

        rec = data["recommendations"][0]
        assert rec["loopTypeTemplate"] is None
        assert rec["effectiveThreshold"] == {"similarity_threshold": 0.4}
        assert len(rec["scopeChain"]) == 1
        assert rec["scopeChain"][0]["source"] == "global_default"
        assert rec["scopeChain"][0]["isApplied"] is True

    @pytest.mark.asyncio
    async def test_loop_not_found_raises(self) -> None:
        """回路不存在时抛 ERR_LOOP_NOT_FOUND。"""
        db = AsyncMock()
        loop_res = MagicMock()
        loop_res.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=loop_res)

        with pytest.raises(BizError) as exc_info:
            await svc.recommend_for_loop(db, loop_id="non-existent")
        assert exc_info.value.code == "ERR_LOOP_NOT_FOUND"
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_full_four_level_merge(self) -> None:
        """四级覆盖全存在时，生效阈值按优先级合并（loop > plant > loop_type > 全局）。"""
        loop = _make_loop(loop_type="FLOW", unit_id="plant-001")
        plant = _make_plant()
        config = _make_config(
            diag_code="OSCILLATION",
            threshold={"similarity_threshold": 0.4, "min_zero_crossings": 4},
        )
        template = _make_override(
            scope_type="loop_type",
            threshold={"similarity_threshold": 0.35, "min_zero_crossings": 5},
        )
        plant_ov = _make_override(
            scope_type="plant",
            scope_id="plant-001",
            threshold={"similarity_threshold": 0.38},
        )
        loop_ov = _make_override(
            scope_type="loop",
            scope_id=str(loop.id),
            threshold={"min_zero_crossings": 6},
        )

        db = AsyncMock()
        loop_res = MagicMock()
        loop_res.scalar_one_or_none.return_value = loop
        plant_res = MagicMock()
        plant_res.scalar_one_or_none.return_value = plant
        cfg_res = MagicMock()
        cfg_res.scalars.return_value.all.return_value = [config]
        ov_res = MagicMock()
        ov_res.scalars.return_value.all.return_value = [template, plant_ov, loop_ov]
        db.execute = AsyncMock(side_effect=[loop_res, plant_res, cfg_res, ov_res])

        data = await svc.recommend_for_loop(db, loop_id=loop.id)
        rec = data["recommendations"][0]

        # loop 覆盖最高优先级：min_zero_crossings=6 来自 loop，similarity=0.38 来自 plant
        assert rec["effectiveThreshold"]["min_zero_crossings"] == 6
        assert rec["effectiveThreshold"]["similarity_threshold"] == 0.38
        # scopeChain 4 层，最后一层（loop）isApplied=True
        assert len(rec["scopeChain"]) == 4
        assert rec["scopeChain"][-1]["source"] == "loop_override"
        assert rec["scopeChain"][-1]["isApplied"] is True

    @pytest.mark.asyncio
    async def test_skip_diag_code_without_threshold(self) -> None:
        """无阈值的 diag_code（如 MANUAL_REVIEW）不进入推荐列表。"""
        loop = _make_loop(loop_type="FLOW", unit_id=None)
        cfg_with = _make_config(diag_code="OSCILLATION", threshold={"similarity_threshold": 0.4})
        cfg_without = _make_config(diag_code="MANUAL_REVIEW", threshold=None)

        db = AsyncMock()
        loop_res = MagicMock()
        loop_res.scalar_one_or_none.return_value = loop
        cfg_res = MagicMock()
        cfg_res.scalars.return_value.all.return_value = [cfg_with, cfg_without]
        ov_res = MagicMock()
        ov_res.scalars.return_value.all.return_value = []
        db.execute = AsyncMock(side_effect=[loop_res, cfg_res, ov_res])

        data = await svc.recommend_for_loop(db, loop_id=loop.id)
        codes = [r["diagCode"] for r in data["recommendations"]]
        assert "OSCILLATION" in codes
        assert "MANUAL_REVIEW" not in codes


# ===========================================================================
# apply_template_to_loop：一键套用模板
# ===========================================================================


class TestApplyTemplateToLoop:
    """测试 apply_template_to_loop 服务。"""

    @pytest.mark.asyncio
    async def test_creates_loop_override(self) -> None:
        """套用模板到 loop scope 创建回路级覆盖。"""
        loop = _make_loop(loop_type="FLOW", unit_id="plant-001")
        template = _make_override(
            scope_type="loop_type",
            scope_id="FLOW",
            threshold={"similarity_threshold": 0.35},
        )

        db = AsyncMock()
        loop_res = MagicMock()
        loop_res.scalar_one_or_none.return_value = loop
        tpl_res = MagicMock()
        tpl_res.scalar_one_or_none.return_value = template
        db.execute = AsyncMock(side_effect=[loop_res, tpl_res])

        expected = {"overrideId": "new-id", "diagCode": "OSCILLATION", "scopeType": "loop"}
        with patch.object(
            svc, "upsert_override", new=AsyncMock(return_value=expected)
        ) as mock_upsert:
            data = await svc.apply_template_to_loop(
                db,
                "engineer",
                loop_id=loop.id,
                diag_code="OSCILLATION",
                target_scope="loop",
                operator_role="IC_ENGINEER",
            )

        assert data == expected
        mock_upsert.assert_awaited_once()
        call_kwargs = mock_upsert.call_args
        assert call_kwargs.kwargs["scope_type"] == "loop"
        assert call_kwargs.kwargs["scope_id"] == str(loop.id)
        assert call_kwargs.kwargs["threshold"] == {"similarity_threshold": 0.35}
        assert call_kwargs.kwargs["operator_role"] == "IC_ENGINEER"

    @pytest.mark.asyncio
    async def test_creates_plant_override(self) -> None:
        """ADMIN 套用模板到 plant scope 创建装置级覆盖。"""
        loop = _make_loop(loop_type="FLOW", unit_id="plant-001")
        template = _make_override(
            scope_type="loop_type",
            scope_id="FLOW",
            threshold={"similarity_threshold": 0.35},
        )

        db = AsyncMock()
        loop_res = MagicMock()
        loop_res.scalar_one_or_none.return_value = loop
        tpl_res = MagicMock()
        tpl_res.scalar_one_or_none.return_value = template
        db.execute = AsyncMock(side_effect=[loop_res, tpl_res])

        with patch.object(svc, "upsert_override", new=AsyncMock(return_value={})) as mock_upsert:
            await svc.apply_template_to_loop(
                db,
                "admin",
                loop_id=loop.id,
                diag_code="OSCILLATION",
                target_scope="plant",
                operator_role="ADMIN",
            )

        call_kwargs = mock_upsert.call_args
        assert call_kwargs.kwargs["scope_type"] == "plant"
        assert call_kwargs.kwargs["scope_id"] == "plant-001"

    @pytest.mark.asyncio
    async def test_ic_engineer_cannot_apply_to_plant(self) -> None:
        """ic_engineer 套用到 plant scope 抛 ERR_PERMISSION_DENIED。"""
        db = AsyncMock()

        with pytest.raises(BizError) as exc_info:
            await svc.apply_template_to_loop(
                db,
                "engineer",
                loop_id="loop-001",
                diag_code="OSCILLATION",
                target_scope="plant",
                operator_role="IC_ENGINEER",
            )
        assert exc_info.value.code == "ERR_PERMISSION_DENIED"
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_loop_not_found_raises(self) -> None:
        """回路不存在时抛 ERR_LOOP_NOT_FOUND。"""
        db = AsyncMock()
        loop_res = MagicMock()
        loop_res.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=loop_res)

        with pytest.raises(BizError) as exc_info:
            await svc.apply_template_to_loop(
                db,
                "admin",
                loop_id="non-existent",
                diag_code="OSCILLATION",
                target_scope="loop",
                operator_role="ADMIN",
            )
        assert exc_info.value.code == "ERR_LOOP_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_no_matching_template_raises(self) -> None:
        """回路 loop_type 无匹配模板时抛 ERR_NO_TEMPLATE。"""
        loop = _make_loop(loop_type="OTHER", unit_id=None)

        db = AsyncMock()
        loop_res = MagicMock()
        loop_res.scalar_one_or_none.return_value = loop
        tpl_res = MagicMock()
        tpl_res.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(side_effect=[loop_res, tpl_res])

        with pytest.raises(BizError) as exc_info:
            await svc.apply_template_to_loop(
                db,
                "admin",
                loop_id=loop.id,
                diag_code="OSCILLATION",
                target_scope="loop",
                operator_role="ADMIN",
            )
        assert exc_info.value.code == "ERR_NO_TEMPLATE"
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_plant_scope_without_unit_raises(self) -> None:
        """套用到 plant scope 但回路未关联装置时抛 ERR_NO_PLANT。"""
        loop = _make_loop(loop_type="FLOW", unit_id=None)
        template = _make_override(scope_type="loop_type", threshold={"similarity_threshold": 0.35})

        db = AsyncMock()
        loop_res = MagicMock()
        loop_res.scalar_one_or_none.return_value = loop
        tpl_res = MagicMock()
        tpl_res.scalar_one_or_none.return_value = template
        db.execute = AsyncMock(side_effect=[loop_res, tpl_res])

        with pytest.raises(BizError) as exc_info:
            await svc.apply_template_to_loop(
                db,
                "admin",
                loop_id=loop.id,
                diag_code="OSCILLATION",
                target_scope="plant",
                operator_role="ADMIN",
            )
        assert exc_info.value.code == "ERR_NO_PLANT"
        assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_invalid_target_scope_raises(self) -> None:
        """target_scope 不合法时抛 ERR_INVALID_SCOPE。"""
        loop = _make_loop(loop_type="FLOW", unit_id="plant-001")
        template = _make_override(scope_type="loop_type", threshold={"similarity_threshold": 0.35})

        db = AsyncMock()
        loop_res = MagicMock()
        loop_res.scalar_one_or_none.return_value = loop
        tpl_res = MagicMock()
        tpl_res.scalar_one_or_none.return_value = template
        db.execute = AsyncMock(side_effect=[loop_res, tpl_res])

        with pytest.raises(BizError) as exc_info:
            await svc.apply_template_to_loop(
                db,
                "admin",
                loop_id=loop.id,
                diag_code="OSCILLATION",
                target_scope="invalid",
                operator_role="ADMIN",
            )
        assert exc_info.value.code == "ERR_INVALID_SCOPE"


# ===========================================================================
# ic_engineer 权限边界（upsert / delete）
# ===========================================================================


class TestIcEngineerPermission:
    """测试 ic_engineer 在 upsert/delete 中的权限边界。"""

    @pytest.mark.asyncio
    async def test_ic_engineer_can_upsert_loop_scope(self) -> None:
        """ic_engineer 可创建 loop scope 覆盖。"""
        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=result_mock)
        db.add = MagicMock()
        db.commit = AsyncMock()

        data = await svc.upsert_override(
            db,
            "engineer",
            diag_code="OSCILLATION",
            scope_type="loop",
            scope_id="loop-001",
            threshold={"similarity_threshold": 0.5},
            operator_role="IC_ENGINEER",
        )
        assert data["scopeType"] == "loop"
        db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ic_engineer_cannot_upsert_loop_type_scope(self) -> None:
        """ic_engineer 不能创建 loop_type scope 覆盖（403）。"""
        db = AsyncMock()

        with pytest.raises(BizError) as exc_info:
            await svc.upsert_override(
                db,
                "engineer",
                diag_code="OSCILLATION",
                scope_type="loop_type",
                scope_id="FLOW",
                threshold={"similarity_threshold": 0.5},
                operator_role="IC_ENGINEER",
            )
        assert exc_info.value.code == "ERR_PERMISSION_DENIED"
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_ic_engineer_cannot_upsert_plant_scope(self) -> None:
        """ic_engineer 不能创建 plant scope 覆盖（403）。"""
        db = AsyncMock()

        with pytest.raises(BizError) as exc_info:
            await svc.upsert_override(
                db,
                "engineer",
                diag_code="OSCILLATION",
                scope_type="plant",
                scope_id="plant-001",
                threshold={"similarity_threshold": 0.5},
                operator_role="IC_ENGINEER",
            )
        assert exc_info.value.code == "ERR_PERMISSION_DENIED"

    @pytest.mark.asyncio
    async def test_admin_can_upsert_all_scopes(self) -> None:
        """ADMIN 可创建所有 scope 覆盖（loop_type/plant/loop）。"""
        db = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=result_mock)
        db.add = MagicMock()
        db.commit = AsyncMock()

        for scope in ("loop_type", "plant", "loop"):
            await svc.upsert_override(
                db,
                "admin",
                diag_code="OSCILLATION",
                scope_type=scope,
                scope_id=f"{scope}-001",
                threshold={"similarity_threshold": 0.5},
                operator_role="ADMIN",
            )
        assert db.commit.await_count == 3

    @pytest.mark.asyncio
    async def test_ic_engineer_can_delete_loop_scope(self) -> None:
        """ic_engineer 可删除 loop scope 覆盖。"""
        db = AsyncMock()
        ov = _make_override(scope_type="loop", scope_id="loop-001")
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = ov
        db.execute = AsyncMock(return_value=result_mock)
        db.delete = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()

        await svc.delete_override(db, ov.id, "engineer", operator_role="IC_ENGINEER")
        db.delete.assert_awaited_once_with(ov)

    @pytest.mark.asyncio
    async def test_ic_engineer_cannot_delete_loop_type_scope(self) -> None:
        """ic_engineer 不能删除 loop_type scope 覆盖（403）。"""
        db = AsyncMock()
        ov = _make_override(scope_type="loop_type", scope_id="FLOW")
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = ov
        db.execute = AsyncMock(return_value=result_mock)

        with pytest.raises(BizError) as exc_info:
            await svc.delete_override(db, ov.id, "engineer", operator_role="IC_ENGINEER")
        assert exc_info.value.code == "ERR_PERMISSION_DENIED"

    @pytest.mark.asyncio
    async def test_ic_engineer_cannot_delete_plant_scope(self) -> None:
        """ic_engineer 不能删除 plant scope 覆盖（403）。"""
        db = AsyncMock()
        ov = _make_override(scope_type="plant", scope_id="plant-001")
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = ov
        db.execute = AsyncMock(return_value=result_mock)

        with pytest.raises(BizError) as exc_info:
            await svc.delete_override(db, ov.id, "engineer", operator_role="IC_ENGINEER")
        assert exc_info.value.code == "ERR_PERMISSION_DENIED"

    @pytest.mark.asyncio
    async def test_admin_can_delete_any_scope(self) -> None:
        """ADMIN 可删除任意 scope 覆盖。"""
        db = AsyncMock()
        ov = _make_override(scope_type="loop_type", scope_id="FLOW")
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = ov
        db.execute = AsyncMock(return_value=result_mock)
        db.delete = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()

        await svc.delete_override(db, ov.id, "admin", operator_role="ADMIN")
        db.delete.assert_awaited_once_with(ov)
