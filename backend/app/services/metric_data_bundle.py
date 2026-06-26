"""MetricDataBundle 组装器.

DataPlanner 在获取（命中缓存或回源预处理）DataBlock 后，调用本组装器
按指标数据需求契约（clpm_metric_data_requirement）生成 MetricDataBundle：

    - 应用 Metric Validity Mask（preprocessing.validity_mask.apply_mask）
    - 生成 8 字段数据血缘 DataLineage

MetricDataBundle 是指标计算器的唯一输入，包含数据块引用、掩码表达式、
有效索引列表和数据血缘。

设计依据：数据流程图 §7.5, 算法说明 §3.6-3.7, FDS §5.3.10
"""

from __future__ import annotations

import logging
from typing import Any

from app.contracts.data_types import DataBlock, DataLineage, MetricDataBundle
from app.services.preprocessing.validity_mask import apply_mask

logger = logging.getLogger(__name__)

# 算法版本号（与 kpi_calc v2.0 综合评分公式对齐）
_ALGORITHM_VERSION = "KPI_CALC_v2.0"


class MetricDataBundleAssembler:
    """MetricDataBundle 组装器.

    职责：
        - 调用 ``apply_mask`` 应用 Metric Validity Mask
        - 生成 8 字段 DataLineage（算法说明 §3.7.1）
        - 组装 MetricDataBundle

    设计依据：数据流程图 §7.5 Phase 8, 算法说明 §3.6-3.7
    """

    def assemble(
        self,
        metric_code: str,
        data_block: DataBlock,
        mask_expression: str | None,
        requirement: Any | None = None,
    ) -> MetricDataBundle:
        """组装 MetricDataBundle，应用 Metric Validity Mask.

        Args:
            metric_code: 指标代码，如 ``"accuracy_rate"``
            data_block: 预处理后的数据块（来自缓存或回源预处理）
            mask_expression: 掩码表达式，如 ``"pv_valid && sp_valid"``；
                ``None`` 或空字符串表示不筛选（如好值率）
            requirement: 指标数据需求契约对象（ClpmMetricDataRequirement），
                用于提取聚合策略/质量策略等血缘字段；``None`` 时使用默认值

        Returns:
            MetricDataBundle 实例

        设计依据：算法说明 §3.4.2 步骤⑦, §3.7.1, 数据流程图 §7.5
        """
        # 应用 Metric Validity Mask（算法说明 §3.4.2 步骤⑦, PRD §5.5.4）
        masked_indices = apply_mask(data_block, mask_expression)

        # 生成数据血缘（算法说明 §3.7.1, FDS §5.3.10）
        lineage = self._build_lineage(metric_code, data_block, mask_expression, requirement)

        bundle = MetricDataBundle(
            metric_code=metric_code,
            data_block=data_block,
            mask_expression=mask_expression or "",
            masked_indices=masked_indices,
            lineage=lineage,
        )

        logger.debug(
            "Bundle assembled: metric=%s, tagGroup=%s, total=%d, masked=%d (%.1f%%), "
            "valid_rate=%.4f",
            metric_code,
            data_block.tag_group,
            data_block.point_count,
            len(masked_indices),
            (len(masked_indices) / data_block.point_count * 100)
            if data_block.point_count
            else 0.0,
            data_block.quality_summary.valid_rate,
        )
        return bundle

    # ------------------------------------------------------------------
    # 数据血缘生成
    # ------------------------------------------------------------------

    def _build_lineage(
        self,
        metric_code: str,
        data_block: DataBlock,
        mask_expression: str | None,
        requirement: Any | None,
    ) -> DataLineage:
        """生成 8 字段数据血缘.

        字段（算法说明 §3.7.1, FDS §5.3.10）：
            1. sampling_freq: 实际采样频率（来自 DataBlock）
            2. aggregation_policy: 聚合策略（来自契约，默认 LAST）
            3. quality_policy: 质量策略（来自契约，默认 KEEP_ALL_WITH_VALIDITY）
            4. tag_group: 数据来源 tagGroup（来自 DataBlock）
            5. data_block_ids: 使用的 DataBlock ID 列表
            6. valid_rate: 有效数据率（来自 DataBlock.quality_summary）
            7. data_policy_version: 预处理版本（来自 DataBlock）
            8. algorithm_version: 算法版本（KPI_CALC_v2.0）

        设计依据：算法说明 §3.7.1
        """
        # 从契约对象提取字段（容错：requirement 可能为 None 或 mock）
        aggregation_policy = _safe_get(requirement, "aggregation_policy") or "LAST"
        quality_policy = _safe_get(requirement, "quality_policy") or "KEEP_ALL_WITH_VALIDITY"

        lineage = DataLineage(
            sampling_freq=data_block.sampling_freq,
            aggregation_policy=aggregation_policy,
            quality_policy=quality_policy,
            tag_group=data_block.tag_group,
            data_block_ids=[data_block.data_block_id],
            valid_rate=data_block.quality_summary.valid_rate,
            data_policy_version=data_block.preprocess_version,
            algorithm_version=_ALGORITHM_VERSION,
        )
        return lineage


def _safe_get(obj: Any, attr: str) -> Any:
    """安全获取对象属性，obj 为 None 或属性不存在时返回 None."""
    if obj is None:
        return None
    try:
        return getattr(obj, attr)
    except AttributeError:
        return None


__all__ = ["MetricDataBundleAssembler", "DataLineage"]
