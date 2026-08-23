"""处置模块 API 测试（v2.0 双实体：建议审核 + 工单流转）。

设计文档：docs/MVP设计/08-处置模块设计方案.md §4 状态机 / §6 API 定义
覆盖：
- 建议 4 态全迁移矩阵（accept/reject/ignore/convert + 非法 ERR_STATE 400 + 原因必填）
- 工单 6 态全迁移矩阵（start/feedback 自环/submit/verify/cancel + 非法迁移）
- convert（多建议合一单回链 / 跨回路拒绝 / 非 ACCEPTED 拒绝 / order_no 格式与冲突重试）
- 手动新增（建议回路不存在报错 / 工单 title 缺省）
- KPI 固化窗口 SQL 口径断言（前窗 started_at−24h，§4.3 v2.0）
- 权限（IC_ENGINEER/PE_ENGINEER/ADMIN 可写，SPONSOR/EXPERT 403）

模式参照 test_diagnosis_v2_api.py：mock db（_seq_execute）+ mock_current_user。
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from app.models.handling_order import HandlingOrder
from app.models.loop_action_item import LoopActionItem
from app.models.metric import KpiSnapshotHourly
from tests.conftest import TEST_USERS, mock_current_user

LOOP_ID = str(uuid4())
LOOP_ID2 = str(uuid4())
RUN_ID = str(uuid4())
SUGGESTION_ID = str(uuid4())
ORDER_ID = str(uuid4())

#: 固定时间锚点（naive UTC），用于 KPI 窗口口径断言（§4.3 v2.0：前窗 started_at 口径）
STARTED_AT = datetime(2026, 8, 10, 8, 0, 0)
SUBMITTED_AT = datetime(2026, 8, 12, 9, 30, 0)


# ---------------------------------------------------------------------------
# 构造 helper
# ---------------------------------------------------------------------------


def _make_suggestion(
    *,
    status: str = "PENDING",
    loop_id: str = LOOP_ID,
    reviewed_by: str | None = None,
    rejected_reason: str | None = None,
    converted_order_id: str | None = None,
    ignore_reason: str | None = None,
) -> LoopActionItem:
    return LoopActionItem(
        id=SUGGESTION_ID,
        run_id=RUN_ID,
        loop_id=loop_id,
        source="SYSTEM",
        category="TUNING",
        content="重新整定 PID 参数",
        basis="诊断结论：参数问题（PID 整定）",
        priority=1,
        status=status,
        suggested_by="系统",
        suggested_at=datetime(2026, 8, 9, 0, 0, 0),
        reviewed_by=reviewed_by,
        reviewed_at=datetime(2026, 8, 9, 6, 0, 0) if reviewed_by else None,
        rejected_reason=rejected_reason,
        converted_order_id=converted_order_id,
        ignore_reason=ignore_reason,
    )


def _make_order(
    *,
    status: str = "PENDING",
    action_type: str = "TUNING",
    action_detail: dict | None = None,
    handler: str | None = None,
    started_at: datetime | None = None,
    submitted_at: datetime | None = None,
    feedback_log: list | None = None,
    verify_result: str | None = None,
    verify_note: str | None = None,
    verified_by: str | None = None,
    verified_at: datetime | None = None,
    verify_run_id: str | None = None,
    kpi_before: dict | None = None,
    kpi_after: dict | None = None,
    tuning_record_id: str | None = None,
    cancel_reason: str | None = None,
    suggestion_ids: list | None = None,
    source: str = "DIAGNOSIS",
) -> HandlingOrder:
    return HandlingOrder(
        id=ORDER_ID,
        order_no="HD-20260820-001",
        loop_id=LOOP_ID,
        source=source,
        suggestion_ids=suggestion_ids if suggestion_ids is not None else [SUGGESTION_ID],
        title="反应器压力控制处置",
        action_type=action_type,
        action_detail=action_detail,
        planned_at=None,
        planned_by="admin",
        handler=handler,
        started_at=started_at,
        feedback_log=feedback_log,
        submitted_at=submitted_at,
        verify_run_id=verify_run_id,
        verify_result=verify_result,
        verify_note=verify_note,
        verified_by=verified_by,
        verified_at=verified_at,
        kpi_before=kpi_before,
        kpi_after=kpi_after,
        tuning_record_id=tuning_record_id,
        cancel_reason=cancel_reason,
        status=status,
    )


def _make_snapshot(
    *,
    score: str = "85.50",
    ts_start: datetime,
    ts_end: datetime,
    confidence: str = "B",
) -> KpiSnapshotHourly:
    return KpiSnapshotHourly(
        id=str(uuid4()),
        loop_id=LOOP_ID,
        ts_start=ts_start,
        ts_end=ts_end,
        score=Decimal(score),
        good_value_rate=Decimal("97.10"),
        auto_mode_rate=Decimal("88.00"),
        steady_rate=Decimal("92.30"),
        accuracy_rate=Decimal("90.00"),
        oscillation_rate=Decimal("12.50"),
        saturation_rate=Decimal("3.20"),
        fast_rate=Decimal("81.40"),
        effective_auto_rate=Decimal("86.70"),
        status="SUCCESS",
        confidence_level=confidence,
    )


def _seq_execute(results: list):
    it = iter(results)

    async def _execute(*args, **kwargs):  # noqa: ARG001
        return next(it)

    return _execute


def _scalar_result(value: Any) -> MagicMock:
    """execute 结果：scalar_one_or_none（ORM 单行）与 scalar（COUNT）双口径 mock。"""
    r = MagicMock()
    r.scalar_one_or_none.return_value = value
    r.scalar.return_value = value
    return r


def _scalars_all_result(rows: list) -> MagicMock:
    """select(...).scalars().all() 形态的 execute 结果。"""
    r = MagicMock()
    r.scalars.return_value.all.return_value = rows
    return r


def _override_db(client, results: list) -> MagicMock:
    """mock db：按序返回 execute 结果；commit/refresh 可断言。"""
    from app.core.db import get_db

    mock_db = MagicMock()
    mock_db.execute = _seq_execute(results)
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()
    client.app.dependency_overrides[get_db] = lambda: mock_db
    return mock_db


def _capture_override_db(client, results: list, captured: list) -> MagicMock:
    """同 _override_db，额外捕获传入的 SQL 语句（用于 KPI 窗口口径断言）。"""
    from app.core.db import get_db

    it = iter(results)

    async def _execute(stmt, *args, **kwargs):
        captured.append(stmt)
        return next(it)

    mock_db = MagicMock()
    mock_db.execute = _execute
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()
    client.app.dependency_overrides[get_db] = lambda: mock_db
    return mock_db


def _pg_sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


# ===========================================================================
# 建议侧：accept / reject / ignore（§4.1）
# ===========================================================================


class TestAcceptEndpoint:
    def _post(self, client, user_key: str = "ic_engineer"):
        with mock_current_user(TEST_USERS[user_key]):
            return client.post(
                f"/api/v1/handling/suggestions/{SUGGESTION_ID}/accept",
                headers={"Authorization": "Bearer fake-token"},
            )

    def test_accept_pending_ok(self, client) -> None:
        """合法迁移 #1：PENDING → ACCEPTED；记录审核人/时间。"""
        sug = _make_suggestion(status="PENDING")
        mock_db = _override_db(client, [_scalar_result(sug)])
        resp = self._post(client)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] == "ACCEPTED"
        assert data["statusLabel"] == "已接受"
        assert data["reviewedBy"] == "ic_engineer"
        assert data["reviewedAt"] is not None and data["reviewedAt"].endswith("Z")
        mock_db.commit.assert_awaited()

    @pytest.mark.parametrize("status", ["ACCEPTED", "CONVERTED", "REJECTED", "IGNORED"])
    def test_accept_invalid_state(self, client, status: str) -> None:
        """非法迁移：accept 仅允许 PENDING（REJECTED/CONVERTED 终态不可再审核）。"""
        sug = _make_suggestion(status=status)
        _override_db(client, [_scalar_result(sug)])
        resp = self._post(client)
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_STATE"

    def test_accept_not_found(self, client) -> None:
        _override_db(client, [_scalar_result(None)])
        resp = self._post(client)
        assert resp.status_code == 404
        assert resp.json()["code"] == "ERR_NOT_FOUND"

    def test_accept_malformed_id_rejected(self, client) -> None:
        """畸形非 UUID suggestionId → ERR_PARAM 400（PG UUID 比较防 500 traceback）。"""
        _override_db(client, [])
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.post(
                "/api/v1/handling/suggestions/not-a-uuid/accept",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_PARAM"

    @pytest.mark.parametrize("user_key", ["admin", "ic_engineer", "pe_engineer"])
    def test_accept_allowed_roles(self, client, user_key: str) -> None:
        sug = _make_suggestion(status="PENDING")
        _override_db(client, [_scalar_result(sug)])
        resp = self._post(client, user_key=user_key)
        assert resp.status_code == 200

    @pytest.mark.parametrize("user_key", ["sponsor", "expert"])
    def test_accept_forbidden(self, client, user_key: str) -> None:
        """SPONSOR / EXPERT 只读，审核 403（§7）。"""
        sug = _make_suggestion(status="PENDING")
        _override_db(client, [_scalar_result(sug)])
        resp = self._post(client, user_key=user_key)
        assert resp.status_code == 403


class TestRejectEndpoint:
    def _post(self, client, body: dict, user_key: str = "ic_engineer"):
        with mock_current_user(TEST_USERS[user_key]):
            return client.post(
                f"/api/v1/handling/suggestions/{SUGGESTION_ID}/reject",
                json=body,
                headers={"Authorization": "Bearer fake-token"},
            )

    def test_reject_ok_with_reason(self, client) -> None:
        """合法迁移 #2：PENDING → REJECTED 终态；原因+审核人留痕。"""
        sug = _make_suggestion(status="PENDING")
        mock_db = _override_db(client, [_scalar_result(sug)])
        resp = self._post(client, {"rejectedReason": "与年度大修计划重复"})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] == "REJECTED"
        assert data["statusLabel"] == "已驳回"
        assert data["rejectedReason"] == "与年度大修计划重复"
        assert data["reviewedBy"] == "ic_engineer"
        mock_db.commit.assert_awaited()

    def test_reject_missing_reason(self, client) -> None:
        sug = _make_suggestion(status="PENDING")
        _override_db(client, [_scalar_result(sug)])
        resp = self._post(client, {})
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_PARAM"

    def test_reject_blank_reason(self, client) -> None:
        sug = _make_suggestion(status="PENDING")
        _override_db(client, [_scalar_result(sug)])
        resp = self._post(client, {"rejectedReason": "   "})
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_PARAM"

    @pytest.mark.parametrize("status", ["ACCEPTED", "CONVERTED", "REJECTED", "IGNORED"])
    def test_reject_invalid_state(self, client, status: str) -> None:
        sug = _make_suggestion(status=status)
        _override_db(client, [_scalar_result(sug)])
        resp = self._post(client, {"rejectedReason": "重复"})
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_STATE"

    def test_reject_forbidden_sponsor(self, client) -> None:
        sug = _make_suggestion(status="PENDING")
        _override_db(client, [_scalar_result(sug)])
        resp = self._post(client, {"rejectedReason": "重复"}, user_key="sponsor")
        assert resp.status_code == 403


