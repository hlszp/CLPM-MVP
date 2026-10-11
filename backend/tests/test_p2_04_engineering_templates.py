"""P2-04 工程模板、阈值真实消费与维护界面：单元测试（mock DB + 临时目录）.

覆盖（03 §5 P2-04 任务卡 + 04 C08 剩余 + 07 号文 §3.3）：
- 工程模板版本化：追加式不可变版本（发布新版不改旧行）、固定引用
  （pin 版本）解析不变、发布接统一发布链（revision/账本/审计/409）
- TEMPLATE→回路映射：影响回路数精确计数（消除 affectedLoops=None 降级）
- 跨覆盖层组合颠倒收口（P2-02 验收报告 I 项）：NODE 已覆盖 trip_inactive
  时 LOOP 发 trip_normal 被拦（跨层反例）
- 发布预览 dry-run：校验/差异/受影响清单零写入
- 批量配置 dry-run：逐回路差异 / 部分失败状态（含 NOT_FOUND）零写入
- 工程事实未知态：未知 DCS 型号 / 未配置 PID 结构 / 显式 UNKNOWN 均
  status=UNKNOWN，不默认标准 kp/ti/td；控制方向默认 UNKNOWN 显式态
- 历史片段辨识资格与 PID 应用资格分别定义（L0/L1 门禁语义零改动）
- 辨识包 schemaVersion 1.2：engineeringFacts 落包；1.1 旧包仍可读（主版本门禁）
- C08 剩余：带宽 2%→4% 按样本重计且饱和占比不下降（单调性）
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import BizError
from app.services import calc_snapshot as cs
from app.services import config_publish as cp
from app.services import engineering_template as et
from app.services.engineering_facts import (
    get_loop_qualifications,
    resolve_loop_engineering_facts,
)
from tests.conftest import TEST_USERS, mock_current_user

_TEST_BUILD = "testbuild012345678"


def asyncio_run(coro):
    """pytest-asyncio auto 模式下在同步测试中执行协程的便捷包装."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# mock 辅助（与 test_p2_02_config_publish 同款）
# ---------------------------------------------------------------------------


def _scalar_result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _none_result():
    return _scalar_result(None)


def _count_result(count: int = 5):
    result = MagicMock()
    result.scalar_one.return_value = count
    return result


def _scalars_all_result(items: list):
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    return result


def _all_result(items: list):
    result = MagicMock()
    result.all.return_value = items
    return result


def _first_result(value):
    result = MagicMock()
    result.first.return_value = value
    return result


def _db_with(side_effect):
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=side_effect)
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    db.delete = AsyncMock()
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


def _make_version_row(template_id="tpl-1", version=1, profile=None):
    row = MagicMock()
    row.template_id = template_id
    row.version = version
    row.profile = dict(profile or {})
    row.reason = "r"
    row.published_by = "admin"
    row.published_at = None
    return row


def _make_loop_row(
    loop_id="loop-1",
    *,
    unit_id=None,
    template_id=None,
    template_version=None,
    direction="UNKNOWN",
    tag_name="TIC-101",
    dcs_model_id=None,
    op_lower=None,
    op_upper=None,
):
    row = MagicMock()
    row.id = loop_id
    row.tag_name = tag_name
    row.unit_id = unit_id
    row.engineering_template_id = template_id
    row.engineering_template_version = template_version
    row.control_action_direction = direction
    row.dcs_model_id = dcs_model_id
    row.op_output_lower_limit = op_lower
    row.op_output_upper_limit = op_upper
    row.is_active = True
    return row


# ---------------------------------------------------------------------------
# 1. 工程模板版本化：不可变发布 + 固定引用
# ---------------------------------------------------------------------------


