"""诊断 v2 API 集成测试。

模式参照 test_api_tasks.py：FakeRedis（TaskTracker 建单）+ mock_current_user
+ patch Celery 任务函数。
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.models.diagnosis_run import DiagnosisRun
from tests.conftest import TEST_USERS, mock_current_user

LOOP_ID = str(uuid4())
RUN_ID = str(uuid4())


def _loops_result():
    r = MagicMock()
    loop = MagicMock()
    loop.id = LOOP_ID
    r.scalars.return_value.all.return_value = [loop]
    return r


def _pv_mapping_result():
    r = MagicMock()
    r.scalars.return_value.all.return_value = [LOOP_ID]
    return r


class TestTriggerDiagnosis:
    """POST /api/v1/diagnosis/run."""

    def test_trigger_success(self, client, fake_redis) -> None:
        with (
            patch("app.tasks.diagnosis_v2.run_diagnosis_batch") as mock_celery,
            mock_current_user(TEST_USERS["ic_engineer"]),
        ):
            mock_celery.delay.return_value = MagicMock(id="celery-1")
            db = client.app.dependency_overrides.get("_db")  # noqa: F841
            from app.core.db import get_db

            mock_db = MagicMock()
            mock_db.execute = _seq_execute([_loops_result(), _pv_mapping_result()])
            client.app.dependency_overrides[get_db] = lambda: mock_db

            resp = client.post(
                "/api/v1/diagnosis/run",
                headers={"Authorization": "Bearer fake-token"},
                json={"loopIds": [LOOP_ID], "timeWindow": {"preset": "last_7d"}},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert body["data"]["accepted"] == 1
        assert body["data"]["taskId"]
        # delay 参数：ISO 时间窗 + task_id 透传
        kwargs = mock_celery.delay.call_args.kwargs
        assert kwargs["loop_ids"] == [LOOP_ID]
        assert kwargs["task_id"] == body["data"]["taskId"]
        assert kwargs["operator_group"] == "full"

    def test_trigger_with_operator_selection(self, client, fake_redis) -> None:
        """算子细选：合法名单经 delay 透传；未细选时 operators=None。"""
        with (
            patch("app.tasks.diagnosis_v2.run_diagnosis_batch") as mock_celery,
            mock_current_user(TEST_USERS["ic_engineer"]),
        ):
            mock_celery.delay.return_value = MagicMock(id="celery-1")
            from app.core.db import get_db

            mock_db = MagicMock()
            mock_db.execute = _seq_execute([_loops_result(), _pv_mapping_result()])
            client.app.dependency_overrides[get_db] = lambda: mock_db

            resp = client.post(
                "/api/v1/diagnosis/run",
                headers={"Authorization": "Bearer fake-token"},
                json={
                    "loopIds": [LOOP_ID],
                    "timeWindow": {"preset": "last_24h"},
                    "operators": ["quality_code_rules", "sensor_fault"],
                },
            )
        assert resp.status_code == 200
        kwargs = mock_celery.delay.call_args.kwargs
        assert kwargs["operators"] == ["quality_code_rules", "sensor_fault"]

    def test_trigger_with_unknown_operator_rejected(self, client) -> None:
        """未知算子名 → 400（防拼写错误静默空跑）。"""
        with mock_current_user(TEST_USERS["ic_engineer"]):
            from app.core.db import get_db

            mock_db = MagicMock()
            mock_db.execute = _seq_execute([_loops_result(), _pv_mapping_result()])
            client.app.dependency_overrides[get_db] = lambda: mock_db

            resp = client.post(
                "/api/v1/diagnosis/run",
                headers={"Authorization": "Bearer fake-token"},
                json={
                    "loopIds": [LOOP_ID],
                    "timeWindow": {"preset": "last_24h"},
                    "operators": ["not_an_operator"],
                },
            )
        assert resp.status_code == 400

    def test_trigger_loop_not_found(self, client) -> None:
        empty = MagicMock()
        empty.scalars.return_value.all.return_value = []
        with (
            mock_current_user(TEST_USERS["ic_engineer"]),
        ):
            from app.core.db import get_db

            mock_db = MagicMock()
            mock_db.execute = _seq_execute([empty])
            client.app.dependency_overrides[get_db] = lambda: mock_db
            resp = client.post(
                "/api/v1/diagnosis/run",
                headers={"Authorization": "Bearer fake-token"},
                json={"loopIds": [LOOP_ID], "timeWindow": {"preset": "last_24h"}},
            )
        assert resp.status_code == 400

    def test_trigger_forbidden_for_sponsor(self, client) -> None:
        with mock_current_user(TEST_USERS["sponsor"]):
            resp = client.post(
                "/api/v1/diagnosis/run",
                headers={"Authorization": "Bearer fake-token"},
                json={"loopIds": [LOOP_ID], "timeWindow": {"preset": "last_24h"}},
            )
        assert resp.status_code == 403

    def test_trigger_invalid_window(self, client) -> None:
        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.post(
                "/api/v1/diagnosis/run",
                headers={"Authorization": "Bearer fake-token"},
                json={"loopIds": [LOOP_ID], "timeWindow": {}},
            )
        assert resp.status_code == 400


class TestOperatorsEndpoint:
    def test_operators_returns_registry(self, client) -> None:
        client.get("/api/v1/auth/logout")  # warmup no-op
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/diagnosis/operators",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        items = resp.json()["data"]
        names = {item["name"] for item in items}
        assert len(names) == 11
        assert "stiction_ellipse" in names
        fast = [i for i in items if i["fastGroup"]]
        assert len(fast) == 7


def _seq_execute(results):
    it = iter(results)

    async def _execute(*args, **kwargs):  # noqa: ARG001
        return next(it)

    return _execute


def _make_run() -> DiagnosisRun:
    return DiagnosisRun(
        id=RUN_ID,
        task_id="task-1",
        loop_id=LOOP_ID,
        triggered_by="tester",
        time_window_start=datetime(2026, 8, 15, 0, 0, 0),
        time_window_end=datetime(2026, 8, 15, 7, 0, 0),
        operator_group="full",
        status="SUCCESS",
        data_gate={"passed": True, "confidenceLevel": "A"},
        operator_results={"sensor_fault": {"executed": True, "detected": True}},
        fusion_results={"QUALITY_ABNORMAL": {"detected": True, "confidence": 0.85}},
        symptom_tags={"QUALITY_ABNORMAL": {"detected": True, "confidence": 0.85}},
        primary_category="INSTRUMENT",
        primary_confidence=0.85,
        secondary_categories=[],
        pending_review=[
            {
                "category": "TUNING",
                "confidence": 0.9,
                "status": "pending_review",
                "contaminationNote": "主因仪表污染",
            }
        ],
        severity="MEDIUM",
        rationale=["主分类 仪表/测量问题"],
        recommendations=[
            {"content": "检查变送器", "basis": "frozen", "direction": "校验", "priority": 1}
        ],
        evidence_charts={"trend": {"ts": [0, 1], "pv": [50, 50]}, "scatter": {"pv": [], "op": []}},
        threshold_version="default",
        algorithm_version="MVP_DIAG_V2_v1.0",
        created_at=datetime(2026, 8, 16, 12, 0, 0),
    )


class TestRunsEndpoints:
    def _override_db(self, client, results):
        from app.core.db import get_db

        mock_db = MagicMock()
        mock_db.execute = _seq_execute(results)
        client.app.dependency_overrides[get_db] = lambda: mock_db

    def test_list_runs(self, client) -> None:
        count_r = MagicMock()
        count_r.scalar_one.return_value = 1
        rows_r = MagicMock()
        rows_r.all.return_value = [(_make_run(), "TEST-001")]
        with mock_current_user(TEST_USERS["admin"]):
            self._override_db(client, [count_r, rows_r])
            resp = client.get(
                "/api/v1/diagnosis/runs",
                headers={"Authorization": "Bearer fake-token"},
                params={"category": "INSTRUMENT"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["total"] == 1
        item = data["items"][0]
        assert item["loopTagName"] == "TEST-001"
        assert item["primaryCategory"] == "INSTRUMENT"
        assert item["primaryCategoryLabel"] == "仪表/测量问题"
        assert item["severity"] == "MEDIUM"

    def test_run_detail(self, client) -> None:
        rows_r = MagicMock()
        rows_r.first.return_value = (_make_run(), "TEST-001")
        with mock_current_user(TEST_USERS["admin"]):
            self._override_db(client, [rows_r])
            resp = client.get(
                f"/api/v1/diagnosis/runs/{RUN_ID}",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        detail = resp.json()["data"]
        assert detail["dataGate"]["passed"] is True
        assert detail["evidenceCharts"]["trend"]["pv"] == [50, 50]
        assert detail["pendingReview"][0]["category"] == "TUNING"

    def test_run_detail_not_found(self, client) -> None:
        rows_r = MagicMock()
        rows_r.first.return_value = None
        with mock_current_user(TEST_USERS["admin"]):
            self._override_db(client, [rows_r])
            resp = client.get(
                f"/api/v1/diagnosis/runs/{RUN_ID}",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 404

    def test_export_csv(self, client) -> None:
        rows_r = MagicMock()
        rows_r.all.return_value = [(_make_run(), "TEST-001")]
        with mock_current_user(TEST_USERS["admin"]):
            self._override_db(client, [rows_r])
            resp = client.get(
                "/api/v1/diagnosis/export",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        assert "主分类" in resp.text
        assert "仪表/测量问题" in resp.text
        assert resp.headers["content-type"].startswith("text/csv")
