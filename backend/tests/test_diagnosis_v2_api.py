"""诊断 v2 API 集成测试。

模式参照 test_api_tasks.py：FakeRedis（TaskTracker 建单）+ mock_current_user
+ patch Celery 任务函数。
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
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

    def test_trigger_custom_window_strips_timezone(self, client, fake_redis) -> None:
        """自定义时间窗带 Z 后缀（前端 toISOString）→ 归一化为 naive UTC。

        回归：aware datetime 直传会使 kpi_snapshot_hourly（TIMESTAMP
        WITHOUT TIME ZONE）比较抛 asyncpg DataError → 任务级"诊断失败"。
        """
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
                    "timeWindow": {
                        # 北京时间 10:00/12:00 整点 → UTC 02:00/04:00（naive）
                        "start": "2026-08-17T02:00:00.000Z",
                        "end": "2026-08-17T04:00:00.000Z",
                    },
                },
            )
        assert resp.status_code == 200
        kwargs = mock_celery.delay.call_args.kwargs
        assert kwargs["start"] == "2026-08-17T02:00:00"
        assert kwargs["end"] == "2026-08-17T04:00:00"

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


class TestRunsLatestEndpoint:
    """GET /api/v1/diagnosis/runs/latest（每回路最新诊断概览）。"""

    @staticmethod
    def _row(
        *,
        tag: str,
        run_id,
        diagnosed: datetime | None,
        trigger_type: str | None = None,
        importance_level: int | None = 2,
        latest_score=91.5,
        run_count: int = 3,
        review_status: str | None = "PENDING",
        review_results=None,
        reviewed_by: str | None = None,
        reviewed_at: datetime | None = None,
    ):
        from types import SimpleNamespace

        return SimpleNamespace(
            loop_id=uuid4(),
            tag_name=tag,
            loop_description=f"{tag} 描述",
            importance_level=importance_level,
            run_id=run_id,
            primary_category="VALVE" if run_id else None,
            primary_confidence=0.7 if run_id else None,
            severity="LOW" if run_id else None,
            status="SUCCESS" if run_id else None,
            last_diagnosed_at=diagnosed,
            time_window_start=None,
            time_window_end=None,
            trigger_type=trigger_type,
            latest_score=latest_score if run_id else None,
            run_count=run_count,
            review_status=review_status if run_id else None,
            review_results=review_results if run_id else None,
            reviewed_by=reviewed_by,
            reviewed_at=reviewed_at,
        )

    def _override_execute(self, client, execute) -> None:
        from app.core.db import get_db

        mock_db = MagicMock()
        mock_db.execute = execute
        client.app.dependency_overrides[get_db] = lambda: mock_db

    def test_latest_serialization_with_undiagnosed(self, client) -> None:
        """已诊断行带结论标签；未诊断回路 runId=null 一并返回。"""
        rid = uuid4()
        rows = [
            self._row(tag="L1", run_id=rid, diagnosed=datetime(2026, 8, 16, 12, 0, 0)),
            self._row(tag="L2", run_id=None, diagnosed=None),
        ]
        rows_r = MagicMock()
        rows_r.all.return_value = rows
        with mock_current_user(TEST_USERS["admin"]):
            self._override_execute(client, _seq_execute([rows_r]))
            resp = client.get(
                "/api/v1/diagnosis/runs/latest",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        assert resp.json()["data"]["total"] == 2
        assert items[0]["runId"] == str(rid)
        assert items[0]["primaryCategoryLabel"] == "阀门/执行机构问题"
        assert items[0]["lastDiagnosedAt"] == "2026-08-16T12:00:00"
        assert items[0]["triggerType"] is None  # 行未带 trigger_type 时安全缺省
        # 2026-08-18 重构：回路等级/名称/性能评分/诊断次序/复核字段
        assert items[0]["importanceLevel"] == 2
        assert items[0]["loopDescription"] == "L1 描述"
        assert items[0]["latestScore"] == 91.5
        assert items[0]["runCount"] == 3
        assert items[0]["reviewStatus"] == "PENDING"
        assert items[0]["reviewResultLabels"] == []
        assert items[1]["runId"] is None
        assert items[1]["primaryCategoryLabel"] is None
        assert items[1]["runCount"] == 0
        assert items[1]["reviewStatus"] is None

    def test_latest_invalid_plant_node_id_rejected(self, client) -> None:
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/diagnosis/runs/latest",
                headers={"Authorization": "Bearer fake-token"},
                params={"plantNodeId": "not-a-uuid"},
            )
        assert resp.status_code == 400

    def test_latest_orders_desc_nulls_last(self, client) -> None:
        """排序行为：回路按最新诊断时间降序在前，未诊断回路垫底；透出 triggerType。"""

        async def _execute(sql, params=None):  # noqa: ARG001
            rid_new, rid_old = uuid4(), uuid4()
            rows_r = MagicMock()
            rows_r.all.return_value = [
                self._row(
                    tag="NEW",
                    run_id=rid_new,
                    diagnosed=datetime(2026, 8, 18, 6, 0, 0),
                    trigger_type="SCHEDULED",
                ),
                self._row(
                    tag="OLD",
                    run_id=rid_old,
                    diagnosed=datetime(2026, 8, 17, 6, 0, 0),
                    trigger_type="EVENT",
                ),
                self._row(tag="NONE", run_id=None, diagnosed=None),
            ]
            return rows_r

        with mock_current_user(TEST_USERS["admin"]):
            self._override_execute(client, _execute)
            resp = client.get(
                "/api/v1/diagnosis/runs/latest",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        tags = [i["loopTagName"] for i in items]
        # 已诊断回路按最新诊断时间降序在前，未诊断垫底
        assert tags == ["NEW", "OLD", "NONE"]
        assert items[0]["triggerType"] == "SCHEDULED"
        assert items[0]["triggerTypeLabel"] == "定期诊断"
        assert items[1]["triggerTypeLabel"] == "事件触发"
        assert items[2]["triggerType"] is None


class TestReviewEndpoint:
    """POST /api/v1/diagnosis/runs/{id}/review（复核闭环，2026-08-18）。"""

    def _override_db_scalar(self, client, run_or_none) -> None:
        """mock db.execute → result.scalar_one_or_none() 返回指定 run。"""
        from app.core.db import get_db

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = run_or_none
        mock_db = MagicMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.commit = AsyncMock()
        client.app.dependency_overrides[get_db] = lambda: mock_db

    def test_review_success(self, client) -> None:
        run = _make_run()
        with mock_current_user(TEST_USERS["ic_engineer"]):
            self._override_db_scalar(client, run)
            resp = client.post(
                f"/api/v1/diagnosis/runs/{RUN_ID}/review",
                headers={"Authorization": "Bearer fake-token"},
                json={
                    "reviewResults": ["INSTRUMENT", "TUNING"],
                    "reviewComment": "现场确认为仪表漂移叠加参数偏松",
                },
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["reviewStatus"] == "REVIEWED"
        assert data["reviewResults"] == ["INSTRUMENT", "TUNING"]
        assert data["reviewResultLabels"] == ["仪表/测量问题", "参数问题（PID 整定）"]
        assert data["reviewedBy"] == TEST_USERS["ic_engineer"].username
        assert data["reviewedAt"] is not None

    def test_review_rejects_unknown_category(self, client) -> None:
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                f"/api/v1/diagnosis/runs/{RUN_ID}/review",
                headers={"Authorization": "Bearer fake-token"},
                json={"reviewResults": ["NOT_A_CATEGORY"]},
            )
        assert resp.status_code == 400

    def test_review_rejects_empty_results(self, client) -> None:
        """空复核结论被 pydantic min_length 拦截（422）。"""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.post(
                f"/api/v1/diagnosis/runs/{RUN_ID}/review",
                headers={"Authorization": "Bearer fake-token"},
                json={"reviewResults": []},
            )
        assert resp.status_code == 422

    def test_review_run_not_found(self, client) -> None:
        with mock_current_user(TEST_USERS["admin"]):
            self._override_db_scalar(client, None)
            resp = client.post(
                f"/api/v1/diagnosis/runs/{RUN_ID}/review",
                headers={"Authorization": "Bearer fake-token"},
                json={"reviewResults": ["VALVE"]},
            )
        assert resp.status_code == 404

    def test_review_forbidden_for_sponsor(self, client) -> None:
        with mock_current_user(TEST_USERS["sponsor"]):
            resp = client.post(
                f"/api/v1/diagnosis/runs/{RUN_ID}/review",
                headers={"Authorization": "Bearer fake-token"},
                json={"reviewResults": ["VALVE"]},
            )
        assert resp.status_code == 403
