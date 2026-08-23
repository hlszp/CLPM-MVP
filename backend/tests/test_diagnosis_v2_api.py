"""诊断 v2 API 集成测试。

模式参照 test_api_tasks.py：FakeRedis（TaskTracker 建单）+ mock_current_user
+ patch Celery 任务函数。
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.models.diagnosis_run import DiagnosisRun
from app.models.loop_action_item import LoopActionItem
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
        metric_summary=None,
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
            metric_summary=metric_summary,
        )

    def _override_execute(self, client, execute) -> None:
        from app.core.db import get_db

        mock_db = MagicMock()
        mock_db.execute = execute
        client.app.dependency_overrides[get_db] = lambda: mock_db

    def test_latest_serialization_with_undiagnosed(self, client) -> None:
        """已诊断行带结论标签；未诊断回路 runId=null 一并返回。"""
        rid = uuid4()
        metric_summary = {
            "negative": {"badValueRate": 1.16, "oscillationRate": 97.07},
            "positive": {"score": 60.95},
            "source": {"badValueRate": "kpi"},
        }
        rows = [
            self._row(
                tag="L1",
                run_id=rid,
                diagnosed=datetime(2026, 8, 16, 12, 0, 0),
                metric_summary=metric_summary,
            ),
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
        # metricSummary 透传（2026-08-19：回路工作台 R5 诊断卡/整定摘要条消费）
        assert items[0]["metricSummary"] == metric_summary
        assert items[1]["runId"] is None
        assert items[1]["primaryCategoryLabel"] is None
        assert items[1]["runCount"] == 0
        assert items[1]["reviewStatus"] is None
        assert items[1]["metricSummary"] is None

    def test_latest_invalid_plant_node_id_rejected(self, client) -> None:
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/diagnosis/runs/latest",
                headers={"Authorization": "Bearer fake-token"},
                params={"plantNodeId": "not-a-uuid"},
            )
        assert resp.status_code == 400

    def test_latest_invalid_loop_id_rejected(self, client) -> None:
        """loopId 非 UUID 直接 400（与 plantNodeId 同防御）。"""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/diagnosis/runs/latest",
                headers={"Authorization": "Bearer fake-token"},
                params={"loopId": "not-a-uuid"},
            )
        assert resp.status_code == 400

    def test_latest_loop_id_filter(self, client) -> None:
        """loopId 单回路过滤：SQL 拼入 ll.id = :loop_id 且参数透传。"""
        captured: dict = {}

        async def _execute(sql, params=None):
            captured["sql"] = str(sql)
            captured["params"] = params
            rid = uuid4()
            rows_r = MagicMock()
            rows_r.all.return_value = [
                self._row(tag="L1", run_id=rid, diagnosed=datetime(2026, 8, 19, 8, 0, 0))
            ]
            return rows_r

        loop_id = str(uuid4())
        with mock_current_user(TEST_USERS["admin"]):
            self._override_execute(client, _execute)
            resp = client.get(
                "/api/v1/diagnosis/runs/latest",
                headers={"Authorization": "Bearer fake-token"},
                params={"loopId": loop_id},
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["total"] == 1
        # 单回路过滤：条件与参数均生效（plantNodeId 未传 → 无 node_tree CTE）
        assert "ll.id = :loop_id" in captured["sql"]
        assert "node_tree" not in captured["sql"]
        assert captured["params"] == {"loop_id": loop_id}

    def test_latest_plant_node_and_loop_combined(self, client) -> None:
        """plantNodeId + loopId 同时传入：CTE 与回路过滤并存（AND 语义）。"""
        captured: dict = {}

        async def _execute(sql, params=None):
            captured["sql"] = str(sql)
            captured["params"] = params
            rows_r = MagicMock()
            rows_r.all.return_value = []
            return rows_r

        plant_id, loop_id = str(uuid4()), str(uuid4())
        with mock_current_user(TEST_USERS["admin"]):
            self._override_execute(client, _execute)
            resp = client.get(
                "/api/v1/diagnosis/runs/latest",
                headers={"Authorization": "Bearer fake-token"},
                params={"plantNodeId": plant_id, "loopId": loop_id},
            )
        assert resp.status_code == 200
        assert "WITH RECURSIVE node_tree" in captured["sql"]
        assert "ll.id = :loop_id" in captured["sql"]
        assert captured["params"] == {"root_id": plant_id, "loop_id": loop_id}

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


def _make_action(
    *,
    source: str = "SYSTEM",
    category: str | None = "INSTRUMENT",
    content: str = "校验测量仪表",
    basis: str = "诊断结论：仪表/测量问题",
    priority: int | None = 1,
    suggested_by: str = "系统",
) -> LoopActionItem:
    from datetime import UTC

    return LoopActionItem(
        id=str(uuid4()),
        run_id=RUN_ID,
        loop_id=LOOP_ID,
        source=source,
        category=category,
        content=content,
        basis=basis,
        priority=priority,
        status="PENDING",
        suggested_by=suggested_by,
        suggested_at=datetime.now(UTC).replace(tzinfo=None),
    )


class TestRunActionsEndpoint:
    """GET/POST /api/v1/diagnosis/runs/{id}/actions（§9.4 处置建议）。"""

    def _override_db(self, client, run_or_none, list_results) -> MagicMock:
        """mock db：第 1 次 execute 返回 run（scalar_one_or_none），
        后续 execute 按序返回列表查询结果（scalars().all()）。"""
        from app.core.db import get_db

        run_r = MagicMock()
        run_r.scalar_one_or_none.return_value = run_or_none
        mock_db = MagicMock()
        mock_db.execute = _seq_execute([run_r, *list_results])
        mock_db.commit = AsyncMock()
        mock_db.flush = AsyncMock()
        mock_db.add = MagicMock()
        client.app.dependency_overrides[get_db] = lambda: mock_db
        return mock_db

    @staticmethod
    def _list_result(items: list) -> MagicMock:
        r = MagicMock()
        r.scalars.return_value.all.return_value = items
        return r

    @staticmethod
    def _count_result(n: int = 0) -> MagicMock:
        """A3 幂等守卫 count 查询结果。"""
        r = MagicMock()
        r.scalar.return_value = n
        return r

    def test_list_generates_system_actions_when_empty(self, client) -> None:
        """首次拉取为空 → 按诊断结论自动生成标准建议（INSTRUMENT 2 条）。"""
        run = _make_run()
        generated = [
            _make_action(content="校验测量仪表：对变送器进行零点/量程校验…", priority=1),
            _make_action(content="检查信号接线与屏蔽：排查信号电缆…", priority=2),
        ]
        with mock_current_user(TEST_USERS["admin"]):
            mock_db = self._override_db(
                client,
                run,
                [
                    self._list_result([]),
                    self._count_result(0),
                    self._list_result(generated),
                ],
            )
            resp = client.get(
                f"/api/v1/diagnosis/runs/{RUN_ID}/actions",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        assert len(items) == 2
        assert all(i["source"] == "SYSTEM" for i in items)
        assert items[0]["categoryLabel"] == "仪表/测量问题"
        assert items[0]["suggestedBy"] == "系统"
        # 生成路径：db.add 被调用并提交
        assert mock_db.add.call_count == 2
        mock_db.commit.assert_awaited()

    def test_list_returns_existing_without_regeneration(self, client) -> None:
        run = _make_run()
        existing = [
            _make_action(source="MANUAL", category=None, content="人工措施", suggested_by="admin")
        ]
        with mock_current_user(TEST_USERS["admin"]):
            mock_db = self._override_db(client, run, [self._list_result(existing)])
            resp = client.get(
                f"/api/v1/diagnosis/runs/{RUN_ID}/actions",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        assert len(items) == 1
        assert items[0]["source"] == "MANUAL"
        assert items[0]["suggestedBy"] == "admin"
        mock_db.add.assert_not_called()

    def test_list_uses_review_results_when_reviewed(self, client) -> None:
        """已复核 → 按复核结论生成（basis=人工复核前缀）。"""
        run = _make_run()
        run.review_status = "REVIEWED"
        run.review_results = ["TUNING"]
        generated = [
            _make_action(
                category="TUNING",
                content="重新整定 PID 参数：…",
                basis="人工复核：参数问题（PID 整定）",
                priority=1,
            ),
        ]
        with mock_current_user(TEST_USERS["admin"]):
            self._override_db(
                client,
                run,
                [
                    self._list_result([]),
                    self._count_result(0),
                    self._list_result(generated),
                ],
            )
            resp = client.get(
                f"/api/v1/diagnosis/runs/{RUN_ID}/actions",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        items = resp.json()["data"]["items"]
        assert items[0]["basis"].startswith("人工复核")

    def test_list_run_not_found(self, client) -> None:
        with mock_current_user(TEST_USERS["admin"]):
            self._override_db(client, None, [])
            resp = client.get(
                f"/api/v1/diagnosis/runs/{RUN_ID}/actions",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 404

    def test_create_manual_action(self, client) -> None:
        """人工新增：source=MANUAL，建议人=当前登录用户。"""
        run = _make_run()
        with mock_current_user(TEST_USERS["ic_engineer"]):
            mock_db = self._override_db(client, run, [])
            resp = client.post(
                f"/api/v1/diagnosis/runs/{RUN_ID}/actions",
                headers={"Authorization": "Bearer fake-token"},
                json={"content": "安排 8 月 20 日变送器校验", "basis": "现场确认漂移"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["source"] == "MANUAL"
        assert data["suggestedBy"] == TEST_USERS["ic_engineer"].username
        assert data["suggestedAt"] is not None
        mock_db.add.assert_called_once()

    def test_create_action_forbidden_for_sponsor(self, client) -> None:
        with mock_current_user(TEST_USERS["sponsor"]):
            resp = client.post(
                f"/api/v1/diagnosis/runs/{RUN_ID}/actions",
                headers={"Authorization": "Bearer fake-token"},
                json={"content": "x"},
            )
        assert resp.status_code == 403


class TestActionUpdateDeleteEndpoints:
    """PUT/DELETE /api/v1/diagnosis/runs/actions/{id}（建议编辑/删除，2026-08-18）。"""

    ACTION_ID = str(uuid4())

    def _override_db(self, client, action_or_none) -> MagicMock:
        from app.core.db import get_db

        r = MagicMock()
        r.scalar_one_or_none.return_value = action_or_none
        mock_db = MagicMock()
        mock_db.execute = AsyncMock(return_value=r)
        mock_db.commit = AsyncMock()
        client.app.dependency_overrides[get_db] = lambda: mock_db
        return mock_db

    def test_update_manual_action(self, client) -> None:
        action = _make_action(
            source="MANUAL", category=None, content="旧内容", suggested_by="admin"
        )
        with mock_current_user(TEST_USERS["ic_engineer"]):
            self._override_db(client, action)
            resp = client.put(
                f"/api/v1/diagnosis/runs/actions/{self.ACTION_ID}",
                headers={"Authorization": "Bearer fake-token"},
                json={"content": "新内容：安排周六检修", "basis": "现场复核确认"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["content"] == "新内容：安排周六检修"
        assert data["basis"] == "现场复核确认"

    def test_update_system_action_rejected(self, client) -> None:
        """系统建议不可编辑（400）。"""
        action = _make_action(source="SYSTEM")
        with mock_current_user(TEST_USERS["admin"]):
            self._override_db(client, action)
            resp = client.put(
                f"/api/v1/diagnosis/runs/actions/{self.ACTION_ID}",
                headers={"Authorization": "Bearer fake-token"},
                json={"content": "改系统建议"},
            )
        assert resp.status_code == 400

    def test_update_not_found(self, client) -> None:
        with mock_current_user(TEST_USERS["admin"]):
            self._override_db(client, None)
            resp = client.put(
                f"/api/v1/diagnosis/runs/actions/{self.ACTION_ID}",
                headers={"Authorization": "Bearer fake-token"},
                json={"content": "x"},
            )
        assert resp.status_code == 404

    def test_delete_system_action(self, client) -> None:
        """系统建议可删除。"""
        action = _make_action(source="SYSTEM")
        with mock_current_user(TEST_USERS["admin"]):
            mock_db = self._override_db(client, action)
            resp = client.delete(
                f"/api/v1/diagnosis/runs/actions/{self.ACTION_ID}",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["deleted"] is True
        mock_db.commit.assert_awaited()

    def test_delete_manual_action(self, client) -> None:
        action = _make_action(source="MANUAL", category=None, suggested_by="admin")
        with mock_current_user(TEST_USERS["ic_engineer"]):
            self._override_db(client, action)
            resp = client.delete(
                f"/api/v1/diagnosis/runs/actions/{self.ACTION_ID}",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200

    def test_delete_not_found(self, client) -> None:
        with mock_current_user(TEST_USERS["admin"]):
            self._override_db(client, None)
            resp = client.delete(
                f"/api/v1/diagnosis/runs/actions/{self.ACTION_ID}",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 404

    def test_delete_forbidden_for_sponsor(self, client) -> None:
        with mock_current_user(TEST_USERS["sponsor"]):
            resp = client.delete(
                f"/api/v1/diagnosis/runs/actions/{self.ACTION_ID}",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 403
