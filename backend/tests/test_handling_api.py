"""处置模块 API 测试（Phase 1 后端：5 个流转端点）。

设计文档：docs/MVP设计/08-处置模块设计方案.md §4 状态机 / §6.2 流转操作
覆盖：
- 状态机全迁移矩阵（合法 6 条 + 非法迁移 ERR_STATE 400）
- 权限（IC_ENGINEER/PE_ENGINEER/ADMIN 可流转，SPONSOR/EXPERT 403）
- KPI 固化逻辑（verify 时服务端固化 kpi_before/after；窗口口径 §4.3）
- TUNING 类型 submit 时 pidAfter 必填校验

模式参照 test_diagnosis_v2_api.py：mock db（_seq_execute）+ mock_current_user。
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.models.loop_action_item import LoopActionItem
from app.models.metric import KpiSnapshotHourly
from tests.conftest import TEST_USERS, mock_current_user

LOOP_ID = str(uuid4())
RUN_ID = str(uuid4())
ITEM_ID = str(uuid4())

#: 固定时间锚点（naive UTC），用于 KPI 窗口口径断言
HANDLED_AT = datetime(2026, 8, 10, 8, 0, 0)
SUBMITTED_AT = datetime(2026, 8, 12, 9, 30, 0)


def _make_item(
    *,
    status: str = "PENDING",
    action_type: str | None = None,
    action_detail: dict | None = None,
    handled_by: str | None = None,
    handled_at: datetime | None = None,
    submitted_at: datetime | None = None,
    verify_result: str | None = None,
    verify_note: str | None = None,
    verified_by: str | None = None,
    verified_at: datetime | None = None,
    verify_run_id: str | None = None,
    kpi_before: dict | None = None,
    kpi_after: dict | None = None,
    ignore_reason: str | None = None,
) -> LoopActionItem:
    return LoopActionItem(
        id=ITEM_ID,
        run_id=RUN_ID,
        loop_id=LOOP_ID,
        source="SYSTEM",
        category="TUNING",
        content="重新整定 PID 参数",
        basis="诊断结论：参数问题（PID 整定）",
        priority=1,
        status=status,
        suggested_by="系统",
        suggested_at=datetime(2026, 8, 9, 0, 0, 0),
        action_type=action_type,
        action_detail=action_detail,
        handled_by=handled_by,
        handled_at=handled_at,
        submitted_at=submitted_at,
        verify_result=verify_result,
        verify_note=verify_note,
        verified_by=verified_by,
        verified_at=verified_at,
        verify_run_id=verify_run_id,
        kpi_before=kpi_before,
        kpi_after=kpi_after,
        ignore_reason=ignore_reason,
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
    r = MagicMock()
    r.scalar_one_or_none.return_value = value
    return r


def _override_db(client, results: list) -> MagicMock:
    """mock db：按序返回 execute 结果；commit 可断言。"""
    from app.core.db import get_db

    mock_db = MagicMock()
    mock_db.execute = _seq_execute(results)
    mock_db.commit = AsyncMock()
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
    client.app.dependency_overrides[get_db] = lambda: mock_db
    return mock_db


def _pg_sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


# ===========================================================================
# POST /handling/items/{id}/start（PENDING/REOPENED → HANDLING）
# ===========================================================================


class TestStartEndpoint:
    def _post(self, client, body: dict, user_key: str = "ic_engineer"):
        with mock_current_user(TEST_USERS[user_key]):
            return client.post(
                f"/api/v1/handling/items/{ITEM_ID}/start",
                json=body,
                headers={"Authorization": "Bearer fake-token"},
            )

    def test_start_pending_ok_default_handler(self, client) -> None:
        """合法迁移 #1：PENDING → HANDLING；handler 缺省=当前登录用户。"""
        item = _make_item(status="PENDING")
        mock_db = _override_db(client, [_scalar_result(item)])
        resp = self._post(
            client, {"actionType": "TUNING", "pidBefore": {"p": 1.2, "i": 20, "d": 0}}
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] == "HANDLING"
        assert data["statusLabel"] == "处置中"
        assert data["actionType"] == "TUNING"
        assert data["actionTypeLabel"] == "参数整定"
        assert data["handledBy"] == "ic_engineer"
        assert data["handledAt"] is not None and data["handledAt"].endswith("Z")
        assert data["actionDetail"]["pidBefore"] == {"p": 1.2, "i": 20, "d": 0}
        mock_db.commit.assert_awaited()

    def test_start_custom_handler(self, client) -> None:
        """handler 手工填写他人（评审决策 #11：不做指派，可填班组名）。"""
        item = _make_item(status="PENDING")
        _override_db(client, [_scalar_result(item)])
        resp = self._post(client, {"actionType": "VALVE", "handler": "仪表班-张三"})
        assert resp.status_code == 200
        assert resp.json()["data"]["handledBy"] == "仪表班-张三"

    def test_start_reopened_clears_verify_fields(self, client) -> None:
        """合法迁移 #3：REOPENED → HANDLING；上一轮验证字段清空待新轮回。"""
        item = _make_item(
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
        _override_db(client, [_scalar_result(item)])
        resp = self._post(client, {"actionType": "VALVE", "handler": "李四"})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] == "HANDLING"
        assert data["actionType"] == "VALVE"
        assert data["handledBy"] == "李四"
        # 上一轮验证字段全部清空
        assert data["submittedAt"] is None
        assert data["verifyResult"] is None
        assert data["verifyNote"] is None
        assert data["verifiedBy"] is None
        assert data["verifiedAt"] is None
        assert data["verifyRunId"] is None
        assert data["kpiBefore"] is None
        assert data["kpiAfter"] is None

    @pytest.mark.parametrize("status", ["HANDLING", "VERIFYING", "CLOSED", "IGNORED"])
    def test_start_invalid_state(self, client, status: str) -> None:
        """非法迁移：start 仅允许 PENDING/REOPENED（CLOSED 终态不可重开，评审决策 #9）。"""
        item = _make_item(status=status)
        _override_db(client, [_scalar_result(item)])
        resp = self._post(client, {"actionType": "TUNING"})
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_STATE"

    def test_start_missing_action_type(self, client) -> None:
        item = _make_item(status="PENDING")
        _override_db(client, [_scalar_result(item)])
        resp = self._post(client, {})
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_PARAM"

    def test_start_invalid_action_type(self, client) -> None:
        item = _make_item(status="PENDING")
        _override_db(client, [_scalar_result(item)])
        resp = self._post(client, {"actionType": "BOGUS"})
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_PARAM"

    def test_start_not_found(self, client) -> None:
        _override_db(client, [_scalar_result(None)])
        resp = self._post(client, {"actionType": "TUNING"})
        assert resp.status_code == 404
        assert resp.json()["code"] == "ERR_NOT_FOUND"

    @pytest.mark.parametrize("user_key", ["admin", "ic_engineer", "pe_engineer"])
    def test_start_allowed_roles(self, client, user_key: str) -> None:
        """流转角色口径：IC_ENGINEER / PE_ENGINEER / ADMIN（复用诊断触发角色）。"""
        item = _make_item(status="PENDING")
        _override_db(client, [_scalar_result(item)])
        resp = self._post(client, {"actionType": "OTHER"}, user_key=user_key)
        assert resp.status_code == 200

    @pytest.mark.parametrize("user_key", ["sponsor", "expert"])
    def test_start_forbidden(self, client, user_key: str) -> None:
        """SPONSOR / EXPERT 只读，流转 403。"""
        item = _make_item(status="PENDING")
        _override_db(client, [_scalar_result(item)])
        resp = self._post(client, {"actionType": "TUNING"}, user_key=user_key)
        assert resp.status_code == 403


