"""NodeAggregator 节点级回路聚合单元测试（算法说明 §4.11）.

测试用例覆盖：
- 正常聚合（按级别权重 1/2/3 → 3/2/1 加权平均）
- 所有回路 INCONCLUSIVE → 评分留空
- 单回路聚合
- loop_weights 字典指定级别
- details.loop_level 指定级别
- 默认级别 3
- 可信度取最低
- 评分限制在 [0, 100]
- weight_total / inconclusive_count 正确统计

设计依据：算法说明 §4.11；GB/T 44693.2-2024 附录 E.2
"""

from __future__ import annotations

from app.contracts.data_types import DataLineage, MetricResult
from app.services.node_aggregation import NodeAggregator

# ---------------------------------------------------------------------------
# 辅助构造
# ---------------------------------------------------------------------------


def _make_loop_result(
    value: float | None,
    confidence: str = "A",
    loop_id: str | None = None,
    loop_level: int | None = None,
) -> MetricResult:
    """构造回路级 MetricResult。"""
    details: dict = {}
    if loop_id is not None:
        details["loop_id"] = loop_id
    if loop_level is not None:
        details["loop_level"] = loop_level
    return MetricResult(
        metric_code="composite_score",
        value=value,
        confidence_level=confidence,
        lineage=DataLineage(),
        details=details,
    )


# ---------------------------------------------------------------------------
# 正常聚合测试
# ---------------------------------------------------------------------------


class TestNormalAggregation:
    """正常场景聚合测试。"""

    def test_three_loops_with_levels(self):
        """3 个回路分属 1/2/3 级，按权重 3/2/1 加权平均。"""
        # level 1, weight 3, score 90
        # level 2, weight 2, score 80
        # level 3, weight 1, score 70
        # 加权平均 = (90*3 + 80*2 + 70*1) / (3+2+1) = (270+160+70)/6 = 500/6 ≈ 83.33
        loops = [
            _make_loop_result(90.0, loop_id="L1", loop_level=1),
            _make_loop_result(80.0, loop_id="L2", loop_level=2),
            _make_loop_result(70.0, loop_id="L3", loop_level=3),
        ]
        agg = NodeAggregator()
        result = agg.aggregate(loops)
        assert result.value == 83.33
        assert result.confidence_level == "A"
        assert result.details["total_loops"] == 3
        assert result.details["valid_loops"] == 3
        assert result.details["inconclusive_count"] == 0
        assert result.details["weight_total"] == 6

    def test_single_loop(self):
        """单回路聚合：值直接透传。"""
        loops = [_make_loop_result(85.0, loop_level=1)]
        agg = NodeAggregator()
        result = agg.aggregate(loops)
        assert result.value == 85.0
        assert result.details["valid_loops"] == 1

    def test_loop_weights_dict_overrides_details(self):
        """loop_weights 字典优先于 details.loop_level。"""
        # details 中 loop_level=3，但 loop_weights 指定 L1 → level 1（权重 3）
        loops = [
            _make_loop_result(90.0, loop_id="L1", loop_level=3),
            _make_loop_result(60.0, loop_id="L2", loop_level=3),
        ]
        agg = NodeAggregator()
        result = agg.aggregate(loops, loop_weights={"L1": 1, "L2": 2})
        # L1 level=1, weight=3, score=90
        # L2 level=2, weight=2, score=60
        # (90*3 + 60*2) / (3+2) = (270+120)/5 = 390/5 = 78.0
        assert result.value == 78.0


# ---------------------------------------------------------------------------
# INCONCLUSIVE 处理
# ---------------------------------------------------------------------------


class TestInconclusiveHandling:
    """INCONCLUSIVE 回路处理测试。"""

    def test_all_inconclusive_returns_none(self):
        """所有回路 INCONCLUSIVE → 节点评分留空。"""
        loops = [
            _make_loop_result(None, confidence="E"),
            _make_loop_result(None, confidence="E"),
        ]
        agg = NodeAggregator()
        result = agg.aggregate(loops)
        assert result.value is None
        assert result.confidence_level == "E"
        assert result.details["reason"] == "all_loops_inconclusive"
        assert result.details["inconclusive_count"] == 2

    def test_partial_inconclusive_excluded(self):
        """部分回路 INCONCLUSIVE → 不参与聚合，单独计数。"""
        loops = [
            _make_loop_result(90.0, loop_level=1),
            _make_loop_result(None, confidence="E"),
            _make_loop_result(70.0, loop_level=3),
        ]
        agg = NodeAggregator()
        result = agg.aggregate(loops)
        # 仅 L1 (weight 3, score 90) 和 L3 (weight 1, score 70)
        # (90*3 + 70*1) / (3+1) = (270+70)/4 = 340/4 = 85.0
        assert result.value == 85.0
        assert result.details["inconclusive_count"] == 1
        assert result.details["valid_loops"] == 2


