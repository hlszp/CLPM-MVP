"""族内 D-S 证据融合。

设计文档：docs/MVP设计/07-诊断模块设计方案.md §7.1
D-S 公式（等价复制自引擎 _dempster_shafer_fusion L3934-3983）：
    C_fused = (Π cᵢ) / (Π cᵢ + Π (1-cᵢ))
仅同族（同一症状假设）算子做交叉验证融合，禁止跨族融合。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services.diagnosis_operators.base import OperatorResult


def dempster_shafer(confidences: list[float]) -> float:
    """Dempster-Shafer 证据融合（等价复制自引擎 L3934-3983）。

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
    """族级融合结果。"""

    family: str
    symptom_tag: str
    detected: bool = False
    confidence: float = 0.0
    contributors: list[dict[str, float]] = field(default_factory=list)
    fused: bool = False  # 是否发生 ≥2 证据融合（单证据原样、零证据未检出）

    def to_dict(self) -> dict:
        return {
            "family": self.family,
            "symptomTag": self.symptom_tag,
            "detected": self.detected,
            "confidence": round(self.confidence, 4),
            "contributors": self.contributors,
            "fused": self.fused,
        }


def fuse_family(family: str, symptom_tag: str, results: list[OperatorResult]) -> FamilyFusion:
    """族内融合：仅取 executed 且 detected 的算子置信度。

    - ≥2 条命中 → D-S 融合
    - 1 条命中 → 该置信度原样
    - 0 条命中 → detected=False（未执行/失败的算子不参与）
    """
    hits = [
        {"operator": r.operator, "confidence": round(float(r.confidence), 4)}
        for r in results
        if r.executed and r.detected
    ]
    if not hits:
        return FamilyFusion(family=family, symptom_tag=symptom_tag)
    if len(hits) == 1:
        return FamilyFusion(
            family=family,
            symptom_tag=symptom_tag,
            detected=True,
            confidence=float(hits[0]["confidence"]),
            contributors=hits,
            fused=False,
        )
    fused_conf = dempster_shafer([h["confidence"] for h in hits])
    return FamilyFusion(
        family=family,
        symptom_tag=symptom_tag,
        detected=True,
        confidence=fused_conf,
        contributors=hits,
        fused=True,
    )
