"""P3-05 实施记录与验证证据测试（后端先行，DEC-06 已裁口径）。

覆盖（03§6 P3-05 卡 + 02 台账 ENG-02~05）：
- P/PI/PID 反馈按结构校验（ENG-03）：合法 P/PI/PID 键集 + 反例
  （非数值/缺 p/有 d 无 i/多余键/空对象/字符串值）
- implementedAt 补录分离（ENG-02）：实施时刻≠提交时刻、未来时刻拒绝、
  早于开工拒绝、未申报=未知（不伪填）、后窗锚定 implementedAt
- tuningRecordId 严格关联（ENG-02）：跨回路建单拒绝、回写点脏关联拒绝、
  清单按 tuningRecordId 精确过滤
- 复诊三重校验（ENG-04）：同回路、完成（SUCCESS/PARTIAL）、实施后创建；
  revisit-candidates 候选端点
- 闭环证据三分类（DEC-06）：DATA_VERIFIED / COMPARABLE_CONDITION_VERIFIED
  （需数据+人工确认）/ MANUAL_CONFIRMATION（无数据闭环理由必填）
- actualRollbackConfirmed 独立（DEC-06/ENG-05）：验证前后状态门禁、
  确认须说明、可更正、ROLLED_BACK 判断结论与之独立
- 统计分开（DEC-06）：effectiveByEvidence 三分类 + dataVerifiedEffective
  不含无数据人工闭环
- pidFeedbackContext 冻结（B2 冻结链工单侧）：KNOWN 转换 / UNKNOWN 不默认

实现方式：与 test_handling_api.py 同款 mock db（_seq_execute）；
pidFeedbackContext 用 monkeypatch 替换 engineering_facts 单源。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.models.diagnosis_run import DiagnosisRun
from app.models.handling_order import HandlingOrder
from app.models.loop import LoopLedger
from app.models.tuning import TuningRecord
from app.services import handling_stats as hs
from tests.conftest import TEST_USERS, mock_current_user

LOOP_ID = str(uuid4())
LOOP_ID2 = str(uuid4())
ORDER_ID = str(uuid4())
TUNING_RECORD_ID = str(uuid4())

STARTED_AT = datetime(2026, 8, 10, 8, 0, 0)
SUBMITTED_AT = datetime(2026, 8, 12, 9, 30, 0)
IMPLEMENTED_AT = datetime(2026, 8, 11, 6, 0, 0)  # 补录场景：实施早于提交 27.5h


# ---------------------------------------------------------------------------
# 构造 helper（与 test_handling_api.py 同模式）
# ---------------------------------------------------------------------------


def _make_order(
    *,
    status: str = "PENDING",
    action_type: str = "TUNING",
    action_detail: dict | None = None,
    started_at: datetime | None = None,
    submitted_at: datetime | None = None,
    implemented_at: datetime | None = None,
    tuning_record_id: str | None = None,
    loop_id: str = LOOP_ID,
    verify_result: str | None = None,
) -> HandlingOrder:
    return HandlingOrder(
        id=ORDER_ID,
        order_no="HD-20260820-001",
        loop_id=loop_id,
        source="DIAGNOSIS",
        suggestion_ids=[],
        title="重新整定 PID 参数",
        action_type=action_type,
        action_detail=action_detail,
        planned_by="admin",
        handler="ic_engineer",
        started_at=started_at,
        submitted_at=submitted_at,
        implemented_at=implemented_at,
        verify_result=verify_result,
        tuning_record_id=tuning_record_id,
        status=status,
    )


def _make_tuning_record(loop_id: str = LOOP_ID) -> TuningRecord:
    return TuningRecord(
        id=TUNING_RECORD_ID,
        loop_id=loop_id,
        model_type="FOPDT",
        algorithm="IMC",
        status="SIMULATED",
    )


def _make_run(
    *,
    loop_id: str = LOOP_ID,
    status: str = "SUCCESS",
    created_at: datetime | None = None,
) -> DiagnosisRun:
    run = DiagnosisRun(
        id=str(uuid4()),
        loop_id=loop_id,
        triggered_by="ic_engineer",
        time_window_start=datetime(2026, 8, 11, 7, 0, 0),
        time_window_end=datetime(2026, 8, 11, 9, 0, 0),
        status=status,
    )
    run.created_at = created_at or datetime(2026, 8, 11, 10, 0, 0)
    return run


def _seq_execute(results: list):
    it = iter(results)

    async def _execute(*args, **kwargs):  # noqa: ARG001
        return next(it)

    return _execute


def _scalar_result(value: Any) -> MagicMock:
    r = MagicMock()
    r.scalar_one_or_none.return_value = value
    r.scalar.return_value = value
    return r


def _scalars_all_result(rows: list) -> MagicMock:
    r = MagicMock()
    r.scalars.return_value.all.return_value = rows
    return r


def _override_db(client, results: list, *, tuning_rec: TuningRecord | None = None) -> MagicMock:
    from app.core.db import get_db

    mock_db = MagicMock()
    mock_db.execute = _seq_execute(results)
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()
    mock_db.get = AsyncMock(return_value=tuning_rec)
    client.app.dependency_overrides[get_db] = lambda: mock_db
    return mock_db


def _capture_override_db(client, results: list, captured: list) -> MagicMock:
    from app.core.db import get_db

    it = iter(results)

    async def _execute(stmt, *args, **kwargs):
        captured.append(stmt)
        return next(it)

    mock_db = MagicMock()
    mock_db.execute = _execute
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()
    mock_db.get = AsyncMock(return_value=None)
    client.app.dependency_overrides[get_db] = lambda: mock_db
    return mock_db


def _pg_sql(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


# ===========================================================================
# ENG-03：P/PI/PID 反馈按结构校验
# ===========================================================================


class TestPidStructureValidation:
    """submit pidAfter / start pidBefore 结构契约（合法 P/PI/PID + 全套反例）。"""

    def _submit(self, client, action_detail: dict):
        order = _make_order(
            status="EXECUTING",
            started_at=STARTED_AT,
            action_detail={"pidBefore": {"p": 1.2, "i": 20, "d": 0}},
        )
        _override_db(client, [_scalar_result(order)])
        with mock_current_user(TEST_USERS["ic_engineer"]):
            return client.post(
                f"/api/v1/handling/orders/{ORDER_ID}/submit",
                headers={"Authorization": "Bearer fake-token"},
                json={"actionDetail": action_detail},
            )

    @pytest.mark.parametrize(
        "pid_after",
        [
            {"p": 1.2},  # P 结构
            {"p": 1.2, "i": 20},  # PI 结构
            {"p": 0.8, "i": 35, "d": 0},  # PID 结构
        ],
        ids=["p-only", "pi", "pid"],
    )
    def test_submit_valid_structures_pass(self, client, pid_after: dict) -> None:
        resp = self._submit(client, {"pidAfter": pid_after})
        assert resp.status_code == 200, resp.json()
        assert resp.json()["data"]["actionDetail"]["pidAfter"] == pid_after

    @pytest.mark.parametrize(
        "pid_after",
        [
            {},  # 空对象（原"任一项非空可通过"反例）
            {"i": 20},  # 缺 p——无比例项无结构
            {"p": 1.2, "d": 3},  # 有 d 无 i——P+D 不是标准反馈结构
            {"p": "0.8", "i": 35, "d": 0},  # 字符串数值
            {"p": 0.8, "i": None, "d": 0},  # None 值
            {"p": True, "i": 35},  # bool 冒充数值
            {"kp": 0.8, "ti": 35, "td": 0},  # 多余键（标准形冒充 DCS 形）
        ],
        ids=[
            "empty",
            "no-p",
            "d-without-i",
            "string-value",
            "none-value",
            "bool-value",
            "extra-keys",
        ],
    )
    def test_submit_invalid_structures_rejected(self, client, pid_after: dict) -> None:
        resp = self._submit(client, {"pidAfter": pid_after})
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_PARAM"

    def test_start_pid_before_invalid_rejected(self, client) -> None:
        """开工侧同样按结构校验（残缺 pidBefore 拒绝）。"""
        order = _make_order(status="PENDING")
        _override_db(client, [_scalar_result(order)])
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.post(
                f"/api/v1/handling/orders/{ORDER_ID}/start",
                headers={"Authorization": "Bearer fake-token"},
                json={"pidBefore": {"i": 20}},  # 缺 p
            )
        assert resp.status_code == 400
        assert "pidBefore" in resp.json()["message"]

    def test_validate_pid_feedback_unit_direct(self) -> None:
        """纯函数直测：错误消息点名字段与键；NaN/Inf 非有限数值拒绝
        （HTTP 层无法携带非合规 JSON，由直测覆盖该反例）。"""
        from app.api.v1.endpoints.handling import _validate_pid_feedback

        with pytest.raises(Exception, match="pidAfter.缺少比例项"):
            _validate_pid_feedback({"i": 1}, "pidAfter")
        with pytest.raises(Exception, match="有限数值"):
            _validate_pid_feedback({"p": float("nan"), "i": 35}, "pidAfter")
        with pytest.raises(Exception, match="有限数值"):
            _validate_pid_feedback({"p": float("inf")}, "pidAfter")
        with pytest.raises(Exception, match="同时填写积分项"):
            _validate_pid_feedback({"p": 1.2, "d": 3}, "pidAfter")


# ===========================================================================
# ENG-02：implementedAt 补录分离 + 统一窗口锚
# ===========================================================================


class TestImplementedAt:
    def _submit(self, client, body: dict, order: HandlingOrder | None = None):
        order = order or _make_order(status="EXECUTING", started_at=STARTED_AT)
        _override_db(client, [_scalar_result(order)])
        with mock_current_user(TEST_USERS["ic_engineer"]):
            return client.post(
                f"/api/v1/handling/orders/{ORDER_ID}/submit",
                headers={"Authorization": "Bearer fake-token"},
                json=body,
            )

    def test_submit_with_implemented_at_separates_moments(self, client) -> None:
        """补录分离：实施 08-11 06:00、提交时刻为服务端 now——两时刻各自独立。"""
        resp = self._submit(
            client,
            {
                "actionDetail": {"pidAfter": {"p": 0.8, "i": 35, "d": 0}},
                "implementedAt": "2026-08-11T06:00:00Z",
            },
        )
        assert resp.status_code == 200, resp.json()
        data = resp.json()["data"]
        assert data["implementedAt"] is not None
        assert data["implementedAt"].startswith("2026-08-11T06:00:00")
        assert data["implementationTimeKnown"] is True
        # submittedAt=服务端提交时刻（now），与申报实施时刻分离
        assert data["submittedAt"] != data["implementedAt"]

    def test_submit_without_implemented_at_marks_unknown(self, client) -> None:
        """未申报实施时刻=未知（NULL），不伪填 submitted_at。"""
        resp = self._submit(client, {"actionDetail": {"pidAfter": {"p": 0.8, "i": 35, "d": 0}}})
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["implementedAt"] is None
        assert data["implementationTimeKnown"] is False
        assert data["submittedAt"] is not None

    def test_submit_future_implemented_at_rejected(self, client) -> None:
        resp = self._submit(
            client,
            {
                "actionDetail": {"pidAfter": {"p": 0.8, "i": 35, "d": 0}},
                "implementedAt": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
            },
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_PARAM"

    def test_submit_implemented_before_started_rejected(self, client) -> None:
        """实施不可能早于开工（数据一致性反例）。"""
        resp = self._submit(
            client,
            {
                "actionDetail": {"pidAfter": {"p": 0.8, "i": 35, "d": 0}},
                "implementedAt": "2026-08-09T06:00:00Z",
            },
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_PARAM"

    def test_verify_after_window_anchors_implemented_at(self, client) -> None:
        """统一窗口契约：后窗锚 implemented_at（非 submitted_at）——
        补录场景下按提交时刻开窗会把实施后 27.5h 效果实错切掉。"""
        order = _make_order(
            status="VERIFYING",
            started_at=STARTED_AT,
            submitted_at=SUBMITTED_AT,
            implemented_at=IMPLEMENTED_AT,
        )
        captured: list = []
        _capture_override_db(
            client,
            [_scalar_result(order), _scalars_all_result([]), _scalars_all_result([])],
            captured,
        )
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.post(
                f"/api/v1/handling/orders/{ORDER_ID}/verify",
                headers={"Authorization": "Bearer fake-token"},
                json={"verifyResult": "EFFECTIVE", "verifyNote": "现场确认"},
            )
        assert resp.status_code == 200
        after_sql = _pg_sql(captured[2])
        # 后窗 [implemented_at, implemented_at+24h] = [08-11 06:00, 08-12 06:00]
        assert "2026-08-11 06:00:00" in after_sql
        assert "2026-08-12 06:00:00" in after_sql
        assert "2026-08-12 09:30:00" not in after_sql  # 提交时刻不参与后窗


# ===========================================================================
# ENG-02：tuningRecordId 严格关联（同回路 A/B 方案不串工单）
# ===========================================================================


class TestTuningRecordStrictAssociation:
    def _create(self, client, body: dict):
        with mock_current_user(TEST_USERS["ic_engineer"]):
            return client.post(
                "/api/v1/handling/orders",
                headers={"Authorization": "Bearer fake-token"},
                json=body,
            )

    def _create_db(self, client, tuning_rec: TuningRecord | None) -> MagicMock:
        """create_order mock：回路存在 + 当日序号 0。"""
        loop = LoopLedger(id=LOOP_ID, tag_name="TIC-101", loop_type="FLOW", is_active=True)
        return _override_db(
            client,
            [_scalar_result(loop), _scalar_result(0)],
            tuning_rec=tuning_rec,
        )

    def test_create_cross_loop_tuning_record_rejected(self, client) -> None:
        """跨回路整定记录建单拒绝（A 方案记录挂到 B 方案工单=串工单源头）。"""
        rec_other_loop = _make_tuning_record(loop_id=LOOP_ID2)
        self._create_db(client, rec_other_loop)
        resp = self._create(
            client,
            {
                "loopId": LOOP_ID,
                "actionType": "TUNING",
                "title": "A 方案实施",
                "tuningRecordId": TUNING_RECORD_ID,
            },
        )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_PARAM"
        assert "跨回路" in resp.json()["message"]

    def test_create_same_loop_tuning_record_ok(self, client) -> None:
        rec = _make_tuning_record(loop_id=LOOP_ID)
        self._create_db(client, rec)
        resp = self._create(
            client,
            {
                "loopId": LOOP_ID,
                "actionType": "TUNING",
                "title": "A 方案实施",
                "tuningRecordId": TUNING_RECORD_ID,
            },
        )
        assert resp.status_code == 200, resp.json()
        assert resp.json()["data"]["tuningRecordId"] == TUNING_RECORD_ID

    def test_submit_writeback_rejects_stale_cross_loop_link(self, client) -> None:
        """存量脏关联（历史建单未验回路）在回写点显式拒绝，不写别人状态。"""
        order = _make_order(
            status="EXECUTING",
            started_at=STARTED_AT,
            tuning_record_id=TUNING_RECORD_ID,
        )
        rec_other_loop = _make_tuning_record(loop_id=LOOP_ID2)
        _override_db(client, [_scalar_result(order)], tuning_rec=rec_other_loop)
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.post(
                f"/api/v1/handling/orders/{ORDER_ID}/submit",
                headers={"Authorization": "Bearer fake-token"},
                json={"actionDetail": {"pidAfter": {"p": 0.8, "i": 35, "d": 0}}},
            )
        assert resp.status_code == 400
        assert "关联不一致" in resp.json()["message"]
        assert rec_other_loop.status == "SIMULATED"  # 未被改写

    def test_list_orders_filters_by_tuning_record_id(self, client) -> None:
        """清单精确过滤（效果验证页按整定记录反查工单的后端能力）。"""
        from app.core.db import get_db

        captured: list = []
        it = iter([_scalar_result(0), _scalars_all_result([]), _scalars_all_result([])])

        async def _execute(stmt, *args, **kwargs):
            captured.append(stmt)
            return next(it)

        mock_db = MagicMock()
        mock_db.execute = _execute
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()
        client.app.dependency_overrides[get_db] = lambda: mock_db
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.get(
                f"/api/v1/handling/orders?tuningRecordId={TUNING_RECORD_ID}",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        where_sql = " ".join(_pg_sql(s) for s in captured)
        assert "ho.tuning_record_id" in where_sql


# ===========================================================================
# ENG-04：复诊三重校验 + 候选端点
# ===========================================================================


class TestRevisitValidation:
    def _verify_with_run(self, client, run: DiagnosisRun, body: dict | None = None):
        order = _make_order(
            status="VERIFYING",
            started_at=STARTED_AT,
            submitted_at=SUBMITTED_AT,
            implemented_at=IMPLEMENTED_AT,
        )
        _override_db(
            client,
            [
                _scalar_result(order),
                _scalar_result(run),
                _scalars_all_result([]),
                _scalars_all_result([]),
            ],
        )
        payload = {"verifyResult": "EFFECTIVE", "verifyRunId": run.id}
        payload.update(body or {})
        with mock_current_user(TEST_USERS["ic_engineer"]):
            return client.post(
                f"/api/v1/handling/orders/{ORDER_ID}/verify",
                headers={"Authorization": "Bearer fake-token"},
                json=payload,
            )

    def test_verify_cross_loop_run_rejected(self, client) -> None:
        """跨回路复诊拒绝（手填别的回路 UUID 不再被接受）。"""
        run = _make_run(loop_id=LOOP_ID2)  # 其他回路
        resp = self._verify_with_run(client, run)
        assert resp.status_code == 400
        assert "属于其他回路" in resp.json()["message"]

    def test_verify_unfinished_run_rejected(self, client) -> None:
        run = _make_run(status="FAILED")
        resp = self._verify_with_run(client, run)
        assert resp.status_code == 400
        assert "SUCCESS/PARTIAL" in resp.json()["message"]

    def test_verify_pre_implementation_run_rejected(self, client) -> None:
        """实施前创建的 run 不是复诊记录（锚=implemented_at 08-11 06:00）。"""
        run = _make_run(created_at=datetime(2026, 8, 10, 9, 0, 0))
        resp = self._verify_with_run(client, run)
        assert resp.status_code == 400
        assert "实施时刻之前" in resp.json()["message"]

    def test_verify_post_implementation_run_accepted(self, client) -> None:
        run = _make_run(created_at=datetime(2026, 8, 11, 10, 0, 0))
        resp = self._verify_with_run(client, run, {"verifyNote": "复诊确认"})
        assert resp.status_code == 200
        assert resp.json()["data"]["verifyRunId"] == run.id

    def test_revisit_candidates_only_after_anchor(self, client) -> None:
        """候选端点：同回路 + SUCCESS/PARTIAL + 实施锚后（SQL 三重条件）。"""
        order = _make_order(
            status="VERIFYING",
            started_at=STARTED_AT,
            submitted_at=SUBMITTED_AT,
            implemented_at=IMPLEMENTED_AT,
        )
        run = _make_run()
        captured: list = []
        from app.core.db import get_db

        it = iter([_scalar_result(order), _scalars_all_result([run])])

        async def _execute(stmt, *args, **kwargs):
            captured.append(stmt)
            return next(it)

        mock_db = MagicMock()
        mock_db.execute = _execute
        client.app.dependency_overrides[get_db] = lambda: mock_db
        with mock_current_user(TEST_USERS["sponsor"]):  # 只读角色可查（view 权限）
            resp = client.get(
                f"/api/v1/handling/orders/{ORDER_ID}/revisit-candidates",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200, resp.json()
        data = resp.json()["data"]
        assert data["anchorSource"] == "implementedAt"
        assert data["items"][0]["id"] == run.id
        sql = _pg_sql(captured[1])
        assert "status IN" in sql
        assert "created_at >=" in sql

    def test_revisit_candidates_requires_verifying(self, client) -> None:
        order = _make_order(status="EXECUTING", started_at=STARTED_AT)
        _override_db(client, [_scalar_result(order)])
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.get(
                f"/api/v1/handling/orders/{ORDER_ID}/revisit-candidates",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_STATE"


# ===========================================================================
# DEC-06：闭环证据三分类
# ===========================================================================


class TestClosureEvidence:
    def _snap(self, ts: datetime, score: str = "85.0") -> Any:
        from decimal import Decimal

        from app.models.metric import KpiSnapshotHourly

        return KpiSnapshotHourly(
            id=str(uuid4()),
            loop_id=LOOP_ID,
            ts_start=ts,
            ts_end=ts,
            score=Decimal(score),
            status="SUCCESS",
        )

    def _verify(self, client, order: HandlingOrder, body: dict):
        windows = (
            [
                _scalars_all_result([self._snap(STARTED_AT)]),
                _scalars_all_result([self._snap(SUBMITTED_AT)]),
            ]
            if body.pop("_withData", False)
            else [_scalars_all_result([]), _scalars_all_result([])]
        )
        _override_db(client, [_scalar_result(order), *windows])
        with mock_current_user(TEST_USERS["ic_engineer"]):
            return client.post(
                f"/api/v1/handling/orders/{ORDER_ID}/verify",
                headers={"Authorization": "Bearer fake-token"},
                json=body,
            )

    def _verifying(self) -> HandlingOrder:
        return _make_order(
            status="VERIFYING",
            started_at=STARTED_AT,
            submitted_at=SUBMITTED_AT,
        )

    def test_data_verified_when_both_windows_have_data(self, client) -> None:
        resp = self._verify(
            client,
            self._verifying(),
            {"verifyResult": "EFFECTIVE", "_withData": True},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["closureEvidence"] == "DATA_VERIFIED"
        assert data["conditionComparable"] is False  # 工况可比性未确认

    def test_comparable_condition_requires_data_and_confirmation(self, client) -> None:
        resp = self._verify(
            client,
            self._verifying(),
            {
                "verifyResult": "EFFECTIVE",
                "conditionComparable": True,
                "_withData": True,
            },
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["closureEvidence"] == "COMPARABLE_CONDITION_VERIFIED"
        assert data["conditionComparable"] is True

    def test_condition_comparable_without_data_stays_manual(self, client) -> None:
        """缺数据时工况确认不生效——工况可比不能替代数据。"""
        resp = self._verify(
            client,
            self._verifying(),
            {
                "verifyResult": "EFFECTIVE",
                "conditionComparable": True,
                "verifyNote": "现场确认工况一致",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["data"]["closureEvidence"] == "MANUAL_CONFIRMATION"

    def test_manual_closure_without_reason_rejected(self, client) -> None:
        """无数据人工闭环理由必填（不进数据验证通过数的前置门槛）。"""
        resp = self._verify(client, self._verifying(), {"verifyResult": "EFFECTIVE"})
        assert resp.status_code == 400
        assert "理由" in resp.json()["message"]

    def test_manual_closure_with_reason_ok_and_classified(self, client) -> None:
        resp = self._verify(
            client,
            self._verifying(),
            {"verifyResult": "EFFECTIVE", "verifyNote": "现场观察 4h 稳定，人工确认"},
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["closureEvidence"] == "MANUAL_CONFIRMATION"
        assert data["closureEvidenceLabel"] == "人工确认（无数据）"

    def test_ineffective_without_note_allowed(self, client) -> None:
        """验证无效（重开）不是闭环主张——无数据时不强制理由。"""
        resp = self._verify(client, self._verifying(), {"verifyResult": "INEFFECTIVE"})
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "REOPENED"
        assert resp.json()["data"]["closureEvidence"] == "MANUAL_CONFIRMATION"


# ===========================================================================
# DEC-06 / ENG-05：actualRollbackConfirmed 独立
# ===========================================================================


class TestRollbackConfirm:
    def _confirm(self, client, body: dict, order: HandlingOrder):
        _override_db(client, [_scalar_result(order)])
        with mock_current_user(TEST_USERS["ic_engineer"]):
            return client.post(
                f"/api/v1/handling/orders/{ORDER_ID}/rollback-confirm",
                headers={"Authorization": "Bearer fake-token"},
                json=body,
            )

    def test_confirm_before_verify_rejected(self, client) -> None:
        order = _make_order(status="VERIFYING", submitted_at=SUBMITTED_AT)
        resp = self._confirm(client, {"confirmed": True, "note": "x"}, order)
        assert resp.status_code == 400
        assert resp.json()["code"] == "ERR_STATE"

    def test_confirm_true_requires_note(self, client) -> None:
        order = _make_order(
            status="REOPENED", submitted_at=SUBMITTED_AT, verify_result="INEFFECTIVE"
        )
        resp = self._confirm(client, {"confirmed": True}, order)
        assert resp.status_code == 400
        assert "说明" in resp.json()["message"]

    def test_confirm_missing_bool_rejected(self, client) -> None:
        order = _make_order(
            status="REOPENED", submitted_at=SUBMITTED_AT, verify_result="INEFFECTIVE"
        )
        resp = self._confirm(client, {"note": "x"}, order)
        assert resp.status_code == 400

    def test_confirm_independent_of_rolled_back_writeback(self, client) -> None:
        """回退事实独立：验证无效先写 ROLLED_BACK（判断），未确认回退时
        actualRollbackConfirmed=false——不声称 DCS 已恢复。"""
        order = _make_order(
            status="REOPENED",
            started_at=STARTED_AT,
            submitted_at=SUBMITTED_AT,
            verify_result="INEFFECTIVE",
            tuning_record_id=TUNING_RECORD_ID,
        )
        rec = _make_tuning_record()
        rec.status = "ROLLED_BACK"  # verify 时已按判断写入
        mock_db = _override_db(client, [_scalar_result(order)], tuning_rec=rec)
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.post(
                f"/api/v1/handling/orders/{ORDER_ID}/rollback-confirm",
                headers={"Authorization": "Bearer fake-token"},
                json={"confirmed": True, "note": "08-13 22:00 DCS 工程师确认已恢复旧参数"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["actualRollbackConfirmed"] is True
        assert data["rollbackConfirmedBy"] == "ic_engineer"
        assert data["rollbackConfirmedAt"] is not None
        assert rec.status == "ROLLED_BACK"  # 判断结论独立保留
        mock_db.commit.assert_awaited()

    def test_confirm_false_corrects_misconfirmation(self, client) -> None:
        """误确认可更正（confirmed=false，说明可缺省）。"""
        order = _make_order(
            status="REOPENED", submitted_at=SUBMITTED_AT, verify_result="INEFFECTIVE"
        )
        order.actual_rollback_confirmed = True
        resp = self._confirm(client, {"confirmed": False}, order)
        assert resp.status_code == 200
        assert resp.json()["data"]["actualRollbackConfirmed"] is False


# ===========================================================================
# DEC-06：统计与数据验证分开
# ===========================================================================


class TestStatsEvidenceSeparation:
    async def test_effective_by_evidence_and_data_verified_excludes_manual(self) -> None:
        """无数据人工闭环不进数据验证通过数（分母/分子口径断言）。"""

        class _FakeResult:
            def __init__(self, rows=None, one_row=None):
                self._rows = rows or []
                self._one = one_row

            def one(self):
                return self._one

            def all(self):
                return self._rows

        async def _execute(stmt, _params=None):
            sql = str(stmt)
            if "closed_period" in sql:
                return _FakeResult(
                    one_row=SimpleNamespace(
                        closed_period=1,
                        closed_total=3,
                        verified_total=4,
                        ineffective_total=1,
                        avg_cycle_hours=10.0,
                        avg_schedule_hours=2.0,
                        avg_kpi_delta=5.0,
                    )
                )
            if "reviewed_total" in sql:
                return _FakeResult(one_row=SimpleNamespace(reviewed_total=5, rejected_total=1))
            if "sla_on_time" in sql:
                return _FakeResult(
                    one_row=SimpleNamespace(
                        sla_warn_count=0,
                        sla_breach_count=0,
                        sla_closed_total=3,
                        sla_on_time=3,
                        effective_count=4,
                        ineffective_count=1,
                        effective_manual_count=2,
                        effective_data_count=1,
                        effective_comparable_count=1,
                        effective_legacy_count=0,
                    )
                )
            if "not_dispatched" in sql:
                return _FakeResult(
                    one_row=SimpleNamespace(
                        not_dispatched=0,
                        dispatched_todo=0,
                        executing=0,
                        verifying=0,
                        closed=0,
                        reopened=0,
                        cancelled=0,
                    )
                )
            return _FakeResult(rows=[])

        db = MagicMock()
        db.execute = _execute

        data = await hs.build_handling_statistics(db, months=1)

        vr = data["verifyResult"]
        assert vr["effective"] == 4  # 原口径（全部有效结论）不变
        assert vr["effectiveByEvidence"] == {
            "MANUAL_CONFIRMATION": 2,
            "DATA_VERIFIED": 1,
            "COMPARABLE_CONDITION_VERIFIED": 1,
            "UNKNOWN": 0,
        }
        # 数据验证通过数=DATA+COMPARABLE=2，不含 2 例人工闭环
        assert vr["dataVerifiedEffective"] == 2


# ===========================================================================
# B2 冻结链工单侧：pidFeedbackContext 表示冻结
# ===========================================================================


class TestPidFeedbackContext:
    def _submit(self, client, monkeypatch, facts: dict | None, repr_node: dict):
        async def _fake_facts(db, loop_id):  # noqa: ARG001
            if facts is not None:
                return facts
            return {"pidRepresentation": repr_node}

        monkeypatch.setattr(
            "app.services.engineering_facts.resolve_loop_engineering_facts",
            _fake_facts,
        )
        order = _make_order(
            status="EXECUTING",
            started_at=STARTED_AT,
            action_detail={"pidBefore": {"p": 50.0, "i": 0.75, "d": 0}},
        )
        _override_db(client, [_scalar_result(order)])
        with mock_current_user(TEST_USERS["ic_engineer"]):
            return client.post(
                f"/api/v1/handling/orders/{ORDER_ID}/submit",
                headers={"Authorization": "Bearer fake-token"},
                json={"actionDetail": {"pidAfter": {"p": 25.0, "i": 2.0, "d": 0}}},
            )

    def test_known_representation_freezes_standard_form(self, client, monkeypatch) -> None:
        """KNOWN 表示：PB=25→Kp=4、I=2 分钟→Ti=120s（B1 单源换算冻结入工单）。"""
        resp = self._submit(
            client,
            monkeypatch,
            facts=None,
            repr_node={
                "status": "KNOWN",
                "pType": "PROPORTION_BAND",
                "iUnit": "MINUTES",
                "dUnit": "SECONDS",
                "dcsTemplateRevision": "dcs_model:test",
            },
        )
        assert resp.status_code == 200
        ctx = resp.json()["data"]["actionDetail"]["pidFeedbackContext"]
        assert ctx["conversionStatus"] == "KNOWN"
        assert ctx["pidAfterStandard"] == {"kp": 4.0, "ti": 120.0, "td": 0.0}
        assert ctx["pidBeforeStandard"] == {"kp": 2.0, "ti": 45.0, "td": 0.0}
        assert ctx["pidRepresentation"]["pType"] == "PROPORTION_BAND"

    def test_unknown_representation_no_default_standard(self, client, monkeypatch) -> None:
        """UNKNOWN 表示：不默认标准 kp/ti/td（诚实态），提交不被阻断。"""
        resp = self._submit(
            client,
            monkeypatch,
            facts=None,
            repr_node={"status": "UNKNOWN", "reason": "回路未绑定 DCS 型号"},
        )
        assert resp.status_code == 200
        ctx = resp.json()["data"]["actionDetail"]["pidFeedbackContext"]
        assert ctx["conversionStatus"] == "REPRESENTATION_UNKNOWN"
        assert ctx["pidAfterStandard"] is None
        assert ctx["pidBeforeStandard"] is None
