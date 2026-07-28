"""P2 数据正确性：tag 名入口校验 + tag 重关联治理测试。

覆盖：
- schemas/tag.py：tagName 白名单 pattern（含单引号等非法字符 → ValidationError，
  FastAPI 请求模型命中即 422；合法名不受影响）
- aas_sync.sync_tags_from_aas：非法 tag 名跳过并记录 warning，不中断整体同步
- loop.import_loops（Excel）：非法 tag 名整行跳过计入 failed/errors；
  覆盖式删建映射导致各角色 tag 名变化时返回 warnings，
  并调用 CacheInvalidator.invalidate_loop + 清除 tdengine_provider._subtable_cache
- PUT /api/v1/loops/{id}/tags：tag 变更响应带 warnings 且缓存失效被调用
"""

from __future__ import annotations

import io
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import openpyxl
import pytest
from pydantic import ValidationError

from tests.conftest import TEST_USERS, mock_current_user

# ---------------------------------------------------------------------------
# 通用 mock 构造
# ---------------------------------------------------------------------------


def _make_scalars_mock(items: list) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    return result


def _make_scalar_one_or_none_mock(value) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _make_all_mock(rows: list) -> MagicMock:
    result = MagicMock()
    result.all.return_value = rows
    return result


def _make_service_db(execute_side_effect=None) -> MagicMock:
    """service 层测试用 mock AsyncSession（add 同步、execute/commit/flush 异步）。"""
    db = MagicMock()
    db.execute = AsyncMock(side_effect=execute_side_effect)
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    # MagicMock 原生支持 async with（__aenter__/__aexit__ 自动为 AsyncMock）
    db.begin_nested = MagicMock()
    return db