# ===========================================================================
# POST /handling/items/{id}/submit（HANDLING → VERIFYING）
# ===========================================================================


class TestSubmitEndpoint:
    def _post(self, client, body: dict, user_key: str = "ic_engineer"):
        with mock_current_user(TEST_USERS[user_key]):
            return client.post(
                f"/api/v1/handling/items/{ITEM_ID}/submit",
                json=body,
                headers={"Authorization": "Bearer fake-token"},
            )

    def test_submit_ok_merges_existing_detail(self, client) -> None:
        """合法迁移 #4：HANDLING → VERIFYING；提交详情与先填详情合并（保留 pidBefore）。"""
        item = _make_item(
            status="HANDLING",
            action_type="TUNING",
            action_detail={"pidBefore": {"p": 1.2, "i": 20, "d": 0}},
            handled_by="ic_engineer",
            handled_at=HANDLED_AT,
        )
        _override_db(client, [_scalar_result(item)])
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
        item = _make_item(status="HANDLING", action_type="VALVE", handled_at=HANDLED_AT)
        _override_db(client, [_scalar_result(item)])
        resp = self._post(client, {"actionDetail": {"parts": "更换填料函", "downtimeHours": 4}})
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "VERIFYING"

    def test_submit_tuning_missing_pid_after(self, client) -> None:
        """TUNING 类型 submit 时 pidAfter 必填（服务端校验）。"""
        item = _make_item(
            status="HANDLING",
            action_type="TUNING",
            action_detail={"pidBefore": {"p": 1.2, "i": 20, "d": 0}},
            handled_at=HANDLED_AT,
        )
        _override_db(client, [_scalar_result(item)])
        resp = self._post(client, {"actionDetail": {"method": "Lambda 整定法"}})
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_PARAM"
        assert "pidAfter" in resp.json()["message"]

    def test_submit_empty_detail(self, client) -> None:
        item = _make_item(status="HANDLING", action_type="OTHER", handled_at=HANDLED_AT)
        _override_db(client, [_scalar_result(item)])
        resp = self._post(client, {"actionDetail": {}})
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_PARAM"

    @pytest.mark.parametrize("status", ["PENDING", "VERIFYING", "CLOSED", "REOPENED", "IGNORED"])
    def test_submit_invalid_state(self, client, status: str) -> None:
        """非法迁移：submit 仅允许 HANDLING。"""
        item = _make_item(status=status)
        _override_db(client, [_scalar_result(item)])
        resp = self._post(client, {"actionDetail": {"note": "x"}})
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_STATE"

    def test_submit_not_found(self, client) -> None:
        _override_db(client, [_scalar_result(None)])
        resp = self._post(client, {"actionDetail": {"note": "x"}})
        assert resp.status_code == 404

    def test_submit_forbidden_sponsor(self, client) -> None:
        item = _make_item(status="HANDLING", action_type="OTHER", handled_at=HANDLED_AT)
        _override_db(client, [_scalar_result(item)])
        resp = self._post(client, {"actionDetail": {"note": "x"}}, user_key="sponsor")
        assert resp.status_code == 403