class TestTemplateVersionImmutability:
    """追加式不可变版本：发布只 INSERT 新行，不改不删旧行."""

    def test_publish_appends_new_version_and_advances_pointer(self) -> None:
        """首次发布 → version=1、current_version 指针前进、账本+审计入册."""
        tpl = _make_template_row(current_version=None)
        db = _db_with(
            [
                _scalar_result(tpl),  # 模板实体
                _scalar_result(0),  # revision
                _none_result(),  # max(version)（无版本）
                _count_result(3),  # 关联回路数
            ]
        )
        result = asyncio_run(
            et.publish_template_version(
                db,
                template_id="tpl-1",
                profile={"settlingTau": 120},
                reason="初版",
                operator="admin",
                expected_revision=0,
            )
        )
        assert result["version"] == 1
        assert result["revision"] == 1
        assert result["affectedLoops"] == 3
        assert result["revisionRef"] == "engineering_template:tpl-1@v1"
        # 指针前进（非新建行——模板实体是既有行，仅改指针属性）
        assert tpl.current_version == 1
        added = [call.args[0] for call in db.add.call_args_list]
        versions = [o for o in added if type(o).__name__ == "EngineeringTemplateVersion"]
        publications = [o for o in added if type(o).__name__ == "ConfigPublication"]
        audits = [o for o in added if type(o).__name__ == "SysAuditLog"]
        assert len(versions) == 1 and versions[0].version == 1
        assert len(publications) == 1
        assert publications[0].operation == "TEMPLATE_VERSION"
        assert publications[0].affected_loops == 3
        assert len(audits) == 1
        db.commit.assert_awaited_once()

    def test_publish_second_version_leaves_v1_row_untouched(self) -> None:
        """再发布 v2：旧行零 SELECT-for-update/零改动语义——v1 行仅被读（max），
        新增 v2 行；固定引用 v1 解析结果不变."""
        tpl = _make_template_row(current_version=1)
        v1 = _make_version_row(version=1, profile={"settlingTau": 120})
        db = _db_with(
            [
                _scalar_result(tpl),
                _scalar_result(0),  # revision
                _scalar_result(1),  # max(version) = 1
                _scalar_result({"settlingTau": 120}),  # before 摘要：上一版本 profile
                _count_result(3),
            ]
        )
        result = asyncio_run(
            et.publish_template_version(
                db,
                template_id="tpl-1",
                profile={"settlingTau": 60},
                reason="收紧",
                operator="admin",
                expected_revision=0,
            )
        )
        assert result["version"] == 2
        assert tpl.current_version == 2
        added = [call.args[0] for call in db.add.call_args_list]
        versions = [o for o in added if type(o).__name__ == "EngineeringTemplateVersion"]
        assert len(versions) == 1 and versions[0].version == 2
        # v1 行对象未被触碰（本函数对既有版本行只有 max() 聚合读取）
        assert v1.version == 1 and v1.profile == {"settlingTau": 120}
        # 固定引用语义：revisionRef 按版本精确寻址
        assert et.template_revision_ref("tpl-1", 1) == "engineering_template:tpl-1@v1"
        assert et.template_revision_ref("tpl-1", 2) == "engineering_template:tpl-1@v2"

    def test_publish_stale_revision_409(self) -> None:
        tpl = _make_template_row(current_version=1)
        db = _db_with([_scalar_result(tpl), _scalar_result(5)])
        with pytest.raises(BizError) as exc_info:
            asyncio_run(
                et.publish_template_version(
                    db,
                    template_id="tpl-1",
                    profile={},
                    reason="r",
                    operator="admin",
                    expected_revision=4,
                )
            )
        assert exc_info.value.status_code == 409
        db.add.assert_not_called()

    def test_rollback_repoints_pointer_version_history_kept(self) -> None:
        """指针回拨：current_version 回到既有版本，生成新 revision."""
        tpl = _make_template_row(current_version=3)
        v2 = _make_version_row(version=2)
        db = _db_with(
            [
                _scalar_result(tpl),
                _scalar_result(v2),
                _scalar_result(7),  # revision
                _count_result(3),
            ]
        )
        result = asyncio_run(
            et.rollback_template_version(
                db,
                template_id="tpl-1",
                version=2,
                reason="回拨",
                operator="admin",
                expected_revision=7,
            )
        )
        assert result["revision"] == 8
        assert tpl.current_version == 2
        assert result["revisionRef"] == "engineering_template:tpl-1@v2"

    def test_resolve_template_reference_pin_wins_over_pointer(self) -> None:
        """固定引用：loop pin v1，指针已到 v2 → 解析 v1（发布新版不自动改）."""
        tpl = _make_template_row(current_version=2)
        v1 = _make_version_row(version=1, profile={"settlingTau": 120})
        loop = _make_loop_row(template_id="tpl-1", template_version=1)
        db = _db_with([_scalar_result(tpl), _scalar_result(v1)])
        ref = asyncio_run(et.resolve_template_reference(db, loop))
        assert ref["version"] == 1
        assert ref["pinned"] is True
        assert ref["profile"] == {"settlingTau": 120}
        assert ref["revisionRef"] == "engineering_template:tpl-1@v1"

    def test_resolve_template_reference_follows_pointer_when_not_pinned(self) -> None:
        tpl = _make_template_row(current_version=2)
        v2 = _make_version_row(version=2, profile={"settlingTau": 60})
        loop = _make_loop_row(template_id="tpl-1", template_version=None)
        db = _db_with([_scalar_result(tpl), _scalar_result(v2)])
        ref = asyncio_run(et.resolve_template_reference(db, loop))
        assert ref["version"] == 2
        assert ref["pinned"] is False

    def test_resolve_template_reference_no_template_returns_none(self) -> None:
        loop = _make_loop_row(template_id=None)
        assert asyncio_run(et.resolve_template_reference(_db_with([]), loop)) is None


# ---------------------------------------------------------------------------
# 2. TEMPLATE→回路映射：影响回路数精确计数（affectedLoops 收口）
# ---------------------------------------------------------------------------


class TestTemplateAffectedLoops:
    """TEMPLATE 层发布影响数：模板实体 + loop.engineering_template_id 精确计数."""

    def test_template_layer_counted_via_mapping(self) -> None:
        tpl = _make_template_row()
        db = _db_with([_scalar_result(tpl), _count_result(7)])
        count, note = asyncio_run(cp._count_affected_loops(db, "TEMPLATE", "FAST_RESP"))
        assert count == 7
        assert note is None

    def test_template_layer_unregistered_key_degrades_honestly(self) -> None:
        """模板键未注册为工程模板实体 → None + 说明（不虚报）."""
        db = _db_with([_none_result()])
        count, note = asyncio_run(cp._count_affected_loops(db, "TEMPLATE", "GHOST_KEY"))
        assert count is None
        assert "未注册为工程模板实体" in note

    def test_loop_layer_unknown_scope_reports_zero(self) -> None:
        """LOOP scope 不存在的回路 → 0 + 说明（发布不被拦截，诚实计数）."""
        db = _db_with([_count_result(0)])
        count, note = asyncio_run(cp._count_affected_loops(db, "LOOP", "ghost-loop"))
        assert count == 0
        assert note is not None


