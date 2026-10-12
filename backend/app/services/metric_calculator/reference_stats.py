"""F 参考双统计与每指标独立加权统计（P3-09a 纯函数，08d 聚合消费）.

设计依据（正本）：
- D0+P3-08a 公共契约草案 §1.4（F 参考独立统计规则——双统计字段名契约）；
- 10 号文 §3.6.3（每个分项独立汇总——分子与权重和合并，不平均各装置
  百分比/均分，不统一用 evaluated_loops 给所有指标加权）；
- 04/10 号文 C26-EA3（9 条参考 100 权重 1 + 1 条动态 F0 权重 3 → 主参考
  均分 75、动态子集 0、参考权重 75%；全静稳 → 参考均分 100、动态子集
  null、动态覆盖 0%）。

本模块为**纯函数**：不读 DB/缓存，输入为逐对象条目（由 08d 从 record
payload ``metricsSemantic``/``composite`` 中提取），输出契约字段名。
记录级字段（referenceScore/referencePolicy/evidenceStatus 等）由
fast_rate 计算器产出；本模块只做跨对象聚合。

核心口径（10§3.6.3）：
1. 快速性参考均分（主参考展示）：每对象有完整正式 F 取正式 F；否则仅有
   满足 §3.6.1 的静稳参考分才取 referenceScore；其他对象不进入点值平均。
   按既有回路/组重要度权重加权。``dynamicEstimateCount + policyReferenceCount
   + unavailableCount = 参评对象数``。
2. 动态证据充分的 F 均分：只用完整正式 F；无完整 F 时 null，参评数>0 而
   动态数=0 时动态覆盖 0%。
3. 零值参与、null 不作 0；零权重对象保留计数不贡献均分；权重和为 0 时
   均分 null。
"""

from __future__ import annotations

from typing import Any