# ===========================================================================
# POST /handling/items/{id}/verify（VERIFYING → CLOSED/REOPENED，固化 KPI）
# ===========================================================================


class TestVerifyEndpoint:
    def _post(self, client, body: dict, user_key: str = "ic_engineer"):
        with mock_current_user(TEST_USERS[user_key]):
            return client.post(
                f"/api/v1/handling/items/{ITEM_ID}/verify",
                json=body,
                headers={"Authorization": "Bearer fake-token"},
            )

    @staticmethod
    def _verifying_item() -> LoopActionItem:
        return _make_item(
            status="VERIFYING",
            action_type="TUNING",
            action_detail={"pidBefore": {"p": 1.2}, "pidAfter": {"p": 0.8}},
            handled_by="ic_engineer",
            handled_at=HANDLED_AT,
            submitted_at=SUBMITTED_AT,
        )

    def test_verify_effective_closes_with_kpi_snapshot(self, client) -> None:
        """合法迁移 #5：VERIFYING → CLOSED；服务端固化 kpi_before/after。"""
        item = self._verifying_item()
        snap_before = _make_snapshot(
            score="72.30", ts_start=datetime(2026, 8, 10, 7, 0, 0), ts_end=HANDLED_AT
        )
        snap_after = _make_snapshot(
            score="88.60",
            ts_start=datetime(2026, 8, 13, 9, 0, 0),
            ts_end=datetime(2026, 8, 13, 10, 0, 0),
            confidence="A",
        )
        _override_db(
            client,
            [_scalar_result(item), _scalar_result(snap_before), _scalar_result(snap_after)],
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
        """合法迁移 #6：VERIFYING → REOPENED（验证无效，可再次处置）。"""
        item = self._verifying_item()
        _override_db(client, [_scalar_result(item), _scalar_result(None), _scalar_result(None)])
        resp = self._post(client, {"verifyResult": "INEFFECTIVE", "verifyNote": "振荡未消除"})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] == "REOPENED"
        assert data["statusLabel"] == "重开"
        assert data["verifyResult"] == "INEFFECTIVE"
        # 窗口无快照：对应侧为 None（前端对比卡显示"数据不足"）
        assert data["kpiBefore"] is None
        assert data["kpiAfter"] is None

    def test_verify_kpi_window_boundaries(self, client) -> None:
        """KPI 窗口口径（评审决策 #8）：前窗 [handled_at−24h, handled_at]，
        后窗 [submitted_at, submitted_at+24h]，处置执行期数据隔离。"""
        item = self._verifying_item()
        captured: list = []
        _capture_override_db(
            client, [_scalar_result(item), _scalar_result(None), _scalar_result(None)], captured
        )
        resp = self._post(client, {"verifyResult": "EFFECTIVE"})
        assert resp.status_code == 200
        # 第 1 条 SQL：取处置项；第 2 条：前窗快照；第 3 条：后窗快照
        before_sql = _pg_sql(captured[1])
        after_sql = _pg_sql(captured[2])
        # 前窗 [2026-08-09 08:00, 2026-08-10 08:00]
        assert "2026-08-09 08:00:00" in before_sql
        assert "2026-08-10 08:00:00" in before_sql
        # 后窗 [2026-08-12 09:30, 2026-08-13 09:30]
        assert "2026-08-12 09:30:00" in after_sql
        assert "2026-08-13 09:30:00" in after_sql

    def test_verify_with_run_id(self, client) -> None:
        """verifyRunId 关联复诊记录（存在性校验通过后落库）。"""
        item = self._verifying_item()
        revisit_run_id = str(uuid4())
        _override_db(
            client,
            [
                _scalar_result(item),
                _scalar_result(revisit_run_id),  # diagnosis_run 存在性校验
                _scalar_result(None),
                _scalar_result(None),
            ],
        )
        resp = self._post(client, {"verifyResult": "EFFECTIVE", "verifyRunId": revisit_run_id})
        assert resp.status_code == 200
        assert resp.json()["data"]["verifyRunId"] == revisit_run_id

    def test_verify_run_id_not_found(self, client) -> None:
        item = self._verifying_item()
        _override_db(client, [_scalar_result(item), _scalar_result(None)])
        resp = self._post(client, {"verifyResult": "EFFECTIVE", "verifyRunId": str(uuid4())})
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_PARAM"

    def test_verify_invalid_result(self, client) -> None:
        item = self._verifying_item()
        _override_db(client, [_scalar_result(item)])
        resp = self._post(client, {"verifyResult": "MAYBE"})
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_PARAM"

    @pytest.mark.parametrize("status", ["PENDING", "HANDLING", "CLOSED", "REOPENED", "IGNORED"])
    def test_verify_invalid_state(self, client, status: str) -> None:
        """非法迁移：verify 仅允许 VERIFYING（CLOSED 终态不可再验证）。"""
        item = _make_item(status=status)
        _override_db(client, [_scalar_result(item)])
        resp = self._post(client, {"verifyResult": "EFFECTIVE"})
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_STATE"

    def test_verify_not_found(self, client) -> None:
        _override_db(client, [_scalar_result(None)])
        resp = self._post(client, {"verifyResult": "EFFECTIVE"})
        assert resp.status_code == 404

    def test_verify_forbidden_sponsor(self, client) -> None:
        item = self._verifying_item()
        _override_db(client, [_scalar_result(item)])
        resp = self._post(client, {"verifyResult": "EFFECTIVE"}, user_key="sponsor")
        assert resp.status_code == 403