# ---------------------------------------------------------------------------
# 3. 跨覆盖层组合颠倒收口（P2-02 验收报告 I 项——核心反例）
# ---------------------------------------------------------------------------


class TestCrossLayerInversionLoopOverNode:
    """LOOP 发布合并其下 NODE/TEMPLATE 有效覆盖后求组合约束."""

    def _publish(self, db, params):
        return asyncio_run(
            cp.publish_override(
                db,
                layer="LOOP",
                scope_id="loop-1",
                metric_code="output_trip_index",
                params=params,
                expected_revision=0,
                reason="r",
                operator="admin",
                control_type="STABLE",
            )
        )

    def test_node_override_trip_inactive_blocks_loop_trip_normal(self) -> None:
        """反例（任务卡原文）：NODE 已覆盖 trip_inactive=0.06 时，LOOP 发布
        trip_normal=0.05 → 合并后 0.06 > 0.05 档位颠倒 → 400 原子拒绝.

        DEFAULT 层无覆盖（单层各自合法）；旧口径只在 DEFAULT 层求值时漏拦。
        """
        loop = _make_loop_row(unit_id="11111111-1111-1111-1111-111111111111")
        node_override = MagicMock()
        node_override.params = {"trip_inactive": 0.06}
        node_override.published_revision = 3
        db = _db_with(
            [
                _scalar_result(0),  # revision
                _scalar_result(loop),  # LOOP 下层链解析（回路行）
                _scalar_result(node_override),  # NODE 层覆盖加载
                _none_result(),  # DEFAULT 层 algorithm_parameter
                _none_result(),  # DEFAULT 层 metric threshold
            ]
        )
        with pytest.raises(BizError) as exc_info:
            self._publish(db, {"trip_normal": 0.05})
        assert exc_info.value.status_code == 400
        assert "组合约束" in exc_info.value.message
        db.add.assert_not_called()
        db.commit.assert_not_awaited()

    def test_node_override_compatible_loop_publish_passes(self) -> None:
        """对照组：NODE trip_inactive=0.06 下 LOOP 发 trip_normal=0.2 合法."""
        loop = _make_loop_row(unit_id="11111111-1111-1111-1111-111111111111")
        node_override = MagicMock()
        node_override.params = {"trip_inactive": 0.06}
        node_override.published_revision = 3
        db = _db_with(
            [
                _scalar_result(0),  # revision
                _scalar_result(loop),  # 回路行
                _scalar_result(node_override),  # NODE 覆盖
                _none_result(),  # ap
                _none_result(),  # threshold
                _none_result(),  # 既有 LOOP 覆盖（新建）
                _count_result(1),  # LOOP 影响数存在性
                _scalars_all_result([]),  # sync: load_stored_config
                _all_result([]),  # sync: load_metric_thresholds
                _scalar_result(1),  # sync: revision
            ]
        )
        result = self._publish(db, {"trip_normal": 0.2})
        assert result["revision"] == 1
        db.commit.assert_awaited_once()

    def test_loop_lower_chain_template_then_node_order(self) -> None:
        """链序：TEMPLATE（回路模板键）→ NODE（回路单元）低→高."""
        tpl = _make_template_row(key="FAST_RESP")
        loop = _make_loop_row(unit_id="unit-1", template_id="tpl-1")
        db = _db_with([_scalar_result(loop), _scalar_result(tpl)])
        chain, degradation = asyncio_run(cp._loop_lower_chain(db, "loop-1"))
        assert chain == [("TEMPLATE", "FAST_RESP"), ("NODE", "unit-1")]
        assert degradation is None

    def test_node_publish_merges_each_template_chain(self) -> None:
        """NODE 发布：单位内回路模板各异 → 每模板一条链分别求值（含空链）."""
        loop_a = _make_loop_row("loop-a", template_id="tpl-1")
        loop_b = _make_loop_row("loop-b", template_id="tpl-2")
        tpl1 = _make_template_row("tpl-1", key="FAST_RESP")
        tpl2 = _make_template_row("tpl-2", key="SLOW_RESP")
        # 单位内回路模板 t1 的 TEMPLATE 覆盖 trip_inactive=0.06：
        # NODE 发布 trip_normal=0.05 → 在该链上颠倒 → 400
        t1_override = MagicMock()
        t1_override.params = {"trip_inactive": 0.06}
        t1_override.published_revision = 2
        db = _db_with(
            [
                _scalar_result(0),  # revision
                _scalars_all_result([loop_a, loop_b]),  # 单位回路集
                _scalar_result(tpl1),
                _scalar_result(tpl2),
                _scalar_result(t1_override),  # TEMPLATE@FAST_RESP 覆盖
                _none_result(),  # TEMPLATE@SLOW_RESP 无覆盖
                _none_result(),  # ap（STABLE）
                _none_result(),  # threshold
            ]
        )
        with pytest.raises(BizError) as exc_info:
            asyncio_run(
                cp.publish_override(
                    db,
                    layer="NODE",
                    scope_id="unit-1",
                    metric_code="output_trip_index",
                    params={"trip_normal": 0.05},
                    expected_revision=0,
                    reason="r",
                    operator="admin",
                    control_type="STABLE",
                )
            )
        assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# 4. 发布预览 dry-run（读取与发布能力分开）
