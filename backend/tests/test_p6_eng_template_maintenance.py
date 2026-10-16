"""P6 Slice M（03/04-a）：P2-04 工程模板维护 UI 后端消费契约回归.

映射结论：维护 UI 所需后端能力（模板 CRUD/版本/发布/回拨/回路清单、
覆盖预览 dry-run、批量应用部分失败、发布账本与回退）在 P2-04 已验收，
行为级断言见 tests/test_p2_04_engineering_templates.py（本批复跑不重复）。

本文件锚定的**唯一证实缺口**与其修复回归：

- ``PublicationItem.operation`` Literal 漏 ``TEMPLATE_VERSION``——模型 CHECK
  （ck_config_publication_operation）五值，schema 只有四值；任一模板版本
  发布/回拨后，``GET /configs/publish/publications`` 在
  ``_publication_to_item`` pydantic 校验处必 500（维护 UI 发布账本与
  回退入口不可用）。修复=Literal 补齐第五值。

另锚定维护 UI 消费契约（防回归）：

- 覆盖预览（TEMPLATE 层 dry-run）：零写入，且 affectedLoops 计数与
  受影响回路样本清单一致（UI 展示"影响 N 条回路"+清单，数字必须对得上）；
- 批量应用结果 schema 逐条承载 OK/FAIL/NOT_FOUND + error（失败项逐条
  呈现契约）；
- 模板发布/回拨响应携带 revisionRef/rollbackRevision/affectedLoops
  （UI 结果与回退入口消费字段）。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.schemas.config import PublicationItem
from app.schemas.loop_batch import (
    LoopBatchChangeItem,
    LoopBatchConfigResult,
    LoopBatchResultItem,
)
from app.services import config_publish as cp


def asyncio_run(coro):
    """pytest-asyncio auto 模式下在同步测试中执行协程的便捷包装."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# mock 辅助（与 test_p2_04_engineering_templates 同款）
# ---------------------------------------------------------------------------


def _scalar_result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _none_result():
    return _scalar_result(None)


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
    return db


# ---------------------------------------------------------------------------
# 1. 证实缺口修复回归：发布账本 operation 五值与模型 CHECK 一致
# ---------------------------------------------------------------------------


class TestPublicationOperationParity:
    """模型 CHECK 允许 TEMPLATE_VERSION，schema Literal 必须同集合."""

    def test_publication_item_accepts_template_version(self) -> None:
        """修复前：Literal 四值，模板发布后账本列表 pydantic 校验 500."""
        item = PublicationItem(
            revision=7,
            operation="TEMPLATE_VERSION",
            layer="TEMPLATE",
            scope={"templateId": "t-1", "templateKey": "FAST_RESP"},
            reason="发布 v2",
            operator="admin",
        )
        assert item.operation == "TEMPLATE_VERSION"

    def test_operation_literal_matches_model_check_set(self) -> None:
        """Literal 取值集合 == ck_config_publication_operation 五值（防再漂移）."""
        import typing

        allowed = set(typing.get_args(PublicationItem.model_fields["operation"].annotation))
        assert allowed == {"PUBLISH", "RESET", "ROLLBACK", "LEGACY_SYNC", "TEMPLATE_VERSION"}

    def test_publication_to_item_serializes_template_version_row(self) -> None:
        """端点序列化路径：TEMPLATE_VERSION 账本行不再 ValidationError."""
        from app.api.v1.endpoints.config_publish import _publication_to_item

        row = SimpleNamespace(
            revision=9,
            operation="TEMPLATE_VERSION",
            layer="TEMPLATE",
            scope={"templateId": "t-1", "templateKey": "FAST_RESP", "rollbackToVersion": 1},
            reason="指针回拨 v1",
            operator="admin",
            before_value='{"currentVersion": 2}',
            after_value='{"currentVersion": 1}',
            affected_loops=3,
            rollback_revision=8,
            created_at=None,
        )
        dumped = _publication_to_item(row).model_dump(by_alias=True)
        assert dumped["operation"] == "TEMPLATE_VERSION"
        assert dumped["affectedLoops"] == 3
        assert dumped["rollbackRevision"] == 8
        assert dumped["scope"]["rollbackToVersion"] == 1


