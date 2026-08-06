"""自然语言诊断解读服务（P3-04）。

混合方案：
- 规则模板生成（默认/离线可用）：基于结构化诊断数据 + 特征值拼装自然语言段落
- LLM API 接入（增强/可选）：调用 OpenAI 兼容接口生成更灵活的解读，超时/错误 fallback 到模板

职责：
- generate_interpretation：按 mode 编排模板/LLM 生成
- _generate_template：规则模板生成核心
- STRUCTURED_REPORT：8 标签结构化报告常量（与前端 DIAGNOSIS_STRUCTURED_REPORT 对齐）
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BizError
from app.services.diagnosis import get_diagnosis_detail

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 结构化报告常量（与前端 DIAGNOSIS_STRUCTURED_REPORT 对齐）
# ---------------------------------------------------------------------------

STRUCTURED_REPORT: dict[str, dict[str, str]] = {
    "OSCILLATION": {
        "cause": "PV/OP 出现周期性波动，可能由 PID 参数过激、阀门粘滞或外扰引起",
        "suggestion": "结合频谱分析定位振荡源：峰频与回路自然频率一致→参数过激；"
        "PV-OP 椭圆轨迹→阀门粘滞；无明显峰频→外扰",
        "improvement": "消除振荡后综合评分预计提升 15-30 分，平稳率提升至 90%+",
        "actionType": "tuning",
        "urgency": "high",
    },
    "VALVE_STICTION": {
        "cause": "调节阀存在静摩擦（stiction），OP 变化时卡涩不动，累积后突然动作",
        "suggestion": "联系仪表人员检修调节阀（更换填料/润滑），或临时增加 PID 积分作用补偿",
        "improvement": "检修后振荡消除，综合评分预计提升 20-40 分",
        "actionType": "maintenance",
        "urgency": "high",
    },
    "OVERAGGRESSIVE": {
        "cause": "PID 比例增益过大或积分时间过短，控制器对偏差反应过度导致振荡",
        "suggestion": "减小比例增益（Kp ↓20-30%）或增大积分时间（Ti ↑1.5-2 倍），"
        "使用整定工作台仿真对比",
        "improvement": "参数调整后振荡消除，综合评分预计提升 15-25 分",
        "actionType": "tuning",
        "urgency": "high",
    },
    "OVERCONSERVATIVE": {
        "cause": "PID 比例增益过小或积分时间过长，控制器响应迟缓无法及时消除偏差",
        "suggestion": "增大比例增益（Kp ↑30-50%）或减小积分时间（Ti ↓30-50%），"
        "使用整定工作台仿真对比",
        "improvement": "参数调整后响应速度提升，综合评分预计提升 10-20 分，快速率显著改善",
        "actionType": "tuning",
        "urgency": "medium",
    },
    "EXTERNAL_DISTURBANCE": {
        "cause": "上游负荷、原料组分等不可控因素频繁变化，超出回路调节能力",
        "suggestion": "排查扰动源（上游流量/温度/压力变化），考虑增加前馈补偿或"
        "调整回路结构（如串级控制）",
        "improvement": "前馈补偿后抗扰能力提升，综合评分预计提升 10-15 分",
        "actionType": "investigation",
        "urgency": "medium",
    },
    "OUTPUT_SATURATION": {
        "cause": "OP 长期处于上下限附近，执行器已达极限位置仍无法消除偏差",
        "suggestion": "检查阀门选型是否匹配工况（可能需增大阀门口径），或调整工艺参数降低负荷",
        "improvement": "解除饱和后恢复调节能力，综合评分预计提升 5-15 分",
        "actionType": "investigation",
        "urgency": "medium",
    },
    "QUALITY_ABNORMAL": {
        "cause": "传感器故障或通讯问题导致 PV 信号存在坏值，影响 KPI 计算准确性",
        "suggestion": "联系仪表人员检查测量回路（传感器校验/接线/通讯），修复后重新触发评估",
        "improvement": "修复后数据质量恢复，KPI 评估可信度提升至 A/B 级",
        "actionType": "maintenance",
        "urgency": "high",
    },
    "MANUAL_REVIEW": {
        "cause": "自动诊断无法明确归类，特征值处于多个标签的边界区域",
        "suggestion": "由经验丰富的仪控工程师结合工艺情况、历史趋势和频谱图综合分析",
        "improvement": "人工定位后针对性优化，避免盲目整定",
        "actionType": "review",
        "urgency": "low",
    },
}

ACTION_TYPE_LABEL: dict[str, str] = {
    "tuning": "PID 整定",
    "maintenance": "仪表维护",
    "investigation": "工况排查",
    "review": "人工复核",
}

URGENCY_LABEL: dict[str, str] = {
    "high": "紧急",
    "medium": "一般",
    "low": "低",
}

# 各标签的关键特征值字段名（用于解读中引用具体数值）
LABEL_FEATURE_KEYS: dict[str, list[str]] = {
    "OSCILLATION": ["similarity_score", "zero_crossings", "dominant_freq"],
    "VALVE_STICTION": ["stiction_f", "stiction_j", "fit_quality"],
    "OVERAGGRESSIVE": ["harris_index", "overshoot"],
    "OVERCONSERVATIVE": ["response_time", "settling_time", "ias"],
    "OUTPUT_SATURATION": ["saturation_ratio", "op_max_ratio", "op_min_ratio"],
    "QUALITY_ABNORMAL": ["bad_count", "bad_rate", "frozen_ratio"],
    "EXTERNAL_DISTURBANCE": ["disturbance_rate", "cross_correlation"],
}


# ---------------------------------------------------------------------------
# 核心服务
# ---------------------------------------------------------------------------


async def generate_interpretation(
    db: AsyncSession,
    loop_id: str,
    *,
    mode: str = "auto",
) -> dict:
    """生成自然语言诊断解读（P3-04）。

    Args:
        db: 数据库会话
        loop_id: 回路 ID
        mode: 生成模式
            - "template": 仅规则模板
            - "llm": 仅 LLM（不可用则抛错）
            - "auto": 优先 LLM，fallback 到模板（默认）

    Returns:
        dict: {
            "interpretation": str,  # 结构化纯文本
            "source": "template" | "llm",
            "model": str | None,
            "generatedAt": str,
        }

    Raises:
        BizError: ERR_LLM_UNAVAILABLE — mode=llm 但 LLM 不可用
    """
    # 获取诊断详情（复用已有服务，内部已处理回路不存在/无诊断结果错误）
    detail = await get_diagnosis_detail(db=db, loop_id=loop_id)

    # mode=template：直接规则模板
    if mode == "template":
        return _build_template_result(detail)

    # mode=llm 或 mode=auto：尝试 LLM
    if mode in ("llm", "auto"):
        try:
            from app.services.llm_provider import call_llm, is_llm_available

            if not await is_llm_available(db):
                if mode == "llm":
                    raise BizError(
                        code="ERR_LLM_UNAVAILABLE",
                        message="LLM 未启用或配置缺失，请在系统配置中开启 LLM 服务",
                        status_code=503,
                    )
                # mode=auto: fallback 到模板
                logger.info("LLM 不可用，fallback 到规则模板（loop_id=%s）", loop_id)
                return _build_template_result(detail)

            # 构造 prompt 并调用 LLM
            system_prompt = _build_system_prompt()
            user_prompt = _build_user_prompt(detail)
            llm_text, model_name = await call_llm(db, system_prompt, user_prompt)

            return {
                "interpretation": llm_text,
                "source": "llm",
                "model": model_name,
                "generatedAt": datetime.now(UTC).replace(tzinfo=None).isoformat(),
            }
        except BizError:
            if mode == "llm":
                raise
            # mode=auto: LLM 调用失败，fallback 到模板
            logger.warning("LLM 调用失败，fallback 到规则模板（loop_id=%s）", loop_id)
            return _build_template_result(detail)
        except Exception:
            logger.exception("LLM 调用异常，fallback 到规则模板（loop_id=%s）", loop_id)
            if mode == "llm":
                raise BizError(
                    code="ERR_LLM_UNAVAILABLE",
                    message="LLM 调用失败",
                    status_code=503,
                ) from None
            return _build_template_result(detail)

    # 无效 mode
    raise BizError(
        code="ERR_INVALID_MODE",
        message=f"mode 必须为 auto/template/llm 之一，收到 {mode}",
        status_code=422,
    )


def _build_template_result(detail: dict) -> dict:
    """构建规则模板解读结果。"""
    return {
        "interpretation": _generate_template(detail),
        "source": "template",
        "model": None,
        "generatedAt": datetime.now(UTC).replace(tzinfo=None).isoformat(),
    }


def _generate_template(detail: dict[str, Any]) -> str:
    """规则模板生成自然语言解读。

    Args:
        detail: get_diagnosis_detail 返回的字典

    Returns:
        str: 结构化纯文本解读
    """
    tag_name = detail.get("tagName", "未知回路")
    composite_score = detail.get("compositeScore")
    confidence_level = detail.get("confidenceLevel") or "—"
    valid_rate = detail.get("validRate")
    labels = detail.get("diagnosisLabels", [])
    feature_values = detail.get("featureValues", {})

    lines: list[str] = []

    # ---- 概述段 ----
    score_text = f"{composite_score:.1f}" if composite_score is not None else "—"
    valid_text = f"{valid_rate * 100:.1f}%" if valid_rate is not None else "—"
    lines.append("【概述】")
    lines.append(
        f"{tag_name} 回路当前综合评分 {score_text}，"
        f"诊断可信度 {confidence_level} 级（有效数据率 {valid_text}）。"
    )

    if not labels:
        lines.append("该回路暂无诊断标签，系统未检测到明显异常。")
        lines.append("")
        lines.append("【建议】定期关注回路 KPI 指标变化，如有异常系统将自动告警。")
        return "\n".join(lines)

    # 按置信度降序排序
    sorted_labels = sorted(
        labels,
        key=lambda x: x.get("confidence", 0),
        reverse=True,
    )

    # ---- 主因分析 ----
    lines.append("")
    lines.append("【主因分析】")

    for i, label_item in enumerate(sorted_labels, 1):
        label = label_item.get("label", "MANUAL_REVIEW")
        label_name = label_item.get("labelName", label)
        confidence = label_item.get("confidence", 0)
        confidence_pct = f"{confidence * 100:.0f}%" if isinstance(confidence, (int, float)) else "—"

        report = STRUCTURED_REPORT.get(label, STRUCTURED_REPORT["MANUAL_REVIEW"])

        lines.append(f"  {i}. {label_name}（置信度 {confidence_pct}）")
        lines.append(f"     根因：{report['cause']}")

        # 关键特征值
        feature_keys = LABEL_FEATURE_KEYS.get(label, [])
        feature_parts: list[str] = []
        for fk in feature_keys:
            fv = feature_values.get(fk)
            if fv is not None:
                if isinstance(fv, float):
                    feature_parts.append(f"{fk}={fv:.4f}")
                else:
                    feature_parts.append(f"{fk}={fv}")
        if feature_parts:
            lines.append(f"     特征值：{', '.join(feature_parts)}")

        lines.append(f"     建议：{report['suggestion']}")
        lines.append(f"     预估改善：{report['improvement']}")
        lines.append("")

    # ---- 风险提示 ----
    lines.append("【风险提示】")

    # 取最高紧急度
    urgency_order = {"high": 0, "medium": 1, "low": 2}
    highest_urgency = min(
        (
            STRUCTURED_REPORT.get(lbl.get("label", ""), {}).get("urgency", "low")
            for lbl in sorted_labels
        ),
        key=lambda u: urgency_order.get(u, 99),
        default="low",
    )
    highest_action = max(
        sorted_labels,
        key=lambda x: (
            -urgency_order.get(
                STRUCTURED_REPORT.get(x.get("label", ""), {}).get("urgency", "low"), 99
            )
        ),
    )
    highest_label = highest_action.get("label", "MANUAL_REVIEW")
    highest_report = STRUCTURED_REPORT.get(highest_label, STRUCTURED_REPORT["MANUAL_REVIEW"])

    urgency_text = URGENCY_LABEL.get(highest_urgency, "低")
    action_text = ACTION_TYPE_LABEL.get(highest_report["actionType"], "人工复核")

    lines.append(f"  紧急程度：{urgency_text}")
    lines.append(f"  建议动作：{action_text}")
    if highest_urgency == "high":
        lines.append("  ⚠ 该回路存在高优先级问题，建议尽快处理。")
    elif highest_urgency == "medium":
        lines.append("  该回路存在一般性问题，建议择机处理。")
    else:
        lines.append("  该回路问题优先级较低，可纳入后续优化计划。")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# LLM Prompt 构造
# ---------------------------------------------------------------------------


def _build_system_prompt() -> str:
    """构造 LLM 系统提示词。"""
    return (
        "你是危化企业控制回路诊断专家，擅长用通俗语言解读诊断结果。\n"
        "请基于提供的诊断数据，生成一段自然语言解读，包含以下部分：\n"
        "【概述】回路当前状态概述（综合评分、可信度）\n"
        "【主因分析】按置信度从高到低分析每个诊断标签的含义、原因和关键特征值\n"
        "【建议】给出具体的处置建议和预估改善效果\n"
        "【风险提示】紧急程度和建议动作类型\n\n"
        "要求：\n"
        "1. 用大白话解释，避免过多专业术语\n"
        "2. 每个标签的分析要具体到特征值数值\n"
        "3. 建议要可操作（如'降低比例增益 20%'而非'调整参数'）\n"
        "4. 总长度控制在 300-500 字"
    )


def _build_user_prompt(detail: dict[str, Any]) -> str:
    """构造 LLM 用户提示词（诊断数据 JSON）。"""
    import json

    # 精简数据，只传 LLM 需要的字段
    labels = detail.get("diagnosisLabels", [])
    simplified_labels = [
        {
            "label": lbl.get("label"),
            "labelName": lbl.get("labelName"),
            "confidence": lbl.get("confidence"),
        }
        for lbl in labels
    ]

    data = {
        "tagName": detail.get("tagName"),
        "compositeScore": detail.get("compositeScore"),
        "confidenceLevel": detail.get("confidenceLevel"),
        "validRate": detail.get("validRate"),
        "diagnosisLabels": simplified_labels,
        "featureValues": detail.get("featureValues", {}),
    }

    return f"请解读以下诊断数据：\n{json.dumps(data, ensure_ascii=False, indent=2)}"
