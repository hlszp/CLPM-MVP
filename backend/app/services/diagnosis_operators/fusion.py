"""族内保守证据融合（D5，08 号文 §4.4 A 裁决：conservative-max/1）。

融合规则版本沿革：
- v1（历史）：族内 ≥2 命中按 Dempster-Shafer 连乘融合（两法 0.9×0.9 →
  0.988）——把"方法数量"误当独立证据增强，且支持度被冒充为正确率概率。
- **conservative-max/1（现行，2026-10-11 用户裁决 A，08§13.1/§4.4.6）**：
  同依赖组取最大有效阳性支持度；跨组在未校准阶段仍取最大值——不因叠加
  方法数量提高总支持度。修改后的值属于新融合 profile（supportRuleVersion
  标识），不与历史 D-S 值无标识混用。

保守规则要点（08§4.4）：
- 症状完整保留：族内任一算子阳性即症状检出，症状不因"原因无法归类"消失；
- 方法数 ≠ 独立证据数：同依赖组（同一 PV/OP、共用极限环门控/椭圆特征/
  相同预处理）归并计数（C15-D05：同一特征通过两条方法重复进入融合不
  虚增独立证据数）；
- 保留所有证据与矛盾标记：阳性与合格阴性共存时记 conflict，不省略；
- confidence 字段语义 = 规则支持度（supportKind=RULE_SUPPORT，
  calibrationStatus=NOT_CALIBRATED），**不冒充概率**；只有经现场独立
  校准的方法才能另行发布概率解释（08§4.4.8）。

``dempster_shafer`` 函数保留为历史规则参考（既有测试锚定其数值语义），
现行融合链路不再调用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services.diagnosis_operators.base import (
    ASSESSMENT_NEGATIVE,
    ASSESSMENT_POSITIVE,
    OperatorResult,
)

#: 融合规则版本（08§4.2 assessment 示例 supportRuleVersion 字段来源）
SUPPORT_RULE_VERSION = "conservative-max/1"

#: 同依赖组登记（08§4.4.5"同源关系至少标出"）：
#: - stiction_pv_op：粘滞三法消费同一 PV/OP 轴；椭圆与 Choudhury 共享
#:   内部椭圆调用与极限环门控（D3/D4 已统一采样/量程口径）；Kano 段占比
#:   同样基于同一 PV/OP（方法数 ≠ 独立证据数）；
#: - oscillation_pv：FFT 与 IAE 同一 PV、相同预处理，谱/面积两视角。
#: 组内命中只计 1 份独立证据、支持度取组内最大，不重复增强。
DEPENDENCY_GROUPS: dict[str, tuple[str, ...]] = {
    "stiction_pv_op": ("stiction_ellipse", "stiction_choudhury", "stiction_kano"),
    "oscillation_pv": ("oscillation_fft", "oscillation_iae"),
}

#: 冲突标记：族内阳性与合格阴性并存（08§4.4.6/§4.4.7——矛盾证据保留
#: 可查，反证不能简单换算为 1−支持度）
CONFLICT_POSITIVE_NEGATIVE_COEXIST = "POSITIVE_NEGATIVE_COEXIST"


def dempster_shafer(confidences: list[float]) -> float:
    """Dempster-Shafer 证据融合（**历史 v1 规则，现行链路已不调用**）。

    保留目的：锚定历史融合数值语义（两法 0.9+0.9 → 0.9878），供新旧
    profile 对照说明；conservative-max/1 详见模块头。

    - 空列表 → 0.0；单条 → 原样返回
    - 全 0 → 0.0；全 1 → 1.0（避免 0/0）
    - 证据完全冲突（如 0.9 与 0.1）时融合值趋近 0.5
    """
    if not confidences:
        return 0.0
    if len(confidences) == 1:
        return float(confidences[0])
    if all(c <= 0.0 for c in confidences):
        return 0.0
    if all(c >= 1.0 for c in confidences):
        return 1.0

    eps = 1e-9
    prod_c = 1.0
    prod_not_c = 1.0
    for conf in confidences:
        c = max(eps, min(1.0 - eps, float(conf)))
        prod_c *= c
        prod_not_c *= 1.0 - c
    return float(prod_c / (prod_c + prod_not_c))


@dataclass
class FamilyFusion:
    """族级融合结果。

    confidence = 规则支持度（0~1，未校准，不冒充概率）。
    supportStructure（D5）：保守规则的支持结构——各算子贡献、依赖组
    归并、独立证据计数与冲突标记全部可查（08§4.4.6"融合输出含支持结构"）。
    """

    family: str
    symptom_tag: str
    detected: bool = False
    confidence: float = 0.0
    contributors: list[dict[str, float]] = field(default_factory=list)
    fused: bool = False  # 是否 ≥2 算子阳性（保守规则下取最大，不增强）
    #: 融合规则版本（新 profile 标识，历史 D-S 值不带此版本）
    support_rule_version: str = SUPPORT_RULE_VERSION
    #: 依赖组归并结果（组名 → 命中算子/组内最大支持度）
    dependency_groups: list[dict[str, Any]] = field(default_factory=list)
    #: 独立证据数（依赖组去重后计数，≤ contributors 数）
    independent_evidence_count: int = 0
    #: 矛盾标记（阳性与合格阴性并存等）
    conflicts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "family": self.family,
            "symptomTag": self.symptom_tag,
            "detected": self.detected,
            "confidence": round(self.confidence, 4),
            "contributors": self.contributors,
            "fused": self.fused,
            # D5（08§4.4.6）：支持结构——各算子贡献可查、规则版本可溯
            "supportStructure": {
                "ruleVersion": self.support_rule_version,
                "dependencyGroups": self.dependency_groups,
                "independentEvidenceCount": self.independent_evidence_count,
                "conflicts": self.conflicts,
            },
        }


def _is_positive_hit(r: OperatorResult) -> bool:
    """有效阳性判定：优先消费算子八态（D3/D4 产物）。

    assessment_state 缺失（直调/旧输入）时回退 executed+detected 旧字段
    （reader-first：旧调用方行为不变）。
    """
    if r.assessment_state is not None:
        return r.assessment_state == ASSESSMENT_POSITIVE
    return bool(r.executed and r.detected)


def _dependency_key(operator: str) -> str:
    """算子所属依赖组（无登记则自成单例组）。"""
    for group, members in DEPENDENCY_GROUPS.items():
        if operator in members:
            return group
    return operator


def fuse_family(family: str, symptom_tag: str, results: list[OperatorResult]) -> FamilyFusion:
    """族内保守融合（conservative-max/1）。

    - 阳性命中（八态 POSITIVE）→ 症状检出；支持度 = 命中支持度最大值
      （同依赖组内外均不增强——08§4.4.6 A 裁决）
    - 1 条命中 → 该支持度原样；≥2 条命中 → fused=True（仍取最大）
    - 0 条命中 → detected=False（未执行/失败/不适用/缺数据的算子不参与）
    - 阳性与合格阴性并存 → conflicts 记 POSITIVE_NEGATIVE_COEXIST
      （矛盾保留可查；原因归类层面的"关键反证保持未决"由分类层消费）
    """
    hits = [
        {"operator": r.operator, "confidence": round(float(r.confidence), 4)}
        for r in results
        if _is_positive_hit(r)
    ]
    negative_ops = [r.operator for r in results if r.assessment_state == ASSESSMENT_NEGATIVE]
    conflicts: list[str] = []
    if hits and negative_ops:
        conflicts.append(CONFLICT_POSITIVE_NEGATIVE_COEXIST)

    if not hits:
        return FamilyFusion(
            family=family,
            symptom_tag=symptom_tag,
            conflicts=conflicts,
            independent_evidence_count=0,
        )

    # 依赖组归并：组内取最大，独立证据按组计数（C15-D05 不虚增）
    groups: dict[str, dict[str, Any]] = {}
    for h in hits:
        key = _dependency_key(h["operator"])
        g = groups.setdefault(key, {"group": key, "operators": [], "maxSupport": h["confidence"]})
        g["operators"].append(h["operator"])
        g["maxSupport"] = max(g["maxSupport"], h["confidence"])
    for g in groups.values():
        g["operators"] = sorted(g["operators"])  # 组内集合语义，排序稳定序列化

    fused_conf = max(float(h["confidence"]) for h in hits)
    return FamilyFusion(
        family=family,
        symptom_tag=symptom_tag,
        detected=True,
        confidence=fused_conf,
        contributors=hits,
        fused=len(hits) >= 2,
        dependency_groups=[
            groups[k] for k in sorted(groups)
        ],  # 排序保证确定性输出（同输入必同输出）
        independent_evidence_count=len(groups),
        conflicts=conflicts,
    )
