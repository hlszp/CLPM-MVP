"""处置流转回写整定记录状态测试（09 设计方案 §5.4 / 10 计划 T11；v2.0 平移至工单端点）。

联动口径：
- submit（EXECUTING → VERIFYING）：TUNING 类且 tuning_record_id 非空 →
  tuning_record.status = APPLIED
- verify 有效（→ CLOSED）：→ VERIFIED；验证无效（→ REOPENED）→ 回退 SIMULATED
- 非 TUNING 类型或未关联整定记录：不回写
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

from app.models.handling_order import HandlingOrder
from app.models.tuning import TuningRecord
from tests.conftest import TEST_USERS, mock_current_user

ORDER_ID = "00000000-0000-0000-0000-0000000000b1"
LOOP_ID = "00000000-0000-0000-0000-0000000000b3"
TUNING_RECORD_ID = "00000000-0000-0000-0000-0000000000b4"

STARTED_AT = datetime(2026, 8, 10, 8, 0, 0)
SUBMITTED_AT = datetime(2026, 8, 10, 9, 0, 0)


def _make_order(
    *,
    status: str,
    action_type: str | None = "TUNING",
    tuning_record_id: str | None = TUNING_RECORD_ID,
) -> HandlingOrder:
    return HandlingOrder(
        id=ORDER_ID,
        order_no="HD-20260820-001",
        loop_id=LOOP_ID,
        source="DIAGNOSIS",
        suggestion_ids=[],
        title="重新整定 PID 参数",
        action_type=action_type,
        action_detail={"pidBefore": {"p": 1.2, "i": 20, "d": 0}},
        handler="ic_engineer",
        started_at=STARTED_AT,
        submitted_at=SUBMITTED_AT if status == "VERIFYING" else None,
        tuning_record_id=tuning_record_id,
        status=status,
    )


def _scalar_result(value) -> MagicMock:
    r = MagicMock()
    r.scalar_one_or_none.return_value = value
    return r


def _seq_execute(results: list):
    it = iter(results)

    async def _execute(*args, **kwargs):  # noqa: ARG001
        return next(it)

    return _execute


def _override_db(client, results: list, tuning_rec: TuningRecord | None) -> MagicMock:
    """mock db：execute 按序返回；db.get 返回整定记录。"""
    from app.core.db import get_db

    mock_db = MagicMock()
    mock_db.execute = _seq_execute(results)
    mock_db.commit = AsyncMock()
    mock_db.refresh = AsyncMock()
    mock_db.get = AsyncMock(return_value=tuning_rec)
    client.app.dependency_overrides[get_db] = lambda: mock_db
    return mock_db


def _make_tuning_record(status: str = "SIMULATED") -> TuningRecord:
    return TuningRecord(
        id=TUNING_RECORD_ID,
        loop_id=LOOP_ID,
        model_type="FOPDT",
        algorithm="IMC",
        status=status,
    )


class TestSubmitWriteback:
    """submit 提交验证回写 APPLIED。"""

    def test_submit_tuning_writes_back_applied(self, client) -> None:
        order = _make_order(status="EXECUTING")
        rec = _make_tuning_record("SIMULATED")
        mock_db = _override_db(client, [_scalar_result(order)], rec)
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                f"/api/v1/handling/orders/{ORDER_ID}/submit",
                headers={"Authorization": "Bearer fake-token"},
                json={"actionDetail": {"pidAfter": {"p": 0.8, "i": 35, "d": 0}}},
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "VERIFYING"
        assert rec.status == "APPLIED"
        mock_db.get.assert_awaited_once()

    def test_submit_non_tuning_no_writeback(self, client) -> None:
        """非 TUNING 类型（VALVE）：不触发回写。"""
        order = _make_order(status="EXECUTING", action_type="VALVE", tuning_record_id=None)
        rec = _make_tuning_record("SIMULATED")
        mock_db = _override_db(client, [_scalar_result(order)], rec)
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                f"/api/v1/handling/orders/{ORDER_ID}/submit",
                headers={"Authorization": "Bearer fake-token"},
                json={"actionDetail": {"parts": "更换填料函"}},
            )
        assert resp.status_code == 200
        mock_db.get.assert_not_called()
        assert rec.status == "SIMULATED"


class TestVerifyWriteback:
    """verify 验证结论回写 VERIFIED / SIMULATED。"""

    def _verify(self, client, verify_result: str):
        order = _make_order(status="VERIFYING")
        rec = _make_tuning_record("APPLIED")
        # execute 序列：_get_order_or_404 → kpi_before 快照 → kpi_after 快照
        mock_db = _override_db(
            client,
            [_scalar_result(order), _scalar_result(None), _scalar_result(None)],
            rec,
        )
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                f"/api/v1/handling/orders/{ORDER_ID}/verify",
                headers={"Authorization": "Bearer fake-token"},
                json={"verifyResult": verify_result},
            )
        return resp, rec, mock_db

    def test_verify_effective_writes_back_verified(self, client) -> None:
        resp, rec, _ = self._verify(client, "EFFECTIVE")
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "CLOSED"
        assert rec.status == "VERIFIED"

    def test_verify_ineffective_rolls_back_to_simulated(self, client) -> None:
        resp, rec, _ = self._verify(client, "INEFFECTIVE")
        assert resp.status_code == 200
        assert resp.json()["data"]["status"] == "REOPENED"
        assert rec.status == "SIMULATED"