# ---------------------------------------------------------------------------


class TestPreviewPublishOverride:
    def test_preview_reports_diff_validation_and_zero_writes(self) -> None:
        """预览：before/after 差异 + 校验错误 + 受影响样本；零写入零 revision."""
        loop = _make_loop_row(unit_id=None)
        existing = MagicMock()
        existing.params = {"similarity_threshold": 0.5}
        db = _db_with(
            [
                _scalar_result(4),  # 当前 revision
                _scalar_result(loop),  # LOOP 链解析
                _none_result(),  # ap（STABLE）
                _none_result(),  # threshold
                _scalar_result(existing),  # 既有覆盖
                _count_result(1),  # 影响数
                _scalars_all_result([]),  # 受影响样本（LOOP 分支 select all）
            ]
        )
        result = asyncio_run(
            cp.preview_publish_override(
                db,
                layer="LOOP",
                scope_id="loop-1",
                metric_code="oscillation_rate",
                params={"similarity_threshold": 0.8},
                control_type="STABLE",
                expected_revision=4,
            )
        )
        assert result["currentRevision"] == 4
        assert result["wouldConflict"] is False
        assert result["validation"]["ok"] is True
        assert result["before"] == {"similarity_threshold": 0.5}
        assert result["after"] == {"similarity_threshold": 0.8}
        assert result["affectedLoops"] == 1
        assert result["mergePreview"][0]["result"]["similarity_threshold"] == 0.8
        db.add.assert_not_called()
        db.commit.assert_not_awaited()

    def test_preview_surfaces_inversion_as_validation_error_not_raise(self) -> None:
        """预览不抛 400：跨层颠倒进 validation.errors；wouldConflict 报告不 409."""
        loop = _make_loop_row(unit_id="11111111-1111-1111-1111-111111111111")
        node_override = MagicMock()
        node_override.params = {"trip_inactive": 0.06}
        node_override.published_revision = 3
        db = _db_with(
            [
                _scalar_result(4),
                _scalar_result(loop),
                _scalar_result(node_override),
                _none_result(),  # ap
                _none_result(),  # threshold
                _none_result(),  # 既有覆盖
                _count_result(1),
                _scalars_all_result([]),
            ]
        )
        result = asyncio_run(
            cp.preview_publish_override(
                db,
                layer="LOOP",
                scope_id="loop-1",
                metric_code="output_trip_index",
                params={"trip_normal": 0.05},
                control_type="STABLE",
                expected_revision=99,  # 失配：预览口径报告而非 409
            )
        )
        assert result["wouldConflict"] is True
        assert result["validation"]["ok"] is False
        assert any("组合约束" in e for e in result["validation"]["errors"])
        db.add.assert_not_called()


# ---------------------------------------------------------------------------
# 5. 批量配置 dry-run（差异 / 部分失败状态 / 受影响清单）
# ---------------------------------------------------------------------------


def _make_batch_loop(loop_id, **kw):
    loop = _make_loop_row(loop_id, **kw)
    loop.is_active = True
    loop.importance_level = 2
    loop.include_in_evaluation = True
    loop.op_output_lower_limit = None
    loop.op_output_upper_limit = None
    loop.dcs_model_id = None
    return loop