# ===========================================================================
# POST /handling/items/{id}/ignore（PENDING → IGNORED 终态）
# ===========================================================================


class TestIgnoreEndpoint:
    def _post(self, client, body: dict, user_key: str = "ic_engineer"):
        with mock_current_user(TEST_USERS[user_key]):
            return client.post(
                f"/api/v1/handling/items/{ITEM_ID}/ignore",
                json=body,
                headers={"Authorization": "Bearer fake-token"},
            )

    def test_ignore_ok(self, client) -> None:
        """合法迁移 #2：PENDING → IGNORED；ignore_reason 必填。"""
        item = _make_item(status="PENDING")
        mock_db = _override_db(client, [_scalar_result(item)])
        resp = self._post(client, {"ignoreReason": "建议与近期检修计划重复"})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["status"] == "IGNORED"
        assert data["statusLabel"] == "已忽略"
        assert data["ignoreReason"] == "建议与近期检修计划重复"
        mock_db.commit.assert_awaited()

    def test_ignore_missing_reason(self, client) -> None:
        item = _make_item(status="PENDING")
        _override_db(client, [_scalar_result(item)])
        resp = self._post(client, {})
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_PARAM"

    def test_ignore_blank_reason(self, client) -> None:
        item = _make_item(status="PENDING")
        _override_db(client, [_scalar_result(item)])
        resp = self._post(client, {"ignoreReason": "   "})
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_PARAM"

    @pytest.mark.parametrize("status", ["HANDLING", "VERIFYING", "CLOSED", "REOPENED", "IGNORED"])
    def test_ignore_invalid_state(self, client, status: str) -> None:
        """非法迁移：ignore 仅允许 PENDING。"""
        item = _make_item(status=status)
        _override_db(client, [_scalar_result(item)])
        resp = self._post(client, {"ignoreReason": "重复"})
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_STATE"

    def test_ignore_not_found(self, client) -> None:
        _override_db(client, [_scalar_result(None)])
        resp = self._post(client, {"ignoreReason": "重复"})
        assert resp.status_code == 404

    def test_ignore_forbidden_sponsor(self, client) -> None:
        item = _make_item(status="PENDING")
        _override_db(client, [_scalar_result(item)])
        resp = self._post(client, {"ignoreReason": "重复"}, user_key="sponsor")
        assert resp.status_code == 403


