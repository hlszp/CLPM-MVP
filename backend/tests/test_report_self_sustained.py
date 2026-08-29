"""报告自持端点测试（报告模块优化 P0-10 / P2-5，2026-08）。

覆盖 R1 自持链路（方案 §3.2，模块禁用组合下报告页完整可用）：
- services/handling_stats.build_handling_statistics：聚合结构（默认月度口径 /
  空数据归档态 / 时间窗+装置筛选下钻）；P2-1 闭环增强字段（SLA 达成率 /
  建议漏斗 / 整改有效率 / 人员工作量 MV 降级）；
- services/report_stats.get_benefit：P2-3 整定执行区块（算法/状态分布 /
  回滚率 / 加权平均拟合度 / 拟合度四桶 / 最近批次散点归一化）；
- services/report_stats.get_benefit_orders：P2-4 逐工单前后对比明细；
- GET /reports/handling-statistics：参数透传（months/startDate/endDate/plantNodeId）；
- GET /reports/diagnosis-runs 分页明细 + /export CSV（≤5000 行，D4）；
- GET /reports/benefit/orders：分页与筛选透传；
- 结构性断言：自持端点不引用 is_module_enabled（可插拔门禁不穿透报告域）。

实现方式：SQL 文本分发打桩，无需真实 PG（CI 单测即覆盖）。
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from app.api.v1.endpoints import reports as rp
from app.services import alert_stats as als
from app.services import data_quality_stats as dqs
from app.services import handling_stats as hs
from app.services import report_stats as rs
from tests.conftest import TEST_USERS, mock_current_user

START = datetime(2026, 6, 1)
END = datetime(2026, 8, 1)


class _FakeResult:
    def __init__(
        self,
        rows: list[Any] | None = None,
        one_row: Any = None,
        scalar_one: Any = None,
    ) -> None:
        self._rows = rows or []
        self._one = one_row
        self._scalar_one = scalar_one

    def one(self) -> Any:
        return self._one

    def one_or_none(self) -> Any:
        return self._one

    def all(self) -> list[Any]:
        return self._rows

    def scalar_one(self) -> Any:
        return self._scalar_one

    def scalar(self) -> Any:
        return self._scalar_one


# ---------------------------------------------------------------------------
# build_handling_statistics（service 层）
# ---------------------------------------------------------------------------


def _make_stats_db(
    *,
    summary_row: Any = None,
    reject_row: Any = None,
    monthly_rows: list[Any] | None = None,
    by_type_rows: list[Any] | None = None,
    by_unit_rows: list[Any] | None = None,
    top_rows: list[Any] | None = None,
    plant_rows: list[Any] | None = None,
    subtree_rows: list[Any] | None = None,
    sla_row: Any = None,
    funnel_rows: list[Any] | None = None,
    workload_rows: list[Any] | None = None,
    mv_error: bool = False,
    captured: list[str] | None = None,
) -> AsyncMock:
    """按 SQL 文本特征分发预制查询结果（与聚合内部查询顺序无关）。"""

    async def _execute(stmt: Any, _params: Any = None) -> _FakeResult:
        sql = str(stmt)
        if captured is not None:
            captured.append(sql)
        if "WITH RECURSIVE node_tree" in sql:
            return _FakeResult(rows=subtree_rows or [])
        if "closed_period" in sql:
            return _FakeResult(
                one_row=summary_row
                or SimpleNamespace(
                    closed_period=2,
                    closed_total=5,
                    verified_total=6,
                    ineffective_total=1,
                    avg_cycle_hours=12.5,
                    avg_schedule_hours=8.0,
                    avg_kpi_delta=3.2,
                )
            )
        if "reviewed_total" in sql:
            return _FakeResult(
                one_row=reject_row or SimpleNamespace(reviewed_total=10, rejected_total=2)
            )
        if "AT TIME ZONE 'Asia/Shanghai'" in sql:
            return _FakeResult(rows=monthly_rows or [])
        if "GROUP BY ho.action_type" in sql:
            return _FakeResult(
                rows=by_type_rows
                if by_type_rows is not None
                else [SimpleNamespace(action_type="TUNING", cnt=3)]
            )
        if "COALESCE(pn.name" in sql:
            return _FakeResult(
                rows=by_unit_rows
                if by_unit_rows is not None
                else [SimpleNamespace(unit="氧化装置", closed=4)]
            )
        if "ORDER BY agg.ho_reopened" in sql:
            return _FakeResult(
                rows=top_rows
                if top_rows is not None
                else [
                    SimpleNamespace(
                        loop_id="l1",
                        loop_tag_name="TIC-101",
                        importance_level="A",
                        unit_id="u1",
                        order_total=6,
                        ho_reopened=2,
                        ho_ineffective=1,
                        last_closed_kpi_delta=5.5,
                    )
                ]
            )
        # --- P2-1 闭环增强查询 ---
        if "sla_on_time" in sql:
            return _FakeResult(
                one_row=sla_row
                or SimpleNamespace(
                    sla_warn_count=1,
                    sla_breach_count=2,
                    sla_closed_total=4,
                    sla_on_time=3,
                    effective_count=5,
                    ineffective_count=1,
                )
            )
        if "GROUP BY su.status" in sql:
            return _FakeResult(
                rows=funnel_rows
                if funnel_rows is not None
                else [
                    SimpleNamespace(status="PENDING", cnt=3),
                    SimpleNamespace(status="CONVERTED", cnt=5),
                    SimpleNamespace(status="REJECTED", cnt=2),
                ]
            )
        if "FROM mv_staff_workload" in sql:
            if mv_error:
                raise RuntimeError("relation mv_staff_workload does not exist")
            return _FakeResult(
                rows=workload_rows
                if workload_rows is not None
                else [
                    SimpleNamespace(
                        user_name="张三",
                        active_count=2,
                        closed_count=4,
                        sla_warned_count=1,
                    )
                ]
            )
        if "SELECT id, name, parent_id FROM plant_node" in sql:
            return _FakeResult(
                rows=plant_rows
                if plant_rows is not None
                else [SimpleNamespace(id="u1", name="氧化装置", parent_id=None)]
            )
        raise AssertionError(f"未预期的 SQL: {sql[:120]}")

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=_execute)
    return db


class TestBuildHandlingStatistics:
    async def test_summary_structure_default_monthly_window(self) -> None:
        """默认口径：summary 7 指标 + 近 N 月空月补零 + topLoops 装置路径回溯。"""
        db = _make_stats_db(monthly_rows=[SimpleNamespace(month="2026-08", closed=2, verified=3)])

        data = await hs.build_handling_statistics(db, months=6)

        s = data["summary"]
        assert s["closedThisMonth"] == 2
        assert s["closeRate"] == round(5 / 6, 4)
        assert s["ineffectiveRate"] == round(1 / 6, 4)
        assert s["rejectRate"] == 0.2
        assert s["avgCycleHours"] == 12.5
        assert s["avgScheduleHours"] == 8.0
        assert s["avgKpiDelta"] == 3.2

        monthly = data["monthly"]
        assert len(monthly) == 6
        assert monthly[-1] == {"month": "2026-08", "closed": 2, "closeRate": round(2 / 3, 4)}
        assert all(m["closed"] == 0 and m["closeRate"] is None for m in monthly[:-1])

        assert data["byType"][0]["label"] == "参数整定"
        assert data["byUnit"] == [{"unit": "氧化装置", "closed": 4}]
        top = data["topLoops"][0]
        assert top["loopTagName"] == "TIC-101"
        assert top["unitPath"] == "氧化装置"
        assert top["reopened"] == 2
        assert top["lastClosedKpiDelta"] == 5.5

    async def test_empty_data_archived_state(self) -> None:
        """空数据（可插拔模块从未接入的归档态）：指标 null、分布空列表、不误导为 0 率。"""
        db = _make_stats_db(
            summary_row=SimpleNamespace(
                closed_period=0,
                closed_total=0,
                verified_total=0,
                ineffective_total=0,
                avg_cycle_hours=None,
                avg_schedule_hours=None,
                avg_kpi_delta=None,
            ),
            reject_row=SimpleNamespace(reviewed_total=0, rejected_total=0),
            monthly_rows=[],
            by_type_rows=[],
            by_unit_rows=[],
            top_rows=[],
            plant_rows=[],
        )

        data = await hs.build_handling_statistics(db, months=3)

        s = data["summary"]
        assert s["closedThisMonth"] == 0
        assert s["closeRate"] is None
        assert s["avgCycleHours"] is None
        assert s["rejectRate"] is None
        assert len(data["monthly"]) == 3
        assert data["byType"] == []
        assert data["byUnit"] == []
        assert data["topLoops"] == []

    async def test_time_window_and_plant_subtree_filter(self) -> None:
        """时间窗 + 装置下钻：触发 WITH RECURSIVE 子树解析，月度按窗口逐月展开。"""
        captured: list[str] = []
        db = _make_stats_db(
            monthly_rows=[SimpleNamespace(month="2026-06", closed=1, verified=1)],
            plant_rows=[SimpleNamespace(id="u1", name="氧化装置", parent_id=None)],
            subtree_rows=[SimpleNamespace(id="u1")],
            captured=captured,
        )

        data = await hs.build_handling_statistics(
            db,
            months=6,
            start=START,
            end=END,
            plant_node_id="pn-1",
        )

        # 子树下钻被触发（WITH RECURSIVE node_tree）
        assert any("WITH RECURSIVE node_tree" in sql for sql in captured)
        # 时间窗过滤下推到工单/建议聚合（:win_start 半开区间）
        assert any(":win_start" in sql and "closed_period" in sql for sql in captured)
        # 窗口逐月展开：2026-06 ~ 2026-08（3 桶）
        assert [m["month"] for m in data["monthly"]] == ["2026-06", "2026-07", "2026-08"]


class TestHandlingStatsP2Enhanced:
    """P2-1 闭环增强字段（方案 §5.1）：SLA / 建议漏斗 / 整改有效率 / 人员工作量。"""

    async def test_p2_fields_structure(self) -> None:
        """向后兼容增字段：sla/suggestionFunnel/verifyResult/staffWorkload 口径正确。"""
        db = _make_stats_db()

        data = await hs.build_handling_statistics(db, months=6)

        sla = data["sla"]
        assert sla["onTimeRate"] == 0.75  # 3/4 按时闭环
        assert sla["onTimeClosed"] == 3
        assert sla["slaClosedTotal"] == 4
        assert sla["warnCount"] == 1
        assert sla["breachCount"] == 2

        funnel = {f["status"]: f for f in data["suggestionFunnel"]}
        # 五态固定顺序全量返回，未出现的态补 0
        assert [f["status"] for f in data["suggestionFunnel"]] == [
            "PENDING",
            "ACCEPTED",
            "CONVERTED",
            "REJECTED",
            "IGNORED",
        ]
        assert funnel["PENDING"]["count"] == 3
        assert funnel["CONVERTED"]["count"] == 5
        assert funnel["REJECTED"]["count"] == 2
        assert funnel["ACCEPTED"]["count"] == 0
        assert funnel["PENDING"]["label"] == "待审核"

        vr = data["verifyResult"]
        assert vr["effective"] == 5
        assert vr["ineffective"] == 1
        assert vr["effectiveRate"] == round(5 / 6, 4)

        assert data["staffWorkload"] == [
            {"userName": "张三", "activeCount": 2, "closedCount": 4, "slaWarnedCount": 1}
        ]

    async def test_p2_empty_data_archived_state(self) -> None:
        """空数据归档态：SLA/整改有效率为 null，漏斗补零，工作量空列表。"""
        db = _make_stats_db(
            sla_row=SimpleNamespace(
                sla_warn_count=0,
                sla_breach_count=0,
                sla_closed_total=0,
                sla_on_time=0,
                effective_count=0,
                ineffective_count=0,
            ),
            funnel_rows=[],
            workload_rows=[],
        )

        data = await hs.build_handling_statistics(db, months=3)

        assert data["sla"]["onTimeRate"] is None
        assert data["sla"]["warnCount"] == 0
        assert data["verifyResult"]["effectiveRate"] is None
        assert all(f["count"] == 0 for f in data["suggestionFunnel"])
        assert data["staffWorkload"] == []

    async def test_p2_mv_unavailable_degrades_to_empty(self) -> None:
        """mv_staff_workload 不可用（缺失/未刷新）时降级为空列表，不影响其余聚合。"""
        db = _make_stats_db(mv_error=True)

        data = await hs.build_handling_statistics(db, months=3)

        assert data["staffWorkload"] == []
        # 其余区块不受影响
        assert data["sla"]["onTimeRate"] == 0.75
        assert data["summary"]["closedThisMonth"] == 2

    async def test_p2_filters_propagate_to_new_queries(self) -> None:
        """时间窗+装置过滤下推到 SLA/漏斗聚合（人员工作量 MV 为全量口径除外）。"""
        captured: list[str] = []
        db = _make_stats_db(
            subtree_rows=[SimpleNamespace(id="u1")],
            captured=captured,
        )

        await hs.build_handling_statistics(db, months=6, start=START, end=END, plant_node_id="pn-1")

        sla_sql = next(s for s in captured if "sla_on_time" in s)
        assert ":win_start" in sla_sql and ":unit_ids" in sla_sql
        funnel_sql = next(s for s in captured if "GROUP BY su.status" in s)
        assert ":win_start" in funnel_sql and ":unit_ids" in funnel_sql


# ---------------------------------------------------------------------------
# 自持端点
# ---------------------------------------------------------------------------


class TestReportHandlingStatisticsEndpoint:
    def test_params_passthrough(self, client, mock_db, monkeypatch) -> None:
        """months/startDate/endDate/plantNodeId 全量透传到 service。"""
        captured: dict[str, Any] = {}

        async def _fake_stats(db, *, months=6, start=None, end=None, plant_node_id=None):
            captured.update(months=months, start=start, end=end, plant_node_id=plant_node_id)
            return {"summary": {}, "monthly": [], "byType": [], "byUnit": [], "topLoops": []}

        monkeypatch.setattr(rp, "build_handling_statistics", _fake_stats)
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/reports/handling-statistics",
                params={
                    "months": 6,
                    "startDate": "2026-07-01",
                    "endDate": "2026-08-01",
                    "plantNodeId": "pn-1",
                },
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 200
        assert resp.json()["code"] == "0"
        assert captured["months"] == 6
        assert captured["start"] == datetime(2026, 7, 1)
        # end 为半开区间（次日 0 点，_parse_date_range +1 天）
        assert captured["end"] == datetime(2026, 8, 2)
        assert captured["plant_node_id"] == "pn-1"

    def test_default_months_only(self, client, mock_db, monkeypatch) -> None:
        """不传筛选时与 /handling/statistics 行为口径一致（全量聚合）。"""
        captured: dict[str, Any] = {}

        async def _fake_stats(db, *, months=6, start=None, end=None, plant_node_id=None):
            captured.update(start=start, end=end, plant_node_id=plant_node_id)
            return {"summary": {}}

        monkeypatch.setattr(rp, "build_handling_statistics", _fake_stats)
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/reports/handling-statistics",
                params={"months": 12},
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 200
        assert captured == {"start": None, "end": None, "plant_node_id": None}


def _make_run_row(tag: str = "TIC-101") -> tuple[Any, str]:
    run = SimpleNamespace(
        id="run-1",
        created_at=datetime(2026, 7, 15, 10, 30, tzinfo=UTC).replace(tzinfo=None),
        primary_category="OSCILLATION",
        secondary_categories=[],
        primary_confidence=0.85,
        severity="HIGH",
        time_window_start=datetime(2026, 7, 15, 9, 0),
        time_window_end=datetime(2026, 7, 15, 10, 0),
        triggered_by="admin",
        status="SUCCESS",
    )
    return run, tag


class TestReportDiagnosisRunsEndpoint:
    def test_list_paginated(self, client, mock_db, monkeypatch) -> None:
        """分页明细：total 计数 + 行结构经 _run_to_summary 契约。"""

        async def _execute(stmt: Any, _params: Any = None) -> _FakeResult:
            sql = str(stmt).upper()
            if "COUNT(*)" in sql:
                return _FakeResult(scalar_one=42)
            if "ORDER BY" in sql:
                return _FakeResult(rows=[_make_run_row()])
            raise AssertionError(f"未预期的 SQL: {str(stmt)[:120]}")

        mock_db.execute = AsyncMock(side_effect=_execute)
        monkeypatch.setattr(
            rp, "_run_to_summary", lambda run, tag: {"id": "run-1", "loopTagName": tag}
        )

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/reports/diagnosis-runs",
                params={"page": 2, "pageSize": 20, "severity": "HIGH"},
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert body["data"]["total"] == 42
        assert body["data"]["page"] == 2
        assert body["data"]["pageSize"] == 20
        assert body["data"]["items"][0]["loopTagName"] == "TIC-101"

    def test_export_csv_with_limit(self, client, mock_db, monkeypatch) -> None:
        """CSV 导出：列头中文化、行数上限 5000（D4）。"""
        assert rp._REPORT_RUN_EXPORT_LIMIT == 5000

        async def _execute(stmt: Any, _params: Any = None) -> _FakeResult:
            if "LIMIT" in str(stmt).upper():
                return _FakeResult(rows=[_make_run_row()])
            raise AssertionError(f"未预期的 SQL: {str(stmt)[:120]}")

        mock_db.execute = AsyncMock(side_effect=_execute)
        monkeypatch.setattr(rp, "_run_to_summary", lambda run, tag: {})

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/reports/diagnosis-runs/export",
                params={"startDate": "2026-07-01", "endDate": "2026-08-01"},
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 200
        assert "text/csv" in resp.headers["content-type"]
        text = resp.text
        assert "时间" in text and "主分类" in text
        assert "OSCILLATION" in text


# ---------------------------------------------------------------------------
# 数据质量聚合（P1-1，方案 §4.1）
# ---------------------------------------------------------------------------


def _make_dq_db(
    *,
    loop_rows: list[Any] | None = None,
    kpi_rows: list[Any] | None = None,
    trend_rows: list[Any] | None = None,
    integrity_rows: list[Any] | None = None,
    fitness_rows: list[Any] | None = None,
    confidence_rows: list[Any] | None = None,
    plant_rows: list[Any] | None = None,
    subtree_rows: list[Any] | None = None,
    captured: list[str] | None = None,
) -> AsyncMock:
    """数据质量聚合打桩：按 SQL 文本特征分发预制结果。"""

    async def _execute(stmt: Any, _params: Any = None) -> _FakeResult:
        sql = str(stmt)
        if captured is not None:
            captured.append(sql)
        if "WITH RECURSIVE node_tree" in sql:
            return _FakeResult(rows=subtree_rows or [])
        if "DISTINCT ON (lis.loop_id)" in sql:
            return _FakeResult(rows=integrity_rows or [])
        if "DISTINCT ON (k.loop_id)" in sql:
            return _FakeResult(rows=fitness_rows or [])
        if "FROM loop_confidence_latest" in sql:
            return _FakeResult(rows=confidence_rows or [])
        if "GROUP BY k.loop_id" in sql:
            return _FakeResult(rows=kpi_rows or [])
        if "date_trunc('day'" in sql:
            return _FakeResult(rows=trend_rows or [])
        if "ORDER BY ll.tag_name" in sql:
            return _FakeResult(rows=loop_rows or [])
        if "SELECT id, name, parent_id FROM plant_node" in sql:
            return _FakeResult(
                rows=plant_rows
                if plant_rows is not None
                else [SimpleNamespace(id="u1", name="氧化装置", parent_id=None)]
            )
        raise AssertionError(f"未预期的 SQL: {sql[:120]}")

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=_execute)
    return db


def _loop_row(
    loop_id: str = "l1",
    tag: str = "TIC-101",
    include: bool = True,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=loop_id,
        tag_name=tag,
        description="氧化反应器温度",
        unit_id="u1",
        include_in_evaluation=include,
    )


class TestBuildDataQualityStats:
    async def test_summary_and_reason_attribution(self) -> None:
        """KPI 卡口径 + 未参评原因三级归因（未纳入参评 > L0 > INCONCLUSIVE）。"""
        db = _make_dq_db(
            loop_rows=[
                _loop_row("l1", "TIC-101", include=True),
                _loop_row("l2", "TIC-102", include=False),
                _loop_row("l3", "TIC-103", include=True),
            ],
            kpi_rows=[
                SimpleNamespace(
                    loop_id="l1",
                    avg_good_value=96.5,
                    snap_total=100,
                    inconclusive_total=8,
                ),
                SimpleNamespace(
                    loop_id="l2",
                    avg_good_value=0.88,  # 0~1 比率量纲兼容
                    snap_total=100,
                    inconclusive_total=0,
                ),
            ],
            trend_rows=[
                SimpleNamespace(
                    d="2026-08-01",
                    health_rate=96.5,
                    inconclusive_rate=8.0,
                )
            ],
            integrity_rows=[
                SimpleNamespace(
                    loop_id="l1",
                    pv_completeness=0.97,
                    overall_completeness=0.95,
                    integrity_status="OK",
                    check_date=datetime(2026, 8, 1),
                )
            ],
            fitness_rows=[
                SimpleNamespace(loop_id="l2", fitness_level="L0"),
                SimpleNamespace(loop_id="l3", fitness_level="L3"),
            ],
            confidence_rows=[
                SimpleNamespace(
                    loop_id="l1",
                    confidence_level="A",
                    eval_status="SUCCESS",
                    eval_time=datetime(2026, 8, 1, 3),
                ),
                SimpleNamespace(
                    loop_id="l3",
                    confidence_level=None,
                    eval_status="INCONCLUSIVE",
                    eval_time=datetime(2026, 8, 1, 3),
                ),
            ],
        )

        data = await dqs.build_data_quality_stats(db)

        s = data["summary"]
        assert s["totalLoops"] == 3
        assert s["evaluableLoops"] == 2
        assert s["evaluateRate"] == round(2 / 3 * 100, 1)
        # 数据健康率：per-loop 均值 [96.5, 88.0] → 92.25 → round 半偶 92.2（量纲兼容生效）
        assert s["dataHealthRate"] == 92.2
        # INCONCLUSIVE 率：8/200 → 4.0%
        assert s["inconclusiveRate"] == 4.0
        conf = {r["level"]: r["count"] for r in s["confidenceDistribution"]}
        assert conf["A"] == 1
        assert conf["UNKNOWN"] == 2

        assert data["trend"][0]["healthRate"] == 96.5
        assert data["trend"][0]["inconclusiveRate"] == 8.0

        items = {i["loopTagName"]: i for i in data["items"]}
        # l1：参评 + 非 L0 + SUCCESS → 无未参评原因
        assert items["TIC-101"]["nonEvalReason"] is None
        assert items["TIC-101"]["pvCompleteness"] == 97.0
        assert items["TIC-101"]["goodValueRate"] == 96.5
        assert items["TIC-101"]["confidenceLevel"] == "A"
        assert items["TIC-101"]["unitPath"] == "氧化装置"
        # l2：include_in_evaluation=false 优先于 L0（fitness=L0）
        assert items["TIC-102"]["nonEvalReason"] == "未纳入参评"
        assert items["TIC-102"]["fitnessLevel"] == "L0"
        assert items["TIC-102"]["goodValueRate"] == 88.0
        # l3：参评但评估 INCONCLUSIVE → 原因归因
        assert items["TIC-103"]["nonEvalReason"] == "评估 INCONCLUSIVE"

    async def test_l0_reason_when_evaluated(self) -> None:
        """已参评 + L0 → 归因"L0 数据不足"（优先级高于 INCONCLUSIVE）。"""
        assert dqs.non_eval_reason(True, "L0", "INCONCLUSIVE") == "L0 数据不足"
        assert dqs.non_eval_reason(True, None, "INCONCLUSIVE") == "评估 INCONCLUSIVE"
        assert dqs.non_eval_reason(False, None, None) == "未纳入参评"
        assert dqs.non_eval_reason(True, "L3", "SUCCESS") is None

    async def test_empty_data_archived_state(self) -> None:
        """空数据（基础模块未产出）：指标 null、trend/明细空、不误导为 0 率。"""
        db = _make_dq_db(loop_rows=[], kpi_rows=[], trend_rows=[])

        data = await dqs.build_data_quality_stats(db)

        s = data["summary"]
        assert s["totalLoops"] == 0
        assert s["evaluateRate"] is None
        assert s["dataHealthRate"] is None
        assert s["inconclusiveRate"] is None
        assert data["trend"] == []
        assert data["items"] == []

    async def test_plant_subtree_filter_triggered(self) -> None:
        """装置下钻：WITH RECURSIVE 子树解析 + 装置过滤下推到回路基数与 KPI 聚合。"""
        captured: list[str] = []
        db = _make_dq_db(
            loop_rows=[],
            subtree_rows=[SimpleNamespace(id="u1")],
            captured=captured,
        )

        await dqs.build_data_quality_stats(db, start=START, end=END, plant_node_id="pn-1")

        assert any("WITH RECURSIVE node_tree" in sql for sql in captured)
        # 回路基数与 KPI/趋势聚合均带 :unit_ids 过滤
        assert any("ORDER BY ll.tag_name" in sql and ":unit_ids" in sql for sql in captured)
        assert any("GROUP BY k.loop_id" in sql and ":unit_ids" in sql for sql in captured)


class TestReportDataQualityEndpoint:
    def test_params_passthrough(self, client, mock_db, monkeypatch) -> None:
        """startDate/endDate/plantNodeId 透传（end 半开区间 +1 天）。"""
        captured: dict[str, Any] = {}

        async def _fake_dq(db, *, start=None, end=None, plant_node_id=None):
            captured.update(start=start, end=end, plant_node_id=plant_node_id)
            return {"summary": {}, "trend": [], "items": []}

        monkeypatch.setattr(rp, "build_data_quality_stats", _fake_dq)
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/reports/data-quality",
                params={
                    "startDate": "2026-07-01",
                    "endDate": "2026-08-01",
                    "plantNodeId": "pn-1",
                },
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 200
        assert resp.json()["code"] == "0"
        assert captured["start"] == datetime(2026, 7, 1)
        assert captured["end"] == datetime(2026, 8, 2)
        assert captured["plant_node_id"] == "pn-1"

    def test_no_module_gate_in_source(self) -> None:
        """数据质量端点/服务源码不引用 is_module_enabled（纯基础模块数据）。"""
        assert "is_module_enabled" not in inspect.getsource(rp.get_report_data_quality)
        assert "is_module_enabled" not in inspect.getsource(dqs)


# ---------------------------------------------------------------------------
# 预警统计聚合（P1-3，方案 §4.2）
# ---------------------------------------------------------------------------


def _make_alert_db(
    *,
    summary_row: Any = None,
    suppression_count: int = 0,
    trend_rows: list[Any] | None = None,
    status_dist: list[Any] | None = None,
    severity_dist: list[Any] | None = None,
    top_rule_rows: list[Any] | None = None,
    top_loop_rows: list[Any] | None = None,
    subtree_rows: list[Any] | None = None,
    captured: list[str] | None = None,
) -> AsyncMock:
    """预警统计聚合打桩：按 SQL 文本特征分发预制结果。"""

    async def _execute(stmt: Any, _params: Any = None) -> _FakeResult:
        sql = str(stmt)
        if captured is not None:
            captured.append(sql)
        if "WITH RECURSIVE node_tree" in sql:
            return _FakeResult(rows=subtree_rows or [])
        if "mtta_hours" in sql:
            return _FakeResult(
                one_row=summary_row
                or SimpleNamespace(
                    total=10,
                    active=3,
                    mtta_hours=1.5,
                    mttr_hours=8.25,
                    marked_total=4,
                    fp_total=1,
                )
            )
        if "FROM alert_suppression" in sql:
            return _FakeResult(scalar_one=suppression_count)
        if "GROUP BY 1, 2" in sql:
            return _FakeResult(rows=trend_rows or [])
        if "ae.severity AS k" in sql:
            return _FakeResult(rows=severity_dist if severity_dist is not None else [])
        if "GROUP BY 1 ORDER BY 2 DESC" in sql:
            return _FakeResult(rows=status_dist if status_dist is not None else [])
        if "GROUP BY ae.rule_code" in sql:
            return _FakeResult(rows=top_rule_rows or [])
        if "GROUP BY ae.loop_id" in sql:
            return _FakeResult(rows=top_loop_rows or [])
        raise AssertionError(f"未预期的 SQL: {sql[:120]}")

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=_execute)
    return db


class TestBuildAlertStatistics:
    async def test_summary_trend_and_top_lists(self) -> None:
        """KPI 口径 + 按天 severity 堆叠透视 + TOP10 规则/回路。"""
        db = _make_alert_db(
            trend_rows=[
                SimpleNamespace(d="2026-08-01", severity="WARN", cnt=3),
                SimpleNamespace(d="2026-08-01", severity="ERROR", cnt=1),
                SimpleNamespace(d="2026-08-02", severity="WARN", cnt=2),
            ],
            status_dist=[
                SimpleNamespace(k="RESOLVED", cnt=6),
                SimpleNamespace(k="ACTIVE", cnt=3),
            ],
            severity_dist=[SimpleNamespace(k="WARN", cnt=5)],
            top_rule_rows=[
                SimpleNamespace(rule_code="STUCK", rule_name="卡滞预警", cnt=6, fp_cnt=1)
            ],
            top_loop_rows=[SimpleNamespace(loop_id="l1", loop_tag_name="TIC-101", cnt=4, fp_cnt=0)],
        )

        data = await als.build_alert_statistics(db)

        s = data["summary"]
        assert s["total"] == 10
        assert s["active"] == 3
        assert s["mttaHours"] == 1.5
        # round 半偶：8.25 → 8.2
        assert s["mttrHours"] == 8.2
        # 误报率：1/4 已标记 → 25.0%
        assert s["falsePositiveRate"] == 25.0
        assert s["activeSuppressions"] == 0

        # 堆叠透视：同日多 severity 合并一行，缺省 severity 补 0
        assert data["trend"] == [
            {"date": "2026-08-01", "INFO": 0, "WARN": 3, "ERROR": 1, "CRITICAL": 0},
            {"date": "2026-08-02", "INFO": 0, "WARN": 2, "ERROR": 0, "CRITICAL": 0},
        ]
        assert data["statusDistribution"][0]["key"] == "RESOLVED"
        assert data["severityDistribution"] == [{"key": "WARN", "count": 5}]
        assert data["topRules"][0] == {
            "ruleCode": "STUCK",
            "ruleName": "卡滞预警",
            "count": 6,
            "falsePositives": 1,
        }
        assert data["topLoops"][0]["loopTagName"] == "TIC-101"

    async def test_empty_data_and_suppression_count(self) -> None:
        """空数据（预警未接入）：指标 null、分布/TOP 空；抑制条数独立统计。"""
        db = _make_alert_db(
            summary_row=SimpleNamespace(
                total=0,
                active=0,
                mtta_hours=None,
                mttr_hours=None,
                marked_total=0,
                fp_total=0,
            ),
            suppression_count=2,
        )

        data = await als.build_alert_statistics(db)

        s = data["summary"]
        assert s["total"] == 0
        assert s["mttaHours"] is None
        assert s["mttrHours"] is None
        assert s["falsePositiveRate"] is None
        assert s["activeSuppressions"] == 2
        assert data["trend"] == []
        assert data["topRules"] == []
        assert data["topLoops"] == []

    async def test_filters_and_plant_subtree(self) -> None:
        """severity/status 过滤 + 装置下钻下推到全部聚合 SQL。"""
        captured: list[str] = []
        db = _make_alert_db(
            subtree_rows=[SimpleNamespace(id="u1")],
            captured=captured,
        )

        await als.build_alert_statistics(
            db,
            start=START,
            end=END,
            plant_node_id="pn-1",
            severity="WARN",
            status="ACTIVE",
        )

        assert any("WITH RECURSIVE node_tree" in sql for sql in captured)
        # severity/status 过滤下推（KPI 汇总与 TOP 榜共用 WHERE 片段）
        assert any("mtta_hours" in sql and "ae.severity = :severity" in sql for sql in captured)
        assert any(
            "GROUP BY ae.loop_id" in sql and "ae.status = :status" in sql for sql in captured
        )


class TestReportAlertStatisticsEndpoint:
    def test_params_passthrough(self, client, mock_db, monkeypatch) -> None:
        """startDate/endDate/plantNodeId/severity/status 全量透传。"""
        captured: dict[str, Any] = {}

        async def _fake_alert(db, **kwargs):
            captured.update(kwargs)
            return {"summary": {}}

        monkeypatch.setattr(rp, "build_alert_statistics", _fake_alert)
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/reports/alert-statistics",
                params={
                    "startDate": "2026-07-01",
                    "endDate": "2026-08-01",
                    "plantNodeId": "pn-1",
                    "severity": "WARN",
                    "status": "ACTIVE",
                },
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 200
        assert resp.json()["code"] == "0"
        assert captured["start"] == datetime(2026, 7, 1)
        assert captured["end"] == datetime(2026, 8, 2)
        assert captured["plant_node_id"] == "pn-1"
        assert captured["severity"] == "WARN"
        assert captured["status"] == "ACTIVE"

    def test_no_module_gate_in_source(self) -> None:
        """预警统计端点/服务源码不引用 is_module_enabled（纯基础模块数据）。"""
        assert "is_module_enabled" not in inspect.getsource(rp.get_report_alert_statistics)
        assert "is_module_enabled" not in inspect.getsource(als)


# ---------------------------------------------------------------------------
# 收益报告 P2-3 整定执行区块 + P2-4 逐工单明细（方案 §5.2/§5.3）
# ---------------------------------------------------------------------------


def _make_benefit_db(
    *,
    tuning_count: int = 3,
    cmp_row: Any = None,
    curve_rows: list[Any] | None = None,
    bench_rows: list[Any] | None = None,
    delta_rows: list[Any] | None = None,
    unit_path_rows: list[Any] | None = None,
    exec_rows: list[Any] | None = None,
    fit_rows: list[Any] | None = None,
    batch_row: Any = None,
    tag_rows: list[Any] | None = None,
    subtree_rows: list[Any] | None = None,
    captured: list[tuple[str, Any]] | None = None,
) -> AsyncMock:
    """get_benefit 打桩：按 SQL 文本特征分发预制结果。"""

    async def _execute(stmt: Any, _params: Any = None) -> _FakeResult:
        sql = str(stmt)
        if captured is not None:
            captured.append((sql, _params))
        if "WITH RECURSIVE node_tree" in sql:
            return _FakeResult(rows=subtree_rows or [])
        if "WHERE tr.status = 'COMPLETED'" in sql:
            return _FakeResult(scalar_one=tuning_count)
        if "before_score" in sql:
            return _FakeResult(
                one_row=cmp_row
                or SimpleNamespace(
                    before_score=70.0,
                    after_score=82.0,
                    before_auto=75.0,
                    after_auto=88.0,
                    before_good=95.0,
                    after_good=97.0,
                    before_osc=15.0,
                    after_osc=8.0,
                    closed_cnt=4,
                )
            )
        if "date_trunc('month', k.ts_start)" in sql:
            return _FakeResult(rows=curve_rows or [])
        if "COUNT(DISTINCT ll.id)" in sql:
            return _FakeResult(rows=bench_rows or [])
        if "avg_delta" in sql:
            return _FakeResult(rows=delta_rows or [])
        # --- P2-3 整定执行区块 ---
        if "GROUP BY tr.algorithm, tr.status" in sql:
            return _FakeResult(
                rows=exec_rows
                if exec_rows is not None
                else [
                    SimpleNamespace(algorithm="IMC", status="COMPLETED", cnt=4, avg_fit=82.5),
                    SimpleNamespace(algorithm="IMC", status="ROLLED_BACK", cnt=1, avg_fit=None),
                    SimpleNamespace(algorithm="LAMBDA", status="COMPLETED", cnt=2, avg_fit=90.0),
                ]
            )
        if "WHEN tr.fitting_score < 60" in sql:
            return _FakeResult(
                rows=fit_rows
                if fit_rows is not None
                else [
                    SimpleNamespace(bucket="b60_75", cnt=2),
                    SimpleNamespace(bucket="ge90", cnt=1),
                ]
            )
        if "FROM tuning_batch" in sql:
            return _FakeResult(one_row=batch_row)
        if "SELECT id, tag_name FROM loop_ledger" in sql:
            return _FakeResult(rows=tag_rows or [])
        if "SELECT n.id AS id, n.name AS name, p.name AS parent_name" in sql:
            return _FakeResult(rows=unit_path_rows or [])
        raise AssertionError(f"未预期的 SQL: {sql[:120]}")

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=_execute)
    return db


class TestGetBenefitP2TuningExecution:
    async def test_tuning_execution_aggregation(self) -> None:
        """算法/状态分布 + 回滚率 + 加权平均拟合度 + 拟合度四桶口径。"""
        db = _make_benefit_db()

        data = await rs.get_benefit(db, start_date=START, end_date=END, plant_node_id=None)

        te = data["tuningExecution"]
        assert te["totalRecords"] == 7
        assert te["byAlgorithm"] == [
            {"algorithm": "IMC", "count": 5},
            {"algorithm": "LAMBDA", "count": 2},
        ]
        assert te["byStatus"] == [
            {"status": "COMPLETED", "count": 6},
            {"status": "ROLLED_BACK", "count": 1},
        ]
        assert te["rollbackRate"] == round(1 / 7, 4)
        # 加权平均拟合度：(82.5*4 + 90.0*2) / 6 = 85.0
        assert te["avgFittingScore"] == 85.0

        assert data["fittingDistribution"] == [
            {"bucket": "lt60", "label": "<60", "count": 0},
            {"bucket": "b60_75", "label": "60~75", "count": 2},
            {"bucket": "b75_90", "label": "75~90", "count": 0},
            {"bucket": "ge90", "label": "≥90", "count": 1},
        ]
        # 无批次数据时 latestBatchScatter 为 None
        assert data["latestBatchScatter"] is None

    async def test_empty_window_records(self) -> None:
        """窗口内零整定记录：回滚率/平均拟合度为 null，四桶补零，不误导为 0 率。"""
        db = _make_benefit_db(exec_rows=[], fit_rows=[])

        data = await rs.get_benefit(db, start_date=START, end_date=END, plant_node_id=None)

        te = data["tuningExecution"]
        assert te["totalRecords"] == 0
        assert te["rollbackRate"] is None
        assert te["avgFittingScore"] is None
        assert all(b["count"] == 0 for b in data["fittingDistribution"])

    async def test_latest_batch_scatter_normalization(self) -> None:
        """最近批次散点：snake_case JSONB 归一化为 loopId/score + 位号补全。"""
        batch_row = SimpleNamespace(
            batch_no="TB-20260820-001",
            title="常减压批次整定",
            completed_at=datetime(2026, 8, 20, 10, 0),
            scatters_before=[{"loop_id": "l1", "score": 62.34}, {"loop_id": "l2"}],
            scatters_after=[{"loop_id": "l1", "score": 85.12}],
        )
        db = _make_benefit_db(
            batch_row=batch_row,
            tag_rows=[SimpleNamespace(id="l1", tag_name="TIC-101")],
        )

        data = await rs.get_benefit(db, start_date=None, end_date=None, plant_node_id=None)

        bs = data["latestBatchScatter"]
        assert bs["batchNo"] == "TB-20260820-001"
        assert bs["completedAt"] == "2026-08-20T10:00:00"
        # score 缺失的点保留（score=None 由前端配对时跳过），位号补全
        assert bs["before"] == [
            {"loopId": "l1", "score": 62.3, "loopTagName": "TIC-101"},
            {"loopId": "l2", "score": None, "loopTagName": None},
        ]
        assert bs["after"] == [
            {"loopId": "l1", "score": 85.1, "loopTagName": "TIC-101"},
        ]

    async def test_plant_filter_propagates_to_tuning_queries(self) -> None:
        """装置下钻：整定执行/拟合度聚合带 :unit_ids 过滤（批次散点全局口径除外）。"""
        captured: list[tuple[str, Any]] = []
        db = _make_benefit_db(
            subtree_rows=[SimpleNamespace(id="u1")],
            captured=captured,
        )

        await rs.get_benefit(db, start_date=START, end_date=END, plant_node_id="pn-1")

        exec_sql = next(s for s, _ in captured if "GROUP BY tr.algorithm, tr.status" in s)
        assert ":unit_ids" in exec_sql and ":start" in exec_sql
        fit_sql = next(s for s, _ in captured if "WHEN tr.fitting_score < 60" in s)
        assert ":unit_ids" in fit_sql


def _make_benefit_orders_db(
    *,
    total: int = 1,
    rows: list[Any] | None = None,
    subtree_rows: list[Any] | None = None,
    captured: list[tuple[str, Any]] | None = None,
) -> AsyncMock:
    """get_benefit_orders 打桩。"""

    async def _execute(stmt: Any, _params: Any = None) -> _FakeResult:
        sql = str(stmt)
        if captured is not None:
            captured.append((sql, _params))
        if "WITH RECURSIVE node_tree" in sql:
            return _FakeResult(rows=subtree_rows or [])
        if "SELECT COUNT(*)" in sql:
            return _FakeResult(scalar_one=total)
        if "ORDER BY ho.verified_at DESC" in sql:
            return _FakeResult(
                rows=rows
                if rows is not None
                else [
                    SimpleNamespace(
                        order_no="HD-20260820-001",
                        loop_id="l1",
                        loop_tag_name="TIC-101",
                        action_type="TUNING",
                        handler="张三",
                        kpi_before={"score": 62.3, "oscillationRate": 15.0},
                        kpi_after={"score": 85.1, "oscillationRate": 8.0},
                        verify_result="EFFECTIVE",
                        verified_at=datetime(2026, 8, 20, 12, 0),
                    )
                ]
            )
        raise AssertionError(f"未预期的 SQL: {sql[:120]}")

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=_execute)
    return db


class TestGetBenefitOrders:
    async def test_items_mapping_and_pagination(self) -> None:
        """行结构（orderNo/回路/类型标签/kpi 前后快照/verify_result）+ 分页参数。"""
        captured: list[tuple[str, Any]] = []
        db = _make_benefit_orders_db(total=23, captured=captured)

        data = await rs.get_benefit_orders(db, START, END, None, page=2, page_size=10)

        assert data["total"] == 23
        assert data["page"] == 2
        assert data["pageSize"] == 10
        item = data["items"][0]
        assert item["orderNo"] == "HD-20260820-001"
        assert item["loopTagName"] == "TIC-101"
        assert item["actionTypeLabel"] == "参数整定"
        assert item["kpiBefore"] == {"score": 62.3, "oscillationRate": 15.0}
        assert item["verifyResult"] == "EFFECTIVE"
        assert item["verifiedAt"] == "2026-08-20T12:00:00"
        # 分页参数（OFFSET 半开）与 verified_at 归窗
        list_params = next(p for s, p in captured if "ORDER BY ho.verified_at DESC" in s)
        assert list_params["limit"] == 10
        assert list_params["offset"] == 10
        assert list_params["start"] == START
        assert list_params["end"] == END

    async def test_plant_filter_propagates(self) -> None:
        """装置下钻：子树解析 + :unit_ids 过滤下推计数与明细查询。"""
        captured: list[tuple[str, Any]] = []
        db = _make_benefit_orders_db(
            subtree_rows=[SimpleNamespace(id="u1")],
            captured=captured,
        )

        await rs.get_benefit_orders(db, None, None, "pn-1", page=1, page_size=10)

        assert any("WITH RECURSIVE node_tree" in s for s, _ in captured)
        assert all(":unit_ids" in s for s, _ in captured if "FROM handling_order ho" in s)


class TestReportBenefitOrdersEndpoint:
    def test_params_passthrough(self, client, mock_db, monkeypatch) -> None:
        """page/pageSize/startDate/endDate/plantNodeId 全量透传到 service。"""
        captured: dict[str, Any] = {}

        async def _fake_orders(db, start_date, end_date, plant_node_id, *, page, page_size):
            captured.update(
                start=start_date,
                end=end_date,
                plant_node_id=plant_node_id,
                page=page,
                page_size=page_size,
            )
            return {"items": [], "total": 0, "page": page, "pageSize": page_size}

        monkeypatch.setattr(rp, "get_benefit_orders", _fake_orders)
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/reports/benefit/orders",
                params={
                    "page": 3,
                    "pageSize": 20,
                    "startDate": "2026-07-01",
                    "endDate": "2026-08-01",
                    "plantNodeId": "pn-1",
                },
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 200
        assert resp.json()["code"] == "0"
        assert captured["page"] == 3
        assert captured["page_size"] == 20
        assert captured["start"] == datetime(2026, 7, 1)
        assert captured["end"] == datetime(2026, 8, 2)
        assert captured["plant_node_id"] == "pn-1"

    def test_default_pagination(self, client, mock_db, monkeypatch) -> None:
        """默认 page=1/pageSize=10；不传时间窗为 None（全量归档口径）。"""
        captured: dict[str, Any] = {}

        async def _fake_orders(db, start_date, end_date, plant_node_id, *, page, page_size):
            captured.update(start=start_date, page=page, page_size=page_size)
            return {"items": [], "total": 0, "page": page, "pageSize": page_size}

        monkeypatch.setattr(rp, "get_benefit_orders", _fake_orders)
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/reports/benefit/orders",
                headers={"Authorization": "Bearer fake-token"},
            )

        assert resp.status_code == 200
        assert captured == {"start": None, "page": 1, "page_size": 10}


# ---------------------------------------------------------------------------
# 结构性断言：可插拔门禁不穿透报告域（模块禁用组合下报告完整可用）
# ---------------------------------------------------------------------------


class TestModuleIndependenceStructure:
    def test_self_sustained_endpoints_do_not_check_module_gate(self) -> None:
        """R1 自持端点源码不得引用 is_module_enabled（处置/诊断/收益直读表）。"""
        for func in (
            rp.get_report_handling_statistics,
            rp.get_report_diagnosis_runs,
            rp.export_report_diagnosis_runs,
            rp.get_report_benefit,
            rp.get_report_benefit_orders,
        ):
            src = inspect.getsource(func)
            assert "is_module_enabled" not in src, f"{func.__name__} 穿透了模块门禁"

    def test_benefit_stats_service_does_not_check_module_gate(self) -> None:
        """收益聚合 service 同样不感知模块启停（P2-3/P2-4 含整定执行/逐单明细）。"""
        src = inspect.getsource(rs)
        assert "is_module_enabled" not in src

    def test_handling_stats_service_does_not_check_module_gate(self) -> None:
        """聚合 service 同样不感知模块启停（单一实现双端点共用）。"""
        src = inspect.getsource(hs)
        assert "is_module_enabled" not in src

    def test_statistics_window_semantics_doc(self) -> None:
        """闭环数归期语义已文档化：默认本月、传窗按 verified_at 归窗。"""
        doc = inspect.getdoc(hs.build_handling_statistics) or ""
        assert "verified_at" in doc
        assert "本月" in doc