class TestIgnoreEndpoint:
    def _post(self, client, body: dict, user_key: str = "ic_engineer"):
        with mock_current_user(TEST_USERS[user_key]):
            return client.post(
                f"/api/v1/handling/suggestions/{SUGGESTION_ID}/ignore",
                json=body,
                headers={"Authorization": "Bearer fake-token"},
            )

    def test_ignore_ok(self, client) -> None:
        """合法迁移 #3：PENDING → IGNORED 终态；ignoreReason 必填。"""
        sug = _make_suggestion(status="PENDING")
        mock_db = _override_db(client, [_scalar_result(sug)])
        resp = self._post(client, {"ignoreReason": "建议不适用（旁路回路）"})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] == "IGNORED"
        assert data["statusLabel"] == "已忽略"
        assert data["ignoreReason"] == "建议不适用（旁路回路）"
        mock_db.commit.assert_awaited()

    def test_ignore_missing_reason(self, client) -> None:
        sug = _make_suggestion(status="PENDING")
        _override_db(client, [_scalar_result(sug)])
        resp = self._post(client, {})
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_PARAM"

    @pytest.mark.parametrize("status", ["ACCEPTED", "CONVERTED", "REJECTED", "IGNORED"])
    def test_ignore_invalid_state(self, client, status: str) -> None:
        sug = _make_suggestion(status=status)
        _override_db(client, [_scalar_result(sug)])
        resp = self._post(client, {"ignoreReason": "重复"})
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_STATE"

    def test_ignore_forbidden_sponsor(self, client) -> None:
        sug = _make_suggestion(status="PENDING")
        _override_db(client, [_scalar_result(sug)])
        resp = self._post(client, {"ignoreReason": "重复"}, user_key="sponsor")
        assert resp.status_code == 403


# ===========================================================================
# 建议侧：手动新增 + convert 转工单（§6.1）
# ===========================================================================


class TestCreateSuggestion:
    def _post(self, client, body: dict, user_key: str = "ic_engineer"):
        with mock_current_user(TEST_USERS[user_key]):
            return client.post(
                "/api/v1/handling/suggestions",
                json=body,
                headers={"Authorization": "Bearer fake-token"},
            )

    def test_create_ok_manual_source(self, client) -> None:
        """手动新增：run_id 置空、source=MANUAL、建议人=当前用户。"""
        loop_row = MagicMock()
        mock_db = _override_db(client, [_scalar_result(loop_row)])
        resp = self._post(client, {"loopId": LOOP_ID, "content": "现场巡检发现阀位偏差"})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["source"] == "MANUAL"
        assert data["runId"] is None
        assert data["status"] == "PENDING"
        assert data["suggestedBy"] == "ic_engineer"
        mock_db.commit.assert_awaited()

    def test_create_loop_not_found(self, client) -> None:
        """回路不存在 → ERR_PARAM 400。"""
        _override_db(client, [_scalar_result(None)])
        resp = self._post(client, {"loopId": str(uuid4()), "content": "内容"})
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_PARAM"
        assert "回路不存在" in resp.json()["message"]

    def test_create_missing_content(self, client) -> None:
        resp = self._post(client, {"loopId": LOOP_ID})
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_PARAM"

    def test_create_forbidden_sponsor(self, client) -> None:
        resp = self._post(client, {"loopId": LOOP_ID, "content": "x"}, user_key="sponsor")
        assert resp.status_code == 403


