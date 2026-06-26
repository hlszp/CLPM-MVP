"""可信度评估器与综合评分计算单元测试（算法说明 §3.7.1, §3.7.2, §4.10）.

测试用例覆盖：
- evaluate: 可信度等级 A/B/C/D/E 边界判定
- build_lineage: 数据血缘 8 字段构建
- compute_composite_score: 综合评分 v2 计算
    - 正常（A/F/S/R 均存在）
    - R INCONCLUSIVE → 评分留空
    - R 缺失 → 基础评分 60%
    - 所有权重为 0 → 0
    - 核心指标缺失 → 0 计入分子
    - 评分限制在 [0, 100]
    - 可信度取最低等级

设计依据：算法说明 §3.7.1, §3.7.2, §4.10；GB/T 44693.2-2024 附录 B.6
"""

from __future__ import annotations

import pytest

from app.contracts.data_types import (
    ConfidenceLevel,
    DataLineage,
    MetricResult,
)
from app.services.confidence_evaluator import (
    ALGORITHM_VERSION,
    ConfidenceEvaluator,
    DEFAULT_WEIGHTS,
    DISCOUNT_METRIC_CODE,
    QUALITY_POLICY,
)

from .conftest import make_bundle


# ---------------------------------------------------------------------------
# 辅助构造
# ---------------------------------------------------------------------------


def _make_metric_result(
    metric_code: str,
    value: float | None,
    confidence: str = "A",
    lineage: DataLineage | None = None,
) -> MetricResult:
    """构造 MetricResult。"""
    return MetricResult(
        metric_code=metric_code,
        value=value,
        confidence_level=confidence,
        lineage=lineage or DataLineage(),
        details={},
    )


def _make_full_results(
    a: float = 90.0,
    f: float = 80.0,
    s: float = 70.0,
    r: float = 60.0,
    confidence: str = "A",
) -> dict[str, MetricResult]:
    """构造完整四指标结果字典。"""
    return {
        "accuracy_rate": _make_metric_result("accuracy_rate", a, confidence),
        "fast_rate": _make_metric_result("fast_rate", f, confidence),
        "stability_rate": _make_metric_result("stability_rate", s, confidence),
        DISCOUNT_METRIC_CODE: _make_metric_result(
            DISCOUNT_METRIC_CODE, r, confidence
        ),
    }


# ---------------------------------------------------------------------------
# evaluate 测试
# ---------------------------------------------------------------------------


class TestEvaluate:
    """可信度等级判定测试。"""

    @pytest.mark.parametrize(
        "valid_rate,expected",
        [
            (1.00, ConfidenceLevel.A),
            (0.95, ConfidenceLevel.A),
            (0.949, ConfidenceLevel.B),
            (0.80, ConfidenceLevel.B),
            (0.799, ConfidenceLevel.C),
            (0.60, ConfidenceLevel.C),
            (0.599, ConfidenceLevel.D),
            (0.20, ConfidenceLevel.D),
            (0.199, ConfidenceLevel.E),
            (0.0, ConfidenceLevel.E),
        ],
    )
    def test_boundary_thresholds(self, valid_rate, expected):
        """A/B/C/D/E 边界阈值正确判定。"""
        assert ConfidenceEvaluator.evaluate(valid_rate) == expected

    def test_one_returns_A(self):
        """valid_rate=1.0 → A 级。"""
        assert ConfidenceEvaluator.evaluate(1.0) == ConfidenceLevel.A

    def test_zero_returns_E(self):
        """valid_rate=0.0 → E 级（INCONCLUSIVE）。"""
        assert ConfidenceEvaluator.evaluate(0.0) == ConfidenceLevel.E


# ---------------------------------------------------------------------------
# build_lineage 测试
# ---------------------------------------------------------------------------


class TestBuildLineage:
    """数据血缘构建测试。"""

    def test_lineage_eight_fields(self):
        """血缘 8 字段正确填充。"""
        bundle = make_bundle(
            {"pv": [50.0, 50.0], "sp": [50.0, 50.0]},
            metric_code="accuracy_rate",
            tag_group="BASE",
            sampling_freq="1s",
        )
        lineage = ConfidenceEvaluator.build_lineage(bundle, 0.95)
        # 8 字段
        assert lineage.sampling_freq == "1s"
        assert lineage.aggregation_policy == "LAST"
        assert lineage.quality_policy == QUALITY_POLICY
        assert lineage.tag_group == "BASE"
        assert lineage.data_block_ids == ["db_test_BASE_1s"]
        assert lineage.valid_rate == 0.95
        assert lineage.data_policy_version == "pre_v1"
        assert lineage.algorithm_version == ALGORITHM_VERSION

    def test_lineage_valid_rate_rounded(self):
        """valid_rate 保留 4 位小数。"""
        bundle = make_bundle(
            {"pv": [50.0] * 3, "sp": [50.0] * 3},
            metric_code="accuracy_rate",
        )
        lineage = ConfidenceEvaluator.build_lineage(bundle, 0.95123456)
        assert lineage.valid_rate == 0.9512

    def test_lineage_custom_algorithm_version(self):
        """自定义 algorithm_version 生效。"""
        bundle = make_bundle(
            {"pv": [50.0], "sp": [50.0]},
            metric_code="accuracy_rate",
        )
        lineage = ConfidenceEvaluator.build_lineage(
            bundle, 1.0, algorithm_version="KPI_CALC_v3.0"
        )
        assert lineage.algorithm_version == "KPI_CALC_v3.0"

    def test_lineage_tag_group_propagated(self):
        """tag_group 从 data_block 透传到血缘。"""
        bundle = make_bundle(
            {"pv": [50.0], "sp": [50.0]},
            metric_code="accuracy_rate",
            tag_group="PVOP_HF",
        )
        lineage = ConfidenceEvaluator.build_lineage(bundle, 1.0)
        assert lineage.tag_group == "PVOP_HF"