def _make_xlsx(rows: list[list]) -> bytes:
    """构造内存 .xlsx（第 1 行为表头）。"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["回路编号", "描述", "SP", "PV", "OP", "MODE"])
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# ① schemas/tag.py：tagName pattern 校验
# ---------------------------------------------------------------------------


class TestTagNameSchemaValidation:
    """tagName 白名单 pattern（与 core/tdengine._TAG_NAME_PATTERN 一致）。"""

    def test_valid_tag_names_accepted(self) -> None:
        """合法 tag 名（字母/数字/下划线/连字符/点号/空白）不受影响。"""
        from app.schemas.tag import TagDetail, TagListItem

        for name in ("41FIC20021.PIDA_PV", "HDS-RX-TIC-101.PV", "LIC_101", "A B-1.pv"):
            item = TagListItem(id="t-1", tagName=name, tagType="PV")
            assert item.tagName == name
            detail = TagDetail(id="t-1", tagName=name, tagType="PV")
            assert detail.tagName == name

    def test_quote_tag_name_rejected(self) -> None:
        """含单引号的 tag 名被 schema 拦截（请求模型命中即 FastAPI 422）。"""
        from app.schemas.tag import TagListItem

        with pytest.raises(ValidationError):
            TagListItem(id="t-1", tagName="BAD';DROP TABLE--", tagType="PV")

    def test_other_special_chars_rejected(self) -> None:
        """双引号/分号/反斜杠等非法字符同样被拦截。"""
        from app.schemas.tag import TagDetail

        for name in ('A"B', "A;B", "A\\B", "A/B", "中文tag"):
            with pytest.raises(ValidationError):
                TagDetail(id="t-1", tagName=name, tagType="PV")

    def test_too_long_tag_name_rejected(self) -> None:
        """超过 128 字符的 tag 名被拦截。"""
        from app.schemas.tag import TagListItem

        with pytest.raises(ValidationError):
            TagListItem(id="t-1", tagName="A" * 129, tagType="PV")

    def test_pattern_consistent_with_tdengine_core(self) -> None:
        """schema pattern 与 core/tdengine._TAG_NAME_PATTERN 单一事实源一致。"""
        from app.core.tdengine import _TAG_NAME_PATTERN
        from app.schemas.tag import TAG_NAME_PATTERN

        assert TAG_NAME_PATTERN == _TAG_NAME_PATTERN.pattern


# ---------------------------------------------------------------------------
# ① aas_sync：非法 tag 名跳过 + warning，不中断整体同步
# ---------------------------------------------------------------------------


class TestAasSyncTagNameValidation:
    """sync_tags_from_aas 对非法 tag 名跳过并记录 warning。"""

    async def test_invalid_tag_name_skipped(self, caplog) -> None:
        from app.services.aas_sync import sync_tags_from_aas

        aas_tags = [
            {
                "tag_name": "VALID-001.PV",
                "tag_description": "合法 PV",
                "tag_type": "PV",
                "current_value": 1.0,
                "quality": "GOOD",
            },
            {
                "tag_name": "BAD';DROP TABLE--",
                "tag_description": "注入尝试",
                "tag_type": "PV",
                "current_value": 2.0,
                "quality": "GOOD",
            },
        ]
        provider = MagicMock()
        provider.read_all_tags = AsyncMock(return_value=aas_tags)

        # 通用结果：select(TagRegistry) → 空；select(SysConfig) → None（走 insert 分支）
        universal = MagicMock()
        universal.scalars.return_value.all.return_value = []
        universal.scalar_one_or_none.return_value = None
        db = _make_service_db()
        db.execute = AsyncMock(return_value=universal)

        with (
            patch("app.services.aas_sync.get_aas_provider", return_value=provider),
            caplog.at_level(logging.WARNING, logger="app.services.aas_sync"),
        ):
            stats = await sync_tags_from_aas(db)

        assert stats["total"] == 2
        assert stats["inserted"] == 1
        assert stats["skipped"] == 1
        assert stats["skipped_tags"] == ["BAD';DROP TABLE--"]
        assert any("非法 tag 名" in r.getMessage() for r in caplog.records)

    async def test_all_valid_tag_names_unaffected(self) -> None:
        """全部合法时不产生跳过（回归保护）。"""
        from app.services.aas_sync import sync_tags_from_aas

        aas_tags = [
            {"tag_name": "A-1.PV", "tag_type": "PV", "current_value": None, "quality": None},
            {"tag_name": "A-1.SP", "tag_type": "SP", "current_value": None, "quality": None},
        ]
        provider = MagicMock()
        provider.read_all_tags = AsyncMock(return_value=aas_tags)

        universal = MagicMock()
        universal.scalars.return_value.all.return_value = []
        universal.scalar_one_or_none.return_value = None
        db = _make_service_db()
        db.execute = AsyncMock(return_value=universal)

        with patch("app.services.aas_sync.get_aas_provider", return_value=provider):
            stats = await sync_tags_from_aas(db)

        assert stats["inserted"] == 2
        assert stats["skipped"] == 0
        assert stats["skipped_tags"] == []


# ---------------------------------------------------------------------------
# ① loop.import_loops：非法 tag 名整行跳过
# ---------------------------------------------------------------------------


class TestExcelImportTagNameValidation:
    """Excel 导入对非法 tag 名整行跳过并记录，不中断整体导入。"""

    async def test_invalid_tag_names_skipped_valid_row_imported(self) -> None:
        from app.services.loop import import_loops

        file_bytes = _make_xlsx(
            [
                # 回路编号含单引号 → 整行跳过
                ["BAD';DROP--", "非法回路", "", "", "", ""],
                # 合法行 → 正常新建
                ["LOOP-OK-1", "合法回路", "LOOP-OK-1.SP", "LOOP-OK-1.PV", "LOOP-OK-1.OP", ""],
                # 回路编号合法但 PV tag 含双引号 → 整行跳过
                ["LOOP-OK-2", "非法PV", "", 'BAD"PV', "", ""],
            ]
        )

        # 所有查询返回"不存在"：回路新建、Tag 自动创建
        db = _make_service_db(
            execute_side_effect=lambda *a, **kw: _make_scalar_one_or_none_mock(None)
        )

        result = await import_loops(db=db, file_bytes=file_bytes, operator="admin")

        assert result["total"] == 3
        assert result["inserted"] == 1
        assert result["failed"] == 2
        assert len(result["errors"]) == 2
        assert all("非法字符" in e["message"] for e in result["errors"])
        assert result["errors"][0]["row"] == 2
        assert result["errors"][1]["row"] == 4
        assert result["warnings"] == []


# ---------------------------------------------------------------------------
# ② tag 重关联：Excel 导入覆盖式删建映射 → warning + 缓存失效
# ---------------------------------------------------------------------------


class TestExcelImportTagReassignment:
    """Excel 导入导致各角色 tag 名变化时的历史数据孤儿化治理。"""

    async def test_tag_change_returns_warning_and_invalidates_cache(self) -> None:
        from app.models.loop import LoopLedger
        from app.services.data_source import tdengine_provider
        from app.services.loop import import_loops

        existing_loop = LoopLedger(
            id="loop-reassign-1",
            tag_name="LOOP-1",
            description="旧描述",
            is_active=True,
            status="READY",
        )
        new_pv_tag = MagicMock()
        new_pv_tag.id = "tag-new-pv"
        new_pv_tag.is_linked = False

        # execute 调用顺序：
        # 1. select(LoopLedger) → 已存在回路（更新路径）
        # 2. get_loop_role_tag_names（join 查询）→ 旧 PV tag 名
        # 3. delete(LoopTagMapping)
        # 4. select(TagRegistry) → 新 PV tag 已存在
        db = _make_service_db(
            execute_side_effect=[
                _make_scalar_one_or_none_mock(existing_loop),
                _make_all_mock([("PV", "LOOP-1.OLD_PV")]),
                MagicMock(),
                _make_scalar_one_or_none_mock(new_pv_tag),
            ]
        )

        file_bytes = _make_xlsx([["LOOP-1", "新描述", "", "LOOP-1.NEW_PV", "", ""]])

        # 预置 subtable 解析缓存，验证被清除
        tdengine_provider._subtable_cache["loop-reassign-1"] = (
            "d_loop_loop_1",
            "LOOP-1",
            999999.0,
        )
        try:
            with patch("app.services.loop.CacheInvalidator") as mock_invalidator_cls:
                mock_invalidator = MagicMock()
                mock_invalidator.invalidate_loop = AsyncMock(return_value=3)
                mock_invalidator_cls.return_value = mock_invalidator

                result = await import_loops(db=db, file_bytes=file_bytes, operator="admin")

            assert result["updated"] == 1
            assert len(result["warnings"]) == 1
            assert "旧数据不可达" in result["warnings"][0]
            assert "LOOP-1" in result["warnings"][0]
            # L1 DataBlock 缓存失效被调用
            mock_invalidator.invalidate_loop.assert_awaited_once_with("loop-reassign-1")
            # subtable 解析缓存对应条目被清除
            assert "loop-reassign-1" not in tdengine_provider._subtable_cache
        finally:
            tdengine_provider._subtable_cache.pop("loop-reassign-1", None)

    async def test_unchanged_tags_no_warning(self) -> None:
        """tag 名未变化时不产生 warning、不触发缓存失效。"""
        from app.models.loop import LoopLedger
        from app.services.loop import import_loops

        existing_loop = LoopLedger(
            id="loop-same-1",
            tag_name="LOOP-2",
            is_active=True,
            status="READY",
        )
        same_pv_tag = MagicMock()
        same_pv_tag.id = "tag-same-pv"
        same_pv_tag.is_linked = True

        db = _make_service_db(
            execute_side_effect=[
                _make_scalar_one_or_none_mock(existing_loop),
                _make_all_mock([("PV", "LOOP-2.PV")]),
                MagicMock(),
                _make_scalar_one_or_none_mock(same_pv_tag),
            ]
        )
        file_bytes = _make_xlsx([["LOOP-2", "", "", "LOOP-2.PV", "", ""]])

        with patch("app.services.loop.CacheInvalidator") as mock_invalidator_cls:
            result = await import_loops(db=db, file_bytes=file_bytes, operator="admin")

        assert result["updated"] == 1
        assert result["warnings"] == []
        mock_invalidator_cls.assert_not_called()


# ---------------------------------------------------------------------------
# ② PUT /api/v1/loops/{id}/tags：tag 变更响应带 warning 且缓存失效被调用
# ---------------------------------------------------------------------------


class TestUpdateLoopTagsReassignment:
    """PUT /loops/{id}/tags 的 tag 重关联 warning + 缓存失效。"""

    def test_tag_change_returns_warning_and_invalidates_cache(
        self, client, mock_db, fake_redis
    ) -> None:
        loop_id = "00000000-0000-0000-0000-000000000201"

        loop = MagicMock()
        loop.id = loop_id
        loop.is_active = True
        loop.updated_at = None

        new_tag = MagicMock()
        new_tag.id = "tag-new-pv"
        new_tag.tag_name = "NEW.PV"

        old_mapping = MagicMock()
        old_mapping.tag_role = "PV"
        old_mapping.tag_id = "tag-old-pv"

        # execute 调用顺序：
        # 1. get_loop_role_tag_names（变更前）→ 旧 PV tag 名
        # 2. update_loop_tags: select(LoopLedger) → 回路存在
        # 3. select(TagRegistry where id in) → 新 tag 存在
        # 4. select(LoopTagMapping) → 旧映射（审计 before）
        # 5. delete(LoopTagMapping)
        # 6. 旧 tag 引用计数 → 仍被其他回路引用（跳过 is_linked 清除）
        mock_db.execute = AsyncMock(
            side_effect=[
                _make_all_mock([("PV", "OLD.PV")]),
                _make_scalar_one_or_none_mock(loop),
                _make_scalars_mock([new_tag]),
                _make_scalars_mock([old_mapping]),
                MagicMock(),
                MagicMock(scalar=MagicMock(return_value=1)),
            ]
        )
        mock_db.scalar = AsyncMock(return_value="HDS-RX-TIC-101")

        with (
            patch("app.services.loop.CacheInvalidator") as mock_invalidator_cls,
            mock_current_user(TEST_USERS["admin"]),
        ):
            mock_invalidator = MagicMock()
            mock_invalidator.invalidate_loop = AsyncMock(return_value=2)
            mock_invalidator_cls.return_value = mock_invalidator

            resp = client.put(
                f"/api/v1/loops/{loop_id}/tags",
                headers={"Authorization": "Bearer fake-token"},
                json={"pv": "tag-new-pv"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        warnings = body["data"]["warnings"]
        assert len(warnings) == 1
        assert "旧数据不可达" in warnings[0]
        assert "HDS-RX-TIC-101" in warnings[0]
        mock_invalidator.invalidate_loop.assert_awaited_once_with(loop_id)

    def test_unchanged_tags_no_warning(self, client, mock_db, fake_redis) -> None:
        """tag 未变化时响应无 warnings、不触发缓存失效。"""
        loop_id = "00000000-0000-0000-0000-000000000201"

        loop = MagicMock()
        loop.id = loop_id
        loop.is_active = True
        loop.updated_at = None

        same_tag = MagicMock()
        same_tag.id = "tag-same-pv"
        same_tag.tag_name = "SAME.PV"

        old_mapping = MagicMock()
        old_mapping.tag_role = "PV"
        old_mapping.tag_id = "tag-same-pv"

        mock_db.execute = AsyncMock(
            side_effect=[
                _make_all_mock([("PV", "SAME.PV")]),
                _make_scalar_one_or_none_mock(loop),
                _make_scalars_mock([same_tag]),
                _make_scalars_mock([old_mapping]),
                MagicMock(),
            ]
        )

        with (
            patch("app.services.loop.CacheInvalidator") as mock_invalidator_cls,
            mock_current_user(TEST_USERS["admin"]),
        ):
            resp = client.put(
                f"/api/v1/loops/{loop_id}/tags",
                headers={"Authorization": "Bearer fake-token"},
                json={"pv": "tag-same-pv"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert body["data"]["warnings"] == []
        mock_invalidator_cls.assert_not_called()