class TestConvertEndpoint:
    def _post(self, client, body: dict, user_key: str = "ic_engineer"):
        with mock_current_user(TEST_USERS[user_key]):
            return client.post(
                "/api/v1/handling/suggestions/convert",
                json=body,
                headers={"Authorization": "Bearer fake-token"},
            )

    @staticmethod
    def _body(ids: list[str], **over: Any) -> dict:
        body: dict = {"suggestionIds": ids, "actionType": "VALVE"}
        body.update(over)
        return body

    def test_convert_multi_suggestions_one_order(self, client) -> None:
        """多建议合一单：同回路 2 条 ACCEPTED → 1 张工单，建议各自转 CONVERTED 回链。"""
        s1 = _make_suggestion(status="ACCEPTED")
        s2 = _make_suggestion(status="ACCEPTED")
        s2.id = str(uuid4())
        mock_db = _override_db(
            client,
            [_scalars_all_result([s1, s2]), _scalar_result(0)],
        )
        resp = self._post(client, self._body([s1.id, s2.id], handler="仪表班-张三"))
        assert resp.status_code == 200
        data = resp.json()["data"]
        today = datetime.now(UTC).strftime("%Y%m%d")
        assert data["orderNo"] == f"HD-{today}-001"
        assert data["source"] == "DIAGNOSIS"
        assert data["status"] == "PENDING"
        assert data["handler"] == "仪表班-张三"
        assert set(data["suggestionIds"]) == {s1.id, s2.id}
        assert data["title"] == "重新整定 PID 参数"[:50]
        # 建议回链：两行均 CONVERTED 且 converted_order_id=工单 id
        assert s1.status == "CONVERTED" and s1.converted_order_id == data["id"]
        assert s2.status == "CONVERTED" and s2.converted_order_id == data["id"]
        mock_db.commit.assert_awaited()

    def test_convert_cross_loop_rejected(self, client) -> None:
        """跨回路建议不能合并转工单 → ERR_PARAM。"""
        s1 = _make_suggestion(status="ACCEPTED", loop_id=LOOP_ID)
        s2 = _make_suggestion(status="ACCEPTED", loop_id=LOOP_ID2)
        s2.id = str(uuid4())
        _override_db(client, [_scalars_all_result([s1, s2])])
        resp = self._post(client, self._body([s1.id, s2.id]))
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_PARAM"
        assert "同一回路" in resp.json()["message"]

    def test_convert_non_accepted_rejected(self, client) -> None:
        """非 ACCEPTED 状态（PENDING）不能转工单 → ERR_PARAM。"""
        s1 = _make_suggestion(status="ACCEPTED")
        s2 = _make_suggestion(status="PENDING")
        s2.id = str(uuid4())
        _override_db(client, [_scalars_all_result([s1, s2])])
        resp = self._post(client, self._body([s1.id, s2.id]))
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_PARAM"
        assert "ACCEPTED" in resp.json()["message"]

    def test_convert_invalid_id(self, client) -> None:
        """部分建议 id 无效（查询行数不匹配）→ ERR_PARAM。"""
        s1 = _make_suggestion(status="ACCEPTED")
        _override_db(client, [_scalars_all_result([s1])])
        resp = self._post(client, self._body([s1.id, str(uuid4())]))
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_PARAM"

    def test_convert_empty_ids(self, client) -> None:
        resp = self._post(client, self._body([]))
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_PARAM"

    def test_convert_invalid_action_type(self, client) -> None:
        s1 = _make_suggestion(status="ACCEPTED")
        _override_db(client, [_scalars_all_result([s1])])
        resp = self._post(client, self._body([s1.id], actionType="BOGUS"))
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_PARAM"

    def test_convert_order_no_conflict_retry_once(self, client) -> None:
        """order_no 唯一冲突：捕获 IntegrityError → rollback → 序号+1 重试一次成功（§3.3）。"""
        s1 = _make_suggestion(status="ACCEPTED")
        from app.core.db import get_db

        it = iter(
            [
                _scalars_all_result([s1]),  # 首次建议查询
                _scalar_result(0),  # 首次 COUNT → HD-...-001
                _scalars_all_result([s1]),  # 重试后建议重新查询
                _scalar_result(0),  # 重试 COUNT（bump=1）→ HD-...-002
            ]
        )

        async def _execute(*args, **kwargs):  # noqa: ARG001
            return next(it)

        mock_db = MagicMock()
        mock_db.execute = _execute
        mock_db.commit = AsyncMock(
            side_effect=[IntegrityError("dup", None, Exception("unique")), None]
        )
        mock_db.rollback = AsyncMock()
        mock_db.refresh = AsyncMock()
        client.app.dependency_overrides[get_db] = lambda: mock_db

        resp = self._post(client, self._body([s1.id]))
        assert resp.status_code == 200
        data = resp.json()["data"]
        today = datetime.now(UTC).strftime("%Y%m%d")
        assert data["orderNo"] == f"HD-{today}-002"
        mock_db.rollback.assert_awaited_once()
        assert s1.status == "CONVERTED"

    def test_convert_forbidden_sponsor(self, client) -> None:
        resp = self._post(client, self._body([SUGGESTION_ID], handler="x"), user_key="sponsor")
        assert resp.status_code == 403


# ===========================================================================
# 工单侧：start / feedback / submit / verify / cancel（§4.2）
# ===========================================================================


class TestOrderStartEndpoint:
    def _post(self, client, body: dict, user_key: str = "ic_engineer"):
        with mock_current_user(TEST_USERS[user_key]):
            return client.post(
                f"/api/v1/handling/orders/{ORDER_ID}/start",
                json=body,
                headers={"Authorization": "Bearer fake-token"},
            )

    def test_start_pending_ok_default_handler(self, client) -> None:
        """合法迁移 #1：PENDING → EXECUTING；handler 缺省=当前登录用户。"""
        order = _make_order(status="PENDING", action_type="TUNING")
        mock_db = _override_db(client, [_scalar_result(order)])
        resp = self._post(client, {"pidBefore": {"p": 1.2, "i": 20, "d": 0}})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] == "EXECUTING"
        assert data["statusLabel"] == "执行中"
        assert data["handler"] == "ic_engineer"
        assert data["startedAt"] is not None and data["startedAt"].endswith("Z")
        # TUNING 开工回填 pidBefore 并入 action_detail（§6.2）
        assert data["actionDetail"]["pidBefore"] == {"p": 1.2, "i": 20, "d": 0}
        mock_db.commit.assert_awaited()

    def test_start_custom_handler(self, client) -> None:
        order = _make_order(status="PENDING", action_type="VALVE")
        _override_db(client, [_scalar_result(order)])
        resp = self._post(client, {"handler": "仪表班-张三"})
        assert resp.status_code == 200
        assert resp.json()["data"]["handler"] == "仪表班-张三"

    def test_start_reopened_clears_verify_fields(self, client) -> None:
        """合法迁移 #7：REOPENED → EXECUTING；上一轮验证字段清空待新轮回。"""
        order = _make_order(
            status="REOPENED",
            action_type="TUNING",
            submitted_at=SUBMITTED_AT,
            verify_result="INEFFECTIVE",
            verify_note="振动仍在",
            verified_by="admin",
            verified_at=datetime(2026, 8, 13, 0, 0, 0),
            verify_run_id=RUN_ID,
            kpi_before={"score": 70.0},
            kpi_after={"score": 71.0},
        )
        _override_db(client, [_scalar_result(order)])
        resp = self._post(client, {"handler": "李四"})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] == "EXECUTING"
        assert data["submittedAt"] is None
        assert data["verifyResult"] is None
        assert data["verifyNote"] is None
        assert data["verifiedBy"] is None
        assert data["verifiedAt"] is None
        assert data["verifyRunId"] is None
        assert data["kpiBefore"] is None
        assert data["kpiAfter"] is None

    @pytest.mark.parametrize("status", ["EXECUTING", "VERIFYING", "CLOSED", "CANCELLED"])
    def test_start_invalid_state(self, client, status: str) -> None:
        """非法迁移：start 仅允许 PENDING/REOPENED（CLOSED/CANCELLED 终态）。"""
        order = _make_order(status=status)
        _override_db(client, [_scalar_result(order)])
        resp = self._post(client, {})
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_STATE"

    def test_start_not_found(self, client) -> None:
        _override_db(client, [_scalar_result(None)])
        resp = self._post(client, {})
        assert resp.status_code == 404

    @pytest.mark.parametrize("user_key", ["admin", "ic_engineer", "pe_engineer"])
    def test_start_allowed_roles(self, client, user_key: str) -> None:
        order = _make_order(status="PENDING")
        _override_db(client, [_scalar_result(order)])
        resp = self._post(client, {}, user_key=user_key)
        assert resp.status_code == 200

    @pytest.mark.parametrize("user_key", ["sponsor", "expert"])
    def test_start_forbidden(self, client, user_key: str) -> None:
        order = _make_order(status="PENDING")
        _override_db(client, [_scalar_result(order)])
        resp = self._post(client, {}, user_key=user_key)
        assert resp.status_code == 403


class TestOrderFeedbackEndpoint:
    def _post(self, client, body: dict, user_key: str = "ic_engineer"):
        with mock_current_user(TEST_USERS[user_key]):
            return client.post(
                f"/api/v1/handling/orders/{ORDER_ID}/feedback",
                json=body,
                headers={"Authorization": "Bearer fake-token"},
            )

    def test_feedback_appends_log_keeps_status(self, client) -> None:
        """合法迁移 #3：EXECUTING 自环——追加 feedback_log，状态不变。"""
        order = _make_order(
            status="EXECUTING",
            feedback_log=[{"at": "2026-08-11T02:00:00Z", "by": "李四", "content": "已拆检阀体"}],
        )
        mock_db = _override_db(client, [_scalar_result(order)])
        resp = self._post(client, {"content": "更换填料函完成，待工艺确认"})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] == "EXECUTING"
        assert len(data["feedbackLog"]) == 2
        assert data["feedbackLog"][0]["content"] == "已拆检阀体"
        assert data["feedbackLog"][1]["by"] == "ic_engineer"
        assert data["feedbackLog"][1]["content"] == "更换填料函完成，待工艺确认"
        mock_db.commit.assert_awaited()

    def test_feedback_first_entry(self, client) -> None:
        order = _make_order(status="EXECUTING", feedback_log=None)
        _override_db(client, [_scalar_result(order)])
        resp = self._post(client, {"content": "开工确认"})
        assert resp.status_code == 200
        assert len(resp.json()["data"]["feedbackLog"]) == 1

    def test_feedback_missing_content(self, client) -> None:
        order = _make_order(status="EXECUTING")
        _override_db(client, [_scalar_result(order)])
        resp = self._post(client, {})
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_PARAM"

    @pytest.mark.parametrize("status", ["PENDING", "VERIFYING", "CLOSED", "REOPENED", "CANCELLED"])
    def test_feedback_invalid_state(self, client, status: str) -> None:
        """非法迁移：feedback 仅允许 EXECUTING。"""
        order = _make_order(status=status)
        _override_db(client, [_scalar_result(order)])
        resp = self._post(client, {"content": "x"})
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_STATE"

    def test_feedback_forbidden_sponsor(self, client) -> None:
        order = _make_order(status="EXECUTING")
        _override_db(client, [_scalar_result(order)])
        resp = self._post(client, {"content": "x"}, user_key="sponsor")
        assert resp.status_code == 403