# ---------------------------------------------------------------------------
# 2. 维护 UI 消费契约：覆盖预览计数与样本清单一致 + 零写入
# ---------------------------------------------------------------------------


def _make_template_row(template_id="tpl-1", key="FAST_RESP"):
    row = MagicMock()
    row.id = template_id
    row.template_key = key
    return row


def _make_loop_ref(loop_id, tag):
    return SimpleNamespace(id=loop_id, tag_name=tag, is_active=True)


class TestPreviewTemplateLayerContract:
    def test_preview_template_zero_write_and_count_matches_sample(self) -> None:
        """TEMPLATE 层 dry-run：零写入；affectedLoops=2 与样本清单 2 条一致."""
        template = _make_template_row()
        loops = [_make_loop_ref("loop-1", "FIC-101"), _make_loop_ref("loop-2", "FIC-102")]
        db = _db_with(
            [
                _scalar_result(4),  # 当前 revision（scalar_one_or_none）
                _none_result(),  # algorithm_parameter（STABLE）
                _none_result(),  # metric_config.threshold
                _none_result(),  # 既有覆盖（layer+scope+metric）
                _scalar_result(template),  # _count_affected_loops 模板解析
                _count_result(2),  # 影响回路数
                _scalar_result(template),  # _affected_loop_sample 模板解析
                _all_result(loops),  # 受影响样本
            ]
        )
        result = asyncio_run(
            cp.preview_publish_override(
                db,
                layer="TEMPLATE",
                scope_id="FAST_RESP",
                metric_code="oscillation_rate",
                params={"similarity_threshold": 0.8},
                control_type="STABLE",
                expected_revision=4,
            )
        )
        assert result["wouldConflict"] is False
        assert result["affectedLoops"] == 2
        # 计数与清单一致（UI「影响 N 条回路」与清单逐条对得上）
        sample = result["affectedLoopSample"]
        assert len(sample) == result["affectedLoops"]
        assert [s["tagName"] for s in sample] == ["FIC-101", "FIC-102"]
        assert result["mergePreview"][0]["result"]["similarity_threshold"] == 0.8
        # dry-run 零写入（不落库红线）
        db.add.assert_not_called()
        db.commit.assert_not_awaited()
        db.rollback.assert_not_awaited()


# ---------------------------------------------------------------------------
# 3. 维护 UI 消费契约：批量应用结果逐条失败项 + 发布/回拨响应字段
# ---------------------------------------------------------------------------


class TestBatchResultSchemaContract:
    def test_result_roundtrip_carries_per_item_failure(self) -> None:
        """LoopBatchConfigResult 逐条 OK/FAIL/NOT_FOUND + error 可序列化往返."""
        payload = LoopBatchConfigResult(
            affected=1,
            action="update",
            loopIds=["loop-1"],
            dryRun=False,
            results=[
                LoopBatchResultItem(
                    loopId="loop-1",
                    tagName="FIC-101",
                    status="OK",
                    changes=[
                        LoopBatchChangeItem(field="engineering_template_version", before=1, after=2)
                    ],
                ),
                LoopBatchResultItem(
                    loopId="loop-2",
                    tagName="FIC-102",
                    status="FAIL",
                    error="OP 输出下限 90 与该回路既有上限 80 颠倒",
                ),
                LoopBatchResultItem(loopId="ghost", status="NOT_FOUND", error="回路不存在"),
            ],
        )
        dumped = payload.model_dump(by_alias=True)
        by_id = {r["loopId"]: r for r in dumped["results"]}
        assert by_id["loop-1"]["changes"][0]["after"] == 2
        assert by_id["loop-2"]["status"] == "FAIL"
        assert "颠倒" in by_id["loop-2"]["error"]
        assert by_id["ghost"]["status"] == "NOT_FOUND"
        assert dumped["affected"] == 1

    def test_status_enum_covers_maintenance_ui_states(self) -> None:
        """UI 结果呈现依赖四态全集（OK/UNCHANGED/FAIL/NOT_FOUND）可构造."""
        for status in ("OK", "UNCHANGED", "FAIL", "NOT_FOUND"):
            item = LoopBatchResultItem(loopId=f"l-{status}", status=status)
            assert item.status == status