class TestBatchUpdateDryRun:
    def test_dry_run_reports_diffs_and_zero_writes(self) -> None:
        """dry-run：逐回路差异返回，ORM 不改、不审计、不 commit."""
        from app.services.loop_batch import batch_update_loops

        loop1 = _make_batch_loop("loop-1")
        loop2 = _make_batch_loop("loop-2", direction="REVERSE")
        db = _db_with([_scalars_all_result([loop1, loop2])])
        with patch("app.services.loop.derive_loop_status", new=AsyncMock(return_value="READY")):
            result = asyncio_run(
                batch_update_loops(
                    db,
                    ["loop-1", "loop-2"],
                    {"control_action_direction": "REVERSE"},
                    "admin",
                    dry_run=True,
                )
            )
        assert result["dryRun"] is True
        assert result["affected"] == 1  # loop-2 已是 REVERSE → UNCHANGED
        by_id = {r["loopId"]: r for r in result["results"]}
        assert by_id["loop-1"]["status"] == "OK"
        assert by_id["loop-1"]["changes"] == [
            {"field": "control_action_direction", "before": "UNKNOWN", "after": "REVERSE"}
        ]
        assert by_id["loop-2"]["status"] == "UNCHANGED"
        # 零写入
        assert loop1.control_action_direction == "UNKNOWN"
        db.add.assert_not_called()
        db.commit.assert_not_awaited()

    def test_dry_run_reports_partial_failure_and_not_found(self) -> None:
        """部分失败：一个回路 OP 限位与其既有上限冲突 → FAIL；ghost → NOT_FOUND."""
        from app.services.loop_batch import batch_update_loops

        loop_ok = _make_batch_loop("loop-1")
        loop_bad = _make_batch_loop("loop-2")
        loop_bad.op_output_upper_limit = 80.0  # 既有上限 80，本次下限 90 → 颠倒
        db = _db_with(
            [
                _scalars_all_result([loop_ok, loop_bad]),
                _first_result(None),  # loop_ok OP tag 量程
                _first_result(None),  # loop_bad OP tag 量程
            ]
        )
        result = asyncio_run(
            batch_update_loops(
                db,
                ["loop-1", "loop-2", "ghost"],
                {"op_output_lower_limit": 90.0},
                "admin",
                dry_run=True,
            )
        )
        by_id = {r["loopId"]: r for r in result["results"]}
        assert by_id["loop-1"]["status"] == "OK"
        assert by_id["loop-2"]["status"] == "FAIL"
        assert by_id["loop-2"]["error"]
        assert by_id["ghost"]["status"] == "NOT_FOUND"
        assert result["affected"] == 1

    def test_non_dry_run_applies_only_ok_loops(self) -> None:
        """执行模式：FAIL 回路跳过、OK 回路应用 + 审计."""
        from app.services.loop_batch import batch_update_loops

        loop_ok = _make_batch_loop("loop-1")
        loop_bad = _make_batch_loop("loop-2")
        loop_bad.op_output_upper_limit = 80.0
        db = _db_with(
            [
                _scalars_all_result([loop_ok, loop_bad]),
                _first_result(None),  # loop_ok OP tag 量程
                _first_result(None),  # loop_bad OP tag 量程
            ]
        )
        result = asyncio_run(
            batch_update_loops(db, ["loop-1", "loop-2"], {"op_output_lower_limit": 90.0}, "admin")
        )
        assert result["affected"] == 1
        assert loop_ok.op_output_lower_limit == 90.0
        assert loop_bad.op_output_lower_limit is None  # FAIL 未应用
        db.add.assert_called_once()  # 仅 OK 回路审计
        db.commit.assert_awaited_once()


# ---------------------------------------------------------------------------
# 6. 工程事实未知态（07 号文 §3.3：未知 DCS 型号不得默认标准 kp/ti/td）
# ---------------------------------------------------------------------------


def _tag_row(tag_id="tag-1", name="PV1", range_min=0.0, range_max=100.0, unit="℃"):
    row = MagicMock()
    row.id = tag_id
    row.tag_name = name
    row.range_min = range_min
    row.range_max = range_max
    row.unit = unit
    return row


def _tag_rows_result(rows):
    """_load_tag_facts 的 select(tag_role, TagRegistry).all() 形状."""
    result = MagicMock()
    result.all.return_value = rows
    return result


