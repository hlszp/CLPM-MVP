"""Diagnosis recommendation & report service tests (SVC-11 / SVC-12 / SVC-13).

Covers:
- get_recommendations: 8 类标签标准化建议
- generate_diagnosis_report: PDF 生成返回 bytes
- export_diagnosis_statistics: CSV 导出
- API 端点：GET /diagnosis/{loopId}/recommendations, POST /diagnosis/{loopId}/report,
  GET /diagnosis/statistics/export
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from tests.conftest import TEST_USERS, mock_current_user

# ---------------------------------------------------------------------------
# SVC-11: 诊断解决方案推荐 - 单元测试
# ---------------------------------------------------------------------------


class TestGetRecommendations:
    """get_recommendations 函数单元测试。"""

    def test_get_recommendations_oscillation(self) -> None:
        """振荡标签返回 3 条建议。"""
        from app.services.diagnosis_recommendation import get_recommendations

        result = get_recommendations("loop-001", ["OSCILLATION"])
        assert result["loopId"] == "loop-001"
        assert result["totalCount"] == 3
        recs = result["recommendations"]
        assert len(recs) == 3
        # 每条建议包含必要字段
        for rec in recs:
            assert "label" in rec
            assert "labelName" in rec
            assert "priority" in rec
            assert "action" in rec
            assert "description" in rec
            assert "targetModule" in rec
            assert rec["label"] == "OSCILLATION"
            assert rec["labelName"] == "振荡"
            assert 1 <= rec["priority"] <= 3
        # 按 priority 升序排序
        priorities = [r["priority"] for r in recs]
        assert priorities == sorted(priorities)
        # 第一条建议应为"重新整定PID"
        assert recs[0]["action"] == "重新整定PID"
        assert recs[0]["priority"] == 1

    def test_get_recommendations_stiction(self) -> None:
        """黏滞标签返回 3 条建议。"""
        from app.services.diagnosis_recommendation import get_recommendations

        result = get_recommendations("loop-002", ["STICTION"])
        assert result["totalCount"] == 3
        recs = result["recommendations"]
        assert len(recs) == 3
        assert recs[0]["action"] == "清洁/更换阀门填料"
        assert recs[0]["priority"] == 1
        for rec in recs:
            assert rec["label"] == "STICTION"
            assert rec["labelName"] == "黏滞"

    def test_get_recommendations_multiple_tags(self) -> None:
        """多标签返回合并建议。"""
        from app.services.diagnosis_recommendation import get_recommendations

        result = get_recommendations("loop-003", ["OSCILLATION", "STICTION", "TUNING"])
        # 3 个标签 × 3 条建议 = 9 条
        assert result["totalCount"] == 9
        recs = result["recommendations"]
        assert len(recs) == 9
        # 应包含 3 种标签
        labels = {r["label"] for r in recs}
        assert labels == {"OSCILLATION", "STICTION", "TUNING"}
        # 按 priority 升序排序
        priorities = [r["priority"] for r in recs]
        assert priorities == sorted(priorities)

    def test_get_recommendations_alias_valve_stiction(self) -> None:
        """现有诊断引擎标签 VALVE_STICTION 应映射到 STICTION。"""
        from app.services.diagnosis_recommendation import get_recommendations

        result = get_recommendations("loop-004", ["VALVE_STICTION"])
        assert result["totalCount"] == 3
        recs = result["recommendations"]
        # 标签归一化为 STICTION
        assert all(r["label"] == "STICTION" for r in recs)
        assert all(r["labelName"] == "黏滞" for r in recs)

    def test_get_recommendations_alias_output_saturation(self) -> None:
        """OUTPUT_SATURATION 应映射到 SATURATION。"""
        from app.services.diagnosis_recommendation import get_recommendations

        result = get_recommendations("loop-005", ["OUTPUT_SATURATION"])
        assert result["totalCount"] == 3
        assert all(r["label"] == "SATURATION" for r in result["recommendations"])

    def test_get_recommendations_unknown_label_skipped(self) -> None:
        """未知标签应被跳过，不报错。"""
        from app.services.diagnosis_recommendation import get_recommendations

        result = get_recommendations("loop-006", ["UNKNOWN_LABEL", "OSCILLATION"])
        # 只有 OSCILLATION 的 3 条建议
        assert result["totalCount"] == 3
        assert all(r["label"] == "OSCILLATION" for r in result["recommendations"])

    def test_get_recommendations_empty_tags(self) -> None:
        """空标签列表返回空推荐。"""
        from app.services.diagnosis_recommendation import get_recommendations

        result = get_recommendations("loop-007", [])
        assert result["totalCount"] == 0
        assert result["recommendations"] == []

    def test_get_recommendations_all_eight_labels(self) -> None:
        """8 类标签全部返回建议。"""
        from app.services.diagnosis_recommendation import get_recommendations

        all_labels = [
            "OSCILLATION",
            "STICTION",
            "SATURATION",
            "SLUGGISH",
            "DEVIATION",
            "NOISE",
            "DEAD_BAND",
            "TUNING",
        ]
        result = get_recommendations("loop-008", all_labels)
        # 8 × 3 = 24
        assert result["totalCount"] == 24
        labels = {r["label"] for r in result["recommendations"]}
        assert labels == set(all_labels)

    def test_get_recommendations_dedup(self) -> None:
        """重复标签应去重。"""
        from app.services.diagnosis_recommendation import get_recommendations

        result = get_recommendations("loop-009", ["OSCILLATION", "OSCILLATION", "OSCILLATION"])
        # 去重后只有 3 条
        assert result["totalCount"] == 3


# ---------------------------------------------------------------------------
# SVC-11: get_recommendations_for_loop 服务层测试
# ---------------------------------------------------------------------------


class TestGetRecommendationsForLoop:
    """get_recommendations_for_loop 服务层单元测试。"""

    async def test_for_loop_success(self) -> None:
        """从数据库读取诊断标签并返回推荐。"""
        from app.services.diagnosis_recommendation import (
            get_recommendations_for_loop,
        )

        loop = MagicMock()
        loop.id = "loop-100"
        loop.tag_name = "101-FC-1023"

        # 第一次查询 loop，第二次查询 diag_label distinct
        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                m = MagicMock()
                m.scalar_one_or_none.return_value = loop
                return m
            # distinct labels
            m = MagicMock()
            m.all.return_value = [("OSCILLATION",), ("VALVE_STICTION",)]
            return m

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=execute_side_effect)

        result = await get_recommendations_for_loop(db, "loop-100")
        assert result["loopId"] == "loop-100"
        # OSCILLATION(3) + STICTION(3) = 6
        assert result["totalCount"] == 6

    async def test_for_loop_not_found(self) -> None:
        """回路不存在返回 ERR_LOOP_NOT_FOUND。"""
        from app.core.exceptions import BizError
        from app.services.diagnosis_recommendation import (
            get_recommendations_for_loop,
        )

        db = AsyncMock()
        m = MagicMock()
        m.scalar_one_or_none.return_value = None
        db.execute = AsyncMock(return_value=m)

        with pytest.raises(BizError) as exc_info:
            await get_recommendations_for_loop(db, "nonexistent")
        assert exc_info.value.code == "ERR_LOOP_NOT_FOUND"


# ---------------------------------------------------------------------------
# SVC-12: 诊断建议书 PDF 生成 - 单元测试
# ---------------------------------------------------------------------------


class TestGenerateDiagnosisReport:
    """generate_diagnosis_report 函数单元测试。"""

    def test_generate_diagnosis_report_returns_bytes(self) -> None:
        """PDF 生成返回 bytes。"""
        from app.services.diagnosis_recommendation import get_recommendations
        from app.services.diagnosis_report import generate_diagnosis_report

        loop_id = "loop-pdf-001"
        snapshot_data = {
            "tagName": "101-FC-1023",
            "unitName": "常减压装置-单元A",
            "compositeScore": 45.20,
            "diagnosisLabels": [
                {
                    "label": "OSCILLATION",
                    "labelName": "振荡",
                    "confidence": 0.85,
                    "evidence": {"fused_confidence": 0.82},
                    "algorithm": "DIAG_ENGINE_v1.0",
                }
            ],
            "featureValues": {
                "oscillation_index": 0.78,
                "frequency": 0.5,
            },
            "evidenceChain": {
                "reasoning": "PV-OP 散点图呈椭圆轨迹，FFT 检测到 0.5Hz 主频",
            },
            "diagnosedAt": "2026-06-22T08:00:00Z",
            "algorithmVersion": "DIAG_ENGINE_v1.0",
        }
        recommendations = get_recommendations(loop_id, ["OSCILLATION"])

        pdf_bytes = generate_diagnosis_report(
            loop_id=loop_id,
            snapshot_data=snapshot_data,
            recommendations=recommendations,
        )

        # 应返回 bytes
        assert isinstance(pdf_bytes, bytes)
        # PDF 文件应以 %PDF 开头
        assert pdf_bytes.startswith(b"%PDF")
        # 应有内容（至少 1KB）
        assert len(pdf_bytes) > 1000

    def test_generate_diagnosis_report_minimal_data(self) -> None:
        """最小数据也能生成 PDF。"""
        from app.services.diagnosis_report import generate_diagnosis_report

        pdf_bytes = generate_diagnosis_report(
            loop_id="loop-min",
            snapshot_data={},
            recommendations={"recommendations": [], "totalCount": 0},
        )
        assert isinstance(pdf_bytes, bytes)
        assert pdf_bytes.startswith(b"%PDF")

    def test_generate_diagnosis_report_with_multiple_labels(self) -> None:
        """多标签场景生成 PDF。"""
        from app.services.diagnosis_recommendation import get_recommendations
        from app.services.diagnosis_report import generate_diagnosis_report

        snapshot_data = {
            "tagName": "101-FC-1024",
            "unitName": "催化裂化装置",
            "compositeScore": 30.50,
            "diagnosisLabels": [
                {
                    "label": "OSCILLATION",
                    "labelName": "振荡",
                    "confidence": 0.78,
                    "evidence": {},
                    "algorithm": "DIAG_ENGINE_v1.0",
                },
                {
                    "label": "VALVE_STICTION",
                    "labelName": "阀门粘滞",
                    "confidence": 0.65,
                    "evidence": {},
                    "algorithm": "DIAG_ENGINE_v1.0",
                },
            ],
            "featureValues": {"stiction_index": 0.78},
            "evidenceChain": {"reasoning": "多源证据融合：振荡 + 粘滞"},
            "diagnosedAt": "2026-06-22T08:00:00Z",
            "algorithmVersion": "DIAG_ENGINE_v1.0",
        }
        recommendations = get_recommendations("loop-multi", ["OSCILLATION", "VALVE_STICTION"])

        pdf_bytes = generate_diagnosis_report(
            loop_id="loop-multi",
            snapshot_data=snapshot_data,
            recommendations=recommendations,
        )
        assert pdf_bytes.startswith(b"%PDF")
        assert len(pdf_bytes) > 2000


# ---------------------------------------------------------------------------
# SVC-13: 诊断统计 CSV 导出 - 单元测试
# ---------------------------------------------------------------------------


class TestExportDiagnosisStatistics:
    """export_diagnosis_statistics 函数单元测试。"""

    async def test_export_csv_success(self) -> None:
        """CSV 导出成功返回 bytes。"""
        from app.services.diagnosis_report import export_diagnosis_statistics

        # mock 数据库查询：第一次返回标签聚合，第二次返回趋势
        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            m = MagicMock()
            if call_count[0] == 1:
                # 标签分布
                m.all.return_value = [("OSCILLATION", 5), ("VALVE_STICTION", 3)]
            elif call_count[0] == 2:
                # plant_node 查询（如果指定了 plant_node_id）
                m.scalar_one_or_none.return_value = None
            else:
                # 趋势
                m.all.return_value = []
            return m

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=execute_side_effect)

        csv_bytes = await export_diagnosis_statistics(
            db=db,
            start_date="2026-06-01T00:00:00Z",
            end_date="2026-06-30T00:00:00Z",
            plant_node_id=None,
        )

        assert isinstance(csv_bytes, bytes)
        # UTF-8 with BOM
        assert csv_bytes.startswith(b"\xef\xbb\xbf")
        # 解码后应包含标题
        csv_str = csv_bytes.decode("utf-8")
        assert "CLPM 诊断统计报表" in csv_str
        assert "标签分布汇总" in csv_str
        assert "OSCILLATION" in csv_str
        assert "VALVE_STICTION" in csv_str
        assert "按天趋势" in csv_str
        assert "分布统计" in csv_str

    async def test_export_csv_with_plant_node(self) -> None:
        """指定装置节点时 CSV 包含装置名。"""
        from app.services.diagnosis_report import export_diagnosis_statistics

        node = MagicMock()
        node.id = "node-001"
        node.name = "常减压装置"

        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            m = MagicMock()
            if call_count[0] == 1:
                # 标签分布（带 join）
                m.all.return_value = [("OSCILLATION", 2)]
            elif call_count[0] == 2:
                # plant_node 查询
                m.scalar_one_or_none.return_value = node
            else:
                # 趋势
                m.all.return_value = []
            return m

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=execute_side_effect)

        csv_bytes = await export_diagnosis_statistics(
            db=db,
            start_date="2026-06-01T00:00:00Z",
            end_date="2026-06-30T00:00:00Z",
            plant_node_id="node-001",
        )

        csv_str = csv_bytes.decode("utf-8")
        assert "常减压装置" in csv_str

    async def test_export_csv_empty_data(self) -> None:
        """无数据时 CSV 仍可生成。"""
        from app.services.diagnosis_report import export_diagnosis_statistics

        m = MagicMock()
        m.all.return_value = []
        db = AsyncMock()
        db.execute = AsyncMock(return_value=m)

        csv_bytes = await export_diagnosis_statistics(
            db=db,
            start_date="2026-06-01T00:00:00Z",
            end_date="2026-06-30T00:00:00Z",
        )
        assert isinstance(csv_bytes, bytes)
        csv_str = csv_bytes.decode("utf-8")
        assert "CLPM 诊断统计报表" in csv_str
        assert "合计" in csv_str


# ---------------------------------------------------------------------------
# API 端点测试
# ---------------------------------------------------------------------------


def _make_loop_mock(
    loop_id: str = "00000000-0000-0000-0000-000000000201",
    tag_name: str = "101-FC-1023",
) -> MagicMock:
    """构造 LoopLedger mock。"""
    loop = MagicMock()
    loop.id = loop_id
    loop.tag_name = tag_name
    loop.description = "测试回路"
    loop.unit_id = "00000000-0000-0000-0000-000000000111"
    loop.status = "READY"
    loop.is_active = True
    return loop


def _make_diag_result_mock(
    loop_id: str = "00000000-0000-0000-0000-000000000201",
    diag_label: str = "OSCILLATION",
) -> MagicMock:
    """构造 DiagnosisResult mock。"""
    r = MagicMock()
    r.id = str(uuid4())
    r.loop_id = loop_id
    r.diag_label = diag_label
    r.confidence = Decimal("85.00")
    r.feature_values = {"oscillation_index": 0.78}
    r.evidence_chain = {
        "fused_confidence": 0.82,
        "reasoning": "PV-OP 散点图呈椭圆轨迹",
    }
    r.algorithm_version = "DIAG_ENGINE_v1.0"
    r.diagnosed_at = datetime.now(UTC)
    return r


def _make_snapshot_mock(
    loop_id: str = "00000000-0000-0000-0000-000000000201",
) -> MagicMock:
    """构造 KpiSnapshotHourly mock。"""
    s = MagicMock()
    s.id = str(uuid4())
    s.loop_id = loop_id
    s.ts_start = datetime.now(UTC)
    s.ts_end = s.ts_start
    s.score = Decimal("45.20")
    s.status = "SUCCESS"
    return s


def _make_scalar_one_or_none_mock(value) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _make_scalars_mock(items: list) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    return result


class TestRecommendationsEndpoint:
    """GET /api/v1/diagnosis/{loopId}/recommendations tests."""

    def test_get_recommendations_with_tag_codes(self, client, fake_redis) -> None:
        """通过 query 参数 tagCodes 获取推荐。"""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/diagnosis/00000000-0000-0000-0000-000000000001/recommendations?tagCodes=OSCILLATION,STICTION",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        data = body["data"]
        assert data["loopId"] == "00000000-0000-0000-0000-000000000001"
        # OSCILLATION(3) + STICTION(3) = 6
        assert data["totalCount"] == 6
        assert len(data["recommendations"]) == 6

    def test_get_recommendations_no_token(self, client) -> None:
        """未认证请求返回 401。"""
        resp = client.get("/api/v1/diagnosis/00000000-0000-0000-0000-000000000001/recommendations")
        assert resp.status_code == 401

    def test_get_recommendations_from_db(self, client, mock_db, fake_redis) -> None:
        """不传 tagCodes 时从数据库读取。"""
        loop = _make_loop_mock()

        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_scalar_one_or_none_mock(loop)
            # distinct labels
            m = MagicMock()
            m.all.return_value = [("OSCILLATION",)]
            return m

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                f"/api/v1/diagnosis/{loop.id}/recommendations",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert body["data"]["totalCount"] == 3

    def test_get_recommendations_loop_not_found(self, client, mock_db, fake_redis) -> None:
        """回路不存在返回 404。"""
        mock_db.execute = AsyncMock(return_value=_make_scalar_one_or_none_mock(None))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/diagnosis/00000000-0000-0000-0000-000000000000/recommendations",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 404
        assert resp.json()["code"] == "ERR_LOOP_NOT_FOUND"


class TestReportEndpoint:
    """POST /api/v1/diagnosis/{loopId}/report tests."""

    def test_generate_report_success(self, client, mock_db, fake_redis) -> None:
        """生成 PDF 建议书成功。"""
        loop = _make_loop_mock()
        diag = _make_diag_result_mock()
        snapshot = _make_snapshot_mock()

        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                # get_diagnosis_detail: loop
                return _make_scalar_one_or_none_mock(loop)
            if call_count[0] == 2:
                # get_diagnosis_detail: diag results
                return _make_scalars_mock([diag])
            if call_count[0] == 3:
                # get_diagnosis_detail: snapshot
                return _make_scalar_one_or_none_mock(snapshot)
            if call_count[0] == 4:
                # get_recommendations_for_loop: loop
                return _make_scalar_one_or_none_mock(loop)
            # get_recommendations_for_loop: distinct labels
            m = MagicMock()
            m.all.return_value = [("OSCILLATION",)]
            return m

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)

        with mock_current_user(TEST_USERS["ic_engineer"]):
            resp = client.post(
                f"/api/v1/diagnosis/{loop.id}/report",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/pdf"
        assert resp.content.startswith(b"%PDF")
        # Content-Disposition 应包含文件名
        assert "attachment" in resp.headers.get("content-disposition", "")
        assert "diagnosis_report" in resp.headers.get("content-disposition", "")

    def test_generate_report_no_token(self, client) -> None:
        """未认证请求返回 401。"""
        resp = client.post("/api/v1/diagnosis/00000000-0000-0000-0000-000000000001/report")
        assert resp.status_code == 401

    def test_generate_report_sponsor_forbidden(self, client, mock_db, fake_redis) -> None:
        """SPONSOR 无权限生成报告（403）。"""
        with mock_current_user(TEST_USERS["sponsor"]):
            resp = client.post(
                "/api/v1/diagnosis/00000000-0000-0000-0000-000000000001/report",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 403


class TestStatisticsExportEndpoint:
    """GET /api/v1/diagnosis/statistics/export tests."""

    def test_export_csv_success(self, client, mock_db, fake_redis) -> None:
        """导出 CSV 成功。"""
        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            m = MagicMock()
            if call_count[0] == 1:
                m.all.return_value = [("OSCILLATION", 5), ("VALVE_STICTION", 3)]
            else:
                m.all.return_value = []
            return m

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)

        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/diagnosis/statistics/export",
                headers={"Authorization": "Bearer fake-token"},
                params={
                    "startDate": "2026-06-01T00:00:00Z",
                    "endDate": "2026-06-30T00:00:00Z",
                },
            )
        assert resp.status_code == 200
        assert "text/csv" in resp.headers["content-type"]
        # UTF-8 with BOM
        assert resp.content.startswith(b"\xef\xbb\xbf")
        csv_str = resp.content.decode("utf-8")
        assert "CLPM 诊断统计报表" in csv_str
        assert "OSCILLATION" in csv_str

    def test_export_csv_no_token(self, client) -> None:
        """未认证请求返回 401。"""
        resp = client.get(
            "/api/v1/diagnosis/statistics/export",
            params={
                "startDate": "2026-06-01T00:00:00Z",
                "endDate": "2026-06-30T00:00:00Z",
            },
        )
        assert resp.status_code == 401

    def test_export_csv_missing_params(self, client, fake_redis) -> None:
        """缺少必填参数返回 422。"""
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/diagnosis/statistics/export",
                headers={"Authorization": "Bearer fake-token"},
                params={"startDate": "2026-06-01T00:00:00Z"},
            )
        assert resp.status_code == 422