# ---------------------------------------------------------------------------
# compute_composite_score 测试
# ---------------------------------------------------------------------------


class TestComputeCompositeScore:
    """综合评分 v2 计算测试。"""

    def test_normal_full_metrics(self):
        """正常场景：A=90, F=80, S=70, R=60，默认权重。"""
        results = _make_full_results(a=90.0, f=80.0, s=70.0, r=60.0)
        score = ConfidenceEvaluator.compute_composite_score(results)
        assert score.value is not None
        # 默认权重 a=0.25, f=0.20, s=0.55
        # 加权和 = 0.25*0.9 + 0.20*0.8 + 0.55*0.7 = 0.225 + 0.16 + 0.385 = 0.77
        # base_score = 0.77 / 1.0 * 100 = 77.0
        # P = 77.0 * 60/100 = 46.2
        assert score.value == 46.2
        assert score.metric_code == "composite_score"

    def test_R_inconclusive_score_none(self):
        """R 可信度 E 级（INCONCLUSIVE）→ 评分留空。"""
        results = _make_full_results()
        results[DISCOUNT_METRIC_CODE] = _make_metric_result(
            DISCOUNT_METRIC_CODE, None, confidence="E"
        )
        score = ConfidenceEvaluator.compute_composite_score(results)
        assert score.value is None
        assert score.confidence_level == ConfidenceLevel.E.value

    def test_R_missing_degrades_to_60_percent(self):
        """R 缺失 → 评分为基础评分的 60%。"""
        results = _make_full_results(a=100.0, f=100.0, s=100.0, r=100.0)
        # 删除 R
        del results[DISCOUNT_METRIC_CODE]
        score = ConfidenceEvaluator.compute_composite_score(results)
        assert score.value is not None
        # base_score = 100, R 缺失 → 100 * 0.6 = 60
        assert score.value == 60.0

    def test_zero_total_weight_returns_zero(self):
        """所有权重为 0 → 评分为 0。"""
        results = _make_full_results()
        score = ConfidenceEvaluator.compute_composite_score(
            results, weights={"accuracy_rate": 0.0, "fast_rate": 0.0, "stability_rate": 0.0}
        )
        assert score.value == 0.0
        assert score.details["reason"] == "zero total weight"

    def test_missing_core_metric_counted_as_zero(self):
        """核心指标缺失（value=None）→ 按 0 计入分子，权重仍计入分母。"""
        results = _make_full_results(a=100.0, f=100.0, s=100.0, r=100.0)
        # accuracy 缺失
        results["accuracy_rate"] = _make_metric_result("accuracy_rate", None)
        score = ConfidenceEvaluator.compute_composite_score(results)
        # a=0.25, f=0.20, s=0.55；A=None 视为 0
        # 加权和 = 0 + 0.20*1.0 + 0.55*1.0 = 0.75
        # base = 0.75 / 1.0 * 100 = 75
        # P = 75 * 100/100 = 75
        assert score.value == 75.0

    def test_custom_weights(self):
        """自定义权重生效。"""
        results = _make_full_results(a=100.0, f=100.0, s=100.0, r=100.0)
        weights = {"accuracy_rate": 0.5, "fast_rate": 0.3, "stability_rate": 0.2}
        score = ConfidenceEvaluator.compute_composite_score(results, weights=weights)
        # 全 100，权重和=1.0 → base=100, P=100
        assert score.value == 100.0
        assert score.details["weights"] == {"a": 0.5, "f": 0.3, "s": 0.2}

    def test_value_clamped_to_100(self):
        """评分限制在 [0, 100] 上限。"""
        results = _make_full_results(a=100.0, f=100.0, s=100.0, r=100.0)
        score = ConfidenceEvaluator.compute_composite_score(results)
        assert score.value == 100.0
        assert score.value <= 100.0

    def test_confidence_takes_minimum(self):
        """可信度取核心指标 + R 中最低等级。"""
        results = _make_full_results(confidence="A")
        results["stability_rate"] = _make_metric_result(
            "stability_rate", 50.0, confidence="D"
        )
        score = ConfidenceEvaluator.compute_composite_score(results)
        assert score.confidence_level == "D"

    def test_lineage_from_accuracy(self):
        """血缘取 accuracy_rate 的血缘（若存在）。"""
        lineage_acc = DataLineage(
            sampling_freq="5s",
            tag_group="BASE",
            algorithm_version="KPI_CALC_v2.0",
        )
        results = _make_full_results()
        results["accuracy_rate"] = _make_metric_result(
            "accuracy_rate", 90.0, lineage=lineage_acc
        )
        score = ConfidenceEvaluator.compute_composite_score(results)
        assert score.lineage.sampling_freq == "5s"
        assert score.lineage.tag_group == "BASE"


# ---------------------------------------------------------------------------
# 默认权重常量
# ---------------------------------------------------------------------------


class TestDefaults:
    """模块常量测试。"""

    def test_default_weights_keys(self):
        """默认权重包含三个核心指标。"""
        assert set(DEFAULT_WEIGHTS.keys()) == {
            "accuracy_rate",
            "fast_rate",
            "stability_rate",
        }

    def test_algorithm_version(self):
        """算法版本号正确。"""
        assert ALGORITHM_VERSION == "KPI_CALC_v2.0"

    def test_quality_policy(self):
        """质量策略标识正确。"""
        assert QUALITY_POLICY == "KEEP_ALL_WITH_VALIDITY"