class TestOrderSubmitEndpoint:
    def _post(self, client, body: dict, user_key: str = "ic_engineer"):
        with mock_current_user(TEST_USERS[user_key]):
            return client.post(
                f"/api/v1/handling/orders/{ORDER_ID}/submit",
                json=body,
                headers={"Authorization": "Bearer fake-token"},
            )

    def test_submit_ok_merges_existing_detail(self, client) -> None:
        """合法迁移 #4：EXECUTING → VERIFYING；提交详情与先填详情合并（保留 pidBefore）。"""
        order = _make_order(
            status="EXECUTING",
            action_type="TUNING",
            action_detail={"pidBefore": {"p": 1.2, "i": 20, "d": 0}},
            started_at=STARTED_AT,
        )
        _override_db(client, [_scalar_result(order)])
        resp = self._post(
            client,
            {"actionDetail": {"pidAfter": {"p": 0.8, "i": 35, "d": 0}, "method": "Lambda 整定法"}},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] == "VERIFYING"
        assert data["statusLabel"] == "验证中"
        assert data["submittedAt"] is not None and data["submittedAt"].endswith("Z")
        assert data["actionDetail"]["pidBefore"] == {"p": 1.2, "i": 20, "d": 0}
        assert data["actionDetail"]["pidAfter"] == {"p": 0.8, "i": 35, "d": 0}
        assert data["actionDetail"]["method"] == "Lambda 整定法"

    def test_submit_non_tuning_ok(self, client) -> None:
        """非 TUNING 类型：仅要求非空对象（§5.2）。"""
        order = _make_order(status="EXECUTING", action_type="VALVE", started_at=STARTED_AT)
        _override_db(client, [_scalar_result(order)])
        resp = self._post(client, {"actionDetail": {"parts": "更换填料函", "downtimeHours": 4}})
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "VERIFYING"

    def test_submit_tuning_missing_pid_after(self, client) -> None:
        """TUNING 类型 submit 时 pidAfter 必填（服务端校验）。"""
        order = _make_order(
            status="EXECUTING",
            action_type="TUNING",
            action_detail={"pidBefore": {"p": 1.2, "i": 20, "d": 0}},
            started_at=STARTED_AT,
        )
        _override_db(client, [_scalar_result(order)])
        resp = self._post(client, {"actionDetail": {"method": "Lambda 整定法"}})
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_PARAM"
        assert "pidAfter" in resp.json()["message"]

    def test_submit_empty_detail(self, client) -> None:
        order = _make_order(status="EXECUTING", action_type="OTHER", started_at=STARTED_AT)
        _override_db(client, [_scalar_result(order)])
        resp = self._post(client, {"actionDetail": {}})
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_PARAM"

    @pytest.mark.parametrize("status", ["PENDING", "VERIFYING", "CLOSED", "REOPENED", "CANCELLED"])
    def test_submit_invalid_state(self, client, status: str) -> None:
        """非法迁移：submit 仅允许 EXECUTING。"""
        order = _make_order(status=status)
        _override_db(client, [_scalar_result(order)])
        resp = self._post(client, {"actionDetail": {"note": "x"}})
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_STATE"

    def test_submit_forbidden_sponsor(self, client) -> None:
        order = _make_order(status="EXECUTING", action_type="OTHER", started_at=STARTED_AT)
        _override_db(client, [_scalar_result(order)])
        resp = self._post(client, {"actionDetail": {"note": "x"}}, user_key="sponsor")
        assert resp.status_code == 403


class TestOrderVerifyEndpoint:
    def _post(self, client, body: dict, user_key: str = "ic_engineer"):
        with mock_current_user(TEST_USERS[user_key]):
            return client.post(
                f"/api/v1/handling/orders/{ORDER_ID}/verify",
                json=body,
                headers={"Authorization": "Bearer fake-token"},
            )

    @staticmethod
    def _verifying_order() -> HandlingOrder:
        return _make_order(
            status="VERIFYING",
            action_type="TUNING",
            action_detail={"pidBefore": {"p": 1.2}, "pidAfter": {"p": 0.8}},
            handler="ic_engineer",
            started_at=STARTED_AT,
            submitted_at=SUBMITTED_AT,
        )

    def test_verify_effective_closes_with_kpi_snapshot(self, client) -> None:
        """合法迁移 #5：VERIFYING → CLOSED；服务端固化 kpi_before/after（前窗 started_at 口径）。"""
        order = self._verifying_order()
        snap_before = _make_snapshot(
            score="72.30", ts_start=datetime(2026, 8, 10, 7, 0, 0), ts_end=STARTED_AT
        )
        snap_after = _make_snapshot(
            score="88.60",
            ts_start=datetime(2026, 8, 13, 9, 0, 0),
            ts_end=datetime(2026, 8, 13, 10, 0, 0),
            confidence="A",
        )
        _override_db(
            client,
            [_scalar_result(order), _scalar_result(snap_before), _scalar_result(snap_after)],
        )
        resp = self._post(client, {"verifyResult": "EFFECTIVE", "verifyNote": "评分回升"})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] == "CLOSED"
        assert data["statusLabel"] == "已闭环"
        assert data["verifyResult"] == "EFFECTIVE"
        assert data["verifyResultLabel"] == "有效"
        assert data["verifyNote"] == "评分回升"
        assert data["verifiedBy"] == "ic_engineer"
        assert data["verifiedAt"] is not None and data["verifiedAt"].endswith("Z")
        # KPI 固化：摘要字段口径 §4.3（score + 六率 + 可信度 + 窗口）
        assert data["kpiBefore"]["score"] == pytest.approx(72.3)
        assert data["kpiBefore"]["confidenceLevel"] == "B"
        assert data["kpiBefore"]["effectiveAutoRate"] == pytest.approx(86.7)
        assert data["kpiBefore"]["tsStart"].endswith("Z")
        assert data["kpiAfter"]["score"] == pytest.approx(88.6)
        assert data["kpiAfter"]["confidenceLevel"] == "A"

    def test_verify_ineffective_reopens(self, client) -> None:
        """合法迁移 #6：VERIFYING → REOPENED（验证无效，可再次开工）。"""
        order = self._verifying_order()
        _override_db(client, [_scalar_result(order), _scalar_result(None), _scalar_result(None)])
        resp = self._post(client, {"verifyResult": "INEFFECTIVE", "verifyNote": "振荡未消除"})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] == "REOPENED"
        assert data["statusLabel"] == "重开"
        assert data["verifyResult"] == "INEFFECTIVE"
        # 窗口无快照：对应侧为 None（前端对比卡显示"数据不足"）
        assert data["kpiBefore"] is None
        assert data["kpiAfter"] is None

    def test_verify_kpi_window_boundaries_started_at(self, client) -> None:
        """KPI 窗口口径（§4.3 v2.0）：前窗 [started_at−24h, started_at]，
        后窗 [submitted_at, submitted_at+24h]，处置执行期数据隔离。"""
        order = self._verifying_order()
        captured: list = []
        _capture_override_db(
            client, [_scalar_result(order), _scalar_result(None), _scalar_result(None)], captured
        )
        resp = self._post(client, {"verifyResult": "EFFECTIVE"})
        assert resp.status_code == 200
        # 第 1 条 SQL：取工单；第 2 条：前窗快照；第 3 条：后窗快照
        before_sql = _pg_sql(captured[1])
        after_sql = _pg_sql(captured[2])
        # 前窗 [2026-08-09 08:00, 2026-08-10 08:00]（started_at 口径）
        assert "2026-08-09 08:00:00" in before_sql
        assert "2026-08-10 08:00:00" in before_sql
        # 后窗 [2026-08-12 09:30, 2026-08-13 09:30]
        assert "2026-08-12 09:30:00" in after_sql
        assert "2026-08-13 09:30:00" in after_sql

    def test_verify_with_run_id(self, client) -> None:
        """verifyRunId 关联复诊记录（存在性校验通过后落库）。"""
        order = self._verifying_order()
        revisit_run_id = str(uuid4())
        _override_db(
            client,
            [
                _scalar_result(order),
                _scalar_result(revisit_run_id),  # diagnosis_run 存在性校验
                _scalar_result(None),
                _scalar_result(None),
            ],
        )
        resp = self._post(client, {"verifyResult": "EFFECTIVE", "verifyRunId": revisit_run_id})
        assert resp.status_code == 200
        assert resp.json()["data"]["verifyRunId"] == revisit_run_id

    def test_verify_run_id_not_found(self, client) -> None:
        order = self._verifying_order()
        _override_db(client, [_scalar_result(order), _scalar_result(None)])
        resp = self._post(client, {"verifyResult": "EFFECTIVE", "verifyRunId": str(uuid4())})
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_PARAM"

    def test_verify_invalid_result(self, client) -> None:
        order = self._verifying_order()
        _override_db(client, [_scalar_result(order)])
        resp = self._post(client, {"verifyResult": "MAYBE"})
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_PARAM"

    @pytest.mark.parametrize("status", ["PENDING", "EXECUTING", "CLOSED", "REOPENED", "CANCELLED"])
    def test_verify_invalid_state(self, client, status: str) -> None:
        """非法迁移：verify 仅允许 VERIFYING（CLOSED 终态不可再验证）。"""
        order = _make_order(status=status)
        _override_db(client, [_scalar_result(order)])
        resp = self._post(client, {"verifyResult": "EFFECTIVE"})
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_STATE"

    def test_verify_forbidden_sponsor(self, client) -> None:
        order = self._verifying_order()
        _override_db(client, [_scalar_result(order)])
        resp = self._post(client, {"verifyResult": "EFFECTIVE"}, user_key="sponsor")
        assert resp.status_code == 403


