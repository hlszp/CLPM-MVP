"""诊断洞察 API 测试（16 号文 Phase A：F1 loop-archive + F2 compare）。

模式参照 test_diagnosis_v2_api.py：mock db（_seq_execute）+ mock_current_user；
模块热插拔门控通过 patch app.services.diagnosis_insights.is_module_enabled
控制（避免测试环境连真实 DB 读取 module_plugin/sys_config）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
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