# ---------------------------------------------------------------------------
# 级别解析
# ---------------------------------------------------------------------------


class TestLevelResolution:
    """回路级别解析测试。"""

    def test_default_level_when_no_info(self):
        """无 loop_id 且无 loop_level → 默认级别 3（权重 1）。"""
        loops = [
            _make_loop_result(80.0),  # 无任何级别信息
            _make_loop_result(90.0, loop_level=1),  # 权重 3
        ]
        agg = NodeAggregator()
        result = agg.aggregate(loops)
        # L1 (default level 3, weight 1, score 80)
        # L2 (level 1, weight 3, score 90)
        # (80*1 + 90*3) / (1+3) = (80+270)/4 = 350/4 = 87.5
        assert result.value == 87.5

    def test_invalid_loop_level_falls_back(self):
        """details.loop_level 非整数 → 回退到默认级别 3。"""
        loops = [
            _make_loop_result(80.0, loop_level="invalid"),  # type: ignore[arg-type]
        ]
        agg = NodeAggregator()
        result = agg.aggregate(loops)
        # 默认 level 3, weight 1 → value = 80
        assert result.value == 80.0

    def test_unknown_level_uses_default_weight(self):
        """未知级别（如 5）→ 使用默认级别权重（level 3 → weight 1）。"""
        loops = [
            _make_loop_result(80.0, loop_level=5),
        ]
        agg = NodeAggregator()
        result = agg.aggregate(loops)
        # LEVEL_WEIGHTS.get(5, LEVEL_WEIGHTS[3]=1) → weight 1
        assert result.value == 80.0


# ---------------------------------------------------------------------------
# 可信度与边界
# ---------------------------------------------------------------------------


class TestConfidenceAndBoundary:
    """可信度与边界值测试。"""

    def test_confidence_takes_minimum(self):
        """可信度取有效回路中最低等级。"""
        loops = [
            _make_loop_result(90.0, confidence="A", loop_level=1),
            _make_loop_result(70.0, confidence="D", loop_level=2),
        ]
        agg = NodeAggregator()
        result = agg.aggregate(loops)
        assert result.confidence_level == "D"

    def test_value_clamped_to_100(self):
        """评分限制在 [0, 100] 上限（即使输入接近上限）。"""
        loops = [
            _make_loop_result(100.0, loop_level=1),
            _make_loop_result(100.0, loop_level=1),
        ]
        agg = NodeAggregator()
        result = agg.aggregate(loops)
        assert result.value == 100.0

    def test_empty_loops_returns_inconclusive(self):
        """空回路列表 → INCONCLUSIVE。"""
        agg = NodeAggregator()
        result = agg.aggregate([])
        assert result.value is None
        assert result.confidence_level == "E"
        assert result.details["total_loops"] == 0

    def test_lineage_from_first_valid_loop(self):
        """血缘取第一条有效回路的血缘。"""
        lineage_first = DataLineage(
            sampling_freq="1s",
            tag_group="BASE",
            algorithm_version="KPI_CALC_v2.0",
        )
        loops = [
            MetricResult(
                metric_code="composite_score",
                value=90.0,
                confidence_level="A",
                lineage=lineage_first,
                details={"loop_level": 1},
            ),
            _make_loop_result(70.0, loop_level=2),
        ]
        agg = NodeAggregator()
        result = agg.aggregate(loops)
        assert result.lineage.sampling_freq == "1s"
        assert result.lineage.tag_group == "BASE"


# ---------------------------------------------------------------------------
# 权重映射常量
# ---------------------------------------------------------------------------


class TestLevelWeights:
    """级别权重常量测试。"""

    def test_level_weights_mapping(self):
        """level 1→3, 2→2, 3→1。"""
        assert NodeAggregator.LEVEL_WEIGHTS == {1: 3, 2: 2, 3: 1}

    def test_default_level(self):
        """默认级别为 3。"""
        assert NodeAggregator.DEFAULT_LEVEL == 3