class TestOrderCancelEndpoint:
    def _post(self, client, body: dict, user_key: str = "ic_engineer"):
        with mock_current_user(TEST_USERS[user_key]):
            return client.post(
                f"/api/v1/handling/orders/{ORDER_ID}/cancel",
                json=body,
                headers={"Authorization": "Bearer fake-token"},
            )

    def test_cancel_ok(self, client) -> None:
        """合法迁移 #2：PENDING → CANCELLED 终态；cancelReason 必填。"""
        order = _make_order(status="PENDING")
        mock_db = _override_db(client, [_scalar_result(order)])
        resp = self._post(client, {"cancelReason": "检修计划变更，并入下月窗口"})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] == "CANCELLED"
        assert data["statusLabel"] == "已作废"
        assert data["cancelReason"] == "检修计划变更，并入下月窗口"
        mock_db.commit.assert_awaited()

    def test_cancel_missing_reason(self, client) -> None:
        order = _make_order(status="PENDING")
        _override_db(client, [_scalar_result(order)])
        resp = self._post(client, {})
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_PARAM"

    @pytest.mark.parametrize("status", ["EXECUTING", "VERIFYING", "CLOSED", "REOPENED"])
    def test_cancel_invalid_state(self, client, status: str) -> None:
        """非法迁移：cancel 仅允许 PENDING（已开工不可作废，应走验证/重开）。"""
        order = _make_order(status=status)
        _override_db(client, [_scalar_result(order)])
        resp = self._post(client, {"cancelReason": "x"})
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_STATE"

    def test_cancel_forbidden_sponsor(self, client) -> None:
        order = _make_order(status="PENDING")
        _override_db(client, [_scalar_result(order)])
        resp = self._post(client, {"cancelReason": "x"}, user_key="sponsor")
        assert resp.status_code == 403


class TestOrderKpiComparisonEndpoint:
    def _post(self, client, user_key: str = "ic_engineer"):
        with mock_current_user(TEST_USERS[user_key]):
            return client.post(
                f"/api/v1/handling/orders/{ORDER_ID}/kpi-comparison",
                headers={"Authorization": "Bearer fake-token"},
            )

    def test_preview_ok_not_persisted(self, client) -> None:
        """VERIFYING 阶段实时拉取前后窗口 KPI（不落库，verify 时才固化）。"""
        order = _make_order(
            status="VERIFYING",
            action_type="TUNING",
            started_at=STARTED_AT,
            submitted_at=SUBMITTED_AT,
        )
        snap_before = _make_snapshot(
            score="72.30", ts_start=datetime(2026, 8, 10, 7, 0, 0), ts_end=STARTED_AT
        )
        mock_db = _override_db(
            client, [_scalar_result(order), _scalar_result(snap_before), _scalar_result(None)]
        )
        resp = self._post(client)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["orderNo"] == "HD-20260820-001"
        assert data["kpiBefore"]["score"] == pytest.approx(72.3)
        assert data["kpiAfter"] is None
        # 窗口字段：前窗以 started_at 为界，后窗以 submitted_at 为界（v2.0 口径）
        assert data["window"]["beforeStart"] == "2026-08-09T08:00:00Z"
        assert data["window"]["beforeEnd"] == "2026-08-10T08:00:00Z"
        assert data["window"]["afterStart"] == "2026-08-12T09:30:00Z"
        assert data["window"]["afterEnd"] == "2026-08-13T09:30:00Z"
        # 预览不落库：不 commit，行状态与 kpi 字段不变
        mock_db.commit.assert_not_awaited()
        assert order.kpi_before is None
        assert order.status == "VERIFYING"

    @pytest.mark.parametrize("status", ["PENDING", "EXECUTING", "CLOSED", "REOPENED", "CANCELLED"])
    def test_preview_invalid_state(self, client, status: str) -> None:
        order = _make_order(status=status)
        _override_db(client, [_scalar_result(order)])
        resp = self._post(client)
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_STATE"

    def test_preview_forbidden_sponsor(self, client) -> None:
        order = _make_order(status="VERIFYING", started_at=STARTED_AT, submitted_at=SUBMITTED_AT)
        _override_db(client, [_scalar_result(order)])
        resp = self._post(client, user_key="sponsor")
        assert resp.status_code == 403


# ===========================================================================
# 工单侧：手动新建 / 清单 / 详情（§6.2）
# ===========================================================================


