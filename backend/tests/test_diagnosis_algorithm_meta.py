"""Batch 4 F1：算法元数据 API + A/B 对比 includeDiagnosis 扩展测试.

Covers:
- GET /api/v1/diagnosis/algorithms/meta（8 类算法展示元数据 + 阈值快照）
- GET /api/v1/diagnosis/ab-compare?includeDiagnosis=true（诊断标签对比）
- _diff_label_changes 辅助函数单元测试
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.services.diagnosis import ALGORITHM_META_STATIC, list_algorithm_meta
from app.services.tracker import _diff_label_changes
from tests.conftest import TEST_USERS, mock_current_user

# 8 类诊断标签（与 ALGORITHM_META_STATIC 一致）
_ALL_LABELS = list(ALGORITHM_META_STATIC.keys())


def _make_diag_config(
    diag_code: str = "OSCILLATION",
    threshold: dict | None = None,
    is_enabled: bool = True,
) -> MagicMock:
    """构造 DiagnosisConfig mock。"""
    c = MagicMock()
    c.id = str(uuid4())
    c.diag_code = diag_code
    c.diag_name = diag_code
    c.algorithm_type = "FFT"
    c.calc_method = None
    c.params = None
    c.threshold = threshold
    c.is_enabled = is_enabled
    c.updated_by = "admin"
    c.updated_at = datetime.now(UTC)
    c.version = 1
    return c


def _make_diag_result(
    diag_label: str = "OSCILLATION",
    confidence: Decimal = Decimal("80.00"),
    diagnosed_at: datetime | None = None,
) -> MagicMock:
    """构造 DiagnosisResult mock。"""
    r = MagicMock()
    r.id = str(uuid4())
    r.loop_id = "00000000-0000-0000-0000-000000000201"
    r.diag_label = diag_label
    r.confidence = confidence
    r.diagnosed_at = diagnosed_at or datetime.now(UTC)
    return r


def _make_loop() -> MagicMock:
    loop = MagicMock()
    loop.id = "00000000-0000-0000-0000-000000000201"
    loop.tag_name = "101-FC-1023"
    return loop


def _make_scalars_mock(items: list) -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = items
    return result


def _make_scalar_one_or_none_mock(value) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _make_window_agg_mock(count: int, avgs: list) -> MagicMock:
    """构造窗口聚合查询（func.count + 8 项 func.avg）结果 mock。"""
    result = MagicMock()
    result.one.return_value = (count, *avgs)
    return result


# ---------------------------------------------------------------------------
# 算法元数据 API
# ---------------------------------------------------------------------------


class TestAlgorithmMeta:
    """GET /api/v1/diagnosis/algorithms/meta tests."""

    @pytest.mark.asyncio
    async def test_list_algorithm_meta_merges_threshold(self) -> None:
        """list_algorithm_meta 合并静态元数据与运行时阈值快照。"""
        db = MagicMock()
        configs = [
            _make_diag_config("OSCILLATION", threshold={"similarity_threshold": 0.5}),
            _make_diag_config("OUTPUT_SATURATION", is_enabled=False),
        ]
        db.execute = AsyncMock(return_value=_make_scalars_mock(configs))

        result = await list_algorithm_meta(db)
        assert result["total"] == 8
        items = {item["label"]: item for item in result["items"]}
        # OSCILLATION 有阈值快照
        assert items["OSCILLATION"]["threshold"] == {"similarity_threshold": 0.5}
        assert items["OSCILLATION"]["isEnabled"] is True
        assert items["OSCILLATION"]["visualizationKey"] == "spectrum"
        assert "FFT" in items["OSCILLATION"]["algorithmName"]
        # OUTPUT_SATURATION 被禁用
        assert items["OUTPUT_SATURATION"]["isEnabled"] is False
        # 未配置阈值的标签 threshold 为 None
        assert items["VALVE_STICTION"]["threshold"] is None
        # MANUAL_REVIEW 无可视化键
        assert items["MANUAL_REVIEW"]["visualizationKey"] is None
        # 所有标签都有原理说明与置信度释义
        for label in _ALL_LABELS:
            assert items[label]["principle"]
            assert items[label]["confidenceLevelExplanation"]

    def test_algorithm_meta_api_success(self, client, mock_db, fake_redis) -> None:
        """认证用户可获取 8 类算法元数据。"""
        configs = [_make_diag_config("OSCILLATION", threshold={"similarity_threshold": 0.4})]
        mock_db.execute = AsyncMock(return_value=_make_scalars_mock(configs))
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                "/api/v1/diagnosis/algorithms/meta",
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert body["data"]["total"] == 8
        labels = [item["label"] for item in body["data"]["items"]]
        assert set(labels) == set(_ALL_LABELS)
        osc = next(item for item in body["data"]["items"] if item["label"] == "OSCILLATION")
        assert osc["algorithmName"]
        assert osc["visualizationKey"] == "spectrum"
        assert osc["threshold"] == {"similarity_threshold": 0.4}

    def test_algorithm_meta_api_no_token(self, client) -> None:
        """未认证请求返回 401。"""
        resp = client.get("/api/v1/diagnosis/algorithms/meta")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# _diff_label_changes 辅助函数
# ---------------------------------------------------------------------------


class TestDiffLabelChanges:
    """_diff_label_changes 单元测试。"""

    def test_added_label(self) -> None:
        before = [{"label": "OSCILLATION", "confidence": 0.8, "diagnosedAt": None}]
        after = [
            {"label": "OSCILLATION", "confidence": 0.8, "diagnosedAt": None},
            {"label": "VALVE_STICTION", "confidence": 0.7, "diagnosedAt": None},
        ]
        changes = _diff_label_changes(before, after)
        assert len(changes) == 1
        assert changes[0]["label"] == "VALVE_STICTION"
        assert changes[0]["change"] == "added"
        assert changes[0]["afterConfidence"] == 0.7

    def test_removed_label(self) -> None:
        before = [
            {"label": "OSCILLATION", "confidence": 0.8, "diagnosedAt": None},
            {"label": "VALVE_STICTION", "confidence": 0.7, "diagnosedAt": None},
        ]
        after = [{"label": "OSCILLATION", "confidence": 0.8, "diagnosedAt": None}]
        changes = _diff_label_changes(before, after)
        assert len(changes) == 1
        assert changes[0]["label"] == "VALVE_STICTION"
        assert changes[0]["change"] == "removed"

    def test_confidence_changed(self) -> None:
        before = [{"label": "OSCILLATION", "confidence": 0.5, "diagnosedAt": None}]
        after = [{"label": "OSCILLATION", "confidence": 0.8, "diagnosedAt": None}]
        changes = _diff_label_changes(before, after)
        assert len(changes) == 1
        assert changes[0]["change"] == "confidence_changed"
        assert changes[0]["beforeConfidence"] == 0.5
        assert changes[0]["afterConfidence"] == 0.8

    def test_no_changes(self) -> None:
        before = [{"label": "OSCILLATION", "confidence": 0.8, "diagnosedAt": None}]
        after = [{"label": "OSCILLATION", "confidence": 0.8, "diagnosedAt": None}]
        assert _diff_label_changes(before, after) == []

    def test_both_empty(self) -> None:
        assert _diff_label_changes([], []) == []


# ---------------------------------------------------------------------------
# A/B 对比 includeDiagnosis=true
# ---------------------------------------------------------------------------


class TestAbCompareIncludeDiagnosis:
    """GET /api/v1/diagnosis/ab-compare?includeDiagnosis=true tests."""

    def test_ab_compare_with_diagnosis_labels(self, client, mock_db, fake_redis) -> None:
        """includeDiagnosis=true 返回前后窗口诊断标签与变化。"""
        loop = _make_loop()
        avgs = [Decimal("50")] * 8
        before_diag = [
            _make_diag_result("OSCILLATION", Decimal("80.00")),
        ]
        after_diag = [
            _make_diag_result("OSCILLATION", Decimal("40.00")),
            _make_diag_result("VALVE_STICTION", Decimal("70.00")),
        ]

        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_scalar_one_or_none_mock(loop)
            if call_count[0] == 2:
                return self._make_window_agg_mock(100, avgs)
            if call_count[0] == 3:
                return self._make_window_agg_mock(200, avgs)
            if call_count[0] == 4:
                return _make_scalars_mock(before_diag)
            return _make_scalars_mock(after_diag)

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                (
                    f"/api/v1/diagnosis/ab-compare?loopId={loop.id}"
                    "&beforeStartTime=2026-07-01T00:00:00Z"
                    "&beforeEndTime=2026-07-08T00:00:00Z"
                    "&afterStartTime=2026-07-08T00:00:00Z"
                    "&afterEndTime=2026-07-15T00:00:00Z"
                    "&includeDiagnosis=true"
                ),
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        body = resp.json()
        data = body["data"]
        # 诊断标签对比字段存在
        assert data["beforeDiagnosisLabels"] is not None
        assert data["afterDiagnosisLabels"] is not None
        assert data["labelChanges"] is not None
        # before 仅 OSCILLATION（置信度 0.80）
        before_labels = {item["label"]: item for item in data["beforeDiagnosisLabels"]}
        assert "OSCILLATION" in before_labels
        assert before_labels["OSCILLATION"]["confidence"] == 0.8
        # after 有 OSCILLATION + VALVE_STICTION
        after_labels = {item["label"]: item for item in data["afterDiagnosisLabels"]}
        assert "VALVE_STICTION" in after_labels
        # labelChanges 包含新增与置信度变化
        changes = {c["label"]: c for c in data["labelChanges"]}
        assert changes["VALVE_STICTION"]["change"] == "added"
        assert changes["OSCILLATION"]["change"] == "confidence_changed"

    def test_ab_compare_without_include_diagnosis(self, client, mock_db, fake_redis) -> None:
        """includeDiagnosis=false（默认）不返回诊断标签对比字段（None）。"""
        loop = _make_loop()
        avgs = [Decimal("50")] * 8

        call_count = [0]

        async def execute_side_effect(stmt, *args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return _make_scalar_one_or_none_mock(loop)
            if call_count[0] == 2:
                return self._make_window_agg_mock(100, avgs)
            return self._make_window_agg_mock(200, avgs)

        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        with mock_current_user(TEST_USERS["admin"]):
            resp = client.get(
                (
                    f"/api/v1/diagnosis/ab-compare?loopId={loop.id}"
                    "&beforeStartTime=2026-07-01T00:00:00Z"
                    "&beforeEndTime=2026-07-08T00:00:00Z"
                    "&afterStartTime=2026-07-08T00:00:00Z"
                    "&afterEndTime=2026-07-15T00:00:00Z"
                ),
                headers={"Authorization": "Bearer fake-token"},
            )
        assert resp.status_code == 200
        data = resp.json()["data"]
        # 默认不返回诊断标签对比
        assert data["beforeDiagnosisLabels"] is None
        assert data["afterDiagnosisLabels"] is None
        assert data["labelChanges"] is None
        # 仅 3 次 DB 调用（loop + before agg + after agg）
        assert call_count[0] == 3

    @staticmethod
    def _make_window_agg_mock(count: int, avgs: list) -> MagicMock:
        result = MagicMock()
        result.one.return_value = (count, *avgs)
        return result
