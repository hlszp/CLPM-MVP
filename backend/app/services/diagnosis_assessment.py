"""诊断科学结论元信息（D2，08§4.2/§4.3 + D0 契约 §1.5/§1.8）。

职责（纯函数，无 DB/IO）：
- 从算子八态 + 融合/分类结果构建 ``diagnosis_run.assessment_meta``
  （judgementStatus 五值 / causeStatus 三值 / scope / coverage 三等式）；
- 运行状态（run.status RUNNING/SUCCESS/PARTIAL/FAILED）与科学结论
  **分层**：status 描述执行过程，judgementStatus 描述科学结论，两者
  按 08§4.2 组合决定表映射，不互相冒充。

写入口径（writer，P3-01 D1/D2 Owner）：本模块只产出新 run 的元信息；
历史 run 无 assessment_meta → 读端按 LEGACY_UNVERIFIABLE 口径处理
（reader 侧归 D6），writer 不回填。
"""

from __future__ import annotations

from typing import Any

from app.services.diagnosis_operators.base import (
    ASSESSMENT_CANCELLED,
    ASSESSMENT_ERROR,
    ASSESSMENT_INSUFFICIENT_DATA,
    ASSESSMENT_NEGATIVE,
    ASSESSMENT_NOT_APPLICABLE,
    ASSESSMENT_NOT_RUN,
    ASSESSMENT_POSITIVE,
)
from app.services.diagnosis_operators.classification import DATA_INSUFFICIENT, NO_SYMPTOM
from app.services.diagnosis_operators.fusion import SUPPORT_RULE_VERSION

#: assessment_meta schema 版本（只加不改；语义变化升次版本）
DIAGNOSIS_ASSESSMENT_SCHEMA_VERSION = "diagnosis-assessment/1"

#: judgementStatus 值域（D0 契约 §1.5，11§3 一字不差）
JUDGEMENT_SYMPTOM_DETECTED = "SYMPTOM_DETECTED"
JUDGEMENT_VALID_NEGATIVE = "VALID_NEGATIVE"
JUDGEMENT_INCONCLUSIVE = "INCONCLUSIVE"
JUDGEMENT_NOT_ASSESSED = "NOT_ASSESSED"
JUDGEMENT_LEGACY_UNVERIFIABLE = "LEGACY_UNVERIFIABLE"  # 仅历史读端，writer 不产出

#: causeStatus 值域（08§4.2）
CAUSE_CANDIDATE = "CANDIDATE"
CAUSE_UNRESOLVED = "UNRESOLVED"
CAUSE_NOT_ASSESSED = "NOT_ASSESSED"

#: supportKind：08§4.2 示例结构——A 裁决（保守规则融合）前，confidence
#: 保持"规则支持度"解释，不冒充概率；calibrationStatus 未校准。
SUPPORT_KIND_RULE_SUPPORT = "RULE_SUPPORT"
CALIBRATION_STATUS_NOT_CALIBRATED = "NOT_CALIBRATED"

_STATE_TO_COVERAGE_KEY: dict[str, str] = {
    ASSESSMENT_POSITIVE: "positive",
    ASSESSMENT_NEGATIVE: "negative",
    ASSESSMENT_NOT_APPLICABLE: "notApplicable",
    ASSESSMENT_INSUFFICIENT_DATA: "insufficient",
    ASSESSMENT_ERROR: "error",
    ASSESSMENT_CANCELLED: "cancelled",
    ASSESSMENT_NOT_RUN: "notRun",
}


def coverage_counts(op_results: dict[str, Any]) -> dict[str, int]:
    """selected 集合八态计数（08§4.3 第二等式右端）。

    仅统计带 assessment_state 的算子（selected 集合内）；profile 外/
    显式排除算子（assessment_state=None）不进分母——它们属于
    explicitlyExcluded，另行计数。
    """
    counts = dict.fromkeys(_STATE_TO_COVERAGE_KEY.values(), 0)
    for r in op_results.values():
        state = (
            (r or {}).get("assessmentState")
            if isinstance(r, dict)
            else getattr(r, "assessment_state", None)
        )
        key = _STATE_TO_COVERAGE_KEY.get(state or "")
        if key is not None:
            counts[key] += 1
    return counts


