"""算法公共输入守卫（AD03/I04）：缺口敏感动态指标的连续性检查.

设计依据：《算法与数据接口核查及兼容改进方案》§4.3 评估链——"缺少分段
支持的动态指标遇硬断点返回现有 INCONCLUSIVE，不悄悄把最长段结果代表整个
窗口"；§3 I04（mask [0,1,60,61] 被当作相邻一步）。

登记口径（逐项，不扩大）：
- ``time_constant``：以统一采样周期做相关分析（I04 证据），且无内建
  均匀性自检 → 列入缺口敏感；
- ``settling_time``：已内建 ``_check_uniform_sampling`` 分段支持 → 沿用
  原支持，不入册；
- 其余统计/比率类指标：沿用既有 masked 压缩口径（legacy 稀疏数据一贯
  行为，由 R14 可信度准入约束），不入册、不改动。

守卫位置：kpi_calc 调用编排处（计算器本身零改动）；守卫失败构造的结果
与计算器自身"数据不足"结果同构（value=None + 原因），不虚构数值。
"""

from __future__ import annotations

from typing import Any

#: 缺口敏感动态指标（点布局非连续 mask → INCONCLUSIVE；legacy 不适用——
#: legacy 行为完全保持）
GAP_SENSITIVE_METRICS: frozenset[str] = frozenset({"time_constant"})

#: 守卫失败原因码（进入 MetricResult.details）
GAP_GUARD_REASON = "GAP_SENSITIVE_MASK_DISCONTINUOUS"


def masked_indices_max_gap(indices: list[int]) -> int:
    """mask 索引相邻间隙的最大跳变（连续等间隔=1；[0,1,60,61]→59）。"""
    if len(indices) < 2:
        return 1
    max_gap = 1
    for prev, cur in zip(indices, indices[1:], strict=False):
        gap = cur - prev
        if gap > max_gap:
            max_gap = gap
    return max_gap


def gap_guard_verdict(
    metric_code: str,
    bundle: Any,
) -> tuple[bool, int]:
    """返回 (放行, 最大间隙)。

    放行条件：指标不在册 / bundle 无 point 上下文（legacy 行为不变）/
    mask 连续（最大间隙=1，即无缺口的等间隔输入）。
    """
    if metric_code not in GAP_SENSITIVE_METRICS:
        return True, 1
    block = getattr(bundle, "data_block", None)
    context = getattr(block, "series_context", None) if block is not None else None
    if context is None or not getattr(context, "is_point", False):
        return True, 1  # legacy：行为完全保持
    indices = list(getattr(bundle, "masked_indices", []) or [])
    max_gap = masked_indices_max_gap(indices)
    return (max_gap <= 1), max_gap


def make_gap_guard_result(bundle: Any, max_gap: int) -> Any:
    """构造守卫失败的 MetricResult（与计算器数据不足结果同构）。"""
    from app.contracts.data_types import DataLineage, MetricResult

    block = bundle.data_block
    return MetricResult(
        metric_code=bundle.metric_code,
        value=None,
        confidence_level="E",
        lineage=DataLineage(
            sampling_freq=block.sampling_freq,
            quality_policy="KEEP_ALL_WITH_VALIDITY",
            tag_group=block.tag_group,
            valid_rate=0.0,
        ),
        details={
            "reason": GAP_GUARD_REASON,
            "maxIndexGap": max_gap,
            "pointCount": block.point_count,
            "maskedCount": len(bundle.masked_indices),
            # 非连续有效样本不得拼成 1s 连续序列——tau/延迟类动态分析拒绝
            "note": (
                "gap-sensitive dynamic metric rejected: masked indices non-contiguous on point grid"
            ),
        },
    )