# ===========================================================================
# POST /handling/items/{id}/kpi-comparison（VERIFYING 预览，不落库）
# ===========================================================================


class TestKpiComparisonEndpoint:
    def _post(self, client, user_key: str = "ic_engineer"):
        with mock_current_user(TEST_USERS[user_key]):
            return client.post(
                f"/api/v1/handling/items/{ITEM_ID}/kpi-comparison",
                headers={"Authorization": "Bearer fake-token"},
            )

    def test_preview_ok_not_persisted(self, client) -> None:
        """VERIFYING 阶段实时拉取前后窗口 KPI（不落库，verify 时才固化）。"""
        item = _make_item(
            status="VERIFYING",
            action_type="TUNING",
            handled_at=HANDLED_AT,
            submitted_at=SUBMITTED_AT,
        )
        snap_before = _make_snapshot(
            score="72.30", ts_start=datetime(2026, 8, 10, 7, 0, 0), ts_end=HANDLED_AT
        )
        mock_db = _override_db(
            client, [_scalar_result(item), _scalar_result(snap_before), _scalar_result(None)]
        )
        resp = self._post(client)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["kpiBefore"]["score"] == pytest.approx(72.3)
        assert data["kpiAfter"] is None
        # 窗口字段：前窗以 handled_at 为界，后窗以 submitted_at 为界
        assert data["window"]["beforeStart"] == "2026-08-09T08:00:00Z"
        assert data["window"]["beforeEnd"] == "2026-08-10T08:00:00Z"
        assert data["window"]["afterStart"] == "2026-08-12T09:30:00Z"
        assert data["window"]["afterEnd"] == "2026-08-13T09:30:00Z"
        # 预览不落库：不 commit，行状态与 kpi 字段不变
        mock_db.commit.assert_not_awaited()
        assert item.kpi_before is None
        assert item.status == "VERIFYING"

    @pytest.mark.parametrize("status", ["PENDING", "HANDLING", "CLOSED", "REOPENED", "IGNORED"])
    def test_preview_invalid_state(self, client, status: str) -> None:
        """预览仅 VERIFYING 阶段可用。"""
        item = _make_item(status=status)
        _override_db(client, [_scalar_result(item)])
        resp = self._post(client)
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_STATE"

    def test_preview_not_found(self, client) -> None:
        _override_db(client, [_scalar_result(None)])
        resp = self._post(client)
        assert resp.status_code == 404

    def test_preview_forbidden_sponsor(self, client) -> None:
        item = _make_item(status="VERIFYING", handled_at=HANDLED_AT, submitted_at=SUBMITTED_AT)
        _override_db(client, [_scalar_result(item)])
        resp = self._post(client, user_key="sponsor")
        assert resp.status_code == 403