class TestEngineeringFactsUnknownStates:
    def _loop(self, **kw):
        loop = _make_loop_row(**kw)
        loop.op_output_lower_limit = kw.get("op_lower")
        loop.op_output_upper_limit = kw.get("op_upper")
        loop.dcs_model_id = kw.get("dcs_model_id")
        return loop

    def test_unknown_dcs_model_yields_unknown_pid_representation(self) -> None:
        """未绑定 DCS 型号 → status=UNKNOWN，且不携带 pType/iUnit（不默认标准表示）."""
        loop = self._loop()
        db = _db_with(
            [_scalar_result(loop), _tag_rows_result([("PV", _tag_row()), ("OP", _tag_row())])]
        )
        facts = asyncio_run(resolve_loop_engineering_facts(db, "loop-1"))
        assert facts["controlActionDirection"] == "UNKNOWN"
        assert facts["pidRepresentation"]["status"] == "UNKNOWN"
        assert facts["pidRepresentation"]["pType"] is None
        assert facts["pidRepresentation"]["iUnit"] is None
        assert facts["pidRepresentation"]["dcsTemplateRevision"] == "UNKNOWN_DCS_TEMPLATE"
        assert "不得默认标准" in facts["pidRepresentation"]["reason"]

    def test_model_without_structure_is_unknown(self) -> None:
        loop = self._loop(dcs_model_id="model-1")
        model = MagicMock()
        model.id = "model-1"
        model.code = "hollysys-macs"
        db = _db_with(
            [
                _scalar_result(loop),
                _tag_rows_result([("PV", _tag_row()), ("OP", _tag_row())]),
                _scalar_result(model),
                _none_result(),  # 无 dcs_pid_structure 行
            ]
        )
        facts = asyncio_run(resolve_loop_engineering_facts(db, "loop-1"))
        assert facts["pidRepresentation"]["status"] == "UNKNOWN"
        assert facts["pidRepresentation"]["modelCode"] == "hollysys-macs"

    def test_structure_marked_unknown_is_unknown(self) -> None:
        """结构行 representation_status=UNKNOWN → 显式未知（p_type 无转换语义）."""
        loop = self._loop(dcs_model_id="model-1")
        model = MagicMock()
        model.id = "model-1"
        model.code = "unknown-dcs"
        structure = MagicMock()
        structure.representation_status = "UNKNOWN"
        db = _db_with(
            [
                _scalar_result(loop),
                _tag_rows_result([("PV", _tag_row()), ("OP", _tag_row())]),
                _scalar_result(model),
                _scalar_result(structure),
            ]
        )
        facts = asyncio_run(resolve_loop_engineering_facts(db, "loop-1"))
        assert facts["pidRepresentation"]["status"] == "UNKNOWN"
        assert facts["pidRepresentation"]["pType"] is None

    def test_known_structure_and_op_limit_priority(self) -> None:
        """KNOWN 结构带 pType/iUnit；OP 限幅来源优先级=回路字段."""
        loop = self._loop(dcs_model_id="model-1", op_lower=5.0, op_upper=95.0, direction="REVERSE")
        model = MagicMock()
        model.id = "model-1"
        model.code = "supcon-ecs700"
        structure = MagicMock()
        structure.representation_status = "KNOWN"
        structure.p_type = "PROPORTION_BAND"
        structure.i_unit = "MINUTES"
        structure.d_unit = "SECONDS"
        structure.d_filter_enabled = False
        structure.d_filter_unit = None
        structure.d_filter_multiplier = False
        db = _db_with(
            [
                _scalar_result(loop),
                _tag_rows_result([("PV", _tag_row()), ("OP", _tag_row())]),
                _scalar_result(model),
                _scalar_result(structure),
            ]
        )
        facts = asyncio_run(resolve_loop_engineering_facts(db, "loop-1"))
        assert facts["pidRepresentation"]["status"] == "KNOWN"
        assert facts["pidRepresentation"]["pType"] == "PROPORTION_BAND"
        assert facts["pidRepresentation"]["iUnit"] == "MINUTES"
        assert facts["controlActionDirection"] == "REVERSE"
        assert facts["opLimits"] == {
            "lower": 5.0,
            "upper": 95.0,
            "source": "LOOP_FIELD",
            "note": "回路工程属性 op_output_lower/upper_limit",
        }
        assert facts["pvRange"]["unit"] == "℃"

    def test_op_limits_fall_back_to_tag_range_then_default(self) -> None:
        loop = self._loop()  # 无回路字段
        db = _db_with(
            [
                _scalar_result(loop),
                _tag_rows_result(
                    [
                        ("PV", _tag_row()),
                        ("OP", _tag_row("tag-op", "OP1", range_min=4.0, range_max=20.0)),
                    ]
                ),
            ]
        )
        facts = asyncio_run(resolve_loop_engineering_facts(db, "loop-1"))
        assert facts["opLimits"]["source"] == "TAG_RANGE"
        assert facts["opLimits"]["lower"] == 4.0
        # 完全缺失 → 默认 0~100（显式标注假设）
        db2 = _db_with(
            [
                _scalar_result(self._loop()),
                _tag_rows_result(
                    [("PV", _tag_row()), ("OP", _tag_row(range_min=None, range_max=None))]
                ),
            ]
        )
        facts2 = asyncio_run(resolve_loop_engineering_facts(db2, "loop-1"))
        assert facts2["opLimits"]["source"] == (
            "DEFAULT_0_100"  # 回路字段与 OP 位号量程均缺失 → 显式默认假设
        )


# ---------------------------------------------------------------------------
# 7. 历史片段辨识资格与 PID 应用资格分别定义（07 号文 §3.3）
# ---------------------------------------------------------------------------


class TestQualificationsSeparation:
    def test_history_identification_independent_of_tuning_gate(self) -> None:
        """历史片段辨识资格只看数据前提（PV/OP 绑定），不消费 L0/L1 门禁."""
        loop = _make_loop_row(dcs_model_id=None)
        db = _db_with(
            [
                _scalar_result(loop),
                _tag_rows_result([("PV", _tag_row()), ("OP", _tag_row())]),
                _none_result(),  # _resolve_pid_representation: 无 dcs_model → 一次查询都不走
            ]
        )
        with patch(
            "app.services.loop_fitness.get_latest_fitness_per_loop",
            new=AsyncMock(return_value={"loop-1": SimpleNamespace(tune_level_effective="L1")}),
        ):
            quals = asyncio_run(get_loop_qualifications(db, "loop-1"))
        # 手动主导（tuneLevel=L1，PID 应用被阻断）不影响历史辨识资格
        assert quals["historyIdentification"]["eligible"] is True
        assert quals["pidApplication"]["tuneLevel"] == "L1"
        assert quals["pidApplication"]["gate"] == "TUNING_FITNESS_L0_L1"
        assert quals["pidApplication"]["blockingLevels"] == ["L0", "L1"]
        assert "不消费 L0/L1" in quals["historyIdentification"]["note"]

    def test_missing_op_binding_disables_history_identification(self) -> None:
        loop = _make_loop_row()
        db = _db_with(
            [
                _scalar_result(loop),
                _tag_rows_result([("PV", _tag_row())]),  # 缺 OP 绑定
                _none_result(),
            ]
        )
        with patch(
            "app.services.loop_fitness.get_latest_fitness_per_loop",
            new=AsyncMock(return_value={}),
        ):
            quals = asyncio_run(get_loop_qualifications(db, "loop-1"))
        assert quals["historyIdentification"]["eligible"] is False
        assert quals["pidApplication"]["tuneLevel"] is None  # 无快照=未知，不伪补


