"""诊断 v2 → 工作台展示语义映射层（14 号方案阶段 A1）。

背景：工作台 A-03 各区块此前消费旧引擎 4 档 severity（INFO/WARN/ERROR/CRITICAL）
与中文自由文本分类；诊断 v2（diagnosis_run）为 3 档 severity（HIGH/MEDIUM/LOW）
+ 8 类 primary_category 枚举。本层提供语义映射，供 A2/A3 聚合迁移时复用，
避免各查询各自硬编码。

口径（14 号文 §4 阶段 A1）：
- severity：HIGH→CRITICAL（v2 最高档映射旧最高档）、MEDIUM→WARN、LOW→INFO；
  旧 ERROR 档无 v2 对应值，映射域不含
- category：8 类枚举→中文标签，复用诊断算子分类层 CATEGORY_LABELS
  （与端点层 diagnosis_v2._CATEGORY_LABELS 同源同值，服务层取 services 侧避免分层反转）
"""

from __future__ import annotations

from app.services.diagnosis_operators.classification import CATEGORY_LABELS

#: v2 severity 三档 → 旧工作台四档颜色域映射
SEVERITY_V2_TO_LEGACY: dict[str, str] = {
    "HIGH": "CRITICAL",
    "MEDIUM": "WARN",
    "LOW": "INFO",
}

#: 8 类 primary_category → 中文标签（导出复用，避免各处再复制一份映射）
CATEGORY_LABELS_V2: dict[str, str] = CATEGORY_LABELS

#: v2 症状标签（symptom_tags 键域，与算子族 symptom_tags 同域）→ 中文标签名。
#: A2 rule_stats（规则名）/ rootcause_top（根因名）复用；未知标签原样透传
SYMPTOM_LABELS_V2: dict[str, str] = {
    "OSCILLATION": "回路振荡",
    "VALVE_STICTION": "阀门粘滞",
    "QUALITY_ABNORMAL": "数据质量异常",
    "LINK_ABNORMAL": "通信链路异常",
    "EXTERNAL_DISTURBANCE": "外部扰动",
    "OUTPUT_SATURATION": "输出饱和",
    "OVERAGGRESSIVE": "整定过激",
    "OVERCONSERVATIVE": "整定过保守",
}


def symptom_label(tag: str | None) -> str | None:
    """症状标签域名 → 中文标签名（None 透传；未知值原样透传便于暴露脏数据）。"""
    if not tag:
        return None
    return SYMPTOM_LABELS_V2.get(tag, tag)


def severity_to_legacy(severity: str | None) -> str | None:
    """v2 severity → 旧四档颜色域（None 透传；未知值原样透传便于暴露脏数据）。"""
    if not severity:
        return None
    return SEVERITY_V2_TO_LEGACY.get(severity, severity)


def category_label(category: str | None) -> str | None:
    """primary_category 8 类枚举 → 中文标签（None 透传；未知值原样透传）。"""
    if not category:
        return None
    return CATEGORY_LABELS_V2.get(category, category)
