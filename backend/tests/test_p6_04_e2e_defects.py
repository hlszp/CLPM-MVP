"""P6-04 E2E 缺陷修复回归（DEF-2 / DEF-3，2026-10-16 报告 §5）.

- DEF-2（中）：模板详情端点 loopCount 曾用 ``len(list_loops_for_template())``
  （默认 limit 50）当计数——千回路场景详情/列表口径不一致（列表=精确 count，
  详情恒 ≤50）。修复后详情与列表同用 ``count_loops_for_template``（精确）。
- DEF-3（中）：``GET /loops/{id}`` basicInfo 不序列化
  engineeringTemplateId/engineeringTemplateVersion——回路侧关联可见性缺口
  （用户看不到模板关联/pin 状态，resolve_template_reference 无出口）。
  修复后 basicInfo 携带两字段（与 LoopUpdate 返回同口径）。

mock 手法与 tests/test_p2_04_engineering_templates.py 同款（AsyncMock DB +
端点/服务直调），不依赖真实库。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.api.v1.endpoints import engineering_templates as etp
from app.schemas.loop import LoopBasicInfo
from app.services import loop as loop_service


def asyncio_run(coro):
    """pytest-asyncio auto 模式下在同步测试中执行协程的便捷包装."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# mock 辅助（与 test_p2_04 / test_p6 同款）
# ---------------------------------------------------------------------------


def _scalar_result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _count_result(count: int):
    result = MagicMock()
    result.scalar_one.return_value = count
    return result


def _all_result(items: list):
    result = MagicMock()
    result.all.return_value = items
    return result


def _db_with(side_effect):
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=side_effect)
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    db.refresh = AsyncMock()
    return db


def _make_template_row(template_id="tpl-1", key="FAST_RESP", current_version=None):
    row = MagicMock()
    row.id = template_id
    row.template_key = key
    row.name = key
    row.category = "RESPONSE"
    row.current_version = current_version
    row.description = None
    row.created_by = "admin"
    row.created_at = None
    row.updated_at = None
    return row


def _make_version_row(template_id="tpl-1", version=1):
    row = MagicMock()
    row.template_id = template_id
    row.version = version
    row.profile = {}
    row.reason = "r"
    row.published_by = "admin"
    row.published_at = None
    return row


def _make_loop_ref(loop_id, tag):
    return SimpleNamespace(id=loop_id, tag_name=tag, is_active=True)


# ---------------------------------------------------------------------------
# DEF-2：模板详情 loopCount 口径（列表/详情一致 = 精确计数）
# ---------------------------------------------------------------------------


class TestDef2TemplateDetailLoopCount:
    """详情端点 loopCount 必须用 count_loops_for_template（精确），与列表端点一致."""

    def test_detail_loop_count_is_exact_count_beyond_sample_limit(self) -> None:
        """千回路场景：样本清单 50 条（limit 默认），loopCount 必须是精确 1000.

        修复前：loopCount = len(loops) = 50（误导影响范围判断，A08 对账口径）。
        """
        template = _make_template_row(current_version=1)
        version = _make_version_row()
        loops = [_make_loop_ref(f"loop-{i}", f"FIC-{i:04d}") for i in range(50)]
        db = _db_with(
            [
                _scalar_result(template),  # get_template_by_id
                _scalar_result(version),  # get_version(current_version=1)
                _count_result(1000),  # count_loops_for_template（DEF-2 修复路径）
                _all_result(loops),  # list_loops_for_template（样本，limit 50）
            ]
        )
        resp = asyncio_run(etp.get_template_endpoint(template_id="tpl-1", db=db, _=None))
        data = resp["data"]
        assert data["loopCount"] == 1000
        assert len(data["loops"]) == 50  # 样本清单仍为 limit 截断（展示用）

    def test_detail_loop_count_matches_list_endpoint_semantics(self) -> None:
        """0 关联与 3 关联小场景不回归（计数=清单长度=精确值）."""
        template = _make_template_row(current_version=None)
        db = _db_with(
            [
                _scalar_result(template),  # get_template_by_id
                _count_result(0),  # count_loops_for_template
                _all_result([]),  # list_loops_for_template
            ]
        )
        resp = asyncio_run(etp.get_template_endpoint(template_id="tpl-1", db=db, _=None))
        assert resp["data"]["loopCount"] == 0
        assert resp["data"]["loops"] == []

    def test_count_and_list_use_distinct_queries(self) -> None:
        """修复后详情端点发起 count 查询（scalar_one）与 list 查询（all）各一次."""
        et_count = MagicMock()
        et_count.scalar_one.return_value = 7
        et_list = MagicMock()
        et_list.all.return_value = [_make_loop_ref("loop-1", "FIC-101")] * 7
        db = AsyncMock()
        db.execute = AsyncMock(
            side_effect=[_scalar_result(_make_template_row()), et_count, et_list]
        )
        asyncio_run(etp.get_template_endpoint(template_id="tpl-1", db=db, _=None))
        assert et_count.scalar_one.called
        assert et_list.all.called


