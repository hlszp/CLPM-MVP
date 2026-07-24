"""DCS PID 结构模板服务测试（P5）.

覆盖 1:1 upsert 语义、404 校验、CHECK 约束联动、列表查询。
使用 AsyncMock 模拟 DB session，无外部依赖。

设计依据：app/services/dcs_config.py §DcsPidStructure
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.core.exceptions import BizError
from app.services import dcs_config as svc

# ---------------------------------------------------------------------------
# 辅助：构造 mock 对象
# ---------------------------------------------------------------------------


def _make_model(model_id: str | None = None, code: str = "hollysys-macs") -> MagicMock:
    m = MagicMock()
    m.id = model_id or str(uuid4())
    m.code = code
    m.name = "和利时 MACS"
    m.vendor_id = str(uuid4())
    return m


def _make_structure(
    model_id: str,
    *,
    p_type: str = "PROPORTION",
    i_unit: str = "SECONDS",
    d_unit: str = "SECONDS",
    d_filter_enabled: bool = False,
    d_filter_unit: str | None = None,
    d_filter_multiplier: bool = False,
    description: str | None = None,
) -> MagicMock:
    s = MagicMock()
    s.id = str(uuid4())
    s.dcs_model_id = model_id
    s.p_type = p_type
    s.i_unit = i_unit
    s.d_unit = d_unit
    s.d_filter_enabled = d_filter_enabled
    s.d_filter_unit = d_filter_unit
    s.d_filter_multiplier = d_filter_multiplier
    s.description = description
    s.created_at = datetime.now(UTC)
    s.updated_at = datetime.now(UTC)
    return s


def _make_db(model: MagicMock | None, structure: MagicMock | None = None) -> AsyncMock:
    """构造 mock AsyncSession：第一次 execute 返回 model 查询，第二次返回 structure 查询。"""
    db = AsyncMock()
    model_result = MagicMock()
    model_result.scalar_one_or_none.return_value = model
    struct_result = MagicMock()
    struct_result.scalar_one_or_none.return_value = structure
    db.execute = AsyncMock(side_effect=[model_result, struct_result])
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.add = MagicMock()
    return db


# ---------------------------------------------------------------------------
# upsert_pid_structure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_creates_new_structure():
    """型号存在 + 无既有结构 → 新建。"""
    model = _make_model()
    db = _make_db(model, structure=None)

    data = await svc.upsert_pid_structure(
        db,
        model.id,
        p_type="PROPORTION_BAND",
        i_unit="MINUTES",
        d_unit="SECONDS",
        d_filter_enabled=False,
        d_filter_unit=None,
        d_filter_multiplier=False,
        description="测试模板",
        operator="admin",
    )

    assert data["dcs_model_id"] == model.id
    assert data["p_type"] == "PROPORTION_BAND"
    assert data["i_unit"] == "MINUTES"
    assert data["model_code"] == "hollysys-macs"
    db.add.assert_called_once()
    db.commit.assert_awaited_once()
    db.refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_upsert_updates_existing_structure():
    """型号存在 + 既有结构 → 更新（不新增行）。"""
    model = _make_model()
    existing = _make_structure(model.id, p_type="PROPORTION")
    db = _make_db(model, structure=existing)

    data = await svc.upsert_pid_structure(
        db,
        model.id,
        p_type="PROPORTION_BAND",
        i_unit="MINUTES",
        d_unit="MINUTES",
        d_filter_enabled=True,
        d_filter_unit="SECONDS",
        d_filter_multiplier=True,
        description="更新后",
        operator="admin",
    )

    # 返回值反映更新后的字段
    assert data["p_type"] == "PROPORTION_BAND"
    # 更新既有对象字段
    assert existing.p_type == "PROPORTION_BAND"
    assert existing.i_unit == "MINUTES"
    assert existing.d_filter_enabled is True
    assert existing.d_filter_unit == "SECONDS"
    assert existing.d_filter_multiplier is True
    # 不应 add 新对象
    db.add.assert_not_called()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_upsert_model_not_found_raises_404():
    """型号不存在 → BizError 404。"""
    db = _make_db(model=None, structure=None)
    with pytest.raises(BizError) as exc:
        await svc.upsert_pid_structure(
            db,
            "nonexistent-id",
            p_type="PROPORTION",
            i_unit="SECONDS",
            d_unit="SECONDS",
            d_filter_enabled=False,
            d_filter_unit=None,
            d_filter_multiplier=False,
        )
    assert exc.value.status_code == 404
    assert "ERR_DCS_MODEL_NOT_FOUND" in exc.value.code


# ---------------------------------------------------------------------------
# get_pid_structure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_returns_none_when_not_configured():
    """型号存在 + 未配置结构 → 返回 None。"""
    model = _make_model()
    db = _make_db(model, structure=None)
    data = await svc.get_pid_structure(db, model.id)
    assert data is None


@pytest.mark.asyncio
async def test_get_returns_dict_when_configured():
    """型号存在 + 已配置结构 → 返回 dict 含型号信息。"""
    model = _make_model(code="supcon-ecs700")
    structure = _make_structure(
        model.id,
        p_type="PROPORTION_BAND",
        d_filter_enabled=True,
        d_filter_unit="MINUTES",
    )
    db = _make_db(model, structure=structure)
    data = await svc.get_pid_structure(db, model.id)
    assert data is not None
    assert data["p_type"] == "PROPORTION_BAND"
    assert data["d_filter_enabled"] is True
    assert data["d_filter_unit"] == "MINUTES"
    assert data["model_code"] == "supcon-ecs700"


@pytest.mark.asyncio
async def test_get_model_not_found_raises_404():
    db = _make_db(model=None)
    with pytest.raises(BizError) as exc:
        await svc.get_pid_structure(db, "missing")
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# delete_pid_structure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_removes_existing_structure():
    """型号 + 结构均存在 → 删除成功。"""
    model = _make_model()
    structure = _make_structure(model.id)
    db = _make_db(model, structure=structure)
    # delete_pid_structure 第三次 execute 是 delete() 语句
    db.execute = AsyncMock(
        side_effect=[
            MagicMock(scalar_one_or_none=MagicMock(return_value=model)),
            MagicMock(scalar_one_or_none=MagicMock(return_value=structure)),
            AsyncMock(),  # delete 语句执行
        ]
    )

    await svc.delete_pid_structure(db, model.id, operator="admin")

    assert db.execute.await_count == 3
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_structure_not_found_raises_404():
    """型号存在但未配置结构 → 404。"""
    model = _make_model()
    db = _make_db(model, structure=None)
    with pytest.raises(BizError) as exc:
        await svc.delete_pid_structure(db, model.id)
    assert exc.value.status_code == 404
    assert "ERR_DCS_PID_STRUCTURE_NOT_FOUND" in exc.value.code


# ---------------------------------------------------------------------------
# list_pid_structures
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_returns_all_with_model_info():
    """列表查询返回全部结构含型号信息。"""
    model1 = _make_model(code="a-model")
    model2 = _make_model(code="b-model")
    s1 = _make_structure(model1.id, p_type="PROPORTION")
    s2 = _make_structure(model2.id, p_type="PROPORTION_BAND")

    db = AsyncMock()
    result = MagicMock()
    result.all.return_value = [(s1, model1), (s2, model2)]
    db.execute = AsyncMock(return_value=result)

    data = await svc.list_pid_structures(db)
    assert len(data) == 2
    assert data[0]["model_code"] == "a-model"
    assert data[1]["model_code"] == "b-model"
    assert data[1]["p_type"] == "PROPORTION_BAND"