def _as_dict_results(op_results: dict[str, Any]) -> list[dict[str, Any]]:
    """算子结果统一为 dict 视图（OperatorResult 或已序列化 dict）。"""
    out = []
    for r in (op_results or {}).values():
        if isinstance(r, dict):
            out.append(r)
        else:
            out.append(
                {
                    "operator": r.operator,
                    "assessmentState": r.assessment_state,
                    "reasonCode": r.reason_code,
                    "detected": r.detected,
                    "error": r.error,
                }
            )
    return out


def build_assessment_meta(
    *,
    op_results: dict[str, Any],
    explicitly_excluded: dict[str, str],
    profile_expected_operators: list[str],
    primary_category: str | None,
    gate_passed: bool,
    gate_reason: str | None,
    selected_is_empty: bool,
    cancelled: bool = False,
) -> dict[str, Any]:
    """构建 diagnosis_run.assessment_meta（08§4.2 结构）。

    Args:
        op_results: 算子名 → OperatorResult/序列化 dict（selected 内的带
            assessmentState；profile 外/显式排除的为 None）
        explicitly_excluded: 算子名 → 排除原因码（DISABLED_BY_CONFIG /
            NOT_SELECTED），即 profileExpected − selected
        profile_expected_operators: 本次 profile 期望算子清单（任务开始固定，
            不因数据不足/未命中缩小——08§4.3）
        primary_category: 分类层主分类（NO_SYMPTOM/DATA_INSUFFICIENT 或
            八类或 None）
        gate_passed / gate_reason: 数据门禁结论
        selected_is_empty: selected 集合是否为空（零检查 → NOT_ASSESSED）
        cancelled: 任务是否被取消（当前链路恒 False；08§4.1 CANCELLED 态
            由任务层写入，此处保留参数位）
    """
    results = _as_dict_results(op_results)
    selected = [r for r in results if r.get("assessmentState") is not None]
    cov = coverage_counts(op_results)

    # ---- judgementStatus（08§4.2 组合决定表） ----
    # 零检查：selected 为空（含 custom 白名单与 profile 交集空/全部显式排除）
    if selected_is_empty:
        judgement = JUDGEMENT_NOT_ASSESSED
    elif cancelled:
        judgement = JUDGEMENT_NOT_ASSESSED
    elif cov["positive"] > 0:
        judgement = JUDGEMENT_SYMPTOM_DETECTED
    elif selected and all(r.get("assessmentState") == ASSESSMENT_NEGATIVE for r in selected):
        # 全部合格阴性（执行无异常、前提成立）→ 范围内有效阴性
        judgement = JUDGEMENT_VALID_NEGATIVE
    else:
        # 混合：阴性+不适用 / 缺数据 / 错误 / 未运行 → 不可判定
        judgement = JUDGEMENT_INCONCLUSIVE

    # L0 门禁阻断：primary=DATA_INSUFFICIENT 时科学结论必为 INCONCLUSIVE
    # （不因"数据不足"伪称阴性；L0 手动放行产出正式 DATA_INSUFFICIENT 的
    # 既有裁决在分类层保留，此处只保证科学状态不冒充）
    if not gate_passed and judgement not in (JUDGEMENT_NOT_ASSESSED,):
        judgement = JUDGEMENT_INCONCLUSIVE

    # ---- causeStatus（08§4.2：原因候选需正面证据） ----
    if judgement in (JUDGEMENT_NOT_ASSESSED,):
        cause = CAUSE_NOT_ASSESSED
    elif (
        judgement == JUDGEMENT_SYMPTOM_DETECTED
        and primary_category is not None
        and primary_category not in (NO_SYMPTOM, DATA_INSUFFICIENT)
    ):
        cause = CAUSE_CANDIDATE
    else:
        cause = CAUSE_UNRESOLVED

    valid_assessment_count = cov["positive"] + cov["negative"]

    meta: dict[str, Any] = {
        "schemaVersion": DIAGNOSIS_ASSESSMENT_SCHEMA_VERSION,
        "judgementStatus": judgement,
        "causeStatus": cause,
        "scope": {
            # 任务开始固定的期望算子（fast=fast 组，full=注册表全集，
            # custom=细选白名单 ∩ profile）——不因结果缩小
            "profileExpectedOperators": sorted(profile_expected_operators),
            "requiredChecks": sorted(r["operator"] for r in selected),
            "excludedChecks": dict(sorted(explicitly_excluded.items())),
            "gatePassed": bool(gate_passed),
            "gateReason": gate_reason,
        },
        # ---- 覆盖分母三等式（08§4.3）----
        "coverage": {
            "profileExpected": len(profile_expected_operators),
            "selected": len(selected),
            "explicitlyExcluded": len(explicitly_excluded),
            **cov,
            "validAssessmentCount": valid_assessment_count,
            # 等式自校验（True=守恒；落库供读端对账，不再单百分比混代）
            "invariantSelected":
            # selected = positive+negative+notApplicable+insufficient+error+cancelled+notRun
            len(selected) == sum(cov.values()),
            "invariantExpected": len(profile_expected_operators)
            == len(selected) + len(explicitly_excluded),
        },
        "supportKind": SUPPORT_KIND_RULE_SUPPORT,
        "calibrationStatus": CALIBRATION_STATUS_NOT_CALIBRATED,
        # D5（08§4.2 示例字段）：融合规则版本——conservative-max/1（A 裁决
        # 保守规则）与历史 D-S 值（无此版本标识）不无标识混用（08§4.4.6）
        "supportRuleVersion": SUPPORT_RULE_VERSION,
        # per 算子状态明细（读端 LEGACY 判定与覆盖对账的最小充分信息）
        "operatorStates": {
            r["operator"]: {"state": r.get("assessmentState"), "reasonCode": r.get("reasonCode")}
            for r in results
        },
    }
    if cancelled:
        meta["cancelled"] = True
    return meta


