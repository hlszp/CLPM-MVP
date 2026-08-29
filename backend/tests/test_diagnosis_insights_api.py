"""诊断洞察 API 测试（16 号文：F1 loop-archive + F2 compare + F3 coverage + F4 category-cohort）。

模式参照 test_diagnosis_v2_api.py：mock db（_seq_execute）+ mock_current_user；
模块热插拔门控通过 patch app.services.diagnosis_insights.is_module_enabled
控制（避免测试环境连真实 DB 读取 module_plugin/sys_config）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.models.diagnosis_run import DiagnosisRun
from app.models.handling_order import HandlingOrder
from app.models.loop import LoopLedger
from app.services.diagnosis_insights import (
    _build_kpi_trend,
    _direction,
    _dt_to_epoch_ms,
    _freshness_bucket,
)
from tests.conftest import TEST_USERS, mock_current_user

LOOP_ID = str(uuid4())
ORDER_ID = str(uuid4())
ACTION_ID = str(uuid4())


def _seq_execute(results):
    it = iter(results)

    async def _execute(*args, **kwargs):  # noqa: ARG001
        return next(it)

    return _execute


def _modules(*keys: str):
    """patch insights 服务的模块启用判定（默认全部禁用）。"""
    allowed = set(keys)
    return patch(
        "app.services.diagnosis_insights.is_module_enabled",
        side_effect=lambda key: key in allowed,
    )


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _make_loop() -> LoopLedger:
    return LoopLedger(
        id=LOOP_ID,
        tag_name="FIC-101",
        description="进料流量",
        loop_type="FLOW",
        importance_level=2,
    )


def _make_run(
    *,
    days_ago: float = 0,
    category: str | None = "VALVE",
    confidence: float | None = 0.82,
    severity: str | None = "MEDIUM",
    status: str = "SUCCESS",
    run_id: str | None = None,
    operator_results: dict | None = None,
    metric_summary: dict | None = None,
) -> DiagnosisRun:
    return DiagnosisRun(
        id=run_id or str(uuid4()),
        loop_id=LOOP_ID,
        triggered_by="tester",
        trigger_type="MANUAL",
        time_window_start=_now() - timedelta(days=days_ago + 7),
        time_window_end=_now() - timedelta(days=days_ago),
        operator_group="full",
        status=status,
        operator_results=operator_results
        or {
            "stiction_ellipse": {
                "executed": True,
                "detected": True,
                "features": {"stiction_index": 0.45, "fitting_score": 0.92},
            },
            "oscillation": {
                "executed": True,
                "detected": False,
                "features": {"index": 2.1},
            },
        },
        primary_category=category,
        primary_confidence=confidence,
        secondary_categories=[{"category": "TUNING", "confidence": 0.3}],
        severity=severity,
        metric_summary=metric_summary
        or {
            "negative": {"badValueRate": 5.0, "stictionIndex": 45.0},
            "positive": {"score": 60.0},
        },
        review_status="PENDING",
        created_at=_now() - timedelta(days=days_ago),
        finished_at=_now() - timedelta(days=days_ago),
    )


class TestLoopArchiveEndpoint:
    """GET /api/v1/diagnosis/runs/loop-archive（F1）。"""

    def _override_db(self, client, results) -> None:
        from app.core.db import get_db

        mock_db = MagicMock()
        mock_db.execute = _seq_execute(results)
        client.app.dependency_overrides[get_db] = lambda: mock_db

    @staticmethod
    def _archive_results():
        loop = _make_loop()
        run1 = _make_run(days_ago=20, category="VALVE", confidence=0.7)
        run2 = _make_run(days_ago=10, category="VALVE", confidence=0.8)
        run3 = _make_run(days_ago=1, category="TUNING", confidence=0.9, severity="LOW")

        r_loop = MagicMock()
        r_loop.scalar_one_or_none.return_value = loop
        r_stats = MagicMock()
        r_stats.one.return_value = (3, run1.created_at, run3.created_at)
        r_latest = MagicMock()
        r_latest.scalars.return_value.first.return_value = run3
        r_runs = MagicMock()
        r_runs.scalars.return_value.all.return_value = [run1, run2, run3]
        r_snap = MagicMock()
        r_snap.all.return_value = [
            (run1.created_at, Decimal("60.00"), Decimal("5.50")),
            (run2.created_at, Decimal("70.00"), Decimal("4.00")),
            (run3.created_at, Decimal("80.00"), Decimal("2.00")),
        ]
        r_handling = MagicMock()
        r_handling.all.return_value = [
            (ORDER_ID, "HD-20260820-001", "阀门检修", "CLOSED", run1.created_at, run3.created_at)
        ]
        r_tuning = MagicMock()
        r_tuning.all.return_value = [
            (str(uuid4()), "IMC", run2.created_at, run3.created_at),
        ]
        return [r_loop, r_stats, r_latest, r_runs, r_snap, r_handling, r_tuning]

    def test_archive_full_payload(self, client) -> None:
        with (
            mock_current_user(TEST_USERS["admin"]),
            _modules("handling", "tuning"),
        ):
            self._override_db(client, self._archive_results())
            resp = client.get(
                "/api/v1/diagnosis/runs/loop-archive",
                headers={"Authorization": "Bearer fake-token"},
                params={"loopId": LOOP_ID, "window": "30d"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        # loop 静态信息
        assert data["loop"] == {
            "loopId": LOOP_ID,
            "loopName": "FIC-101",
            "loopType": "FLOW",
            "level": 2,
        }
        # 全量摘要
        assert data["summary"]["totalRuns"] == 3
        assert data["summary"]["latestCategory"] == "TUNING"
        assert data["summary"]["latestConfidence"] == 0.9
        assert data["summary"]["firstDiagnosedAt"] is not None
        # run 时间轴升序
        runs = data["runs"]
        assert [r["primaryCategory"] for r in runs] == ["VALVE", "VALVE", "TUNING"]
        assert runs[0]["diagnosedAt"] <= runs[1]["diagnosedAt"] <= runs[2]["diagnosedAt"]
        assert runs[0]["reviewStatus"] == "PENDING"
        assert runs[0]["triggerType"]  # 默认 MANUAL
        # KPI 趋势
        assert data["kpiTrend"]["available"] is True
        assert len(data["kpiTrend"]["series"]["score"]) == 3
        assert data["kpiTrend"]["series"]["oscillationRate"][0]["v"] == 5.5
        # 事件（处置创建+关闭、整定创建+完成），按时间升序
        events = data["events"]
        assert events["handlingEnabled"] is True
        assert events["tuningEnabled"] is True
        types = [(e["type"], e["subtype"]) for e in events["items"]]
        assert ("handling", "created") in types
        assert ("handling", "closed") in types
        assert ("tuning", "created") in types
        assert ("tuning", "completed") in types
        assert events["items"] == sorted(events["items"], key=lambda e: e["at"])
        closed = next(e for e in events["items"] if e["subtype"] == "closed")
        assert closed["refId"] == ORDER_ID

    def test_archive_no_kpi_snapshots(self, client) -> None:
        """评估无快照 → kpiTrend.available=false，不误报。"""
        results = self._archive_results()
        results[4].all.return_value = []  # snapshots 为空
        with (
            mock_current_user(TEST_USERS["admin"]),
            _modules("handling", "tuning"),
        ):
            self._override_db(client, results)
            resp = client.get(
                "/api/v1/diagnosis/runs/loop-archive",
                headers={"Authorization": "Bearer fake-token"},
                params={"loopId": LOOP_ID},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["kpiTrend"]["available"] is False
        assert data["kpiTrend"]["series"]["score"] == []

    def test_archive_modules_disabled_skips_queries(self, client) -> None:
        """处置/整定禁用 → 跳过事件查询段（execute 序列少 2 次），能力字段 false。"""
        results = self._archive_results()[:5]  # 无 handling/tuning 两段
        with (
            mock_current_user(TEST_USERS["admin"]),
            _modules(),  # 全部禁用
        ):
            self._override_db(client, results)
            resp = client.get(
                "/api/v1/diagnosis/runs/loop-archive",
                headers={"Authorization": "Bearer fake-token"},
                params={"loopId": LOOP_ID},
            )
        assert resp.status_code == 200
        events = resp.json()["data"]["events"]
        assert events == {
            "handlingEnabled": False,
            "tuningEnabled": False,
            "items": [],
        }

    def test_archive_loop_not_found(self, client) -> None:
        r = MagicMock()
        r.scalar_one_or_none.return_value = None
        with mock_current_user(TEST_USERS["admin"]):
            self._override_db(client, [r])
            resp = client.get(
                "/api/v1/diagnosis/runs/loop-archive",
                headers={"Authorization": "Bearer fake-token"},
                params={"loopId": LOOP_ID},
            )
        assert resp.status_code == 404

    def test_archive_invalid_window(self, client) -> None:
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/diagnosis/runs/loop-archive",
                headers={"Authorization": "Bearer fake-token"},
                params={"loopId": LOOP_ID, "window": "7d"},
            )
        assert resp.status_code == 400

    def test_archive_accessible_by_sponsor(self, client) -> None:
        """权限全员：SPONSOR 也可查看档案。"""
        results = self._archive_results()[:5]
        with (
            mock_current_user(TEST_USERS["sponsor"]),
            _modules(),
        ):
            self._override_db(client, results)
            resp = client.get(
                "/api/v1/diagnosis/runs/loop-archive",
                headers={"Authorization": "Bearer fake-token"},
                params={"loopId": LOOP_ID},
            )
        assert resp.status_code == 200


class TestCompareEndpoint:
    """GET /api/v1/diagnosis/runs/{id}/compare（F2）。"""

    def _override_db(self, client, results) -> None:
        from app.core.db import get_db

        mock_db = MagicMock()
        mock_db.execute = _seq_execute(results)
        client.app.dependency_overrides[get_db] = lambda: mock_db

    def test_compare_adjacent(self, client) -> None:
        """相邻对比：结论/特征值/KPI 对照 + 方向着色 + verifyPair=false。"""
        base = _make_run(
            days_ago=10,
            category="VALVE",
            confidence=0.6,
            severity="HIGH",
        )
        target = _make_run(
            days_ago=1,
            category="TUNING",
            confidence=0.85,
            severity="LOW",
        )
        target.operator_results = {
            "stiction_ellipse": {
                "executed": True,
                "detected": False,
                "features": {"stiction_index": 0.12, "fitting_score": 0.88},
            },
            "oscillation": {
                "executed": True,
                "detected": False,
                "features": {"index": 2.1},
            },
            "sensor_fault": {"executed": False, "skipReason": "信号缺失", "features": {}},
        }
        target.metric_summary = {
            "negative": {"badValueRate": 2.0, "stictionIndex": 12.0},
            "positive": {"score": 80.0},
        }

        r_target = MagicMock()
        r_target.scalar_one_or_none.return_value = target
        r_verify = MagicMock()
        r_verify.first.return_value = None  # 未关联处置验证
        r_base = MagicMock()
        r_base.scalars.return_value.first.return_value = base

        with (
            mock_current_user(TEST_USERS["admin"]),
            _modules("handling"),
        ):
            self._override_db(client, [r_target, r_verify, r_base])
            resp = client.get(
                f"/api/v1/diagnosis/runs/{target.id}/compare",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["mode"] == "adjacent"
        assert data["verifyPair"] is False
        # base/target 摘要（含时间窗）
        assert data["base"]["runId"] == base.id
        assert data["target"]["runId"] == target.id
        assert data["base"]["windowStart"] is not None
        # 结论变化
        assert data["conclusion"]["primaryCategory"] == {
            "base": "VALVE",
            "target": "TUNING",
            "delta": "changed",
        }
        assert data["conclusion"]["severity"] == {"base": "HIGH", "target": "LOW"}
        assert data["conclusion"]["confidence"]["delta"] == 0.25
        # 特征值对照：反向指标降=改善；正向拟合分降=恶化；持平=flat
        feats = {(f["operator"], f["feature"]): f for f in data["features"]}
        stiction = feats[("stiction_ellipse", "stiction_index")]
        assert stiction["delta"] == round(0.12 - 0.45, 6)
        assert stiction["direction"] == "improved"
        fitting = feats[("stiction_ellipse", "fitting_score")]
        assert fitting["direction"] == "worsened"
        osc = feats[("oscillation", "index")]
        assert osc["delta"] == 0.0
        assert osc["direction"] == "flat"
        # 双侧均未执行的算子不出现
        assert all(f["operator"] != "sensor_fault" for f in data["features"])
        # KPI 对照：negative 反向、positive 正向
        kpis = {k["metric"]: k for k in data["kpi"]}
        assert kpis["score"]["direction"] == "improved"
        assert kpis["score"]["delta"] == 20.0
        assert kpis["badValueRate"]["direction"] == "improved"
        assert kpis["stictionIndex"]["direction"] == "improved"

    def test_compare_adjacent_no_previous(self, client) -> None:
        """首条 run 无相邻前序 → 404。"""
        target = _make_run(days_ago=0)
        r_target = MagicMock()
        r_target.scalar_one_or_none.return_value = target
        r_verify = MagicMock()
        r_verify.first.return_value = None
        r_base = MagicMock()
        r_base.scalars.return_value.first.return_value = None
        with (
            mock_current_user(TEST_USERS["admin"]),
            _modules("handling"),
        ):
            self._override_db(client, [r_target, r_verify, r_base])
            resp = client.get(
                f"/api/v1/diagnosis/runs/{target.id}/compare",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 404

    def test_compare_verify_with_suggestions(self, client) -> None:
        """验证对比：经 suggestion_ids 回溯处置前 run，verifyPair=true。"""
        base = _make_run(days_ago=10, category="VALVE")
        target = _make_run(days_ago=1, category=None, confidence=None, severity=None)
        order = HandlingOrder(
            id=ORDER_ID,
            loop_id=LOOP_ID,
            order_no="HD-20260827-001",
            title="阀门检修",
            source="DIAGNOSIS",
            action_type="VALVE",
            suggestion_ids=[ACTION_ID],
            status="CLOSED",
        )

        r_target = MagicMock()
        r_target.scalar_one_or_none.return_value = target
        r_verify = MagicMock()
        r_verify.first.return_value = (ORDER_ID,)
        r_order = MagicMock()
        r_order.scalars.return_value.first.return_value = order
        r_base = MagicMock()
        r_base.scalars.return_value.first.return_value = base

        with (
            mock_current_user(TEST_USERS["admin"]),
            _modules("handling"),
        ):
            self._override_db(client, [r_target, r_verify, r_order, r_base])
            resp = client.get(
                f"/api/v1/diagnosis/runs/{target.id}/compare",
                headers={"Authorization": "Bearer fake-token"},
                params={"mode": "verify"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["mode"] == "verify"
        assert data["verifyPair"] is True
        assert data["base"]["runId"] == base.id
        assert data["target"]["runId"] == target.id

    def test_compare_verify_manual_order_falls_back_adjacent(self, client) -> None:
        """MANUAL 工单无来源建议 → 回退相邻前序。"""
        base = _make_run(days_ago=10)
        target = _make_run(days_ago=1)
        order = HandlingOrder(
            id=ORDER_ID,
            loop_id=LOOP_ID,
            order_no="HD-20260827-002",
            title="现场处理",
            source="MANUAL",
            action_type="OTHER",
            suggestion_ids=[],
            status="CLOSED",
        )
        r_target = MagicMock()
        r_target.scalar_one_or_none.return_value = target
        r_verify = MagicMock()
        r_verify.first.return_value = (ORDER_ID,)
        r_order = MagicMock()
        r_order.scalars.return_value.first.return_value = order
        r_base = MagicMock()
        r_base.scalars.return_value.first.return_value = base

        with (
            mock_current_user(TEST_USERS["admin"]),
            _modules("handling"),
        ):
            self._override_db(client, [r_target, r_verify, r_order, r_base])
            resp = client.get(
                f"/api/v1/diagnosis/runs/{target.id}/compare",
                headers={"Authorization": "Bearer fake-token"},
                params={"mode": "verify"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["mode"] == "verify"
        assert data["base"]["runId"] == base.id

    def test_compare_verify_not_associated(self, client) -> None:
        """无 verify_run_id 关联 → 404。"""
        target = _make_run(days_ago=1)
        r_target = MagicMock()
        r_target.scalar_one_or_none.return_value = target
        r_verify = MagicMock()
        r_verify.first.return_value = None
        r_order = MagicMock()
        r_order.scalars.return_value.first.return_value = None
        with (
            mock_current_user(TEST_USERS["admin"]),
            _modules("handling"),
        ):
            self._override_db(client, [r_target, r_verify, r_order])
            resp = client.get(
                f"/api/v1/diagnosis/runs/{target.id}/compare",
                headers={"Authorization": "Bearer fake-token"},
                params={"mode": "verify"},
            )
        assert resp.status_code == 404

    def test_compare_verify_handling_disabled(self, client) -> None:
        """处置模块禁用 → verify 模式 404（前端隐藏入口，不置灰）。"""
        target = _make_run(days_ago=1)
        r_target = MagicMock()
        r_target.scalar_one_or_none.return_value = target
        with (
            mock_current_user(TEST_USERS["admin"]),
            _modules(),  # handling 禁用
        ):
            self._override_db(client, [r_target])
            resp = client.get(
                f"/api/v1/diagnosis/runs/{target.id}/compare",
                headers={"Authorization": "Bearer fake-token"},
                params={"mode": "verify"},
            )
        assert resp.status_code == 404

    def test_compare_run_not_found(self, client) -> None:
        r = MagicMock()
        r.scalar_one_or_none.return_value = None
        with mock_current_user(TEST_USERS["admin"]):
            self._override_db(client, [r])
            resp = client.get(
                f"/api/v1/diagnosis/runs/{str(uuid4())}/compare",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 404

    def test_compare_invalid_mode(self, client) -> None:
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                f"/api/v1/diagnosis/runs/{str(uuid4())}/compare",
                headers={"Authorization": "Bearer fake-token"},
                params={"mode": "unknown"},
            )
        assert resp.status_code == 400

    def test_compare_adjacent_without_handling_module(self, client) -> None:
        """处置禁用时相邻对比不受影响（P3 自包含兜底）。"""
        base = _make_run(days_ago=10)
        target = _make_run(days_ago=1)
        r_target = MagicMock()
        r_target.scalar_one_or_none.return_value = target
        r_base = MagicMock()
        r_base.scalars.return_value.first.return_value = base
        with (
            mock_current_user(TEST_USERS["admin"]),
            _modules(),  # handling 禁用 → 无 verify_pair 查询
        ):
            self._override_db(client, [r_target, r_base])
            resp = client.get(
                f"/api/v1/diagnosis/runs/{target.id}/compare",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["mode"] == "adjacent"
        assert data["verifyPair"] is False


class TestInsightsHelpers:
    """纯函数级单测：方向判定 / 时间换算 / LTTB 趋势。"""

    def test_direction_semantics(self) -> None:
        # 正向（升=改善）
        assert _direction(1.0, reverse=False) == "improved"
        assert _direction(-1.0, reverse=False) == "worsened"
        # 反向（降=改善，如 stiction_index/oscillation_rate）
        assert _direction(-0.33, reverse=True) == "improved"
        assert _direction(0.33, reverse=True) == "worsened"
        # 持平 / 不可比较
        assert _direction(0.0, reverse=True) == "flat"
        assert _direction(None, reverse=True) is None

    def test_dt_to_epoch_ms_naive_and_aware(self) -> None:
        naive = datetime(2026, 8, 28, 0, 0, 0)
        aware = naive.replace(tzinfo=UTC)
        assert _dt_to_epoch_ms(naive) == _dt_to_epoch_ms(aware)
        assert _dt_to_epoch_ms(datetime(1970, 1, 1)) == 0

    def test_kpi_trend_empty(self) -> None:
        result = _build_kpi_trend([])
        assert result["available"] is False
        assert result["series"] == {"score": [], "oscillationRate": []}

    def test_kpi_trend_lttb_caps_points(self) -> None:
        """>2000 点触发 LTTB 降采样（90d≈2160 点边界）。"""
        base = datetime(2026, 8, 1)
        snapshots = [
            (base + timedelta(hours=i), Decimal(f"{50 + i % 40}.00"), Decimal(f"{10 + i % 20}.00"))
            for i in range(2160)
        ]
        result: dict[str, Any] = _build_kpi_trend(snapshots)
        assert result["available"] is True
        score = result["series"]["score"]
        osc = result["series"]["oscillationRate"]
        assert len(score) <= 2000
        assert len(score) == len(osc)
        # 首末点保留（LTTB 端点性质）
        assert score[0]["t"] == _dt_to_epoch_ms(base)
        assert score[-1]["t"] == _dt_to_epoch_ms(base + timedelta(hours=2159))

    def test_kpi_trend_none_values_kept(self) -> None:
        base = datetime(2026, 8, 1)
        snapshots = [
            (base, Decimal("80.00"), None),
            (base + timedelta(hours=1), None, Decimal("3.00")),
        ]
        result = _build_kpi_trend(snapshots)
        assert result["series"]["score"][0]["v"] == 80.0
        assert result["series"]["score"][1]["v"] is None
        assert result["series"]["oscillationRate"][0]["v"] is None


# ---------------------------------------------------------------------------
# Phase B：F3 诊断覆盖台账 + F4 共性问题回路组
# ---------------------------------------------------------------------------

LOOP_L1_OK = str(uuid4())  # 1 级 READY，调度 2h 前跑过
LOOP_L1_LAG = str(uuid4())  # 1 级 READY，调度 30h 前 → 滞后
LOOP_L2_NEVER = str(uuid4())  # 2 级 READY，从未排程 → 滞后
LOOP_L3 = str(uuid4())  # 3 级 READY，不排程
LOOP_PARTIAL = str(uuid4())  # 1 级但 status=PARTIAL → 不计入调度应跑


def _loop_row(loop_id: str, tag: str, level: int, status: str = "READY") -> Any:
    return SimpleNamespace(id=loop_id, tag_name=tag, importance_level=level, status=status)


def _coverage_loop_rows() -> list[Any]:
    return [
        _loop_row(LOOP_L1_OK, "FIC-101", 1),
        _loop_row(LOOP_L1_LAG, "FIC-102", 1),
        _loop_row(LOOP_L2_NEVER, "LIC-201", 2),
        _loop_row(LOOP_L3, "TIC-301", 3),
        _loop_row(LOOP_PARTIAL, "PIC-401", 1, status="PARTIAL"),
    ]


class TestCoverageEndpoint:
    """GET /api/v1/diagnosis/coverage（16 号文 F3）。"""

    def _override_db(self, client, results) -> None:
        from app.core.db import get_db

        mock_db = MagicMock()
        mock_db.execute = _seq_execute(results)
        client.app.dependency_overrides[get_db] = lambda: mock_db

    @staticmethod
    def _results(now: datetime) -> list[MagicMock]:
        """默认数据：5 活跃回路覆盖全部 5 个新鲜度档。"""
        r_loops = MagicMock()
        r_loops.all.return_value = _coverage_loop_rows()
        r_success = MagicMock()
        r_success.all.return_value = [
            (LOOP_L1_OK, now - timedelta(hours=2)),  # within24h
            (LOOP_L1_LAG, now - timedelta(days=3)),  # within7d
            (LOOP_L2_NEVER, now - timedelta(days=20)),  # within30d
            (LOOP_L3, now - timedelta(days=40)),  # stale
            # LOOP_PARTIAL 无 SUCCESS run → never
        ]
        r_sched = MagicMock()
        r_sched.all.return_value = [
            (LOOP_L1_OK, now - timedelta(hours=2)),  # 1 级 2h 前 → 不滞后
            (LOOP_L1_LAG, now - timedelta(hours=30)),  # 1 级 30h 前 → 超 25h 滞后
        ]
        r_di = MagicMock()
        r_di.all.return_value = [
            (LOOP_L1_OK, 4, 3),  # 75%
            (LOOP_L1_LAG, 2, 2),  # 100%
            (LOOP_L3, 5, 0),  # 无 DI → 不入榜
        ]
        return [r_loops, r_success, r_sched, r_di]

    def test_coverage_full_admin(self, client) -> None:
        """ADMIN：新鲜度 5 档 + 调度执行（1/2 级滞后、3 级手动）+ DI Top5。"""
        now = _now()
        with mock_current_user(TEST_USERS["admin"]):
            self._override_db(client, self._results(now))
            resp = client.get(
                "/api/v1/diagnosis/coverage",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]

        buckets = {b["key"]: b for b in data["freshness"]["buckets"]}
        assert data["freshness"]["totalLoops"] == 5
        assert [b["key"] for b in data["freshness"]["buckets"]] == [
            "within24h",
            "within7d",
            "within30d",
            "stale",
            "never",
        ]
        assert buckets["within24h"]["count"] == 1
        assert buckets["within24h"]["loops"][0]["loopTagName"] == "FIC-101"
        assert buckets["within7d"]["count"] == 1
        assert buckets["within30d"]["count"] == 1
        assert buckets["stale"]["count"] == 1
        assert buckets["never"]["count"] == 1
        assert buckets["never"]["loops"][0]["loopId"] == LOOP_PARTIAL
        assert buckets["never"]["loops"][0]["lastDiagnosedAt"] is None

        # 调度执行（S3 口径：1 级超 25h 无 SCHEDULED run 计入滞后）
        schedule = data["schedule"]
        assert schedule is not None
        levels = {lv["level"]: lv for lv in schedule["levels"]}
        lv1 = levels[1]
        assert lv1["cadence"] == "daily"
        assert lv1["expectedLoops"] == 2  # LOOP_PARTIAL（PARTIAL 状态）不计入应跑
        assert lv1["lagThresholdHours"] == 25
        assert lv1["laggingCount"] == 1
        assert lv1["lagging"][0]["loopId"] == LOOP_L1_LAG
        assert lv1["lastScheduledAt"] is not None
        lv2 = levels[2]
        assert lv2["cadence"] == "weekly"
        assert lv2["lagThresholdHours"] == 169
        assert lv2["expectedLoops"] == 1
        assert lv2["laggingCount"] == 1  # 从未排程 → 滞后
        assert lv2["lagging"][0]["lastScheduledAt"] is None
        lv3 = levels[3]
        assert lv3["cadence"] == "manual"
        assert lv3["expectedLoops"] == 1
        assert "不排程" in lv3["note"]
        assert "laggingCount" not in lv3

        # 数据不足 Top5：占比降序（100% 在 75% 前），无 DI 回路不入榜
        top = data["dataInsufficient"]["top"]
        assert data["dataInsufficient"]["windowDays"] == 30
        assert [t["loopId"] for t in top] == [LOOP_L1_LAG, LOOP_L1_OK]
        assert top[0]["ratio"] == 1.0
        assert top[1]["ratio"] == 0.75
        assert top[1]["loopTagName"] == "FIC-101"

    def test_coverage_non_admin_hides_schedule(self, client) -> None:
        """非 ADMIN：schedule 整段 None（前端隐藏），且不发调度查询（少一次 execute）。"""
        now = _now()
        results = self._results(now)[:2] + [self._results(now)[3]]  # 跳过 r_sched
        with mock_current_user(TEST_USERS["sponsor"]):
            self._override_db(client, results)
            resp = client.get(
                "/api/v1/diagnosis/coverage",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["schedule"] is None
        assert data["freshness"]["totalLoops"] == 5
        assert len(data["dataInsufficient"]["top"]) == 2

    def test_coverage_empty(self, client) -> None:
        """空态：无回路/无 run → 各档 0，调度应跑 0，DI 榜空。"""
        r_loops = MagicMock()
        r_loops.all.return_value = []
        r_success = MagicMock()
        r_success.all.return_value = []
        r_sched = MagicMock()
        r_sched.all.return_value = []
        r_di = MagicMock()
        r_di.all.return_value = []
        with mock_current_user(TEST_USERS["admin"]):
            self._override_db(client, [r_loops, r_success, r_sched, r_di])
            resp = client.get(
                "/api/v1/diagnosis/coverage",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["freshness"]["totalLoops"] == 0
        assert all(b["count"] == 0 for b in data["freshness"]["buckets"])
        levels = {lv["level"]: lv for lv in data["schedule"]["levels"]}
        assert levels[1]["expectedLoops"] == 0
        assert levels[1]["laggingCount"] == 0
        assert levels[1]["lastScheduledAt"] is None
        assert data["dataInsufficient"]["top"] == []

    def test_coverage_bucket_boundaries(self) -> None:
        """纯函数：24h/7d/30d 边界与 never。"""
        now = _now()
        assert _freshness_bucket(None, now) == "never"
        assert _freshness_bucket(now - timedelta(hours=24), now) == "within24h"
        assert _freshness_bucket(now - timedelta(hours=25), now) == "within7d"
        assert _freshness_bucket(now - timedelta(days=7), now) == "within7d"
        assert _freshness_bucket(now - timedelta(days=30), now) == "within30d"
        assert _freshness_bucket(now - timedelta(days=31), now) == "stale"


class TestCategoryCohortEndpoint:
    """GET /api/v1/diagnosis/category-cohort（16 号文 F4）。"""

    def _override_db(self, client, results) -> None:
        from app.core.db import get_db

        mock_db = MagicMock()
        mock_db.execute = _seq_execute(results)
        client.app.dependency_overrides[get_db] = lambda: mock_db

    @staticmethod
    def _cohort_row(
        loop_id: str,
        tag: str,
        *,
        confidence: float | None = 0.8,
        metric_summary: dict | None = None,
    ) -> Any:
        now = _now()
        return SimpleNamespace(
            loop_id=loop_id,
            tag_name=tag,
            loop_description=f"{tag} 描述",
            importance_level=2,
            run_id=str(uuid4()),
            primary_confidence=Decimal(str(confidence)) if confidence is not None else None,
            severity="MEDIUM",
            last_diagnosed_at=now,
            metric_summary=metric_summary
            or {"positive": {"score": 61.5}, "negative": {"stictionIndex": 40.0}},
        )

    def test_cohort_basic(self, client) -> None:
        r = MagicMock()
        r.all.return_value = [
            self._cohort_row(LOOP_ID, "FIC-101"),
            self._cohort_row(LOOP_L1_LAG, "FIC-102", confidence=None),
        ]
        with mock_current_user(TEST_USERS["admin"]):
            self._override_db(client, [r])
            resp = client.get(
                "/api/v1/diagnosis/category-cohort",
                headers={"Authorization": "Bearer fake-token"},
                params={"category": "VALVE"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["category"] == "VALVE"
        assert data["plantNodeId"] is None
        assert data["total"] == 2
        first, second = data["items"]
        assert first["loopId"] == LOOP_ID
        assert first["loopTagName"] == "FIC-101"
        assert first["importanceLevel"] == 2
        assert first["primaryConfidence"] == 0.8
        assert first["metricSummary"]["positive"]["score"] == 61.5
        assert first["lastDiagnosedAt"] is not None
        assert second["primaryConfidence"] is None

    def test_cohort_with_plant_node(self, client) -> None:
        """plantNodeId 合法 UUID → 递归过滤（参数透传），S4 链路口径。"""
        plant_id = str(uuid4())
        r = MagicMock()
        r.all.return_value = [self._cohort_row(LOOP_ID, "FIC-101")]
        with mock_current_user(TEST_USERS["admin"]):
            self._override_db(client, [r])
            resp = client.get(
                "/api/v1/diagnosis/category-cohort",
                headers={"Authorization": "Bearer fake-token"},
                params={"category": "VALVE", "plantNodeId": plant_id},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["plantNodeId"] == plant_id
        assert data["total"] == 1

    def test_cohort_empty(self, client) -> None:
        """空态：该分类×装置下无回路 → items=[]。"""
        r = MagicMock()
        r.all.return_value = []
        with mock_current_user(TEST_USERS["admin"]):
            self._override_db(client, [r])
            resp = client.get(
                "/api/v1/diagnosis/category-cohort",
                headers={"Authorization": "Bearer fake-token"},
                params={"category": "DESIGN"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["items"] == []
        assert data["total"] == 0

    def test_cohort_invalid_category(self, client) -> None:
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/diagnosis/category-cohort",
                headers={"Authorization": "Bearer fake-token"},
                params={"category": "UNKNOWN"},
            )
        assert resp.status_code == 400

    def test_cohort_invalid_plant_node_id(self, client) -> None:
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/diagnosis/category-cohort",
                headers={"Authorization": "Bearer fake-token"},
                params={"category": "VALVE", "plantNodeId": "not-a-uuid"},
            )
        assert resp.status_code == 400

    def test_cohort_accessible_by_sponsor(self, client) -> None:
        """权限全员（§5.1：F4 与记录页一致）。"""
        r = MagicMock()
        r.all.return_value = []
        with mock_current_user(TEST_USERS["sponsor"]):
            self._override_db(client, [r])
            resp = client.get(
                "/api/v1/diagnosis/category-cohort",
                headers={"Authorization": "Bearer fake-token"},
                params={"category": "TUNING"},
            )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Phase C：F5 发起前数据预检徽标 + F6 复核反馈统计
# ---------------------------------------------------------------------------

LOOP_PC_FULL = str(uuid4())  # 24/24 → sufficient
LOOP_PC_MARGINAL = str(uuid4())  # 15/24 ≈ 0.625 → marginal
LOOP_PC_LOW = str(uuid4())  # 5/24 ≈ 0.208 → insufficient
LOOP_PC_NONE = str(uuid4())  # 无任何快照 → unknown（中性态，不误报）


class TestPrecheckEndpoint:
    """GET /api/v1/diagnosis/precheck（16 号文 F5，D1=a 廉价代理密度徽标）。"""

    def _override_db(self, client, results) -> None:
        from app.core.db import get_db

        mock_db = MagicMock()
        mock_db.execute = _seq_execute(results)
        client.app.dependency_overrides[get_db] = lambda: mock_db

    @staticmethod
    def _density_rows() -> list[tuple]:
        """(loop_id, 窗口内行数, 全量行数)。"""
        return [
            (LOOP_PC_FULL, 24, 500),
            (LOOP_PC_MARGINAL, 15, 500),
            (LOOP_PC_LOW, 5, 500),
            # LOOP_PC_NONE 不出现在聚合结果中（全量 0 行）
        ]

    def test_precheck_levels_and_unknown(self, client) -> None:
        """三态分级 + 无快照回路 unknown（中性态，不误报"不足"）。"""
        r = MagicMock()
        r.all.return_value = self._density_rows()
        with (
            mock_current_user(TEST_USERS["admin"]),
            _modules("assess"),
        ):
            self._override_db(client, [r])
            resp = client.get(
                "/api/v1/diagnosis/precheck",
                headers={"Authorization": "Bearer fake-token"},
                params={
                    "loopIds": ",".join([LOOP_PC_FULL, LOOP_PC_MARGINAL, LOOP_PC_LOW, LOOP_PC_NONE])
                },
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["assessEnabled"] is True
        assert data["window"] == "24h"
        assert data["expectedRows"] == 24
        items = {i["loopId"]: i for i in data["items"]}
        assert items[LOOP_PC_FULL]["level"] == "sufficient"
        assert items[LOOP_PC_FULL]["ratio"] == 1.0
        assert items[LOOP_PC_MARGINAL]["level"] == "marginal"
        assert items[LOOP_PC_MARGINAL]["rowCount"] == 15
        assert items[LOOP_PC_LOW]["level"] == "insufficient"
        assert items[LOOP_PC_LOW]["ratio"] == round(5 / 24, 4)
        # 无任何快照 → unknown，rowCount=0，ratio=null
        none_item = items[LOOP_PC_NONE]
        assert none_item["level"] == "unknown"
        assert none_item["ratio"] is None

    def test_precheck_window_7d(self, client) -> None:
        """7d 窗口预期 168 行，分级随窗口缩放。"""
        r = MagicMock()
        r.all.return_value = [(LOOP_PC_FULL, 168, 500)]
        with (
            mock_current_user(TEST_USERS["admin"]),
            _modules("assess"),
        ):
            self._override_db(client, [r])
            resp = client.get(
                "/api/v1/diagnosis/precheck",
                headers={"Authorization": "Bearer fake-token"},
                params={"loopIds": LOOP_PC_FULL, "window": "7d"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["expectedRows"] == 168
        assert data["items"][0]["level"] == "sufficient"

    def test_precheck_assess_disabled_skips_query(self, client) -> None:
        """评估模块禁用 → assessEnabled=false、items 空，且不发密度查询（P3 降级）。"""
        mock_db = MagicMock()

        async def _boom(*args, **kwargs):  # noqa: ARG001
            raise AssertionError("评估禁用时不应发起密度查询")

        mock_db.execute = _boom
        from app.core.db import get_db

        with (
            mock_current_user(TEST_USERS["admin"]),
            _modules(),  # assess 禁用
        ):
            client.app.dependency_overrides[get_db] = lambda: mock_db
            resp = client.get(
                "/api/v1/diagnosis/precheck",
                headers={"Authorization": "Bearer fake-token"},
                params={"loopIds": LOOP_PC_FULL},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["assessEnabled"] is False
        assert data["items"] == []

    def test_precheck_accessible_by_sponsor(self, client) -> None:
        """权限全员（§5.1：F5 与记录页一致）。"""
        r = MagicMock()
        r.all.return_value = []
        with (
            mock_current_user(TEST_USERS["sponsor"]),
            _modules("assess"),
        ):
            self._override_db(client, [r])
            resp = client.get(
                "/api/v1/diagnosis/precheck",
                headers={"Authorization": "Bearer fake-token"},
                params={"loopIds": LOOP_PC_FULL},
            )
        assert resp.status_code == 200
        assert resp.json()["data"]["items"][0]["level"] == "unknown"

    def test_precheck_invalid_window(self, client) -> None:
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/diagnosis/precheck",
                headers={"Authorization": "Bearer fake-token"},
                params={"loopIds": LOOP_PC_FULL, "window": "1h"},
            )
        assert resp.status_code == 400

    def test_precheck_empty_loop_ids(self, client) -> None:
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/diagnosis/precheck",
                headers={"Authorization": "Bearer fake-token"},
                params={"loopIds": " , ,"},
            )
        assert resp.status_code == 400

    def test_precheck_over_limit(self, client) -> None:
        """批量上限 10（§5.3，与发起上限一致）。"""
        ids = ",".join(str(uuid4()) for _ in range(11))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/diagnosis/precheck",
                headers={"Authorization": "Bearer fake-token"},
                params={"loopIds": ids},
            )
        assert resp.status_code == 400

    def test_precheck_invalid_loop_id(self, client) -> None:
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/diagnosis/precheck",
                headers={"Authorization": "Bearer fake-token"},
                params={"loopIds": "not-a-uuid"},
            )
        assert resp.status_code == 400

    def test_precheck_level_boundaries(self) -> None:
        """纯函数：红档 0.5 与调度密度门禁同口径；琥珀带 0.5~0.9。"""
        from app.services.diagnosis_insights import _precheck_level

        assert _precheck_level(0.0) == "insufficient"
        assert _precheck_level(0.4999) == "insufficient"
        assert _precheck_level(0.5) == "marginal"
        assert _precheck_level(0.8999) == "marginal"
        assert _precheck_level(0.9) == "sufficient"
        assert _precheck_level(1.0) == "sufficient"


def _fb_row(
    *,
    primary: str | None = "VALVE",
    secondary: list | None = None,
    pending: list | None = None,
    reviewed: bool = True,
    review_results: list | None = None,
    operator_results: dict | None = None,
) -> tuple:
    """F6 全量扫描行：(primary, secondary, pending, review_status, review_results, ops)。"""
    return (
        primary,
        secondary if secondary is not None else [],
        pending if pending is not None else [],
        "REVIEWED" if reviewed else "PENDING",
        review_results if review_results is not None else ["VALVE"],
        operator_results if operator_results is not None else {},
    )


def _stiction_detected() -> dict:
    return {"stiction_ellipse": {"executed": True, "detected": True, "features": {}}}


class TestReviewFeedbackEndpoint:
    """GET /api/v1/diagnosis/review-feedback（16 号文 F6，D4 样本门槛 ≥10，仅 ADMIN）。"""

    def _override_db(self, client, rows: list[tuple]) -> None:
        from app.core.db import get_db

        r = MagicMock()
        r.all.return_value = rows
        mock_db = MagicMock()
        mock_db.execute = _seq_execute([r])
        client.app.dependency_overrides[get_db] = lambda: mock_db

    @staticmethod
    def _op(data: dict, name: str) -> dict:
        return next(o for o in data["operators"] if o["operator"] == name)

    def test_feedback_operator_overturn_hint(self, client) -> None:
        """12 样本 6 确认 6 改判 → 改判率 0.5 > 0.4 → tuningHint=true + 去向 Top3。"""
        rows = [
            # 6 条确认（复核含 VALVE）
            *[_fb_row(operator_results=_stiction_detected()) for _ in range(6)],
            # 6 条改判（复核改判为 TUNING）
            *[
                _fb_row(review_results=["TUNING"], operator_results=_stiction_detected())
                for _ in range(6)
            ],
        ]
        with mock_current_user(TEST_USERS["admin"]):
            self._override_db(client, rows)
            resp = client.get(
                "/api/v1/diagnosis/review-feedback",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["sampleMin"] == 10
        assert data["overturnHintThreshold"] == 0.4
        assert data["totalRuns"] == 12
        assert data["reviewedRuns"] == 12

        op = self._op(data, "stiction_ellipse")
        assert op["displayName"]
        assert op["category"] == "VALVE"  # VALVE_STICTION 症状 → VALVE 归因
        assert op["detectedCount"] == 12
        assert op["reviewedCount"] == 12
        assert op["reviewRate"] == 1.0
        assert op["sampleSize"] == 12
        assert op["insufficientSample"] is False
        assert op["confirmRate"] == 0.5
        assert op["overturnRate"] == 0.5
        assert op["tuningHint"] is True  # > 40% 琥珀提示
        assert op["overturnTop"] == [{"category": "TUNING", "count": 6}]

        # 分类维度：VALVE 检出 12、复核 12、改判 6
        cat = next(c for c in data["categories"] if c["category"] == "VALVE")
        assert cat["detectedCount"] == 12
        assert cat["reviewedCount"] == 12
        assert cat["confirmRate"] == 0.5
        assert cat["overturnRate"] == 0.5
        assert cat["overturnTop"] == [{"category": "TUNING", "count": 6}]

    def test_feedback_sample_gate(self, client) -> None:
        """D4：样本 <10 → insufficientSample=true，比例/去向/提示全置空。"""
        rows = [
            _fb_row(review_results=["TUNING"], operator_results=_stiction_detected())
            for _ in range(9)  # 9 条全改判，但 < 10
        ]
        with mock_current_user(TEST_USERS["admin"]):
            self._override_db(client, rows)
            resp = client.get(
                "/api/v1/diagnosis/review-feedback",
                headers={"Authorization": "Bearer fake-token"},
            )
        data = resp.json()["data"]
        op = self._op(data, "stiction_ellipse")
        assert op["sampleSize"] == 9
        assert op["insufficientSample"] is True
        assert op["confirmRate"] is None
        assert op["overturnRate"] is None
        assert op["overturnTop"] == []
        assert op["tuningHint"] is False  # 小样本不误导

    def test_feedback_pending_review_excluded(self, client) -> None:
        """pending_review 命中不计入改判分母（§5.1）：sample 不增，记 pendingExcludedCount。"""
        rows = [
            _fb_row(
                primary="TUNING",
                pending=[{"category": "VALVE", "confidence": 0.5}],
                review_results=["TUNING"],
                operator_results=_stiction_detected(),
            )
            for _ in range(12)
        ]
        with mock_current_user(TEST_USERS["admin"]):
            self._override_db(client, rows)
            resp = client.get(
                "/api/v1/diagnosis/review-feedback",
                headers={"Authorization": "Bearer fake-token"},
            )
        data = resp.json()["data"]
        op = self._op(data, "stiction_ellipse")
        assert op["detectedCount"] == 12
        assert op["reviewedCount"] == 12
        assert op["pendingExcludedCount"] == 12
        assert op["sampleSize"] == 0  # 全部 pending 排除，不入改判分母
        assert op["insufficientSample"] is True
        assert op["tuningHint"] is False

    def test_feedback_only_executed_detected_counted(self, client) -> None:
        """只统计 executed=true 且 detected=true 的算子；未执行/未命中不计。"""
        rows = [
            _fb_row(
                operator_results={
                    "stiction_ellipse": {"executed": True, "detected": True},
                    "stiction_kano": {"executed": False, "skipReason": "信号缺失"},
                    "oscillation_iae": {"executed": True, "detected": False},
                }
            )
        ]
        with mock_current_user(TEST_USERS["admin"]):
            self._override_db(client, rows)
            resp = client.get(
                "/api/v1/diagnosis/review-feedback",
                headers={"Authorization": "Bearer fake-token"},
            )
        data = resp.json()["data"]
        assert self._op(data, "stiction_ellipse")["detectedCount"] == 1
        assert self._op(data, "stiction_kano")["detectedCount"] == 0
        assert self._op(data, "oscillation_iae")["detectedCount"] == 0

    def test_feedback_not_adopted_excluded(self, client) -> None:
        """算子检出但机器未采纳其映射分类（非主/次分类）→ 无从改判，不入分母。"""
        rows = [
            _fb_row(
                primary="TUNING",
                review_results=["PROCESS"],  # 复核改判 TUNING，但与 stiction 无关
                operator_results=_stiction_detected(),
            )
            for _ in range(12)
        ]
        with mock_current_user(TEST_USERS["admin"]):
            self._override_db(client, rows)
            resp = client.get(
                "/api/v1/diagnosis/review-feedback",
                headers={"Authorization": "Bearer fake-token"},
            )
        data = resp.json()["data"]
        op = self._op(data, "stiction_ellipse")
        assert op["detectedCount"] == 12
        assert op["reviewedCount"] == 12
        assert op["sampleSize"] == 0  # 机器未采纳 VALVE → 不入分母
        assert op["pendingExcludedCount"] == 0
        # 分类维度 TUNING 全改判 → PROCESS
        cat = next(c for c in data["categories"] if c["category"] == "TUNING")
        assert cat["detectedCount"] == 12
        assert cat["overturnRate"] == 1.0
        assert cat["overturnTop"] == [{"category": "PROCESS", "count": 12}]

    def test_feedback_secondary_affirmed(self, client) -> None:
        """映射分类为次分类也算机器采纳（入改判分母）。"""
        rows = [
            _fb_row(
                primary="TUNING",
                secondary=[{"category": "VALVE", "confidence": 0.6}],
                review_results=["TUNING"],  # 复核不含 VALVE → 改判
                operator_results=_stiction_detected(),
            )
            for _ in range(11)
        ]
        with mock_current_user(TEST_USERS["admin"]):
            self._override_db(client, rows)
            resp = client.get(
                "/api/v1/diagnosis/review-feedback",
                headers={"Authorization": "Bearer fake-token"},
            )
        op = self._op(resp.json()["data"], "stiction_ellipse")
        assert op["sampleSize"] == 11
        assert op["overturnRate"] == 1.0
        assert op["tuningHint"] is True

    def test_feedback_unreviewed_not_in_sample(self, client) -> None:
        """未复核 run 计入检出/复核率分母，但不入改判分母。"""
        rows = [
            *[_fb_row(operator_results=_stiction_detected()) for _ in range(10)],
            *[
                _fb_row(reviewed=False, review_results=[], operator_results=_stiction_detected())
                for _ in range(5)
            ],
        ]
        with mock_current_user(TEST_USERS["admin"]):
            self._override_db(client, rows)
            resp = client.get(
                "/api/v1/diagnosis/review-feedback",
                headers={"Authorization": "Bearer fake-token"},
            )
        op = self._op(resp.json()["data"], "stiction_ellipse")
        assert op["detectedCount"] == 15
        assert op["reviewedCount"] == 10
        assert op["reviewRate"] == round(10 / 15, 4)
        assert op["sampleSize"] == 10

    def test_feedback_empty(self, client) -> None:
        """空态：无 run → 各维度 0，全部样本不足，reviewRate=null。"""
        with mock_current_user(TEST_USERS["admin"]):
            self._override_db(client, [])
            resp = client.get(
                "/api/v1/diagnosis/review-feedback",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["totalRuns"] == 0
        assert data["reviewedRuns"] == 0
        assert len(data["operators"]) == 11  # 注册表 11 元算子全列出
        assert len(data["categories"]) == 8
        assert all(o["detectedCount"] == 0 for o in data["operators"])
        assert all(o["reviewRate"] is None for o in data["operators"])
        assert all(o["insufficientSample"] is True for o in data["operators"])
        assert all(c["insufficientSample"] is True for c in data["categories"])

    def test_feedback_admin_only(self, client) -> None:
        """权限：F6 仅 ADMIN（§5.1，与诊断配置页口径一致）。"""
        with mock_current_user(TEST_USERS["sponsor"]):
            self._override_db(client, [])
            resp = client.get(
                "/api/v1/diagnosis/review-feedback",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 403
