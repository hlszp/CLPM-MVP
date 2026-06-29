"""MetricDataBundle 组装器单元测试.

测试要点：
    - Mask 应用（pv_valid && sp_valid）
    - 空 mask 表达式（不筛选，全部索引）
    - DataLineage 8 字段生成
    - valid_rate 计算来自 DataBlock.quality_summary
    - 契约字段正确传入 lineage（aggregation_policy / quality_policy）

设计依据：数据流程图 §7.5, 算法说明 §3.6-3.7
"""

from __future__ import annotations

import pytest

from app.contracts.data_types import DataLineage, TagGroup
from app.services.metric_data_bundle import MetricDataBundleAssembler

from .conftest import build_data_block, build_requirement


class TestMaskApplication:
    """Metric Validity Mask 应用测试."""

    def test_mask_pv_and_sp_valid(self) -> None:
        """pv_valid && sp_valid 应只返回两者都有效的索引."""
        assembler = MetricDataBundleAssembler()
        block = build_data_block(n=10, valid_rate=0.8)  # 8 有效，2 无效
        req = build_requirement(
            "accuracy_rate",
            TagGroup.BASE,
            ["pv", "sp"],
            mask_expression="pv_valid && sp_valid",
        )

        bundle = assembler.assemble("accuracy_rate", block, "pv_valid && sp_valid", req)

        assert bundle.metric_code == "accuracy_rate"
        assert bundle.mask_expression == "pv_valid && sp_valid"
        # 8 个有效点（前 8 个）
        assert bundle.masked_indices == [0, 1, 2, 3, 4, 5, 6, 7]

    def test_empty_mask_returns_all_indices(self) -> None:
        """空 mask 表达式应返回全部索引（如好值率）."""
        assembler = MetricDataBundleAssembler()
        block = build_data_block(n=10, valid_rate=0.5)
        req = build_requirement(
            "good_value_rate",
            TagGroup.QUALITY_HF,
            ["pv_quality"],
            mask_expression=None,
        )

        bundle = assembler.assemble("good_value_rate", block, None, req)

        assert bundle.masked_indices == list(range(10))

    def test_empty_string_mask_returns_all(self) -> None:
        """空字符串 mask 应返回全部索引."""
        assembler = MetricDataBundleAssembler()
        block = build_data_block(n=5, valid_rate=1.0)
        bundle = assembler.assemble("m", block, "", None)
        assert bundle.masked_indices == [0, 1, 2, 3, 4]

    def test_mask_with_partial_validity(self) -> None:
        """部分有效时 mask 应正确筛选."""
        assembler = MetricDataBundleAssembler()
        block = build_data_block(n=20, valid_rate=0.5)  # 前 10 有效
        bundle = assembler.assemble("stability_rate", block, "pv_valid && sp_valid", None)
        assert bundle.masked_indices == list(range(10))