# ---------------------------------------------------------------------------
# DEF-3：GET /loops/{id} basicInfo 序列化工程模板关联
# ---------------------------------------------------------------------------


def _make_loop_row(**overrides):
    row = MagicMock()
    row.id = "loop-1"
    row.tag_name = "FIC-101"
    row.description = None
    row.unit_id = None
    row.is_active = True
    row.status = "READY"
    row.loop_type = None
    row.control_type = None
    row.importance_level = None
    row.include_in_evaluation = None
    row.modeattr_tag_id = None
    row.data_retention_days = None
    row.score_weights = None
    row.remark = None
    row.op_output_lower_limit = None
    row.op_output_upper_limit = None
    row.dcs_model_id = None
    row.ideal_settling_time = None
    row.complex_loop_group_id = None
    row.complex_role = None
    row.engineering_template_id = None
    row.engineering_template_version = None
    row.created_at = None
    row.created_by = None
    row.updated_at = None
    row.updated_by = None
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


class TestDef3LoopDetailTemplateFields:
    """回路详情 basicInfo 携带 engineeringTemplateId/engineeringTemplateVersion."""

    def test_associated_loop_serializes_template_ref(self, monkeypatch) -> None:
        loop = _make_loop_row(
            engineering_template_id="tpl-1",
            engineering_template_version=2,
        )
        db = _db_with([_scalar_result(loop)])
        monkeypatch.setattr(loop_service, "_get_unit_name", AsyncMock(return_value=None))
        monkeypatch.setattr(loop_service, "_get_loop_tag_mappings", AsyncMock(return_value={}))
        monkeypatch.setattr(
            loop_service,
            "_build_raw_to_standard_maps",
            AsyncMock(return_value={}),
        )
        data = asyncio_run(loop_service.get_loop_detail(db, "loop-1"))
        assert data["basicInfo"]["engineeringTemplateId"] == "tpl-1"
        assert data["basicInfo"]["engineeringTemplateVersion"] == 2

    def test_unassociated_loop_serializes_none(self, monkeypatch) -> None:
        loop = _make_loop_row()
        db = _db_with([_scalar_result(loop)])
        monkeypatch.setattr(loop_service, "_get_unit_name", AsyncMock(return_value=None))
        monkeypatch.setattr(loop_service, "_get_loop_tag_mappings", AsyncMock(return_value={}))
        monkeypatch.setattr(
            loop_service,
            "_build_raw_to_standard_maps",
            AsyncMock(return_value={}),
        )
        data = asyncio_run(loop_service.get_loop_detail(db, "loop-1"))
        assert data["basicInfo"]["engineeringTemplateId"] is None
        assert data["basicInfo"]["engineeringTemplateVersion"] is None

    def test_loop_basic_info_schema_declares_template_fields(self) -> None:
        """响应 schema 契约防回归：两字段在 LoopBasicInfo 声明（camelCase 别名）."""
        fields = LoopBasicInfo.model_fields
        assert "engineeringTemplateId" in fields
        assert "engineeringTemplateVersion" in fields
        dumped = LoopBasicInfo(
            loopId="loop-1",
            tagName="FIC-101",
            engineeringTemplateId="tpl-1",
            engineeringTemplateVersion=2,
        ).model_dump(by_alias=True)
        assert dumped["engineeringTemplateId"] == "tpl-1"
        assert dumped["engineeringTemplateVersion"] == 2