class TestCreateOrderEndpoint:
    def _post(self, client, body: dict, user_key: str = "ic_engineer"):
        with mock_current_user(TEST_USERS[user_key]):
            return client.post(
                "/api/v1/handling/orders",
                json=body,
                headers={"Authorization": "Bearer fake-token"},
            )

    def test_create_ok_manual_source(self, client) -> None:
        """手动新建：source=MANUAL、order_no 自动生成、title 缺省取 content 前 50 字。"""
        loop_row = MagicMock()
        mock_db = _override_db(client, [_scalar_result(loop_row), _scalar_result(0)])
        resp = self._post(
            client,
            {
                "loopId": LOOP_ID,
                "actionType": "VALVE",
                "content": "调节阀 FV-5121 填料函渗漏，计划借停工窗口检修更换填料并研磨阀芯",
                "handler": "仪表班-李四",
            },
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        today = datetime.now(UTC).strftime("%Y%m%d")
        assert data["orderNo"] == f"HD-{today}-001"
        assert data["source"] == "MANUAL"
        assert data["status"] == "PENDING"
        assert data["plannedBy"] == "ic_engineer"
        assert (
            data["title"] == "调节阀 FV-5121 填料函渗漏，计划借停工窗口检修更换填料并研磨阀芯"[:50]
        )
        assert data["suggestionIds"] == []
        mock_db.commit.assert_awaited()

    def test_create_missing_title_and_content(self, client) -> None:
        """title 与 content 均空 → ERR_PARAM。"""
        loop_row = MagicMock()
        _override_db(client, [_scalar_result(loop_row)])
        resp = self._post(client, {"loopId": LOOP_ID, "actionType": "VALVE"})
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_PARAM"

    def test_create_loop_not_found(self, client) -> None:
        _override_db(client, [_scalar_result(None)])
        resp = self._post(client, {"loopId": str(uuid4()), "actionType": "VALVE", "title": "x"})
        assert resp.status_code == 400
        assert "回路不存在" in resp.json()["message"]

    def test_create_invalid_action_type(self, client) -> None:
        resp = self._post(client, {"loopId": LOOP_ID, "actionType": "BOGUS", "title": "x"})
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_PARAM"

    def test_create_forbidden_sponsor(self, client) -> None:
        resp = self._post(
            client, {"loopId": LOOP_ID, "actionType": "VALVE", "title": "x"}, user_key="sponsor"
        )
        assert resp.status_code == 403


UNIT_ID = str(uuid4())
PLANT_ID = str(uuid4())


def _all_result(rows: list) -> MagicMock:
    r = MagicMock()
    r.all.return_value = rows
    return r


def _count_result(n: int) -> MagicMock:
    r = MagicMock()
    r.scalar.return_value = n
    return r


def _one_result(row: Any) -> MagicMock:
    r = MagicMock()
    r.one.return_value = row
    return r


def _make_suggestion_list_row(**over: Any) -> MagicMock:
    """建议清单 SQL 行（ai.* + ll 回路字段 + converted_order_no）mock。"""
    row = MagicMock()
    row.id = SUGGESTION_ID
    row.run_id = RUN_ID
    row.loop_id = LOOP_ID
    row.loop_tag_name = "90PIC51212A"
    row.loop_description = "反应器压力控制"
    row.importance_level = 1
    row.unit_id = UNIT_ID
    row.source = "SYSTEM"
    row.category = "TUNING"
    row.content = "重新整定 PID 参数：当前回路存在振荡"
    row.basis = "诊断结论：参数问题（PID 整定）"
    row.priority = 1
    row.status = "PENDING"
    row.suggested_by = "系统"
    row.suggested_at = datetime(2026, 8, 9, 0, 0, 0)
    row.reviewed_by = None
    row.reviewed_at = None
    row.rejected_reason = None
    row.converted_order_id = None
    row.converted_order_no = None
    row.ignore_reason = None
    row.updated_at = datetime(2026, 8, 9, 1, 0, 0)
    for k, v in over.items():
        setattr(row, k, v)
    return row


def _make_order_list_row(**over: Any) -> MagicMock:
    """工单清单 SQL 行（ho.* + ll 回路字段）mock。"""
    row = MagicMock()
    row.id = ORDER_ID
    row.order_no = "HD-20260820-001"
    row.loop_id = LOOP_ID
    row.loop_tag_name = "90PIC51212A"
    row.loop_description = "反应器压力控制"
    row.importance_level = 1
    row.unit_id = UNIT_ID
    row.source = "DIAGNOSIS"
    row.suggestion_ids = [SUGGESTION_ID]
    row.title = "反应器压力控制处置"
    row.action_type = "TUNING"
    row.planned_at = None
    row.planned_by = "admin"
    row.handler = "ic_engineer"
    row.started_at = STARTED_AT
    row.feedback_log = [{"at": "2026-08-11T02:00:00Z", "by": "李四", "content": "已拆检"}]
    row.submitted_at = None
    row.verify_result = None
    row.verified_by = None
    row.verified_at = None
    row.cancel_reason = None
    row.status = "EXECUTING"
    row.updated_at = datetime(2026, 8, 11, 6, 0, 0)
    for k, v in over.items():
        setattr(row, k, v)
    return row


def _plant_node_rows() -> list:
    plant = MagicMock()
    plant.id = PLANT_ID
    plant.name = "一联合装置"
    plant.parent_id = None
    unit = MagicMock()
    unit.id = UNIT_ID
    unit.name = "常减压单元"
    unit.parent_id = PLANT_ID
    return [plant, unit]


class TestSuggestionListEndpoint:
    """GET /api/v1/handling/suggestions（§6.1 建议清单）。"""

    def _get(self, client, params: dict | None = None, user_key: str = "admin"):
        with mock_current_user(TEST_USERS[user_key]):
            return client.get(
                "/api/v1/handling/suggestions",
                params=params or {},
                headers={"Authorization": "Bearer fake-token"},
            )

    def test_list_ok_with_converted_order_no(self, client) -> None:
        """清单行字段 + unitPath + convertedOrderNo（CONVERTED 行回显工单编号）。"""
        _override_db(
            client,
            [
                _count_result(1),
                _all_result(
                    [
                        _make_suggestion_list_row(
                            status="CONVERTED", converted_order_no="HD-20260820-001"
                        )
                    ]
                ),
                _all_result(_plant_node_rows()),
            ],
        )
        resp = self._get(client)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 1
        item = data["items"][0]
        assert item["loopTagName"] == "90PIC51212A"
        assert item["importanceLevel"] == 1
        assert item["unitPath"] == "一联合装置.常减压单元"
        assert item["statusLabel"] == "已转工单"
        assert item["convertedOrderNo"] == "HD-20260820-001"
        assert item["categoryLabel"] == "参数问题（PID 整定）"
        assert item["suggestedAt"].endswith("Z")

    def test_list_status_filter_and_order_sql(self, client) -> None:
        """status 多值过滤 + 排序口径：状态分组优先级 + suggested_at DESC（§6.1）。"""
        captured: list = []
        _capture_override_db(
            client,
            [_count_result(0), _all_result([]), _all_result(_plant_node_rows())],
            captured,
        )
        resp = self._get(client, {"status": "PENDING,REOPENED"})
        assert resp.status_code == 200
        count_sql = _pg_sql(captured[0])
        assert "ai.status = ANY" in count_sql
        list_sql = _pg_sql(captured[1])
        assert "CASE ai.status" in list_sql
        assert "suggested_at DESC" in list_sql

    def test_list_keyword_and_source_filters(self, client) -> None:
        captured: list = []
        _capture_override_db(
            client,
            [_count_result(0), _all_result([]), _all_result(_plant_node_rows())],
            captured,
        )
        resp = self._get(client, {"keyword": "90PIC", "source": "MANUAL"})
        assert resp.status_code == 200
        sql = _pg_sql(captured[0])
        assert "ILIKE" in sql
        assert "ai.source = " in sql

    def test_list_plant_node_recursion(self, client) -> None:
        """plantNodeId 先递归取子树，再以 unit_id ANY 过滤。"""
        captured: list = []
        subtree_row = MagicMock()
        subtree_row.id = UNIT_ID
        _capture_override_db(
            client,
            [
                _all_result([subtree_row]),
                _count_result(0),
                _all_result([]),
                _all_result(_plant_node_rows()),
            ],
            captured,
        )
        resp = self._get(client, {"plantNodeId": PLANT_ID})
        assert resp.status_code == 200
        assert "WITH RECURSIVE node_tree" in _pg_sql(captured[0])
        assert "ll.unit_id = ANY" in _pg_sql(captured[1])

    def test_list_all_roles_can_view(self, client) -> None:
        """清单查看：全部登录用户（含 SPONSOR 只读，§7）。"""
        _override_db(
            client,
            [_count_result(0), _all_result([]), _all_result(_plant_node_rows())],
        )
        resp = self._get(client, user_key="sponsor")
        assert resp.status_code == 200


class TestOrderListEndpoint:
    """GET /api/v1/handling/orders（§6.2 工单清单）。"""

    def _get(self, client, params: dict | None = None, user_key: str = "admin"):
        with mock_current_user(TEST_USERS[user_key]):
            return client.get(
                "/api/v1/handling/orders",
                params=params or {},
                headers={"Authorization": "Bearer fake-token"},
            )

    def test_list_ok_mapping(self, client) -> None:
        """清单行映射：orderNo/statusLabel/unitPath/feedbackCount。"""
        _override_db(
            client,
            [
                _count_result(1),
                _all_result([_make_order_list_row()]),
                _all_result(_plant_node_rows()),
            ],
        )
        resp = self._get(client)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 1
        item = data["items"][0]
        assert item["orderNo"] == "HD-20260820-001"
        assert item["statusLabel"] == "执行中"
        assert item["actionTypeLabel"] == "参数整定"
        assert item["unitPath"] == "一联合装置.常减压单元"
        assert item["feedbackCount"] == 1
        assert item["handler"] == "ic_engineer"

    def test_list_filters_sql(self, client) -> None:
        """筛选口径：handler 模糊 + keyword（编号/回路/标题）+ 排序状态分组。"""
        captured: list = []
        _capture_override_db(
            client,
            [_count_result(0), _all_result([]), _all_result(_plant_node_rows())],
            captured,
        )
        resp = self._get(client, {"handler": "仪表班", "keyword": "HD-2026", "status": "EXECUTING"})
        assert resp.status_code == 200
        sql = _pg_sql(captured[0])
        assert "ho.handler ILIKE" in sql
        assert "ho.order_no ILIKE" in sql
        assert "ho.status = " in sql
        list_sql = _pg_sql(captured[1])
        assert "CASE ho.status" in list_sql
        assert "updated_at DESC" in list_sql

    def test_list_planned_window_filter(self, client) -> None:
        captured: list = []
        _capture_override_db(
            client,
            [_count_result(0), _all_result([]), _all_result(_plant_node_rows())],
            captured,
        )
        resp = self._get(
            client,
            {"plannedAfter": "2026-08-20T00:00:00Z", "plannedBefore": "2026-08-30T00:00:00Z"},
        )
        assert resp.status_code == 200
        sql = _pg_sql(captured[0])
        assert "ho.planned_at >= " in sql
        assert "ho.planned_at <= " in sql

    def test_list_all_roles_can_view(self, client) -> None:
        _override_db(
            client,
            [_count_result(0), _all_result([]), _all_result(_plant_node_rows())],
        )
        resp = self._get(client, user_key="expert")
        assert resp.status_code == 200


class TestOrderDetailEndpoint:
    """GET /api/v1/handling/orders/{id}（§6.2 工单详情）。"""

    def _get(self, client, user_key: str = "admin"):
        with mock_current_user(TEST_USERS[user_key]):
            return client.get(
                f"/api/v1/handling/orders/{ORDER_ID}",
                headers={"Authorization": "Bearer fake-token"},
            )

    @staticmethod
    def _loop_row() -> MagicMock:
        loop = MagicMock()
        loop.id = LOOP_ID
        loop.tag_name = "90PIC51212A"
        loop.description = "反应器压力控制"
        loop.importance_level = 1
        loop.unit_id = UNIT_ID
        return loop

    def test_detail_ok_with_suggestions(self, client) -> None:
        """详情：工单全字段 + 回路信息 + 来源建议摘要（suggestion_ids 解析）。"""
        order = _make_order(
            status="VERIFYING",
            action_detail={"pidBefore": {"p": 1.2}, "pidAfter": {"p": 0.8}},
            started_at=STARTED_AT,
            submitted_at=SUBMITTED_AT,
        )
        sug = _make_suggestion(status="CONVERTED")
        _override_db(
            client,
            [
                _scalar_result(order),
                _scalar_result(self._loop_row()),
                _all_result(_plant_node_rows()),
                _scalars_all_result([sug]),
            ],
        )
        resp = self._get(client)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["orderNo"] == "HD-20260820-001"
        assert data["loopTagName"] == "90PIC51212A"
        assert data["unitPath"] == "一联合装置.常减压单元"
        assert data["actionDetail"]["pidAfter"] == {"p": 0.8}
        assert len(data["suggestions"]) == 1
        assert data["suggestions"][0]["id"] == SUGGESTION_ID
        assert data["suggestions"][0]["statusLabel"] == "已转工单"

    def test_detail_manual_order_no_suggestions(self, client) -> None:
        """MANUAL 工单无来源建议：suggestions 为空数组。"""
        order = _make_order(status="PENDING", source="MANUAL", suggestion_ids=[])
        _override_db(
            client,
            [
                _scalar_result(order),
                _scalar_result(self._loop_row()),
                _all_result(_plant_node_rows()),
            ],
        )
        resp = self._get(client)
        assert resp.status_code == 200
        assert resp.json()["data"]["suggestions"] == []

    def test_detail_not_found(self, client) -> None:
        _override_db(client, [_scalar_result(None)])
        resp = self._get(client)
        assert resp.status_code == 404
        assert resp.json()["code"] == "ERR_NOT_FOUND"

    def test_detail_malformed_id_rejected(self, client) -> None:
        """畸形非 UUID orderId → ERR_PARAM 400（避免 asyncpg UUID 解析异常吐 500）。"""
        _override_db(client, [])
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/handling/orders/not-a-uuid",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_PARAM"


# ===========================================================================
# 聚合与统计（§6.3，双实体口径）
# ===========================================================================


def _make_loop_agg_row(**over: Any) -> MagicMock:
    """回路聚合行 mock（聚合 SQL 各列；_total 为窗口函数 COUNT(*) OVER() 携带的总数）。"""
    row = MagicMock()
    row._total = 1
    row.loop_id = LOOP_ID
    row.loop_tag_name = "90PIC51212A_PIDA"
    row.loop_description = "辛醇罐TK521A顶部压力"
    row.importance_level = 1
    row.unit_id = UNIT_ID
    row.su_pending = 2
    row.su_accepted = 1
    row.su_converted = 1
    row.su_rejected = 0
    row.su_ignored = 1
    row.suggestion_total = 5
    row.last_suggested_at = datetime(2026, 8, 18, 6, 0, 0)
    row.ho_pending = 1
    row.ho_executing = 0
    row.ho_verifying = 0
    row.ho_closed = 3
    row.ho_reopened = 1
    row.ho_cancelled = 0
    row.order_total = 5
    row.ho_verified = 4
    row.ho_ineffective = 1
    row.last_handled_at = datetime(2026, 8, 18, 9, 0, 0)
    row.last_order_at = datetime(2026, 8, 18, 10, 0, 0)
    row.last_handled_by = "mock-仪控班"
    row.last_closed_kpi_delta = 18.2
    for k, v in over.items():
        setattr(row, k, v)
    return row


class TestLoopsEndpoint:
    """GET /api/v1/handling/loops（§6.3 双实体档案聚合）。"""

    def _get(self, client, params: dict | None = None, user_key: str = "admin"):
        with mock_current_user(TEST_USERS[user_key]):
            return client.get(
                "/api/v1/handling/loops",
                params=params or {},
                headers={"Authorization": "Bearer fake-token"},
            )

    def test_loops_ok_dual_entity_mapping(self, client) -> None:
        """聚合行映射：建议五态 + 工单六态 + closeRate + lastClosedKpiDelta。"""
        # 窗口函数合并后：分页查询（携带 _total）→ 单位路径，无独立 COUNT 轮次
        _override_db(
            client,
            [
                _all_result([_make_loop_agg_row()]),
                _all_result(_plant_node_rows()),
            ],
        )
        resp = self._get(client)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 1
        item = data["items"][0]
        assert item["loopId"] == LOOP_ID
        assert item["unitPath"] == "一联合装置.常减压单元"
        assert item["suggestionCounts"] == {
            "pending": 2,
            "accepted": 1,
            "converted": 1,
            "rejected": 0,
            "ignored": 1,
        }
        assert item["suggestionTotal"] == 5
        assert item["orderCounts"] == {
            "pending": 1,
            "executing": 0,
            "verifying": 0,
            "closed": 3,
            "reopened": 1,
            "cancelled": 0,
        }
        assert item["orderTotal"] == 5
        assert item["closeRate"] == pytest.approx(0.75)  # 3 closed / 4 verified
        assert item["lastClosedKpiDelta"] == pytest.approx(18.2)
        assert item["lastHandledBy"] == "mock-仪控班"
        assert item["lastSuggestedAt"].endswith("Z")

    def test_loops_plant_node_filter_sql(self, client) -> None:
        """plantNodeId 过滤：外层 WHERE 必须引用内层子查询别名 base。

        回归（批次 D）：外层误引子查询内部别名 ll → missing FROM-clause 500。
        """
        captured: list = []
        subtree_row = MagicMock()
        subtree_row.id = PLANT_ID
        _capture_override_db(
            client,
            [
                _all_result([subtree_row]),  # 递归子树
                _all_result([_make_loop_agg_row()]),  # 分页聚合（携带 _total）
                _all_result(_plant_node_rows()),  # 单位路径
            ],
            captured,
        )
        resp = self._get(client, {"plantNodeId": PLANT_ID})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 1
        assert len(data["items"]) == 1
        assert data["items"][0]["loopId"] == LOOP_ID
        sql = captured[1].text
        assert "base.unit_id = ANY(CAST(:unit_ids AS uuid[]))" in sql
        assert "ll.unit_id = ANY" not in sql

    def test_loops_importance_level_filter_sql(self, client) -> None:
        """importanceLevel 过滤：外层 WHERE 同样必须引用 base 别名（同批回归）。"""
        captured: list = []
        _capture_override_db(
            client,
            [
                _all_result([_make_loop_agg_row()]),
                _all_result(_plant_node_rows()),
            ],
            captured,
        )
        resp = self._get(client, {"importanceLevel": 1})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 1
        assert len(data["items"]) == 1
        assert data["items"][0]["importanceLevel"] == 1
        sql = captured[0].text
        assert "base.importance_level = :importance_level" in sql
        assert "ll.importance_level =" not in sql

    def test_loops_status_distribution_filter_sql(self, client) -> None:
        """状态分布筛选：建议/工单该状态计数>0 任一命中（HAVING 语义）。"""
        captured: list = []
        _capture_override_db(
            client,
            [_all_result([]), _all_result(_plant_node_rows())],
            captured,
        )
        resp = self._get(client, {"status": "PENDING,REOPENED"})
        assert resp.status_code == 200
        sql = _pg_sql(captured[0])
        # PENDING 为 OR 组（建议/工单任一命中）且带括号；REOPENED 为独立 AND 条件
        assert "(COALESCE(agg.su_pending, 0) > 0 OR COALESCE(agg.ho_pending, 0) > 0)" in sql
        assert "AND (COALESCE(agg.ho_reopened, 0) > 0)" in sql

    @pytest.mark.parametrize(
        ("kpi_delta", "frag"),
        [
            ("improved", "agg.last_closed_kpi_delta > 0"),
            ("degraded", "agg.last_closed_kpi_delta < 0"),
            ("closed", "agg.ho_closed > 0"),
            ("unclosed", "COALESCE(agg.ho_closed, 0) = 0"),
        ],
    )
    def test_loops_kpi_delta_filter_sql(self, client, kpi_delta: str, frag: str) -> None:
        """KPI 改善筛选：四档口径断言（工单口径）。"""
        captured: list = []
        _capture_override_db(
            client,
            [_all_result([]), _all_result(_plant_node_rows())],
            captured,
        )
        resp = self._get(client, {"kpiDelta": kpi_delta})
        assert resp.status_code == 200
        assert frag in _pg_sql(captured[0])

    def test_loops_active_only_sql(self, client) -> None:
        """在途口径：待审核/已接受建议 + 非终态工单 >0。"""
        captured: list = []
        _capture_override_db(
            client,
            [_all_result([]), _all_result(_plant_node_rows())],
            captured,
        )
        resp = self._get(client, {"activeOnly": "true"})
        assert resp.status_code == 200
        sql = _pg_sql(captured[0])
        assert "agg.su_pending" in sql
        assert "agg.ho_reopened" in sql

    def test_loops_sort_reopened_sql(self, client) -> None:
        """reopened 排序：工单重开 → 无效 → 工单总数（问题回路 Top）。"""
        captured: list = []
        _capture_override_db(
            client,
            [_all_result([]), _all_result(_plant_node_rows())],
            captured,
        )
        resp = self._get(client, {"sort": "reopened"})
        assert resp.status_code == 200
        sql = _pg_sql(captured[0])
        assert "ho_reopened DESC" in sql
        assert "ho_ineffective DESC" in sql

    def test_loops_recent_sort_default(self, client) -> None:
        """recent 排序：建议/工单最近活动时间取大。"""
        captured: list = []
        _capture_override_db(
            client,
            [_all_result([]), _all_result(_plant_node_rows())],
            captured,
        )
        resp = self._get(client)
        assert resp.status_code == 200
        sql = _pg_sql(captured[0])
        assert "GREATEST" in sql
        assert "last_suggested_at" in sql
        assert "last_order_at" in sql

    def test_loops_invalid_status_value(self, client) -> None:
        """非法状态值 → ERR_PARAM 400。"""
        resp = self._get(client, {"status": "HANDLING"})
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_PARAM"

    def test_loops_invalid_kpi_delta(self, client) -> None:
        resp = self._get(client, {"kpiDelta": "bogus"})
        assert resp.status_code == 422

    def test_loops_all_roles_can_view(self, client) -> None:
        _override_db(
            client,
            [_all_result([]), _all_result(_plant_node_rows())],
        )
        resp = self._get(client, user_key="sponsor")
        assert resp.status_code == 200


class TestStatisticsEndpoint:
    """GET /api/v1/handling/statistics（§6.3 工单维度统计）。"""

    def _get(self, client, params: dict | None = None, user_key: str = "admin"):
        with mock_current_user(TEST_USERS[user_key]):
            return client.get(
                "/api/v1/handling/statistics",
                params=params or {},
                headers={"Authorization": "Bearer fake-token"},
            )

    @staticmethod
    def _summary_row(**over: Any) -> MagicMock:
        row = MagicMock()
        row.closed_this_month = 3
        row.closed_total = 6
        row.verified_total = 8
        row.ineffective_total = 2
        row.avg_cycle_hours = Decimal("36.5")
        row.avg_schedule_hours = Decimal("12.3")
        row.avg_kpi_delta = Decimal("12.4")
        for k, v in over.items():
            setattr(row, k, v)
        return row

    @staticmethod
    def _reject_row(**over: Any) -> MagicMock:
        row = MagicMock()
        row.reviewed_total = 10
        row.rejected_total = 2
        for k, v in over.items():
            setattr(row, k, v)
        return row

    def _full_results(
        self,
        summary: MagicMock,
        reject: MagicMock,
        monthly_rows: list | None = None,
    ) -> list:
        from collections import namedtuple
        from datetime import timedelta
        from datetime import timezone as dt_timezone

        MRow = namedtuple("MRow", ["month", "closed", "verified"])
        TRow = namedtuple("TRow", ["action_type", "cnt"])
        URow = namedtuple("URow", ["unit", "closed"])
        TopRow = MagicMock()
        TopRow.loop_id = LOOP_ID
        TopRow.loop_tag_name = "90PIC51212A_PIDA"
        TopRow.importance_level = 1
        TopRow.unit_id = UNIT_ID
        TopRow.order_total = 5
        TopRow.ho_reopened = 1
        TopRow.ho_ineffective = 1
        TopRow.last_closed_kpi_delta = 18.2
        now_month = datetime.now(dt_timezone(timedelta(hours=8))).strftime("%Y-%m")
        if monthly_rows is None:
            monthly_rows = [MRow(now_month, 3, 4)]
        return [
            _one_result(summary),
            _one_result(reject),
            _all_result(monthly_rows),
            _all_result([TRow("TUNING", 5), TRow("VALVE", 2)]),
            _all_result([URow("一联合装置", 4)]),
            _all_result([TopRow]),
            _all_result(_plant_node_rows()),
        ]

    def test_statistics_full(self, client) -> None:
        """summary 比率/驳回率/排程周期 + monthly 空月补齐 + topLoops 工单口径。"""
        _override_db(
            client,
            self._full_results(self._summary_row(), self._reject_row()),
        )
        resp = self._get(client, {"months": 6})
        assert resp.status_code == 200
        data = resp.json()["data"]
        s = data["summary"]
        assert s["closedThisMonth"] == 3
        assert s["closeRate"] == pytest.approx(0.75)
        assert s["ineffectiveRate"] == pytest.approx(0.25)
        assert s["avgCycleHours"] == pytest.approx(36.5)
        assert s["avgScheduleHours"] == pytest.approx(12.3)
        assert s["avgKpiDelta"] == pytest.approx(12.4)
        assert s["rejectRate"] == pytest.approx(0.2)  # 2 rejected / 10 reviewed
        assert len(data["monthly"]) == 6
        assert data["monthly"][0]["closed"] == 0
        assert data["monthly"][0]["closeRate"] is None
        assert data["byType"][0] == {"count": 5, "label": "参数整定", "type": "TUNING"}
        assert data["byUnit"][0] == {"closed": 4, "unit": "一联合装置"}
        assert data["topLoops"][0]["unitPath"] == "一联合装置.常减压单元"
        assert data["topLoops"][0]["orderTotal"] == 5
        assert data["topLoops"][0]["reopened"] == 1

    def test_statistics_empty_no_verified(self, client) -> None:
        """无验证记录：比率/时长/改善/驳回率为 null，不返回误导性 0。"""
        _override_db(
            client,
            self._full_results(
                self._summary_row(
                    closed_this_month=0,
                    closed_total=0,
                    verified_total=0,
                    ineffective_total=0,
                    avg_cycle_hours=None,
                    avg_schedule_hours=None,
                    avg_kpi_delta=None,
                ),
                self._reject_row(reviewed_total=0, rejected_total=0),
                monthly_rows=[],
            ),
        )
        resp = self._get(client)
        assert resp.status_code == 200
        s = resp.json()["data"]["summary"]
        assert s["closedThisMonth"] == 0
        assert s["closeRate"] is None
        assert s["avgCycleHours"] is None
        assert s["ineffectiveRate"] is None
        assert s["avgKpiDelta"] is None
        assert s["rejectRate"] is None
        assert s["avgScheduleHours"] is None
        assert all(m["closeRate"] is None for m in resp.json()["data"]["monthly"])

    def test_statistics_months_validation(self, client) -> None:
        """months 越界（>12 / <1）→ 422 参数校验。"""
        resp = self._get(client, {"months": 13})
        assert resp.status_code == 422
        resp = self._get(client, {"months": 0})
        assert resp.status_code == 422

    def test_statistics_months_sequence_boundaries(self, client) -> None:
        """跨年月份序列：months=3 从当前月往前 3 个月（含跨年正确性）。"""
        _override_db(
            client,
            self._full_results(self._summary_row(), self._reject_row()),
        )
        resp = self._get(client, {"months": 3})
        assert resp.status_code == 200
        months = [m["month"] for m in resp.json()["data"]["monthly"]]
        assert len(months) == 3
        for prev, cur in zip(months, months[1:], strict=False):
            py, pm = (int(x) for x in prev.split("-"))
            cy, cm = (int(x) for x in cur.split("-"))
            assert (cy * 12 + cm) - (py * 12 + pm) == 1

    def test_statistics_all_roles_can_view(self, client) -> None:
        _override_db(
            client,
            self._full_results(self._summary_row(), self._reject_row()),
        )
        resp = self._get(client, user_key="expert")
        assert resp.status_code == 200