class TestDataLineageGeneration:
    """数据血缘 8 字段生成测试."""

    def test_lineage_has_all_8_fields(self) -> None:
        """DataLineage 应包含算法说明 §3.7.1 定义的 8 个字段."""
        assembler = MetricDataBundleAssembler()
        block = build_data_block(
            n=100,
            tag_group=TagGroup.BASE,
            sampling_freq="5s",
            valid_rate=0.95,
            config_version="cfg_12",
        )
        req = build_requirement(
            "accuracy_rate",
            TagGroup.BASE,
            ["pv", "sp"],
            mask_expression="pv_valid && sp_valid",
            aggregation_policy="LAST",
            quality_policy="KEEP_ALL_WITH_VALIDITY",
        )

        bundle = assembler.assemble("accuracy_rate", block, "pv_valid && sp_valid", req)
        lineage = bundle.lineage

        # 8 字段（算法说明 §3.7.1）
        assert isinstance(lineage, DataLineage)
        assert lineage.sampling_freq == "5s"
        assert lineage.aggregation_policy == "LAST"
        assert lineage.quality_policy == "KEEP_ALL_WITH_VALIDITY"
        assert lineage.tag_group == "BASE"
        assert lineage.data_block_ids == [block.data_block_id]
        assert lineage.valid_rate == pytest.approx(0.95)
        assert lineage.data_policy_version == block.preprocess_version
        assert lineage.algorithm_version == "KPI_CALC_v2.0"

    def test_lineage_defaults_when_requirement_none(self) -> None:
        """requirement 为 None 时 lineage 使用默认值."""
        assembler = MetricDataBundleAssembler()
        block = build_data_block(n=10, tag_group=TagGroup.OP_HF)

        bundle = assembler.assemble("saturation_rate", block, "op_valid", None)
        lineage = bundle.lineage

        assert lineage.aggregation_policy == "LAST"  # 默认
        assert lineage.quality_policy == "KEEP_ALL_WITH_VALIDITY"  # 默认
        assert lineage.tag_group == "OP_HF"

    def test_lineage_uses_requirement_policies(self) -> None:
        """lineage 应从 requirement 读取聚合/质量策略."""
        assembler = MetricDataBundleAssembler()
        block = build_data_block(n=10, tag_group=TagGroup.QUALITY_HF)
        req = build_requirement(
            "good_value_rate",
            TagGroup.QUALITY_HF,
            ["pv_quality"],
            quality_policy="KEEP_ALL",
            aggregation_policy="MEAN",
        )

        bundle = assembler.assemble("good_value_rate", block, None, req)
        lineage = bundle.lineage

        assert lineage.quality_policy == "KEEP_ALL"
        assert lineage.aggregation_policy == "MEAN"

    def test_lineage_to_dict_serializable(self) -> None:
        """DataLineage.to_dict 应返回可 JSON 序列化的字典."""
        assembler = MetricDataBundleAssembler()
        block = build_data_block(n=10)
        bundle = assembler.assemble("m", block, None, None)
        d = bundle.lineage.to_dict()

        import json

        json.dumps(d)  # 不应抛异常
        assert set(d.keys()) == {
            "sampling_freq",
            "aggregation_policy",
            "quality_policy",
            "tag_group",
            "data_block_ids",
            "valid_rate",
            "data_policy_version",
            "algorithm_version",
        }


class TestValidRateCalculation:
    """valid_rate 计算测试."""

    def test_valid_rate_from_quality_summary(self) -> None:
        """lineage.valid_rate 应来自 DataBlock.quality_summary.valid_rate."""
        assembler = MetricDataBundleAssembler()
        for expected_rate in [1.0, 0.95, 0.80, 0.60, 0.20]:
            block = build_data_block(n=100, valid_rate=expected_rate)
            bundle = assembler.assemble("m", block, None, None)
            assert bundle.lineage.valid_rate == pytest.approx(expected_rate)

    def test_masked_indices_count_reflects_valid_rate(self) -> None:
        """masked_indices 数量应反映 valid_rate（pv_valid && sp_valid 时）."""
        assembler = MetricDataBundleAssembler()
        block = build_data_block(n=100, valid_rate=0.85)
        bundle = assembler.assemble("m", block, "pv_valid && sp_valid", None)
        # 85% 有效 → 85 个 masked 索引
        assert len(bundle.masked_indices) == 85


class TestBundleStructure:
    """MetricDataBundle 结构完整性测试."""

    def test_bundle_references_same_data_block(self) -> None:
        """bundle.data_block 应引用传入的 DataBlock（不复制）."""
        assembler = MetricDataBundleAssembler()
        block = build_data_block(n=10)
        bundle = assembler.assemble("m", block, None, None)
        assert bundle.data_block is block

    def test_assemble_multiple_metrics_share_block(self) -> None:
        """多个指标可共享同一 DataBlock（缓存复用场景）."""
        assembler = MetricDataBundleAssembler()
        block = build_data_block(n=20, valid_rate=0.9)
        req1 = build_requirement(
            "accuracy_rate", TagGroup.BASE, ["pv", "sp"], "pv_valid && sp_valid"
        )
        req2 = build_requirement(
            "stability_rate", TagGroup.BASE, ["pv", "sp"], "pv_valid && sp_valid"
        )

        b1 = assembler.assemble("accuracy_rate", block, "pv_valid && sp_valid", req1)
        b2 = assembler.assemble("stability_rate", block, "pv_valid && sp_valid", req2)

        assert b1.data_block is b2.data_block  # 同一引用
        assert b1.metric_code != b2.metric_code
        assert b1.masked_indices == b2.masked_indices  # 相同 mask