def derive_run_status(
    *,
    op_results: dict[str, Any],
    gate_passed: bool,
    selected_is_empty: bool,
) -> str:
    """run.status 组合决定表（08§4.2 第三列，执行状态与科学结论分开）。

    - 所选必需检查全部合格阴性、执行无异常 → SUCCESS
    - 有阳性/阴性 + 部分 ERROR/NOT_RUN → PARTIAL（保留已得结论，
      列出未完成范围——由 assessment_meta.coverage 呈现）
    - 全部 ERROR（或全部因预算 NOT_RUN）→ FAILED（配置/资源问题不计缺数据）
    - selected 为空 → FAILED（零检查不进有效诊断成功数；08§4.2 行 7
      "已有占位则 FAILED"——创建前拒绝归触发层，本函数兜底 run 级）
    - L0 门禁/缺数据 → SUCCESS（正常完成，科学结论 INCONCLUSIVE）
    """
    cov = coverage_counts(op_results)
    if selected_is_empty:
        return "FAILED"
    assessed = cov["positive"] + cov["negative"]
    problematic = cov["error"] + cov["notRun"] + cov["cancelled"]
    total_stateful = assessed + problematic + cov["insufficient"] + cov["notApplicable"]
    if total_stateful == 0:
        # selected 非空但全无状态（防御：不应出现）
        return "PARTIAL"
    if assessed > 0:
        return "PARTIAL" if problematic > 0 else "SUCCESS"
    # 无有效判定：全错误/未运行 → FAILED；全缺数据/不适用 → SUCCESS
    if problematic > 0:
        return "PARTIAL" if (cov["insufficient"] > 0 or cov["notApplicable"] > 0) else "FAILED"
    return "SUCCESS"