# ---------------------------------------------------------------------------
# 8. 辨识包 schemaVersion 1.2：engineeringFacts 落包 + 1.1 兼容
# ---------------------------------------------------------------------------


_TS0 = datetime(2026, 10, 12, 8, 0, 0)
_TS1 = _TS0 + timedelta(hours=1)


def _result_dict_min() -> dict:
    return {"success": False, "reason": "test", "algorithmVersion": "v-test"}


class _SaveDB:
    """save_calculation_snapshot 的最小 DB mock（幂等查询→不存在→insert）."""

    def __init__(self):
        self._db = AsyncMock()
        insert_result = MagicMock()
        insert_result.scalar_one_or_none.return_value = MagicMock(
            id="snap-p204-1",
            input_hash="x",
        )
        self._db.execute = AsyncMock(side_effect=[_none_result(), insert_result])
        self._db.add = MagicMock()


class TestIdentificationManifest12:
    _SNAP_ID = "00000000-0000-0000-0000-00000000abcd"

    @pytest.fixture()
    def snap_dir(self, tmp_path, monkeypatch):
        from app.core.config import settings

        monkeypatch.setattr(settings, "CLPM_CALC_SNAPSHOT_DIR", str(tmp_path / "snaps"))
        return settings.CLPM_CALC_SNAPSHOT_DIR

    @pytest.fixture()
    def fixed_build(self, monkeypatch):
        monkeypatch.setattr(cs, "executable_build_digest", lambda: _TEST_BUILD)
        return _TEST_BUILD

    @pytest.fixture()
    def fixed_snapshot_id(self, monkeypatch):
        from uuid import UUID

        monkeypatch.setattr(cs, "uuid4", lambda: UUID(self._SNAP_ID))
        return self._SNAP_ID

    def _save(self, engineering_facts=None):
        n = 50
        return asyncio_run(
            cs.save_identification_snapshot(
                _SaveDB()._db,
                loop_id="loop-p204",
                ts_start=_TS0,
                ts_end=_TS1,
                timestamps_rel=[float(i) for i in range(n)],
                op=[50.0] * n,
                pv=[70.0] * n,
                sp=[70.0] * n,
                mode=[1] * n,
                sampling_period_seconds=1.0,
                theta_estimate=None,
                candidate_model_types=["FOPDT"],
                result_dict=_result_dict_min(),
                tuning_record_id="tr-p204",
                dcs_template_revision="dcs_model:m-1",
                engineering_facts=engineering_facts,
            )
        )

    def test_manifest_1_2_with_engineering_facts(
        self, snap_dir, fixed_build, fixed_snapshot_id
    ) -> None:
        """新包 schemaVersion=1.2，engineeringFacts 原样入 manifest."""
        facts = {
            "loopId": "loop-p204",
            "controlActionDirection": "UNKNOWN",
            "opLimits": {"lower": 0.0, "upper": 100.0, "source": "DEFAULT_0_100", "note": "n"},
            "pvRange": {"lower": 0.0, "upper": 100.0, "unit": "℃", "tagName": "PV1"},
            "pidRepresentation": {
                "status": "UNKNOWN",
                "pType": None,
                "iUnit": None,
                "dUnit": None,
                "dcsTemplateRevision": "dcs_model:m-1",
            },
            "engineeringTemplate": None,
        }
        self._save(engineering_facts=facts)
        manifest = self._read_manifest()
        assert manifest["schemaVersion"] == "1.2"
        assert manifest["engineeringFacts"] == facts

    def test_manifest_1_2_without_facts_explicit_null(
        self, snap_dir, fixed_build, fixed_snapshot_id
    ) -> None:
        """engineering_facts=None → 节显式 null（当时未记录，不伪补）."""
        self._save()
        manifest = self._read_manifest()
        assert manifest["schemaVersion"] == "1.2"
        assert manifest["engineeringFacts"] is None

    def test_manifest_1_1_package_still_readable(
        self, snap_dir, fixed_build, fixed_snapshot_id
    ) -> None:
        """兼容（任务卡）：1.1 旧包（无 engineeringFacts 节）仍可读——主版本门禁."""
        self._save()
        paths = cs.snapshot_file_paths(self._SNAP_ID)
        man = next(p for p in paths if p.name.endswith(".manifest.json"))
        npz = next(p for p in paths if p.suffix == ".npz")
        manifest = json.loads(man.read_text(encoding="utf-8"))
        manifest["schemaVersion"] = "1.1"
        manifest.pop("engineeringFacts")  # 1.1 包没有该节
        man.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True), "utf-8")

        now_naive = datetime.now(UTC).replace(tzinfo=None)  # 与文件落盘月份目录一致
        row = SimpleNamespace(
            id=self._SNAP_ID,
            input_hash=None,
            cleaned_at=None,
            retention_class="NORMAL",
            expires_at=now_naive + timedelta(days=90),
            created_at=now_naive,
            ts_start=now_naive,
        )
        _, arrays = cs._read_files(npz, man)
        row.input_hash = cs.compute_input_hash(manifest, arrays)
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_scalar_result(row))
        loaded = asyncio_run(cs.load_snapshot_for_replay(db, "snap-p204-1"))
        # 1.1 包可读；缺失节按"当时未记录"处理（不伪补）
        assert loaded.manifest["schemaVersion"] == "1.1"
        assert "engineeringFacts" not in loaded.manifest

    def _read_manifest(self) -> dict:
        paths = cs.snapshot_file_paths(self._SNAP_ID)
        man = next(p for p in paths if p.name.endswith(".manifest.json"))
        return json.loads(man.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 9. C08 剩余：带宽 2%→4% 按样本重计且饱和占比不下降（单调性）
# ---------------------------------------------------------------------------


class TestC08BandWidenMonotonic:
    """C08 剩余条款验证（CFG-06 收编后的现状口径）.

    逐点判定口径（compute_time_ratio_counters 按实际配置阈值）：带宽从
    2% 扩到 4% 时阈值边界外推，已饱和样本仍饱和、新增边界样本计入——
    同一样本集上饱和占比单调不降。
    """

    def test_band_widening_never_decreases_saturation_ratio(self) -> None:
        from app.services.loop_fitness import compute_time_ratio_counters

        op = [0.0, 1.0, 3.0, 50.0, 97.0, 99.0, 100.0]
        kwargs = {
            "op_series": op,
            "sp_series": [50.0] * 7,
            "pv_series": [50.0] * 7,
            "mode_series": [1] * 7,
            "op_range": (0.0, 100.0),
            "pv_range": (0.0, 100.0),
        }
        r2 = compute_time_ratio_counters(op_sat_band_pct=2.0, **kwargs)
        r4 = compute_time_ratio_counters(op_sat_band_pct=4.0, **kwargs)
        # C08 基线值（04 号文）：2%→4/7、4%→6/7
        assert r2["op_saturated_ratio"] == pytest.approx(4 / 7)
        assert r4["op_saturated_ratio"] == pytest.approx(6 / 7)
        # 单调性：带宽扩大占比不下降（逐点重计，非旧口径的线性缩放近似）
        assert r4["op_saturated_ratio"] >= r2["op_saturated_ratio"]
        # 连续档位单调（0%→2%→4%→10%→25%→50%）
        prev = -1.0
        for band in (0.0, 2.0, 4.0, 10.0, 25.0, 50.0):
            r = compute_time_ratio_counters(op_sat_band_pct=band, **kwargs)
            assert r["op_saturated_ratio"] >= prev
            prev = r["op_saturated_ratio"]

    def test_deviation_threshold_widen_does_not_increase_hits(self) -> None:
        """偏差阈值扩大命中不增加（10%→20%：1/3→0/3）."""
        from app.services.loop_fitness import compute_time_ratio_counters

        kwargs = {
            "op_series": [50.0] * 3,
            "sp_series": [0.0, 0.0, 0.0],
            "pv_series": [1.0, 5.0, 15.0],
            "mode_series": [1] * 3,
            "op_range": (0.0, 100.0),
            "pv_range": (0.0, 100.0),
        }
        r10 = compute_time_ratio_counters(op_sat_band_pct=0.0, sp_pv_dev_pct=10.0, **kwargs)
        r20 = compute_time_ratio_counters(op_sat_band_pct=0.0, sp_pv_dev_pct=20.0, **kwargs)
        assert r10["sp_pv_deviation_ratio"] == pytest.approx(1 / 3)
        assert r20["sp_pv_deviation_ratio"] == 0.0
        assert r20["sp_pv_deviation_ratio"] <= r10["sp_pv_deviation_ratio"]


# ---------------------------------------------------------------------------
# 10. 端点权限：预览只读三角色 / 发布 ADMIN（读取与发布能力分开）
# ---------------------------------------------------------------------------


class TestPreviewEndpointRoles:
    def test_preview_open_to_ic_engineer(self, client, mock_db) -> None:
        """IC_ENGINEER 可调用预览（只读）；发布端点对其 403（DEC-03 不变）."""
        loop = _make_loop_row()
        mock_db.execute = AsyncMock(
            side_effect=[
                _scalar_result(0),  # revision
                _scalar_result(loop),  # LOOP 链解析
                _none_result(),  # ap
                _none_result(),  # threshold
                _none_result(),  # 既有覆盖
                _count_result(1),  # 影响数
                _scalars_all_result([]),  # 受影响样本
            ]
        )
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.post(
                "/api/v1/configs/publish/overrides/preview",
                json={
                    "layer": "LOOP",
                    "scopeId": "loop-1",
                    "metricCode": "oscillation_rate",
                    "params": {"similarity_threshold": 0.8},
                    "controlType": "STABLE",
                },
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["validation"]["ok"] is True

    def test_preview_as_sponsor_forbidden(self, client, mock_db) -> None:
        """SPONSOR 不在只读三角色 → 403（预览不是全员开放）."""
        with mock_current_user(TEST_USERS["sponsor"]):
            resp = client.post(
                "/api/v1/configs/publish/overrides/preview",
                json={
                    "layer": "LOOP",
                    "scopeId": "loop-1",
                    "metricCode": "oscillation_rate",
                    "params": {},
                },
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 403