def weighted_score_stats(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """通用每指标独立加权统计（10§3.6.3"对每个指标独立保存入选对象、加权
    分子、权重和"的纯函数形态；08d 装置→全厂合并时对本函数输出再合并
    分子与权重和，不得平均百分比）.

    Args:
        entries: 逐对象条目 ``[{"weight": w>0, "value": float|None}, ...]``；
            value=None 表示该对象无该指标正式值（不作 0）

    Returns:
        ``{"objectCount", "numericCount", "weightedNumerator",
           "weightSum"（含无值对象权重）, "numericWeightSum"（仅有值对象）,
           "weightedMean"（numericWeightSum=0 → None）}``
    """
    object_count = len(entries)
    weighted_numerator = 0.0
    weight_sum = 0.0
    numeric_weight_sum = 0.0
    numeric_count = 0
    for e in entries:
        w = float(e.get("weight") or 0.0)
        weight_sum += w
        v = e.get("value")
        if v is None:
            continue  # null 不作 0；其权重留在 weightSum（分母不缩）
        numeric_count += 1
        numeric_weight_sum += w
        eta = max(0.0, min(1.0, float(v) / 100.0))
        weighted_numerator += w * eta
    return {
        "objectCount": object_count,
        "numericCount": numeric_count,
        "weightedNumerator": round(weighted_numerator, 6),
        "weightSum": round(weight_sum, 6),
        "numericWeightSum": round(numeric_weight_sum, 6),
        "weightedMean": (
            round(weighted_numerator / numeric_weight_sum * 100.0, 4)
            if numeric_weight_sum > 0
            else None
        ),
    }


def fast_rate_reference_stats(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """F 参考双统计（契约 §1.4 字段名契约；C26-EA3 oracle）.

    Args:
        entries: 逐评分对象条目，每条形如::

            {
                "weight": float,              # 回路/组重要度权重（≥0）
                "formalValue": float | None,  # 完整正式 F（合法 0 有效）
                "referenceScore": float | None,  # §3.6.1 静稳参考分（100）
            }

        formalValue 优先：正式 F（含 0 分）与参考分互斥，同对象两者并存时
        以正式 F 为准（兼容读取——10§3.6.1"兼容读取如遇残留参考字段仍以
        合法正式 F 优先（包括 0 分）"）。

    Returns:
        双统计字段（契约 §1.4）：
        - 主参考展示：``fastRateReferenceMean`` /
          ``referenceWeightedNumerator`` / ``referenceWeightSum`` /
          ``numericCount`` / ``dynamicEstimateCount`` /
          ``policyReferenceCount`` / ``unavailableCount`` /
          ``policyReferenceWeightShare``；
        - 动态子集：``fastRateDynamicEstimateMean`` /
          ``dynamicWeightedNumerator`` / ``dynamicWeightSum`` /
          ``dynamicSampleCount`` / ``dynamicEvidenceCoverageRatio``。
        其中 ``dynamicEstimateCount + policyReferenceCount +
        unavailableCount = objectCount``（参评对象数守恒）。
    """
    object_count = len(entries)
    ref_numerator = 0.0
    ref_weight = 0.0
    policy_ref_weight = 0.0  # 仅静稳参考分对象的权重（policyReferenceWeightShare 分子）
    dyn_numerator = 0.0
    dyn_weight = 0.0
    numeric_count = 0
    dynamic_count = 0
    reference_count = 0
    unavailable_count = 0

    for e in entries:
        w = float(e.get("weight") or 0.0)
        formal = e.get("formalValue")
        reference = e.get("referenceScore")
        if formal is not None:
            # 完整正式 F（含合法 0 分）——正式与参考互斥，正式优先
            dynamic_count += 1
            dyn_weight += w
            dyn_numerator += w * max(0.0, min(1.0, float(formal) / 100.0))
            numeric_count += 1
            ref_numerator += w * max(0.0, min(1.0, float(formal) / 100.0))
            ref_weight += w
        elif reference is not None:
            # 仅有静稳参考分（动态证据不足，管理统计约定值）
            reference_count += 1
            numeric_count += 1
            ref_numerator += w * max(0.0, min(1.0, float(reference) / 100.0))
            ref_weight += w
            policy_ref_weight += w
        else:
            unavailable_count += 1

    return {
        # 1. 快速性参考均分（主参考展示；含参考值时消费方名称/徽标必须
        #    明确"含 N 条动态证据不足"——契约 §1.4）
        "fastRateReferenceMean": round(ref_numerator / ref_weight * 100.0, 4)
        if ref_weight > 0
        else None,
        "referenceWeightedNumerator": round(ref_numerator, 6),
        "referenceWeightSum": round(ref_weight, 6),
        "numericCount": numeric_count,
        "dynamicEstimateCount": dynamic_count,
        "policyReferenceCount": reference_count,
        "unavailableCount": unavailable_count,
        # 参考均分中政策参考值所占权重（C26-EA3：9/(9+3)=75%）；
        # 无参考均分对象时 null
        "policyReferenceWeightShare": round(policy_ref_weight / ref_weight, 6)
        if ref_weight > 0
        else None,
        # 2. 动态证据充分的 F 均分（只用完整正式 F；不称"现场实测快速性"）
        "fastRateDynamicEstimateMean": round(dyn_numerator / dyn_weight * 100.0, 4)
        if dyn_weight > 0
        else None,
        "dynamicWeightedNumerator": round(dyn_numerator, 6),
        "dynamicWeightSum": round(dyn_weight, 6),
        "dynamicSampleCount": dynamic_count,
        # 参评数>0 而动态数=0 → 动态覆盖 0%（不是 null）
        "dynamicEvidenceCoverageRatio": (
            round(dynamic_count / object_count, 6) if object_count > 0 else None
        ),
    }


def scorable_p_ratio(values: list[Any]) -> dict[str, Any]:
    """完整 P 可评分比例（名册内回路清单级；EA-D01/§1.3 纯函数形态）.

    Args:
        values: 参评名册（E）内每回路的综合评分 P 值列表（None/非有限值=
            不可评分；真实 0 分算可评分）

    Returns:
        ``{"eligibleLoopCount", "scorableLoopCount", "scorableLoopRatio"}``；
        E=0 → ratio=None（"无参评回路"）；E>0 且 N=0 → 0%。
    """
    eligible = len(values)
    scorable = sum(
        1 for v in values if isinstance(v, (int, float)) and not isinstance(v, bool) and v == v
    )
    return {
        "eligibleLoopCount": eligible,
        "scorableLoopCount": scorable,
        "scorableLoopRatio": round(100.0 * scorable / eligible, 4) if eligible > 0 else None,
    }


__all__ = [
    "fast_rate_reference_stats",
    "scorable_p_ratio",
    "weighted_score_stats",
]
